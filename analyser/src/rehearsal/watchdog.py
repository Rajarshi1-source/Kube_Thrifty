#!/usr/bin/env python3
"""
watchdog.py -- the dead-man's switch. `python -m src.rehearsal.watchdog`

Sweeps every rehearsal still marked `running` past its `revert_deadline` and puts the pod back.

WHY THIS IS A SEPARATE CRONJOB AND NOT A THREAD IN THE ANALYSER

Its entire purpose is to clean up after an analyser that died. A watchdog running inside the process
it guards dies with it, which makes it decorative. The failure it exists for is precisely:

    the analyser is OOMKilled between the resize PATCH and its `finally` revert

In that instant the pod is carrying experimental limits, the `finally` will never run, and the only
record is the compensation row written before the mutation. A separate CronJob on a separate schedule
in a separate pod is what turns that record into an actual recovery.

It is deliberately dumb. It does not evaluate signals, does not decide whether the candidate was
safe, and never produces a floor. It reads original_requests, patches them back, and marks the row
`reverted_by_watchdog` -- an outcome that is explicitly NOT `safe`, because nobody observed anything.
"""
from __future__ import annotations

import argparse
import logging
import sys

from ..config import get_settings
from ..db import Repository, get_engine

log = logging.getLogger("kubethrifty.watchdog")


def sweep(repo: Repository, client, *, dry_run: bool = False) -> tuple[int, int]:
    """
    Revert every overdue rehearsal. Returns (found, reverted).

    One failure does not stop the sweep: each row is independent, and a pod that cannot be reverted
    now must not prevent the next pod from being reverted at all.
    """
    overdue = repo.overdue_rehearsals()
    if not overdue:
        log.info("no overdue rehearsals")
        return 0, 0

    log.warning(
        "%d rehearsal(s) past their revert deadline. This means an analyser died mid-experiment, "
        "or a revert failed.", len(overdue),
    )

    reverted = 0
    for row in overdue:
        rid = row["rehearsal_id"]
        namespace, pod, container = row["namespace"], row["pod"], row["container"]

        log.warning(
            "sweeping %s: %s/%s container=%s (deadline was %s)",
            rid, namespace, pod, container, row["revert_deadline"],
        )

        if dry_run:
            continue

        try:
            # The pod may legitimately be gone -- it was rescheduled, or the Deployment was scaled
            # down. That is a SUCCESSFUL sweep: there is nothing left carrying experimental limits.
            status = client.get_pod_status(namespace, pod)
            if not status:
                log.info("%s: pod no longer exists; nothing to revert", rid)
                repo.close_rehearsal(
                    rid, outcome="inconclusive",
                    trip_reasons=["pod no longer existed when the watchdog swept it"],
                    reverted=False,
                )
                continue

            # If the pod was replaced, the new one was never modified. Reverting would patch
            # original values onto a container that never left them.
            if status.get("uid") and status["uid"] != row["pod_uid"]:
                log.info("%s: pod was replaced; the original container is gone", rid)
                repo.close_rehearsal(
                    rid, outcome="inconclusive",
                    trip_reasons=["pod was replaced; nothing to revert"],
                    reverted=False,
                )
                continue

            client.patch_resize(
                namespace, pod, container,
                row["original_requests"], row["original_limits"],
            )

            # `reverted_by_watchdog`, NEVER `safe`. Nobody observed this experiment: the owning
            # process died, so there is no evidence either way. Recording it as safe would let an
            # unobserved change become a sizing floor.
            repo.close_rehearsal(
                rid, outcome="reverted_by_watchdog",
                trip_reasons=["owning process did not revert before the deadline"],
                reverted=True,
            )
            reverted += 1
            log.info("%s: reverted", rid)

        except Exception as e:                                        # noqa: BLE001
            # Left `running` on purpose. The next sweep will try again -- far better than marking it
            # done while a pod is still carrying experimental limits.
            log.exception("%s: sweep failed, leaving the row open for the next pass: %s", rid, e)

    return len(overdue), reverted


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kubethrifty-watchdog")
    ap.add_argument("--dry-run", action="store_true", help="report what would be reverted, change nothing")
    args = ap.parse_args(argv)

    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level, logging.INFO), stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        repo = Repository(get_engine(s.database_url.get_secret_value()))
    except Exception as e:                                            # noqa: BLE001
        # Non-zero exit so the CronJob is visibly failed. A watchdog that cannot read its own
        # compensation log is not a watchdog, and silence here would be indistinguishable from
        # "nothing to do".
        log.error("cannot reach the compensation store: %s", e)
        return 1

    from .k8s_client import KubernetesResizeClient
    try:
        client = KubernetesResizeClient()
    except Exception as e:                                            # noqa: BLE001
        log.error("cannot build a Kubernetes client: %s", e)
        return 1

    found, reverted = sweep(repo, client, dry_run=args.dry_run)

    if found and reverted < found and not args.dry_run:
        # Loud failure: some pod is still carrying experimental limits.
        log.error("swept %d overdue rehearsal(s) but only reverted %d", found, reverted)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
