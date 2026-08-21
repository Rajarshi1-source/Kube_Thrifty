#!/usr/bin/env python3
"""
queue.py -- the `analysis-jobs` stream, its consumer group, and the dead-letter queue.

Why a STREAM and not a list: KEDA's `redis-streams` scaler reads consumer-group lag, which is what
scales the analyser from zero. A plain `LPUSH`/`BRPOP` list has no notion of "delivered but not
acknowledged", so a worker killed mid-analysis loses the job silently. With a consumer group the
entry stays in the Pending Entries List until it is explicitly ACKed, and `XAUTOCLAIM` lets a
surviving worker pick up what a dead one dropped.

The delivery contract, and it is deliberately this way round:

    ACK AFTER the work succeeds. At-least-once, never at-most-once.

An analysis is idempotent-ish and cheap to repeat (it reads metrics and writes a recommendation
keyed by run_id); losing one is not. Re-running an analysis wastes a few minutes of CPU. Dropping
one means a cluster silently stops being right-sized, which nobody notices until the bill arrives.

Poison messages are bounded by `MAX_DELIVERIES`: a job that has failed that many times goes to
`analysis-jobs-dlq` and is ACKed off the main stream. Without that cap, one malformed job is
redelivered forever and starves every good one behind it.
"""
from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis
from redis.exceptions import RedisError, ResponseError

from .keys import CONSUMER_GROUP, STREAM_ANALYSIS_DLQ, STREAM_ANALYSIS_JOBS

log = logging.getLogger(__name__)

# After this many delivery attempts a job is poison, not unlucky.
MAX_DELIVERIES = 3
# Reclaim work from a consumer that has held an entry this long without ACKing. Comfortably longer
# than a healthy analysis run, so a slow run is not stolen from itself mid-flight.
IDLE_RECLAIM_MS = 30 * 60 * 1000
# Cap the stream. Jobs are transient; unbounded retention would grow forever for no reader.
STREAM_MAXLEN = 10_000


@dataclass(frozen=True)
class Job:
    """One unit of analyser work, as read off the stream."""

    entry_id: str
    run_id: str
    cluster: str
    window: str = "7d"
    namespaces: tuple[str, ...] = ()
    trigger: str = "manual"
    auto_pr: bool = False
    delivery_count: int = 1
    raw: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_entry(cls, entry_id: str, fields: dict[str, str], delivery_count: int = 1) -> Job:
        namespaces: tuple[str, ...] = ()
        rawns = fields.get("namespaces", "")
        if rawns:
            try:
                parsed = json.loads(rawns)
                if isinstance(parsed, list):
                    namespaces = tuple(str(n) for n in parsed)
            except json.JSONDecodeError:
                namespaces = tuple(n.strip() for n in rawns.split(",") if n.strip())
        return cls(
            entry_id=entry_id,
            run_id=fields.get("run_id", ""),
            cluster=fields.get("cluster", ""),
            window=fields.get("window", "7d"),
            namespaces=namespaces,
            trigger=fields.get("trigger", "manual"),
            # Stream fields are strings. "false" is truthy in Python, so parse explicitly.
            auto_pr=fields.get("auto_pr", "false").lower() == "true",
            delivery_count=delivery_count,
            raw=fields,
        )


