# ThriftDetective — Rules, Bundles, and Calibration Reference

Load this when writing or changing rules, confidence arithmetic, the bundle schema, the corpus, or the CI
gates. `SKILL.md` carries the rules of engagement; this file carries the implementation.

## Contents
1. Engine skeleton and the attributor
2. The eight rules
3. Evidence bundle schema
4. The CI harness: accuracy · calibration · determinism
5. Chaos generators (how the corpus is manufactured)
6. Narrator (optional LLM layer)
7. Database rows
8. Competitive grid — and how to keep the claim honest

---

## 1. Engine skeleton and the attributor

```python
# detective/engine.py
"""
CONTRACT: investigate(bundle) is a pure function. Same bundle -> byte-identical verdicts, forever.
No network, no clock, no LLM, no randomness. CI asserts this by hashing the whole corpus's output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Callable, Optional

RULESET_VERSION = "1.3.0"


@dataclass(frozen=True)
class Verdict:
    rule_id: str
    confidence: float                    # calibrated, 0..1
    summary: str
    evidence: dict
    attributed_change: Optional[dict] = None
    remediation: str = ""


Rule = Callable[[dict], Optional[Verdict]]
RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    RULES.append(fn)
    return fn


def _attribute(b: dict, window_hours: int = 48) -> Optional[dict]:
    """The moat: did WE cause this? Correlate with KubeThrifty's own change log."""
    for ch in sorted(b.get("kubethrifty_changes", []),
                     key=lambda c: c["applied_at"], reverse=True):
        if ch["hours_before_incident"] <= window_hours and ch["container"] == b["container"]:
            return {"recommendation_id": ch.get("recommendation_id"), "pr": ch.get("pr_url"),
                    "commit": ch.get("commit"), "kind": ch.get("kind"),
                    "hours_before": ch["hours_before_incident"], "delta": ch.get("delta")}
    return None


def investigate(bundle: dict) -> list[Verdict]:
    verdicts = [v for v in (r(bundle) for r in RULES) if v]
    verdicts.sort(key=lambda v: v.confidence, reverse=True)
    return verdicts


def bundle_sha256(bundle: dict) -> str:
    return hashlib.sha256(json.dumps(bundle, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def verdicts_digest(verdicts: list[Verdict]) -> str:
    """Determinism anchor asserted in CI."""
    return hashlib.sha256(json.dumps([asdict(v) for v in verdicts], sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
```

Canonical JSON matters: `sort_keys=True` and tight separators, or the same bundle hashes differently on
two machines and every downstream guarantee evaporates.

---

## 2. The eight rules

Confidence arithmetic is explicit and small on purpose — every constant is something you can defend, and
every constant is scored by the calibration gate.

