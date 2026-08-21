#!/usr/bin/env python3
"""
verify_valkey.py -- integration check for the Valkey layer against a REAL Valkey.

The unit tests in `analyser/tests/test_cache_lock.py` use a stub, which proves our protocol is
correct but cannot prove Valkey agrees with our reading of it. This script closes that gap: it
exercises `SET NX EX`, the compare-and-delete release, consumer groups, the pending-entries list and
`XAUTOCLAIM` against the pinned `valkey/valkey:9.1-alpine3.24` server.

    docker compose up -d valkey
    python scripts/verify_valkey.py

Exits non-zero on the first failed assertion, so it is usable as a CI step.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analyser"))

from src.cache import Cache, JobQueue, LockUnavailable, keys, make_client, run_lock  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_checks = 0
_failures: list[str] = []


def check(label: str, condition: bool) -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        _failures.append(label)


def main() -> int:
    client = make_client(REDIS_URL)
    client.ping()
    print(f"connected to {REDIS_URL}\n")

    # Namespace this run so a re-run never inherits a previous run's keys.
    cluster = f"verify-{uuid.uuid4().hex[:8]}"
    stream = f"analysis-jobs-verify-{uuid.uuid4().hex[:8]}"

    # --- the lock -------------------------------------------------------------------------------
    print("run lock")
    lock_key = keys.analysis_lock(cluster)
    with run_lock(client, cluster, ttl_seconds=60) as token:
        check("lock key exists while held", client.get(lock_key) == token)
        # A real server enforcing NX is the whole point of this file.
        check("SET NX refuses a second holder",
              client.set(lock_key, "intruder", nx=True, ex=60) is None)
        ttl = client.ttl(lock_key)
        check(f"lease carries a TTL ({ttl}s)", 0 < ttl <= 60)
    check("lock released on exit", client.get(lock_key) is None)

    print("\nlock does not delete somebody else's lease")
    # Simulate an overrun: our token is replaced while we hold the context. The compare-and-delete
    # must then leave the key alone -- a bare DEL would destroy the new holder's lock.
    with run_lock(client, cluster, ttl_seconds=60):
        client.set(lock_key, "somebody-elses-token", ex=60)
    check("foreign lock survives our release", client.get(lock_key) == "somebody-elses-token")
    client.delete(lock_key)

    print("\ncontention")
    with run_lock(client, cluster, ttl_seconds=60):
        try:
            with run_lock(client, cluster, ttl_seconds=60):
                check("second holder refused", False)
        except LockUnavailable:
            check("second holder refused", True)

    # --- cache-aside ----------------------------------------------------------------------------
    print("\ncache-aside")
    cache = Cache(client)
    key = keys.recommendations(cluster)
    computed: list[int] = []

    def compute() -> dict:
        computed.append(1)
        return {"recs": [{"workload": "api", "action": "reduce"}]}

    first = cache.get_or_compute(key, 60, compute)
    second = cache.get_or_compute(key, 60, compute)
    check("miss then hit computes exactly once", len(computed) == 1)
    check("cached value round-trips intact", first == second)
    check("cached entry has a TTL", 0 < client.ttl(key) <= 60)

    cache.invalidate_cluster(cluster)
    check("invalidation removes the entry", client.get(key) is None)

    # --- the stream -----------------------------------------------------------------------------
    print("\nstream + consumer group")
    queue = JobQueue(client, stream=stream)
    queue.ensure_group()
    queue.ensure_group()  # idempotent: BUSYGROUP must be swallowed
    check("ensure_group is idempotent", True)

    run_id = queue.enqueue(cluster=cluster, window="7d", namespaces=["shop"], trigger="verify")
    jobs = queue.read(count=10, block_ms=1000)
    check("job read back off the stream", len(jobs) == 1)
    job = jobs[0]
    check("run_id survives the round trip", job.run_id == run_id)
    check("namespaces decode from JSON", job.namespaces == ("shop",))
    check("auto_pr defaults to a real False", job.auto_pr is False)

    pending = client.xpending(stream, keys.CONSUMER_GROUP)
    check("unacked job sits in the PEL", int(pending["pending"]) == 1)

    queue.ack(job)
    pending = client.xpending(stream, keys.CONSUMER_GROUP)
    check("ACK clears the PEL", int(pending["pending"]) == 0)

    # --- reclaim: the dead-man's-switch for the queue --------------------------------------------
    print("\nreclaim of an abandoned job")
    queue.enqueue(cluster=cluster, window="7d", trigger="verify-abandon")
    abandoned = queue.read(count=1, block_ms=1000)
    check("job delivered to the first consumer", len(abandoned) == 1)

    # A second consumer with a 0ms idle threshold stands in for "the first worker died".
    other = JobQueue(client, stream=stream)
    reclaimed, _ = _autoclaim(client, stream, other._consumer)  # noqa: SLF001
    check("a surviving consumer can reclaim it", len(reclaimed) == 1)
    delivered = client.xpending_range(stream, keys.CONSUMER_GROUP,
                                      min=reclaimed[0][0], max=reclaimed[0][0], count=1)
    check("delivery count incremented on reclaim", int(delivered[0]["times_delivered"]) >= 2)

    # --- DLQ ------------------------------------------------------------------------------------
    print("\ndead-letter queue")
    poison_id = reclaimed[0][0]
    from src.cache.queue import Job
    queue.dead_letter(Job(entry_id=poison_id, run_id="run-poison", cluster=cluster,
                          delivery_count=4, raw={"run_id": "run-poison", "cluster": cluster}),
                      reason="exceeded 3 delivery attempts")
    dlq = client.xrange(keys.STREAM_ANALYSIS_DLQ, count=100)
    mine = [e for e in dlq if e[1].get("cluster") == cluster]
    check("poison job landed in the DLQ", len(mine) == 1)
    check("DLQ entry records why", mine[0][1]["dlq_reason"] == "exceeded 3 delivery attempts")
    pending = client.xpending(stream, keys.CONSUMER_GROUP)
    check("dead-lettered job no longer pending on the main stream",
          int(pending["pending"]) == 0)

    # --- cleanup --------------------------------------------------------------------------------
    client.delete(stream)
    for entry_id, _ in mine:
        client.xdel(keys.STREAM_ANALYSIS_DLQ, entry_id)

    print(f"\n{_checks - len(_failures)}/{_checks} checks passed")
    if _failures:
        for f in _failures:
            print(f"  FAILED: {f}")
        return 1
    print("VALKEY_VERIFY_OK")
    return 0


def _autoclaim(client, stream: str, consumer: str):
    """XAUTOCLAIM with a 0ms idle threshold, to force an immediate reclaim in a test."""
    time.sleep(0.05)
    cursor, entries, _deleted = client.xautoclaim(
        stream, keys.CONSUMER_GROUP, consumer, min_idle_time=0, count=10)
    return entries, cursor


if __name__ == "__main__":
    raise SystemExit(main())
