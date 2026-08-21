#!/usr/bin/env python3
"""
generate_fixtures.py — Reproducibly generate the KubeThrifty labeled eval fixture set.

Each fixture is one pod's univariate HOURLY CPU series (cores), 21 days = 504 points,
written as `ds,y` CSV. The shapes are chosen to exercise every branch of the
recommendation engine AND the forecaster (trend, weekly seasonality, idle, bursty,
and a deliberately too-noisy/short series that must trigger the deterministic fallback).

Deterministic: a fixed RNG seed means the fixtures (and therefore the eval baseline)
are stable across machines and CI runs. Re-run only when you intentionally change the
fixture design, then re-baseline (see evals/README in the starter README).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HOURS = 24 * 21                      # 21 days of hourly data
HOLDOUT_HOURS = 24 * 3               # last 3 days are the backtest holdout
SEED = 20260622
HERE = Path(__file__).parent / "fixtures"

rng = np.random.default_rng(SEED)
idx = pd.date_range("2026-05-01", periods=HOURS, freq="h")


def _series(values: np.ndarray) -> pd.DataFrame:
    y = np.clip(values, 0.001, None)         # CPU cores never negative
    return pd.DataFrame({"ds": idx, "y": y})


def flat_steady() -> pd.DataFrame:
    # ~200m steady with light noise -> clearly over-provisioned, low variability, flat forecast
    base = 0.20 + rng.normal(0, 0.01, HOURS)
    return _series(base)


def bursty() -> pd.DataFrame:
    # baseline 80m with random spikes to ~700m -> P95 >> P50, moderate variability
    base = 0.08 + rng.normal(0, 0.02, HOURS)
    spikes = (rng.random(HOURS) < 0.06) * rng.uniform(0.4, 0.62, HOURS)
    return _series(base + spikes)


def rising_trend() -> pd.DataFrame:
    # 150m rising ~18%/week with mild daily seasonality -> forecast floor MUST exceed trailing P95*margin.
    # The growth rate is deliberately strong enough that a forward forecast over the lookahead window
    # clears trailing-P95*margin with comfortable headroom -- this fixture is the proof that the
    # predictive floor catches a growing service a naive P95-of-history right-sizer would under-provision.
    t = np.arange(HOURS)
    trend = 0.15 * (1.0 + 0.18 * (t / (24 * 7)))     # +18% per week
    daily = 0.02 * np.sin(2 * np.pi * t / 24)
    return _series(trend + daily + rng.normal(0, 0.008, HOURS))


def weekly_seasonal() -> pd.DataFrame:
    # strong daily + weekly seasonality around 300m -> forecaster must capture season
    t = np.arange(HOURS)
    daily = 0.10 * np.sin(2 * np.pi * t / 24)
    weekly = 0.06 * np.sin(2 * np.pi * t / (24 * 7))
    return _series(0.30 + daily + weekly + rng.normal(0, 0.012, HOURS))


def nearly_idle() -> pd.DataFrame:
    # ~10m almost-flat -> huge over-provision, high confidence
    return _series(0.01 + rng.normal(0, 0.002, HOURS))


def noisy_short() -> pd.DataFrame:
    # Only ~5 days of EXTREMELY noisy data: too little/too erratic to forecast confidently.
    # The forecaster must return confident=False and the engine must fall back to trailing P95.
    n = 24 * 5
    short_idx = idx[:n]
    y = np.abs(rng.normal(0.25, 0.25, n))            # cv ~100%
    return pd.DataFrame({"ds": short_idx, "y": np.clip(y, 0.001, None)})


FIXTURES = {
    "flat_steady": flat_steady,
    "bursty": bursty,
    "rising_trend": rising_trend,
    "weekly_seasonal": weekly_seasonal,
    "nearly_idle": nearly_idle,
    "noisy_short": noisy_short,
}

# The LABELS — ground truth the evals assert against. `requested` is the (over-provisioned)
# value the sample app declared, in cores, so the engine can compute an action/savings.
LABELS = {
    "flat_steady":     {"requested_cores": 2.0, "expect_action": "reduce", "expect_confidence": "high",
                        "expect_forecast_floor": False, "expect_confident_forecast": True},
    "bursty":          {"requested_cores": 1.0, "expect_action": "reduce", "expect_confidence": "medium",
                        "expect_forecast_floor": False, "expect_confident_forecast": True},
    "rising_trend":    {"requested_cores": 1.0, "expect_action": "reduce", "expect_confidence": "high",
                        "expect_forecast_floor": True,  "expect_confident_forecast": True},
    "weekly_seasonal": {"requested_cores": 1.0, "expect_action": "reduce", "expect_confidence": "high",
                        "expect_forecast_floor": False, "expect_confident_forecast": True},
    "nearly_idle":     {"requested_cores": 1.0, "expect_action": "reduce", "expect_confidence": "high",
                        "expect_forecast_floor": False, "expect_confident_forecast": True},
    "noisy_short":     {"requested_cores": 1.0, "expect_action": "needs_review", "expect_confidence": "low",
                        "expect_forecast_floor": False, "expect_confident_forecast": False},
}

META = {"hours": HOURS, "holdout_hours": HOLDOUT_HOURS, "seed": SEED, "freq": "h", "unit": "cpu_cores"}


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    for name, fn in FIXTURES.items():
        df = fn()
        df.to_csv(HERE / f"{name}.csv", index=False)
        print(f"  wrote {name}.csv  ({len(df)} rows)")
    (HERE / "labels.json").write_text(json.dumps({"_meta": META, "fixtures": LABELS}, indent=2))
    print(f"  wrote labels.json  ({len(LABELS)} labeled fixtures)")


if __name__ == "__main__":
    main()
