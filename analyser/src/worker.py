#!/usr/bin/env python3
"""
worker.py -- the consumer. `python -m src.worker --once`

Reads `analysis-jobs`, runs the analysis, then ACKs. The ordering is the delivery contract:

    ACK AFTER the work, never before.

At-least-once. Re-running an analysis costs a few minutes of CPU and produces the same
recommendations; losing one means a cluster silently stops being right-sized, which nobody notices
until the bill arrives. When those are the two failure modes, you choose the cheap one.

`--once` is the KEDA shape: process the available work and exit, so the ScaledObject can take the
pod back to zero. `--loop` is for local development, where waiting for a scaler is tedious.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .cache import Job, JobQueue, make_client
from .cache.queue import MAX_DELIVERIES
from .config import get_settings
from .main import run as run_analysis

log = logging.getLogger("kubethrifty.worker")


def _handle(job: Job, queue: JobQueue, *, default_window: str) -> None:
    """
    Run one job.

    A job that has already been redelivered `MAX_DELIVERIES` times is poison, not unlucky: it goes
    to the DLQ. Without that cap one malformed job is redelivered forever and starves every good job
    queued behind it.
    """
    if job.delivery_count > MAX_DELIVERIES:
        queue.dead_letter(job, reason=f"exceeded {MAX_DELIVERIES} delivery attempts")
        return

    argv = ["--window", job.window or default_window, "--run-id", job.run_id]
    for ns in job.namespaces:
        argv += ["--namespace", ns]
    if job.auto_pr:
        argv.append("--auto-pr")

    log.info("running job %s (run_id=%s, delivery %d)", job.entry_id, job.run_id, job.delivery_count)
    try:
        code = run_analysis(argv)
    except Exception as e:                                            # noqa: BLE001
        # Do NOT ack. The entry stays in the PEL and is reclaimed by XAUTOCLAIM after the idle
        # window, which is what makes a mid-analysis OOMKill recoverable rather than silent.
        log.exception("job %s raised (%s); leaving it pending for redelivery", job.entry_id, e)
        return

    if code == 0:
        queue.ack(job)
    else:
        # A non-zero exit is usually a refusal to run (Prometheus unhealthy, half-configured write
        # path). Those are worth retrying -- the dependency may recover -- so it is left pending
        # rather than dead-lettered on the first failure.
        log.warning("job %s exited %d; leaving it pending for redelivery", job.entry_id, code)


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kubethrifty-worker")
    ap.add_argument("--once", action="store_true",
                    help="drain the currently available work and exit (the KEDA shape)")
    ap.add_argument("--loop", action="store_true", help="keep polling (local development)")
    ap.add_argument("--window", default="7d", help="fallback window if a job omits one")
    ap.add_argument("--block-ms", type=int, default=5000)
    args = ap.parse_args(argv)

    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO), stream=sys.stderr,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    try:
        queue = JobQueue(make_client(s.redis_url))
        queue.ensure_group()
    except Exception as e:                                            # noqa: BLE001
        log.error("cannot reach the job stream: %s", e)
        return 1

    idle_polls = 0
    while True:
        jobs = queue.read(count=1, block_ms=args.block_ms)
        if not jobs:
            if args.once:
                # Two empty polls before exiting. One is not enough: the first read after a cold
                # start can return nothing while the consumer group is still being established, and
                # exiting there would hand the job straight back to KEDA to wake another pod for.
                idle_polls += 1
                if idle_polls >= 2:
                    log.info("no work available; exiting for scale-to-zero")
                    return 0
                continue
            if not args.loop:
                return 0
            time.sleep(1)
            continue

        idle_polls = 0
        for job in jobs:
            _handle(job, queue, default_window=args.window)

        if args.once and not args.loop:
            # One job per invocation under KEDA. The scaler still sees lag and will start another
            # pod, which spreads a backlog across pods instead of serialising it in one.
            return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
