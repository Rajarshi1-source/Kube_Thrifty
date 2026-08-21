#!/usr/bin/env python3
"""
Tests for the run lock and the job queue's safety ordering.

These use a hand-rolled stub rather than a real Valkey or a fake-redis package, deliberately: what
is under test is not Valkey's behaviour (that is Valkey's job) but OUR protocol on top of it --
that acquisition is a single NX call, that release is a compare-and-delete against our own token,
that an overrun lease is left alone, and that a dead-lettered job is written before it is ACKed.
A stub makes each of those assertions about an observable call sequence, which a real server would
hide.
"""
from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from src.cache import keys
from src.cache.client import Cache, LockUnavailable, run_lock
from src.cache.queue import Job, JobQueue


class StubRedis:
    """Just enough of the Valkey surface, with every call recorded."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.acked: list[str] = []
        self.calls: list[str] = []
        self._fail = fail
        self._seq = 0

    def _guard(self) -> None:
        if self._fail:
            raise RedisError("stub is down")

    # --- string ops -------------------------------------------------------------------------------
    def set(self, key, value, nx=False, ex=None):
        self.calls.append(f"set:{key}:nx={nx}:ex={ex}")
        self._guard()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key):
        self.calls.append(f"get:{key}")
        self._guard()
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.calls.append(f"setex:{key}:{ttl}")
        self._guard()
        self.store[key] = value
        self.ttls[key] = ttl
        return True

    def delete(self, *k):
        self.calls.append(f"delete:{','.join(k)}")
        self._guard()
        n = 0
        for key in k:
            n += 1 if self.store.pop(key, None) is not None else 0
            self.ttls.pop(key, None)
        return n

    def ttl(self, key):
        self._guard()
        return self.ttls.get(key, -1)

    def eval(self, _script, _numkeys, key, arg):
        """Stands in for the compare-and-delete Lua."""
        self.calls.append(f"eval:{key}")
        self._guard()
        if self.store.get(key) == arg:
            del self.store[key]
            self.ttls.pop(key, None)
            return 1
        return 0

    # --- stream ops -------------------------------------------------------------------------------
    def xadd(self, stream, fields, maxlen=None, approximate=True):
        self.calls.append(f"xadd:{stream}")
        self._guard()
        self._seq += 1
        entry_id = f"{self._seq}-0"
        self.streams.setdefault(stream, []).append((entry_id, dict(fields)))
        return entry_id

    def xack(self, stream, group, entry_id):
        self.calls.append(f"xack:{stream}:{entry_id}")
        self._guard()
        self.acked.append(entry_id)
        return 1


# ==================================================================================================
# The lock
# ==================================================================================================

def test_lock_acquires_with_single_nx_call_and_releases_by_token():
    c = StubRedis()
    with run_lock(c, "demo", ttl_seconds=60) as token:
        assert c.store[keys.analysis_lock("demo")] == token
        # Acquisition must be ONE atomic NX+EX command. An EXISTS-then-SET pair would leave a race
        # window in which two runners both see "free".
        assert c.calls[0] == f"set:{keys.analysis_lock('demo')}:nx=True:ex=60"
    # Released via compare-and-delete, never a bare DEL.
    assert f"eval:{keys.analysis_lock('demo')}" in c.calls
    assert keys.analysis_lock("demo") not in c.store


def test_lock_always_carries_a_ttl():
    """A lock with no expiry is wedged forever by one OOMKill mid-run."""
    c = StubRedis()
    with run_lock(c, "demo", ttl_seconds=1800):
        assert c.ttls[keys.analysis_lock("demo")] == 1800


def _try_acquire(client, cluster: str = "demo") -> bool:
    """True if the lock was granted. Used so the contention test needs no nested `with`."""
    try:
        with run_lock(client, cluster, ttl_seconds=60):
            return True
    except LockUnavailable:
        return False


def test_second_holder_is_refused():
    c = StubRedis()
    with run_lock(c, "demo", ttl_seconds=60):
        assert _try_acquire(c) is False, "two runs must never hold the same cluster lock"
    # Once released, the next run may proceed.
    assert _try_acquire(c) is True


def test_lock_fails_closed_when_valkey_is_unreachable():
    """
    The one place a Valkey outage is allowed to stop work.

    Everything else here degrades to a cache miss. A lock that fails OPEN is not a lock: it would
    let two runs open competing PRs and resize the same pod from two directions.
    """
    with pytest.raises(LockUnavailable), run_lock(StubRedis(fail=True), "demo"):
        pytest.fail("must not proceed without proving exclusivity")

    with pytest.raises(LockUnavailable), run_lock(None, "demo"):
        pytest.fail("must not proceed with no lock backend at all")


def test_lock_is_released_even_when_the_body_raises():
    c = StubRedis()
    with pytest.raises(ValueError), run_lock(c, "demo", ttl_seconds=60):
        raise ValueError("analysis blew up")
    assert keys.analysis_lock("demo") not in c.store


def test_overrun_lease_is_not_released(monkeypatch):
    """
    If the run outlived its lease, the key may now belong to somebody else.

    Deleting it would delete THEIR lock -- the classic distributed-lock footgun. The correct move is
    to leave it entirely alone and log loudly.
    """
    c = StubRedis()
    ticks = iter([0.0, 5000.0])
    monkeypatch.setattr("src.cache.client.time.monotonic", lambda: next(ticks))

    with run_lock(c, "demo", ttl_seconds=60):
        pass

    assert not any(call.startswith("eval:") for call in c.calls)
    assert not any(call.startswith("delete:") for call in c.calls)


# ==================================================================================================
# Cache-aside
# ==================================================================================================

def test_cache_outage_is_a_miss_not_an_error():
    cache = Cache(StubRedis(fail=True))
    calls = []

    def compute():
        calls.append(1)
        return {"value": 42}

    assert cache.get_or_compute("k", 60, compute) == {"value": 42}
    assert len(calls) == 1  # computed, despite Valkey being down


def test_cache_hit_skips_compute():
    c = StubRedis()
    cache = Cache(c)
    cache.set_json("k", {"value": 1}, 60)
    assert cache.get_or_compute("k", 60, lambda: pytest.fail("should not recompute")) == {"value": 1}


def test_every_cache_write_has_an_expiry():
    """No unbounded keys: `setex` only, never a bare `set`."""
    c = StubRedis()
    Cache(c).set_json("k", {"a": 1}, 900)
    assert c.ttls["k"] == 900
    assert not any(call.startswith("set:k") for call in c.calls)


def test_corrupt_entry_is_dropped_not_returned():
    c = StubRedis()
    c.store["k"] = "{not json"
    cache = Cache(c)
    assert cache.get_json("k") is None
    assert "k" not in c.store


def test_promql_key_separates_windows():
    """Same expression over different ranges is a different answer; keys must not collide."""
    a = keys.promql("up", start="1", end="2", step="30s")
    b = keys.promql("up", start="1", end="9999", step="30s")
    assert a != b
    assert keys.promql("up", start="1", end="2", step="30s") == a


# ==================================================================================================
# The queue
# ==================================================================================================

def test_job_parses_auto_pr_as_a_real_boolean():
    """Stream fields are strings, and the string "false" is truthy in Python."""
    assert Job.from_entry("1-0", {"auto_pr": "false"}).auto_pr is False
    assert Job.from_entry("1-0", {"auto_pr": "true"}).auto_pr is True


def test_job_parses_namespaces_as_json_or_csv():
    assert Job.from_entry("1-0", {"namespaces": '["a","b"]'}).namespaces == ("a", "b")
    assert Job.from_entry("1-0", {"namespaces": "a, b"}).namespaces == ("a", "b")
    assert Job.from_entry("1-0", {}).namespaces == ()


def test_enqueue_returns_the_run_id_it_minted():
    """The producer mints the run_id so the API can return 202 {runId} without waiting on a worker."""
    c = StubRedis()
    run_id = JobQueue(c).enqueue(cluster="demo", window="7d")
    assert run_id.startswith("run-")
    assert c.streams[keys.STREAM_ANALYSIS_JOBS][0][1]["run_id"] == run_id


def test_dead_letter_writes_the_dlq_entry_before_acking():
    """
    Ordering is the whole point of a DLQ.

    ACK first, then write, and a crash between them loses the job with no record of why.
    """
    c = StubRedis()
    q = JobQueue(c)
    q.dead_letter(Job(entry_id="5-0", run_id="run-x", cluster="demo", delivery_count=4),
                  reason="exceeded 3 delivery attempts")

    order = [call for call in c.calls if call.startswith(("xadd:", "xack:"))]
    assert order == [f"xadd:{keys.STREAM_ANALYSIS_DLQ}", f"xack:{keys.STREAM_ANALYSIS_JOBS}:5-0"]
    assert c.streams[keys.STREAM_ANALYSIS_DLQ][0][1]["dlq_reason"] == "exceeded 3 delivery attempts"


def test_failed_dlq_write_leaves_the_job_pending():
    """Better to redeliver a poison job than to vanish it."""
    c = StubRedis(fail=True)
    JobQueue(c).dead_letter(Job(entry_id="5-0", run_id="run-x", cluster="demo"), reason="boom")
    assert c.acked == []
