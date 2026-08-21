---
name: kubethrifty-cost-and-packing
description: >-
  Turn KubeThrifty's per-pod waste into a defensible money number, and stop right-sizing changes
  that cost more than they save. Use for ANY savings-model or autoscaler-coupling work: the waste
  stack (billed node-hours, capacity, allocatable, requests, usage), first-fit-decreasing
  bin-packing with DaemonSet overhead, kube-reserved, the pods-per-node cap, the pinned price
  catalogue with an as-of date, Karpenter and EKS Auto Mode scenarios including the roughly 12
  percent managed surcharge, node-delta reporting, plus the HPA-collision guard that simulates
  replica count when requests shrink (the right-sizing cost paradox) and the QoS-transition guard.
  Trigger on savings, cost model, bin packing, allocatable, node delta, consolidation, Karpenter,
  Auto Mode, pricing, HPA, averageUtilization, scale-out, or QoS class. MANDATE: savings are only
  ever a node-count delta; prices are pinned with a date; a cut that triggers scale-out is
  co-changed or refused. Pair with kubethrifty-python-analyser.
---

# Cost Model, Bin-Packing, and Autoscaler Coupling

Two jobs, and both exist because the naive version of this project gets them wrong.

**Job 1 — make the money number true.** Per-pod waste × unit price is not savings. Clouds bill
node-hours. Trimming 3 CPU of requests across five pods saves ₹0 until a node disappears.

**Job 2 — make sure the cut actually saves anything.** The HPA scales on CPU utilisation *as a percentage
of requests*. Halve the requests and the same traffic reads as double the utilisation, so the HPA scales
out — and more replicas means more per-replica overhead. Reservation falls while cost rises.

## The waste stack — recite it in this order

```
BILLED for        : node-hours (or vCPU-hours under Auto Mode / Autopilot)
node CAPACITY     : what the instance physically has
node ALLOCATABLE  : capacity − kube-reserved − system-reserved − eviction threshold
SCHEDULER packs   : Σ pod REQUESTS (+ every DaemonSet, on every node)
app USES          : actual consumption
```

Cost optimisation is the business of shrinking the requests-to-usage gap **until a node disappears**.
Karpenter and Cluster Autoscaler work the top half (supply); KubeThrifty works the bottom half (demand),
which is upstream. Feed a node autoscaler 4× over-provisioned requests and it will faithfully buy 4× the
nodes — that is the whole argument for why this project is complementary to Karpenter, not competing.

## Critical rules (never violate)

- **`waste` and `savings` are different words.** `waste = Σ(request − peak)` is an efficiency metric and
  goes on the dashboard as a percentage. `savings = (nodes_before − nodes_after) × node_price` is the
  only thing allowed to carry a currency symbol, in the README, the PR body, and the interview.
- **Pin prices in-repo with an `as_of` date and a region.** A live price feed makes numbers
  irreproducible; a dated catalogue makes every figure checkable. Say "prices pinned as of <date>,
  Mumbai on-demand, catalogue in `config/instances.json`" — that sentence sounds like a FinOps engineer.
- **Model overheads or the packing is fiction.** Allocatable ≠ capacity; DaemonSets land on *every*
  node; there is a pods-per-node cap (commonly 110, lower on small instances with ENI limits).
- **FFD, deterministically.** Bin-packing is NP-hard; first-fit-decreasing is within 11/9 of optimal in
  the 1-D case, mirrors scheduler behaviour closely enough for a bill estimate, and — critically — is
  deterministic, so the number is reproducible in CI. You are bounding a bill, not writing a scheduler.
- **Never ship a request cut that fights an autoscaler.** Detect HPA/KEDA ownership, simulate the coupled
  replica count, then either put the resource change **and** the re-derived HPA target in *one* PR, or
  refuse and show the owner the arithmetic. Shipping them in separate PRs guarantees a bad intermediate
  state.
- **Never demote QoS silently.** Compute the QoS class before and after. Block
  **Guaranteed → Burstable** unless the workload explicitly opts in: that transition changes eviction
  priority under node pressure, and no competing dashboard tells you it happened.
