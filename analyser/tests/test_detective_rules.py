#!/usr/bin/env python3
"""
Tests for the three ruleset-1.1.0 rules and the precedence between them.

The eval gate grades accuracy and calibration over the whole corpus. These tests assert the
individual DISCRIMINATIONS, which a corpus-level metric can hide: a rule that fires on the wrong
evidence can still leave aggregate accuracy acceptable while producing the exactly-wrong remediation
for a specific incident.

The precedence tests matter most. NODE_PRESSURE_EVICTION and MEM_LIMIT_TOO_LOW have OPPOSITE
remediations -- "do not raise this request" versus "raise this request" -- so confusing them is worse
than saying nothing.
"""
from __future__ import annotations

from src.detective.engine import RULESET_VERSION, investigate, verdicts_digest

MIB = 1024 * 1024


def bundle(**over) -> dict:
    """A minimal healthy bundle, overridden per test."""
    base = {
        "schema": "kubethrifty.detective/v1",
        "case": "test",
        "container": "app",
        "exit_code": 0,
        "conditions": {},
        "signals": {"oom_kills": 0, "throttle_ratio": 0.0,
                    "psi_mem_full": 0.0, "psi_cpu_full": 0.0},
        "cgroup": {"memory_current": 100 * MIB, "memory_peak": 110 * MIB,
                   "memory_max": 512 * MIB, "oom_kill_total": 0},
        "hpa": None,
        "node": None,
        "qos": None,
        "startup": None,
        "kubethrifty_changes": [],
    }
    for key, value in over.items():
        if key in ("signals", "cgroup") and isinstance(value, dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def rule_ids(b: dict) -> list[str]:
    return [v.rule_id for v in investigate(b)]


def top(b: dict):
    verdicts = investigate(b)
    return verdicts[0] if verdicts else None


# ==================================================================================================
# NODE_PRESSURE_EVICTION
# ==================================================================================================

def test_fires_when_the_node_ran_out_and_the_container_was_innocent():
    v = top(bundle(
        node={"conditions": {"MemoryPressure": "True"}, "available_mib": 90},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
        cgroup={"memory_current": 60 * MIB, "memory_max": 512 * MIB},
    ))
    assert v is not None
    assert v.rule_id == "NODE_PRESSURE_EVICTION"
    # The remediation is the OPPOSITE of MEM_LIMIT_TOO_LOW's, which is the whole point of the rule.
    assert "Do NOT raise" in v.remediation


def test_does_not_fire_without_node_pressure():
    assert "NODE_PRESSURE_EVICTION" not in rule_ids(bundle(
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
        cgroup={"memory_current": 60 * MIB, "memory_max": 512 * MIB},
    ))


def test_lower_confidence_without_an_eviction_record():
    """Node condition plus low own-usage is an inference; an eviction record is evidence."""
    with_eviction = top(bundle(
        node={"conditions": {"MemoryPressure": "True"}},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
        cgroup={"memory_current": 60 * MIB, "memory_max": 512 * MIB},
    ))
    without = top(bundle(
        node={"conditions": {"MemoryPressure": "True"}},
        cgroup={"memory_current": 60 * MIB, "memory_max": 512 * MIB},
    ))
    assert with_eviction.confidence > without.confidence


def test_unlimited_container_is_never_implicated_by_its_own_limit():
    """A container with no memory limit cannot be over it, so node pressure owns the verdict."""
    v = top(bundle(
        node={"conditions": {"MemoryPressure": "True"}},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
        # memory_max None means "max" -- unlimited.
        cgroup={"memory_current": 8000 * MIB, "memory_max": None},
    ))
    assert v.rule_id == "NODE_PRESSURE_EVICTION"


# ==================================================================================================
# PRECEDENCE: own limit versus node pressure
# ==================================================================================================

def test_own_limit_wins_over_node_pressure():
    """
    A container pinned at its own limit really did outgrow it.

    Node pressure is incidental, and answering NODE_PRESSURE_EVICTION here would tell an operator
    NOT to raise a request that genuinely needs raising.
    """
    ids = rule_ids(bundle(
        exit_code=137,
        signals={"oom_kills": 1, "psi_mem_full": 0.18},
        cgroup={"memory_current": 505 * MIB, "memory_peak": 511 * MIB, "memory_max": 512 * MIB},
        node={"conditions": {"MemoryPressure": "True"}},
    ))
    assert ids[0] == "MEM_LIMIT_TOO_LOW"
    assert "NODE_PRESSURE_EVICTION" not in ids


def test_mem_pressure_no_kill_defers_to_node_pressure():
    """
    Memory stall on a node that is itself out of memory is collateral damage.

    Without this guard the engine blames the container's own limit and recommends MORE memory -- on a
    node that has none left, which makes the problem worse.
    """
    ids = rule_ids(bundle(
        signals={"psi_mem_full": 0.12},
        cgroup={"memory_current": 100 * MIB, "memory_max": 2048 * MIB},
        node={"conditions": {"MemoryPressure": "True"}},
    ))
    assert "MEM_PRESSURE_NO_KILL" not in ids
    assert "NODE_PRESSURE_EVICTION" in ids


def test_mem_pressure_no_kill_still_fires_when_near_its_own_limit():
    """Node pressure must not suppress a genuine own-limit stall."""
    ids = rule_ids(bundle(
        signals={"psi_mem_full": 0.12},
        cgroup={"memory_current": 490 * MIB, "memory_max": 512 * MIB},
        node={"conditions": {"MemoryPressure": "True"}},
    ))
    assert "MEM_PRESSURE_NO_KILL" in ids


# ==================================================================================================
# QOS_DEMOTION_EVICTION
# ==================================================================================================

def _qos_change():
    return [{"kind": "merged_pr", "pr_url": "https://example/pr/1", "recommendation_id": 1,
             "hours_before_incident": 10.0, "container": "app", "delta": {"cpu": "1 -> 400m"}}]


def test_fires_on_a_demotion_attributable_to_us():
    v = top(bundle(
        qos={"before": "Guaranteed", "after": "Burstable"},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
        node={"conditions": {"MemoryPressure": "True"}},
        kubethrifty_changes=_qos_change(),
    ))
    assert v.rule_id == "QOS_DEMOTION_EVICTION"
    assert v.attributed_change is not None


def test_does_not_fire_without_attribution():
    """
    Without one of OUR changes, a QoS class is a fact about the pod rather than something we did.

    Claiming it would be exactly the correlation-as-causation error the red-herring cases exist to
    penalise.
    """
    assert "QOS_DEMOTION_EVICTION" not in rule_ids(bundle(
        qos={"before": "Guaranteed", "after": "Burstable"},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
    ))


def test_promotion_is_never_an_incident():
    """Moving UP the eviction order is a safety improvement."""
    assert "QOS_DEMOTION_EVICTION" not in rule_ids(bundle(
        qos={"before": "Burstable", "after": "Guaranteed"},
        kubethrifty_changes=_qos_change(),
    ))


def test_latent_demotion_is_lower_confidence_than_an_eviction():
    """Demoted but still running is a risk, not an incident. Reporting both at 0.95 would cry wolf."""
    evicted = top(bundle(
        qos={"before": "Guaranteed", "after": "Burstable"},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
        node={"conditions": {"MemoryPressure": "True"}},
        kubethrifty_changes=_qos_change(),
    ))
    latent = top(bundle(
        qos={"before": "Guaranteed", "after": "Burstable"},
        kubethrifty_changes=_qos_change(),
    ))
    assert latent.rule_id == "QOS_DEMOTION_EVICTION"
    assert latent.confidence < evicted.confidence


# ==================================================================================================
# STARTUP_CPU_STARVATION
# ==================================================================================================

def test_fires_when_startup_throttles_and_probes_fail():
    v = top(bundle(
        signals={"throttle_ratio": 0.004},
        startup={"throttle_ratio": 0.71, "probe_failures": 4, "restarts": 3,
                 "duration_seconds": 95},
    ))
    assert v.rule_id == "STARTUP_CPU_STARVATION"
    # The remediation must NOT be "raise the steady-state request" -- that is the trap this rule
    # exists to avoid.
    assert "Do not raise the steady-state request" in v.remediation


def test_does_not_fire_when_startup_throttling_caused_no_harm():
    """Throttling without harm is not an incident. A metric alone must not manufacture one."""
    assert "STARTUP_CPU_STARVATION" not in rule_ids(bundle(
        signals={"throttle_ratio": 0.003},
        startup={"throttle_ratio": 0.38, "probe_failures": 0, "restarts": 0,
                 "duration_seconds": 60},
    ))


def test_steady_state_throttling_outranks_startup_starvation():
    """
    Throttled at startup AND in steady state is simply a limit that is too low.

    Calling that startup-specific would send an operator to tune probe timings when the real fix is
    the CPU limit.
    """
    ids = rule_ids(bundle(
        signals={"throttle_ratio": 0.28, "psi_cpu_full": 0.09},
        startup={"throttle_ratio": 0.33, "probe_failures": 1, "restarts": 1,
                 "duration_seconds": 25},
    ))
    assert ids[0] == "CPU_LIMIT_TOO_LOW"


def test_oom_kill_defers_to_the_memory_rules():
    """An OOM kill is a memory story, whatever the CPU did during startup."""
    ids = rule_ids(bundle(
        exit_code=137,
        signals={"oom_kills": 1, "throttle_ratio": 0.004},
        cgroup={"memory_current": 505 * MIB, "memory_peak": 511 * MIB, "memory_max": 512 * MIB},
        startup={"throttle_ratio": 0.71, "probe_failures": 4, "restarts": 3},
    ))
    assert "STARTUP_CPU_STARVATION" not in ids
    assert ids[0] == "MEM_LIMIT_TOO_LOW"


# ==================================================================================================
# Purity
# ==================================================================================================

def test_investigate_is_pure():
    """
    Same bundle in, byte-identical verdicts out -- forever.

    This is what makes offline replay meaningful. A rule reading a clock, iterating a set, or using
    randomness would break it, and no accuracy metric would notice.
    """
    b = bundle(
        exit_code=137,
        signals={"oom_kills": 2, "psi_mem_full": 0.2},
        cgroup={"memory_current": 500 * MIB, "memory_peak": 511 * MIB, "memory_max": 512 * MIB},
        kubethrifty_changes=_qos_change(),
    )
    digests = {verdicts_digest(investigate(b)) for _ in range(50)}
    assert len(digests) == 1


def test_investigate_does_not_mutate_the_bundle():
    """A pure function that edits its input is not replayable: the second call sees different data."""
    import copy
    b = bundle(
        node={"conditions": {"MemoryPressure": "True"}},
        conditions={"Evicted": {"status": "True", "reason": "Evicted"}},
    )
    before = copy.deepcopy(b)
    investigate(b)
    assert b == before


def test_ruleset_version_is_recorded():
    """A confidence value is only meaningful next to the ruleset it was calibrated under."""
    assert RULESET_VERSION == "1.1.0"


def test_eight_rules_are_registered():
    from src.detective.engine import RULES
    assert len(RULES) == 8
