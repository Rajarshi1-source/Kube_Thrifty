#!/usr/bin/env python3
"""
reliability.py -- the reliability diagram behind the ECE number.

    python -m evals.reliability

`detective_eval` reports ECE as a single figure, which tells you calibration is off but not in which
DIRECTION. That matters, because the two failure modes have opposite fixes:

  OVER-confident   claims 0.95, right 0.70 of the time. Dangerous: the confidence is a lie, and an
                   operator acting on it is being misled.
  UNDER-confident   claims 0.72, right 0.95 of the time. Wasteful but honest: real findings get
                   ignored because the tool undersells them.

This exists so re-freezing the determinism digest is an informed act rather than a ritual. The rule
is to read Brier and ECE first and say why -- and "why" needs the direction.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def main() -> int:
    from src.detective.engine import RULESET_VERSION, investigate

    corpus = json.loads((HERE / "corpus" / "incidents.json").read_text(encoding="utf-8"))

    buckets: dict[float, list[bool]] = defaultdict(list)
    per_rule: dict[str, list[tuple[float, bool]]] = defaultdict(list)

    for case in corpus:
        verdicts = investigate(case["bundle"])
        top = verdicts[0] if verdicts else None
        predicted = top.rule_id if top else "NO_VERDICT"
        # A refusal is a confident assertion that nothing resource-shaped is wrong, so NO_VERDICT is
        # scored at 1.0 rather than excluded.
        confidence = top.confidence if top else 1.0
        correct = predicted == case["ground_truth"]

        buckets[round(confidence * 10) / 10].append(correct)
        per_rule[predicted].append((confidence, correct))

    print(f"ruleset {RULESET_VERSION}  cases={len(corpus)}\n")
    print(f"{'bucket':>8} {'n':>4} {'accuracy':>9} {'gap':>8}  direction")
    print("-" * 52)

    weighted_gap = 0.0
    for bucket in sorted(buckets):
        hits = buckets[bucket]
        accuracy = sum(hits) / len(hits)
        gap = accuracy - bucket
        weighted_gap += abs(gap) * len(hits) / len(corpus)
        direction = "under-confident" if gap > 0.02 else "over-confident" if gap < -0.02 else "calibrated"
        print(f"{bucket:>8.1f} {len(hits):>4} {accuracy:>9.3f} {gap:>+8.3f}  {direction}")

    print("-" * 52)
    print(f"ECE (bucket-weighted mean |gap|): {weighted_gap:.4f}\n")

    print(f"{'rule':<26} {'n':>3} {'mean conf':>10} {'accuracy':>9}")
    print("-" * 52)
    for rule in sorted(per_rule):
        rows = per_rule[rule]
        mean_conf = sum(c for c, _ in rows) / len(rows)
        accuracy = sum(1 for _, ok in rows if ok) / len(rows)
        print(f"{rule:<26} {len(rows):>3} {mean_conf:>10.3f} {accuracy:>9.3f}")

    print(
        "\nA positive gap means the engine is right MORE often than it claims. That is the safe "
        "direction to be wrong in, but it still costs ECE headroom."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
