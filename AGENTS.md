# KubeThrifty — project rules

Kubernetes pod right-sizing advisor. Reads Prometheus, sizes CPU and memory by **different**
rules, rehearses candidate sizes on a live pod, and opens GitHub PRs. Full specification:
`KubeThrifty_Implementation_Plan_Rev3.md`.

These rules are non-negotiable and apply to every change. Depth lives in the skills under
`.cursor/skills/` — invoke them by name rather than re-deriving their contents here.

## Version pins

Kubernetes **1.36** demo / **1.35** floor · cgroup **v2** mandatory · containerd **2.x** ·
Helm **4.2.4** · KEDA **2.20** · Node **24 LTS** · Next.js **16.3.x** · React **19** ·
Tailwind **v4** (CSS-first `@theme`, no `tailwind.config.js`) · Recharts **3.x** · Python **3.14**.

Verified-published container tags (do not substitute a floating tag for any of these):
`timescale/timescaledb-ha:pg18.4-ts2.29.2-all` (the `-all` variant is required — it carries the
Toolkit that `percentile_agg` / `approx_percentile` need) · `valkey/valkey:9.1-alpine3.24` ·
`prom/prometheus:v3.14.0` · `grafana/grafana:13.0.7` · `python:3.14.6-slim-trixie` ·
`kube-prometheus-stack` ~**88.x**.

Never `:latest`, anywhere, `docker-compose.yml` included. CI tags images by commit SHA.
Pin `pandas`/`numpy` to exact versions plus a lockfile. Renovate proposes bumps.

## Sizing — CPU and memory are not symmetric

This is the most important rule in the project.

- **CPU** is sized from percentiles: request = P95 × 1.20, limit = P99 × 1.50 (generous).
  Over-limit means CFS throttling, which is survivable.
- **Memory** is sized from the observed **peak** (cgroup `memory.peak`) × 1.25, plus 1.10 GC
  headroom for JVM/Node/dotnet runtimes, and **`limit == request`**. Over-limit means an
  OOMKill, which is fatal. A percentile by construction discards the top 5% of samples —
  exactly the ones that kill you. **Memory is never percentile-sized.**

Floors compose with `max()`, never `min()`:

```python
request = max(statistical_floor, forecast_upper, rehearsed_floor, absolute_min)
```

Record which floor won as `binding_constraint`. Floors only ever *raise* a request. When the
forecast and a rehearsal disagree, the forecast wins — it is a statement about the future.

Absolute minimums: 50m CPU, 64Mi memory. `MIN_DATA_POINTS = 168`; below that, skip the pod
rather than guess.

## Signals — measure suffering, not just usage

- Compare throttle **ratios** (`throttled_periods / cfs_periods`) over equal windows, never
  raw period counters — counters scale with window length, replica count and CFS period.
- OOM detection uses the **counter** `container_oom_events_total` (or cgroup `memory.events`),
  never the flapping `kube_pod_container_status_last_terminated_reason` gauge.
- A missing signal is `null` / "not observed" — **never `0`**, never green, never "no pressure".
  An empty `container_pressure_*` series downgrades the evidence tier; it never raises confidence.
- `memory.max == "max"` parses to `None`, not `0`.

## Evidence tiers — never overstate what you know

`rehearsed` (ran on a live pod, nothing regressed) > `modelled` (percentiles + forecast) >
`partial` (PSI or cgroup signals unavailable). Stamp the tier on every recommendation row,
render it in the UI, state it in the PR. A recommendation that could not be rehearsed says why.
A modelled recommendation is never described as verified.

## Money

`savings = (nodes_before − nodes_after) × node_price`. The node-count delta from the bin-packer
is the **only** figure that carries a currency symbol. Per-pod waste is a percentage, never
rupees — clouds bill per node, so trimming millicores across pods saves nothing until a node
disappears. Prices are pinned in `config/instances.json` with an `as_of` date and region.

## Safety and blast radius

- The only mutating RBAC verb in the entire system is **`patch` on `pods/resize`**. No pod
  deletes, no Deployment writes, no Secret reads.
- Durable right-sizing reaches a cluster **only** through a merged Git PR. Never `kubectl apply`.
  The analyser opens PRs; it never merges, never force-pushes, never touches `main`.
- The dashboard API holds no cluster credentials and exposes no write endpoints. No resize
  button, no rollback button — only PR links. Its one write is `XADD` onto `analysis-jobs`.
- A rehearsal is an experiment that **always reverts**: compensation row and `revert_deadline`
  are persisted *before* the cluster is touched, revert runs in `finally`, and a separate
  watchdog CronJob sweeps anything past its deadline. Blast radius is one pod per workload.
- A timed-out or `Infeasible` rehearsal is `inconclusive`, never `safe`.
- A degraded run produces **no** recommendation, never a wrong one.

## Determinism

`investigate(bundle)` in the detective engine is pure: no network, no clock, no randomness, no
LLM in the decision path. Evidence bundles are immutable and content-addressed by sha256 of
canonical JSON (`sort_keys=True`, `separators=(",", ":")`). CI hashes the whole corpus; treat a
digest change as a build-breaking test. Only re-freeze `digests.json` after reading Brier and
ECE, and say why in the commit message.

## CI gates

Four harnesses gate every build and exit non-zero on regression: forecast accuracy (sMAPE +
90% interval coverage vs `baseline.json`), **sizing decisions** (categorical assertions, not
floats), detective accuracy + calibration (top-1 ≥ 0.90, Brier ≤ 0.08, ECE ≤ 0.10) and the
determinism digest.

## Skills

Invoke by name for depth: `kubethrifty-python-analyser` (sizing, forecasting, verification),
`kubethrifty-cgroup-psi-signals` (evidence layer), `kubethrifty-resize-rehearsal` (experiments),
`kubethrifty-cost-and-packing` (bin-packing, HPA coupling, QoS), `kubethrifty-thrift-detective`
(rules, calibration), `kubethrifty-devops-k8s` (cluster, Helm, CI/CD),
`kubethrifty-nextjs-backend` (API, security), `kubethrifty-nextjs-frontend` (App Router),
`kubethrifty-tailwind-shadcn` (tokens, components), `kubethrifty-charts` (Recharts, Tremor).
