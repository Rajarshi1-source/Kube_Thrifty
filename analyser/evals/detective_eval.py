#!/usr/bin/env python3
"""
detective_eval.py -- the third CI gate. Three assertions, any of which fails the build.

  1. ACCURACY     top-1 verdict matches ground truth on >= 90% of the corpus, and a NO_VERDICT case
                  must produce NO verdict at all (refusing is graded, not optional).
  2. CALIBRATION  Brier score and Expected Calibration Error on the (confidence, correct) pairs.
                  This is the gate almost nobody builds: it asks "when the engine says 0.9, is it right
                  about 90% of the time?" A confidence number nobody scores is decoration.
  3. DETERMINISM  the sha256 of every case's verdict list equals the committed digest for this ruleset
                  version. Any accidental non-determinism (dict ordering, clock, randomness) fails here.

Run `python corpus/generate.py --freeze` to regenerate the corpus and re-anchor the digests -- but only
after looking at Brier/ECE, because re-freezing blindly defeats the whole gate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.detective.engine import RULESET_VERSION, investigate, verdicts_digest

HERE = Path(__file__).parent
CORPUS = HERE / "corpus" / "incidents.json"
DIGESTS = HERE / "corpus" / "digests.json"

TARGET_TOP1_ACCURACY = 0.90
MAX_BRIER = 0.08
MAX_ECE = 0.10


def brier(pairs: list[tuple[float, bool]]) -> float:
    """Mean squared error between stated confidence and outcome. Lower is better; 0.25 == coin flip."""
    return sum((c - int(ok)) ** 2 for c, ok in pairs) / len(pairs) if pairs else 0.0


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float:
    """Expected Calibration Error: weighted gap between mean confidence and mean accuracy per bucket."""
    if not pairs:
        return 0.0
    total, err = len(pairs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(c, ok) for c, ok in pairs if lo < c <= hi]
        if not bucket:
            continue
        conf = sum(c for c, _ in bucket) / len(bucket)
        acc = sum(int(ok) for _, ok in bucket) / len(bucket)
        err += (len(bucket) / total) * abs(conf - acc)
    return err


def main() -> int:
    corpus = json.loads(CORPUS.read_text())
    frozen = json.loads(DIGESTS.read_text()) if DIGESTS.exists() else {}

    pairs: list[tuple[float, bool]] = []
    correct = 0
    digests: list[str] = []
    rows = []

    for case in corpus:
        verdicts = investigate(case["bundle"])
        digests.append(verdicts_digest(verdicts))
        top = verdicts[0] if verdicts else None
        expected = case["ground_truth"]

        if expected == "NO_VERDICT":
            ok = top is None
            got = "NO_VERDICT" if ok else top.rule_id
            # A false positive on a red herring is scored as an overconfident wrong answer.
            if top is not None:
                pairs.append((top.confidence, False))
        else:
            ok = top is not None and top.rule_id == expected
            got = top.rule_id if top else "NO_VERDICT"
            if top is not None:
                pairs.append((top.confidence, ok))

        correct += int(ok)
        rows.append((case["name"], expected, got,
                     f"{top.confidence:.2f}" if top else "-",
                     "ok" if ok else "MISMATCH",
                     (top.attributed_change or {}).get("pr", "") if top else ""))

    accuracy = correct / len(corpus)
    b, e = brier(pairs), ece(pairs)
    deterministic = digests == frozen.get(RULESET_VERSION)

    print(f"{'case':38s} {'expected':24s} {'got':24s} {'conf':>5s}  result   attribution")
    print("-" * 118)
    for name, exp, got, conf, res, attrib in rows:
        print(f"{name:38s} {exp:24s} {got:24s} {conf:>5s}  {res:8s} {attrib}")
    print("-" * 118)
    print(f"cases={len(corpus)}  top1_accuracy={accuracy:.3f} (>= {TARGET_TOP1_ACCURACY})  "
          f"brier={b:.4f} (<= {MAX_BRIER})  ece={e:.4f} (<= {MAX_ECE})  "
          f"deterministic={deterministic}  ruleset={RULESET_VERSION}")

    failures = []
    if accuracy < TARGET_TOP1_ACCURACY:
        failures.append(f"accuracy {accuracy:.3f} < {TARGET_TOP1_ACCURACY}")
    if b > MAX_BRIER:
        failures.append(f"brier {b:.4f} > {MAX_BRIER}")
    if e > MAX_ECE:
        failures.append(f"ECE {e:.4f} > {MAX_ECE}")
    if not deterministic:
        failures.append("verdict digests differ from the committed anchor (non-deterministic engine "
                        "or an intentional ruleset change -- re-freeze deliberately)")
    if failures:
        print("\nFAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("PASS: accuracy, calibration and determinism all within gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
