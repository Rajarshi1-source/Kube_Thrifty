#!/usr/bin/env python3
"""
detective/engine.py -- a runnable slice of ThriftDetective.

CONTRACT: investigate(bundle) is a PURE function. Same bundle in, byte-identical verdicts out, forever.
No network, no clock, no randomness, no LLM in the decision path. That property is what makes verdicts
replayable offline, unit-testable, and hash-assertable in CI (see ../detective_eval.py).

Scope discipline: EIGHT resource-shaped failure modes. Anything else returns NO VERDICT with an explicit
scope statement rather than a low-confidence guess -- a tool that never says "I don't know" cannot be
trusted when it does say something.

The differentiator is `_attribute`: every verdict tries to name the KubeThrifty change that caused the
incident (merged PR, recommendation id, resize event) inside a time window on the same container. No
external investigator can do that, because none of them owns the change log.

RULESET 1.1.0 added the three rules that distinguish "this container's own limits hurt it" from "this
container was collateral damage":

    NODE_PRESSURE_EVICTION      killed because the NODE ran out, not because its own limit was low
    QOS_DEMOTION_EVICTION       killed EARLIER than it would have been, because our change moved it
                                down the eviction order
    STARTUP_CPU_STARVATION      steady-state sizing starved the startup phase

The first of those also fixed a known misfire: `thumbnailer__noisy_neighbour` used to be answered
MEM_PRESSURE_NO_KILL, because with no node-pressure rule the only available explanation was the
container's own memory. That case is exactly why the corpus records misfires instead of hiding them.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass

# 1.1.0: three new rules, plus a precedence guard on MEM_PRESSURE_NO_KILL. Both change verdicts, so
# the digests were re-frozen deliberately -- see the commit message and evals/corpus/digests.json.
RULESET_VERSION = "1.1.0"
ATTRIBUTION_WINDOW_HOURS = 48

# PSI `full` above this is real suffering rather than ordinary busyness.
PSI_FULL_THRESHOLD = 0.05
# A container using less than this fraction of its own limit was not the cause of its own death.
INNOCENT_USAGE_FRACTION = 0.80


@dataclass(frozen=True)
class Verdict:
    rule_id: str
    confidence: float                     # calibrated; scored by detective_eval.py
    summary: str
    evidence: dict
    attributed_change: dict | None = None
    remediation: str = ""


Rule = Callable[[dict], Verdict | None]
RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    RULES.append(fn)
    return fn


def _attribute(b: dict) -> dict | None:
    """Did WE cause this? Correlate with KubeThrifty's own change log."""
    changes = sorted(b.get("kubethrifty_changes", []),
                     key=lambda c: c["hours_before_incident"])
    for ch in changes:
        if (ch["hours_before_incident"] <= ATTRIBUTION_WINDOW_HOURS
                and ch.get("container") == b.get("container")):
            return {
                "recommendation_id": ch.get("recommendation_id"),
                "pr": ch.get("pr_url"),
                "kind": ch.get("kind"),
                "hours_before": ch["hours_before_incident"],
                "delta": ch.get("delta"),
            }
    return None


# ---------------------------------------------------------------------------- rules
@rule
def mem_limit_too_low(b: dict) -> Verdict | None:
    if b["signals"].get("oom_kills", 0) <= 0:
        return None
    peak = b["cgroup"].get("memory_peak", 0)
    limit = b["cgroup"].get("memory_max") or 0          # None/0 == unlimited -> no ratio
    ratio = (peak / limit) if limit else 0.0
    conf = 0.97 if ratio > 0.95 else 0.85 if b.get("exit_code") == 137 else 0.70
    change = _attribute(b)
    if change and "memory" in (change.get("delta") or {}):
        conf = min(0.99, conf + 0.02)
    return Verdict(
        "MEM_LIMIT_TOO_LOW", round(conf, 3),
        "Container was OOMKilled; the memory limit is below real demand.",
        {"oom_kills": b["signals"]["oom_kills"], "memory_peak": peak, "memory_max": limit,
         "peak_over_limit": round(ratio, 3), "exit_code": b.get("exit_code")},
        change,
        "Raise memory request/limit to peak x 1.25 and mark the workload reduction-blocked for 14 days.",
    )


