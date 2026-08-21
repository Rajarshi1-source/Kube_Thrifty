#!/usr/bin/env python3
"""
exporter.py -- the cgroup-truth DaemonSet's Prometheus endpoint.

    python -m src.collector.exporter --port 9847

Metric naming is deliberate. Every series is prefixed `kubethrifty_cgroup_` so it is obvious at a
glance that the value came from the kernel rather than from cAdvisor. When the analyser prefers
`kubethrifty_cgroup_memory_peak_bytes` over `container_memory_working_set_bytes`, the metric name
itself records why the resulting recommendation earns a stronger evidence tier.

THE RULE THAT GOVERNS THIS FILE: a metric that could not be read is NOT EXPORTED. It is never
exported as 0.

That is the opposite of what most exporters do, and it matters. An absent series makes PromQL return
an empty result, which the analyser and the dashboard both already treat as "not observed" and which
downgrades the evidence tier. A series present with value 0 asserts "we measured, and there was no
pressure" -- a claim that would justify shrinking a workload nobody has actually observed.

`kubethrifty_cgroup_scrape_*` exists for the same reason: a collector that is running but reading
nothing must be distinguishable from a healthy one, or a silent failure looks like a quiet cluster.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from prometheus_client.core import GaugeMetricFamily

from prometheus_client import REGISTRY, Gauge, start_http_server

from .cgroup import DEFAULT_CGROUP_ROOT, read_cgroup
from .discovery import discover

log = logging.getLogger("kubethrifty.collector")

LABELS = ("pod_uid", "container_id", "qos_class", "node")

SCRAPE_DURATION = Gauge(
    "kubethrifty_cgroup_scrape_duration_seconds",
    "Time spent walking the cgroup tree on the last scrape.",
)
SCRAPE_TARGETS = Gauge(
    "kubethrifty_cgroup_scrape_targets",
    "Container cgroups discovered on the last scrape. Zero means the collector found NOTHING, "
    "which is a failure and not a quiet cluster.",
)
SCRAPE_COMPLETE = Gauge(
    "kubethrifty_cgroup_scrape_evidence_complete",
    "Cgroups where BOTH memory.peak and memory PSI were readable. Compare against "
    "kubethrifty_cgroup_scrape_targets: a gap means those containers can only support the "
    "`partial` evidence tier.",
)


class CgroupCollector:
    """
    A custom Prometheus collector, scraping lazily on collect().

    Lazy rather than on a timer: Prometheus already decides how often it wants data, and a
    background loop would either duplicate that schedule or drift from it, serving values whose age
    nobody can determine from the outside.
    """

    def __init__(self, root: Path, node_name: str) -> None:
        self.root = root
        self.node_name = node_name

    def collect(self):  # noqa: C901 -- a flat list of metric definitions; splitting it hides them
        started = time.monotonic()
        targets = discover(self.root)

        def family(name: str, doc: str, unit: str = "") -> GaugeMetricFamily:
            return GaugeMetricFamily(f"kubethrifty_cgroup_{name}", doc, labels=list(LABELS), unit=unit)

        memory_peak = family(
            "memory_peak_bytes",
            "Kernel high-water mark of memory usage (cgroup v2 memory.peak). THE memory sizing "
            "basis: unlike a sampled working-set gauge it cannot miss an allocation burst between "
            "scrapes. Absent when the kernel predates 5.19.",
        )
        memory_current = family("memory_current_bytes", "Current memory usage (memory.current).")
        memory_max = family(
            "memory_limit_bytes",
            "Memory limit (memory.max). NOT EXPORTED when the cgroup reports 'max' (unlimited) -- "
            "an absent series means no limit, whereas 0 would mean a limit of zero bytes.",
        )
        oom_kill = family(
            "oom_kill_total",
            "Cumulative OOM kills (memory.events oom_kill). A COUNTER: the "
            "kube_pod_container_status_last_terminated_reason gauge flaps as pods restart and is "
            "deliberately not used anywhere in this product.",
        )
        nr_periods = family("cpu_periods_total", "CFS periods elapsed (cpu.stat nr_periods).")
        nr_throttled = family("cpu_throttled_periods_total", "CFS periods throttled.")
        throttle_ratio = family(
            "cpu_throttle_ratio",
            "throttled_periods / cfs_periods. Exported pre-divided so nobody compares raw counters "
            "across windows of different length, which is meaningless. Absent when nr_periods is 0 "
            "-- an undefined ratio, not a ratio of zero.",
        )
        headroom = family(
            "memory_headroom_index",
            "1 - (memory.peak / memory.max). 0 means the peak reached the limit. Absent when the "
            "container has no memory limit, because infinite headroom is not a number.",
        )
        cpu_psi_full = family(
            "cpu_pressure_full_avg10",
            "PSI cpu full avg10 (percent). EVERY task stalled. `some` is normal on a busy node; "
            "`full` is what the safety gates use.",
        )
        mem_psi_full = family(
            "memory_pressure_full_avg10", "PSI memory full avg10 (percent)."
        )
        mem_psi_total = family(
            "memory_pressure_full_total_microseconds",
            "Cumulative microseconds with every task stalled on memory. A counter, so rate() over "
            "equal windows is comparable.",
        )

        complete = 0

        for t in targets:
            truth = read_cgroup(t.path)
            labels = [t.pod_uid, t.container_id, t.qos_class, self.node_name]

            # Each of these is guarded. A None is simply not added to its family, so the series does
            # not exist for that container -- which reads downstream as "not observed".
            if truth.memory_peak is not None:
                memory_peak.add_metric(labels, truth.memory_peak)
            if truth.memory_current is not None:
                memory_current.add_metric(labels, truth.memory_current)
            if truth.memory_max is not None:
                memory_max.add_metric(labels, truth.memory_max)
            if truth.oom_kill is not None:
                oom_kill.add_metric(labels, truth.oom_kill)
            if truth.nr_periods is not None:
                nr_periods.add_metric(labels, truth.nr_periods)
            if truth.nr_throttled is not None:
                nr_throttled.add_metric(labels, truth.nr_throttled)

            ratio = truth.throttle_ratio
            if ratio is not None:
                throttle_ratio.add_metric(labels, ratio)

            hr = truth.headroom_index
            if hr is not None:
                headroom.add_metric(labels, hr)

            if truth.cpu_pressure.full_avg10 is not None:
                cpu_psi_full.add_metric(labels, truth.cpu_pressure.full_avg10)
            if truth.memory_pressure.full_avg10 is not None:
                mem_psi_full.add_metric(labels, truth.memory_pressure.full_avg10)
            if truth.memory_pressure.full_total_us is not None:
                mem_psi_total.add_metric(labels, truth.memory_pressure.full_total_us)

            if truth.evidence_complete:
                complete += 1

        SCRAPE_DURATION.set(time.monotonic() - started)
        SCRAPE_TARGETS.set(len(targets))
        SCRAPE_COMPLETE.set(complete)

        yield from (
            memory_peak, memory_current, memory_max, oom_kill,
            nr_periods, nr_throttled, throttle_ratio, headroom,
            cpu_psi_full, mem_psi_full, mem_psi_total,
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kubethrifty-cgroup-collector")
    ap.add_argument("--port", type=int, default=9847)
    ap.add_argument("--cgroup-root", default=str(DEFAULT_CGROUP_ROOT))
    ap.add_argument("--once", action="store_true", help="print one scrape to stdout and exit")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    root = Path(args.cgroup_root)
    # Downward API. Falls back to the hostname, which inside a DaemonSet pod using hostNetwork is
    # the node name anyway.
    node_name = os.environ.get("NODE_NAME") or os.uname().nodename

    collector = CgroupCollector(root, node_name)
    REGISTRY.register(collector)

    if args.once:
        from prometheus_client import generate_latest
        sys.stdout.write(generate_latest(REGISTRY).decode())
        return 0

    # Preflight. A collector that can see nothing should say so loudly at startup rather than
    # serving an empty endpoint that looks like a healthy, quiet node.
    targets = discover(root)
    if not targets:
        log.error(
            "no container cgroups found under %s. The DaemonSet needs /sys/fs/cgroup mounted "
            "read-only, and the node must be cgroup v2 (`stat -fc %%T /sys/fs/cgroup` == "
            "cgroup2fs). Serving anyway so the scrape-health metrics are visible.", root,
        )
    else:
        sample = read_cgroup(targets[0].path)
        if sample.memory_peak is None:
            log.warning(
                "memory.peak is unreadable on this node (kernel < 5.19?). Memory will fall back to "
                "a sampled working-set gauge and every memory recommendation will be stamped "
                "`partial`.",
            )
        if not sample.memory_pressure.observed:
            log.warning(
                "memory PSI is unreadable. Add psi=1 to the kernel command line, or accept the "
                "`partial` evidence tier.",
            )

    start_http_server(args.port)
    log.info("serving cgroup truth on :%d for node %s", args.port, node_name)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
