---
name: kubethrifty-devops-k8s
description: >-
  Build and operate the DevOps and Kubernetes layer of KubeThrifty (pod right-sizing advisor) on
  Kubernetes 1.36 with 1.35 as the floor, Helm 4, and Node.js 24 LTS. Use for ANY infra work: the
  kind cluster and its cgroup v2 plus containerd 2.x prerequisites, Helm charts for the platform
  and the over-provisioned sample app, kube-prometheus-stack with the kubelet cadvisor scrape that
  PSI needs, the cgroup-truth DaemonSet, the rehearsal watchdog CronJob, minimal RBAC whose only
  mutating verb is patch on pods/resize, Docker images, GitHub Actions CI/CD with the eval gates,
  KEDA scale-to-zero, TimescaleDB, Valkey, Ingress, Secrets, QoS, HPA, and the self-referential
  demo. Trigger on Kubernetes, Helm 4, kind, KEDA, kube-prometheus-stack, cAdvisor,
  kube-state-metrics, CronJob, DaemonSet, GitHub Actions, Dockerfile, GitOps, feature gate, cgroup
  v2, or containerd. MANDATE: K8s 1.36 demo, 1.35 floor; Helm 4.2 (Helm 3 lost bug fixes in July
  2026); pin EVERY image and chart, never latest, including docker-compose.
---

# DevOps & Kubernetes — KubeThrifty Platform (Rev 3 pins)

You build the infrastructure for a tool whose entire thesis is *"stop paying for resources you don't
use."* The platform runs **on** K8s, **for** K8s, and right-sizes **its own pods** — so every pin,
request, and limit in this repo is itself part of the argument. An interviewer will open your compose file
and your `values.yaml` before they read your README.

## Version mandate (state it confidently — it is an interview question)

| Component | Pin | Why this, not the alternative |
|---|---|---|
| **Kubernetes** | **1.36** for the demo (`kindest/node:v1.36.x`); **1.35 is the supported floor** | 1.34 entered maintenance 27 Aug 2026 (EOL 27 Oct 2026). 1.35 runs to 28 Feb 2027; 1.36 has the longest runway and is where **PSI metrics are GA** and **pod-level in-place resize** is beta. **In-place pod resize is GA in 1.35** — the on-theme feature for a right-sizer, and the basis of the Resize Rehearsal |
| **cgroup** | **v2, mandatory** | Kubernetes **1.35+ requires cgroup v2**. Two features read cgroup data, so this is a README prerequisite, not a detail |
| **Container runtime** | **containerd 2.x** | PSI support landed in containerd 2.0 and is **not** in the 1.7 line — without it `container_pressure_*` series are empty |
| **Helm** | **4.2.x** (pin in CI via `azure/setup-helm`) | **Helm 3 bug fixes ended 8 July 2026**; security-only to 10 Feb 2027. Helm 4 GA'd Nov 2025. Migration notes in `references/platform-prereqs.md` |
| **KEDA** | **2.20** (~May 2026), chart version pinned | KEDA ships every 4 months and supports ~2 cycles; 2.17 is long out of support. Still the `redis-streams` scale-to-zero story |
| **kube-prometheus-stack** | chart pinned (~88.x), Prometheus 3.x, installable from the OCI artefact | The chart is the reproducibility boundary. **Enable the kubelet `/metrics/cadvisor` scrape** or PSI never arrives |
| **Grafana** | **13.0.x**, dashboards provisioned as JSON in-repo | `latest` breaks dashboards on upgrade |
| **TimescaleDB** | **`timescale/timescaledb-ha:pg18.4-ts2.28.1-all`** | The `-ha` image bundles the **Toolkit** that `percentile_agg`/`approx_percentile` need; the plain image fails the migration silently (a real bug caught in Rev 2). Vendor is now branded TigerData; image path unchanged |
| **Cache / queue** | **Valkey 9.1** (default) or **Redis 8.x** | Valkey is BSD-3 and a drop-in; pin the minor either way and be ready to explain the Redis licence change |
| **Node.js** | **24 LTS (Krypton)** | Active LTS, EOL Apr 2028. Node 26 is *Current*, not LTS until Oct 2026 — don't build on Current |
| **Python** | **3.14** (`python:3.14-slim`, non-root) | Current stable; cp314 wheels for pandas/numpy |