def _node_under_pressure(b: dict) -> bool:
    """True when the NODE, not this container, was out of memory."""
    node = b.get("node") or {}
    conditions = node.get("conditions") or {}
    return (
        conditions.get("MemoryPressure") == "True"
        or bool(node.get("eviction_threshold_crossed"))
    )


@rule
def mem_pressure_no_kill(b: dict) -> Verdict | None:
    """Reclaim thrash: slow, not dead. Invisible to every percentile-only tool."""
    psi = b["signals"].get("psi_mem_full")
    if psi is None or psi < PSI_FULL_THRESHOLD or b["signals"].get("oom_kills", 0) > 0:
        return None

    # PRECEDENCE GUARD (added in 1.1.0). Memory stall on a node that is itself out of memory is
    # collateral damage, and NODE_PRESSURE_EVICTION explains it properly. Without this the engine
    # blames the container's own limit and recommends giving it MORE memory -- on a node that has
    # none left, which makes the problem worse.
    if _node_under_pressure(b) and not _own_limit_implicated(b):
        return None
    cur = b["cgroup"].get("memory_current", 0)
    limit = b["cgroup"].get("memory_max") or 0
    near_limit = bool(limit) and cur / limit > 0.90
    return Verdict(
        "MEM_PRESSURE_NO_KILL", 0.88 if near_limit else 0.62,
        "Sustained memory stall without an OOM kill -- the workload is thrashing reclaim, so average "
        "usage understates real demand.",
        {"psi_mem_full": psi, "memory_current": cur, "memory_max": limit,
         "current_over_limit": round(cur / limit, 3) if limit else None},
        _attribute(b),
        "Increase memory; do NOT size this workload from percentile usage.",
    )


@rule
def cpu_limit_too_low(b: dict) -> Verdict | None:
    tr = b["signals"].get("throttle_ratio")
    if tr is None or tr < 0.05:
        return None
    psi = b["signals"].get("psi_cpu_full") or 0.0
    return Verdict(
        "CPU_LIMIT_TOO_LOW", 0.90 if psi > 0.02 else 0.72,
        "The CPU limit is throttling the workload.",
        {"throttle_ratio": tr, "psi_cpu_full": psi},
        _attribute(b),
        "Raise the CPU limit (or drop it and let requests schedule); keep the request as sized.",
    )


@rule
def hpa_coupling_storm(b: dict) -> Verdict | None:
    hpa, change = b.get("hpa"), _attribute(b)
    if not hpa or not change:
        return None
    before, after = hpa.get("replicas_before"), hpa.get("replicas_after")
    if not (before and after and after > before * 1.5):
        return None
    return Verdict(
        "HPA_COUPLING_STORM", 0.93,
        "Replica count grew sharply after a request reduction -- the HPA is reacting to higher "
        "utilisation-of-request, not to more traffic.",
        {"replicas_before": before, "replicas_after": after,
         "hpa_target": hpa.get("target_utilization"),
         "traffic_delta_pct": hpa.get("traffic_delta_pct")},
        change,
        "Re-derive the HPA target for the new request, or revert the request.",
    )


@rule
def resize_infeasible_stuck(b: dict) -> Verdict | None:
    cond = (b.get("conditions") or {}).get("PodResizePending") or {}
    if cond.get("reason") != "Infeasible":
        return None
    return Verdict(
        "RESIZE_INFEASIBLE_STUCK", 0.99,
        "An in-place resize cannot be satisfied on this node.",
        {"condition": cond, "node_allocatable": b.get("node_allocatable")},
        _attribute(b),
        "Abandon the rehearsal (never wait indefinitely); recommend via PR only.",
    )


# ============================================================================================
# RULESET 1.1.0 -- distinguishing "its own limit hurt it" from "it was collateral damage"
# ============================================================================================

def _own_limit_implicated(b: dict) -> bool:
    """
    Was this container using enough of its OWN limit to be the cause of its own trouble?

    The distinction the next three rules turn on. A container sitting at 30% of its limit did not
    die of its own configuration, however unhealthy the pod looks -- and recommending more memory
    for it would be treating a symptom of somebody else's problem.
    """
    current = b["cgroup"].get("memory_current") or 0
    limit = b["cgroup"].get("memory_max") or 0
    # No limit means no ratio. Unlimited containers are never "over their limit".
    if not limit:
        return False
    return (current / limit) >= INNOCENT_USAGE_FRACTION


