#!/usr/bin/env python3
"""
recommendation_engine.py -- orchestration only. All sizing maths lives in `sizing.py`.

That split is deliberate and load-bearing. `sizing.py` is pure, deterministic and graded by
`sizing_eval.py`; this module does I/O, assembles context, and decides what to do with the result.
If a margin or a threshold ever appears in this file, the eval gate has stopped grading the code
that actually ships.

The composition rule, restated because it is the invariant everything else protects:

    request = max(statistical_floor, forecast_upper, rehearsed_floor, absolute_min)

Never min(). Every clever input is a FLOOR that can only raise a request. A forecast that predicts
lower demand does not shrink anything; it simply stops being the binding constraint.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum

import pandas as pd

from .forecasting import Forecaster, default_forecaster
from .metric_collector import MetricCollector, WorkloadRef
from .sizing import MIN_DATA_POINTS, Sizing, UsageStats, size_cpu, size_memory

log = logging.getLogger(__name__)

# Below this proportional change, leave it alone. Churning a PR to reclaim 4% of a request costs
# more in review attention and rollout risk than it saves.
MIN_SAVINGS_THRESHOLD = 0.10


class Action(StrEnum):
    REDUCE = "reduce"
    INCREASE = "increase"
    KEEP = "keep"
    NEEDS_REVIEW = "needs_review"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceTier(StrEnum):
    """What we actually know. Never overstate it.

    REHEARSED  the candidate size ran on a live pod and nothing regressed.
    MODELLED   percentiles plus a forecast. Sound, but nothing was tried.
    PARTIAL    a signal the sizer wanted was unavailable (usually PSI), so the safety checks ran
               with less information than they were designed for.
    """
    REHEARSED = "rehearsed"
    MODELLED = "modelled"
    PARTIAL = "partial"


@dataclass
class ResourceRecommendation:
    namespace: str
    workload: str
    container: str
    resource: str                       # "cpu" | "memory"

    current_request: float | None
    current_limit: float | None
    recommended_request: float
    recommended_limit: float

    action: Action
    confidence: Confidence
    evidence_tier: EvidenceTier

    binding_constraint: str             # which floor won
    sizing_basis: str                   # "p95" (cpu) | "cgroup_memory_peak" / "working_set_max"
    reduction_blocked: bool
    rationale: str

    samples: int
    forecast_model: str | None = None
    rehearsal_id: str | None = None
    hpa_coupled: bool = False
    observed: dict = field(default_factory=dict)

    @property
    def waste_pct(self) -> float | None:
        """Proportional over-provisioning. A PERCENTAGE, never a currency amount.

        Per-pod waste is a ranking signal. The only figure that carries a currency symbol is the
        bin-packer's node-count delta, because that is the only thing a cloud actually bills.
        """
        if not self.current_request or self.current_request <= 0:
            return None
        return (self.current_request - self.recommended_request) / self.current_request

    def to_dict(self) -> dict:
        d = asdict(self)
        d["waste_pct"] = self.waste_pct
        return d


class RecommendationEngine:
    def __init__(
        self,
        collector: MetricCollector,
        forecaster: Forecaster | None = None,
        rehearsed_floors: dict[str, float] | None = None,
    ) -> None:
        self.collector = collector
        self.forecaster = forecaster or default_forecaster()
        # Keyed "namespace/workload/container/resource". Populated from the `rehearsals` table for
        # outcomes recorded as `safe`.
        self.rehearsed_floors = rehearsed_floors or {}

    # -- the predictive floor --------------------------------------------------------------------
    def _forecast_floor(self, history: pd.Series | None, horizon_hours: int = 168) -> tuple[float, str | None]:
        """Return (floor, model_id). (0.0, None) when the forecast is not usable.

        Only `Forecast.upper` is consumed, and only as a floor. `confident=False` means the series
        was too short or the backend unavailable, and the caller falls back to the trailing
        statistical floor -- a documented product guarantee, not an error path.
        """
        if history is None or len(history) == 0:
            return 0.0, None
        try:
            f = self.forecaster.forecast(history, horizon_hours)
        except Exception as e:                                    # noqa: BLE001
            # A forecasting failure must never block a recommendation. Degrade, do not abort.
            log.warning("forecast failed (%s); falling back to statistical floor", type(e).__name__)
            return 0.0, None
        if not f.confident:
            log.info("forecast not confident (model=%s); using statistical floor", f.model_id)
            return 0.0, f.model_id
        return float(f.upper.max()), f.model_id

    # -- evidence + confidence -------------------------------------------------------------------
    @staticmethod
    def _evidence_tier(stats: UsageStats, sizing_basis: str, rehearsal_id: str | None) -> EvidenceTier:
        if rehearsal_id:
            return EvidenceTier.REHEARSED
        # PSI unavailable, or memory sized off a sampled gauge rather than the kernel high-water
        # mark. Both mean the safety checks ran on less than they were designed for.
        if stats.psi_stalled_ratio is None or sizing_basis == "working_set_max":
            return EvidenceTier.PARTIAL
        return EvidenceTier.MODELLED

    @staticmethod
    def _confidence(stats: UsageStats, sizing: Sizing, tier: EvidenceTier) -> Confidence:
        if stats.samples < MIN_DATA_POINTS:
            return Confidence.LOW
        if tier is EvidenceTier.REHEARSED:
            return Confidence.HIGH
        if tier is EvidenceTier.PARTIAL:
            return Confidence.MEDIUM
        # A blocked reduction means we observed suffering; the number is sound but the situation
        # deserves a human glance.
        if sizing.reduction_blocked:
            return Confidence.MEDIUM
        if stats.samples >= MIN_DATA_POINTS * 2:
            return Confidence.HIGH
        return Confidence.MEDIUM

    # -- main entry point ------------------------------------------------------------------------
    def recommend(
        self,
        ref: WorkloadRef,
        resource: str,
        history: pd.Series | None = None,
        runtime_hint: str = "",
    ) -> ResourceRecommendation | None:
        collected = self.collector.collect(ref, resource, runtime_hint=runtime_hint)
        if collected is None:
            return None
        stats, sizing_basis = collected

        current_request = self.collector.current_request(ref, resource)
        current_limit = self.collector.current_limit(ref, resource)
        if current_request is None:
            # No declared request means no denominator and nothing to compare against.
            log.info("%s/%s: no declared request; skipping", ref.key, resource)
            return None

        floor, model_id = self._forecast_floor(history)

        rehearsal_key = f"{ref.key}/{resource}"
        rehearsed = self.rehearsed_floors.get(rehearsal_key)
        rehearsal_id = f"rehearsal:{rehearsal_key}" if rehearsed is not None else None
        if rehearsed is not None:
            stats = UsageStats(**{**asdict(stats), "rehearsed_floor": rehearsed})

        sizer = size_cpu if resource == "cpu" else size_memory
        sizing = sizer(stats, current_request, current_limit or current_request, floor)

        tier = self._evidence_tier(stats, sizing_basis, rehearsal_id)
        confidence = self._confidence(stats, sizing, tier)
        action = Action(sizing.action)

        # Hysteresis on the OUTPUT, not inside the sizer: the sizer's job is to say what the right
        # number is; deciding whether the delta is worth a PR is a product decision.
        if action is Action.REDUCE and current_request > 0:
            change = (current_request - sizing.request) / current_request
            if change < MIN_SAVINGS_THRESHOLD:
                action = Action.KEEP

        return ResourceRecommendation(
            namespace=ref.namespace,
            workload=ref.workload,
            container=ref.container,
            resource=resource,
            current_request=current_request,
            current_limit=current_limit,
            recommended_request=sizing.request,
            recommended_limit=sizing.limit,
            action=action,
            confidence=confidence,
            evidence_tier=tier,
            binding_constraint=sizing.binding_constraint,
            # For memory, prefer the concrete provenance from the collector over the sizer's
            # generic "peak": an operator needs to know whether it was memory.peak or a gauge.
            sizing_basis=sizing_basis if resource == "memory" else sizing.sizing_basis,
            reduction_blocked=sizing.reduction_blocked,
            rationale=sizing.rationale,
            samples=stats.samples,
            forecast_model=model_id,
            rehearsal_id=rehearsal_id,
            observed={
                "p50": stats.p50,
                "p95": stats.p95,
                "p99": stats.p99,
                "peak": stats.peak,
                # None here renders as "not observed" in the UI -- never as 0, never as green.
                "psi_stalled_ratio": stats.psi_stalled_ratio,
                "throttle_ratio": stats.throttle_ratio,
                "oom_events": stats.oom_events,
            },
        )

    def recommend_all(
        self,
        refs: list[WorkloadRef],
        histories: dict[str, pd.Series] | None = None,
    ) -> list[ResourceRecommendation]:
        histories = histories or {}
        out: list[ResourceRecommendation] = []
        for ref in refs:
            for resource in ("cpu", "memory"):
                try:
                    rec = self.recommend(ref, resource, history=histories.get(f"{ref.key}/{resource}"))
                except Exception as e:                            # noqa: BLE001
                    # One bad workload must not abort the run. A degraded run produces no
                    # recommendation for that workload -- never a wrong one.
                    log.exception("recommendation failed for %s/%s: %s", ref.key, resource, e)
                    continue
                if rec is not None:
                    out.append(rec)
        return out
