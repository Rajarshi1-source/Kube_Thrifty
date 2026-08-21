#!/usr/bin/env python3
"""
thriftctl -- offline replay for ThriftDetective verdicts.

    python -m src.detective.thriftctl replay <bundle.json>
    python -m src.detective.thriftctl replay --sha <bundle_sha>      # from the database
    python -m src.detective.thriftctl explain <bundle.json>
    python -m src.detective.thriftctl digest <bundle.json>

WHY THIS EXISTS

A verdict you cannot reproduce is an opinion. `investigate(bundle)` is pure -- no network, no clock,
no randomness, no LLM in the decision path -- which means anyone holding the evidence bundle can
re-derive the exact same verdict, on a laptop, offline, months later. This command is that promise
made usable.

It is also the debugging tool that makes the rules maintainable. When a verdict looks wrong, you
replay the bundle rather than trying to reconstruct a cluster state that no longer exists.

The bundle is content-addressed by sha256 of its CANONICAL JSON (sort_keys, no whitespace). Non-
canonical JSON would hash differently per machine and per Python version, which would make the
address useless as an identity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import (
    RULESET_VERSION,
    bundle_sha256,
    canonical,
    explain,
    investigate,
    verdicts_digest,
)


def _load_bundle(source: str | None, sha: str | None) -> tuple[dict, str]:
    """Load from a file, from stdin, or from the verdicts table by sha."""
    if sha:
        return _load_from_db(sha), f"database:{sha[:12]}"

    if source in (None, "-"):
        return json.load(sys.stdin), "stdin"

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"no such bundle: {path}")
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def _load_from_db(sha: str) -> dict:
    """
    Reconstruct a bundle from the stored evidence.

    Only reachable when a database is configured. The point of replay is that it does NOT need one --
    a bundle on disk is sufficient -- so this import is local and its failure is explained rather
    than propagated as an ImportError.
    """
    try:
        from sqlalchemy import text

        from ..config import get_settings
        from ..db import get_engine
    except ImportError as e:  # pragma: no cover - depends on optional extras
        raise SystemExit(
            f"replaying by sha needs the database extras ({e}). Pass a bundle file instead: "
            f"replay is designed to work entirely offline."
        ) from e

    settings = get_settings()
    engine = get_engine(settings.database_url.get_secret_value())
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT evidence FROM verdicts WHERE bundle_sha = :sha LIMIT 1"),
            {"sha": sha},
        ).first()
    if row is None:
        raise SystemExit(f"no stored verdict for bundle {sha}")
    return row[0]


def cmd_replay(args: argparse.Namespace) -> int:
    bundle, origin = _load_bundle(args.bundle, args.sha)

    actual_sha = bundle_sha256(bundle)
    verdicts = investigate(bundle)
    digest = verdicts_digest(verdicts)

    print(f"source        {origin}")
    print(f"bundle sha256 {actual_sha}")
    print(f"ruleset       {RULESET_VERSION}")
    print(f"verdicts sha  {digest}")

    # If the caller named a sha, confirm the bundle actually hashes to it. A mismatch means the
    # evidence has been altered since the verdict was recorded, which invalidates the replay
    # entirely -- and is exactly what content addressing exists to detect.
    if args.sha and args.sha != actual_sha:
        print()
        print(f"MISMATCH: this bundle hashes to {actual_sha}, not {args.sha}.")
        print("The evidence has been modified since the verdict was recorded, so this replay does")
        print("not reproduce that verdict.")
        return 1

    print()
    if not verdicts:
        print("NO_VERDICT")
        print("  Not a resource-shaped incident. Resource signals look healthy; look at image pull,")
        print("  DNS, RBAC, storage, or the application itself.")
        return 0

    for i, v in enumerate(verdicts, 1):
        print(f"{i}. {v.rule_id}  confidence={v.confidence:.3f}")
        print(f"   {v.summary}")
        if v.attributed_change:
            a = v.attributed_change
            print(f"   ATTRIBUTED to {a.get('kind')} "
                  f"{a.get('pr') or a.get('recommendation_id')} "
                  f"{a.get('hours_before')}h before: {a.get('delta')}")
        print(f"   remediation: {v.remediation}")
        if args.evidence:
            for key, value in sorted(v.evidence.items()):
                # None renders as "not observed" here too. The CLI is held to the same rule as the
                # UI: a missing signal must never print as 0.
                shown = "not observed" if value is None else value
                print(f"     {key}: {shown}")
        print()

    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    bundle, _ = _load_bundle(args.bundle, args.sha)
    # Deterministic narration from templates. The optional LLM narrator in the full product PHRASES
    # this; it never decides it. The verdict is computed first and the prose is validated against it,
    # so a hallucinating model can produce bad writing but not a bad verdict.
    print(explain(bundle))
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    bundle, _ = _load_bundle(args.bundle, args.sha)
    print(f"bundle_sha256   {bundle_sha256(bundle)}")
    print(f"verdicts_digest {verdicts_digest(investigate(bundle))}")
    if args.canonical:
        print()
        print(canonical(bundle))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """
    Prove determinism by re-investigating the same bundle N times.

    Cheap, and it catches the specific class of bug that would silently destroy replayability: a rule
    that reads a clock, iterates a set, or consults a random number. Those produce verdicts that
    differ between runs on the SAME input, which no accuracy metric would ever notice.
    """
    bundle, origin = _load_bundle(args.bundle, args.sha)

    digests = {verdicts_digest(investigate(bundle)) for _ in range(args.runs)}

    print(f"source {origin}")
    print(f"runs   {args.runs}")
    if len(digests) == 1:
        print(f"digest {digests.pop()}")
        print("\nDETERMINISTIC: every run produced byte-identical verdicts.")
        return 0

    print(f"\nNON-DETERMINISTIC: {len(digests)} distinct digests across {args.runs} runs.")
    for d in sorted(digests):
        print(f"  {d}")
    print("\nA rule is reading a clock, iterating an unordered collection, or using randomness.")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="thriftctl",
        description="Offline replay for ThriftDetective. A verdict you cannot reproduce is an opinion.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("bundle", nargs="?", default="-",
                       help="path to an evidence bundle JSON file, or '-' for stdin")
        p.add_argument("--sha", default=None,
                       help="load the bundle from the verdicts table by content address")

    p_replay = sub.add_parser("replay", help="re-derive the verdicts for a bundle")
    add_common(p_replay)
    p_replay.add_argument("--evidence", action="store_true",
                          help="print the full evidence dict for each verdict")
    p_replay.set_defaults(fn=cmd_replay)

    p_explain = sub.add_parser("explain", help="deterministic narration of the verdicts")
    add_common(p_explain)
    p_explain.set_defaults(fn=cmd_explain)

    p_digest = sub.add_parser("digest", help="print the content address and verdict digest")
    add_common(p_digest)
    p_digest.add_argument("--canonical", action="store_true",
                          help="also print the canonical JSON that is hashed")
    p_digest.set_defaults(fn=cmd_digest)

    p_verify = sub.add_parser("verify", help="assert the engine is deterministic for this bundle")
    add_common(p_verify)
    p_verify.add_argument("--runs", type=int, default=20)
    p_verify.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"error: the bundle is not valid JSON ({e})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