@rule
def node_pressure_eviction(b: dict) -> Verdict | None:
    """
    Killed because the NODE ran out of memory, not because this container's limit was low.

    The remediation is the opposite of MEM_LIMIT_TOO_LOW's, which is why conflating them is
    expensive: raising this container's request would make it harder to schedule onto an already
    exhausted node. The fix is elsewhere -- evict the real hog, or add capacity.
    """
    if not _node_under_pressure(b):
        return None

    evicted = (b.get("conditions") or {}).get("Evicted") or {}
    reason = (evicted.get("reason") or "").lower()
    was_evicted = bool(evicted) or reason == "evicted"

    # A container over its own limit on a pressured node is genuinely over its own limit; the node
    # condition is then incidental and MEM_LIMIT_TOO_LOW / MEM_PRESSURE_NO_KILL own the verdict.
    if _own_limit_implicated(b):
        return None

    current = b["cgroup"].get("memory_current") or 0
    limit = b["cgroup"].get("memory_max") or 0
    usage_fraction = round(current / limit, 3) if limit else None

    # Highest when the pod was actually evicted AND was demonstrably innocent. Without an eviction
    # record this is an inference from node condition plus low own-usage, which is weaker.
    confidence = 0.94 if was_evicted else 0.71

    return Verdict(
        "NODE_PRESSURE_EVICTION",
        confidence,
        "The node was under memory pressure and this container was using only a small fraction of "
        "its own limit -- it was collateral damage, not the cause.",
        {
            "node_conditions": (b.get("node") or {}).get("conditions"),
            "node_available_mib": (b.get("node") or {}).get("available_mib"),
            "evicted": was_evicted,
            "memory_current": current,
            "memory_max": limit,
            "usage_of_own_limit": usage_fraction,
            "psi_mem_full": b["signals"].get("psi_mem_full"),
        },
        _attribute(b),
        "Do NOT raise this container's request -- it was not over its limit. Find the workload that "
        "exhausted the node, or add node capacity. Raising this request would only make it harder "
        "to schedule.",
    )


@rule
def qos_demotion_eviction(b: dict) -> Verdict | None:
    """
    Our own change moved this pod DOWN the eviction order, and then it was evicted.

    A resource change that alters QoS is an availability change, not just a cost change. Kubernetes
    derives QoS from resources, so a cut that leaves limits != requests silently demotes Guaranteed
    to Burstable -- and Burstable is evicted before Guaranteed under node pressure. Nothing in the
    resource numbers reveals that.

    Requires attribution: without one of OUR changes, a QoS class is just a fact about the pod
    rather than something we did to it.
    """
    qos = b.get("qos") or {}
    before, after = qos.get("before"), qos.get("after")
    if not before or not after or before == after:
        return None

    rank = {"Guaranteed": 0, "Burstable": 1, "BestEffort": 2}
    if rank.get(after, 99) <= rank.get(before, 99):
        # A promotion, or an unrecognised class. Moving UP the eviction order is a safety
        # improvement and never an incident.
        return None

    change = _attribute(b)
    if not change:
        return None

    evicted = (b.get("conditions") or {}).get("Evicted") or {}
    was_evicted = bool(evicted)
    node_pressure = _node_under_pressure(b)

    # The full causal chain observed: we demoted it, the node came under pressure, it was evicted.
    if was_evicted and node_pressure:
        confidence = 0.95
    elif was_evicted:
        confidence = 0.80
    else:
        # Demoted but not yet evicted. A real latent risk, not yet an incident.
        confidence = 0.58

    return Verdict(
        "QOS_DEMOTION_EVICTION",
        confidence,
        f"A KubeThrifty change demoted this pod from {before} to {after}, moving it earlier in the "
        f"eviction order"
        + (" and it was subsequently evicted." if was_evicted else ", which is a latent risk."),
        {
            "qos_before": before,
            "qos_after": after,
            "evicted": was_evicted,
            "node_under_pressure": node_pressure,
            "memory_current": b["cgroup"].get("memory_current"),
            "memory_max": b["cgroup"].get("memory_max"),
        },
        change,
        "Restore Guaranteed by setting limits == requests for BOTH cpu and memory, or accept the "
        "demotion explicitly with kubethrifty.io/allow-qos-demotion=true. QoS is an availability "
        "property, so this must be a deliberate decision rather than a side effect.",
    )


