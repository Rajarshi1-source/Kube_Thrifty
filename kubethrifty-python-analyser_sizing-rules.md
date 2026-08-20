# Sizing Rules — Implementation Reference

The complete sizing module, the floor composition, the verification signal module, and the labelled eval
cases. Load this before writing or changing any sizing maths — the CPU/memory asymmetry is the easiest
thing in the project to get subtly and dangerously wrong.

## Contents
1. `sizing.py` — the complete module
2. Why `limit == request` for memory but not CPU
3. `verification/signals.py` — the complete module
4. Matching PromQL
5. Labelled eval cases the sizing gate must pass
6. Recommendation row fields Rev 3 added

---

## 1. `analyser/src/sizing.py`

```python
"""
Resource sizing rules for KubeThrifty.

Design rule: CPU and memory are NOT symmetric.

  CPU     is compressible. Exceeding the limit means CFS throttling — the app gets slower, then
          recovers. A percentile is an acceptable statistic.
  Memory  is incompressible. Exceeding the limit means the kernel OOMKills the container. A percentile
          is the WRONG statistic, because it discards the tail that kills you. Size memory off the
          observed PEAK.

Every number returned here is floored by an absolute minimum, by the predictive floor from the
forecaster, and — when available — by the empirical floor proven by a Resize Rehearsal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- configuration
MIN_CPU_MILLICORES = 50
MIN_MEMORY_MIB = 64

CPU_REQUEST_MARGIN = 1.20        # over P95 — throttling is survivable
CPU_LIMIT_MARGIN = 1.50          # over P99
MEM_REQUEST_MARGIN = 1.25        # over observed PEAK — OOM is not survivable
MEM_HEADROOM_FOR_GC = 1.10       # extra for GC'd runtimes (JVM/Node/dotnet heaps)

MIN_SAVINGS_THRESHOLD = 0.10     # ignore <10% wins; churn isn't free
HIGH_VARIABILITY_THRESHOLD = 0.50
MIN_DATA_POINTS = 168            # 7 days of hourly samples

GC_RUNTIME_HINTS = ("java", "jvm", "node", "dotnet", "clr", "golang-heavy")


@dataclass(frozen=True)
class UsageStats:
    """Everything the sizer is allowed to look at, in canonical units."""
    p50: float
    p95: float
    p99: float
    peak: float                              # max working set / cgroup memory.peak
    mean: float
    stddev: float
    samples: int
    psi_stalled_ratio: Optional[float] = None   # 0..1 (cpu or memory 'full'); None == not observed
    throttle_ratio: Optional[float] = None      # 0..1
    oom_events: int = 0
    rehearsed_floor: Optional[float] = None     # proven-safe value


@dataclass(frozen=True)
class Sizing:
    request: float
    limit: float
    rationale: str
    binding_constraint: str                  # which floor won — printed in the PR body


def _units_floor(resource: str) -> float:
    return MIN_CPU_MILLICORES if resource == "cpu" else MIN_MEMORY_MIB


def summarize(values: pd.Series, peak_series: Optional[pd.Series] = None) -> UsageStats:
    """
    peak_series: per-interval max (cgroup memory.peak deltas, or max_over_time() from Prometheus).
    If absent, fall back to the sample max — which UNDERSTATES the true peak between scrapes. Record
    that in the rationale so the reviewer knows the number is conservative only by luck.
    """
    v = values.dropna()
    peak = float(peak_series.max()) if peak_series is not None else float(v.max())
    return UsageStats(
        p50=float(np.percentile(v, 50)), p95=float(np.percentile(v, 95)),
        p99=float(np.percentile(v, 99)), peak=peak,
        mean=float(v.mean()), stddev=float(v.std()), samples=int(len(v)),
    )


def size_cpu(s: UsageStats, predictive_floor: float = 0.0) -> Sizing:
    """
    CPU: percentile-based, forecast-floored. We deliberately do NOT set the CPU limit equal to the
    request: a tight CPU limit converts spare node capacity into latency via CFS throttling, and limits
    have no effect on what you are billed (requests drive scheduling and therefore node count).
    """
    floors = {
        "p95_margin": s.p95 * CPU_REQUEST_MARGIN,
        "forecast": predictive_floor,
        "rehearsed": s.rehearsed_floor or 0.0,
        "absolute_min": _units_floor("cpu"),
    }
    request = max(floors.values())
    binding = max(floors, key=floors.get)
    limit = max(s.p99 * CPU_LIMIT_MARGIN, request * 1.5)

    rationale = (f"CPU: P95={s.p95:.0f}m, P99={s.p99:.0f}m, peak={s.peak:.0f}m. "
                 f"request={request:.0f}m (bound by {binding}), limit={limit:.0f}m. "
                 "CPU is compressible: over-limit means throttling, not death.")
    if s.throttle_ratio is not None and s.throttle_ratio > 0.01:
        rationale += (f" NOTE: already throttled {s.throttle_ratio:.1%} of periods at the CURRENT "
                      "limit — do not reduce the limit; consider raising it.")
    return Sizing(request, limit, rationale, binding)


def size_memory(s: UsageStats, predictive_floor: float = 0.0,
                runtime_hint: str = "") -> Sizing:
    """Memory: PEAK-based, and limit == request by default (predictable eviction behaviour, no QoS
    demotion). See §2 of this reference for why."""
    gc_factor = MEM_HEADROOM_FOR_GC if any(h in runtime_hint.lower() for h in GC_RUNTIME_HINTS) else 1.0

    floors = {
        "peak_margin": s.peak * MEM_REQUEST_MARGIN * gc_factor,
        "forecast": predictive_floor,
        "rehearsed": s.rehearsed_floor or 0.0,
        "absolute_min": _units_floor("memory"),
    }
    request = max(floors.values())
    binding = max(floors, key=floors.get)

    rationale = (f"Memory: peak={s.peak:.0f}Mi (P95={s.p95:.0f}Mi — deliberately NOT the sizing basis), "
                 f"margin={MEM_REQUEST_MARGIN:.2f}"
                 + (f" × GC headroom {gc_factor:.2f}" if gc_factor > 1.0 else "")
                 + f". request=limit={request:.0f}Mi (bound by {binding}).")
    if s.oom_events:
        rationale += (f" BLOCKED-DOWN: {s.oom_events} OOM event(s) in-window; memory may only be "
                      "increased for this container.")
        request = max(request, s.peak * 1.5)
    if s.psi_stalled_ratio is not None and s.psi_stalled_ratio > 0.02:
        rationale += (f" PSI shows {s.psi_stalled_ratio:.1%} full-memory stall — already under reclaim "
                      "pressure; treat any reduction as unsafe.")
    return Sizing(request, request, rationale, binding)      # limit == request
```

