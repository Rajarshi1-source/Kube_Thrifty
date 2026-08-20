---
name: kubethrifty-cgroup-psi-signals
description: >-
  Build KubeThrifty's evidence layer -- Linux PSI pressure metrics plus cgroup v2 truth -- what
  separates this right-sizer from percentile-only tools. Use for ANY signal collection or
  interpretation: the read-only cgroup-truth DaemonSet (memory.peak, memory.max, memory.events
  oom_kill, cpu.stat, cpu.pressure, memory.pressure), kubelet PSI via /metrics/cadvisor or the
  Summary API, the container_pressure_* family, the throttle RATIO (never raw period counters),
  OOM detection by counter, the Headroom Index, evidence tiers (rehearsed/modelled/partial), and
  degradation when signals are missing. Trigger on PSI, pressure stall, some vs full, cgroup v2,
  memory.peak, memory.events, cpu.stat, throttle ratio, container_pressure, KubeletPSI, or why
  free lies inside a container. MANDATE: memory is sized off the kernel high-water mark, never a
  percentile; ratios compared over equal windows; a missing signal downgrades the evidence tier
  and never raises confidence. Pair with kubethrifty-python-analyser.
---

# PSI + cgroup Truth — KubeThrifty's Evidence Layer

You build the layer that lets KubeThrifty say *"this cut is safe"* with evidence instead of a
prediction. Two facts drive every design choice here:

1. **A percentile cannot tell you whether anyone suffered.** Two containers at 90% of their CPU limit
   look identical in `container_cpu_usage_seconds_total`; one is a batch encoder that doesn't care, the
   other is a checkout API that spent 30% of wall time stalled. Usage metrics cannot separate them.
   **PSI can.**
2. **Container memory numbers lie.** `/proc/meminfo` is not namespaced, so `free` and `top` inside a
   container report the *host*. The truth is in cgroup v2 files. Kubernetes **1.35+ requires cgroup
   v2**, so this is the baseline, not an exotic dependency.