### Feature gates the platform depends on

| Capability | Status (Aug 2026) | Why we care |
|---|---|---|
| `InPlacePodVerticalScaling` | **GA 1.35** (beta 1.33) — mutate via the `resize` subresource | The Resize Rehearsal. Setting the gate explicitly on 1.36 warns "GA feature gate" — don't |
| `PodLevelResources` | **Beta since 1.34**, default-on | Sidecar-heavy pods share one budget |
| `InPlacePodLevelResourcesVerticalScaling` | **Beta in 1.36**, default-on | Resize the shared pod budget live — gate behind a Helm value |
| `KubeletPSI` (KEP-4205) | **Beta 1.34 → GA 1.36** | `container_pressure_*` metrics; needs kernel ≥4.20 + `CONFIG_PSI` + cgroup v2 + containerd 2.x |
| DRA (`resource.k8s.io/v1`) | **GA 1.34** | Device claims are not millicore-sizable — detect and refuse CPU cuts on claimed-device pods |

> Soundbite: *"Every image is pinned and every chart is version-pinned, with the option to pin charts by
> OCI digest. Between revisions two pins went stale — Helm 3 lost bug-fix support in July 2026 and
> Kubernetes 1.34 was about to enter maintenance — so I moved to Helm 4.2 and 1.36 with 1.35 as the floor.
> I also learned 1.35 mandates cgroup v2 and PSI needs containerd 2.x, which matters because two of my
> features read cgroup and PSI data. Those aren't nice-to-haves, they're prerequisites in my README."*

## Critical rules (never violate)

- **No `latest`. Anywhere. Including `docker-compose.yml`.** This is the project's whole thesis, and the
  compose file is where people slip. CI tags by commit SHA; charts pin versions; Renovate proposes bumps.
- **The sample app is *intentionally* over-provisioned** (5.5 CPU / 6.5 GB requested versus ~1.7 CPU /
  2.6 GB real peak). It is the demo input — do not "fix" it; KubeThrifty fixes it.
- **KubeThrifty's own pods are right-sized by KubeThrifty**, which opens a PR against its own Helm
  `values.yaml`. Preserve that loop; it is the killer demo. Corollary: the cgroup-truth DaemonSet requests
  10m/32Mi and caps at 100m/64Mi — a cost tool that is itself bloated is a joke.
- **The only mutating cluster permission is `patch` on `pods/resize`.** No `delete pods`, no Deployment
  writes, no Secret reads. Right-sizing reaches the cluster through Git, never `kubectl apply`. This is the
  answer to every security question about the project.
- **The rehearsal watchdog is a separate CronJob**, never a thread inside the analyser: a compensation
  owned by the process that can crash is not a compensation.
- **Resource changes roll out gradually.** Target workloads use `RollingUpdate` with `maxUnavailable: 0`,
  `maxSurge: 1`, so a bad cut crash-loops and the rollout halts itself. Never auto-merge a right-sizing PR.
- **Stateful versus stateless is deliberate:** dashboard = Deployment (2 replicas + HPA + PDB); analyser =
  Deployment at `minReplicas: 0` driven by KEDA; TimescaleDB = StatefulSet + PVC; Valkey/Redis =
  Deployment + small PVC; cgroup-truth = DaemonSet (read-only hostPath); watchdog = CronJob.

## The metrics stack (know what each piece provides)

