#!/usr/bin/env python3
"""
Tests for the Resize Rehearsal.

These are the highest-stakes tests in the project. A rehearsal mutates a live production pod, so the
properties asserted here are the ones that stand between "a controlled experiment" and "an outage":

  1. The compensation row is written BEFORE the pod is touched.
  2. The revert happens on EVERY path -- success, regression, exception, and a failed resize.
  3. A resize that did not actually apply is `inconclusive`, never `safe`.
  4. Only a `safe` outcome may produce a sizing floor.
  5. A replaced pod is never reverted.

Each test is written to fail loudly if the ordering or the outcome mapping is ever relaxed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.rehearsal.candidates import build_candidates, rank_workloads
from src.rehearsal.preflight import (
    ALLOW_QOS_DEMOTION,
    PodFacts,
    preflight,
    qos_after,
)
from src.rehearsal.runner import (
    Outcome,
    Rehearsal,
    RehearsalRequest,
    ResizeState,
    parse_quantity,
)

# ==================================================================================================
# Test doubles that RECORD ORDER
#
# The call log is the point. Several of the properties under test are about sequencing, and a mock
# that only records "was this called" cannot express "was this called first".
# ==================================================================================================

class FakeStore:
    def __init__(self, fail_open: bool = False) -> None:
        self.calls: list[str] = []
        self.opened: dict | None = None
        self.closed: dict | None = None
        self._fail_open = fail_open

    def open_rehearsal(self, **kwargs):
        self.calls.append("open")
        if self._fail_open:
            raise RuntimeError("database down")
        self.opened = kwargs

    def close_rehearsal(self, rehearsal_id, **kwargs):
        self.calls.append("close")
        self.closed = {"rehearsal_id": rehearsal_id, **kwargs}


class FakeClient:
    """
    Records every patch in order, so a test can assert the LAST patch restores the original.

    `states` is a queue of pod statuses returned by successive get_pod_status calls, which lets a
    test drive the resize through Deferred -> Applied, or simulate a replacement mid-flight.
    """

    def __init__(self, states: list[dict] | None = None, fail_patch_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.patches: list[tuple[dict, dict]] = []
        self._states = states or []
        self._fail_patch_after = fail_patch_after

    def patch_resize(self, namespace, pod, container, requests, limits):
        self.calls.append("patch")
        if self._fail_patch_after is not None and len(self.patches) >= self._fail_patch_after:
            self.patches.append((dict(requests), dict(limits)))
            raise RuntimeError("api server rejected the patch")
        self.patches.append((dict(requests), dict(limits)))

    def get_pod_status(self, namespace, pod):
        self.calls.append("status")
        if self._states:
            return self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return {}


class FakeSignals:
    def __init__(self, baseline=None, observed=None) -> None:
        self._baseline = baseline or {"throttle_ratio": 0.001}
        self._observed = observed or {"throttle_ratio": 0.001}
        self.calls = 0

    def sample(self, namespace, pod, container, seconds):
        self.calls += 1
        return self._baseline if self.calls == 1 else self._observed


class Verdict:
    def __init__(self, regressed: bool, reasons=()) -> None:
        self.regressed = regressed
        self.reasons = list(reasons)


def applied_status(uid="uid-1", requests=None):
    return {
        "uid": uid,
        "phase": "Running",
        "conditions": [],
        "containerStatuses": [
            {"name": "app", "resources": {"requests": requests or {"cpu": "300m"}}}
        ],
    }


def make_request(**overrides) -> RehearsalRequest:
    base = {
        "namespace": "shop",
        "workload": "api-gateway",
        "pod": "api-gateway-abc-123",
        "pod_uid": "uid-1",
        "container": "app",
        "cluster_id": 1,
        "run_id": "run-x",
        "original_requests": {"cpu": 0.5},
        "original_limits": {"cpu": 1.0},
        "candidate_requests": {"cpu": 0.3},
        "candidate_limits": {},
        "observe_seconds": 0,
        "baseline_seconds": 0,
        "resize_timeout_seconds": 10,
    }
    base.update(overrides)
    return RehearsalRequest(**base)


def make_rehearsal(client, store, signals, verdict) -> Rehearsal:
    return Rehearsal(
        client, signals, store,
        evaluate=lambda b, o: verdict,
        sleep=lambda s: None,
    )


# ==================================================================================================
# 1. COMPENSATION FIRST
# ==================================================================================================

def test_compensation_row_is_written_before_the_pod_is_touched():
    """
    THE most important ordering in the system.

    If the process dies one millisecond after the PATCH, this row is the only thing that knows how to
    undo it -- and the watchdog acts on it without needing this process to exist. Written afterwards,
    there is a window where a live pod carries experimental limits that nothing knows about.
    """
    store = FakeStore()
    client = FakeClient([applied_status()])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    r.run(make_request())

    # The store's first call must precede the client's first call.
    assert store.calls[0] == "open"
    assert client.calls[0] == "patch"
    # And the compensation row must carry what is needed to undo the change.
    assert store.opened is not None
    assert store.opened["original_requests"] == {"cpu": 0.5}
    assert store.opened["original_limits"] == {"cpu": 1.0}
    assert store.opened["revert_deadline"] > datetime.now(UTC)


def test_pod_is_not_touched_when_compensation_cannot_be_persisted():
    """No way to undo it means no experiment. Refusing is always preferable."""
    store = FakeStore(fail_open=True)
    client = FakeClient([applied_status()])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    result = r.run(make_request())

    assert result.outcome is Outcome.FAILED
    assert client.patches == [], "the pod must not be touched without a compensation row"


def test_revert_deadline_includes_slack_beyond_the_observation_window():
    """The watchdog only acts past the deadline, so slack stops it stealing a merely-slow rehearsal."""
    store = FakeStore()
    client = FakeClient([applied_status()])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    r.run(make_request(observe_seconds=1800, resize_timeout_seconds=120))

    deadline = store.opened["revert_deadline"]
    expected_min = datetime.now(UTC) + timedelta(seconds=1800 + 120)
    assert deadline > expected_min


# ==================================================================================================
# 2. UNCONDITIONAL REVERT
# ==================================================================================================

def test_reverts_on_success():
    store = FakeStore()
    client = FakeClient([applied_status()])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    result = r.run(make_request())

    assert result.outcome is Outcome.SAFE
    # The LAST patch must restore the originals.
    assert client.patches[-1] == ({"cpu": 0.5}, {"cpu": 1.0})


def test_reverts_on_regression():
    store = FakeStore()
    client = FakeClient([applied_status()])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(True, ["throttle ratio tripled"]))

    result = r.run(make_request())

    assert result.outcome is Outcome.REGRESSED
    assert "throttle ratio tripled" in result.trip_reasons
    assert client.patches[-1] == ({"cpu": 0.5}, {"cpu": 1.0})


def test_reverts_when_the_evaluator_raises():
    """An exception must not leave a pod resized. The revert lives in `finally` for this reason."""
    store = FakeStore()
    client = FakeClient([applied_status()])

    def boom(_b, _o):
        raise ValueError("signal maths blew up")

    r = Rehearsal(client, FakeSignals(), store, evaluate=boom, sleep=lambda s: None)
    result = r.run(make_request())

    assert result.outcome is Outcome.FAILED
    assert client.patches[-1] == ({"cpu": 0.5}, {"cpu": 1.0})


def test_reverts_when_the_resize_never_applied():
    """Even a resize that timed out gets a revert: the patch was accepted, so it must be undone."""
    store = FakeStore()
    # Status never reflects the candidate, so the runner times out.
    client = FakeClient([applied_status(requests={"cpu": "500m"})])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    result = r.run(make_request(resize_timeout_seconds=0))

    assert result.outcome is Outcome.INCONCLUSIVE
    assert client.patches[-1] == ({"cpu": 0.5}, {"cpu": 1.0})


# ==================================================================================================
# 3. A RESIZE THAT DID NOT APPLY IS NEVER `safe`
# ==================================================================================================

def test_infeasible_is_inconclusive_not_safe():
    """
    `Infeasible` means the node can never satisfy the request.

    Nothing was tested, so nothing was proven. Reporting `safe` here -- on the grounds that no
    regression was observed -- would be the most dangerous possible misreading.
    """
    store = FakeStore()
    client = FakeClient([{
        "uid": "uid-1",
        "conditions": [{"type": "PodResizePending", "status": "True", "reason": "Infeasible"}],
        "containerStatuses": [],
    }])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    result = r.run(make_request())

    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.resize_state is ResizeState.INFEASIBLE
    assert result.rehearsed_floor is None


def test_timeout_is_inconclusive_not_safe():
    store = FakeStore()
    client = FakeClient([applied_status(requests={"cpu": "500m"})])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    result = r.run(make_request(resize_timeout_seconds=0))

    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.resize_state is ResizeState.TIMEOUT


# ==================================================================================================
# 4. ONLY `safe` PRODUCES A FLOOR
# ==================================================================================================

def test_only_safe_contributes_a_floor():
    store = FakeStore()
    client = FakeClient([applied_status()])

    safe = make_rehearsal(client, store, FakeSignals(), Verdict(False)).run(make_request())
    assert safe.outcome is Outcome.SAFE
    assert safe.contributes_floor is True
    # The floor is the CANDIDATE -- what was proven to work -- not the observed usage.
    assert safe.rehearsed_floor == {"cpu": 0.3}

    for verdict, expected in (
        (Verdict(True, ["oom"]), Outcome.REGRESSED),
    ):
        store2 = FakeStore()
        client2 = FakeClient([applied_status()])
        res = make_rehearsal(client2, store2, FakeSignals(), verdict).run(make_request())
        assert res.outcome is expected
        assert res.rehearsed_floor is None
        assert res.contributes_floor is False


def test_inconclusive_never_records_a_floor_in_the_store():
    """Belt and braces with the database CHECK constraint."""
    store = FakeStore()
    client = FakeClient([applied_status(requests={"cpu": "500m"})])
    make_rehearsal(client, store, FakeSignals(), Verdict(False)).run(
        make_request(resize_timeout_seconds=0)
    )
    assert store.closed["rehearsed_floor"] is None


# ==================================================================================================
# 5. A REPLACED POD IS NEVER REVERTED
# ==================================================================================================

def test_replaced_pod_is_not_reverted():
    """
    A different UID means a different container.

    Reverting would patch original values onto a pod that never left them -- a write with no
    justification, against a container the experiment never touched.
    """
    store = FakeStore()
    client = FakeClient([{"uid": "uid-DIFFERENT", "conditions": [], "containerStatuses": []}])
    r = make_rehearsal(client, store, FakeSignals(), Verdict(False))

    result = r.run(make_request())

    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.resize_state is ResizeState.POD_REPLACED
    assert result.reverted is False
    # Exactly one patch: the original candidate. No revert.
    assert len(client.patches) == 1


# ==================================================================================================
# Quantity parsing -- the comparison the resize assertion depends on
# ==================================================================================================

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100m", 0.1),
        ("1", 1.0),
        ("0.5", 0.5),
        ("2", 2.0),
        ("512Mi", 512 * 1024 * 1024),
        ("1Gi", 1024**3),
        ("536870912", 536870912.0),
        ("1000n", 1e-6),
    ],
)
def test_parse_quantity(raw, expected):
    assert parse_quantity(raw) == pytest.approx(expected)


def test_mi_is_not_parsed_as_m():
    """"512Mi" must be mebibytes, not 512 milli-somethings. Two-char suffixes are checked first."""
    assert parse_quantity("512Mi") == 512 * 1024 * 1024
    assert parse_quantity("512m") == pytest.approx(0.512)


def test_unparseable_quantity_is_none():
    assert parse_quantity("") is None
    assert parse_quantity("banana") is None


# ==================================================================================================
# Preflight
# ==================================================================================================

def healthy_facts(**overrides) -> PodFacts:
    base = {
        "namespace": "shop",
        "workload": "api-gateway",
        "pod": "api-gateway-abc",
        "pod_uid": "uid-1",
        "container": "app",
        "phase": "Running",
        "started_at": datetime.now(UTC) - timedelta(hours=2),
        "ready_replicas": 3,
        "desired_replicas": 3,
        "current_requests": {"cpu": 0.5, "memory": 512.0},
        "current_limits": {"cpu": 1.0, "memory": 512.0},
        "qos_class": "Burstable",
        "oom_events_recent": 0,
        "restarts_recent": 0,
    }
    base.update(overrides)
    return PodFacts(**base)


def run_preflight(facts, requests=None, limits=None, **kwargs):
    defaults = {
        "enabled": True,
        "kill_switch_engaged": False,
        "running_rehearsals": 0,
        "max_concurrent": 3,
        "workload_has_active_rehearsal": False,
    }
    defaults.update(kwargs)
    return preflight(facts, requests or {"cpu": 0.3}, limits or {}, **defaults)


def test_healthy_pod_passes():
    assert run_preflight(healthy_facts()).ok


def test_kill_switch_refuses_alone():
    """When the kill switch is on, nothing else matters -- and a list of secondary complaints would
    obscure the real reason."""
    result = run_preflight(healthy_facts(phase="Pending"), kill_switch_engaged=True)
    assert not result.ok
    assert len(result.reasons) == 1
    assert "kill switch" in result.reasons[0]


def test_single_replica_workload_is_refused():
    """No spare replica means nowhere for traffic to go if the experiment degrades the pod."""
    result = run_preflight(healthy_facts(desired_replicas=1, ready_replicas=1))
    assert not result.ok
    assert any("replica" in r for r in result.reasons)


def test_young_pod_is_refused():
    result = run_preflight(healthy_facts(started_at=datetime.now(UTC) - timedelta(minutes=2)))
    assert not result.ok
    assert any("old" in r or "baseline" in r for r in result.reasons)


def test_unknown_start_time_fails_closed():
    """Unknown is not safe. A missing input must never read as a pass."""
    result = run_preflight(healthy_facts(started_at=None))
    assert not result.ok


def test_unknown_oom_history_fails_closed():
    result = run_preflight(healthy_facts(oom_events_recent=None))
    assert not result.ok
    assert any("OOM" in r for r in result.reasons)


def test_recent_oom_is_refused():
    result = run_preflight(healthy_facts(oom_events_recent=2))
    assert not result.ok


def test_restart_container_resize_policy_is_refused():
    """`RestartContainer` turns an in-place resize into a restart, which is what a rehearsal
    promises not to do."""
    result = run_preflight(healthy_facts(resize_policy={"cpu": "RestartContainer"}))
    assert not result.ok
    assert any("RestartContainer" in r for r in result.reasons)


def test_pdb_at_floor_is_refused():
    result = run_preflight(healthy_facts(pdb_current_healthy=2, pdb_desired_healthy=2))
    assert not result.ok
    assert any("PodDisruptionBudget" in r for r in result.reasons)


def test_concurrency_cap_is_enforced():
    result = run_preflight(healthy_facts(), running_rehearsals=3, max_concurrent=3)
    assert not result.ok
    assert any("concurrency cap" in r for r in result.reasons)


def test_one_pod_per_workload():
    result = run_preflight(healthy_facts(), workload_has_active_rehearsal=True)
    assert not result.ok
    assert any("blast radius" in r for r in result.reasons)


def test_qos_demotion_refused_without_annotation():
    guaranteed = healthy_facts(
        qos_class="Guaranteed",
        current_requests={"cpu": 1.0, "memory": 512.0},
        current_limits={"cpu": 1.0, "memory": 512.0},
    )
    # Candidate drops cpu request but leaves the limit, which demotes Guaranteed -> Burstable.
    result = run_preflight(guaranteed, {"cpu": 0.5, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0})
    assert not result.ok
    assert any("QoS" in r for r in result.reasons)


def test_qos_demotion_allowed_with_annotation():
    guaranteed = healthy_facts(
        qos_class="Guaranteed",
        current_requests={"cpu": 1.0, "memory": 512.0},
        current_limits={"cpu": 1.0, "memory": 512.0},
        annotations={ALLOW_QOS_DEMOTION: "true"},
    )
    result = run_preflight(guaranteed, {"cpu": 0.5, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0})
    assert result.ok
    assert any("QoS demotion" in w for w in result.warnings)


def test_memory_limit_must_equal_request():
    """Over-limit on memory is an OOMKill. A candidate with limit != request is not a shape the
    sizer would ever propose, so rehearsing it would test the wrong thing."""
    result = run_preflight(healthy_facts(), {"memory": 256.0}, {"memory": 512.0})
    assert not result.ok
    assert any("limit" in r and "request" in r for r in result.reasons)


def test_qos_after_requires_both_resources_to_match():
    assert qos_after({"cpu": 1.0, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0}) == "Guaranteed"
    # cpu matches, memory does not -> Burstable.
    assert qos_after({"cpu": 1.0, "memory": 256.0}, {"cpu": 1.0, "memory": 512.0}) == "Burstable"
    # Missing a limit entirely -> Burstable.
    assert qos_after({"cpu": 1.0}, {"cpu": 1.0}) == "Burstable"


# ==================================================================================================
# Candidate ranking
# ==================================================================================================

class Rec:
    def __init__(self, workload, resource, current, recommended, action="reduce",
                 tier="modelled", blocked=False):
        self.namespace = "shop"
        self.workload = workload
        self.container = "app"
        self.resource = resource
        self.current_request = current
        self.recommended_request = recommended
        self.action = action
        self.evidence_tier = tier
        self.reduction_blocked = blocked


def test_ranking_uses_absolute_saving_not_percentage():
    """
    A container wasting 90% of 50m yields 45m; one wasting 40% of 4 cores yields 1.6 cores.

    Percentage ranking picks the first and leaves the second unexamined -- and only the second can
    remove a node.
    """
    tiny_huge_pct = Rec("tiny", "cpu", 0.05, 0.005)      # 90%, saves 0.045
    big_modest_pct = Rec("big", "cpu", 4.0, 2.4)         # 40%, saves 1.6

    ranked = rank_workloads([tiny_huge_pct, big_modest_pct], top_n=1)
    assert [w.workload for w in ranked] == ["big"]


def test_increases_are_not_rehearsed():
    """Giving a container more resource is not a risk to it, so there is nothing to prove."""
    assert rank_workloads([Rec("w", "cpu", 0.1, 0.5, action="increase")]) == []


def test_already_rehearsed_is_skipped():
    assert rank_workloads([Rec("w", "cpu", 1.0, 0.5, tier="rehearsed")]) == []


def test_blocked_reductions_are_not_rehearsed():
    """The sizer already refused this cut because pressure was observed. Rehearsing it would run an
    experiment the safety layer explicitly declined."""
    assert rank_workloads([Rec("w", "cpu", 1.0, 0.5, blocked=True)]) == []


def test_cpu_and_memory_are_interleaved():
    """Memory's larger raw numbers would otherwise monopolise every slot."""
    recs = [
        Rec("mem-a", "memory", 4096.0, 1024.0),
        Rec("mem-b", "memory", 2048.0, 512.0),
        Rec("cpu-a", "cpu", 4.0, 1.0),
    ]
    ranked = rank_workloads(recs, top_n=3)
    resources = [w.resource for w in ranked]
    assert "cpu" in resources and "memory" in resources


def test_candidate_ladder_is_gentlest_first():
    """If the gentlest reduction already regresses, the aggressive ones certainly will -- and there
    is no reason to spend an hour proving it on a live pod."""
    ranked = rank_workloads([Rec("w", "cpu", 1.0, 0.5)], top_n=1)
    candidates = build_candidates(ranked[0])

    values = [c.requests["cpu"] for c in candidates]
    assert values == sorted(values, reverse=True), "candidates must get more aggressive, not less"
    assert values[0] == pytest.approx(0.75)   # halfway
    assert values[-1] == pytest.approx(0.5)   # the full proposed reduction


def test_memory_candidates_always_set_limit_equal_to_request():
    ranked = rank_workloads([Rec("w", "memory", 1024.0, 512.0)], top_n=1)
    for c in build_candidates(ranked[0]):
        assert c.limits["memory"] == c.requests["memory"]


def test_cpu_candidates_leave_the_limit_alone():
    """Changing the limit at the same time would confound two variables in one experiment."""
    ranked = rank_workloads([Rec("w", "cpu", 1.0, 0.5)], top_n=1)
    for c in build_candidates(ranked[0]):
        assert c.limits == {}
