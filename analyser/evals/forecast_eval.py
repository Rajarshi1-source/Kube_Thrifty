#!/usr/bin/env python3
"""
forecast_eval.py -- the ACCURACY gate for the forecaster (the "eval connector" that wires
fixtures -> grader -> CI exit code).

What it does, per *predictable* fixture (the ones labeled expect_confident_forecast and not
deliberately spiky/short):
  1. Hold out the last HOLDOUT_HOURS of the series.
  2. Fit/forecast from the remaining history over that horizon.
  3. Score the point forecast with sMAPE and score the 90% interval with empirical coverage.
Then it means those across fixtures and gates:
  * mean sMAPE     <= baseline.target_smape   (point accuracy must not regress)
  * mean coverage  >= baseline.min_coverage   (intervals must stay calibrated; the upper bound
                                               is the predictive FLOOR, so under-coverage is unsafe)
It also asserts the FALLBACK CONTRACT: the deliberately too-short/noisy fixture must come back
confident=False so the engine drops to deterministic trailing-P95.

bursty and noisy_short are excluded from *accuracy* scoring on purpose: a point forecast of a random
spike train is not meaningful, and noisy_short is there to exercise the fallback, not the model. They
are still covered by recommendation_eval.py (categorical correctness).

Run modes:
  python forecast_eval.py             -> load baseline.json and gate (CI mode); exit 1 on regression.
  python forecast_eval.py --bootstrap -> compute metrics with the CURRENTLY INSTALLED forecaster and
                                         (re)write baseline.json with sensible tolerance margins.
                                         Use after an intentional fixture/model change, then commit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.forecasting import default_forecaster

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
BASELINE = HERE / "baseline.json"

# fixtures whose POINT forecast we score (predictable shapes). bursty/noisy_short excluded by design.
SCORED = ["flat_steady", "rising_trend", "weekly_seasonal", "nearly_idle"]
FALLBACK_EXPECTED = ["noisy_short"]   # must return confident=False


def _load(name: str) -> pd.Series:
    return pd.read_csv(FIX / f"{name}.csv")["y"].reset_index(drop=True)


def _smape(actual: np.ndarray, forecast: np.ndarray) -> float:
    denom = np.abs(actual) + np.abs(forecast)
    denom = np.where(denom == 0, 1e-9, denom)
    return float(np.mean(2.0 * np.abs(forecast - actual) / denom) * 100.0)   # percentage


def _coverage(actual: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> float:
    return float(np.mean((actual >= lower) & (actual <= upper)))


def evaluate() -> dict:
    meta = json.loads((FIX / "labels.json").read_text())["_meta"]
    holdout = int(meta["holdout_hours"])
    fc = default_forecaster()

    per_fixture: dict[str, dict] = {}
    smapes, coverages = [], []
    for name in SCORED:
        y = _load(name)
        train, test = y.iloc[:-holdout], y.iloc[-holdout:].to_numpy()
        f = fc.forecast(train, holdout)
        sm = _smape(test, f.point.to_numpy())
        cov = _coverage(test, f.lower.to_numpy(), f.upper.to_numpy())
        per_fixture[name] = {"smape": round(sm, 3), "coverage": round(cov, 3),
                             "confident": f.confident, "model": f.model_id}
        smapes.append(sm)
        coverages.append(cov)

    fallback_ok = {}
    for name in FALLBACK_EXPECTED:
        f = fc.forecast(_load(name), holdout)
        fallback_ok[name] = (f.confident is False)

    return {
        "model": fc.forecast(_load(SCORED[0]).iloc[:-holdout], holdout).model_id,
        "mean_smape": round(float(np.mean(smapes)), 3),
        "mean_coverage": round(float(np.mean(coverages)), 3),
        "per_fixture": per_fixture,
        "fallback_contract": fallback_ok,
    }


def bootstrap() -> int:
    r = evaluate()
    # honest tolerance: allow sMAPE to rise 25% (relative) over observed, require coverage within 7pts.
    baseline = {
        "_note": "Bootstrapped from the dependency-free SeasonalNaive forecaster. Re-run "
                 "`python forecast_eval.py --bootstrap` with statsforecast installed to record the "
                 "production AutoETS baseline, then commit. Thresholds gate CI in forecast_eval.py.",
        "model": r["model"],
        "observed_mean_smape": r["mean_smape"],
        "observed_mean_coverage": r["mean_coverage"],
        "target_smape": round(r["mean_smape"] * 1.25 + 0.5, 3),
        "min_coverage": round(max(0.0, r["mean_coverage"] - 0.07), 3),
        "per_fixture": r["per_fixture"],
    }
    BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
    print("Wrote baseline.json:")
    print(json.dumps(baseline, indent=2))
    return 0


def gate() -> int:
    if not BASELINE.exists():
        print("baseline.json missing -- run `python forecast_eval.py --bootstrap` first.", file=sys.stderr)
        return 1
    base = json.loads(BASELINE.read_text())
    r = evaluate()

    print(f"forecaster: {r['model']}")
    print(f"{'fixture':16s} {'sMAPE%':>8s} {'cover':>7s}")
    for name, m in r["per_fixture"].items():
        print(f"{name:16s} {m['smape']:8.2f} {m['coverage']:7.2f}")
    print("-" * 34)
    print(f"{'MEAN':16s} {r['mean_smape']:8.2f} {r['mean_coverage']:7.2f}")
    print(f"gate: sMAPE <= {base['target_smape']}  coverage >= {base['min_coverage']}")

    failures: list[str] = []
    if r["mean_smape"] > base["target_smape"]:
        failures.append(f"mean sMAPE {r['mean_smape']} > target {base['target_smape']}")
    if r["mean_coverage"] < base["min_coverage"]:
        failures.append(f"mean coverage {r['mean_coverage']} < min {base['min_coverage']}")
    for name, ok in r["fallback_contract"].items():
        if not ok:
            failures.append(f"fallback contract broken: {name} returned confident=True")

    if failures:
        print("\nFORECAST EVAL FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nFORECAST EVAL PASSED.")
    return 0


def main(argv: list[str]) -> int:
    if "--bootstrap" in argv:
        return bootstrap()
    return gate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
