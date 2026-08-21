# Cost Model, Packing, and Coupling — Implementation Reference

Load this when writing or changing the savings model, the packer, the price catalogue, or the autoscaler
coupling guards. `SKILL.md` carries the rules; this file carries the code.

## Contents
1. `packing/binpack.py` — FFD with real overheads
2. `config/instances.json` — the pinned catalogue
3. The three scenario reports (fixed / Karpenter / Auto Mode)
4. `coupling/detect.py` — HPA and KEDA ownership
5. `coupling/simulate.py` — the coupled replica simulation
6. `coupling/qos.py` — QoS-class transition guard
7. What to say when challenged

---

## 1. FFD packing with real overheads

```python
# analyser/src/packing/binpack.py
"""
Cluster-level "how many nodes do we actually need" simulation.

FFD is chosen deliberately: bin-packing is NP-hard, FFD is within 11/9 of optimal in the 1-D case, it
mirrors kube-scheduler closely enough for a cost estimate, and it is DETERMINISTIC — so the savings
figure is reproducible in CI and in a demo. We are bounding a bill, not writing a scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class InstanceType:
    name: str
    cpu_millicores: int
    memory_mib: int
    hourly_inr: float
    max_pods: int = 110

    def allocatable(self) -> tuple[int, int]:
        """Approximates managed-provider reservation curves; exact values are provider-specific and
        pinned in instances.json so the estimate stays reproducible."""
        cpu_reserved = min(self.cpu_millicores * 0.06, 400)
        mem_reserved = min(self.memory_mib * 0.10, 4096) + 100      # + eviction threshold
        return int(self.cpu_millicores - cpu_reserved), int(self.memory_mib - mem_reserved)


@dataclass
class PodRequest:
    name: str
    cpu_millicores: int
    memory_mib: int
    daemonset: bool = False


@dataclass
class Node:
    instance: InstanceType
    cpu_free: int
    mem_free: int
    pods: list = field(default_factory=list)

    def fits(self, p: PodRequest) -> bool:
        return (p.cpu_millicores <= self.cpu_free
                and p.memory_mib <= self.mem_free
                and len(self.pods) < self.instance.max_pods)

    def place(self, p: PodRequest) -> None:
        self.cpu_free -= p.cpu_millicores
        self.mem_free -= p.memory_mib
        self.pods.append(p.name)


def _new_node(inst: InstanceType, daemonsets: list[PodRequest]) -> Node:
    cpu, mem = inst.allocatable()
    n = Node(inst, cpu, mem)
    for ds in daemonsets:                       # DaemonSets tax EVERY node
        if not n.fits(ds):
            raise ValueError(f"{inst.name} cannot host the DaemonSet set")
        n.place(ds)
    return n


def pack(pods: Iterable[PodRequest], inst: InstanceType) -> list[Node]:
    workload = [p for p in pods if not p.daemonset]
    daemonsets = [p for p in pods if p.daemonset]
    cpu_alloc, mem_alloc = inst.allocatable()
    # Sort by the more binding dimension, normalised to allocatable.
    workload.sort(key=lambda p: max(p.cpu_millicores / cpu_alloc, p.memory_mib / mem_alloc),
                  reverse=True)
    nodes: list[Node] = []
    for p in workload:
        for n in nodes:
            if n.fits(p):
                n.place(p)
                break
        else:
            n = _new_node(inst, daemonsets)
            n.place(p)
            nodes.append(n)
    return nodes


@dataclass
class PackingResult:
    instance: str
    nodes: int
    monthly_inr: float
    cpu_utilisation: float           # requests / allocatable
    mem_utilisation: float


def evaluate(pods: list[PodRequest], catalog: list[InstanceType],
             hours_per_month: float = 730.0) -> list[PackingResult]:
    out = []
    for inst in catalog:
        try:
            nodes = pack(pods, inst)
        except ValueError:
            continue                              # instance too small for the DaemonSet set
        cpu_alloc, mem_alloc = inst.allocatable()
        cpu_used = sum(cpu_alloc - n.cpu_free for n in nodes)
        mem_used = sum(mem_alloc - n.mem_free for n in nodes)
        out.append(PackingResult(
            instance=inst.name, nodes=len(nodes),
            monthly_inr=round(len(nodes) * inst.hourly_inr * hours_per_month, 2),
            cpu_utilisation=round(cpu_used / (len(nodes) * cpu_alloc), 3),
            mem_utilisation=round(mem_used / (len(nodes) * mem_alloc), 3),
        ))
    return sorted(out, key=lambda r: r.monthly_inr)


def savings_report(before: list[PodRequest], after: list[PodRequest],
                   catalog: list[InstanceType], surcharge_pct: float = 0.0) -> dict:
    """surcharge_pct: e.g. 12.0 to model EKS Auto Mode's managed-node premium."""
    b, a = evaluate(before, catalog)[0], evaluate(after, catalog)[0]
    mult = 1 + surcharge_pct / 100
    return {
        "before": {"instance": b.instance, "nodes": b.nodes,
                   "monthly_inr": round(b.monthly_inr * mult, 2),
                   "cpu_util": b.cpu_utilisation, "mem_util": b.mem_utilisation},
        "after": {"instance": a.instance, "nodes": a.nodes,
                  "monthly_inr": round(a.monthly_inr * mult, 2),
                  "cpu_util": a.cpu_utilisation, "mem_util": a.mem_utilisation},
        "nodes_removed": b.nodes - a.nodes,
        "monthly_savings_inr": round((b.monthly_inr - a.monthly_inr) * mult, 2),
        "surcharge_pct": surcharge_pct,
        "headline": f"{b.nodes} × {b.instance} → {a.nodes} × {a.instance} (−{b.nodes - a.nodes} nodes)",
    }
```

