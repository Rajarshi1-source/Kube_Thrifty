#!/usr/bin/env python3
"""
cgroup.py -- cgroup v2 truth, read straight from the kernel.

This is the evidence layer that separates KubeThrifty from percentile-only right-sizers. cAdvisor
gives a *sampled* working set: it scrapes every 15 seconds, so a 2-second allocation burst that
OOMKills a container is simply invisible to it. `memory.peak` is the kernel's own monotonic
high-water mark. It cannot miss a spike, because it is not sampling -- it is remembering.

Every read here is read-only, and the process needs no capabilities at all.

The parsing rules are the whole point of this module, and each one exists because the obvious
implementation is wrong:

  memory.max == "max"       -> None, NOT 0. "Unlimited" parsed as zero makes every peak/limit ratio
                               either infinite or nonsense, and would make an unconstrained
                               container look like the most over-limit thing in the cluster.
  a missing file            -> None, NOT 0. The controller may not be enabled, or the path may have
                               vanished as the pod died. Both mean "unknown".
  memory.events oom_kill    -> a COUNTER. Never the flapping
                               kube_pod_container_status_last_terminated_reason gauge.
  cpu.stat                  -> both throttled_periods and nr_periods, so consumers can compute a
                               RATIO. Raw counters scale with window length, replica count and CFS
                               period, so two of them are not comparable.
  pressure files            -> avg10/avg60/avg300 are PERCENTAGES (0-100); `total` is microseconds.
                               Mixing the two silently yields a "pressure" of several million.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The cgroup v2 root as mounted into the collector container.
DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")


@dataclass(frozen=True)
class PressureReading:
    """One PSI file: `some` and `full` averages plus cumulative stall totals.

    `some` = at least one task stalled. Normal on any busy node.
    `full` = EVERY task stalled. This is the one the gates use, because `some` above zero is the
             steady state of a healthy multi-tenant node and would block every reduction.
    """

    some_avg10: float | None = None
    some_avg60: float | None = None
    some_avg300: float | None = None
    some_total_us: int | None = None
    full_avg10: float | None = None
    full_avg60: float | None = None
    full_avg300: float | None = None
    full_total_us: int | None = None

    @property
    def observed(self) -> bool:
        """False means PSI was not readable -- which must downgrade the evidence tier, never pass
        as 'no pressure'."""
        return self.some_avg10 is not None or self.full_avg10 is not None


@dataclass(frozen=True)
class CgroupTruth:
    """Everything the collector can learn about one cgroup. Every field may be None."""

    path: str

    # THE memory sizing basis: the kernel's monotonic high-water mark.
    memory_peak: int | None = None
    memory_current: int | None = None
    # None means "max" (unlimited). NOT 0.
    memory_max: int | None = None
    memory_swap_current: int | None = None

    # Counters from memory.events.
    oom_kill: int | None = None
    oom: int | None = None
    memory_high_events: int | None = None

    # cpu.stat. Both period counters, so a ratio can be computed.
    usage_usec: int | None = None
    nr_periods: int | None = None
    nr_throttled: int | None = None
    throttled_usec: int | None = None

    cpu_pressure: PressureReading = field(default_factory=PressureReading)
    memory_pressure: PressureReading = field(default_factory=PressureReading)
    io_pressure: PressureReading = field(default_factory=PressureReading)

    @property
    def throttle_ratio(self) -> float | None:
        """
        throttled_periods / cfs_periods.

        The ONLY comparable form. Returns None when the counters are unavailable, and None when
        `nr_periods` is 0 -- a container that has had no CFS period has an UNDEFINED throttle ratio,
        not a ratio of zero. Reporting 0.0 there would claim we looked and found no throttling,
        when in fact there was nothing to look at.
        """
        if self.nr_periods is None or self.nr_throttled is None:
            return None
        if self.nr_periods == 0:
            return None
        return self.nr_throttled / self.nr_periods

    @property
    def headroom_index(self) -> float | None:
        """
        How much of the memory limit remains above the observed peak, as a ratio.

        1.0  the peak never approached the limit
        0.0  the peak reached the limit -- an OOM kill is imminent or has already happened

        This is a far better signal than "current usage versus limit", which is a snapshot and
        misses the burst entirely. Returns None when the limit is unlimited (memory_max is None) --
        a container with no limit has INFINITE headroom, which is not a number and must not be
        reported as 1.0 alongside genuinely measured values.
        """
        if self.memory_peak is None or self.memory_max is None or self.memory_max <= 0:
            return None
        return max(0.0, 1.0 - (self.memory_peak / self.memory_max))

    @property
    def evidence_complete(self) -> bool:
        """True only when the memory high-water mark AND PSI were both readable.

        False forces the `partial` evidence tier. This is the check that stops a silently
        half-working collector from being mistaken for a healthy one.
        """
        return self.memory_peak is not None and self.memory_pressure.observed


def _read_text(path: Path) -> str | None:
    """Read a cgroup file. Any failure is None -- deliberately not an exception.

    A cgroup path can disappear mid-read when a pod terminates, and the collector must survive that
    without losing the rest of the scrape. `PermissionError` and `OSError` are both expected in
    normal operation.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError, NotADirectoryError):
        return None