class JobQueue:
    """Producer and consumer for `analysis-jobs`."""

    def __init__(self, client: redis.Redis, *, stream: str = STREAM_ANALYSIS_JOBS) -> None:
        self._c = client
        self._stream = stream
        self._group = CONSUMER_GROUP
        # Identifies this worker in the PEL, so `XAUTOCLAIM` by another pod can tell whose entries
        # went stale. Pod name plus a random suffix survives restarts of the same pod name.
        self._consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

    # --- producer side ----------------------------------------------------------------------------

    def enqueue(
        self,
        *,
        cluster: str,
        window: str = "7d",
        namespaces: list[str] | None = None,
        trigger: str = "manual",
        auto_pr: bool = False,
        run_id: str | None = None,
    ) -> str:
        """
        `XADD` a job. This is the ONLY write the dashboard API performs.

        The run_id is minted HERE, by the producer, and returned so the caller can hand it straight
        back as `202 { runId }`. Letting the consumer mint it would mean the API had nothing to
        return until a worker woke up -- which, at `minReplicaCount: 0`, could be a cold start away.
        """
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        fields = {
            "run_id": run_id,
            "cluster": cluster,
            "window": window,
            "namespaces": json.dumps(namespaces or []),
            "trigger": trigger,
            "auto_pr": "true" if auto_pr else "false",
            "enqueued_at": str(int(time.time())),
        }
        self._c.xadd(self._stream, fields, maxlen=STREAM_MAXLEN, approximate=True)
        log.info("enqueued %s for cluster=%s window=%s trigger=%s", run_id, cluster, window, trigger)
        return run_id

    # --- consumer side ----------------------------------------------------------------------------

    def ensure_group(self) -> None:
        """
        Create the consumer group, tolerating the race where a sibling worker created it first.

        `mkstream=True` matters at `minReplicaCount: 0`: the first consumer may well start before
        any job has ever been added, and `XGROUP CREATE` on a nonexistent stream would otherwise
        fail.

        Starting at `id="0"` rather than `"$"` is deliberate -- `"$"` means "only jobs added from
        now on", which would silently discard everything already queued while the analyser was
        scaled to zero. That is precisely the window this system spends most of its life in.
        """
        try:
            self._c.xgroup_create(self._stream, self._group, id="0", mkstream=True)
            log.info("created consumer group %s on %s", self._group, self._stream)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            log.debug("consumer group %s already exists", self._group)

    def read(self, *, count: int = 1, block_ms: int = 5000) -> list[Job]:
        """
        Read new jobs, and first re-claim anything a dead worker abandoned.

        Reclaim comes FIRST. A stuck entry in the PEL is invisible to `>` reads, so a queue could
        look empty while holding work that nobody will ever finish.
        """
        reclaimed = self._reclaim(count=count)
        if reclaimed:
            return reclaimed

        try:
            response = self._c.xreadgroup(
                self._group, self._consumer, {self._stream: ">"}, count=count, block=block_ms,
            )
        except RedisError as exc:
            log.warning("xreadgroup failed: %s", exc)
            return []

        jobs: list[Job] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                jobs.append(Job.from_entry(entry_id, fields))
        return jobs

    def _reclaim(self, *, count: int) -> list[Job]:
        """
        `XAUTOCLAIM` entries idle longer than `IDLE_RECLAIM_MS`.

        This is the dead-man's-switch for the queue: the reason a worker OOMKilled mid-analysis does
        not strand its job. Anything already past `MAX_DELIVERIES` is dead-lettered here rather than
        handed to yet another worker.
        """
        try:
            _cursor, entries, _deleted = self._c.xautoclaim(
                self._stream, self._group, self._consumer,
                min_idle_time=IDLE_RECLAIM_MS, count=count,
            )
        except RedisError as exc:
            log.warning("xautoclaim failed: %s", exc)
            return []

        jobs: list[Job] = []
        for entry_id, fields in entries or []:
            deliveries = self._delivery_count(entry_id)
            if deliveries > MAX_DELIVERIES:
                self.dead_letter(
                    Job.from_entry(entry_id, fields, deliveries),
                    reason=f"exceeded {MAX_DELIVERIES} delivery attempts",
                )
                continue
            log.warning("reclaimed abandoned job %s (delivery %d)", entry_id, deliveries)
            jobs.append(Job.from_entry(entry_id, fields, deliveries))
        return jobs

    def _delivery_count(self, entry_id: str) -> int:
        try:
            pending = self._c.xpending_range(
                self._stream, self._group, min=entry_id, max=entry_id, count=1,
            )
        except RedisError:
            return 1
        return int(pending[0]["times_delivered"]) if pending else 1

    def ack(self, job: Job) -> None:
        """ACK. Called only after the work is durably done."""
        try:
            self._c.xack(self._stream, self._group, job.entry_id)
        except RedisError as exc:
            # The job succeeded; the ACK did not. It will be redelivered and re-run. That is the
            # cost of at-least-once, and it is the right trade here.
            log.error("failed to ACK %s (will be redelivered): %s", job.entry_id, exc)

    def dead_letter(self, job: Job, *, reason: str) -> None:
        """
        Move a poison job to `analysis-jobs-dlq`, then ACK it off the main stream.

        Order matters: write the DLQ entry FIRST, ACK second. Reversed, a crash between the two
        loses the job entirely -- and the whole point of a DLQ is that nothing disappears without a
        record of why.
        """
        payload: dict[str, Any] = dict(job.raw)
        payload.update({
            "dlq_reason": reason,
            "dlq_at": str(int(time.time())),
            "original_entry_id": job.entry_id,
            "delivery_count": str(job.delivery_count),
        })
        try:
            self._c.xadd(STREAM_ANALYSIS_DLQ, payload, maxlen=STREAM_MAXLEN, approximate=True)
        except RedisError as exc:
            # Do NOT ACK. Better to redeliver a poison job than to vanish it.
            log.error("could not write %s to DLQ, leaving it pending: %s", job.entry_id, exc)
            return
        self.ack(job)
        log.error("dead-lettered %s (run_id=%s): %s", job.entry_id, job.run_id, reason)

    def depth(self) -> int:
        """Undelivered backlog -- the figure KEDA scales on."""
        try:
            groups = self._c.xinfo_groups(self._stream)
        except RedisError:
            return 0
        for g in groups:
            if g.get("name") == self._group:
                return int(g.get("lag") or 0)
        return 0