```
cAdvisor (kubelet)      -> container_cpu_usage_seconds_total (COUNTER), container_memory_working_set_bytes,
                           container_cpu_cfs_{periods,throttled_periods}_total, container_oom_events_total,
                           container_pressure_* (PSI, via /metrics/cadvisor)
kube-state-metrics      -> kube_pod_container_resource_requests / _limits   (the "requested" side)
cgroup-truth DaemonSet  -> memory.peak, memory.events oom_kill, cpu.stat, *.pressure  (kernel truth)
Prometheus (15s scrape) -> stores all of it, serves PromQL over a 14-day window
```

`rate(container_cpu_usage_seconds_total[5m])` turns the CPU counter into cores; working set is already a
gauge; PSI counters are seconds and must be `rate()`d. Install the stack with one pinned chart and confirm
PSI actually flows before building anything on it:

```bash
kubectl get --raw "/api/v1/nodes/$NODE/proxy/metrics/cadvisor" | grep container_pressure | head
```

If that returns nothing, fix the prerequisites (`references/platform-prereqs.md`) — do not build features
on an empty metric.

## Chart layout

```
charts/kubethrifty/
  Chart.yaml                       # appVersion pinned; dependencies pinned
  values.yaml                      # images, analysisWindow, margins, prometheus.url, github.*, rehearsal.*
  templates/
    dashboard-deployment.yaml      # 2 replicas, readiness /api/health
    dashboard-hpa.yaml  pdb.yaml
    analyser-deployment.yaml       # minReplicas 0 (KEDA-managed), stateless
    analyser-scaledobject.yaml     # KEDA redis-streams -> scale to zero
    analyser-enqueuer-cronjob.yaml # XADD a job every 6h
    rehearsal-watchdog-cronjob.yaml# reverts any rehearsal past its deadline (every 2 min)
    daemonset-cgroup-truth.yaml    # read-only /sys/fs/cgroup, non-root, caps dropped
    timescaledb-statefulset.yaml   # PVC, timescaledb-ha:pg18.x
    valkey-deployment.yaml
    rbac.yaml                      # pods/resize patch + reads; nothing else mutating
    configmap.yaml secrets.yaml service.yaml ingress.yaml serviceaccount.yaml podmonitor.yaml
charts/sample-app/                 # 5 deliberately over-provisioned services
```

Helm 4 specifics that will bite you: **server-side apply is the default for new installs** (expect
explicit conflict errors instead of silent overwrites — that is what you want), `--wait` now uses kstatus
and needs the **`watch`** verb on all chart resources, and `--post-renderer <exe>` is gone (package it as
a plugin with `plugin.yaml`). Chart `apiVersion: v2` charts run unchanged; chart format v3 is experimental
— do not use it. Rollback is still `helm rollback kubethrifty <REV>`; migrations follow expand-contract.

## KEDA — scale the analyser to zero

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata: {name: kubethrifty-analyser, namespace: kubethrifty}
spec:
  scaleTargetRef: {name: kubethrifty-analyser}
  minReplicaCount: 0            # scale-to-zero is itself a FinOps win
  maxReplicaCount: 3
  cooldownPeriod: 120           # avoid flapping; alert if >0 replicas while the queue is empty
  triggers:
    - type: redis-streams
      metadata:
        address: valkey.kubethrifty.svc:6379
        stream: analysis-jobs
        consumerGroup: analysers
        pendingEntriesCount: "1"
```

Note the interaction to be aware of: KEDA creates and owns an HPA. Never attach a second HPA to a
KEDA-scaled workload, and remember that a KEDA-owned HPA on CPU is subject to the same right-sizing cost
paradox as any other (kubethrifty-cost-and-packing).

## CI/CD — GitHub Actions GitOps loop

Four workflows, every action pinned: `ci.yml` (lint + test against service containers
`timescaledb-ha:pg18.x` + `valkey:9-alpine`, **plus the three eval gates**), `deploy.yml` (build → push by
SHA → `helm upgrade --install`), `analysis.yml` (scheduled analysis run that may open a right-sizing PR),
`policy.yml` (the shift-left waste gate: fail a PR whose new manifests request more than N× the
workload's historical peak, or omit requests entirely).

```yaml
- name: Eval gates (these can fail the build)
  run: |
    cd analyser
    python -m evals.forecast_eval     # sMAPE + 90% interval coverage vs baseline.json
    python -m evals.sizing_eval       # DECISION assertions (memory peak, pressure, throttle cases)
    python -m evals.detective_eval    # accuracy + Brier/ECE calibration + determinism digest
