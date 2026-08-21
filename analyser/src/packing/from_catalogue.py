#!/usr/bin/env python3
"""
from_catalogue.py -- bridge the pinned price catalogue to the bin-packer.

Turns `config/instances.json` into `InstanceType` objects and a set of recommendations into the
before/after `PodSpec` lists the packer needs.

The interesting work is `pods_from_recommendations`. Recommendations arrive one per
(container, resource), but the packer places PODS -- so CPU and memory have to be recombined per
workload, and a workload with a CPU recommendation but no memory recommendation must keep its
CURRENT memory request in both the before and after packing. Dropping it would model a pod that asks
for no memory at all, which packs beautifully and is a fantasy.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .binpack import InstanceType, PodSpec

log = logging.getLogger(__name__)


def load_instances(path: str | Path) -> tuple[dict[str, InstanceType], dict]:
    """Return ({type_name: InstanceType}, metadata)."""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))

    instances: dict[str, InstanceType] = {}
    for entry in raw.get("instances", []):
        instances[entry["type"]] = InstanceType(
            name=entry["type"],
            cpu_cores=entry["cpu_millicores"] / 1000.0,
            memory_mib=float(entry["memory_mib"]),
            hourly_price=float(entry["on_demand_hourly"]),
            # Real ENI-derived ceilings, not the 110 default: a node can be far from its CPU limit
            # and still be unable to take another pod.
            max_pods=int(entry.get("max_pods", 110)),
        )

    metadata = {
        "as_of": raw.get("as_of", "unknown"),
        "currency": raw.get("currency", "USD"),
        "region": raw.get("region", "unknown"),
        "scenarios": raw.get("scenarios", {}),
        "daemonset_overhead": raw.get("daemonset_overhead", {}),
    }
    return instances, metadata


def daemonset_pods(metadata: dict) -> list[PodSpec]:
    """
    The DaemonSet overhead that lands on EVERY node.

    Seeded onto each node before any workload pod, because in reality it is not optional: a node runs
    the CNI, kube-proxy, a log shipper, node-exporter and KubeThrifty's own collector from the moment
    it joins.
    """
    ds = metadata.get("daemonset_overhead") or {}
    cpu_millicores = ds.get("cpu_millicores")
    memory_mib = ds.get("memory_mib")

    if cpu_millicores is None or memory_mib is None:
        # Zero overhead would model nodes that are emptier than any real node, under-counting nodes
        # and thereby OVERSTATING the saving. Warn loudly rather than silently flatter the number.
        log.warning(
            "no daemonset_overhead in the catalogue; packing will under-count nodes and overstate "
            "the saving"
        )
        return []

    return [
        PodSpec(
            namespace="kube-system",
            workload="daemonset-overhead",
            cpu_cores=float(cpu_millicores) / 1000.0,
            memory_mib=float(memory_mib),
        )
    ]


def pods_from_recommendations(
    recommendations,
    *,
    replicas: dict[str, int] | None = None,
) -> tuple[list[PodSpec], list[PodSpec]]:
    """
    Build (before_pods, after_pods) for the packer.

    Recommendations are per (container, resource); the packer needs whole pods. So:

      1. Group by workload, collecting current and recommended CPU and memory.
      2. A workload with a recommendation for only ONE resource keeps its CURRENT value for the
         other -- in BOTH lists. Omitting it would model a pod requesting no memory, which packs
         wonderfully and is fiction.
      3. Expand by replica count: replicas need not share a node, so each is placed separately.
    """
    replicas = replicas or {}

    grouped: dict[str, dict[str, float | None]] = {}

    for r in recommendations:
        key = f"{r.namespace}/{r.workload}"
        entry = grouped.setdefault(
            key,
            {
                "namespace": r.namespace,
                "workload": r.workload,
                "cpu_before": None, "cpu_after": None,
                "mem_before": None, "mem_after": None,
            },
        )
        current = getattr(r, "current_request", None)
        proposed = getattr(r, "recommended_request", None)

        if r.resource == "cpu":
            entry["cpu_before"] = current
            entry["cpu_after"] = proposed if proposed is not None else current
        else:
            entry["mem_before"] = current
            entry["mem_after"] = proposed if proposed is not None else current

    before: list[PodSpec] = []
    after: list[PodSpec] = []
    skipped: list[str] = []

    for key, e in grouped.items():
        cpu_before, mem_before = e["cpu_before"], e["mem_before"]

        # A pod with no declared request for a resource cannot be packed on that axis. Rather than
        # invent a value, the workload is skipped from BOTH sides so the delta stays honest.
        if cpu_before is None or mem_before is None:
            skipped.append(key)
            continue

        cpu_after = e["cpu_after"] if e["cpu_after"] is not None else cpu_before
        mem_after = e["mem_after"] if e["mem_after"] is not None else mem_before

        count = max(1, replicas.get(key, 1))
        for i in range(count):
            before.append(PodSpec(str(e["namespace"]), f"{e['workload']}-{i}",
                                  float(cpu_before), float(mem_before)))
            after.append(PodSpec(str(e["namespace"]), f"{e['workload']}-{i}",
                                 float(cpu_after), float(mem_after)))

    if skipped:
        log.info(
            "%d workload(s) excluded from packing (no declared request for one resource): %s",
            len(skipped), ", ".join(sorted(skipped)),
        )

    return before, after


def surcharge_for(scenario: str, metadata: dict) -> float:
    """
    The management surcharge for a pricing scenario, as a percentage.

    EKS Auto Mode charges roughly 12% on top of every node-hour, so the same node delta is worth
    measurably less there than under self-managed Karpenter. Reading it from the catalogue keeps the
    figure pinned with a date rather than hard-coded in the maths.
    """
    scenarios = metadata.get("scenarios") or {}
    entry = scenarios.get(scenario) or {}
    if isinstance(entry, dict):
        return float(entry.get("surcharge_pct", 0.0) or 0.0)
    return 0.0