| What you want | Wrong source | Right source |
|---|---|---|
| The container's memory limit | `free -m` total | `memory.max` |
| Current usage | `top` RES | `memory.current` |
| **High-water mark (the memory sizing basis)** | sampled max of scraped usage | **`memory.peak`** (kernel's own, immune to scrape gaps) |
| Was it OOM-killed | flapping pod-status gauge | `memory.events` → `oom_kill` (a counter) |
| Was it throttled | "CPU looks high" | `cpu.stat` → `nr_throttled` / `nr_periods` |
| **Did anyone actually wait** | *no usage metric can answer this* | `cpu.pressure`, `memory.pressure`, `io.pressure` |

## The six-layer signal taxonomy (reason in this order)

```
Reservation  kube_pod_container_resource_requests            what are we PAYING for?
Consumption  container_cpu_usage_seconds_total / working_set what did we USE?
High-water   cgroup memory.peak (or max_over_time fallback)  what was the WORST we saw?   <- memory sizing
Enforcement  cpu.stat nr_throttled / cfs_throttled_periods   did the LIMIT bite?
Suffering    container_pressure_*_stalled_seconds_total       did anyone WAIT?
Death        memory.events oom_kill / container_oom_events    did we KILL it?
```

Right-sizing decisions use all six. Competing tools use the first two — say that out loud in an
interview, it lands.

## PSI facts to have straight

- **`some`** = at least one task stalled (early contention). **`full`** = *all* non-idle tasks stalled
  simultaneously (severe). The kernel exposes rolling `avg10 / avg60 / avg300` plus a nanosecond
  `total` counter.
- Kubernetes surfaces PSI through the kubelet **Summary API** and the kubelet **`/metrics/cadvisor`**
  Prometheus endpoint. **Beta (default-on) in 1.34 via the `KubeletPSI` gate; GA in 1.36** — don't set
  the gate explicitly on 1.36, it emits a "GA feature gate" warning.
- Prerequisites, all of which belong in the README: Linux kernel ≥ 4.20 with `CONFIG_PSI` (some distros
  need `psi=1` on the kernel command line), **cgroup v2**, **containerd 2.x** (PSI is not in the 1.7
  line), Linux nodes only.
- The Prometheus counters are in **seconds** — always differentiate them.
- Node-level PSI is a poor scale-out trigger on its own: a single starved pod can push node `some` to
  ~99% while the node is otherwise idle. **Reason at container/pod level**, which is exactly where
  right-sizing operates.

```promql
# share of wall time fully stalled (0..1) — the core safety signal
sum by (namespace,pod,container) (rate(container_pressure_memory_stalled_seconds_total[15m]))
sum by (namespace,pod,container) (rate(container_pressure_cpu_stalled_seconds_total[15m]))

# 'waiting' is broader than 'stalled' — good early-warning panel
sum by (namespace,pod,container) (rate(container_pressure_cpu_waiting_seconds_total[15m]))

# throttle RATIO — dimensionless, so it survives changes in window length and replica count
sum by (namespace,pod,container) (rate(container_cpu_cfs_throttled_periods_total[15m]))
/
sum by (namespace,pod,container) (rate(container_cpu_cfs_periods_total[15m]))

# OOM by COUNTER, not by the pod-status gauge that resets when the pod is replaced
sum by (namespace,pod,container) (increase(container_oom_events_total[1h]))
```

## Critical rules (never violate)

- **Compare ratios over equal windows, never raw counters.** `nr_throttled` and
  `container_cpu_cfs_throttled_periods_total` scale with window length, replica count and the CFS
  period. Comparing counts across a before/after window produces both false rollbacks and missed
  regressions. Always divide by `nr_periods` / `cfs_periods_total`.
- **Memory is sized off the peak, never a percentile.** A percentile discards the top 5% of samples,
  and for an incompressible resource those are the samples that OOMKill you. Prefer cgroup
  `memory.peak`; fall back to `max_over_time(container_memory_working_set_bytes[...])` and record that
  the peak may be understated between scrapes.
- **A missing signal downgrades the evidence tier — it never raises confidence.** Tiers:
  `rehearsed` (proven on a live pod, see kubethrifty-resize-rehearsal) > `modelled` (percentile +
  forecast) > `partial` (PSI or cgroup unavailable). Stamp the tier on every recommendation and render
  it in the UI; never present modelled evidence as if it were observed.
- **The collector is read-only and tiny.** No host network, non-root, `readOnlyRootFilesystem`, all
  capabilities dropped, no service-account token, one mount: `/sys/fs/cgroup` read-only. It requests
  10m/32Mi and is capped at 100m/64Mi — a cost tool that is itself over-provisioned is a joke, and this
  is the answer to "you want to run a DaemonSet with a hostPath?".
- **Never trust anything that shells out to `free`, `top`, or `/proc/meminfo` for container numbers.**
  Read cgroup files, or read cAdvisor (which reads cgroups for you).

## The collector: shape and parsing

One DaemonSet pod per node walks the pod cgroup tree and exposes `/metrics` for a PodMonitor. The
parsing details that trip people up:

```python
CGROUP_ROOT = Path(os.getenv("CGROUP_ROOT", "/host/sys/fs/cgroup"))
# kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod<UID>.slice/cri-containerd-<ID>.scope
POD_DIR_RE = re.compile(r"kubepods-(?:burstable-|besteffort-)?pod([0-9a-f_]{36})\.slice$")

def _read_int(p: Path) -> int | None:
    raw = p.read_text().strip()            # wrap in try/except for FileNotFound/Permission/OSError
    return None if raw == "max" else int(raw)   # 'max' means unlimited — None, not 0, not sys.maxsize

# cpu.pressure / memory.pressure format:
#   some avg10=0.00 avg60=0.00 avg300=0.00 total=0
#   full avg10=0.00 avg60=0.00 avg300=0.00 total=0
```

- Pod UIDs appear in cgroup paths with `_` where the UID has `-` — normalise before joining to pod
  metadata.
- `memory.max` of `max` means unlimited; represent it as `None` so ratio maths short-circuits instead
  of dividing by a fake number.
- `memory.events` is a key/value file; take `oom_kill` (and `oom` if you want reclaim-pressure colour).
- Emit `kubethrifty_cgroup_memory_peak_bytes`, `kubethrifty_cgroup_oom_kill_total`, and
  `kubethrifty_cgroup_throttle_ratio` — pre-divided, so downstream can't get the ratio wrong.
- Export a scrape-health metric. A silent collector must not look like "no pressure".

## The Headroom Index — one number per container

```python
def headroom_index(*, request, peak, psi_full, throttle_ratio, oom_events, samples,
                   min_samples=168) -> float:
    """1.0 = large unused reservation AND no observed suffering -> cut confidently.
       0.0 = no slack, or evidence of suffering -> do not cut."""
    if oom_events > 0 or samples < min_samples:
        return 0.0                                   # hard stops, not soft penalties
    slack = max(0.0, 1.0 - (peak / request)) if request > 0 else 0.0
    suffering = min(1.0, psi_full / 0.05) * 0.7 + min(1.0, throttle_ratio / 0.05) * 0.3
    return round(max(0.0, slack * (1.0 - suffering)), 3)
```

The case this exists for: **low utilisation, high pressure.** A cache thrashing against its memory
limit has low *average* usage and high `memory.pressure full` — every percentile tool would happily
shrink it into an outage. Headroom Index 0.0, recommendation withheld, and the workload instead appears
in the "needs more resources" list. Shipping *increase* recommendations is what makes a cost tool
credible as an SRE tool.

## Dashboard and eval wiring

- The waste heatmap gets a **second axis**: X = unused reservation, Y = pressure. Top-left (high waste,
  no pressure) is the safe-to-cut list; bottom-right (low waste, high pressure) is the needs-more list.
- Grafana panels: per-namespace PSI stall share, throttle-ratio top 10, `memory.peak / request`
  distribution, collector scrape health.
- The sizing eval harness asserts on **decisions**, not floats. Required cases:
  `low_util_high_pressure` → no reduction; `spiky_peak_gt_p95` → memory request ≥ peak;
  `throttled_at_current_limit` → CPU limit not reduced. These live in the starter
  (`kubethrifty-starter`) and gate CI.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| memory sized from P95/P99 | size from `memory.peak` (or `max_over_time`) × margin |
| comparing `nr_throttled` counts across windows | compare `nr_throttled / nr_periods` ratios |
| OOM detection via `kube_pod_container_status_last_terminated_reason` | `container_oom_events_total` / cgroup `memory.events` counter |
| treating empty `container_pressure_*` as "no pressure" | that is a *missing signal* — tier the recommendation `partial` |
| `memory.max == "max"` parsed as a number | `None`, and skip ratio maths |
| privileged / hostNetwork collector | read-only hostPath mount, non-root, caps dropped, no SA token |
| shelling out to `free`/`top` inside a container | read cgroup files (or cAdvisor) |
| node-level PSI used to decide a pod's sizing | container/pod-level PSI only |
| PSI assumed present | check kernel ≥4.20 + `CONFIG_PSI` + cgroup v2 + containerd 2.x; degrade explicitly |

## Quick reference

- PSI: beta 1.34 (`KubeletPSI`), **GA 1.36**; `container_pressure_{cpu,memory,io}_{stalled,waiting}_seconds_total` via `/metrics/cadvisor`; `some` vs `full`; rate() them.
- cgroup v2 truth: `memory.current` / **`memory.peak`** / `memory.max` / `memory.events.oom_kill` / `cpu.stat` / `*.pressure`.
- Ratios over equal windows; peaks for memory; counters for OOM.
- Evidence tiers: `rehearsed` > `modelled` > `partial`; a missing signal only ever downgrades.
- Collector: DaemonSet, read-only `/sys/fs/cgroup`, non-root, 10m/32Mi requests.
- Sizing decisions → kubethrifty-python-analyser; experiments → kubethrifty-resize-rehearsal; verdicts → kubethrifty-thrift-detective; DaemonSet manifests → kubethrifty-devops-k8s.