- **Report achievable, not arithmetic, savings.** Consolidation needs reschedulable pods. Flag workloads
  whose PDB, `do-not-disrupt` annotation, or topology constraints block consolidation, and subtract them
  from the claim.

## The HPA cost paradox — the worked example to memorise

```
utilisation% = actual_cpu / requested_cpu × 100
desired_replicas ≈ ceil(current_replicas × utilisation% / target%)

3 replicas, request 1000m, actual 200m each, HPA target 70%
  BEFORE : util 20%  -> 3 replicas, reservation 3000m
  cut to 250m : util 80% -> ceil(3 × 80/70) = 4 replicas, reservation 1000m      (still a win)
  cut to 220m with target 60% : util 91% -> ceil(3 × 91/60) = 5 replicas
        reservation 1100m, and 5 × 200m = 1000m of ACTUAL cpu where 3 pods used 600m,
        because every replica carries fixed overhead (JVM heap, sidecar, pools).
```

Reservation went down; pod count, real consumption, and possibly node count went up. Memory is worse:
replica count multiplies the per-pod memory floor, so a memory cut plus a scale-out can raise total
memory reservation outright.

Policy: `safe` → normal PR · `co_change_target` → one PR with both changes · `refuse` → no
recommendation, workload listed under "coupled — needs owner decision" with the arithmetic shown.

## Reporting

- **Dashboard cluster tab:** the waste stack as a funnel, the packing simulation beneath it, and a
  scenario selector (fixed node group · self-managed Karpenter · EKS Auto Mode).
- **README headline:** *"The demo requests 5.5 CPU / 6.5 GB and peaks at 1.7 / 2.6. Right-sized it
  bin-packs from 4 nodes onto 1 — ₹X/month at pinned Mumbai on-demand rates (catalogue dated, in-repo),
  or ₹Y under EKS Auto Mode's ≈12% premium."*
- **Every PR:** the node-delta line, plus the HPA interaction section when the workload is autoscaled.
  Never a bare per-pod rupee figure.

## Implementation

`references/cost-scenarios.md` carries the complete packing module, the instance catalogue schema, the
coupling detector and simulator, the QoS guard, and the three scenario reports. Read it before writing or
changing any of that code.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| `savings = wasted_millicores × price` | node-delta from the packer; per-pod waste is a percentage, not money |
| live price API call in the analyser | pinned `config/instances.json` with `as_of`, region, pricing model |
| packing against node *capacity* | pack against `allocatable()` (reserved + eviction threshold subtracted) |
| DaemonSets ignored in the simulation | seed every new node with the DaemonSet set before placing workload pods |
| no pods-per-node cap | enforce `max_pods` per instance type; a 29-pod node is not a 110-pod node |
| recommending a request cut on an HPA-managed workload without simulating replicas | run the coupled simulation; co-change the target or refuse |
| resource change and HPA target change in separate PRs | one PR — the intermediate state is the outage |
| ignoring the QoS class change | compute before/after; block Guaranteed → Burstable by default |
| quoting Auto Mode savings without the surcharge | model the ≈12% premium as a scenario multiplier and re-verify the current figure before quoting it |
| claiming consolidation savings a PDB forbids | flag blocked workloads and subtract them |

## Quick reference

- Waste stack: billed → capacity → allocatable → requests → usage. KubeThrifty owns the demand side.
- FFD packing with DaemonSet overhead, `allocatable()`, `max_pods`; deterministic, so CI-reproducible.
- Prices pinned in-repo (`as_of`, region, model); scenarios: fixed node group · Karpenter · Auto Mode (+≈12%).
- Savings = node delta only. Waste ≠ savings.
- Coupling: detect HPA/KEDA → simulate replicas → `safe` | `co_change_target` | `refuse`; guard QoS transitions.
- Code depth → `references/cost-scenarios.md`; sizing → kubethrifty-python-analyser; charts/funnel → kubethrifty-charts.