```python
@rule
def mem_limit_too_low(b: dict) -> Verdict | None:
    if b["signals"]["oom_kills"] <= 0:
        return None
    peak, limit = b["cgroup"].get("memory_peak", 0), b["cgroup"].get("memory_max") or 0
    ratio = (peak / limit) if limit else 0.0
    conf = 0.97 if ratio > 0.95 else 0.85 if b["exit_code"] == 137 else 0.70
    change = _attribute(b)
    if change and "memory" in (change.get("delta") or {}):
        conf = min(0.99, conf + 0.02)              # our own change makes causation more likely
    return Verdict("MEM_LIMIT_TOO_LOW", round(conf, 3),
                   "Container was OOMKilled; memory limit is below real demand.",
                   {"oom_kills": b["signals"]["oom_kills"], "memory_peak": peak,
                    "memory_max": limit, "peak_over_limit": round(ratio, 3),
                    "exit_code": b["exit_code"]},
                   change,
                   "Raise memory request/limit to peak × 1.25 and mark the workload "
                   "reduction-blocked for 14 days.")


@rule
def cpu_limit_too_low(b: dict) -> Verdict | None:
    tr = b["signals"].get("throttle_ratio", 0.0)
    if tr < 0.05:
        return None
    psi = b["signals"].get("psi_cpu_full", 0.0)
    return Verdict("CPU_LIMIT_TOO_LOW", 0.90 if psi > 0.02 else 0.72,
                   "CPU limit is throttling the workload.",
                   {"throttle_ratio": tr, "psi_cpu_full": psi}, _attribute(b),
                   "Raise the CPU limit (or drop it and let requests schedule); keep the request as sized.")


@rule
def mem_pressure_no_kill(b: dict) -> Verdict | None:
    psi = b["signals"].get("psi_mem_full", 0.0)
    if psi < 0.05 or b["signals"]["oom_kills"] > 0:
        return None
    cur, limit = b["cgroup"].get("memory_current", 0), b["cgroup"].get("memory_max") or 0
    near = bool(limit) and cur / limit > 0.90
    return Verdict("MEM_PRESSURE_NO_KILL", 0.88 if near else 0.62,
                   "Sustained memory stall without an OOM kill — reclaim thrash, so average usage "
                   "understates real demand.",
                   {"psi_mem_full": psi, "memory_current": cur, "memory_max": limit},
                   _attribute(b),
                   "Increase memory; do NOT size this workload from percentile usage.")


@rule
def node_pressure_eviction(b: dict) -> Verdict | None:
    ev = b.get("eviction")
    if not ev or ev.get("reason") != "Evicted":
        return None
    node_cond = b.get("node_conditions", {})
    under_pressure = node_cond.get("MemoryPressure") == "True"
    return Verdict("NODE_PRESSURE_EVICTION", 0.86 if under_pressure else 0.55,
                   "Pod was evicted under node-level pressure rather than its own limit.",
                   {"eviction": ev, "node_conditions": node_cond,
                    "sibling_overcommit_ratio": b.get("node_overcommit_ratio")},
                   _attribute(b),
                   "Reduce node over-commit or move the workload; this is a scheduling problem, "
                   "not a per-container limit problem.")


@rule
def qos_demotion_eviction(b: dict) -> Verdict | None:
    q = b.get("qos", {})
    if not (q.get("before") == "Guaranteed" and q.get("after") in ("Burstable", "BestEffort")):
        return None
    if not b.get("eviction"):
        return None
    return Verdict("QOS_DEMOTION_EVICTION", 0.91,
                   "Pod was evicted after a resource change demoted its QoS class, which moved it "
                   "earlier in the eviction order.",
                   {"qos": q, "eviction": b["eviction"]}, _attribute(b),
                   "Restore requests == limits for memory; block QoS demotion on this workload.")


@rule
def hpa_coupling_storm(b: dict) -> Verdict | None:
    hpa, change = b.get("hpa"), _attribute(b)
    if not hpa or not change:
        return None
    r_b, r_a = hpa.get("replicas_before"), hpa.get("replicas_after")
    if not (r_b and r_a and r_a > r_b * 1.5):
        return None
    return Verdict("HPA_COUPLING_STORM", 0.93,
                   "Replica count grew sharply after a request reduction — the HPA is reacting to "
                   "higher utilisation-of-request, not to more traffic.",
                   {"replicas_before": r_b, "replicas_after": r_a,
                    "hpa_target": hpa.get("target_utilization"),
                    "traffic_delta_pct": hpa.get("traffic_delta_pct")}, change,
                   "Re-derive the HPA target for the new request, or revert the request.")


@rule
def startup_starvation(b: dict) -> Verdict | None:
    s = b.get("startup", {})
    if not s.get("probe_failures_in_window"):
        return None
    if s.get("failures_outside_window", 0) > 0:
        return None                                  # not startup-specific -> another rule's incident
    ratio = (s.get("startup_peak", 0) / s["steady_p95"]) if s.get("steady_p95") else 0
    return Verdict("STARTUP_STARVATION", 0.89 if ratio > 3 else 0.66,
                   "Probe failures occur only during startup: the workload needs more CPU to warm up "
                   "than to serve.",
                   {"startup": s, "startup_over_steady": round(ratio, 2)}, _attribute(b),
                   "Propose a two-phase profile: start high, in-place resize down after readiness.")


@rule
def resize_infeasible_stuck(b: dict) -> Verdict | None:
    cond = (b.get("conditions") or {}).get("PodResizePending") or {}
    if cond.get("reason") != "Infeasible":
        return None
    return Verdict("RESIZE_INFEASIBLE_STUCK", 0.99,
                   "An in-place resize cannot be satisfied on this node.",
                   {"condition": cond, "node_allocatable": b.get("node_allocatable")},
                   _attribute(b),
                   "Abandon the rehearsal (never wait indefinitely); recommend via PR only.")
```

`NO_VERDICT` is the absence of any match — the caller renders it as an explicit scope statement, not as an
empty result.

---

## 3. Evidence bundle schema