---

## 2. The pinned catalogue

```json
// config/instances.json — prices as of 2026-08-01, Mumbai (ap-south-1), on-demand.
{
  "as_of": "2026-08-01",
  "currency": "INR",
  "region": "ap-south-1",
  "pricing_model": "on_demand",
  "source": "AWS EC2 on-demand pricing page (record the URL and the date you checked)",
  "instances": [
    {"name": "m5.large",   "cpu_millicores": 2000, "memory_mib": 8192,  "hourly_inr": 8.9,  "max_pods": 29},
    {"name": "m5.xlarge",  "cpu_millicores": 4000, "memory_mib": 16384, "hourly_inr": 17.8, "max_pods": 58},
    {"name": "m5.2xlarge", "cpu_millicores": 8000, "memory_mib": 32768, "hourly_inr": 35.6, "max_pods": 58},
    {"name": "c5.xlarge",  "cpu_millicores": 4000, "memory_mib": 8192,  "hourly_inr": 15.7, "max_pods": 58},
    {"name": "r5.xlarge",  "cpu_millicores": 4000, "memory_mib": 32768, "hourly_inr": 23.4, "max_pods": 58}
  ],
  "scenarios": {
    "self_managed_karpenter": {"surcharge_pct": 0.0},
    "eks_auto_mode": {"surcharge_pct": 12.0,
                      "note": "managed Karpenter; ~12% premium on EC2 on-demand — re-verify before quoting"}
  }
}
```

Treat the numbers as *inputs you dated*, not facts. Re-check the surcharge and the prices before any
interview and bump `as_of`.

---

## 3. The three scenario reports

| Scenario | What the model does | The line that lands |
|---|---|---|
| **Fixed node group** | Pack into the pinned instance type; savings = node delta × price | "Right-sizing removes one m5.xlarge: ₹13,000/month." |
| **Self-managed Karpenter** | Let the packer *choose* the instance shape | "Karpenter would consolidate onto 2 × c5.xlarge instead of 4 × m5.xlarge — but only after requests come down, because it provisions to requests, not usage." |
| **EKS Auto Mode** | Same packing × 1.12 | "Auto Mode is convenient, and it also means un-right-sized requests cost ~12% *more* than the same waste self-managed. Fixing requests is the highest-leverage move *before* paying for managed autoscaling." |

Two traps to be ready for: (1) node autoscalers do not fix requests — they buy capacity for whatever
requests you give them; (2) consolidation requires reschedulable pods, so PDBs and `do-not-disrupt`
annotations bound the achievable saving.

---

## 4. Detecting the coupling

```python
# analyser/src/coupling/detect.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class Autoscaler:
    kind: str                     # "hpa" | "keda" | "vpa"
    name: str
    metric: str                   # "cpu" | "memory" | "external"
    target_utilization: Optional[int]
    min_replicas: int
    max_replicas: int
    current_replicas: int


def find_autoscaler(k8s, ns: str, workload: str) -> Optional[Autoscaler]:
    for hpa in k8s.autoscaling.list_namespaced_horizontal_pod_autoscaler(ns).items:
        if hpa.spec.scale_target_ref.name != workload:
            continue
        # KEDA creates and owns an HPA; report it as KEDA because the advice differs.
        owned_by_keda = any(o.kind == "ScaledObject" for o in (hpa.metadata.owner_references or []))
        kind = "keda" if owned_by_keda else "hpa"
        for m in (hpa.spec.metrics or []):
            if m.type == "Resource" and m.resource.name in ("cpu", "memory"):
                return Autoscaler(kind, hpa.metadata.name, m.resource.name,
                                  m.resource.target.average_utilization,
                                  hpa.spec.min_replicas or 1, hpa.spec.max_replicas,
                                  hpa.status.current_replicas or 1)
        return Autoscaler(kind, hpa.metadata.name, "external", None,
                          hpa.spec.min_replicas or 1, hpa.spec.max_replicas,
                          hpa.status.current_replicas or 1)
    return None
```