- uses: sigstore/cosign-installer@v3
- run: cosign sign --yes ghcr.io/OWNER/kubethrifty-analyser@${DIGEST}   # keyless, OIDC
```

Prefer `ValidatingAdmissionPolicy` (in-tree CEL) over an extra policy controller for the in-cluster waste
guardrail — one fewer moving part in a demo cluster; use Kyverno only if you want policy *reporting*.

## Resilience & rollout patterns (each maps to a failure)

| Pattern | KubeThrifty use | Prevents |
|---|---|---|
| RollingUpdate `maxUnavailable: 0` | target workload resize | a bad cut taking the service down unnoticed |
| PodDisruptionBudget | dashboard; checked by rehearsal preflight | eviction storms; rehearsing without headroom |
| Readiness `/api/health` | dashboard | serving errors during DB/cache blips |
| KEDA scale-to-zero | analyser | paying for an idle worker (alert if >0 while queue empty) |
| Watchdog CronJob | rehearsals | a pod left at experimental sizes after a crash |
| Lease (`coordination.k8s.io`) | rehearsals | two experiments on one workload |
| StatefulSet + PVC | TimescaleDB | data loss on reschedule |
| Helm rollback + SHA tags | every deploy | non-deterministic rollback |

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| `image: latest` anywhere, compose included | pin tags; CI tags by `${{ github.sha }}` |
| `kindest/node` unpinned, or 1.33/1.34 | `kindest/node:v1.36.x` (floor 1.35) |
| cgroup v1 node / containerd 1.7 | cgroup v2 + containerd 2.x, or PSI silently returns nothing |
| Helm 3 pinned in CI | Helm 4.2.x; add the `watch` verb for `--wait`; post-renderers become plugins |
| `timescaledb` plain image with Toolkit functions | `timescaledb-ha:pg18.x` |
| broad ClusterRole (`*` verbs, Secrets read) | reads + `patch pods/resize` only |
| watchdog as a goroutine/thread in the analyser | separate CronJob |
| analyser always at ≥1 replica | KEDA `ScaledObject`, `minReplicaCount: 0` |
| auto-merging a right-sizing PR | human approval; gradual rollout; verification loop |
| `node:20-alpine` / `python:3.13-slim` | `node:24-alpine` / `python:3.14-slim` |
| no eval gates in CI | forecast + sizing + detective gates in `ci.yml` |

## Quick reference

- K8s **1.36** demo / **1.35** floor · cgroup v2 mandatory · containerd 2.x · Helm **4.2** · KEDA **2.20**.
- `timescaledb-ha:pg18.4-ts2.28.1-all` · Valkey **9.1** · Grafana **13** · Prometheus 3.x via pinned chart.
- Only mutating RBAC verb: `patch pods/resize`. Right-sizing reaches the cluster through Git.
- Components: dashboard (2 + HPA + PDB) · analyser (KEDA 0→N) · cgroup-truth DaemonSet · watchdog CronJob · TimescaleDB StatefulSet · Valkey.
- CI: lint/test + **three eval gates** + cosign signing; deploy by SHA; policy gate on PRs.
- Prereqs and Helm 4 migration → `references/platform-prereqs.md`; signals → kubethrifty-cgroup-psi-signals; rehearsal RBAC → kubethrifty-resize-rehearsal; API → kubethrifty-nextjs-backend.
