#!/usr/bin/env python3
"""
recommendation_eval.py -- the CORRECTNESS gate for the right-sizing engine.

Runs the pure engine on every labeled fixture and asserts the *categorical* outputs the engine
promises -- action, confidence tier, the forecast-floor flag, and the confident/fallback flag --
against the ground-truth labels in fixtures/labels.json.

This is deterministic: no thresholds to drift, no baseline file. Either the engine reproduces the
hand-labeled behaviour for all fixtures or CI fails. Run together with forecast_eval.py (which gates
on forecast *accuracy* numbers). Exit code 1 on any mismatch so a CI step can gate the merge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from evals.legacy.recommendation import recommend
from src.forecasting import default_forecaster

HERE = Path(__file__).parent
FIX = HERE / "fixtures"


def _load(name: str) -> pd.Series:
    return pd.read_csv(FIX / f"{name}.csv")["y"].reset_index(drop=True)


def main() -> int:
    labels = json.loads((FIX / "labels.json").read_text())["fixtures"]
    forecaster = default_forecaster()
    failures: list[str] = []

    print(f"{'fixture':16s} {'action':12s} {'conf':7s} {'floor':6s} {'cfFC':5s}  result")
    print("-" * 64)
    for name, lab in labels.items():
        rec = recommend(name, _load(name), lab["requested_cores"], forecaster)
        checks = {
            "action": (rec.action, lab["expect_action"]),
            "confidence": (rec.confidence, lab["expect_confidence"]),
            "forecast_floor": (rec.forecast_floor_applied, lab["expect_forecast_floor"]),
            "confident_forecast": (rec.forecast_confident, lab["expect_confident_forecast"]),
        }
        bad = [f"{k}: got {got!r} want {want!r}" for k, (got, want) in checks.items() if got != want]
        status = "OK" if not bad else "FAIL -> " + "; ".join(bad)
        if bad:
            failures.append(f"{name}: {'; '.join(bad)}")
        print(f"{name:16s} {rec.action:12s} {rec.confidence:7s} "
              f"{str(rec.forecast_floor_applied):6s} {str(rec.forecast_confident):5s}  {status}")

    print("-" * 64)
    if failures:
        print(f"RECOMMENDATION EVAL FAILED ({len(failures)} fixture(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"RECOMMENDATION EVAL PASSED: all {len(labels)} fixtures match labels.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