Action and confidence classification (`REDUCE` / `INCREASE` / `KEEP` / `NEEDS_REVIEW`, and
`HIGH` / `MEDIUM` / `LOW`) stays where Rev 1 put it, driven by *data trustworthiness*
(`stddev/mean`, sample count), not by how large the saving looks. A big saving on untrustworthy data is
exactly the recommendation you do not want to ship.

---

## 2. Why `limit == request` for memory but not CPU

A standard follow-up question. The answer:

- **Memory:** setting request equal to limit makes the allocation predictable and puts the container at
  the *bottom* of the eviction preference order for memory-pressure evictions. Leaving a gap invites the
  scheduler to overcommit memory the kernel cannot reclaim, and it risks a Guaranteed → Burstable QoS
  demotion (see the QoS guard in kubethrifty-cost-and-packing).
- **CPU:** the opposite. A tight CPU limit throttles a burst the node could have absorbed for free.
  Limits do not reduce your bill — requests do, because requests drive scheduling and therefore node
  count. There is a legitimate school of thought (worth naming in an interview) that says omit CPU limits
  entirely and let requests do the scheduling.

---

## 3. `analyser/src/verification/signals.py`

```python
"""
Regression detection for a merged (or rehearsed) resource change.

Compare RATIOS, not counters. Throttled-period counters scale with window length, replica count and the
CFS period; throttled_periods / periods does not.

Trip rules (any one suffices):
  1. OOM      — any OOM kill attributable to the changed container (by counter).
  2. Throttle — ratio rose by >= THROTTLE_RATIO_DELTA AND now exceeds THROTTLE_RATIO_FLOOR.
  3. Restarts — restart count increased beyond tolerance.
  4. PSI      — 'full' memory or cpu stall share above PSI_FULL_CEILING.
  5. SLO      — optional app-level burn-rate breach.
"""
from dataclasses import dataclass, field
from typing import Optional

THROTTLE_RATIO_DELTA = 0.05
THROTTLE_RATIO_FLOOR = 0.02
RESTART_TOLERANCE = 0
PSI_FULL_CEILING = 0.05


@dataclass
class Window:
    throttled_periods: float
    cfs_periods: float
    restarts: float
    oom_kills: float
    psi_cpu_full: float = 0.0            # seconds stalled / seconds elapsed
    psi_mem_full: float = 0.0

    @property
    def throttle_ratio(self) -> float:
        return self.throttled_periods / self.cfs_periods if self.cfs_periods else 0.0


@dataclass
class Verdict:
    regressed: bool
    reasons: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


def evaluate(before: Window, after: Window, slo_burn_rate: Optional[float] = None) -> Verdict:
    reasons, evidence = [], {
        "throttle_ratio_before": round(before.throttle_ratio, 5),
        "throttle_ratio_after": round(after.throttle_ratio, 5),
        "psi_mem_full_after": round(after.psi_mem_full, 5),
        "psi_cpu_full_after": round(after.psi_cpu_full, 5),
        "oom_kills_after": after.oom_kills,
        "restarts_delta": after.restarts - before.restarts,
    }

    if after.oom_kills > before.oom_kills:
        reasons.append("OOMKilled after change — memory floor was too low")

    delta = after.throttle_ratio - before.throttle_ratio
    if delta >= THROTTLE_RATIO_DELTA and after.throttle_ratio >= THROTTLE_RATIO_FLOOR:
        reasons.append(f"CPU throttle ratio rose {delta:.1%} to {after.throttle_ratio:.1%}")

    if (after.restarts - before.restarts) > RESTART_TOLERANCE:
        reasons.append("container restarted after change")

    if after.psi_mem_full > PSI_FULL_CEILING:
        reasons.append(f"memory PSI 'full' at {after.psi_mem_full:.1%} of wall time")
    if after.psi_cpu_full > PSI_FULL_CEILING:
        reasons.append(f"cpu PSI 'full' at {after.psi_cpu_full:.1%} of wall time")

    if slo_burn_rate is not None and slo_burn_rate > 1.0:
        reasons.append(f"SLO error budget burning at {slo_burn_rate:.2f}×")

    return Verdict(bool(reasons), reasons, evidence)
```