An **external**-metric HPA (queue depth, RPS) is *not* coupled to requests — those workloads are safe to
right-size on the CPU/memory axis. Only utilisation-of-request metrics create the paradox. Say that
distinction out loud; it shows you understand the mechanism rather than the headline.

---

## 5. The coupled simulation

```python
# analyser/src/coupling/simulate.py
import math
from dataclasses import dataclass


@dataclass
class CoupledOutcome:
    replicas_before: int
    replicas_after: int
    reservation_before: float
    reservation_after: float
    verdict: str                  # "safe" | "co_change_target" | "refuse"
    suggested_target: int | None
    note: str


def simulate(*, actual_per_pod: float, request_before: float, request_after: float,
             target_pct: int, replicas: int, min_r: int, max_r: int,
             hysteresis: float = 0.10) -> CoupledOutcome:
    def desired(req: float, tgt: int) -> int:
        util = (actual_per_pod / req) * 100 if req else 0
        return max(min_r, min(max_r, math.ceil(replicas * util / tgt)))

    r_before, r_after = desired(request_before, target_pct), desired(request_after, target_pct)
    res_before, res_after = r_before * request_before, r_after * request_after

    if r_after <= r_before and res_after < res_before * (1 - hysteresis):
        return CoupledOutcome(r_before, r_after, res_before, res_after, "safe", None,
                              "no replica growth; reservation falls")

    util_after = (actual_per_pod / request_after) * 100
    suggested = int(min(90, max(30, math.ceil(util_after / (r_before / replicas)))))
    r_with_target = desired(request_after, suggested)

    if r_with_target <= r_before and r_after * request_after < res_before:
        return CoupledOutcome(r_before, r_with_target, res_before, r_with_target * request_after,
                              "co_change_target", suggested,
                              f"request cut requires HPA target {target_pct}% → {suggested}% "
                              f"to hold {r_before} replicas")

    return CoupledOutcome(r_before, r_after, res_before, res_after, "refuse", None,
                          "cut would trigger scale-out that erases or reverses the saving")
```

---

## 6. QoS-class transition guard

```python
# analyser/src/coupling/qos.py
def qos_class(containers: list[dict]) -> str:
    """Guaranteed: every container sets requests == limits for BOTH cpu and memory.
       BestEffort: no requests or limits anywhere. Otherwise Burstable."""
    if all(c.get("requests") == c.get("limits") and c.get("requests") for c in containers):
        return "Guaranteed"
    if not any(c.get("requests") or c.get("limits") for c in containers):
        return "BestEffort"
    return "Burstable"


ALLOW_ANNOTATION = "kubethrifty.io/allow-qos-demotion"


def guard(before: list[dict], after: list[dict], annotations: dict) -> tuple[bool, str]:
    b, a = qos_class(before), qos_class(after)
    if b == a:
        return True, f"QoS unchanged ({b})"
    if b == "Guaranteed" and a != "Guaranteed" and annotations.get(ALLOW_ANNOTATION) != "true":
        return False, (f"blocked: {b} → {a} would change eviction priority under node pressure; "
                       f"set {ALLOW_ANNOTATION}=true to override")
    return True, f"QoS {b} → {a} (allowed)"
```

Guaranteed pods are evicted last under node memory pressure, Burstable next, BestEffort first. A cut that
happens to leave `requests != limits` silently demotes a critical workload's survival odds — which is why
the memory sizer sets `limit == request` by default.

---

## 7. When challenged

- *"Your ₹ number is made up."* → "It's a node delta from a deterministic first-fit-decreasing pack
  against allocatable capacity, with DaemonSet overhead and the pod cap modelled, priced from a catalogue
  pinned in the repo with an as-of date. Re-run it and you get the same number."
- *"Does right-sizing always save money?"* → the HPA paradox, with the arithmetic.
- *"Why not just use Karpenter?"* → "Karpenter provisions to requests. If the requests are 4× too big it
  buys 4× the nodes, correctly. I fix the input; it fixes the capacity. Under Auto Mode the same waste
  even costs about 12% more."
- *"What about spot, savings plans, reserved capacity?"* → "Out of scope by choice — those are commitment
  discounts on the supply side, and stacking them on top of an unfixed request baseline just locks in the
  waste at a discount."
