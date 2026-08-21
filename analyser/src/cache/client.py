#!/usr/bin/env python3
"""
client.py -- cache-aside reads and the SET NX run lock.

The governing principle: THE CACHE IS NEVER LOAD-BEARING. Every function here degrades to "go ask
the real source" when Valkey is unreachable. A cache outage must make KubeThrifty slower, never
wrong and never down -- so every Valkey call is wrapped and every failure returns the miss path.

The one exception is the lock. A lock that fails open is not a lock, so `run_lock` raises when it
cannot reach Valkey: refusing to run is correct, running twice is not.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Any, TypeVar

import redis
from redis.exceptions import RedisError

from . import keys

log = logging.getLogger(__name__)

T = TypeVar("T")

# Deliberately short. A cache is an optimisation; waiting 2s for one is worse than the miss it was
# meant to avoid.
_SOCKET_TIMEOUT = 2.0


class LockUnavailable(RuntimeError):
    """Another run holds the lock, or Valkey could not be reached to arbitrate."""


def make_client(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=_SOCKET_TIMEOUT,
        socket_connect_timeout=_SOCKET_TIMEOUT,
        # Without this a dropped connection surfaces as an error on the next command rather than
        # being transparently re-established.
        retry_on_timeout=False,
        health_check_interval=30,
    )


class Cache:
    """Cache-aside over Valkey, with every failure mode routed to the miss path."""

    def __init__(self, client: redis.Redis | None, *, enabled: bool = True) -> None:
        self._client = client
        self._enabled = enabled and client is not None

    @property
    def available(self) -> bool:
        return self._enabled

    def get_json(self, key: str) -> Any | None:
        if not self._enabled:
            return None
        try:
            raw = self._client.get(key)
        except RedisError as exc:
            log.warning("cache read failed for %s, treating as miss: %s", key, exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt entry is a miss, and it is also garbage. Drop it so the next reader does
            # not pay the same decode cost forever.
            log.warning("discarding undecodable cache entry %s", key)
            self.delete(key)
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        """
        TTL is a required argument, not a defaulted one.

        Every key here describes something that goes stale. A `set` without an expiry is a slow
        memory leak whose symptom appears weeks later as an eviction storm.
        """
        if not self._enabled:
            return
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value, separators=(",", ":")))
        except RedisError as exc:
            log.warning("cache write failed for %s, continuing uncached: %s", key, exc)

    def delete(self, *key: str) -> None:
        if not self._enabled or not key:
            return
        try:
            self._client.delete(*key)
        except RedisError as exc:
            log.warning("cache delete failed: %s", exc)

    def get_or_compute(self, key: str, ttl_seconds: int, compute: Callable[[], T]) -> T:
        """
        The cache-aside pattern.

        `compute` runs on a miss and its result is cached. Note the ordering: compute FIRST, cache
        second, and a cache-write failure does not fail the call. The caller gets its answer
        whatever Valkey is doing.
        """
        hit = self.get_json(key)
        if hit is not None:
            return hit
        value = compute()
        if value is not None:
            self.set_json(key, value, ttl_seconds)
        return value

    def invalidate_cluster(self, cluster_id: str) -> None:
        """
        Called after a run persists new recommendations.

        Explicit invalidation, by exact key. Never `SCAN`+delete by pattern: on a large keyspace
        that is an O(keys) operation triggered by a routine write.
        """
        self.delete(keys.recommendations(cluster_id))


@contextmanager
def run_lock(
    client: redis.Redis | None,
    cluster: str,
    *,
    ttl_seconds: int = 3600,
) -> Iterator[str]:
    """
    Lease-based mutual exclusion for one analysis run.

    `SET key token NX EX ttl` in a single command -- atomic by construction. The check-then-set
    alternative (`EXISTS` then `SET`) has a window between the two commands where a second runner
    also sees "free".

    The TTL is what makes this safe against a crashed holder: a process that dies mid-run cannot
    release the lock, so the lock releases itself. Without an expiry, one OOMKill would wedge the
    cluster's analyses forever.

    Release is a compare-and-delete against a random token, not a bare `DEL`. If this run overran
    its TTL, the lock now belongs to somebody else, and a blind `DEL` would delete THEIR lock --
    the classic distributed-lock footgun.

    Raises `LockUnavailable` if the lock is held, or if Valkey cannot be reached. A run that cannot
    prove it is alone does not run.
    """
    if client is None:
        raise LockUnavailable("no Valkey client configured; refusing to run unguarded")

    key = keys.analysis_lock(cluster)
    token = uuid.uuid4().hex

    try:
        acquired = client.set(key, token, nx=True, ex=ttl_seconds)
    except RedisError as exc:
        raise LockUnavailable(f"could not reach Valkey to acquire {key}: {exc}") from exc

    if not acquired:
        holder_ttl = None
        with suppress(RedisError):
            holder_ttl = client.ttl(key)
        raise LockUnavailable(
            f"another analysis holds {key}"
            + (f" (expires in {holder_ttl}s)" if holder_ttl and holder_ttl > 0 else "")
        )

    log.info("acquired %s for %ds", key, ttl_seconds)
    started = time.monotonic()
    try:
        yield token
    finally:
        elapsed = time.monotonic() - started
        if elapsed > ttl_seconds:
            # Do not touch the key: the lease expired and may have been re-acquired.
            log.error(
                "run exceeded its %ds lease by %.0fs; NOT releasing %s (may belong to another run)",
                ttl_seconds, elapsed - ttl_seconds, key,
            )
        else:
            _release(client, key, token)


# Compare-and-delete. Lua because it must be one atomic step: read the token, and delete only if it
# is still ours. Split across two round trips, the lock could expire and be re-acquired in between.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _release(client: redis.Redis, key: str, token: str) -> None:
    try:
        released = client.eval(_RELEASE_LUA, 1, key, token)
        if released:
            log.info("released %s", key)
        else:
            log.warning("%s was not ours to release (expired or reassigned)", key)
    except RedisError as exc:
        # Not fatal: the TTL cleans up. Logged because it means the next run waits out the lease.
        log.warning("failed to release %s, relying on TTL: %s", key, exc)
