#!/usr/bin/env python3
"""
recommendation.py -- the pure, deterministic right-sizing engine.

This mirrors analyser/src/engine/ in the real project. It is intentionally free of any I/O,
Prometheus, GitHub, or framework code so it can be unit-tested and eval-graded in isolation:
given a CPU history (cores) + the currently-requested cores + a Forecaster, it returns a fully
explained Recommendation.

Design rules (every one is interview-defensible):
  * Percentile-based, never mean-based. Right-sizing to the mean guarantees throttling/OOM at peak.
  * request  = max(P95 * REQUEST_MARGIN, forecast_upper_peak, ABS_MIN)   -- the forecast is a FLOOR.
  * limit    = max(P99 * LIMIT_MARGIN, request)                          -- limit never below request.
  * The forecast can only ever RAISE the recommendation, never lower it. A growing service is caught
    by forecast_upper_peak even when its trailing P95 is still low; a flat/idle service is unaffected.
  * Confidence and the needs_review gate come from DATA TRUSTWORTHINESS, not from how big the saving is.
  * Absolute floors (ABS_MIN_CPU = 50m) stop us recommending un-runnable requests for idle pods.

The engine is deterministic: same history in => same recommendation out. All randomness lives in the
forecaster's model, which is itself seeded/closed-form in the dependency-free path.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

from src.forecasting import Forecaster, default_forecaster

# --- engine constants (kept here so tests and the analyser import one source of truth) ----------
MIN_DATA_POINTS = 24 * 7          # 168h: below this we will not trust an automated recommendation
ABS_MIN_CPU = 0.05                # 50m -- never recommend a request below this
REQUEST_MARGIN = 1.20             # headroom above observed P95
LIMIT_MARGIN = 1.50               # headroom above observed P99 for the limit
FORECAST_HORIZON_HOURS = 24 * 14  # 336h: provision for the peak across the next ~2 weeks
KEEP_HYSTERESIS = 0.10            # within +/-10% of current -> leave it alone (avoid churn/flapping)
TAIL_HIGH = 2.0                   # P95/P50 below this == predictable workload -> high confidence

Action = str        # "reduce" | "increase" | "keep" | "needs_review"
Confidence = str    # "high" | "medium" | "low"


@dataclass
class Recommendation:
    pod: str
    action: Action
    confidence: Confidence
    requested_cores: float
    recommended_request_cores: float
    recommended_limit_cores: float
    p50: float
    p95: float
    p99: float
    tail_ratio: float                 # P95/P50 -- the spikiness signal behind confidence
    n_points: int
    forecast_floor_applied: bool      # True iff the forecast_upper term is what set the request
    forecast_upper_peak: Optional[float]
    forecast_model_id: str
    forecast_confident: bool
    reason: str                       # short human-readable explanation (shown in the PR body)

    def to_dict(self) -> dict:
        return asdict(self)


def _percentiles(y: np.ndarray) -> tuple[float, float, float]:
    p50, p95, p99 = np.percentile(y, [50, 95, 99])
    return float(p50), float(p95), float(p99)


def recommend(
    pod: str,
    history: pd.Series,
    requested_cores: float,
    forecaster: Optional[Forecaster] = None,
    horizon_hours: int = FORECAST_HORIZON_HOURS,
) -> Recommendation:
    """Compute a right-sizing recommendation for one pod's CPU history (cores, hourly)."""
    forecaster = forecaster or default_forecaster()
    y = history.dropna().to_numpy(dtype=float)
    n = len(y)
    p50, p95, p99 = _percentiles(y)
    tail_ratio = (p95 / p50) if p50 > 0 else float("inf")

    # --- forecast (the predictive floor). Only a CONFIDENT forecast contributes. ---------------
    fc = forecaster.forecast(history, horizon_hours)
    forecast_upper_peak = float(fc.upper.max()) if fc.confident else None

    # --- assemble the request from the three candidate floors --------------------------------
    request_floor_p95 = p95 * REQUEST_MARGIN
    candidates = [request_floor_p95, ABS_MIN_CPU]
    if forecast_upper_peak is not None:
        candidates.append(forecast_upper_peak)
    recommended_request = max(candidates)

    forecast_floor_applied = (
        forecast_upper_peak is not None
        and forecast_upper_peak > request_floor_p95
        and forecast_upper_peak > ABS_MIN_CPU
        and abs(recommended_request - forecast_upper_peak) < 1e-9
    )

    recommended_limit = max(p99 * LIMIT_MARGIN, recommended_request)

    # --- data-trustworthiness gate decides confidence and whether we act at all --------------
    if n < MIN_DATA_POINTS:
        action: Action = "needs_review"
        confidence: Confidence = "low"
        reason = (
            f"only {n}h of data (< {MIN_DATA_POINTS}h required); too little history to trust an "
            f"automated change -- flagged for human review, deterministic trailing-P95 sizing shown."
        )
    else:
        confidence = "high" if tail_ratio < TAIL_HIGH else "medium"
        lo = requested_cores * (1.0 - KEEP_HYSTERESIS)
        hi = requested_cores * (1.0 + KEEP_HYSTERESIS)
        if recommended_request < lo:
            action = "reduce"
        elif recommended_request > hi:
            action = "increase"
        else:
            action = "keep"
        floor_note = (
            f" forecast floor binds (upper peak {forecast_upper_peak:.3f} > P95*{REQUEST_MARGIN})"
            if forecast_floor_applied else ""
        )
        reason = (
            f"P95={p95:.3f} P99={p99:.3f} cores; request=max(P95*{REQUEST_MARGIN}, forecast, "
            f"{ABS_MIN_CPU})={recommended_request:.3f}; {action} vs current {requested_cores:.3f}."
            + floor_note
        )

    return Recommendation(
        pod=pod,
        action=action,
        confidence=confidence,
        requested_cores=round(requested_cores, 6),
        recommended_request_cores=round(recommended_request, 6),
        recommended_limit_cores=round(recommended_limit, 6),
        p50=round(p50, 6),
        p95=round(p95, 6),
        p99=round(p99, 6),
        tail_ratio=round(tail_ratio, 6),
        n_points=n,
        forecast_floor_applied=forecast_floor_applied,
        forecast_upper_peak=(round(forecast_upper_peak, 6) if forecast_upper_peak is not None else None),
        forecast_model_id=fc.model_id,
        forecast_confident=fc.confident,
        reason=reason,
    )
