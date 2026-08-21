#!/usr/bin/env python3
"""
sizing.py -- Rev 3 sizing rules. CPU and memory are NOT symmetric.

Rev 2 of this starter sized both resources as `P95 * 1.20`. That is correct for CPU and wrong for
memory, and the reason is the whole point of this file:

  CPU     is compressible. Exceeding the limit means CFS throttling: the app gets slower, then
          recovers. A percentile is an acceptable statistic.
  Memory  is incompressible. Exceeding the limit means the kernel OOMKills the container. A percentile
          DISCARDS the top 5% of samples -- exactly the samples that kill you. Size memory off the
          observed PEAK (cgroup memory.peak where available, else max_over_time()).

Two more Rev 3 rules live here:

  * Floors compose with max(), never min(). The statistical floor, the forecast upper bound, and a
    rehearsal-proven floor can only ever RAISE a request. `binding_constraint` records which one won,
    so the PR body can explain the number.
  * Observed suffering is a hard stop, not a penalty. An OOM event in-window, or PSI showing sustained
    stall, blocks a reduction outright -- a workload with low average usage and high memory pressure is
    a workload every percentile-based tool would happily shrink into an outage.

Pure and deterministic: no I/O, no clock, no randomness. Unit-testable and eval-gradeable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# --- constants (one source of truth for the analyser and the evals) ----------------------------
MIN_CPU_CORES = 0.05             # 50m
MIN_MEMORY_MIB = 64.0

CPU_REQUEST_MARGIN = 1.20        # over P95
CPU_LIMIT_MARGIN = 1.50          # over P99
MEM_REQUEST_MARGIN = 1.25        # over observed PEAK
MEM_HEADROOM_FOR_GC = 1.10       # extra for GC'd runtimes: RSS lags real demand

MIN_DATA_POINTS = 168            # 7 days hourly; below this we do not trust an automated change
PSI_STALL_BLOCK = 0.02           # >2% of wall time stalled -> reductions are unsafe
THROTTLE_NOTE = 0.01             # >1% of periods throttled -> never lower the CPU limit

GC_RUNTIME_HINTS = ("java", "jvm", "node", "dotnet", "clr")


@dataclass(frozen=True)
class UsageStats:
    """Everything the sizer is allowed to look at. `None` means NOT OBSERVED, never zero."""
    p50: float
    p95: float
    p99: float
    peak: float
    samples: int
    psi_stalled_ratio: float | None = None
    throttle_ratio: float | None = None
    oom_events: int = 0
    rehearsed_floor: float | None = None
    runtime_hint: str = ""


@dataclass(frozen=True)
class Sizing:
    resource: str                     # "cpu" | "memory"
    request: float
    limit: float
    action: str                       # reduce | increase | keep | needs_review
    binding_constraint: str           # p95_margin | peak_margin | forecast | rehearsed | absolute_min
    sizing_basis: str                 # "p95" for cpu, "peak" for memory
    reduction_blocked: bool           # True when observed suffering forbids a cut
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


def _action(current: float, request: float, blocked: bool, samples: int,
            hysteresis: float = 0.10) -> str:
    if samples < MIN_DATA_POINTS:
        return "needs_review"
    if blocked and request <= current:
        return "keep"                              # we may not cut, and we do not need to grow
    if request < current * (1 - hysteresis):
        return "reduce"
    if request > current * (1 + hysteresis):
        return "increase"
    return "keep"


def size_cpu(s: UsageStats, current_request: float, current_limit: float,
             predictive_floor: float = 0.0) -> Sizing:
    floors = {
        "p95_margin": s.p95 * CPU_REQUEST_MARGIN,
        "forecast": predictive_floor,
        "rehearsed": s.rehearsed_floor or 0.0,
        "absolute_min": MIN_CPU_CORES,
    }
    request = max(floors.values())
    binding = max(floors, key=floors.get)

    limit = max(s.p99 * CPU_LIMIT_MARGIN, request * 1.5)
    throttled = s.throttle_ratio is not None and s.throttle_ratio > THROTTLE_NOTE
    if throttled:
        # The current limit is already biting: never propose a lower one.
        limit = max(limit, current_limit)

    stalled = s.psi_stalled_ratio is not None and s.psi_stalled_ratio > PSI_STALL_BLOCK
    blocked = bool(stalled or s.oom_events)

    rationale = (f"cpu: P95={s.p95:.3f} P99={s.p99:.3f} peak={s.peak:.3f}; "
                 f"request={request:.3f} (bound by {binding}); limit={limit:.3f}. "
                 "cpu is compressible -- over-limit means throttling, not death.")
    if throttled:
        rationale += (f" already throttled {s.throttle_ratio:.1%} of periods at the current limit; "
                      "limit not reduced.")
    if stalled:
        rationale += f" PSI shows {s.psi_stalled_ratio:.1%} stall; reduction blocked."

    return Sizing("cpu", round(request, 6), round(limit, 6),
                  _action(current_request, request, blocked, s.samples),
                  binding, "p95", blocked, rationale)


def size_memory(s: UsageStats, current_request: float, current_limit: float,
                predictive_floor: float = 0.0) -> Sizing:
    gc_factor = (MEM_HEADROOM_FOR_GC
                 if any(h in s.runtime_hint.lower() for h in GC_RUNTIME_HINTS) else 1.0)
    floors = {
        "peak_margin": s.peak * MEM_REQUEST_MARGIN * gc_factor,
        "forecast": predictive_floor,
        "rehearsed": s.rehearsed_floor or 0.0,
        "absolute_min": MIN_MEMORY_MIB,
    }
    request = max(floors.values())
    binding = max(floors, key=floors.get)

    stalled = s.psi_stalled_ratio is not None and s.psi_stalled_ratio > PSI_STALL_BLOCK
    blocked = bool(stalled or s.oom_events)
    if s.oom_events:
        # Observed death: memory may only go up for this container.
        request = max(request, s.peak * 1.5, current_request)
        binding = "peak_margin"
    if stalled:
        # Reclaim thrash: average usage understates demand, so never cut.
        request = max(request, current_request)

    rationale = (f"memory: peak={s.peak:.1f}Mi (P95={s.p95:.1f}Mi -- deliberately NOT the basis), "
                 f"margin={MEM_REQUEST_MARGIN}"
                 + (f" x GC headroom {gc_factor}" if gc_factor > 1.0 else "")
                 + f"; request=limit={request:.1f}Mi (bound by {binding}).")
    if s.oom_events:
        rationale += f" {s.oom_events} OOM event(s) in-window: increases only."
    if stalled:
        rationale += f" PSI shows {s.psi_stalled_ratio:.1%} full-memory stall: reduction blocked."

    # limit == request: predictable allocation, bottom of the eviction order, no QoS demotion.
    return Sizing("memory", round(request, 6), round(request, 6),
                  _action(current_request, request, blocked, s.samples),
                  binding, "peak", blocked, rationale)
