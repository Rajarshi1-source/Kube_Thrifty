#!/usr/bin/env python3
"""
sizing_eval.py -- the Rev 3 DECISION gate for the sizing rules.

This is the gate freshers never have. It does not check floats against a tolerance; it asserts the
CATEGORICAL decisions the sizer promises, for every labeled case in fixtures/sizing_cases.json:

  * action (reduce / increase / keep / needs_review)
  * which floor was binding (p95_margin | peak_margin | forecast | rehearsed | absolute_min)
  * reduction_blocked, when observed suffering (OOM or PSI stall) forbids a cut
  * request_at_least / no_reduction_below / limit_not_below / limit_equals_request invariants

Two cases carry the message and must never regress:

  spiky_peak_gt_p95      memory whose peak is far above its P95 must be sized off the PEAK. A
                         percentile-based sizer passes every float test and still ships an OOMKill.
  low_util_high_pressure a workload with low average usage and high memory PSI must NOT be cut. This
                         is the case every percentile tool shrinks into an outage.

Exit code 1 on any mismatch, so CI can gate the merge. Deterministic: no thresholds to drift, no
baseline file, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.sizing import UsageStats, size_cpu, size_memory

HERE = Path(__file__).parent
CASES = HERE / "fixtures" / "sizing_cases.json"
EPS = 1e-6


def _stats(raw: dict) -> UsageStats:
    return UsageStats(
        p50=raw["p50"], p95=raw["p95"], p99=raw["p99"], peak=raw["peak"],
        samples=raw["samples"],
        psi_stalled_ratio=raw.get("psi_stalled_ratio"),
        throttle_ratio=raw.get("throttle_ratio"),
        oom_events=raw.get("oom_events", 0),
        rehearsed_floor=raw.get("rehearsed_floor"),
        runtime_hint=raw.get("runtime_hint", ""),
    )


def _check(case: dict, sizing) -> list[str]:
    exp, fails = case["expect"], []

    def bad(what, got, want):
        fails.append(f"{case['name']}: {what} got {got!r}, expected {want!r}")

    if "action" in exp and sizing.action != exp["action"]:
        bad("action", sizing.action, exp["action"])
    if "binding_constraint" in exp and sizing.binding_constraint != exp["binding_constraint"]:
        bad("binding_constraint", sizing.binding_constraint, exp["binding_constraint"])
    if "reduction_blocked" in exp and sizing.reduction_blocked != exp["reduction_blocked"]:
        bad("reduction_blocked", sizing.reduction_blocked, exp["reduction_blocked"])
    if "request_at_least" in exp and sizing.request < exp["request_at_least"] - EPS:
        bad("request", sizing.request, f">= {exp['request_at_least']}")
    if "no_reduction_below" in exp and sizing.request < exp["no_reduction_below"] - EPS:
        bad("request", sizing.request, f">= {exp['no_reduction_below']} (no cut allowed)")
    if "limit_not_below" in exp and sizing.limit < exp["limit_not_below"] - EPS:
        bad("limit", sizing.limit, f">= {exp['limit_not_below']}")
    if exp.get("limit_equals_request") and abs(sizing.limit - sizing.request) > EPS:
        bad("limit==request", (sizing.limit, sizing.request), "equal")
    return fails


def main() -> int:
    spec = json.loads(CASES.read_text())
    failures: list[str] = []

    print(f"{'case':30s} {'res':7s} {'action':13s} {'binding':13s} {'req':>10s} {'limit':>10s}  blocked")
    print("-" * 96)
    for case in spec["cases"]:
        stats = _stats(case["stats"])
        args = (stats, case["current_request"], case["current_limit"], case["predictive_floor"])
        sizing = size_cpu(*args) if case["resource"] == "cpu" else size_memory(*args)
        failures += _check(case, sizing)
        print(f"{case['name']:30s} {sizing.resource:7s} {sizing.action:13s} "
              f"{sizing.binding_constraint:13s} {sizing.request:10.3f} {sizing.limit:10.3f}  "
              f"{sizing.reduction_blocked}")

    print("-" * 96)
    if failures:
        print(f"\nFAIL: {len(failures)} sizing decision mismatch(es):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"PASS: {len(spec['cases'])} sizing decisions match their labels "
          f"(memory sized off peak, pressure blocks cuts, floors compose with max()).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
