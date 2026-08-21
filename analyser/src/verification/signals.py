#!/usr/bin/env python3
"""
verification/signals.py -- did this change make things worse?

Used in two places, deliberately by the same code path:

  * post-merge verification, comparing the 24h before a merged PR against the 24h after;
  * the Resize Rehearsal, comparing a 300s baseline against a 1800s observation window.

RATIOS, NEVER RAW COUNTERS. `container_cpu_cfs_throttled_periods_total` is a counter: it grows with
window length, with replica count, and with the CFS period itself. Comparing 4,000 throttled
periods against 1,200 across two windows of different length is meaningless, and a tool that does
it will report a regression every time it happens to look at a longer window. The only comparable
quantity is throttled/total within each window.

OOM detection uses a COUNTER (`container_oom_events_total`), never
`kube_pod_container_status_last_terminated_reason`. That gauge flaps: it reports the last
termination reason, so it flips to OOMKilled and then away again on the next restart, and whether
you see it depends entirely on when you scraped.

Pure: no I/O, no clock, no randomness. Unit-testable and eval-gradeable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- thresholds. Not configurable, on purpose: see config.py. ----------------------------------

# A throttle ratio may rise by at most this much before the change is a regression. Some increase
# is expected and fine -- the workload has less CPU headroom than it did, which is the entire point
# of right-sizing. Five points is the line between "tighter" and "hurting".
THROTTLE_RATIO_DELTA = 0.05

# Below this, throttling is noise. Container runtimes throttle a little at almost any limit, and a
# jump from 0.001 to 0.011 is a 10x increase that means nothing. Without a floor, ratio comparisons
# generate false regressions on healthy workloads.
THROTTLE_RATIO_FLOOR = 0.02

# Zero. Not "a few". A restart inside the observation window means the workload died; there is no
# version of that which is a successful right-sizing.
RESTART_TOLERANCE = 0

# PSI `full` means EVERY runnable task was stalled -- not merely delayed, completely stopped. Above
# 5% of wall time, the workload is not "running a bit hot", it is intermittently not running.
PSI_FULL_CEILING = 0.05


@dataclass(frozen=True)
class Window:
    """One observation window. Every field may be None, and None means NOT OBSERVED.

    Counters are raw here and converted to ratios by the properties below; that keeps the unit
    conversion in exactly one place instead of at each call site.
    """
    cfs_periods: float | None = None
    throttled_periods: float | None = None
    oom_events: int | None = None
    restarts: int | None = None
    psi_cpu_full_ratio: float | None = None
    psi_mem_full_ratio: float | None = None
    p95_cpu_cores: float | None = None
    peak_memory_mib: float | None = None
    duration_seconds: float | None = None

    @property
    def throttle_ratio(self) -> float | None:
        """Throttled periods as a fraction of total periods, or None if not measurable.

        Returns None -- not 0.0 -- when the denominator is missing or zero. A container that
        recorded no CFS periods was not "never throttled"; it was not observed.
        """
        if self.cfs_periods is None or self.throttled_periods is None:
            return None
        if self.cfs_periods <= 0:
            return None
        return self.throttled_periods / self.cfs_periods


@dataclass(frozen=True)
class Verdict:
    regressed: bool
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        return "regressed" if self.regressed else "safe"


def evaluate(before: Window, after: Window, slo_burn_rate: float | None = None) -> Verdict:
    """Compare two windows. Any single trip condition is enough to call it regressed.

    Deliberately asymmetric: this function is quick to say "regressed" and slow to say "safe". A
    false regression costs a reverted rehearsal and a `modelled` recommendation instead of a
    `rehearsed` one. A false "safe" ships an OOMKill to production and tells the operator it was
    verified. Those costs are not comparable, so the thresholds are not balanced.
    """
    reasons: list[str] = []
    signals: dict = {}

    # --- observed death. Unconditional. ---------------------------------------------------------
    if after.oom_events is not None and after.oom_events > 0:
        reasons.append(f"{after.oom_events} OOM event(s) in the observation window")
    signals["oom_events_after"] = after.oom_events

    if after.restarts is not None and after.restarts > RESTART_TOLERANCE:
        reasons.append(
            f"{after.restarts} container restart(s) (tolerance {RESTART_TOLERANCE})"
        )
    signals["restarts_after"] = after.restarts

    # --- throttling, as a ratio delta above a noise floor ---------------------------------------
    tr_before, tr_after = before.throttle_ratio, after.throttle_ratio
    signals["throttle_ratio_before"] = tr_before
    signals["throttle_ratio_after"] = tr_after
    if tr_before is not None and tr_after is not None:
        delta = tr_after - tr_before
        signals["throttle_ratio_delta"] = round(delta, 6)
        # BOTH conditions required. A rise from 0.001 to 0.055 clears the delta but the absolute
        # level is still trivial; a level of 0.30 that did not move is pre-existing, not caused by
        # this change. Only a meaningful rise TO a meaningful level is a regression.
        if delta > THROTTLE_RATIO_DELTA and tr_after > THROTTLE_RATIO_FLOOR:
            reasons.append(
                f"throttle ratio rose {delta:.1%} to {tr_after:.1%} "
                f"(delta gate {THROTTLE_RATIO_DELTA:.0%}, floor {THROTTLE_RATIO_FLOOR:.0%})"
            )
    else:
        # Not a regression, but it IS a downgrade in what we can claim.
        signals["throttle_comparable"] = False

    # --- PSI: stalled, not merely slow ----------------------------------------------------------
    for label, value in (("cpu", after.psi_cpu_full_ratio), ("memory", after.psi_mem_full_ratio)):
        signals[f"psi_{label}_full_after"] = value
        if value is not None and value > PSI_FULL_CEILING:
            reasons.append(
                f"PSI {label} full stall {value:.1%} exceeds ceiling {PSI_FULL_CEILING:.0%}"
            )

    # --- the application's own opinion, when it has one -----------------------------------------
    if slo_burn_rate is not None:
        signals["slo_burn_rate"] = slo_burn_rate
        if slo_burn_rate > 1.0:
            reasons.append(f"SLO error budget burn rate {slo_burn_rate:.2f} > 1.0")

    return Verdict(regressed=bool(reasons), reasons=reasons, signals=signals)