---

## 4. Matching PromQL (all rates over the same window length — that is the point)

```promql
# throttle ratio per container
sum by (namespace,pod,container) (rate(container_cpu_cfs_throttled_periods_total[15m]))
/
sum by (namespace,pod,container) (rate(container_cpu_cfs_periods_total[15m]))

# PSI: share of wall time fully stalled (1.34 beta / 1.36 GA)
sum by (namespace,pod,container) (rate(container_pressure_memory_stalled_seconds_total[15m]))

# OOM kills as a COUNTER, not the flapping pod-status gauge
sum by (namespace,pod,container) (increase(container_oom_events_total[1h]))

# memory high-water mark for sizing (fallback when the cgroup collector is absent)
max_over_time(container_memory_working_set_bytes[7d])
```

---

## 5. Labelled eval cases the sizing gate must pass

```python
# evals/sizing_eval.py asserts on DECISIONS, not floats.
CASES = [
    ("steady_idle",                "cpu",    "expect_reduction"),
    ("rising_trend",               "cpu",    "expect_forecast_floor_binds"),
    ("noisy_short",                "cpu",    "expect_needs_review"),
    ("throttled_at_current_limit", "cpu",    "expect_limit_not_reduced"),
    ("low_util_high_pressure",     "memory", "expect_no_reduction"),
    ("spiky_peak_gt_p95",          "memory", "expect_request_ge_peak"),
    ("oom_history",                "memory", "expect_increase_only"),
    ("gc_runtime",                 "memory", "expect_gc_headroom_applied"),
]
```

`low_util_high_pressure` and `spiky_peak_gt_p95` are the two that matter most: they are the cases where a
percentile-based sizer would ship an outage, so they are the regression tests that keep Rev 3's correction
from silently reverting.

---

## 6. Recommendation row fields Rev 3 added

```sql
ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS binding_constraint TEXT,   -- peak_margin|forecast|rehearsed|absolute_min
    ADD COLUMN IF NOT EXISTS sizing_basis TEXT,         -- 'peak' for memory, 'p95' for cpu
    ADD COLUMN IF NOT EXISTS evidence_tier TEXT,        -- rehearsed|modelled|partial
    ADD COLUMN IF NOT EXISTS rehearsal_id BIGINT REFERENCES rehearsals(id),
    ADD COLUMN IF NOT EXISTS hpa_coupled BOOLEAN NOT NULL DEFAULT FALSE;
```

Every one of these exists so the PR body can explain *why* a number is what it is. A recommendation whose
reasoning cannot be printed is a recommendation nobody will merge.