```json
{
  "schema": "kubethrifty.detective/v1",
  "collected_at": "2026-08-16T14:41:02Z",
  "cluster": "demo", "namespace": "shop",
  "workload": "payment-processor", "pod": "payment-processor-7d9f", "container": "app",
  "exit_code": 137,
  "conditions": {"PodResizePending": {"status": "False"}},
  "signals": {"oom_kills": 1, "restarts_delta": 1, "throttle_ratio": 0.004,
              "psi_mem_full": 0.11, "psi_cpu_full": 0.002},
  "cgroup": {"memory_current": 333447168, "memory_peak": 333447168,
             "memory_max": 335544320, "oom_kill_total": 1},
  "qos": {"before": "Guaranteed", "after": "Guaranteed"},
  "hpa": null,
  "eviction": null,
  "node_conditions": {"MemoryPressure": "False"},
  "node_allocatable": {"cpu_millicores": 3760, "memory_mib": 14580},
  "startup": {"probe_failures_in_window": 0, "failures_outside_window": 0,
              "startup_peak": 0.94, "steady_p95": 0.18},
  "kubethrifty_changes": [
    {"kind": "merged_pr", "pr_url": "https://github.com/OWNER/REPO/pull/212",
     "recommendation_id": 1188, "commit": "a1b2c3d",
     "applied_at": "2026-08-16T00:12:00Z", "hours_before_incident": 14.5,
     "container": "app", "delta": {"memory": "1Gi -> 320Mi"}}
  ],
  "collector_versions": {"cgroup_truth": "1.0.0", "engine": "1.3.0"}
}
```

Rules read only from this document. Adding a rule usually means adding a *collector* field first — resist
the temptation to reach into the cluster from the rule.

---

## 4. The CI harness

```python
# evals/detective_eval.py
"""Three gates: accuracy, calibration, determinism. Exit 1 on any failure."""
import json, sys
from detective.engine import investigate, verdicts_digest, RULESET_VERSION

TARGET_TOP1_ACCURACY = 0.90
MAX_BRIER = 0.08
MAX_ECE = 0.10


def brier(pairs) -> float:                       # pairs: [(confidence, was_correct)]
    return sum((c - int(ok)) ** 2 for c, ok in pairs) / len(pairs)


def ece(pairs, bins: int = 10) -> float:
    total, err = len(pairs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(c, ok) for c, ok in pairs if lo < c <= hi]
        if not bucket:
            continue
        conf = sum(c for c, _ in bucket) / len(bucket)
        acc = sum(int(ok) for _, ok in bucket) / len(bucket)
        err += (len(bucket) / total) * abs(conf - acc)
    return err


def main() -> int:
    corpus = json.load(open("evals/corpus/incidents.json"))
    pairs, correct, digests = [], 0, []
    for case in corpus:
        vs = investigate(case["bundle"])
        digests.append(verdicts_digest(vs))
        top = vs[0] if vs else None
        expected = case["ground_truth"]
        ok = (top.rule_id == expected) if top else (expected == "NO_VERDICT")
        correct += int(ok)
        if top:
            pairs.append((top.confidence, ok))

    acc, b, e = correct / len(corpus), brier(pairs), ece(pairs)
    frozen = json.load(open("evals/corpus/digests.json"))
    deterministic = digests == frozen.get(RULESET_VERSION)

    print(f"cases={len(corpus)} top1={acc:.3f} brier={b:.4f} ece={e:.4f} deterministic={deterministic}")
    failures = []
    if acc < TARGET_TOP1_ACCURACY: failures.append(f"accuracy {acc:.3f}")
    if b > MAX_BRIER:              failures.append(f"brier {b:.4f}")
    if e > MAX_ECE:                failures.append(f"ECE {e:.4f}")
    if not deterministic:          failures.append("non-deterministic verdicts")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

When a rule's confidence changes legitimately (better evidence, more cases), re-check calibration *before*
re-freezing `digests.json`, and record why in the commit message. Re-freezing a digest without looking at
Brier/ECE defeats the whole gate.

---

## 5. Chaos generators

`evals/corpus/generate.py` drives a `kind` cluster and manufactures labelled incidents on demand.

| Generator | How it induces the failure | Ground truth |
|---|---|---|
| `oom_squeeze` | Container with a known allocation profile; memory limit set just below its peak | `MEM_LIMIT_TOO_LOW` |
| `reclaim_thrash` | Working set ≈ 95% of limit with continuous page-cache churn — high PSI, no OOM | `MEM_PRESSURE_NO_KILL` |
| `throttle_squeeze` | Busy loop with a CPU limit at 40% of demand | `CPU_LIMIT_TOO_LOW` |
| `node_squeeze` | Over-commit a node, then apply memory pressure from a sibling | `NODE_PRESSURE_EVICTION` |
| `qos_demotion` | Change limits so Guaranteed → Burstable, then apply node pressure | `QOS_DEMOTION_EVICTION` |
| `hpa_paradox` | HPA at 70%, cut requests 4× in place, hold traffic flat, observe replica growth | `HPA_COUPLING_STORM` |
| `startup_starve` | JVM-like warmup with steady-state-sized CPU; liveness fails during boot only | `STARTUP_STARVATION` |
| `infeasible_resize` | Request an in-place growth larger than node allocatable | `RESIZE_INFEASIBLE_STUCK` |
| `red_herring_imagepull` | Bad image tag, healthy resources | `NO_VERDICT` |
| `red_herring_dns` | Broken DNS config, healthy resources | `NO_VERDICT` |
| `red_herring_healthy` | Nothing wrong at all | `NO_VERDICT` |

Each generator emits `{"name": ..., "bundle": {...}, "ground_truth": "..."}` appended to
`evals/corpus/incidents.json`. Commit the bundles; CI never needs a cluster.

---

## 6. Narrator (optional)

```python
# detective/narrative.py
class Narrator:
    """Turns a Verdict into prose. NEVER produces or alters a verdict."""
    def narrate(self, verdict, bundle) -> str: ...


