#!/usr/bin/env python3
"""
preflight.py -- everything that must be true BEFORE a live pod is touched.

A rehearsal applies a candidate size to one running pod, watches it, and always puts it back. That
is a controlled experiment on production, so the interesting engineering is not the resize -- it is
the list of reasons to refuse.

Every gate here answers "what would make this experiment unsafe or uninterpretable?". A gate that
cannot be evaluated FAILS CLOSED: `unknown` is not `safe`. The most expensive bug in this subsystem
would be a preflight check that silently passed because its input was missing.

The gates, and why each exists:

  kill switch          An operator must be able to stop all rehearsals cluster-wide, instantly,
                       without a deploy.
  feature enabled      Off by default, globally.
  concurrency cap      At most N in flight per cluster, so a bad candidate cannot be applied to
                       every workload at once.
  blast radius         One pod per workload. Never two replicas of the same service.
  replica count        The workload must have a spare replica, so if the pod does degrade, traffic
                       has somewhere else to go.
  PDB headroom         A PodDisruptionBudget at its floor means the cluster is already fragile.
  QoS transition       Guaranteed -> Burstable is a real downgrade in eviction priority and needs
                       explicit opt-in.
  restart policy       `resizePolicy: RestartContainer` turns an in-place resize into a restart,
                       which is exactly what a rehearsal promises not to do.
  recent OOM           A workload that OOMKilled recently is already unhealthy; the experiment would
                       measure the incident, not the candidate.
  age                  A pod that started seconds ago has no stable baseline to compare against.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

log = logging.getLogger(__name__)

# A pod must be at least this old before it can be rehearsed: a freshly started container is still
# warming caches and JITting, so its baseline is not representative of steady state.
MIN_POD_AGE = timedelta(minutes=30)

# A workload that OOMKilled within this window is already in trouble.
RECENT_OOM_WINDOW = timedelta(hours=24)

# Opt-in annotation for the one genuinely destructive transition.
ALLOW_QOS_DEMOTION = "kubethrifty.io/allow-qos-demotion"
# Per-workload opt-out, so a team can exclude a workload without disabling the feature globally.
SKIP_REHEARSAL = "kubethrifty.io/skip-rehearsal"


class Decision(StrEnum):
    PROCEED = "proceed"
    REFUSE = "refuse"


@dataclass(frozen=True)
class PreflightResult:
    decision: Decision
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.decision is Decision.PROCEED

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class PodFacts:
    """
    Everything preflight needs about one candidate pod.

    Assembled by the caller from the Kubernetes API. Optional fields default to None meaning
    "unknown", and every gate treats unknown as a refusal -- so a field the caller forgot to
    populate cannot accidentally read as a pass.
    """

    namespace: str
    workload: str
    pod: str
    pod_uid: str
    container: str

    phase: str | None = None
    started_at: datetime | None = None

    ready_replicas: int | None = None
    desired_replicas: int | None = None

    current_requests: dict[str, float] = field(default_factory=dict)
    current_limits: dict[str, float] = field(default_factory=dict)

    qos_class: str | None = None
    # Per-container resizePolicy restart directives, e.g. {"cpu": "NotRequired", "memory":
    # "RestartContainer"}.
    resize_policy: dict[str, str] = field(default_factory=dict)

    pdb_current_healthy: int | None = None
    pdb_desired_healthy: int | None = None

    oom_events_recent: int | None = None
    restarts_recent: int | None = None

    annotations: dict[str, str] = field(default_factory=dict)


def qos_after(requests: dict[str, float], limits: dict[str, float]) -> str:
    """
    Compute the QoS class a container would land in.

    Guaranteed requires limits == requests for BOTH cpu and memory. Kubernetes derives this
    automatically, so a resize that leaves cpu.limit != cpu.request silently demotes a Guaranteed pod
    to Burstable -- which changes its eviction priority under node pressure. That is a real
    regression that no resource number reveals on its own.
    """
    if not requests or not limits:
        return "BestEffort" if not requests and not limits else "Burstable"
    for resource in ("cpu", "memory"):
        if resource not in requests or resource not in limits:
            return "Burstable"
        if requests[resource] != limits[resource]:
            return "Burstable"
    return "Guaranteed"


def preflight(
    facts: PodFacts,
    candidate_requests: dict[str, float],
    candidate_limits: dict[str, float],
    *,
    enabled: bool,
    kill_switch_engaged: bool,
    running_rehearsals: int,
    max_concurrent: int,
    workload_has_active_rehearsal: bool,
) -> PreflightResult:
    """
    Evaluate every gate. Returns PROCEED only if ALL of them pass.

    Deliberately collects every failure rather than short-circuiting on the first: an operator asking
    "why won't this rehearse?" wants the whole list, not one reason at a time across nine runs.
    """
    reasons: list[str] = []
    warnings: list[str] = []

    # --- switches -------------------------------------------------------------------------------
    if kill_switch_engaged:
        # Checked first and reported alone: when the kill switch is on, nothing else matters and a
        # list of secondary complaints would obscure the actual reason.
        return PreflightResult(
            Decision.REFUSE,
            ("kill switch engaged: all rehearsals are suspended cluster-wide",),
        )

    if not enabled:
        return PreflightResult(Decision.REFUSE, ("rehearsals are disabled (rehearsal_enabled=false)",))

    if facts.annotations.get(SKIP_REHEARSAL, "").lower() == "true":
        return PreflightResult(
            Decision.REFUSE, (f"workload opted out via {SKIP_REHEARSAL}=true",)
        )

    # --- blast radius ---------------------------------------------------------------------------
    if workload_has_active_rehearsal:
        # One pod per workload, always. Two concurrent experiments on the same service make both
        # uninterpretable: neither can attribute an observed regression to its own change.
        reasons.append("this workload already has a rehearsal in flight (blast radius is one pod)")

    if running_rehearsals >= max_concurrent:
        reasons.append(
            f"concurrency cap reached ({running_rehearsals}/{max_concurrent} rehearsals running)"
        )

    # --- pod health -----------------------------------------------------------------------------
    if facts.phase != "Running":
        reasons.append(f"pod phase is {facts.phase or 'unknown'}, not Running")

    if facts.started_at is None:
        # Unknown age fails closed. A pod that started 10 seconds ago has no meaningful baseline.
        reasons.append("pod start time is unknown, so no stable baseline can be established")
    else:
        age = datetime.now(UTC) - facts.started_at
        if age < MIN_POD_AGE:
            reasons.append(
                f"pod is only {age.total_seconds() / 60:.0f}m old; a baseline needs "
                f"{MIN_POD_AGE.total_seconds() / 60:.0f}m of steady state"
            )

    # --- availability ---------------------------------------------------------------------------
    if facts.ready_replicas is None or facts.desired_replicas is None:
        reasons.append("replica counts are unknown, so spare capacity cannot be confirmed")
    else:
        if facts.desired_replicas < 2:
            # A single-replica workload has nowhere to shed traffic if the experiment degrades it.
            reasons.append(
                f"workload has {facts.desired_replicas} replica(s); a rehearsal needs at least 2 "
                f"so traffic has somewhere else to go"
            )
        elif facts.ready_replicas < facts.desired_replicas:
            reasons.append(
                f"only {facts.ready_replicas}/{facts.desired_replicas} replicas are ready; the "
                f"workload is already degraded"
            )

    # PDB at its floor means the cluster has no disruption budget left to spend.
    if (
        facts.pdb_desired_healthy is not None
        and facts.pdb_current_healthy is not None
        and facts.pdb_current_healthy <= facts.pdb_desired_healthy
    ):
        reasons.append(
            f"PodDisruptionBudget is at its floor "
            f"({facts.pdb_current_healthy}/{facts.pdb_desired_healthy} healthy)"
        )

    # --- resize mechanics -----------------------------------------------------------------------
    # `RestartContainer` makes the resize restart the container, which defeats the entire promise of
    # a zero-restart rehearsal AND destroys the baseline being measured against.
    for resource, policy in facts.resize_policy.items():
        if policy == "RestartContainer" and resource in candidate_requests:
            reasons.append(
                f"resizePolicy for {resource} is RestartContainer, so an in-place resize would "
                f"restart the container -- a rehearsal must not cause a restart"
            )

    # --- QoS ------------------------------------------------------------------------------------
    before = facts.qos_class or qos_after(facts.current_requests, facts.current_limits)
    after = qos_after(candidate_requests, candidate_limits)
    if before == "Guaranteed" and after != "Guaranteed":
        if facts.annotations.get(ALLOW_QOS_DEMOTION, "").lower() != "true":
            reasons.append(
                f"candidate would demote QoS from Guaranteed to {after}, lowering eviction "
                f"priority under node pressure. Set {ALLOW_QOS_DEMOTION}=true to allow it."
            )
        else:
            warnings.append(f"QoS demotion Guaranteed -> {after} permitted by annotation")

    # --- existing distress ----------------------------------------------------------------------
    if facts.oom_events_recent is None:
        # Unknown fails closed: without OOM history we cannot tell a healthy workload from one that
        # died an hour ago, and the experiment would measure the incident rather than the candidate.
        reasons.append("recent OOM history is unknown; refusing to experiment blind")
    elif facts.oom_events_recent > 0:
        reasons.append(
            f"{facts.oom_events_recent} OOM event(s) in the last "
            f"{RECENT_OOM_WINDOW.total_seconds() / 3600:.0f}h; this workload is already unhealthy"
        )

    if facts.restarts_recent:
        # RESTART_TOLERANCE is 0 during the observation window, so a workload already restarting
        # would trip the verdict for reasons unrelated to the candidate size.
        reasons.append(
            f"{facts.restarts_recent} restart(s) recently; the baseline would be unstable"
        )

    # --- candidate sanity -----------------------------------------------------------------------
    for resource, value in candidate_requests.items():
        current = facts.current_requests.get(resource)
        if current is not None and value > current:
            # A rehearsal exists to prove a REDUCTION is safe. Raising a request needs no
            # experiment -- more resource is not a risk to the workload -- so this is a caller bug.
            warnings.append(
                f"candidate {resource} request ({value}) exceeds the current one ({current}); "
                f"an increase does not need rehearsing"
            )

    if "memory" in candidate_requests:
        mem_req = candidate_requests["memory"]
        mem_lim = candidate_limits.get("memory")
        if mem_lim is not None and mem_lim != mem_req:
            reasons.append(
                f"memory limit ({mem_lim}) != request ({mem_req}). Memory must be sized with "
                f"limit == request: over-limit means an OOMKill, which is fatal rather than merely "
                f"slow."
            )

    if reasons:
        log.info(
            "preflight REFUSED for %s/%s: %s",
            facts.namespace, facts.pod, "; ".join(reasons),
        )
        return PreflightResult(Decision.REFUSE, tuple(reasons), tuple(warnings))

    return PreflightResult(Decision.PROCEED, (), tuple(warnings))
