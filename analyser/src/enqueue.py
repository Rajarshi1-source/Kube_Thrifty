#!/usr/bin/env python3
"""
enqueue.py -- the producer. `python -m src.enqueue --window 7d`

Run by the enqueuer CronJob every 6 hours. It does one thing: `XADD` a job and print the run id.

Why the schedule pushes a job rather than running the analysis itself: the analyser sits at
`minReplicaCount: 0` and is woken by KEDA watching consumer-group lag on `analysis-jobs`. A CronJob
that ran the analysis directly would need the analyser's full configuration, its database
credentials and its cluster RBAC -- and would bypass the concurrency guard entirely, since two
overlapping schedules would both just start working.

This process needs exactly one credential: the Valkey URL.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .cache import JobQueue, make_client
from .config import get_settings

log = logging.getLogger("kubethrifty.enqueue")


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kubethrifty-enqueue")
    ap.add_argument("--window", default="7d")
    ap.add_argument("--namespace", action="append", default=None,
                    help="restrict to a namespace; repeatable")
    ap.add_argument("--trigger", default="schedule", help="schedule | manual | api")
    ap.add_argument("--auto-pr", action="store_true")
    args = ap.parse_args(argv)

    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO), stream=sys.stderr,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    try:
        queue = JobQueue(make_client(s.redis_url))
        run_id = queue.enqueue(
            cluster=s.cluster_name,
            window=args.window,
            namespaces=args.namespace,
            trigger=args.trigger,
            auto_pr=args.auto_pr,
        )
    except Exception as e:                                            # noqa: BLE001
        # Non-zero so the CronJob is visibly failed rather than silently skipping a cycle.
        log.error("could not enqueue: %s", e)
        return 1

    print(run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