class TemplateNarrator(Narrator):        # DEFAULT: deterministic, offline, free
    def narrate(self, verdict, bundle) -> str:
        return (f"{verdict.rule_id} ({verdict.confidence:.0%}): {verdict.summary} "
                f"Evidence: {verdict.evidence}. Suggested: {verdict.remediation}")


class LLMNarrator(Narrator):             # OPTIONAL: model id from config, never hardcoded
    def __init__(self, client, model_id: str, max_tokens: int = 400): ...
    def narrate(self, verdict, bundle) -> str:
        # Prompt carries the verdict + evidence and instructs: rephrase only, invent nothing.
        # Discard the output if it names a rule_id absent from the verdict list, and fall back
        # to TemplateNarrator. Off by default.
        ...
```

---

## 7. Database rows

```sql
CREATE TABLE verdicts (
    id                BIGSERIAL PRIMARY KEY,
    bundle_sha256     TEXT UNIQUE NOT NULL,        -- content-addressed evidence
    cluster           TEXT NOT NULL,
    namespace         TEXT NOT NULL,
    workload          TEXT NOT NULL,
    observed_at       TIMESTAMPTZ NOT NULL,
    rule_id           TEXT NOT NULL,
    confidence        NUMERIC(4,3) NOT NULL,
    attributed_change JSONB,                       -- {pr, commit, recommendation_id, kind, delta}
    evidence          JSONB NOT NULL,
    ruleset_version   TEXT NOT NULL,
    engine_sha        TEXT NOT NULL,               -- determinism anchor
    ground_truth      TEXT                         -- backfilled for calibration scoring
);
CREATE INDEX ON verdicts (cluster, namespace, workload, observed_at DESC);
CREATE INDEX ON verdicts (rule_id, observed_at DESC);
```

Recurrence is a signal: *"this workload has produced `MEM_PRESSURE_NO_KILL` 4× in 30 days"* raises
confidence and escalates from a per-run margin bump to a permanent policy on that workload.

---

## 8. Competitive grid — and keeping the claim honest

| Dimension | General deterministic investigators | LLM investigators (k8sgpt, HolmesGPT) | **ThriftDetective** |
|---|---|---|---|
| Scope | Broad cluster failure modes | Broad, open-ended | 8 resource-shaped modes; everything else `NO_VERDICT` |
| Determinism | Usually yes | No | Yes, **hash-asserted in CI** |
| Change attribution | Identifies a likely cause | Narrates a likely cause | Names the **specific PR / recommendation / resize event** with a time delta |
| Acts on the conclusion | Reports | Reports | Opens the rollback PR, blocks reduction, widens that workload's margin |
| Evidence depth | Events, statuses, logs, metrics | Same, plus prose | + **PSI** and **cgroup truth** (`memory.peak`, `memory.events`) |
| Confidence | Scores of unclear provenance | Vibes | **Calibrated and CI-gated** (Brier + ECE) |
| Corpus | Hand-collected scenarios | None | ≥25 **chaos-generated** labelled cases with red herrings |
| Counterfactual | None | None | The **rehearsal record**: "this size was observed safe at 14:02 and OOMKilled at 22:15 — traffic changed, not the size" |
| Needs an API key | No | Yes | No (LLM narration optional, off by default) |

**Honesty rule.** Before publishing any head-to-head claim, open the current README of each tool you name
and verify the row. If a tool already does something on this list, narrow the claim rather than dropping
the feature — change attribution and calibration remain yours because they depend on KubeThrifty owning
the change log. An unverified comparative claim is the one way this differentiator backfires in an
interview.
