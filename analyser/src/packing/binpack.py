#!/usr/bin/env python3
"""
binpack.py -- turn per-pod waste into a node-count delta.

THIS MODULE PRODUCES THE ONLY NUMBER IN KUBETHRIFTY THAT CARRIES A CURRENCY SYMBOL.

    savings = (nodes_before - nodes_after) x node_price

Everything else -- every waste percentage, every reclaimed millicore -- is a ratio. Clouds bill per
NODE, so trimming 400m across thirty pods saves exactly nothing until a node actually disappears.
Summing per-pod millicores into a monthly figure is the standard lie in this product category, and
it is why nobody trusts these tools.

Three corrections that separate a real node count from a naive one. Omitting any of them
under-counts nodes, which OVERSTATES the saving:

  ALLOCATABLE, not capacity   The kubelet and the OS reserve a slice of every node. Packing against
                              capacity assumes resource that no pod can ever be scheduled onto.
                                cpu_reserved = min(cpu * 0.06, 400m)
                                mem_reserved = min(mem * 0.10, 4096Mi) + 100Mi eviction threshold
  DAEMONSETS FIRST            Every new node immediately runs the full DaemonSet set -- CNI, kube-proxy,
                              log shipper, node exporter, and KubeThrifty's own collector. That
                              overhead is seeded onto each node BEFORE any workload pod, because in
                              reality it is not optional.
  max_pods                    A node has a hard pod-count ceiling (110 by default, and far lower on
                              small AWS instance types because of ENI limits). A node can be nowhere
                              near its CPU or memory limit and still be full.

First-Fit-Decreasing: sort pods largest-first, place each on the first node that fits. Not optimal --
bin packing is NP-hard -- but FFD is provably within 11/9 of optimal, deterministic, and explainable,
and an explainable node count is worth more here than an optimal one nobody can audit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# kube-reserved + system-reserved, as the standard cloud providers compute them.
CPU_RESERVE_FRACTION = 0.06
CPU_RESERVE_CAP_MILLICORES = 400.0
MEM_RESERVE_FRACTION = 0.10
MEM_RESERVE_CAP_MIB = 4096.0
# The kubelet's default hard eviction threshold. Unusable by pods, so it is not allocatable.
EVICTION_THRESHOLD_MIB = 100.0

DEFAULT_MAX_PODS = 110
HOURS_PER_MONTH = 730


@dataclass(frozen=True)
class InstanceType:
    name: str
    cpu_cores: float
    memory_mib: float
    hourly_price: float
    # ENI-derived on AWS, and much lower than 110 on small types. A t3.small caps at 11 pods, so a
    # node can be almost empty on CPU and still unable to take another pod.
    max_pods: int = DEFAULT_MAX_PODS


@dataclass(frozen=True)
class PodSpec:
    """One pod to place. `replicas` is expanded by the caller -- each replica is placed separately,
    since replicas do not have to share a node."""

    namespace: str
    workload: str
    cpu_cores: float
    memory_mib: float

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.workload}"


@dataclass
class Node:
    instance: InstanceType
    allocatable_cpu: float
    allocatable_memory: float
    used_cpu: float = 0.0
    used_memory: float = 0.0
    pods: list[PodSpec] = field(default_factory=list)

    def fits(self, pod: PodSpec) -> bool:
        """All three constraints, and max_pods is the one people forget."""
        return (
            self.used_cpu + pod.cpu_cores <= self.allocatable_cpu
            and self.used_memory + pod.memory_mib <= self.allocatable_memory
            and len(self.pods) < self.instance.max_pods
        )

    def place(self, pod: PodSpec) -> None:
        self.used_cpu += pod.cpu_cores
        self.used_memory += pod.memory_mib
        self.pods.append(pod)


def allocatable(instance: InstanceType) -> tuple[float, float]:
    """
    Compute what a pod can actually be scheduled onto.

    Returns (cpu_cores, memory_mib). Both reservations are `min(fraction, cap)`: the fraction
    dominates on small nodes, the cap on large ones -- a 96-core node does not reserve 5.76 cores.
    """
    cpu_millicores = instance.cpu_cores * 1000.0
    cpu_reserved = min(cpu_millicores * CPU_RESERVE_FRACTION, CPU_RESERVE_CAP_MILLICORES)
    mem_reserved = min(instance.memory_mib * MEM_RESERVE_FRACTION, MEM_RESERVE_CAP_MIB)

    return (
        (cpu_millicores - cpu_reserved) / 1000.0,
        instance.memory_mib - mem_reserved - EVICTION_THRESHOLD_MIB,
    )


@dataclass(frozen=True)
class PackingResult:
    nodes: int
    instance_type: str
    total_cpu_requested: float
    total_memory_requested: float
    cpu_utilisation: float
    memory_utilisation: float
    pods_placed: int
    unplaceable: tuple[str, ...] = ()

    @property
    def bin_limited_by(self) -> str:
        """Which constraint actually decided the node count.

        Worth surfacing: a memory-bound cluster gains nothing from trimming CPU, and telling an
        operator that is more useful than a number they cannot act on.
        """
        if self.cpu_utilisation >= self.memory_utilisation:
            return "cpu"
        return "memory"


def pack(
    pods: list[PodSpec],
    instance: InstanceType,
    daemonset_overhead: list[PodSpec] | None = None,
) -> PackingResult:
    """
    First-Fit-Decreasing pack.

    Sorting is by the DOMINANT resource fraction -- each pod's larger share of its node's
    allocatable CPU or memory -- rather than by raw CPU or raw MiB. Sorting by raw units would order
    a 4 GiB / 100m pod behind a 200m / 256 MiB one on a memory-bound node, which is exactly backwards
    for packing quality.
    """
    alloc_cpu, alloc_mem = allocatable(instance)
    daemonset_overhead = daemonset_overhead or []

    ds_cpu = sum(d.cpu_cores for d in daemonset_overhead)
    ds_mem = sum(d.memory_mib for d in daemonset_overhead)
    ds_count = len(daemonset_overhead)

    if ds_cpu >= alloc_cpu or ds_mem >= alloc_mem:
        raise ValueError(
            f"DaemonSet overhead ({ds_cpu:.2f} cores / {ds_mem:.0f} MiB) does not fit in "
            f"{instance.name}'s allocatable ({alloc_cpu:.2f} cores / {alloc_mem:.0f} MiB). No "
            f"workload pod could ever be scheduled."
        )

    def dominant(p: PodSpec) -> float:
        return max(p.cpu_cores / alloc_cpu, p.memory_mib / alloc_mem)

    ordered = sorted(pods, key=dominant, reverse=True)

    nodes: list[Node] = []
    unplaceable: list[str] = []

    def new_node() -> Node:
        node = Node(instance, alloc_cpu, alloc_mem)
        # DaemonSets FIRST. Every node runs them the moment it joins, so their cost is not optional
        # and must be charged before any workload pod is considered.
        for ds in daemonset_overhead:
            node.place(ds)
        nodes.append(node)
        return node

    for pod in ordered:
        # A pod larger than a whole node's allocatable-minus-DaemonSets can never be placed. Adding
        # nodes for it would loop forever, and silently dropping it would understate the node count.
        if pod.cpu_cores > alloc_cpu - ds_cpu or pod.memory_mib > alloc_mem - ds_mem:
            unplaceable.append(
                f"{pod.key} ({pod.cpu_cores:.2f} cores / {pod.memory_mib:.0f} MiB) exceeds "
                f"{instance.name}'s usable space after DaemonSet overhead"
            )
            continue

        for node in nodes:
            if node.fits(pod):
                node.place(pod)
                break
        else:
            new_node().place(pod)

    total_cpu = sum(n.used_cpu for n in nodes)
    total_mem = sum(n.used_memory for n in nodes)
    capacity_cpu = len(nodes) * alloc_cpu
    capacity_mem = len(nodes) * alloc_mem

    return PackingResult(
        nodes=len(nodes),
        instance_type=instance.name,
        total_cpu_requested=total_cpu,
        total_memory_requested=total_mem,
        cpu_utilisation=(total_cpu / capacity_cpu) if capacity_cpu else 0.0,
        memory_utilisation=(total_mem / capacity_mem) if capacity_mem else 0.0,
        # DaemonSet pods are placed but are not workload placements.
        pods_placed=sum(len(n.pods) for n in nodes) - (ds_count * len(nodes)),
        unplaceable=tuple(unplaceable),
    )


@dataclass(frozen=True)
class SavingsReport:
    nodes_before: int
    nodes_after: int
    instance_type: str
    hourly_price: float
    currency: str
    as_of: str
    region: str
    monthly_saving: float | None
    scenario: str
    before: PackingResult
    after: PackingResult
    note: str

    @property
    def nodes_removed(self) -> int:
        return self.nodes_before - self.nodes_after


def savings_report(
    before_pods: list[PodSpec],
    after_pods: list[PodSpec],
    instance: InstanceType,
    *,
    daemonset_overhead: list[PodSpec] | None = None,
    scenario: str = "fixed_node_pool",
    surcharge_pct: float = 0.0,
    currency: str = "USD",
    as_of: str = "unknown",
    region: str = "unknown",
) -> SavingsReport:
    """
    Pack twice -- current requests, then recommended -- and price the difference.

    The headline is `nodes_removed`. When it is 0, `monthly_saving` is 0.0 and the note says so
    plainly: the reduction is real but does not remove a node, so it is not yet money. Reporting a
    per-pod figure there would be inventing a saving the cloud bill will never show.
    """
    before = pack(before_pods, instance, daemonset_overhead)
    after = pack(after_pods, instance, daemonset_overhead)

    removed = before.nodes - after.nodes

    # The surcharge applies per node-hour, so it scales with the nodes you KEEP -- which means it
    # also scales the value of each node removed.
    effective_hourly = instance.hourly_price * (1.0 + surcharge_pct / 100.0)

    if removed <= 0:
        monthly: float | None = 0.0
        note = (
            f"Right-sizing reduces requests but removes no node: {before.nodes} node(s) before and "
            f"after. The cluster is {after.bin_limited_by}-bound, so there is no saving to report "
            f"yet even though per-pod waste falls. This is the honest answer, not a failure."
        )
    else:
        monthly = removed * effective_hourly * HOURS_PER_MONTH
        note = (
            f"{removed} node(s) of {instance.name} become removable "
            f"({before.nodes} -> {after.nodes}). Priced at {effective_hourly:.4f} {currency}/hour "
            f"x {HOURS_PER_MONTH} hours, as of {as_of} for {region}. The cluster is "
            f"{after.bin_limited_by}-bound after the change."
        )

    if scenario == "self_managed_karpenter" and removed > 0:
        note += (
            " Under Karpenter, freed capacity is consolidated automatically, so this figure depends "
            "on the consolidation policy actually being enabled."
        )
    if scenario == "eks_auto_mode" and removed > 0:
        note += (
            f" EKS Auto Mode adds a {surcharge_pct:.0f}% management surcharge to every node-hour, "
            f"which is already included above."
        )

    return SavingsReport(
        nodes_before=before.nodes,
        nodes_after=after.nodes,
        instance_type=instance.name,
        hourly_price=effective_hourly,
        currency=currency,
        as_of=as_of,
        region=region,
        monthly_saving=monthly,
        scenario=scenario,
        before=before,
        after=after,
        note=note,
    )
