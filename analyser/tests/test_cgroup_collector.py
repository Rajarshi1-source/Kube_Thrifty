#!/usr/bin/env python3
"""
Tests for the cgroup evidence layer.

These are written against a synthetic cgroup tree on disk rather than mocks, because the thing most
likely to be wrong is the PARSING of real kernel file formats -- and a mock would happily return
whatever shape the test author assumed.

Every test here maps to a specific way the obvious implementation is wrong.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.collector.cgroup import (
    parse_int_or_max,
    parse_keyed,
    parse_pressure,
    read_cgroup,
)
from src.collector.discovery import discover, normalise_pod_uid

# ==================================================================================================
# parse_int_or_max -- the single most consequential parser in the evidence layer
# ==================================================================================================

def test_max_parses_to_none_not_zero():
    """
    cgroup v2 writes the literal "max" for "no limit".

    `int("max")` raises, so the tempting `except: return 0` turns "unlimited" into "a limit of zero
    bytes" -- making an unconstrained container look infinitely over its limit.
    """
    assert parse_int_or_max("max") is None
    assert parse_int_or_max("max") != 0


def test_missing_and_empty_parse_to_none():
    assert parse_int_or_max(None) is None
    assert parse_int_or_max("") is None


def test_unparseable_is_none_not_zero():
    assert parse_int_or_max("garbage") is None


def test_real_values_parse():
    assert parse_int_or_max("0") == 0        # a genuine measured zero IS allowed through
    assert parse_int_or_max("2147483648") == 2147483648


# ==================================================================================================
# PSI parsing
# ==================================================================================================

PSI_SAMPLE = """some avg10=0.00 avg60=0.16 avg300=0.30 total=8960918
full avg10=1.50 avg60=0.25 avg300=0.05 total=12345
"""


def test_psi_splits_some_and_full():
    p = parse_pressure(PSI_SAMPLE)
    assert p.some_avg60 == 0.16
    # `full` is what the gates use. `some` above zero is the steady state of any busy node and would
    # block every reduction if treated as pressure.
    assert p.full_avg10 == 1.50
    assert p.full_total_us == 12345
    assert p.observed is True


def test_psi_totals_are_integers_and_averages_are_floats():
    """The avg fields are PERCENTAGES (0-100); total is MICROSECONDS. Conflating them yields a
    'pressure' in the millions."""
    p = parse_pressure(PSI_SAMPLE)
    assert isinstance(p.full_avg10, float)
    assert isinstance(p.full_total_us, int)
    assert p.some_total_us == 8960918


def test_missing_psi_is_unobserved_not_zero():
    p = parse_pressure(None)
    assert p.observed is False
    # Every field is None. A caller cannot accidentally read "no pressure" out of this.
    assert p.full_avg10 is None
    assert p.some_avg10 is None


# ==================================================================================================
# memory.events / cpu.stat
# ==================================================================================================

def test_keyed_file_parsing():
    parsed = parse_keyed("low 0\nhigh 12\nmax 3\noom 1\noom_kill 2\n")
    assert parsed["oom_kill"] == 2
    assert parsed["high"] == 12


def test_keyed_file_ignores_malformed_lines():
    assert parse_keyed("oom_kill 4\ngarbage\n\nnr_periods notanumber\n") == {"oom_kill": 4}


# ==================================================================================================
# Whole-cgroup reads against a synthetic tree
# ==================================================================================================

def test_read_cgroup_full(tmp_path: Path):
    cg = tmp_path / "container"
    cg.mkdir()
    (cg / "memory.peak").write_text("2147483648\n")
    (cg / "memory.current").write_text("1073741824\n")
    (cg / "memory.max").write_text("4294967296\n")
    (cg / "memory.events").write_text("low 0\nhigh 3\nmax 1\noom 1\noom_kill 2\n")
    (cg / "cpu.stat").write_text("usage_usec 100000\nnr_periods 1000\nnr_throttled 25\nthrottled_usec 500\n")
    (cg / "memory.pressure").write_text(PSI_SAMPLE)
    (cg / "cpu.pressure").write_text(PSI_SAMPLE)

    truth = read_cgroup(cg)

    assert truth.memory_peak == 2147483648
    assert truth.memory_max == 4294967296
    assert truth.oom_kill == 2
    # A RATIO, computed here so nobody downstream compares raw counters across unequal windows.
    assert truth.throttle_ratio == pytest.approx(0.025)
    # 1 - (2 GiB / 4 GiB)
    assert truth.headroom_index == pytest.approx(0.5)
    assert truth.evidence_complete is True


def test_unlimited_memory_yields_no_headroom_number(tmp_path: Path):
    """A container with no limit has INFINITE headroom, which is not a number.

    Reporting 1.0 would put it on the same scale as measured values and make it look like the
    safest thing in the cluster."""
    cg = tmp_path / "unlimited"
    cg.mkdir()
    (cg / "memory.peak").write_text("1000\n")
    (cg / "memory.max").write_text("max\n")

    truth = read_cgroup(cg)
    assert truth.memory_max is None
    assert truth.headroom_index is None


def test_zero_cfs_periods_yields_undefined_ratio(tmp_path: Path):
    """No CFS period elapsed means the throttle ratio is UNDEFINED.

    0.0 would claim we looked and found no throttling."""
    cg = tmp_path / "idle"
    cg.mkdir()
    (cg / "cpu.stat").write_text("usage_usec 0\nnr_periods 0\nnr_throttled 0\n")

    assert read_cgroup(cg).throttle_ratio is None


def test_empty_cgroup_is_all_none(tmp_path: Path):
    """A cgroup that vanished mid-read must not crash the scrape, and must not report zeros."""
    cg = tmp_path / "gone"
    cg.mkdir()

    truth = read_cgroup(cg)
    assert truth.memory_peak is None
    assert truth.memory_max is None
    assert truth.oom_kill is None
    assert truth.throttle_ratio is None
    assert truth.headroom_index is None
    assert truth.evidence_complete is False


def test_missing_psi_downgrades_evidence(tmp_path: Path):
    """memory.peak present but PSI absent must NOT count as complete evidence."""
    cg = tmp_path / "nopsi"
    cg.mkdir()
    (cg / "memory.peak").write_text("1000\n")

    truth = read_cgroup(cg)
    assert truth.memory_peak == 1000
    assert truth.evidence_complete is False


# ==================================================================================================
# Discovery
# ==================================================================================================

def test_pod_uid_underscores_become_dashes():
    """The systemd driver substitutes '_' for '-' because '-' is the slice separator.

    Without converting back, the UID matches nothing in the Kubernetes API and every sample is
    collected correctly and then silently discarded."""
    assert (
        normalise_pod_uid("2d5a26be_1f1f_45c0_b9d4_642c1fd00bfe")
        == "2d5a26be-1f1f-45c0-b9d4-642c1fd00bfe"
    )


def test_pod_uid_without_separators_is_reformatted():
    assert normalise_pod_uid("2d5a26be1f1f45c0b9d4642c1fd00bfe") == (
        "2d5a26be-1f1f-45c0-b9d4-642c1fd00bfe"
    )


def test_discovers_systemd_layout(tmp_path: Path):
    path = (
        tmp_path
        / "kubelet.slice"
        / "kubelet-kubepods.slice"
        / "kubelet-kubepods-burstable.slice"
        / "kubelet-kubepods-burstable-pod2d5a26be_1f1f_45c0_b9d4_642c1fd00bfe.slice"
        / "cri-containerd-e662d6b521a97931ebaac02f5e7b0c0fc481e7c0ea399c874c136e4dec99e4c9.scope"
    )
    path.mkdir(parents=True)

    found = discover(tmp_path)
    assert len(found) == 1
    assert found[0].pod_uid == "2d5a26be-1f1f-45c0-b9d4-642c1fd00bfe"
    assert found[0].qos_class == "Burstable"


def test_guaranteed_pods_are_discovered(tmp_path: Path):
    """
    Guaranteed pods have NO QoS path component -- they sit directly under kubepods.

    Code that requires a QoS component silently drops every Guaranteed pod, which (having
    limit == request) are exactly the ones this product reasons hardest about.
    """
    path = (
        tmp_path
        / "kubelet.slice"
        / "kubelet-kubepods.slice"
        / "kubelet-kubepods-pod2d5a26be_1f1f_45c0_b9d4_642c1fd00bfe.slice"
        / "cri-containerd-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
    )
    path.mkdir(parents=True)

    found = discover(tmp_path)
    assert len(found) == 1
    assert found[0].qos_class == "Guaranteed"


def test_discovers_cgroupfs_layout(tmp_path: Path):
    path = (
        tmp_path
        / "kubepods"
        / "besteffort"
        / "pod2d5a26be-1f1f-45c0-b9d4-642c1fd00bfe"
        / ("b" * 64)
    )
    path.mkdir(parents=True)

    found = discover(tmp_path)
    assert len(found) == 1
    assert found[0].qos_class == "BestEffort"


def test_non_kubernetes_cgroups_are_ignored(tmp_path: Path):
    """System slices have no pod ancestor. Skipping them is normal operation, not an error."""
    (tmp_path / "system.slice" / "cri-containerd-abcdef123456.scope").mkdir(parents=True)
    assert discover(tmp_path) == []


def test_missing_root_returns_empty_not_crash(tmp_path: Path):
    assert discover(tmp_path / "does-not-exist") == []