def parse_int_or_max(raw: str | None) -> int | None:
    """
    Parse a cgroup v2 numeric field.

    THE most important parser in the evidence layer. cgroup v2 writes the literal string "max" for
    "no limit", and `int("max")` raises -- so the tempting `except: return 0` turns "unlimited" into
    "a limit of zero bytes". Every ratio computed from that is wrong, and wrong in the alarming
    direction: an unconstrained container would appear infinitely over its limit.
    """
    if raw is None or raw == "":
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        log.debug("unparseable cgroup value %r", raw)
        return None


def parse_keyed(raw: str | None) -> dict[str, int]:
    """Parse a cgroup 'flat keyed' file: one `key value` pair per line."""
    out: dict[str, int] = {}
    if not raw:
        return out
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        value = parse_int_or_max(parts[1])
        if value is not None:
            out[parts[0]] = value
    return out


def parse_pressure(raw: str | None) -> PressureReading:
    """
    Parse a PSI file:

        some avg10=0.00 avg60=0.16 avg300=0.30 total=8960918
        full avg10=0.00 avg60=0.00 avg300=0.00 total=0

    The avg fields are PERCENTAGES (0-100) over their window; `total` is cumulative microseconds
    stalled. Treating them as the same unit produces a "pressure" in the millions, which is the kind
    of number that gets rendered on a dashboard without anyone noticing it is meaningless.
    """
    if not raw:
        return PressureReading()

    values: dict[str, float | int | None] = {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        scope = parts[0]
        if scope not in ("some", "full"):
            continue
        for field_pair in parts[1:]:
            if "=" not in field_pair:
                continue
            key, _, val = field_pair.partition("=")
            try:
                values[f"{scope}_{key}"] = int(val) if key == "total" else float(val)
            except ValueError:
                continue

    def f(key: str) -> float | None:
        v = values.get(key)
        return None if v is None else float(v)

    def i(key: str) -> int | None:
        v = values.get(key)
        return None if v is None else int(v)

    return PressureReading(
        some_avg10=f("some_avg10"),
        some_avg60=f("some_avg60"),
        some_avg300=f("some_avg300"),
        some_total_us=i("some_total"),
        full_avg10=f("full_avg10"),
        full_avg60=f("full_avg60"),
        full_avg300=f("full_avg300"),
        full_total_us=i("full_total"),
    )


def read_cgroup(path: Path) -> CgroupTruth:
    """
    Read every interesting file under one cgroup directory.

    Never raises. A cgroup that vanishes mid-read yields a CgroupTruth full of Nones, which
    correctly reports as "not observed" rather than taking the whole scrape down.
    """
    memory_events = parse_keyed(_read_text(path / "memory.events"))
    cpu_stat = parse_keyed(_read_text(path / "cpu.stat"))

    return CgroupTruth(
        path=str(path),
        # memory.peak needs kernel >= 5.19. Absent on older kernels, in which case the sizer falls
        # back to a sampled gauge and the recommendation is correctly stamped `partial`.
        memory_peak=parse_int_or_max(_read_text(path / "memory.peak")),
        memory_current=parse_int_or_max(_read_text(path / "memory.current")),
        memory_max=parse_int_or_max(_read_text(path / "memory.max")),
        memory_swap_current=parse_int_or_max(_read_text(path / "memory.swap.current")),
        oom_kill=memory_events.get("oom_kill"),
        oom=memory_events.get("oom"),
        memory_high_events=memory_events.get("high"),
        usage_usec=cpu_stat.get("usage_usec"),
        nr_periods=cpu_stat.get("nr_periods"),
        nr_throttled=cpu_stat.get("nr_throttled"),
        throttled_usec=cpu_stat.get("throttled_usec"),
        cpu_pressure=parse_pressure(_read_text(path / "cpu.pressure")),
        memory_pressure=parse_pressure(_read_text(path / "memory.pressure")),
        io_pressure=parse_pressure(_read_text(path / "io.pressure")),
    )