@rule
def startup_cpu_starvation(b: dict) -> Verdict | None:
    """
    The steady-state CPU request is adequate; the STARTUP phase is not.

    A JVM JITs, a Node process builds its module graph, a cache warms. Startup can need several times
    the steady-state CPU for tens of seconds -- and sizing from a steady-state percentile guarantees
    starving it, because the startup burst is a rounding error in a week of samples.

    The signature is specific: heavy throttling confined to the startup window, plus probe failures,
    plus restarts -- and NO OOM kill, which would point at memory instead.
    """
    startup = b.get("startup") or {}
    startup_throttle = startup.get("throttle_ratio")
    if startup_throttle is None or startup_throttle < 0.20:
        return None

    # An OOM kill is a memory story. Let the memory rules own it.
    if b["signals"].get("oom_kills", 0) > 0:
        return None

    probe_failures = startup.get("probe_failures") or 0
    restarts = startup.get("restarts") or 0
    if probe_failures <= 0 and restarts <= 0:
        # Throttled at startup but it still came up. Worth knowing, not an incident.
        return None

    steady_throttle = b["signals"].get("throttle_ratio")
    # The discriminator: throttled hard at startup, fine afterwards. If steady-state is also
    # throttling, this is plain CPU_LIMIT_TOO_LOW rather than a startup-specific problem.
    startup_specific = steady_throttle is not None and steady_throttle < 0.05

    confidence = 0.92 if (startup_specific and probe_failures > 0 and restarts > 0) else 0.68

    return Verdict(
        "STARTUP_CPU_STARVATION",
        confidence,
        "The container is throttled severely during startup and fails its probes, while steady-state "
        "CPU is comfortable. Sizing from steady-state percentiles cannot see the startup burst.",
        {
            "startup_throttle_ratio": startup_throttle,
            "steady_throttle_ratio": steady_throttle,
            "probe_failures": probe_failures,
            "restarts": restarts,
            "startup_seconds": startup.get("duration_seconds"),
            "startup_specific": startup_specific,
        },
        _attribute(b),
        "Do not raise the steady-state request for this. Either lengthen the startup probe "
        "(initialDelaySeconds / failureThreshold) so the slow start is tolerated, or adopt two-phase "
        "sizing: a higher request for the startup window, stepped down once the container is ready.",
    )


# ---------------------------------------------------------------------- entrypoints
def investigate(bundle: dict) -> list[Verdict]:
    verdicts = [v for v in (r(bundle) for r in RULES) if v]
    verdicts.sort(key=lambda v: (-v.confidence, v.rule_id))    # stable, deterministic order
    return verdicts


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def bundle_sha256(bundle: dict) -> str:
    """The incident's identity. Canonical JSON, or the same bundle hashes differently per machine."""
    return hashlib.sha256(canonical(bundle).encode()).hexdigest()


def verdicts_digest(verdicts: list[Verdict]) -> str:
    """Determinism anchor asserted in CI."""
    return hashlib.sha256(canonical([asdict(v) for v in verdicts]).encode()).hexdigest()


def explain(bundle: dict) -> str:
    """Deterministic narration. The optional LLM narrator in the real project phrases this, never
    decides it -- the verdict is computed first and the narration is validated against it."""
    vs = investigate(bundle)
    if not vs:
        return ("NO_VERDICT: outside ThriftDetective's scope -- this is not a resource-shaped incident. "
                "Resource signals look healthy; look at image pull, DNS, RBAC, storage or the app itself.")
    lines = []
    for v in vs:
        attrib = ""
        if v.attributed_change:
            a = v.attributed_change
            attrib = (f" ATTRIBUTED to {a.get('kind')} {a.get('pr') or a.get('recommendation_id')} "
                      f"{a.get('hours_before')}h before ({a.get('delta')}).")
        lines.append(f"{v.rule_id} ({v.confidence:.0%}): {v.summary}{attrib} -> {v.remediation}")
    return "\n".join(lines)
