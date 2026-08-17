# KubeThrifty — Kubernetes Pod Right-Sizing Advisor

## Complete Implementation Plan for Junior DevOps / SRE Engineer Interview (2026)

**Revision 3 — August 2026.** Supersedes Rev 2 (June 2026) and Rev 1.

---

> ### Revision 3 — What Changed and Why
>
> Rev 2 was audited line-by-line against the original brief. **The brief is fully covered** — every requested artefact (MVP, tech stack, HLD, LLD, DB design + choice, cache/messaging choice, design patterns, Docker/K8s, DevOps/MLOps wrapper, resilience, mitigation, availability/consistency, deployment strategy, interview prep, README) exists in Rev 2. See **§A** for the audit grid. So Rev 3 does not re-litigate structure. It does four things:
>
> **1. Re-pins the stack against reality as of 17 August 2026 (§B).** Rev 2's pins have already drifted. The important ones: **Helm 3 stopped receiving bug fixes on 8 July 2026** (security-only until 10 Feb 2027) — Rev 2 pinned Helm 3.16, so Rev 3 moves to **Helm 4.2.x** and documents the three things that break. Kubernetes **1.34 enters maintenance 27 Aug 2026**, so the floor moves to **1.35** and the demo pins **1.36**. **KEDA 2.20**, **Next.js 16.3.x**, **Python 3.14**, **TimescaleDB pg18 + Toolkit**, **Grafana 13**, **Valkey 9.1 / Redis 8.x**.
>
> **2. Fixes four genuine engineering defects in Rev 2 (§21).** (a) Rev 2 sizes **memory** off `P95 × 1.2` — memory is incompressible and an OOMKill is fatal, so memory must be sized off observed **peak** (`memory.peak` / max working set), not a percentile that by construction ignores the top 5% of samples. (b) The verification watcher compares raw throttled-*period counts* across windows, which moves with replica count and window length — it must compare the **throttle ratio**. (c) The ₹40,000/month headline is derived per-pod, but clouds bill per **node** — the headline number must come from the bin-packing delta or an interviewer will dismantle it in one question. (d) Rev 2's own `docker-compose.yml` pins `prom/prometheus:latest` and `grafana/grafana:latest` — in the same document whose headline version rule is *never* `latest`.
>
> **3. Adds five new differentiators built specifically on 2026 platform capability (§22–§26).** These are the answer to "why not VPA/KRR/Goldilocks/Cast AI/ScaleOps," and none of them were possible before this year:
> - **§22 — Resize Rehearsal.** In-place pod resize went **GA in Kubernetes 1.35**. KubeThrifty applies a candidate size **in place, to one live pod, with zero restarts**, watches it for N minutes, then reverts in place — so the PR body carries *empirical evidence that the proposed numbers survived real traffic*. Every competing tool ships a *prediction*; KubeThrifty ships an *experiment*. **This is the headline feature of Rev 3.**
> - **§23 — Evidence-grade signals (PSI + cgroup truth).** PSI metrics are **GA in 1.36** (beta since 1.34). Percentile tools cannot distinguish "ran at 90% of limit and was fine" from "ran at 90% and stalled 30% of the time." KubeThrifty reads `container_pressure_*` plus cgroup v2 `memory.peak` / `memory.events` / `cpu.stat` — which is also the correct answer to "why does `free` inside a container lie?"
> - **§24 — HPA-collision guard.** HPA targets CPU utilisation *as a percentage of requests*. Cut requests 4× and the same load reads as 4× utilisation, HPA scales out, and **the bill goes up**. This "right-sizing cost paradox" is invisible to every point-in-time recommender. KubeThrifty detects HPA/KEDA ownership, simulates the coupled replica count, and co-proposes the target — or refuses.
> - **§25 — Consolidation-aware savings.** Requests → nodes → rupees, with a pinned instance catalogue, DaemonSet and `kube-reserved` overhead, the 110-pods/node cap, and explicit **Karpenter / EKS Auto Mode** scenarios (Auto Mode's ≈12% management surcharge means the *same waste costs 12% more*, and neither Karpenter nor Auto Mode optimises requests — KubeThrifty sits upstream of both).
> - **§26 — ThriftDetective.** The scoped, change-attributed incident investigator (this is the KubeTective question, answered in full: **integrate a narrow version, not a clone** — with calibrated confidence, replayable signed evidence bundles, and a chaos-generated regression corpus).
>
> **4. Adds §27 (DRA / GPU / agent-density mode — deliberately scoped as demo-only), §28 (build-order prioritisation: what to actually ship in 8 weeks), and §29 (fact-check appendix with sources and dates, so every version claim in this document is defensible in an interview).**
>
> **What Rev 2 got right and Rev 3 keeps unchanged:** the TimescaleDB choice and the `-ha` image fix, Redis-over-Kafka, the KEDA scale-to-zero story, the GitOps-PR posture (never auto-apply), the forecasting differentiator + MLOps eval harness (§14), the closed-loop verification + auto-rollback (§15), and the self-referential demo. Those were correct calls and they still are.

---

## §A. Completeness Audit — Rev 2 vs the Original Brief

Every item the brief asked for, and where it lives. **Verdict: no gaps in coverage; the gaps were in currency and rigour.**

| # | Brief asked for | Present in Rev 2? | Where | Rev 3 action |
|---|---|---|---|---|
| 1 | Tech stack | ✅ | §2 | Re-pinned (§B); rows added for PSI collector, resize actuator, policy gate |
| 2 | Exact versions | ⚠️ present but **stale** | §2.1 | **Rewritten** (§B) + fact-check appendix (§29) |
| 3 | MVP blueprint | ✅ | §3 | Re-tiered into Ship / Stretch / Slide-only (§28) |
| 4 | Detailed system design | ✅ | §4 | Unchanged; §22–§26 components appended to the diagram narrative |
| 5 | Detailed system architecture | ✅ | §4 | Unchanged |
| 6 | High-level design (HLD) | ✅ | §4 | Unchanged |
| 7 | Low-level design (LLD) | ✅ | §5 | **Corrected** — memory sizing defect (§21.3) |
| 8 | Detailed database design | ✅ | §6.2 | New tables for PSI samples, rehearsals, verdicts (§21.6) |
| 9 | Database choice + matrix | ✅ | §6.1 | Unchanged verdict (TimescaleDB); image tag re-pinned |
| 10 | Cache & messaging choice | ✅ | §7 | Unchanged verdict (Redis Streams); Valkey noted as the license-clean pin |
| 11 | Design patterns | ✅ 6 patterns | §8 | +3 patterns used by the new features (§21.7) |
| 12 | Docker & K8s | ✅ | §9 | Helm 4 migration notes; cgroup v2 requirement called out |
| 13 | DevOps/MLOps wrapper | ✅ | §14.4 | Extended: calibration metric + rehearsal telemetry gate CI (§26.5) |
| 14 | Resilience patterns | ✅ | §16 | +rehearsal-specific safety (lease, watchdog, kill switch) (§21.5) |
| 15 | Mitigation strategies | ✅ 14 rows | §18 | +8 rows for the new features (§21.8) |
| 16 | Availability & consistency | ✅ | §17 | Unchanged |
| 17 | Deployment strategies | ✅ | §19 | VPA defence updated for in-place-resize GA (§21.9) |
| 18 | Interview prep | ✅ | §12 | +12 Q&As for the new features (§21.10) |
| 19 | README + arch diagram + screenshots | ✅ blueprint | §20 | Tech-stack line re-pinned; rehearsal GIF added to must-haves |
| 20 | CI/CD (Actions → registry → deploy) | ✅ | §10 | +policy gate job, +calibration eval job |
| 21 | docker-compose.yml | ✅ | §9.1 | Image tags re-pinned |
| 22 | Monitoring dashboard | ✅ | §11 | +PSI panel, +rehearsal outcome panel |
| 23 | Documented "scaling decision" | ✅ | §11 | Unchanged (second decision added: rehearsal concurrency) |
| 24 | Public deployment + live link | ✅ | §13, §19.4 | Unchanged |
| 25 | Differentiators vs existing OSS | ⚠️ partial (2) | §14, §15 | **+5** (§22–§26) and a head-to-head grid (§28.2) |
| 26 | KubeTective: integrate or not? | ❌ absent | — | **§26 — answered in full** |
| 27 | The 2026 facts (DRA, agent density, Karpenter surcharge, cgroup memory lies) | ❌ absent | — | **§23, §25, §27** |

> Interview soundbite for the audit itself: *"I keep the plan under revision control and audit it against a checklist. Rev 3's audit found that my documentation was complete but two of my pinned dependencies had gone out of support since Rev 2 — Helm 3 lost bug-fix support in July — and that my memory-sizing maths was subtly wrong. Finding your own bugs before an interviewer does is the whole skill."*

---

## §B. Exact Version Matrix — verified 17 August 2026

Pin everything. `latest` is not a version. Sources and dates for every row are in **§29**.

| Component | Rev 2 pinned | **Rev 3 — pin this** | Why it changed / why this pin |
|---|---|---|---|
| **Kubernetes** | 1.35 | **1.36 for the demo cluster** (`kindest/node:v1.36.x`); **1.35 is the supported floor** | 1.34 enters maintenance 27 Aug 2026 (EOL 27 Oct 2026). 1.35 is supported to 28 Feb 2027; 1.36 is current with the longest runway. 1.36 is also where **PSI metrics are GA** and **pod-level in-place resize** is beta. |
| **cgroup version** | not stated | **cgroup v2 mandatory** | Kubernetes **1.35+ requires cgroup v2** for container resource management. This is a hard prerequisite for §23's cgroup reads *and* for PSI. Call it out in the README prerequisites. |
| **Container runtime** | not stated | **containerd 2.x** | PSI support landed in containerd 2.0 and is **not** in the 1.7 line. Without it, `container_pressure_*` metrics are empty. |
| **Helm** | 3.16+ | **4.2.x** (pin in CI via `azure/setup-helm`) | **Helm 3 bug fixes ended 8 July 2026**; security-only support ends **10 Feb 2027**. Helm 4.0 GA'd 12 Nov 2025. Three things to fix on migration: (1) **server-side apply is the default for new installs** — expect explicit conflict errors instead of silent overwrites (this is desirable); (2) `--wait` now uses kstatus and needs the **`watch` verb** in RBAC on all chart resources or it fails before applying anything; (3) `--post-renderer <exe>` is gone — a post-renderer must be packaged as a plugin with `plugin.yaml`. Chart `apiVersion: v2` charts run unchanged. Chart format v3 is experimental — **do not use it**. |
| **Helm chart supply chain** | — | **OCI digest pinning** (`oci://…/chart@sha256:…`) | Helm 4 can install by digest and rejects a mismatch. Free supply-chain credibility for one line of YAML. |
| **KEDA** | 2.17 | **2.20** (released ~May 2026) | KEDA ships every 4 months and supports roughly two cycles; 2.17 (Apr 2025) is long out of support. Still used for the `redis-streams` scale-to-zero story. |
| **Node.js** (dashboard + CI) | 24 LTS | **24 LTS — unchanged, still correct** | Node 24 is Active LTS (EOL 30 Apr 2028). Node 26 is *Current* until its LTS promotion in Oct 2026 — do not build on Current. Node 22 is Maintenance-only. |
| **Next.js** | 16 | **16.3.x** | 16.3.0 shipped 3 Aug 2026 (large dev-memory and build-cache wins; `next build` can type-check with TypeScript 7). Next.js 15 hits EOL 21 Oct 2026 — 16 is the only sensible line. |
| **Python** (analyser) | 3.13 | **3.14** | 3.14 is the current stable line; pandas ≥2.3.3 and NumPy 2.5.x ship cp314 wheels. 3.15 lands 1 Oct 2026 — **do not** pin a beta. Verify `statsforecast` resolves on 3.14 in CI before committing the pin; if it lags, 3.13 remains a defensible fallback and the reason goes in the ADR. |
| **pandas / NumPy** | unpinned | **pin exact minors in `requirements.txt` + a lockfile** | pandas **3.0** is out and has behaviour changes vs 2.x; an unpinned `pip install pandas` in CI is a time bomb. Pin, lock, and let Renovate open the bump PRs. |
| **TimescaleDB** | `timescaledb-ha:pg17` | **`timescale/timescaledb-ha:pg18.4-ts2.28.1-all`** | pg18 line is current; the `-ha` image bundles the **Toolkit** that `percentile_agg`/`approx_percentile` need (Rev 2's real bug fix — keep it). Verify after `docker compose up`: `SELECT * FROM pg_available_extensions WHERE name LIKE '%toolkit%';`. Vendor is now branded **TigerData**; the image path is unchanged. |
| **Cache / queue** | Redis 7.4 | **Valkey 9.1** (default) or **Redis 8.4/8.6** | Valkey 9.1.0 (19 May 2026) is BSD-3, drop-in, and is now the default in several managed cache services — the license-clean pin. Redis 8.x is fine if you prefer the name recognition. Either way, pin the minor and say why. |
| **Prometheus stack** | `latest` | **`kube-prometheus-stack` chart pinned (~88.x)**, Prometheus 3.x | Never `latest` — the chart is the reproducibility boundary. Install from the OCI artefact and pin the chart version in CI. Enable the kubelet `/metrics/cadvisor` scrape (needed for PSI, §23). |
| **Grafana** | 12.x | **13.0.x** | Grafana 13.0 shipped Apr 2026. Provision dashboards as code (JSON in the repo), never hand-edited. |
| **Kyverno or ValidatingAdmissionPolicy** | absent | **VAP (CEL, in-tree) preferred; Kyverno if you need reporting** | Powers the §26-adjacent shift-left waste gate. In-tree CEL policy means no extra controller to run in a demo cluster. |

### Feature gates and API status the plan depends on

| Capability | Status as of Aug 2026 | Why KubeThrifty cares |
|---|---|---|
| **In-place pod resize** (`InPlacePodVerticalScaling`, KEP-1287) | **GA in 1.35** (beta 1.33). Mutate via the **`resize` subresource**; `--subresource resize` needs kubectl ≥1.32. CPU changes usually apply with **no restart**; memory follows `resizePolicy` (`NotRequired` vs `RestartContainer`). Outcome surfaces as `PodResizePending` (`Deferred`/`Infeasible`) and `PodResizeInProgress` conditions | **The whole of §22 (Resize Rehearsal).** Setting the gate explicitly on 1.36 emits a "GA feature gate" warning — don't set it |
| **Pod-level resources** (`PodLevelResources`) | **Beta since 1.34**, default-on | Sidecar-heavy pods share one budget; §22.6 rehearses at pod level |
| **In-place pod-**level** resize** (`InPlacePodLevelResourcesVerticalScaling`) | **Beta in 1.36**, default-on | Widen/shrink the shared pool on a running pod — the sidecar (istio-proxy) waste story |
| **PSI metrics** (`KubeletPSI`, KEP-4205) | **Beta 1.34 → GA 1.36.** Exposed via kubelet Summary API **and** `/metrics/cadvisor` as `container_pressure_{cpu,memory,io}_{stalled,waiting}_seconds_total`. Requires kernel ≥4.20 with `CONFIG_PSI`, cgroup v2, Linux nodes | **§23** — the "did anyone actually suffer?" signal |
| **DRA** (`resource.k8s.io/v1`) | **GA in 1.34**, enabled by default. `DeviceClass`, `ResourceClaim`, `ResourceClaimTemplate`, `ResourceSlice`; kubelet PodResources API reports DRA allocations | **§27** — devices are claimed, not millicore-sized; CPU/mem maths does not apply |
| **VPA `InPlaceOrRecreate`** | **Alpha, off by default** (as of 2026) | The single best line in the VPA defence (§21.9): the incumbent's non-disruptive path is still alpha; KubeThrifty's rehearsal uses the GA primitive directly |

> Interview soundbite: *"Every image is pinned by tag, and the Helm chart by version — with the option to pin by OCI digest. Between my Rev 2 and Rev 3 two pins went stale: Helm 3 lost bug-fix support in July 2026, and Kubernetes 1.34 was about to enter maintenance. I also learned that Kubernetes 1.35 requires cgroup v2 and that PSI needs containerd 2.x — which matters because two of my features read cgroup and PSI data, so those aren't nice-to-haves, they're prerequisites in my README."*

---

## Table of Contents

**Foundation (Rev 1 + Rev 2, re-verified)**
1. Project Overview & Interview Hook
2. Tech Stack (justified; exact pins in §B)
3. MVP Blueprint (8-week plan; build order in §28)
4. System Design — High Level Design (HLD)
5. System Design — Low Level Design (LLD)
6. Database Design & Choice (comparison matrix)
7. Caching & Messaging — Redis/Valkey vs Kafka vs RabbitMQ + KEDA scale-to-zero
8. Design Patterns Used
9. Docker & Kubernetes Deployment Strategy
10. CI/CD Pipeline (GitHub Actions → registry → K8s)
11. Monitoring & Observability
12. Interview Prep — Questions & Answers
13. Deployment Checklist — Go Live

**Differentiators from Rev 2 (kept)**
14. Predictive Right-Sizing (ML) + MLOps Wrapper — **D1**
15. Closed-Loop Verification & Auto-Rollback — **D2**

**Cross-cutting engineering (Rev 2)**
16. Resilience Patterns
17. Availability & Consistency Patterns
18. Mitigation Strategies (failure-mode table)
19. Deployment Strategies (incl. the VPA / KRR / Goldilocks defence)
20. README Blueprint with Architecture Diagram

**New in Rev 3**
21. **Rev 3 Corrections & Amendments to §§5, 6, 8, 12, 16, 18, 19** (the three real defects, plus the deltas the new features imply)
22. **Resize Rehearsal — zero-restart in-cluster experiments — D3 (headline)**
23. **Evidence-Grade Signals: PSI + cgroup truth — D4**
24. **HPA-Collision Guard: the right-sizing cost paradox — D5**
25. **Consolidation-Aware Savings: requests → nodes → ₹ (Karpenter / Auto Mode aware) — D6**
26. **ThriftDetective — deterministic, change-attributed incident investigation (the KubeTective answer) — D7**
27. **DRA / GPU / Agent-Density Mode — D8 (scoped: demo + slide only)**
28. **Build Order, Effort/Impact Matrix, and the Head-to-Head Competitive Grid**
29. **Fact-Check Appendix — every version claim, with source and date**

---


## 1. Project Overview & Interview Hook

**Project Name:** KubeThrifty

**Tagline:** "Stop paying for CPU your pods never touch."

**One-liner:** A Kubernetes cost optimisation platform that deploys a sample microservices application, collects real CPU/memory metrics via Prometheus over a configurable observation window, runs a Python-based statistical analyser comparing requested resources against actual P95/P99 peak usage, renders an interactive Next.js dashboard showing every over-provisioned pod with recommended right-sized limits, calculates projected monthly savings in rupees/dollars, and automatically creates GitHub Pull Requests that update the Kubernetes manifests with optimised resource values — closing the loop from detection to remediation without human intervention.

**Interview Hook (memorize this):**

> "I built a Kubernetes pod right-sizing advisor that saved ₹40,000/month in simulated compute costs. I deployed a 5-microservice app to K8s where every pod requested 2 CPU / 2 GB memory. My Python analyser scraped 7 days of Prometheus metrics and discovered actual P95 peaks were 200m CPU / 256 MB — a 90% over-provision. The Next.js dashboard visualises this waste per pod, per namespace, per cluster, and the system auto-creates GitHub PRs updating the K8s manifests with right-sized limits. I used TimescaleDB for metric storage because time-series queries like 'P95 CPU over 7 days per pod' are 100x faster than PostgreSQL, and I chose Redis over Kafka for the recommendation queue because my event volume was under 1,000 messages/hour. The entire platform runs on K8s itself, with Helm charts, GitHub Actions CI/CD, and Grafana dashboards for self-monitoring."

**Why DevOps interviewers love this:**
- **Cloud cost optimisation** — the #1 FinOps topic in 2026. Every company wants this
- **Kubernetes-native** — you built ON K8s, FOR K8s. Shows deep platform understanding
- **Prometheus expertise** — PromQL queries, metric collection, alerting — core observability
- **Infrastructure-as-Code** — Helm charts, K8s manifests, automated PR creation
- **CI/CD mastery** — GitHub Actions end-to-end: build → test → deploy → auto-PR
- **Real metrics** — "₹40,000/month saved" is a concrete, memorable number
- **Full-stack** — Python analyser + Next.js dashboard + K8s + monitoring = complete engineer

**Target Companies (Bangalore + India):**
- **Razorpay, Flipkart, Swiggy** — massive K8s clusters, always optimising costs
- **Atlassian India** — Kubernetes + observability is their bread and butter
- **Nutanix, VMware (Broadcom)** — cloud infrastructure companies
- **Freshworks, Zoho** — SaaS companies with large K8s footprints
- **Platform9, InfraCloud** — Kubernetes-native companies
- **Any DevOps/SRE role** — cost optimisation is universally valued

---

## 2. Tech Stack — Every Choice Justified

### Core Stack

| Layer | Technology | Why This (Interview Answer) |
|---|---|---|
| **Dashboard** | Next.js 16.3 (App Router) + TypeScript, Node.js 24 LTS | SSR for the landing page. App Router server components for fast initial loads. React 19 + Turbopack. TypeScript for type safety across metric data structures. (Rev 1 said Next 14 / Node 20 — both EOL now; see §B.) |
| **Charts** | Recharts + Tremor | Recharts for time-series CPU/memory graphs. Tremor (by Tailwind Labs) gives beautiful, ready-made dashboard components — KPI cards, bar lists, progress rings. |
| **UI Framework** | Tailwind CSS + shadcn/ui | Clean, dark-mode-first dashboard aesthetic. shadcn gives polished components. Tailwind keeps it consistent. |
| **Metric Analyser** | Python 3.14 + pandas + numpy | Python dominates data analysis. pandas handles time-series metric aggregation (P50/P95/P99, rolling averages). numpy for statistical calculations. Prometheus client library for PromQL queries. (Rev 1 said 3.12; 3.14 is the current pin — see §B.) |
| **Forecasting (Differentiator)** | statsforecast (AutoETS / MSTL), behind a `Forecaster` adapter | Predicts each pod's next 7–14 days (trend + weekly seasonality) so KubeThrifty never shrinks a pod that's trending up. Fast, light deps, no GPU. Swappable for Prophet or a cloud forecaster via the adapter. See §14. |
| **API Backend** | Next.js API Routes (TypeScript) | Dashboard API for frontend. Lightweight — delegates heavy computation to the Python analyser. Avoids a third language in the stack. |
| **Metric Collection** | Prometheus + kube-state-metrics + cAdvisor | Industry standard for K8s monitoring. Prometheus scrapes pod CPU/memory from cAdvisor. kube-state-metrics provides requested/limit values from K8s API. Together they give actual vs requested data. |
| **Metric Visualization** | Grafana (pre-built dashboards) | Alongside our custom Next.js dashboard, Grafana provides deep drill-down for raw metrics. Shows interviewers you know the industry-standard tool. |
| **Database** | TimescaleDB (PostgreSQL extension) | See Section 6. Time-series metric storage. Continuous aggregates for pre-computed P95 rollups. Compression for storage efficiency. Full PostgreSQL compatibility for relational data (users, clusters, recommendations). |
| **Cache** | Redis 8.x / Valkey 9.1 | See Section 7. Metric query cache, recommendation cache, rate limiting, job queue for analysis runs. |
| **Container Orchestration** | Kubernetes 1.36 (kind for local, EKS/GKE for prod; 1.35 is the supported floor) | The platform itself AND the subject it analyses both run on K8s. Self-referential — KubeThrifty right-sizes its own pods. Pin the kind node image (§B); 1.33 is EOL. |
| **Package Management** | Helm 4.2.x | Charts for the sample app, KubeThrifty itself, and Prometheus stack. Shows IaC maturity. |
| **Autoscaling (KEDA 2.20)** | KEDA `redis-streams` scaler | Scales the analyser **to zero** when no analysis job is queued, and up when one arrives. Scale-to-zero is itself a FinOps win — the tool practices what it preaches. See §7. |
| **Sample App** | 5 microservices (Go/Node/Python) | Intentionally over-provisioned. Each service has a different usage pattern: CPU-heavy, memory-heavy, idle, bursty, steady. Demonstrates the analyser handles varied workloads. |
| **Auto-Remediation** | GitHub Actions + PyGithub | Python generates right-sized manifests → creates a branch → opens a PR with detailed description showing before/after values + projected savings. |
| **CI/CD** | GitHub Actions | Build → test → Docker push → Helm upgrade → runs analysis → creates right-sizing PRs. Full GitOps loop. |
| **Monitoring** | Prometheus + Grafana (self-monitoring) | KubeThrifty monitors itself — analysis job duration, API latency, recommendation accuracy. |

### Why NOT These Alternatives (Interview Ammo)

| Rejected Option | Why |
|---|---|
| Plain PostgreSQL (no TimescaleDB) | Time-series queries ("P95 CPU for pod X over 7 days with 15-min buckets") are 10-100x slower in vanilla Postgres. TimescaleDB's hypertables auto-partition by time, and continuous aggregates pre-compute rollups. See Section 6 for detailed comparison. |
| InfluxDB | Good time-series DB but has its own query language (Flux), not SQL. TimescaleDB gives time-series power WITH full PostgreSQL — same ORM, same JOINs, same ecosystem. One DB for both metrics and relational data. |
| MongoDB | Metric data is inherently time-series (timestamp + pod + CPU + memory). MongoDB has no native time-series optimisation until v5 (and it's still basic). TimescaleDB's continuous aggregates and compression are purpose-built for this. |
| Datadog / New Relic | Commercial tools that SOLVE this problem — but we're BUILDING the solution. Using them would eliminate the project's value. Also, Prometheus is free and universally available. |
| Spring Boot backend | DevOps teams speak Python and Go — not Java. The analyser uses pandas/numpy (Python-native). Next.js API routes handle the dashboard API. Adding Spring Boot adds a language for no benefit. |
| Kafka for job queue | Analysis runs happen hourly — that's 24 events/day. Kafka's 3-broker minimum for 24 messages/day is absurd. Redis Streams handles this trivially. |
| ArgoCD for GitOps | ArgoCD is a deployment tool, not an analysis tool. We auto-create PRs that a human reviews and merges — then the existing deployment pipeline picks them up. Adding ArgoCD is scope creep for MVP. |

---

## 3. MVP Blueprint — 8-Week Build Plan

### MVP Scope (What to Build)

**Must Have (MVP):**
- Sample microservices app (5 services with varied resource patterns) deployed to K8s
- Prometheus + kube-state-metrics + cAdvisor scraping all pod metrics
- Python analyser: query Prometheus → compute P50/P95/P99 for CPU and memory per pod
- Compare actual usage vs requested resources → calculate over/under-provisioning percentage
- Store metric snapshots and recommendations in TimescaleDB
- Next.js dashboard: per-pod resource usage heatmap, recommended limits, projected savings
- Auto-PR creation: generate right-sized K8s manifests → open GitHub PR with before/after diff
- Helm chart for KubeThrifty itself
- Grafana dashboards for raw Prometheus metrics
- "Savings Calculator" — convert wasted resources to ₹/month based on cloud provider pricing

**Phase 2 — The Differentiators (build after the MVP demos cleanly):**
- **Predictive right-sizing (ML forecasting)** — forecast each pod's next 7–14 days and never recommend a request below the forecast's upper bound. Adds the MLOps wrapper (forecaster adapter, backtest eval gating CI, drift monitoring, deterministic fallback). The headline upgrade. See §14.
- **Closed-loop verification + auto-rollback** — after a right-sizing PR merges, watch the pod for OOMKills / CPU throttling / restarts for 24–48h; auto-open a rollback PR if it regresses. See §15.
- **Bin-packing "what-if"** — translate per-pod savings into *fewer nodes* (the savings number that actually moves a cloud bill). See §15.4, extended with real node overheads and Karpenter/Auto Mode scenarios in §25.
- **[Rev 3] Resize Rehearsal** — apply a candidate size in place to one live pod, observe kernel pressure signals, revert, and ship the evidence in the PR. Zero restarts. **The headline differentiator.** See §22.
- **[Rev 3] PSI + cgroup truth** — size memory off the kernel's high-water mark and refuse cuts on workloads that are already stalling. See §23.
- **[Rev 3] HPA-collision guard** — detect and simulate the right-sizing cost paradox; co-change the HPA target or refuse. See §24.
- **[Rev 3] ThriftDetective** — deterministic, change-attributed incident investigation with calibrated confidence. See §26.

**Nice to Have (Post-MVP):**
- Multi-cluster support
- VPA (Vertical Pod Autoscaler) integration — apply recommendations automatically (note: §19 explains why we deliberately do GitOps PRs instead)
- Slack/Teams notification when new recommendations are available
- Cost per namespace breakdown
- OPA (Open Policy Agent) policies for resource guardrails
- Anomaly detection (sudden CPU spike = don't right-size down) — partly subsumed by forecasting (§14)

### The Sample Microservices App (What We're Analysing)

This app exists solely to generate realistic, varied workload patterns:

| Service | Language | Resource Request (Intentionally Over-provisioned) | Actual Usage Pattern | Purpose |
|---|---|---|---|---|
| `api-gateway` | Go | 2 CPU / 2 GB | Steady 100m CPU / 128 MB | Demonstrates massive over-provision on a low-traffic gateway |
| `order-service` | Node.js | 1 CPU / 1 GB | Bursty: 50m-800m CPU / 200-400 MB | Demonstrates bursty workload — P95 differs from P50 |
| `payment-processor` | Python | 1 CPU / 1 GB | CPU-heavy: 600m CPU / 256 MB | Demonstrates CPU-bound service — limit should be close to peak |
| `inventory-cache` | Go | 500m CPU / 2 GB | Memory-heavy: 50m CPU / 1.5 GB | Demonstrates memory-bound — CPU is wasted, memory is well-used |
| `notification-worker` | Python | 1 CPU / 512 MB | Nearly idle: 10m CPU / 64 MB | Demonstrates almost entirely wasted resources |

**Total requested:** 5.5 CPU / 6.5 GB
**Total actual peak (P95):** 1.66 CPU / 2.6 GB
**Waste:** 70% CPU over-provisioned, 60% memory over-provisioned
**Simulated savings:** ~₹40,000/month on a typical Indian cloud provider

### Week-by-Week Schedule

**Week 1 — Kubernetes Foundation + Sample App**
- Day 1–2: Set up local K8s cluster (kind or minikube), install kubectl, Helm
- Day 3–4: Build 5 microservice Docker images with synthetic load generators
- Day 5: Write Helm chart for sample app with intentionally over-provisioned resource requests
- Day 6: Deploy Prometheus stack (kube-prometheus-stack Helm chart)
- Day 7: Verify metrics flowing: container_cpu_usage_seconds_total, container_memory_working_set_bytes
- Deliverable: 5 services running on K8s, Prometheus scraping all pod metrics

**Week 2 — Python Metric Analyser (Core Engine)**
- Day 1–2: Prometheus HTTP API client — query range metrics over configurable window
- Day 3–4: pandas processing: compute P50/P95/P99 per pod for CPU and memory
- Day 5: Compare actual usage (from Prometheus) vs requested resources (from kube-state-metrics)
- Day 6: Recommendation engine: calculate right-sized values with safety margins
- Day 7: Output: JSON report per pod with current/recommended/savings
- Deliverable: CLI tool: `python analyser.py --window 7d` → outputs recommendations

**Week 3 — TimescaleDB + Data Persistence**
- Day 1–2: TimescaleDB setup (Docker or K8s StatefulSet), schema design
- Day 3–4: Store metric snapshots: hypertable partitioned by time, indexed by pod
- Day 5: Continuous aggregates: pre-compute hourly P95 rollups
- Day 6: Store recommendations: relational table linked to metric snapshots
- Day 7: Historical queries: "Show me pod X's CPU P95 trend over 30 days"
- Deliverable: Analyser persists results to TimescaleDB, queryable via SQL

**Week 4 — Next.js Dashboard**
- Day 1–2: Next.js scaffold, Tailwind + shadcn/ui + Tremor, dark mode
- Day 3: KPI cards: total pods, over-provisioned count, projected monthly savings
- Day 4: Resource usage heatmap: grid of pods colored by waste percentage
- Day 5: Per-pod detail page: time-series chart (actual vs requested), recommendation card
- Day 6: Namespace/cluster breakdown view
- Day 7: Savings calculator: input cloud provider (AWS/GCP/Azure) → ₹/$ monthly savings
- Deliverable: Interactive dashboard showing all recommendations

**Week 5 — Auto-Remediation (GitHub PR Creation)**
- Day 1–2: Generate right-sized K8s manifest YAML from recommendations
- Day 3–4: PyGithub integration: create branch, commit modified manifests, open PR
- Day 5: PR description template: before/after table, savings estimate, confidence level
- Day 6: Safety guardrails: never recommend below absolute minimums, never change prod without review
- Day 7: GitHub Actions workflow: scheduled analysis run → auto-PR if recommendations exist
- Deliverable: System automatically creates PRs with right-sized resource limits

**Week 6 — Helm Charts + Self-Deployment**
- Day 1–2: Helm chart for KubeThrifty (dashboard + analyser + TimescaleDB + Redis)
- Day 3: values.yaml: configurable Prometheus URL, analysis window, safety margins
- Day 4: KubeThrifty monitors ITSELF — right-sizes its own pods (self-referential demo)
- Day 5: Grafana dashboards: raw metrics + KubeThrifty operational metrics
- Day 6–7: README, architecture diagram, setup instructions, screenshots
- Deliverable: `helm install kubethrifty ./charts/kubethrifty` — one command deployment

**Week 7 — CI/CD, Polish, Deploy**
- Day 1–2: GitHub Actions: lint → test → Docker build → push → Helm upgrade
- Day 3: Integration tests: deploy to kind cluster in CI, run analysis, verify recommendations
- Day 4: Prometheus + Grafana self-monitoring dashboard
- Day 5: Document scaling decisions (TimescaleDB over Postgres, Redis over Kafka)
- Day 6: Deploy to a cloud K8s cluster (GKE free tier or EKS)
- Day 7: Record demo video, publish live link
- Deliverable: Live deployed, CI/CD pipeline, monitoring, documentation

**Week 8 — Phase 2 Differentiators (Forecasting + Verification)**
- Day 1: `Forecaster` adapter interface + statsforecast implementation (AutoETS/MSTL) on per-pod hourly series
- Day 2: Wire forecast into the recommendation engine — `recommended_request = max(P95 * margin, forecast_upper_bound)`; never cut below the forecast
- Day 3: Backtest eval harness (sMAPE + 90% interval coverage on a holdout) → baseline.json → gate CI
- Day 4: Drift monitoring — track realized-vs-forecast error in Prometheus; deterministic P95 fallback when confidence is low or data is thin
- Day 5: Verification watcher — after a merge, monitor OOMKills / `container_cpu_cfs_throttled_periods_total` / restarts for the changed pod
- Day 6: Auto-rollback PR generator + bin-packing "what-if" (node-count reduction estimate)
- Day 7: Update README + record the "it forecasted, cut safely, then verified itself" demo
- Deliverable: Forecast-aware recommendations with a CI-gated eval, plus a self-correcting verify/rollback loop

---

## 4. High-Level Design (HLD)

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    KUBERNETES CLUSTER                                    │
│                                                                         │
│  ┌──────────────────────────────────────────────────────┐               │
│  │           SAMPLE MICROSERVICES APP                    │               │
│  │          (What We're Analysing)                       │               │
│  │                                                      │               │
│  │  ┌────────────┐ ┌────────────┐ ┌─────────────────┐   │               │
│  │  │api-gateway │ │order-svc   │ │payment-processor│   │               │
│  │  │2CPU/2GB    │ │1CPU/1GB    │ │1CPU/1GB         │   │               │
│  │  │actual:100m │ │actual:bursty│ │actual:600m/256MB│   │               │
│  │  └────────────┘ └────────────┘ └─────────────────┘   │               │
│  │  ┌────────────┐ ┌─────────────────┐                   │               │
│  │  │inv-cache   │ │notif-worker     │                   │               │
│  │  │500m/2GB    │ │1CPU/512MB       │                   │               │
│  │  │actual:50m  │ │actual:10m/64MB  │                   │               │
│  │  └────────────┘ └─────────────────┘                   │               │
│  └────────────────────────┬─────────────────────────────┘               │
│                           │ metrics scraped                              │
│                           ▼                                              │
│  ┌──────────────────────────────────────────────────────┐               │
│  │          OBSERVABILITY STACK                          │               │
│  │                                                      │               │
│  │  ┌───────────────┐  ┌──────────────┐  ┌───────────┐  │               │
│  │  │  Prometheus    │  │kube-state-   │  │  cAdvisor │  │               │
│  │  │  (metrics DB)  │  │metrics       │  │  (per-node)│  │               │
│  │  │                │  │(K8s resource │  │  (container│  │               │
│  │  │  CPU/mem usage │  │ requests &   │  │   metrics) │  │               │
│  │  │  time-series   │  │ limits)      │  │           │  │               │
│  │  └───────┬───────┘  └──────────────┘  └───────────┘  │               │
│  │          │                                            │               │
│  │  ┌───────▼───────┐                                    │               │
│  │  │   Grafana     │  Raw metric dashboards             │               │
│  │  └───────────────┘                                    │               │
│  └────────────────────────┬─────────────────────────────┘               │
│                           │ PromQL queries                               │
│                           ▼                                              │
│  ┌──────────────────────────────────────────────────────┐               │
│  │          KUBETHRIFTY PLATFORM                         │               │
│  │                                                      │               │
│  │  ┌────────────────────┐    ┌──────────────────────┐   │               │
│  │  │  Python Analyser   │    │  Next.js Dashboard   │   │               │
│  │  │                    │    │                      │   │               │
│  │  │  - Query Prometheus│    │  - KPI Summary       │   │               │
│  │  │  - Compute P50/95  │    │  - Pod Heatmap       │   │               │
│  │  │  - Compare req vs  │    │  - Time-series charts│   │               │
│  │  │    actual           │    │  - Savings calculator│   │               │
│  │  │  - Generate recs   │    │  - Recommendation    │   │               │
│  │  │  - Create GitHub PR│    │    detail pages      │   │               │
│  │  └─────────┬──────────┘    └───────────┬──────────┘   │               │
│  │            │                           │               │               │
│  │            ▼                           ▼               │               │
│  │  ┌────────────────┐       ┌────────────────┐          │               │
│  │  │  TimescaleDB   │       │    Redis 7     │          │               │
│  │  │  (metrics +    │       │  (cache +      │          │               │
│  │  │   recs +       │       │   job queue +  │          │               │
│  │  │   history)     │       │   rate limit)  │          │               │
│  │  └────────────────┘       └────────────────┘          │               │
│  └──────────────────────────────────────────────────────┘               │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    │ Auto-PR (Git push)
                    ▼
            ┌───────────────┐
            │  GitHub.com   │
            │               │
            │  PR: "Right-  │
            │  size pods:   │
            │  save ₹40K/mo"│
            └───────────────┘
```

### Core Data Flow

**Flow 1: Metric Collection (Continuous)**
```
Every 15 seconds (Prometheus scrape interval):
    cAdvisor (on each K8s node)
        → exposes container_cpu_usage_seconds_total (counter)
        → exposes container_memory_working_set_bytes (gauge)
    
    kube-state-metrics
        → exposes kube_pod_container_resource_requests (gauge)
        → exposes kube_pod_container_resource_limits (gauge)
    
    Prometheus scrapes all targets
        → stores in TSDB (14-day retention)
        → available for PromQL queries
```

**Flow 2: Analysis Run (Scheduled — every 6 hours or manual)**
```
Step 1:  GitHub Actions cron (or K8s CronJob) triggers Python analyser
    ↓
Step 2:  Analyser queries Prometheus HTTP API:
         PromQL: rate(container_cpu_usage_seconds_total{namespace="sample-app"}[5m])
         → Gets CPU usage rate for every pod over the analysis window (7 days)
    ↓
Step 3:  Analyser queries kube-state-metrics via Prometheus:
         PromQL: kube_pod_container_resource_requests{resource="cpu"}
         → Gets requested CPU for every pod
    ↓
Step 4:  pandas DataFrame processing:
         - Group by pod/container
         - Compute percentiles: P50, P75, P90, P95, P99
         - Compute max, mean, stddev
         - Calculate "actual peak" as max(P95, mean + 2*stddev) [safety buffer]
    ↓
Step 5:  Recommendation Engine:
         For each pod:
           recommended_request = actual_P95 * 1.2  (20% safety margin on requests)
           recommended_limit   = actual_P99 * 1.5  (50% safety margin on limits)
           
           Guardrails:
             - Never recommend below 50m CPU / 64MB memory (absolute floor)
             - Never recommend limit < request
             - Flag as "needs review" if stddev > 50% of mean (too variable)
             - Flag as "keep current" if savings < 10% (not worth the risk)
    ↓
Step 6:  Cost Calculation:
         wasted_cpu = (requested_cpu - recommended_request) per pod
         wasted_memory = (requested_memory - recommended_request) per pod
         
         monthly_savings = wasted_cpu * cpu_price_per_core_hour * 730 hours
                         + wasted_memory * mem_price_per_gb_hour * 730 hours
         
         (Prices configurable per cloud provider: AWS m5.xlarge, GCP e2-standard-4, etc.)
    ↓
Step 7:  Persist to TimescaleDB:
         - metric_snapshots (hypertable): raw P50/P95/P99 per pod per timestamp
         - recommendations: current vs recommended vs savings per pod
         - analysis_runs: metadata about each analysis run
    ↓
Step 8:  Cache recommendations in Redis (TTL: 6 hours)
    ↓
Step 9:  If auto-PR enabled:
         a) Generate modified K8s manifest YAML with right-sized values
         b) Create git branch: "kubethrifty/right-size-{date}"
         c) Commit manifests to branch
         d) Open GitHub PR with detailed description:
            - Before/after resource table per pod
            - Projected monthly savings
            - Confidence level per recommendation
            - Grafana dashboard link for validation
```

**Flow 3: Dashboard Request**
```
Step 1:  User opens https://kubethrifty.example.com
    ↓
Step 2:  Next.js SSR fetches latest recommendations:
         a) Check Redis cache → if hit, return immediately
         b) Cache miss → query TimescaleDB → cache result → return
    ↓
Step 3:  Dashboard renders:
         - Header KPIs: total pods | over-provisioned | savings/month | last analysis
         - Heatmap grid: each cell = pod, color = waste% (green = efficient, red = wasteful)
         - Click pod → time-series chart: actual CPU/memory vs requested (shaded area = waste)
         - Recommendation card: "Reduce api-gateway from 2 CPU to 200m CPU (save ₹8,500/mo)"
         - "Create PR" button: triggers auto-PR for selected recommendations
         - Savings calculator: select cloud provider → total monthly savings in ₹/$
```

### Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     DATA FLOW OVERVIEW                           │
│                                                                 │
│  PROMETHEUS (source of truth for metrics)                       │
│       │                                                         │
│       │  PromQL HTTP API                                        │
│       ▼                                                         │
│  PYTHON ANALYSER                                                │
│       │                                                         │
│       ├── pandas: compute P50/P95/P99 per pod                   │
│       ├── Recommendation Engine: right-sized values + savings   │
│       ├── Store → TimescaleDB (metric_snapshots, recommendations)│
│       ├── Cache → Redis (latest recommendations)                │
│       └── Auto-PR → GitHub (modified K8s manifests)             │
│                                                                 │
│  NEXT.JS DASHBOARD                                              │
│       │                                                         │
│       ├── Read → Redis cache (fast)                             │
│       ├── Read → TimescaleDB (cache miss, historical queries)   │
│       └── Render → KPIs, heatmap, charts, savings calculator    │
│                                                                 │
│  GITHUB ACTIONS                                                 │
│       │                                                         │
│       ├── Cron: trigger analysis every 6 hours                  │
│       ├── On PR merge: re-deploy with right-sized manifests     │
│       └── CI/CD: build → test → deploy KubeThrifty itself       │
│                                                                 │
│  GRAFANA                                                        │
│       │                                                         │
│       └── Raw Prometheus dashboards for deep drill-down         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Low-Level Design (LLD)

### 5.1 Project Structure

```
kubethrifty/
├── analyser/                              # Python metric analyser
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                        # CLI entrypoint
│   │   ├── config.py                      # Configuration (env vars, defaults)
│   │   ├── prometheus_client.py           # PromQL query wrapper
│   │   ├── metric_collector.py            # Fetch + aggregate metrics
│   │   ├── percentile_calculator.py       # P50/P95/P99 computation
│   │   ├── recommendation_engine.py       # Core: actual vs requested → recommendations
│   │   ├── cost_calculator.py             # Resource waste → ₹/$ savings
│   │   ├── manifest_generator.py          # Generate right-sized K8s YAML
│   │   ├── github_pr_creator.py           # Auto-create PRs with PyGithub
│   │   ├── db/
│   │   │   ├── connection.py              # TimescaleDB connection pool
│   │   │   ├── models.py                  # SQLAlchemy models
│   │   │   └── queries.py                 # Complex time-series queries
│   │   ├── cache/
│   │   │   └── redis_client.py            # Redis cache operations
│   │   └── cloud_pricing/
│   │       ├── aws.py                     # AWS instance pricing
│   │       ├── gcp.py                     # GCP instance pricing
│   │       └── azure.py                   # Azure instance pricing
│   ├── tests/
│   │   ├── test_recommendation_engine.py  # Unit tests for core logic
│   │   ├── test_percentile_calculator.py
│   │   ├── test_cost_calculator.py
│   │   ├── test_manifest_generator.py
│   │   └── fixtures/                      # Mock Prometheus responses
│   ├── Dockerfile
│   ├── requirements.txt
│   └── pyproject.toml
│
├── dashboard/                             # Next.js dashboard
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx                 # Root layout (dark theme, nav)
│   │   │   ├── page.tsx                   # Main dashboard
│   │   │   ├── pods/
│   │   │   │   └── [podName]/page.tsx     # Per-pod detail view
│   │   │   ├── namespaces/
│   │   │   │   └── [namespace]/page.tsx   # Per-namespace breakdown
│   │   │   ├── history/page.tsx           # Historical analysis runs
│   │   │   ├── savings/page.tsx           # Savings calculator
│   │   │   └── api/
│   │   │       ├── recommendations/route.ts    # GET current recommendations
│   │   │       ├── pods/[name]/metrics/route.ts # GET time-series for pod
│   │   │       ├── analysis/trigger/route.ts    # POST trigger manual analysis
│   │   │       ├── history/route.ts             # GET analysis run history
│   │   │       └── health/route.ts              # GET health check
│   │   ├── components/
│   │   │   ├── dashboard/
│   │   │   │   ├── KPICards.tsx            # Total pods, waste%, savings
│   │   │   │   ├── PodHeatmap.tsx          # Grid of pods colored by waste%
│   │   │   │   ├── NamespaceBreakdown.tsx  # Bar chart per namespace
│   │   │   │   ├── SavingsSummary.tsx      # Monthly savings in ₹/$
│   │   │   │   └── RecentAnalysis.tsx      # Last analysis timestamp + status
│   │   │   ├── pods/
│   │   │   │   ├── ResourceChart.tsx       # Time-series: actual vs requested
│   │   │   │   ├── RecommendationCard.tsx  # Before → After with savings
│   │   │   │   ├── PercentileTable.tsx     # P50/P75/P90/P95/P99 values
│   │   │   │   └── WasteGauge.tsx          # Circular gauge showing waste%
│   │   │   ├── savings/
│   │   │   │   ├── CloudProviderSelect.tsx # AWS/GCP/Azure picker
│   │   │   │   ├── InstanceTypeSelect.tsx  # m5.xlarge, e2-standard-4, etc.
│   │   │   │   └── MonthlyCostBreakdown.tsx# Itemized savings per pod
│   │   │   └── common/
│   │   │       ├── LoadingSkeleton.tsx
│   │   │       ├── EmptyState.tsx
│   │   │       └── ErrorBoundary.tsx
│   │   ├── hooks/
│   │   │   ├── useRecommendations.ts
│   │   │   ├── usePodMetrics.ts
│   │   │   └── useAnalysisHistory.ts
│   │   ├── services/
│   │   │   ├── api.ts                     # Fetch wrapper
│   │   │   └── metricsService.ts          # API calls for metrics
│   │   └── types/
│   │       └── index.ts
│   ├── Dockerfile
│   └── package.json
│
├── sample-app/                            # Over-provisioned microservices
│   ├── api-gateway/
│   │   ├── main.go
│   │   └── Dockerfile
│   ├── order-service/
│   │   ├── server.js
│   │   └── Dockerfile
│   ├── payment-processor/
│   │   ├── app.py
│   │   └── Dockerfile
│   ├── inventory-cache/
│   │   ├── main.go
│   │   └── Dockerfile
│   └── notification-worker/
│       ├── worker.py
│       └── Dockerfile
│
├── charts/                                # Helm charts
│   ├── kubethrifty/                       # Main platform chart
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   │       ├── dashboard-deployment.yaml
│   │       ├── analyser-cronjob.yaml
│   │       ├── timescaledb-statefulset.yaml
│   │       ├── redis-deployment.yaml
│   │       ├── configmap.yaml
│   │       ├── secrets.yaml
│   │       ├── service.yaml
│   │       ├── ingress.yaml
│   │       └── serviceaccount.yaml
│   └── sample-app/                        # Sample app chart
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── api-gateway.yaml
│           ├── order-service.yaml
│           ├── payment-processor.yaml
│           ├── inventory-cache.yaml
│           └── notification-worker.yaml
│
├── k8s/                                   # Raw K8s manifests (pre-Helm)
│   ├── prometheus/
│   │   └── kube-prometheus-stack-values.yaml
│   └── grafana/
│       └── dashboards/
│           ├── pod-resources.json          # Custom Grafana dashboard
│           └── kubethrifty-operational.json
│
├── monitoring/
│   ├── prometheus.yml                     # Self-monitoring config
│   └── alerting-rules.yml                 # Alerts for analysis failures
│
├── .github/
│   └── workflows/
│       ├── ci.yml                         # Lint + test + build
│       ├── deploy.yml                     # Deploy to K8s
│       └── analysis.yml                   # Scheduled analysis run
│
├── docker-compose.yml                     # Local dev (non-K8s)
├── Makefile                               # Common commands
└── README.md
```

### 5.2 Core Algorithm — recommendation_engine.py

> ⚠️ **Rev 3 correction — read §21.3 before implementing this section.** The code below sizes *both* CPU and memory as `P95 × 1.20`. That is correct for CPU and **wrong for memory**: a percentile discards the top 5% of samples, and for an incompressible resource those are the samples that OOMKill you. Rev 3 replaces the sizing maths with `analyser/src/sizing.py` (§21.3, complete file): CPU stays percentile-based, memory is sized off the observed **peak** (cgroup `memory.peak`), and memory sets `limit == request`. Keep the dataclasses and enums below; import the sizing functions.

```python
# analyser/src/recommendation_engine.py

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import pandas as pd
import numpy as np


class Confidence(Enum):
    HIGH = "high"         # Stable workload, clear recommendation
    MEDIUM = "medium"     # Some variability, recommendation with wider margin
    LOW = "low"           # Highly variable or insufficient data


class Action(Enum):
    REDUCE = "reduce"           # Over-provisioned — shrink
    INCREASE = "increase"       # Under-provisioned — grow (rare but important)
    KEEP = "keep"               # Within 10% — not worth changing
    NEEDS_REVIEW = "needs_review"  # Too variable — human must decide


@dataclass
class ResourceRecommendation:
    pod_name: str
    container_name: str
    namespace: str
    resource_type: str              # "cpu" or "memory"

    current_request: float          # Current requested value
    current_limit: float            # Current limit value
    
    actual_p50: float               # 50th percentile actual usage
    actual_p95: float               # 95th percentile actual usage
    actual_p99: float               # 99th percentile actual usage
    actual_max: float               # Maximum observed
    actual_mean: float
    actual_stddev: float
    
    recommended_request: float      # New recommended request
    recommended_limit: float        # New recommended limit
    
    savings_monthly_inr: float      # Projected savings in ₹
    savings_percentage: float       # % reduction
    
    confidence: Confidence
    action: Action
    reason: str                     # Human-readable explanation


# === CONFIGURATION ===
MIN_CPU_MILLICORES = 50             # Never recommend below 50m CPU
MIN_MEMORY_MB = 64                  # Never recommend below 64 MB
REQUEST_SAFETY_MARGIN = 1.20        # 20% above P95 for requests
LIMIT_SAFETY_MARGIN = 1.50          # 50% above P99 for limits
MIN_SAVINGS_THRESHOLD = 0.10        # Don't recommend if savings < 10%
HIGH_VARIABILITY_THRESHOLD = 0.50   # If stddev/mean > 50% → flag for review
MIN_DATA_POINTS = 168               # At least 168 hourly samples (7 days)


def compute_recommendations(
    metrics_df: pd.DataFrame,
    cloud_pricing: dict
) -> List[ResourceRecommendation]:
    """
    Core recommendation engine.
    
    Input: DataFrame with columns:
      pod_name, container_name, namespace, resource_type,
      timestamp, actual_value, requested_value, limit_value
    
    Output: List of ResourceRecommendation for each pod/container/resource
    """
    recommendations = []
    
    # Group by pod + container + resource type
    grouped = metrics_df.groupby(
        ['pod_name', 'container_name', 'namespace', 'resource_type']
    )
    
    for (pod, container, namespace, resource), group in grouped:
        values = group['actual_value'].dropna()
        
        # Skip if insufficient data
        if len(values) < MIN_DATA_POINTS:
            continue
        
        # Current settings
        current_request = group['requested_value'].iloc[-1]
        current_limit = group['limit_value'].iloc[-1]
        
        # Statistical analysis
        p50 = np.percentile(values, 50)
        p95 = np.percentile(values, 95)
        p99 = np.percentile(values, 99)
        actual_max = values.max()
        actual_mean = values.mean()
        actual_stddev = values.std()
        
        # Variability check
        variability = actual_stddev / actual_mean if actual_mean > 0 else 0
        
        # Compute recommended values
        recommended_request = max(
            p95 * REQUEST_SAFETY_MARGIN,
            _absolute_minimum(resource)
        )
        recommended_limit = max(
            p99 * LIMIT_SAFETY_MARGIN,
            recommended_request * 1.2  # Limit must be >= 1.2x request
        )
        
        # Determine action
        savings_pct = (current_request - recommended_request) / current_request
        
        if variability > HIGH_VARIABILITY_THRESHOLD:
            action = Action.NEEDS_REVIEW
            confidence = Confidence.LOW
            reason = (
                f"Usage is highly variable (stddev/mean = {variability:.0%}). "
                f"P95={_format(p95, resource)} but max={_format(actual_max, resource)}. "
                f"Recommend manual review before resizing."
            )
        elif savings_pct < -MIN_SAVINGS_THRESHOLD:
            action = Action.INCREASE
            confidence = Confidence.HIGH
            reason = (
                f"UNDER-PROVISIONED: P95={_format(p95, resource)} exceeds "
                f"request={_format(current_request, resource)}. "
                f"Risk of OOMKill or CPU throttling."
            )
        elif savings_pct < MIN_SAVINGS_THRESHOLD:
            action = Action.KEEP
            confidence = Confidence.HIGH
            reason = (
                f"Already well-sized. Savings of {savings_pct:.0%} is below "
                f"the {MIN_SAVINGS_THRESHOLD:.0%} threshold."
            )
        else:
            action = Action.REDUCE
            confidence = (
                Confidence.HIGH if variability < 0.20 else Confidence.MEDIUM
            )
            reason = (
                f"Over-provisioned by {savings_pct:.0%}. "
                f"Current request={_format(current_request, resource)}, "
                f"P95 actual={_format(p95, resource)}. "
                f"Recommended request={_format(recommended_request, resource)} "
                f"with {REQUEST_SAFETY_MARGIN:.0%}x safety margin."
            )
        
        # Cost calculation
        monthly_savings = _calculate_savings(
            resource, current_request, recommended_request, cloud_pricing
        )
        
        recommendations.append(ResourceRecommendation(
            pod_name=pod,
            container_name=container,
            namespace=namespace,
            resource_type=resource,
            current_request=current_request,
            current_limit=current_limit,
            actual_p50=p50,
            actual_p95=p95,
            actual_p99=p99,
            actual_max=actual_max,
            actual_mean=actual_mean,
            actual_stddev=actual_stddev,
            recommended_request=recommended_request,
            recommended_limit=recommended_limit,
            savings_monthly_inr=monthly_savings,
            savings_percentage=savings_pct,
            confidence=confidence,
            action=action,
            reason=reason,
        ))
    
    return recommendations


def _absolute_minimum(resource_type: str) -> float:
    """Never recommend below these values — pods need minimum resources to run."""
    if resource_type == 'cpu':
        return MIN_CPU_MILLICORES / 1000  # 50m = 0.05 cores
    return MIN_MEMORY_MB * 1024 * 1024    # 64 MB in bytes


def _calculate_savings(
    resource: str, current: float, recommended: float, pricing: dict
) -> float:
    """Convert resource reduction to monthly ₹ savings."""
    delta = max(0, current - recommended)
    hours_per_month = 730
    
    if resource == 'cpu':
        # CPU: price per core-hour
        return delta * pricing['cpu_per_core_hour'] * hours_per_month
    else:
        # Memory: price per GB-hour
        delta_gb = delta / (1024 * 1024 * 1024)
        return delta_gb * pricing['memory_per_gb_hour'] * hours_per_month


def _format(value: float, resource_type: str) -> str:
    """Human-readable resource formatting."""
    if resource_type == 'cpu':
        if value < 1:
            return f"{int(value * 1000)}m"
        return f"{value:.1f} cores"
    else:
        mb = value / (1024 * 1024)
        if mb < 1024:
            return f"{int(mb)} MB"
        return f"{mb/1024:.1f} GB"
```

### 5.3 PromQL Queries Used by the Analyser

```python
# analyser/src/prometheus_client.py

QUERIES = {
    # Actual CPU usage rate per container (cores)
    "cpu_usage": """
        avg by (pod, container, namespace) (
            rate(container_cpu_usage_seconds_total{
                namespace="{namespace}",
                container!="",
                container!="POD"
            }[5m])
        )
    """,
    
    # Actual memory usage per container (bytes)
    "memory_usage": """
        avg by (pod, container, namespace) (
            container_memory_working_set_bytes{
                namespace="{namespace}",
                container!="",
                container!="POD"
            }
        )
    """,
    
    # Requested CPU per container (cores)
    "cpu_requests": """
        kube_pod_container_resource_requests{
            namespace="{namespace}",
            resource="cpu"
        }
    """,
    
    # Requested memory per container (bytes)
    "memory_requests": """
        kube_pod_container_resource_requests{
            namespace="{namespace}",
            resource="memory"
        }
    """,
    
    # CPU limits per container
    "cpu_limits": """
        kube_pod_container_resource_limits{
            namespace="{namespace}",
            resource="cpu"
        }
    """,
    
    # Memory limits per container
    "memory_limits": """
        kube_pod_container_resource_limits{
            namespace="{namespace}",
            resource="memory"
        }
    """,
    
    # P95 CPU over window (used for quick summary)
    "cpu_p95": """
        quantile_over_time(0.95,
            rate(container_cpu_usage_seconds_total{
                namespace="{namespace}",
                container!=""
            }[5m])[{window}:1m]
        )
    """,
    
    # P95 memory over window
    "memory_p95": """
        quantile_over_time(0.95,
            container_memory_working_set_bytes{
                namespace="{namespace}",
                container!=""
            }[{window}:1m]
        )
    """,
}
```

### 5.4 Auto-PR Generator

```python
# analyser/src/github_pr_creator.py

from github import Github
from typing import List
import yaml

def create_rightsizing_pr(
    recommendations: List[ResourceRecommendation],
    repo_full_name: str,
    manifest_path: str,
    github_token: str
) -> str:
    """
    Creates a GitHub PR with right-sized K8s manifests.
    Returns the PR URL.
    """
    gh = Github(github_token)
    repo = gh.get_repo(repo_full_name)
    
    # 1. Read current manifest
    main_branch = repo.default_branch
    file = repo.get_contents(manifest_path, ref=main_branch)
    manifest = yaml.safe_load(file.decoded_content)
    
    # 2. Apply recommendations to manifest
    modified = apply_recommendations_to_manifest(manifest, recommendations)
    new_content = yaml.dump(modified, default_flow_style=False)
    
    # 3. Create branch
    branch_name = f"kubethrifty/right-size-{datetime.now().strftime('%Y%m%d-%H%M')}"
    source = repo.get_branch(main_branch)
    repo.create_git_ref(f"refs/heads/{branch_name}", source.commit.sha)
    
    # 4. Commit modified manifest
    repo.update_file(
        path=manifest_path,
        message=f"chore: right-size pod resources (save ₹{total_savings:,.0f}/mo)",
        content=new_content,
        sha=file.sha,
        branch=branch_name
    )
    
    # 5. Create PR with detailed description
    pr_body = generate_pr_description(recommendations)
    pr = repo.create_pull(
        title=f"⚡ Right-size pod resources — save ₹{total_savings:,.0f}/month",
        body=pr_body,
        head=branch_name,
        base=main_branch
    )
    
    return pr.html_url


def generate_pr_description(recs: List[ResourceRecommendation]) -> str:
    """Generates a markdown PR body with before/after table."""
    lines = [
        "## KubeThrifty — Pod Right-Sizing Recommendations",
        "",
        "This PR was automatically generated by KubeThrifty after analysing",
        "7 days of Prometheus metrics.",
        "",
        "### Resource Changes",
        "",
        "| Pod | Resource | Current | → Recommended | Savings/mo | Confidence |",
        "|---|---|---|---|---|---|",
    ]
    
    total_savings = 0
    for r in recs:
        if r.action in (Action.REDUCE, Action.INCREASE):
            current = _format(r.current_request, r.resource_type)
            recommended = _format(r.recommended_request, r.resource_type)
            savings = f"₹{r.savings_monthly_inr:,.0f}"
            conf = r.confidence.value
            emoji = "🔻" if r.action == Action.REDUCE else "🔺"
            lines.append(
                f"| {r.pod_name} | {r.resource_type} {emoji} | {current} | "
                f"{recommended} | {savings} | {conf} |"
            )
            total_savings += r.savings_monthly_inr
    
    lines.extend([
        "",
        f"### Total Projected Savings: ₹{total_savings:,.0f}/month",
        "",
        "### Safety Measures",
        "- All requests include a 20% safety margin above P95 peak",
        "- All limits include a 50% safety margin above P99 peak",
        "- No pod is sized below 50m CPU / 64 MB memory",
        "- Highly variable workloads are flagged for manual review",
        "",
        "### How to Validate",
        "1. Review the Grafana dashboard: [link]",
        "2. Check the P95/P99 values in the table below",
        "3. If confident, merge this PR",
        "4. Monitor pods for 24 hours post-merge",
    ])
    
    return "\n".join(lines)
```

### 5.5 Key API Endpoints (Dashboard)

```
RECOMMENDATIONS
  GET    /api/recommendations                    # All current recommendations
         ?namespace=sample-app                   # Filter by namespace
         ?action=reduce                          # Filter by action type
         ?minSavings=1000                        # Filter by minimum savings
  
POD METRICS
  GET    /api/pods/:name/metrics                 # Time-series for specific pod
         ?resource=cpu                           # cpu or memory
         ?window=7d                              # Time window
         ?step=1h                                # Resolution

ANALYSIS
  POST   /api/analysis/trigger                   # Trigger manual analysis run
  GET    /api/analysis/history                   # Past analysis runs
  GET    /api/analysis/:id                       # Specific run results

SAVINGS
  GET    /api/savings/summary                    # Total savings breakdown
         ?provider=aws                           # Cloud provider for pricing
         ?instanceType=m5.xlarge                 # Instance type for pricing

HEALTH
  GET    /api/health                             # Health check
```

---

## 6. Database Design & Choice

### 6.1 Comparison Matrix — Which Database?

| Criteria | TimescaleDB | PostgreSQL (plain) | InfluxDB | MongoDB | Cassandra | CosmosDB |
|---|---|---|---|---|---|---|
| **Time-series queries** | Purpose-built. Hypertables, continuous aggregates, compression | Functional but slow. No auto-partitioning by time | Purpose-built. Flux language | Basic timestamp queries | Write-optimised, but poor for aggregates | Adequate with TTL |
| **"P95 CPU over 7 days per pod"** | < 50ms (continuous aggregate pre-computed) | 2-5 seconds (full table scan without partitioning) | < 100ms (native) | 1-3 seconds (aggregation pipeline) | Not designed for percentiles | Varies |
| **Relational data** | Full PostgreSQL (users, clusters, configs) | Full PostgreSQL | None — separate DB needed | Document model (no JOINs) | Limited | SQL API possible |
| **Compression** | 90-95% compression for time-series | Manual with pg_repack | ~90% | None standard | Decent | Automatic |
| **Continuous Aggregates** | Native (materialised views on hypertables) | Manual with cronjobs | Continuous queries (CQ) | Manual | Manual | N/A |
| **SQL Support** | Full PostgreSQL SQL | Full SQL | Flux (custom language) | MQL (no SQL) | CQL (SQL-like, limited) | SQL API |
| **Retention Policies** | Native (drop_chunks) | Manual partition drops | Native | TTL indexes | TTL | TTL |
| **Operational Cost** | Low (PostgreSQL extension) | Low | Medium (separate binary) | Medium | High (3+ nodes) | High (cloud-only) |
| **ORM Compatibility** | Prisma, SQLAlchemy, Drizzle — all work | Same | None standard | Mongoose | Limited | Azure SDK |

### VERDICT: TimescaleDB (PostgreSQL Extension)

**Why TimescaleDB wins for KubeThrifty:**

1. **Time-series IS our core data:** "What was the P95 CPU usage for pod X over the last 7 days in 1-hour buckets?" is our most critical query. Plain PostgreSQL scans millions of rows for this. TimescaleDB's hypertable auto-partitions by time and the continuous aggregate pre-computes P95 rollups hourly — the query returns in < 50ms.

2. **One database for everything:** TimescaleDB IS PostgreSQL — it's an extension, not a separate database. Our relational data (users, clusters, analysis runs, recommendations) lives in regular PostgreSQL tables. Our time-series metric data lives in hypertables. Same connection, same ORM, same migrations, same backups.

3. **Continuous aggregates solve our rollup problem:** We collect metric snapshots every 15 seconds. That's 5,760 data points per pod per day. For a 7-day P95, that's 40,320 points to aggregate. Continuous aggregates materialise hourly P50/P95/P99 automatically — our dashboard query reads 168 pre-computed rows instead of 40,320 raw rows.

4. **Compression saves 90% storage:** After 24 hours, we compress old metric chunks. Raw data for 100 pods at 15-second intervals = ~500MB/month. Compressed = ~50MB/month. This is critical for keeping hosting costs low.

5. **Retention policies automate cleanup:** `SELECT remove_compression_policy('metric_snapshots', INTERVAL '90 days')` — old data is automatically dropped. No cron jobs, no manual maintenance.

**Why NOT plain PostgreSQL?**
> "Plain PostgreSQL CAN store time-series data, but without hypertable partitioning, a query like 'P95 CPU for pod X over 7 days' scans the entire table — 40,000+ rows per pod. TimescaleDB's hypertable auto-partitions by time (one chunk per day) and continuous aggregates pre-compute hourly rollups. The same query runs 100x faster. Since TimescaleDB IS a PostgreSQL extension, I don't sacrifice anything — I still get full SQL, JOINs, ORM support, and ACID transactions."

**Why NOT InfluxDB?**
> "InfluxDB excels at time-series but has no relational model. I'd need InfluxDB for metrics PLUS PostgreSQL for users/configs/recommendations — two databases, two connections, two backup strategies. TimescaleDB gives me both in one. Also, InfluxDB uses Flux (a custom query language) instead of SQL — higher learning curve for contributors."

**Why NOT MongoDB?**
> "Percentile aggregations ($percentile) in MongoDB's pipeline are significantly slower than TimescaleDB's continuous aggregates for time-series data. MongoDB also has no native time-based partitioning or compression. Our metric data is inherently columnar and time-ordered — a time-series database is the right tool."

**Why NOT Cassandra?**
> "Cassandra is designed for write-heavy, partition-key-based reads across continents. Our write volume is modest (100 pods × 1 data point every 15 seconds = 400 writes/minute). Our query pattern is complex aggregation (P95 over time ranges), which Cassandra handles poorly. Cassandra requires a minimum 3-node cluster for production — overkill for our scale."

### 6.2 Schema Design (TimescaleDB)

```sql
-- Enable TimescaleDB + the Toolkit (Toolkit ships in the timescaledb-ha image — see §B).
-- Without the Toolkit, percentile_agg/approx_percentile do not exist and this migration fails.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

-- =============================================
-- REGULAR POSTGRESQL TABLES (relational data)
-- =============================================

-- Monitored Kubernetes clusters
CREATE TABLE clusters (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    prometheus_url  VARCHAR(500) NOT NULL,
    kube_context    VARCHAR(200),
    cloud_provider  VARCHAR(20),                     -- aws, gcp, azure, on-prem
    instance_type   VARCHAR(50),                     -- m5.xlarge, e2-standard-4
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Analysis run history
CREATE TABLE analysis_runs (
    id              SERIAL PRIMARY KEY,
    cluster_id      INT REFERENCES clusters(id) ON DELETE CASCADE,
    status          VARCHAR(20) DEFAULT 'running',   -- running, completed, failed
    namespace_filter VARCHAR(200),                    -- null = all namespaces
    window_hours    INT DEFAULT 168,                  -- 7 days default
    pods_analysed   INT DEFAULT 0,
    recommendations_count INT DEFAULT 0,
    total_savings_inr DECIMAL(12,2) DEFAULT 0,
    pr_url          VARCHAR(500),                     -- URL of auto-created PR
    error_message   TEXT,
    started_at      TIMESTAMP DEFAULT NOW(),
    completed_at    TIMESTAMP,
    triggered_by    VARCHAR(50) DEFAULT 'scheduled'   -- scheduled, manual, webhook
);

-- Recommendations (output of each analysis run)
CREATE TABLE recommendations (
    id                  SERIAL PRIMARY KEY,
    run_id              INT REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id          INT REFERENCES clusters(id),
    pod_name            VARCHAR(200) NOT NULL,
    container_name      VARCHAR(200) NOT NULL,
    namespace           VARCHAR(200) NOT NULL,
    resource_type       VARCHAR(10) NOT NULL,          -- cpu, memory
    
    current_request     DOUBLE PRECISION NOT NULL,
    current_limit       DOUBLE PRECISION NOT NULL,
    actual_p50          DOUBLE PRECISION NOT NULL,
    actual_p95          DOUBLE PRECISION NOT NULL,
    actual_p99          DOUBLE PRECISION NOT NULL,
    actual_max          DOUBLE PRECISION NOT NULL,
    actual_mean         DOUBLE PRECISION NOT NULL,
    actual_stddev       DOUBLE PRECISION NOT NULL,
    
    recommended_request DOUBLE PRECISION NOT NULL,
    recommended_limit   DOUBLE PRECISION NOT NULL,
    
    savings_monthly_inr DECIMAL(10,2) DEFAULT 0,
    savings_percentage  DECIMAL(5,2) DEFAULT 0,
    confidence          VARCHAR(10) NOT NULL,           -- high, medium, low
    action              VARCHAR(20) NOT NULL,            -- reduce, increase, keep, needs_review
    reason              TEXT,
    
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_rec_run ON recommendations(run_id);
CREATE INDEX idx_rec_pod ON recommendations(pod_name, namespace);
CREATE INDEX idx_rec_action ON recommendations(action);

-- =============================================
-- TIMESCALEDB HYPERTABLE (time-series metrics)
-- =============================================

-- Raw metric snapshots (collected from Prometheus)
CREATE TABLE metric_snapshots (
    time            TIMESTAMPTZ NOT NULL,
    cluster_id      INT NOT NULL,
    pod_name        VARCHAR(200) NOT NULL,
    container_name  VARCHAR(200) NOT NULL,
    namespace       VARCHAR(200) NOT NULL,
    resource_type   VARCHAR(10) NOT NULL,            -- cpu, memory
    actual_value    DOUBLE PRECISION NOT NULL,        -- Actual usage
    requested_value DOUBLE PRECISION,                 -- Requested (from kube-state-metrics)
    limit_value     DOUBLE PRECISION                  -- Limit (from kube-state-metrics)
);

-- Convert to hypertable (auto-partition by time, 1 day per chunk)
SELECT create_hypertable('metric_snapshots', 'time',
    chunk_time_interval => INTERVAL '1 day'
);

-- Indexes for common query patterns
CREATE INDEX idx_metrics_pod_time 
    ON metric_snapshots (pod_name, namespace, resource_type, time DESC);

-- =============================================
-- CONTINUOUS AGGREGATES (pre-computed rollups)
-- =============================================

-- Hourly percentile SKETCH per pod (auto-maintained by TimescaleDB).
-- Correct Toolkit pattern: store the partializable `percentile_agg` SKETCH in the
-- continuous aggregate, then apply the `approx_percentile` accessor at READ time.
-- (Putting approx_percentile() directly in the cagg is the common mistake — the
--  accessor is not partializable, so you store the sketch and read percentiles from it.)
CREATE MATERIALIZED VIEW hourly_pod_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    cluster_id,
    pod_name,
    container_name,
    namespace,
    resource_type,
    percentile_agg(actual_value) AS pct_sketch,   -- the partializable sketch
    max(actual_value)            AS max_value,
    avg(actual_value)            AS mean_value,
    stddev(actual_value)         AS stddev_value,
    avg(requested_value)         AS avg_requested,
    count(*)                     AS sample_count
FROM metric_snapshots
GROUP BY bucket, cluster_id, pod_name, container_name, namespace, resource_type;

-- Read P50/P95/P99 from the stored sketch at query time (and roll up across hours):
--   SELECT pod_name,
--          approx_percentile(0.50, rollup(pct_sketch)) AS p50,
--          approx_percentile(0.95, rollup(pct_sketch)) AS p95,
--          approx_percentile(0.99, rollup(pct_sketch)) AS p99
--   FROM hourly_pod_stats
--   WHERE bucket > NOW() - INTERVAL '7 days'
--   GROUP BY pod_name;

-- Refresh policy: update every hour, look back 2 hours for late data
SELECT add_continuous_aggregate_policy('hourly_pod_stats',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

-- =============================================
-- RETENTION + COMPRESSION POLICIES
-- =============================================

-- Compress chunks older than 1 day (90%+ storage savings)
ALTER TABLE metric_snapshots SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'pod_name, namespace, resource_type',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('metric_snapshots', INTERVAL '1 day');

-- Drop raw data older than 30 days (continuous aggregates retained)
SELECT add_retention_policy('metric_snapshots', INTERVAL '30 days');
```

### 6.3 Key Queries

```sql
-- 1. Dashboard: Current waste per pod (read from latest recommendations)
SELECT r.pod_name, r.namespace, r.resource_type,
       r.current_request, r.recommended_request,
       r.savings_percentage, r.savings_monthly_inr,
       r.confidence, r.action
FROM recommendations r
WHERE r.run_id = (SELECT MAX(id) FROM analysis_runs WHERE status = 'completed')
  AND r.action = 'reduce'
ORDER BY r.savings_monthly_inr DESC;

-- 2. Time-series chart: hourly P95 CPU for a specific pod (accessor on the stored sketch)
SELECT bucket, approx_percentile(0.95, pct_sketch) AS p95, avg_requested, max_value
FROM hourly_pod_stats
WHERE pod_name = 'api-gateway'
  AND namespace = 'sample-app'
  AND resource_type = 'cpu'
  AND bucket > NOW() - INTERVAL '7 days'
ORDER BY bucket;

-- 3. Total savings summary
SELECT
    SUM(savings_monthly_inr) AS total_savings_inr,
    COUNT(*) FILTER (WHERE action = 'reduce') AS over_provisioned,
    COUNT(*) FILTER (WHERE action = 'increase') AS under_provisioned,
    COUNT(*) FILTER (WHERE action = 'keep') AS well_sized,
    COUNT(*) FILTER (WHERE action = 'needs_review') AS needs_review
FROM recommendations
WHERE run_id = (SELECT MAX(id) FROM analysis_runs WHERE status = 'completed');

-- 4. Most wasteful namespaces
SELECT namespace,
       SUM(savings_monthly_inr) AS total_savings,
       COUNT(*) AS pod_count,
       AVG(savings_percentage) AS avg_waste_pct
FROM recommendations
WHERE run_id = (SELECT MAX(id) FROM analysis_runs WHERE status = 'completed')
  AND action = 'reduce'
GROUP BY namespace
ORDER BY total_savings DESC;

-- 5. Historical trend: is a pod's usage growing?
SELECT bucket,
       approx_percentile(0.95, pct_sketch) AS cpu_p95
FROM hourly_pod_stats
WHERE pod_name = 'order-service'
  AND resource_type = 'cpu'
  AND bucket > NOW() - INTERVAL '30 days'
ORDER BY bucket;
-- If the trendline is increasing → don't right-size DOWN (it'll just need more soon)
```

---

## 7. Caching & Messaging — Redis vs Kafka vs RabbitMQ

### 7.1 Comparison for THIS Project

| Criteria | Redis 8.x / Valkey 9.1 | Apache Kafka | RabbitMQ | ZooKeeper |
|---|---|---|---|---|
| **Dashboard Cache** | Native (GET/SET with TTL) | Not a cache | Not a cache | Not a cache |
| **Analysis Job Queue** | Redis Streams (4 runs/day is trivial) | 3+ brokers for 4 msgs/day → absurd | Works, but separate service | Not a queue |
| **Prometheus Query Cache** | Perfect (cache PromQL results, TTL 5 min) | N/A | N/A | N/A |
| **Rate Limiting** | INCR + EXPIRE (GitHub API calls) | Not designed for this | Not designed for this | N/A |
| **Operational Complexity** | Single binary, 50MB RAM | 3 JVM brokers + ZK, 4GB+ | Erlang runtime, 200MB | Used BY Kafka |
| **K8s Deployment** | 1 pod, 128MB memory | 3+ pods, 4GB+ total | 1 pod, 256MB | N/A |

### VERDICT: Redis Streams (pin Redis 8.x, or Valkey 9.1 for the BSD licence)

**Purpose 1 — Dashboard API Cache**
```
Key: "dashboard:recs:{clusterId}"        → Latest recommendations JSON (TTL: 6h)
Key: "dashboard:savings:{clusterId}"     → Savings summary (TTL: 6h)
Key: "pod:metrics:{podName}:{window}"    → Cached time-series chart data (TTL: 15 min)
```

**Purpose 2 — Prometheus Query Result Cache**
```
The Python analyser makes ~50 PromQL queries per analysis run.
Some queries (like kube_pod_container_resource_requests) return
nearly identical results across runs.

Key: "promql:{queryHash}"               → Cached Prometheus response (TTL: 5 min)
Key: "promql:ratelimit"                 → Throttle PromQL queries to avoid overloading Prometheus
```

**Purpose 3 — Analysis Job Queue (Redis Streams)**
```
Stream: "analysis-jobs"

Producer: GitHub Actions cron OR dashboard manual trigger
  XADD analysis-jobs * clusterId 1 namespace sample-app window 7d trigger scheduled

Consumer: Python analyser (long-running process)
  XREADGROUP GROUP analysers worker-1 COUNT 1 BLOCK 30000 STREAMS analysis-jobs >

Why queue at all? (even for 4 runs/day)
  - Prevents concurrent analysis runs (two triggers at once → race condition)
  - Enables retry on failure (unacked messages stay in pending)
  - Dashboard can show "analysis queued" / "analysis running" status
  - Future: allows scaling to multiple clusters without code changes
```

**Purpose 4 — Analysis Status (Pub/Sub)**
```
Channel: "analysis:status:{clusterId}"

Publisher: Python analyser emits progress:
  "STARTED" → "COLLECTING_METRICS" → "COMPUTING_P95" → "GENERATING_RECS" → "COMPLETED"

Subscriber: Next.js API route subscribes → SSE to dashboard
  Dashboard shows real-time progress bar during analysis
```

**Why NOT Kafka?**
> "We run 4 analysis jobs per day — one every 6 hours. Kafka requires a minimum of 3 broker instances plus ZooKeeper or KRaft for production, consuming 4GB+ RAM. Using Kafka for 4 messages per day is like chartering a freight ship to deliver a letter. Redis Streams gives me consumer groups, message acknowledgment, and concurrency control with the same Redis instance I already use for caching. If KubeThrifty scaled to a multi-tenant SaaS analysing 10,000 clusters simultaneously, Kafka's partition model would be justified."

**Why NOT RabbitMQ?**
> "RabbitMQ is a reasonable message broker, but I already run Redis for caching, Prometheus query caching, and analysis status pub/sub. Adding RabbitMQ means another container in the Helm chart, another service for contributors to understand, and another thing to monitor — all for 4 messages per day. Redis Streams handles it trivially."

### 7.2 KEDA — Scale the Analyser to Zero (the tool practices what it preaches)

The analyser does real work only when a job is queued (4–6 times/day). Running it as an always-on Deployment wastes exactly the resources KubeThrifty exists to reclaim. So instead of a fixed CronJob, the analyser is a Deployment driven by **KEDA's `redis-streams` scaler**, scaling **0 → N** on the Pending Entries List of the `analysis-jobs` stream:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: kubethrifty-analyser
spec:
  scaleTargetRef:
    name: kubethrifty-analyser           # the analyser Deployment
  minReplicaCount: 0                       # scale to ZERO when idle — no wasted CPU/RAM
  maxReplicaCount: 4
  cooldownPeriod: 300
  triggers:
    - type: redis-streams
      metadata:
        addressFromEnv: REDIS_ADDRESS
        stream: analysis-jobs
        consumerGroup: analysers
        pendingEntriesCount: "5"           # scale up when XPENDING exceeds this
```

Why this is a strong, on-theme choice (interview answer):
> "The analyser is bursty — idle most of the day, busy for a few minutes per run. KEDA's Redis Streams scaler scales it to zero when the `analysis-jobs` stream is empty and spins up workers when jobs are pending. A scheduler still `XADD`s a job every 6 hours, but between runs the analyser consumes zero resources. It's a small thing, but it's the whole philosophy of the project applied to the project itself — and scale-to-zero is the most aggressive form of right-sizing there is."

This is also why a queue exists for "only 4 jobs/day": KEDA scales on the queue's pending count, and the queue gives at-least-once retry and prevents concurrent runs (§17).

---

## 8. Design Patterns Used

### 8.1 Strategy Pattern (Cloud Pricing Calculators)

```python
class PricingStrategy(ABC):
    @abstractmethod
    def cpu_per_core_hour(self, instance_type: str) -> float: ...
    @abstractmethod
    def memory_per_gb_hour(self, instance_type: str) -> float: ...
    @abstractmethod
    def currency_symbol(self) -> str: ...

class AWSPricing(PricingStrategy):
    PRICES = {
        'm5.xlarge': {'cpu': 0.048, 'memory': 0.006},  # per core-hour, per GB-hour
        'm5.2xlarge': {'cpu': 0.044, 'memory': 0.0055},
    }
    def cpu_per_core_hour(self, instance_type): return self.PRICES[instance_type]['cpu']
    def memory_per_gb_hour(self, instance_type): return self.PRICES[instance_type]['memory']
    def currency_symbol(self): return '$'

class GCPPricing(PricingStrategy): ...
class AzurePricing(PricingStrategy): ...

# Usage: plug in any cloud provider without modifying analyser
pricing = {'aws': AWSPricing(), 'gcp': GCPPricing(), 'azure': AzurePricing()}
```

### 8.2 Observer Pattern (Analysis Event Propagation)

```python
class AnalysisEventBus:
    def __init__(self):
        self.listeners = defaultdict(list)
    
    def on(self, event: str, handler): self.listeners[event].append(handler)
    
    async def emit(self, event: str, data):
        for handler in self.listeners[event]:
            await handler(data)

# Registration
bus.on('analysis:completed', save_to_timescaledb)
bus.on('analysis:completed', cache_recommendations)
bus.on('analysis:completed', publish_status_update)
bus.on('analysis:completed', create_github_pr)
bus.on('analysis:completed', update_grafana_annotations)
```

### 8.3 Builder Pattern (K8s Manifest Generation)

```python
class ManifestBuilder:
    def __init__(self, original_manifest: dict):
        self.manifest = copy.deepcopy(original_manifest)
    
    def set_cpu_request(self, container: str, value: str):
        self._find_container(container)['resources']['requests']['cpu'] = value
        return self
    
    def set_memory_request(self, container: str, value: str):
        self._find_container(container)['resources']['requests']['memory'] = value
        return self
    
    def set_cpu_limit(self, container: str, value: str):
        self._find_container(container)['resources']['limits']['cpu'] = value
        return self
    
    def set_memory_limit(self, container: str, value: str):
        self._find_container(container)['resources']['limits']['memory'] = value
        return self
    
    def build(self) -> dict:
        return self.manifest
```

### 8.4 Pipeline Pattern (Analysis Steps)

```python
class AnalysisPipeline:
    def __init__(self):
        self.steps = []
    
    def add_step(self, step: PipelineStep):
        self.steps.append(step)
        return self
    
    async def execute(self, context: AnalysisContext):
        for step in self.steps:
            context = await step.run(context)
            if context.should_abort:
                break
        return context

# Composable pipeline
pipeline = AnalysisPipeline()
pipeline.add_step(ValidateClusterConnectivity())
pipeline.add_step(CollectCPUMetrics())
pipeline.add_step(CollectMemoryMetrics())
pipeline.add_step(ComputePercentiles())
pipeline.add_step(GenerateRecommendations())
pipeline.add_step(CalculateSavings())
pipeline.add_step(PersistResults())
pipeline.add_step(CreateGitHubPR())
pipeline.add_step(NotifyDashboard())
```

### 8.5 Circuit Breaker (Prometheus + GitHub API Calls)

```python
from circuitbreaker import circuit

@circuit(failure_threshold=3, recovery_timeout=60)
def query_prometheus(query: str, start: str, end: str, step: str):
    response = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range", params={
        'query': query, 'start': start, 'end': end, 'step': step
    })
    response.raise_for_status()
    return response.json()

# If Prometheus is down, circuit opens → analyser fails fast
# instead of hanging on timeouts for every single query
```

### 8.6 Template Method (Recommendation Report Formats)

```python
class ReportGenerator(ABC):
    def generate(self, recs: List[ResourceRecommendation]) -> str:
        header = self.format_header(recs)
        body = self.format_body(recs)
        footer = self.format_footer(recs)
        return f"{header}\n{body}\n{footer}"
    
    @abstractmethod
    def format_header(self, recs): ...
    @abstractmethod
    def format_body(self, recs): ...
    @abstractmethod
    def format_footer(self, recs): ...

class MarkdownReport(ReportGenerator): ...    # For GitHub PR descriptions
class SlackReport(ReportGenerator): ...       # For Slack notifications
class JSONReport(ReportGenerator): ...        # For API responses
```

---

## 9. Docker & Kubernetes Deployment Strategy

### 9.1 Docker Compose (Local Development Without K8s)

> ⚠️ **Rev 3 correction (defect C4).** Rev 2's §2.1 said "never `latest`" and then this very file used `prom/prometheus:latest` and `grafana/grafana:latest`. Tags below are corrected; fill in the exact current patch versions from §29 at build time and let Renovate keep them moving. A pinning policy your own compose file violates is the easiest thing in the world for an interviewer to catch — they *will* open this file.

```yaml
version: '3.8'

services:
  dashboard:
    build: ./dashboard
    ports:
      - "3000:3000"
    depends_on:
      timescaledb:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@timescaledb:5432/kubethrifty
      - REDIS_URL=redis://redis:6379
      - PROMETHEUS_URL=http://prometheus:9090

  analyser:
    build: ./analyser
    depends_on:
      timescaledb:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@timescaledb:5432/kubethrifty
      - REDIS_URL=redis://redis:6379
      - PROMETHEUS_URL=http://prometheus:9090
      - GITHUB_TOKEN=${GITHUB_TOKEN}
      - ANALYSIS_WINDOW=7d
      - CLOUD_PROVIDER=aws
      - INSTANCE_TYPE=m5.xlarge
    command: ["python", "-m", "src.main", "--mode", "worker"]

  timescaledb:
    image: timescale/timescaledb-ha:pg18.4-ts2.28.1-all
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_DB=kubethrifty
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
    volumes:
      - timescale_data:/home/postgres/pgdata/data
      - ./analyser/sql/init.sql:/docker-entrypoint-initdb.d/01-init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: valkey/valkey:9-alpine      # or redis:8-alpine — pin the minor (§B)
    ports:
      - "6379:6379"
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s

  prometheus:
    image: prom/prometheus:v3.x.y      # PIN the exact 3.x patch — never :latest (§21.2b)
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus-local.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:13.0.x      # PIN the exact patch — never :latest (§21.2b)
    ports:
      - "3001:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources

volumes:
  timescale_data:
  grafana_data:
```

### 9.2 Helm Chart — values.yaml

```yaml
# charts/kubethrifty/values.yaml

dashboard:
  replicaCount: 2
  image:
    repository: yourdockerhub/kubethrifty-dashboard
    tag: latest
  resources:
    requests:
      cpu: 100m        # Right-sized by KubeThrifty itself!
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
  service:
    type: ClusterIP
    port: 3000
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: kubethrifty.example.com
        paths:
          - path: /
            pathType: Prefix
    tls:
      - secretName: kubethrifty-tls
        hosts:
          - kubethrifty.example.com

analyser:
  schedule: "0 */6 * * *"     # a tiny enqueuer CronJob XADDs a job on this schedule;
                              # KEDA's redis-streams scaler then scales the analyser
                              # worker Deployment 0->N to consume it (see §7.2). The
                              # worker itself runs at minReplicas=0 when idle.
  image:
    repository: yourdockerhub/kubethrifty-analyser
    tag: latest
  resources:
    requests:
      cpu: 200m
      memory: 512Mi
    limits:
      cpu: 1000m
      memory: 1Gi
  config:
    analysisWindow: "7d"
    safetyMarginRequest: 1.20
    safetyMarginLimit: 1.50
    minCpuMillicores: 50
    minMemoryMB: 64
    autoCreatePR: true

timescaledb:
  enabled: true               # Set false to use external DB
  persistence:
    size: 10Gi
  resources:
    requests:
      cpu: 250m
      memory: 512Mi

redis:
  enabled: true
  architecture: standalone
  resources:
    requests:
      cpu: 50m
      memory: 128Mi

prometheus:
  # URL of existing Prometheus in the cluster
  url: "http://prometheus-kube-prometheus-prometheus.monitoring:9090"

github:
  token:
    secretName: kubethrifty-github
    secretKey: token
  repo: "your-org/k8s-manifests"
  manifestPath: "deployments/"

cloudPricing:
  provider: aws
  instanceType: m5.xlarge
```

### 9.3 Kubernetes Deployment — The Self-Referential Demo

```
THE KILLER DEMO MOMENT:

1. Deploy KubeThrifty to K8s with intentionally over-provisioned resources:
   dashboard: 2 CPU / 2 GB requested
   analyser:  2 CPU / 2 GB requested

2. KubeThrifty analyses its OWN pods along with the sample app

3. Dashboard shows:
   "kubethrifty-dashboard: using 80m CPU / 180 MB → recommended 100m / 256 MB"
   "kubethrifty-analyser:  using 200m CPU / 400 MB → recommended 250m / 512 MB"

4. Auto-PR updates KubeThrifty's OWN Helm values.yaml:
   "This PR right-sizes KubeThrifty itself — saving ₹5,000/month"

5. Interview quote: "KubeThrifty right-sized itself. It analysed its own
   pods, found 90% CPU waste, and auto-created a PR updating its own
   Helm chart. That's the kind of self-referential demo that shows
   I understand the full cycle from metrics to remediation."
```

---

## 10. CI/CD Pipeline — GitHub Actions

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  test-analyser:
    runs-on: ubuntu-latest
    services:
      timescaledb:
        image: timescale/timescaledb-ha:pg18.4-ts2.28.1-all
        env:
          POSTGRES_DB: kubethrifty_test
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        ports: [5432:5432]
        options: --health-cmd pg_isready --health-interval 10s
      redis:
        image: valkey/valkey:9-alpine      # or redis:8-alpine — pin the minor (§B)
        ports: [6379:6379]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - run: cd analyser && pip install -r requirements.txt -r requirements-dev.txt
      - run: cd analyser && pytest tests/ -v --cov=src --cov-report=xml

  test-dashboard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '24'
      - run: cd dashboard && npm ci && npm run lint && npm run test

  build-and-push:
    needs: [test-analyser, test-dashboard]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      - run: |
          docker build -t ${{ secrets.DOCKER_USERNAME }}/kubethrifty-dashboard:${{ github.sha }} ./dashboard
          docker push ${{ secrets.DOCKER_USERNAME }}/kubethrifty-dashboard:${{ github.sha }}
          docker build -t ${{ secrets.DOCKER_USERNAME }}/kubethrifty-analyser:${{ github.sha }} ./analyser
          docker push ${{ secrets.DOCKER_USERNAME }}/kubethrifty-analyser:${{ github.sha }}

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to K8s
        run: |
          helm upgrade --install kubethrifty ./charts/kubethrifty \
            --set dashboard.image.tag=${{ github.sha }} \
            --set analyser.image.tag=${{ github.sha }} \
            --namespace kubethrifty \
            --create-namespace
```

```yaml
# .github/workflows/analysis.yml — Scheduled analysis run
name: Scheduled Analysis

on:
  schedule:
    - cron: '0 */6 * * *'      # Every 6 hours
  workflow_dispatch:             # Manual trigger

jobs:
  analyse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - run: pip install -r analyser/requirements.txt
      - name: Run analysis
        run: |
          cd analyser
          python -m src.main \
            --prometheus-url ${{ secrets.PROMETHEUS_URL }} \
            --database-url ${{ secrets.DATABASE_URL }} \
            --github-token ${{ secrets.GITHUB_TOKEN }} \
            --github-repo ${{ secrets.K8S_MANIFESTS_REPO }} \
            --window 7d \
            --auto-pr
```

---

## 11. Monitoring & Observability

### Grafana Dashboard (4 Panels for README Screenshots)

**Panel 1: Resource Waste Heatmap**
- Each cell = pod, colour = waste percentage
- Green (< 20%), yellow (20-50%), orange (50-80%), red (> 80%)
- "At a glance, I can see api-gateway is 95% wasted (red) while payment-processor is well-sized (green)"

**Panel 2: Actual vs Requested CPU Over Time**
- Time-series: blue line = actual P95, red dashed line = current request
- Shaded area between = waste
- "The gap between the blue line and red line IS the money you're wasting"

**Panel 3: Monthly Savings Trend**
- Bar chart: total projected savings per analysis run over 30 days
- Shows if right-sizing is being adopted (savings should decrease as PRs are merged)

**Panel 4: KubeThrifty Operational Metrics (Self-monitoring)**
- Analysis run duration (P50, P95)
- Prometheus query latency
- Failed analysis runs
- Dashboard API response time

### The "Scaling Decision" to Document

> **Scaling Decision: TimescaleDB Continuous Aggregates over Real-Time Percentile Computation**
>
> **Problem:** The dashboard needed P95 CPU/memory per pod over 7 days. Computing this at request time from 40,000+ raw metric rows per pod took 2-5 seconds — unacceptable for a dashboard.
>
> **Option A — Real-time computation:** Query raw metric_snapshots table on every dashboard load. Pros: always fresh. Cons: 2-5 second latency, heavy DB load.
>
> **Option B — Application-level cache:** Compute once, cache in Redis for 1 hour. Pros: fast reads. Cons: stale data up to 1 hour, cache invalidation complexity.
>
> **Option C — TimescaleDB continuous aggregates:** Materialised view that auto-recomputes hourly P50/P95/P99 from raw data. Pros: always fresh (within 1 hour), zero application code, reads from pre-computed table (< 50ms). Cons: 1-hour staleness (acceptable for hourly metric analysis).
>
> **Decision:** Option C. Continuous aggregates are maintained by TimescaleDB itself — no cron jobs, no cache invalidation bugs, no application code. The dashboard reads from the `hourly_pod_stats` materialized view, which has 168 rows per pod for a 7-day window (one per hour) instead of 40,320 raw rows. Query time dropped from 3 seconds to 40 milliseconds.
>
> **When I'd add Option B:** If the dashboard had 100+ concurrent users hitting the same endpoint, I'd add Redis caching on top of the continuous aggregate for sub-10ms responses. Currently unnecessary for a tool with < 10 concurrent users.

---

## 12. Interview Prep — Top Questions & Answers (DevOps-Focused)

### Kubernetes & Infrastructure

**Q1: "Walk me through the entire system."**

> "I deploy a sample microservices app to Kubernetes with intentionally over-provisioned resources — 5.5 CPU and 6.5 GB requested total. Prometheus scrapes CPU and memory metrics from cAdvisor every 15 seconds, and kube-state-metrics exposes the requested/limit values from the K8s API. Every 6 hours, a Python analyser queries Prometheus's HTTP API, loads 7 days of data into pandas, computes P50/P95/P99 percentiles per pod, and compares actual usage against requested resources. It generates right-sizing recommendations with safety margins — 20% above P95 for requests, 50% above P99 for limits — and calculates monthly savings based on cloud provider pricing. Results go to TimescaleDB. The Next.js dashboard visualises waste per pod with a heatmap, time-series charts, and a savings calculator. If auto-PR is enabled, the analyser generates modified K8s manifests and creates a GitHub PR with a detailed before/after table and projected savings."

**Q2: "Why Prometheus and not Datadog or CloudWatch?"**

> "Three reasons. First, Prometheus is the CNCF standard for K8s monitoring — it integrates natively with kube-state-metrics and cAdvisor, which expose exactly the metrics I need (resource requests vs actual usage). Second, it's free and self-hosted — no per-host licensing that scales with cluster size. Third, PromQL is purpose-built for time-series queries — computing P95 CPU over a time window is a single expression. Datadog would work but costs $23/host/month and creates vendor lock-in. CloudWatch has coarser granularity (1-minute vs Prometheus's 15-second scrape interval) and no native K8s resource request metrics."

**Q3: "Explain the PromQL queries you use."**

> "Two critical queries. First: `rate(container_cpu_usage_seconds_total[5m])` gives me per-second CPU usage rate averaged over 5-minute windows — this is the actual CPU consumed by each container. Second: `kube_pod_container_resource_requests{resource='cpu'}` gives me the requested CPU from the pod spec. By comparing these two metrics across a 7-day range query, I can calculate the gap between what's requested and what's actually used. The `rate()` function is essential because `container_cpu_usage_seconds_total` is a monotonically increasing counter — without `rate()`, the raw value is meaningless."

**Q4: "How do you handle bursty workloads?"**

> "Bursty workloads are the hardest to right-size. My recommendation engine handles them in three ways. First, I use P95 not P50 for requests — this captures the burst peaks while ignoring steady-state noise. Second, I add a 20% safety margin on top of P95, so even if the next burst is 20% higher, the pod has headroom. Third, I compute the standard deviation — if stddev/mean exceeds 50%, I flag the recommendation as NEEDS_REVIEW with low confidence. The dashboard shows a special 'highly variable' badge so the human reviewer knows this pod needs manual judgment. For example, order-service bursts from 50m to 800m CPU — the P95 is 600m, so I recommend 720m request with a 'medium confidence' flag."

**Q5: "How would you scale this to 1,000 clusters?"**

> "Three changes. First, the analyser becomes a distributed job system — each cluster's analysis is an independent task. I'd move from Redis Streams to Kafka, partitioned by cluster ID, with a pool of analyser workers. Second, TimescaleDB gets multi-node (Timescale Cloud or self-hosted with distributed hypertables) to handle the increased write volume. Third, the dashboard adds cluster selection and caching — Redis caches per-cluster recommendations so switching between clusters is instant. The architecture is already prepared for this because the analyser is stateless — it reads from Prometheus, computes in memory, and writes to TimescaleDB. Adding more clusters means more instances of the same stateless worker."

### Database & Data

**Q6: "Why TimescaleDB over plain PostgreSQL?"**

> "Our core query is 'P95 CPU for pod X over 7 days.' In plain PostgreSQL, this scans 40,000+ rows per pod, taking 2-5 seconds. TimescaleDB's hypertable auto-partitions by time — each day is a separate chunk. The query only touches the 7 relevant chunks. But the real win is continuous aggregates — a materialised view that auto-recomputes hourly P50/P95/P99. The dashboard reads from this pre-computed view in 40 milliseconds instead of 3 seconds. TimescaleDB also compresses chunks older than 24 hours with 90% storage savings, and retention policies auto-drop data older than 30 days. All of this is built-in — zero application code."

**Q7: "Why not InfluxDB for time-series?"**

> "InfluxDB is excellent for time-series but it has no relational model. I'd need InfluxDB for metrics PLUS PostgreSQL for users, clusters, recommendations, and analysis history — two databases, two connection pools, two ORMs. TimescaleDB is a PostgreSQL extension — I use one database for both time-series hypertables and regular relational tables. Same SQL, same ORM, same migrations, same backups. The operational simplicity of one database outweighs InfluxDB's marginally better time-series performance."

### DevOps & CI/CD

**Q8: "Explain your CI/CD pipeline."**

> "Three GitHub Actions workflows. First, CI: on every PR, lint Python (ruff), lint TypeScript (ESLint), run Python unit tests with pytest (recommendation engine, cost calculator), run Next.js tests, and build Docker images to verify they compile. Second, Deploy: on merge to main, build and push Docker images to DockerHub tagged with the commit SHA, then Helm upgrade to the K8s cluster with the new image tags. Third, Scheduled Analysis: a cron workflow runs every 6 hours, triggers the Python analyser against the live Prometheus, and if recommendations exist, auto-creates a PR on the K8s manifests repo. This creates a full GitOps loop — code changes flow through CI/CD, and infrastructure changes flow through auto-PRs."

**Q9: "What's a scaling decision you made?"**

> [Use the documented TimescaleDB continuous aggregates decision from Section 11]

**Q10: "Why Redis over Kafka?"**

> "We run 4 analysis jobs per day. Kafka requires 3+ broker instances consuming 4GB+ RAM for 4 messages per day — it's like chartering a cargo plane to deliver a postcard. Redis Streams gives me consumer groups, message acknowledgment, and concurrency control (prevents two analysis runs from racing) with the same Redis instance I use for dashboard caching and Prometheus query caching. One tool, four purposes, zero additional infrastructure."

**Q11: "How does the auto-PR work?"**

> "After the analyser computes recommendations, the manifest generator reads the current K8s deployment YAML from the GitHub repo, replaces resource requests and limits with right-sized values, creates a new branch using the PyGithub library, commits the modified YAML, and opens a pull request. The PR description is auto-generated with a markdown table showing before/after values per pod, projected monthly savings, confidence levels, and a link to the Grafana dashboard for validation. A human reviews and merges the PR — we never auto-merge because resource changes can affect application stability. This is 'auto-detect, auto-propose, human-approve.'"

**Q12: "Tell me about the self-referential demo."**

> "KubeThrifty analyses its own pods. I deploy it with intentionally over-provisioned resources — 2 CPU / 2 GB for the dashboard, 2 CPU / 2 GB for the analyser. After 7 days of metrics, KubeThrifty recommends right-sizing itself to 100m / 256 MB for the dashboard and 250m / 512 MB for the analyser. It auto-creates a PR updating its own Helm values.yaml. This is the demo moment in interviews — 'the tool optimised itself, proving it works end-to-end from metrics collection to automated remediation.'"

**Q13: "What Kubernetes resources does KubeThrifty use?"**

> "The dashboard is a Deployment with 2 replicas and an HPA based on CPU. The analyser is a Deployment driven by KEDA's Redis Streams scaler — it scales to zero when no analysis job is queued and up to a few workers when jobs are pending; a tiny scheduler `XADD`s a job every 6 hours. TimescaleDB is a StatefulSet with a PersistentVolumeClaim for data durability. Redis is a Deployment with a small PVC for persistence. Everything is packaged in a Helm chart with configurable values for Prometheus URL, analysis window, safety margins, and GitHub credentials stored in K8s Secrets. The entire platform runs in a dedicated namespace."

### The Differentiators (Forecasting, Verification, and the VPA Defense)

**Q14: "Kubernetes already has VPA, and KRR and Goldilocks exist. Why build this?"**

> "That's the right question, and I have a deliberate answer. VPA *applies* changes and restarts pods automatically — it's opaque, it has no cost framing, and many teams don't trust it in production for exactly that reason. KRR and Goldilocks are recommenders, but they're point-in-time and read-only. KubeThrifty is different on three axes. First, it's **GitOps-native**: the change is a reviewable PR against your manifests with a before/after table and a rupee figure, so it's auditable and a human approves it — 'auto-detect, auto-propose, human-approve.' Second, it's **forecast-aware**: it won't shrink a pod that's trending up, which a trailing-percentile tool will happily do. Third, it **closes the loop** — it watches the pod after the change and auto-rolls-back if it regresses. So it's not 'a worse VPA,' it's a cost-first, auditable, self-correcting workflow. And honestly — knowing VPA/KRR/Goldilocks exist and being able to articulate the trade-off is itself the point; it shows I researched the space instead of reinventing it blindly."

**Q15: "Why add ML forecasting — isn't P95 enough?"**

> "P95 over a trailing window is a backward-looking statistic. If a service is growing 10% week-over-week, its trailing P95 is already stale — right-size to it and you'll be under-provisioned within days, causing throttling or OOMKills. So I forecast each pod's next 7–14 days with a seasonal model — AutoETS or MSTL via statsforecast, which captures trend and weekly seasonality — and I take `recommended_request = max(P95 × margin, forecast_upper_bound)`. The forecast acts as a floor that prevents unsafe downsizing. It's behind a `Forecaster` adapter so I can swap statsforecast for Prophet or a cloud model, and there's a deterministic P95 fallback when there isn't enough data to forecast confidently."

**Q16: "How do you know the forecasts are any good — and stop a bad model change shipping?"**

> "There's a backtest eval harness in CI. I hold out the last N days, forecast them from the earlier data, and compute sMAPE plus 90% prediction-interval coverage per pod. The results are compared to a committed baseline; if accuracy regresses past a threshold or interval coverage drops below ~85%, CI fails — a model or feature change gets gated exactly like a unit test. In production I track realized-vs-forecast error as a Prometheus metric, so model drift shows up on a Grafana panel and I can alert on it. That's the MLOps discipline: the forecaster is a versioned component with an eval gate and drift monitoring, not a magic function."

**Q17: "Walk me through the verification and rollback loop."**

> "After a right-sizing PR is merged and the new resources roll out, KubeThrifty enters a verification window — 24 to 48 hours — for the pods it changed. It watches three signals from Prometheus: OOMKill events (`kube_pod_container_status_last_terminated_reason`), CPU throttling (`container_cpu_cfs_throttled_periods_total` rising), and restart count. If any cross a threshold, it marks the recommendation as regressed, auto-opens a rollback PR restoring the previous values, and records the failure so future recommendations for that workload are more conservative. So the loop is auto-detect, auto-propose, human-approve, auto-verify, auto-rollback. That last mile — verifying your own change and undoing it if it hurt — is what makes automated remediation trustworthy, and it's the SRE instinct of 'every change is a hypothesis you have to validate.'"

**Q18: "What's the bin-packing 'what-if'?"**

> "Per-pod savings are nice, but cloud bills are paid per *node*. So after right-sizing, I run a first-fit-decreasing bin-packing simulation: given the new, smaller pod requests and the node instance type, how many nodes does the cluster actually need? Going from 5.5 CPU requested to 1.7 CPU across the sample app might collapse three nodes into one. That node-count delta — 'you can drop two m5.xlarge nodes, ₹X/month' — is the number that actually moves a finance conversation, and it's far more credible than summing per-pod waste."

---

## 13. Deployment Checklist — Go Live

- [ ] `docker compose up` runs all services locally
- [ ] K8s cluster (kind/minikube) with sample app deployed
- [ ] Prometheus scraping all pod metrics (verify in Prometheus UI)
- [ ] Analyser CLI produces correct recommendations locally
- [ ] TimescaleDB stores metric snapshots + continuous aggregates working
- [ ] Dashboard renders: KPI cards, heatmap, time-series charts, savings calculator
- [ ] Auto-PR creates correct GitHub PR with before/after table
- [ ] Helm chart installs KubeThrifty to K8s: `helm install kubethrifty ./charts/kubethrifty`
- [ ] Self-referential demo: KubeThrifty analyses its own pods
- [ ] CI/CD pipeline green (GitHub Actions badge)
- [ ] Grafana dashboards: raw metrics + KubeThrifty operational metrics
- [ ] README: architecture diagram, screenshots, setup steps, demo video link
- [ ] Deployed to cloud K8s (GKE free tier or EKS)
- [ ] Record 2-minute demo video showing full flow
- [ ] "₹40,000/month saved" number is documented with calculation

**Phase 2 (differentiators) checklist:**
- [ ] Forecaster runs per pod; recommendation is floored to the 14-day forecast upper bound
- [ ] Backtest eval (sMAPE + 90% coverage) runs in CI and fails on regression vs baseline.json
- [ ] Forecast-error (drift) metric visible in Grafana; falls back to P95 when `confident=False`
- [ ] Verification watcher detects OOMKill/throttle/restarts on a changed pod (test by over-cutting one)
- [ ] Auto-rollback PR is created when a pod regresses post-merge
- [ ] Bin-packing "what-if" shows node-count reduction on the dashboard
- [ ] Analyser scales to zero via KEDA when no job is queued (verify 0 replicas when idle)

### Cost Estimate (Monthly)

| Service | Provider | Cost |
|---|---|---|
| GKE Autopilot (small cluster) | Google Cloud | ~$70/mo (free $300 credit) |
| OR kind cluster on EC2 t3.medium | AWS | ~$30/mo |
| OR local minikube (free) | Local | $0 |
| Docker Hub | Docker | Free (public images) |
| Forecasting (statsforecast, runs in-cluster on CPU) | — | $0 (no GPU, no API) |
| Domain | Namecheap | ~$10/year |
| **Total** | | **$0-70/month** |

---

## 14. Predictive Right-Sizing (ML) + MLOps Wrapper — Differentiator #1

> **Rev 3 status: kept, unchanged in substance.** Python pin moves to 3.14 and the forecast becomes one of *three* floors rather than two — the rehearsal-proven floor (§22) joins the trailing-percentile and forecast floors in `max()`. See §21.3.

### 14.1 The problem with trailing-percentile right-sizing

Rev 1 recommends `request = P95(last 7 days) × 1.2`. That is **backward-looking**. For a service growing week-over-week, last week's P95 is already an underestimate of next week's load. Right-size to it and you cause throttling or OOMKills within days — the exact failure that makes engineers distrust auto-remediation. The fix is to make the recommendation **forward-looking**: forecast the next 7–14 days and never cut below that forecast.

```
recommended_request = max(
    P95_trailing × REQUEST_SAFETY_MARGIN,     # the statistical floor (Rev 1)
    forecast_upper_bound(next 14 days)          # the predictive floor (Rev 2)
)
```

If the forecast says usage is flat or falling, the statistical term dominates and you still get the savings. If the forecast says usage is climbing, the predictive term protects you. You only ever downsize when *both* agree it's safe.

### 14.2 The forecaster (provider-agnostic)

Each pod/resource is a univariate hourly series with daily + weekly seasonality. The default is **statsforecast** (`AutoETS`, or `MSTL` for multiple seasonalities) — fast, CPU-only, no heavy native deps, fine on Python 3.14. It lives behind an adapter so it's swappable.

```python
# analyser/src/forecasting/types.py
from dataclasses import dataclass
import pandas as pd

@dataclass
class Forecast:
    horizon_hours: int
    point: pd.Series          # predicted mean per future hour
    upper: pd.Series          # upper bound of the prediction interval (e.g. 90%)
    model_id: str             # "statsforecast:AutoETS" — recorded for provenance
    confident: bool           # False -> caller falls back to trailing P95

class Forecaster:
    """Provider-agnostic. Default impl wraps statsforecast; swap for Prophet/cloud."""
    def forecast(self, history: pd.Series, horizon_hours: int) -> Forecast: ...
```

```python
# analyser/src/forecasting/statsforecast_forecaster.py
from statsforecast import StatsForecast
from statsforecast.models import AutoETS
import pandas as pd
from .types import Forecaster, Forecast

class StatsForecaster(Forecaster):
    MODEL_ID = "statsforecast:AutoETS"
    MIN_POINTS = 24 * 14                                   # need ~2 weeks of hourly data

    def forecast(self, history: pd.Series, horizon_hours: int) -> Forecast:
        if len(history.dropna()) < self.MIN_POINTS:
            # Not enough history to trust a forecast -> signal fallback to P95.
            return Forecast(horizon_hours, history, history, self.MODEL_ID, confident=False)
        df = history.reset_index()
        df.columns = ["ds", "y"]; df["unique_id"] = "pod"
        sf = StatsForecast(models=[AutoETS(season_length=24 * 7)], freq="h")  # weekly seasonality
        sf.fit(df)
        fc = sf.predict(h=horizon_hours, level=[90])
        return Forecast(
            horizon_hours=horizon_hours,
            point=fc["AutoETS"], upper=fc["AutoETS-hi-90"],
            model_id=self.MODEL_ID, confident=True,
        )
```

### 14.3 Wiring the forecast into the recommendation engine

The recommendation engine (§5.2) gains one term. The `confident=False` path is the **deterministic fallback** — if a pod is new or the series is too short/noisy to forecast, we degrade to Rev 1's trailing-P95 behaviour rather than guess.

```python
fc = forecaster.forecast(pod_cpu_hourly, horizon_hours=24 * 14)
predictive_floor = fc.upper.max() if fc.confident else 0.0
recommended_request = max(
    p95 * REQUEST_SAFETY_MARGIN,
    predictive_floor,
    _absolute_minimum(resource),
)
if fc.confident and predictive_floor > p95 * REQUEST_SAFETY_MARGIN:
    reason += " Forecast shows rising usage; floored to the 14-day forecast upper bound."
```

### 14.4 The MLOps wrapper (this is what makes it an *AI-Engineering* story, not a script)

| Component | What it does | Why a fresher rarely has it |
|---|---|---|
| **Provider-agnostic `Forecaster` adapter** | One interface, swappable backends (statsforecast / Prophet / cloud) | Most people hardcode one library |
| **Versioned model config** | `model_id` recorded on every recommendation for provenance | Treats the model as a tracked artifact |
| **Backtest eval harness gating CI** | Holdout backtest; sMAPE + 90% interval coverage vs a committed baseline; CI fails on regression | Brings test discipline to a non-deterministic component |
| **Drift monitoring** | Realized-vs-forecast error emitted to Prometheus; Grafana panel + alert | Detects silent model decay in production |
| **Deterministic fallback** | `confident=False` -> trailing P95 | The product never depends on the model being available or good |

```python
# evals/forecast_eval.py  — runs in CI on any change to the forecaster or features
# Holdout the last 3 days; forecast them from earlier data; score per pod.
TARGET_SMAPE = 0.25          # mean sMAPE must stay below this
MIN_COVERAGE = 0.85          # 90% interval must actually cover >=85% of points

def smape(actual, pred):
    import numpy as np
    return float(np.mean(2 * np.abs(pred - actual) / (np.abs(actual) + np.abs(pred) + 1e-9)))

# ... load fixtures, forecast, compute mean sMAPE + coverage, compare to baseline.json,
# sys.exit(1) if mean_smape > TARGET_SMAPE or coverage < MIN_COVERAGE  (gates the build)
```

> Interview gold: *"My right-sizer has a model, and the model has a test suite. A backtest in CI scores forecast accuracy with sMAPE and checks that my 90% prediction interval actually covers 85%+ of held-out points. If a change regresses accuracy, the build fails — same as a unit test. In production I track forecast error as a metric so drift is visible on a dashboard. And if a pod can't be forecast confidently, I fall back to the trailing-P95 logic, so the model is a safe enhancement, never a single point of failure."*

---

## 15. Closed-Loop Verification & Auto-Rollback — Differentiator #2

> **Rev 3 status: kept, with a corrected signal implementation (§21.4) and a new consumer.** The same signals now also drive the pre-merge rehearsal (§22) and feed ThriftDetective (§26), which answers *why* a change regressed and *whether KubeThrifty caused it*.

### 15.1 Why this is the feature SREs remember

Every right-sizing tool can *recommend*. Almost none **verify their own change and undo it if it hurt**. KubeThrifty closes the loop:

```
auto-detect  →  auto-propose (PR)  →  human-approve (merge)
                                          │
                                          ▼
                          rollout (new resources applied)
                                          │
                                          ▼
              auto-verify (watch the pod 24–48h)  ──ok──►  mark "validated", learn
                                          │
                                       regressed
                                          ▼
                          auto-rollback PR (restore previous values) + flag workload
```

This is the SRE mindset encoded: *a resource change is a hypothesis; you don't trust it until you've observed it in production.*

### 15.2 What "regressed" means (the signals)

After a merge, the verification watcher tracks the changed pods for a configurable window and trips on any of:

| Signal | PromQL (sketch) | Why it means the cut was too aggressive |
|---|---|---|
| **OOMKill** | `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}` increased | Memory request/limit cut below real need |
| **CPU throttling** | `rate(container_cpu_cfs_throttled_periods_total[5m])` rose materially vs pre-change baseline | CPU limit too low — latency will suffer |
| **Restarts / CrashLoop** | `increase(kube_pod_container_status_restarts_total[1h])` spiked | Pod can't stay up under the new limits |
| **(Optional) SLO** | request latency / error rate breached its SLO | App-level regression even without K8s signals |

### 15.3 The verification watcher

> ⚠️ **Rev 3 correction — see §21.4 for the replacement file.** The sketch below compares raw throttled-*period counters* (`throt > baseline.throttle * 1.5`). Period counts scale with window length, replica count and the CFS period, so this produces both false rollbacks and missed regressions. Rev 3 compares the dimensionless **throttle ratio** (`throttled_periods / periods`), requires an absolute floor as well as a delta, adds PSI as a signal, and detects OOM via a counter (`container_oom_events_total` / cgroup `memory.events`) rather than the flapping `last_terminated_reason` gauge.

```python
# analyser/src/verification/watcher.py
def verify_change(pod, namespace, since, prometheus, baseline) -> VerificationResult:
    oom   = prometheus.increased("kube_pod_container_status_last_terminated_reason",
                                 labels={"pod": pod, "reason": "OOMKilled"}, since=since)
    throt = prometheus.rate("container_cpu_cfs_throttled_periods_total",
                            labels={"pod": pod}, window="5m", since=since)
    restarts = prometheus.increase("kube_pod_container_status_restarts_total",
                                   labels={"pod": pod}, since=since)
    regressed = oom > 0 or restarts > baseline.restarts + 1 or throt > baseline.throttle * 1.5
    return VerificationResult(pod, regressed,
        reason=("OOMKilled" if oom else "throttling" if throt else "restarts" if restarts else "ok"))
```

If `regressed`, the same PyGithub machinery that opened the right-sizing PR (§5.4) opens a **rollback PR** restoring the prior `requests`/`limits`, titled e.g. *"⏪ Roll back right-sizing of payment-processor — OOMKilled after resize"*, and the recommendation row is marked `regressed=true` so the engine widens the safety margin for that workload next time (a cheap form of learning).

> A scheduled GitHub Actions / KEDA-driven job runs the watcher for any recommendation whose PR merged within the verification window; results land in the `recommendations` table and a "Recently Verified / Rolled Back" panel on the dashboard.

### 15.4 Bonus: bin-packing "what-if" (turn pod savings into node savings)

Per-pod waste is the wrong unit for a cloud bill — you pay per node. A first-fit-decreasing bin-packing pass answers "after right-sizing, how many nodes does this actually need?"

```python
def nodes_needed(pod_requests, node_capacity):   # first-fit decreasing
    bins = []                                     # each bin = a node's remaining (cpu, mem)
    for cpu, mem in sorted(pod_requests, reverse=True):
        for b in bins:
            if b["cpu"] >= cpu and b["mem"] >= mem:
                b["cpu"] -= cpu; b["mem"] -= mem; break
        else:
            bins.append({"cpu": node_capacity.cpu - cpu, "mem": node_capacity.mem - mem})
    return len(bins)

before = nodes_needed(current_requests, node)     # e.g. 4 nodes
after  = nodes_needed(rightsized_requests, node)  # e.g. 1 node
# Dashboard: "Right-sizing lets you drop 3 × m5.xlarge — ₹X/month in node cost."
```

This makes the headline savings **credible**: not "we trimmed some millicores," but "you can run this cluster on a third of the nodes."

---

## 16. Resilience Patterns

Rev 1 had only a circuit breaker. Here is the full set, mapped to the specific failure each defends against in KubeThrifty. The analyser touches three flaky externals — Prometheus, the GitHub API, and the database — so resilience matters.

| Pattern | KubeThrifty application | Failure it prevents |
|---|---|---|
| **Timeout** | Every Prometheus query, GitHub call, and DB statement has an explicit deadline | One hung PromQL range query stalling the whole analysis run |
| **Retry + backoff + jitter** | Transient Prometheus 5xx / GitHub 502 / abuse-rate 403 retried with exponential backoff + jitter | A blip aborting an analysis; thundering-herd retries |
| **Circuit breaker** | Per-external (Prometheus, GitHub) via `circuitbreaker` (already in §8.5) | Hammering a downed Prometheus; analysis hanging on every query |
| **Bulkhead** | KEDA-scaled analyser workers are isolated from the dashboard; a stuck analysis can't take the dashboard down | One slow component sinking the whole platform |
| **Dead-letter** | An analysis job that fails past max retries is moved to `analysis-jobs-dlq` with the error | Silent loss of a run; a poison job wedging the consumer |
| **Idempotency** | A run is keyed by `(cluster, window, started_at_bucket)`; re-running produces the same recommendations; PR creation is idempotent on branch name | Duplicate triggers creating duplicate PRs or double-counting savings |
| **Graceful degradation** | Dashboard serves the last completed `analysis_run` from TimescaleDB/Redis when a fresh run is unavailable; if forecasting is down, fall back to P95 (§14) | A blank dashboard instead of slightly-stale-but-useful recommendations |
| **Concurrency guard** | A Redis lock (`SET NX` on `analysis:lock:{cluster}`) ensures one analysis per cluster at a time | Two runs racing and writing conflicting recommendations |
| **Read-only safety floor** | Absolute minimums (50m / 64Mi), `limit ≥ request`, "needs review" on high variance (§5.2) | The engine ever proposing a dangerous cut |

```python
# retry with full jitter (used around Prometheus / GitHub calls)
def with_retry(fn, max_attempts=3, base=0.3):
    import random, time
    for attempt in range(max_attempts):
        try: return fn()
        except TransientError:
            if attempt == max_attempts - 1: raise
            time.sleep(base * 2 ** attempt + random.uniform(0, base))   # full jitter
```

> Layering soundbite: *"Timeout bounds one query, retry handles a blip, the breaker handles a sustained Prometheus outage, the bulkhead keeps a stuck analyser away from the dashboard, and the DLQ guarantees I can replay a failed run. The most important resilience property, though, is that a failed or degraded analysis never produces a *wrong* recommendation — the safety floors and the human-approval PR gate mean the worst case is 'no change,' never 'a bad change.'"*

---

## 17. Availability & Consistency Patterns

### 17.1 CAP positioning

KubeThrifty is a **read-mostly advisory tool over data it does not own**: Prometheus is the source of truth for *observed* metrics, and **Git is the source of truth for *desired* state** (the manifests). It deliberately chooses **AP — availability over strict consistency.** A recommendation that is an hour stale is still useful; a dashboard that errors because a fresh analysis hasn't finished is not. Nothing KubeThrifty does is in a low-latency critical path, so there is no reason to trade availability for strong consistency.

### 17.2 The consistency model — eventual, bounded, GitOps-anchored

| Property | How KubeThrifty achieves it |
|---|---|
| **Eventual consistency** | Metrics → analysis run → recommendations → (approved) PR → merge → rollout. Convergence over time, not instantaneous agreement. |
| **Git as the consistency anchor** | Desired resources live in Git; the cluster converges to Git via the normal deploy pipeline. This is the GitOps guarantee — the manifests are the single source of truth, and KubeThrifty only ever *proposes* changes to that source. |
| **Bounded staleness** | Analysis runs every 6h; the dashboard shows "last analysis 23 min ago." Recommendations are explicitly timestamped. |
| **At-least-once job processing** | Redis Streams may redeliver on consumer crash; KEDA may spin a replacement worker. Accepted, then made safe by... |
| **...idempotent runs + dedup** | ...keying runs idempotently and deduping so a redelivered job doesn't double-write recommendations or open a second PR. |
| **Read-your-writes (dashboard)** | After a manual "Run analysis," the triggering client subscribes to the Redis pub/sub status channel (§7) and sees its own run complete before the list refreshes. |

### 17.3 Availability patterns

- **Cache-as-availability-buffer:** Redis holds the latest recommendations and the dashboard reads them in &lt;10ms; a failed or in-progress analysis never blanks the UI.
- **Stale-while-revalidate:** serve the last completed run instantly; compute the new one in the background; swap when ready.
- **Stateless analyser:** all durable state is in TimescaleDB + Redis, so KEDA can kill and recreate analyser pods freely (including scale-to-zero) with zero data loss.
- **Self-healing via the verification loop (§15):** availability isn't just uptime — a bad right-size that hurts a workload is auto-rolled-back, so the platform protects the *availability of the workloads it advises on*, not just itself.
- **Readiness gates:** `/api/health` checks TimescaleDB + Redis; K8s pulls an unhealthy dashboard pod from rotation before it serves errors.

### 17.4 Consistency edge cases to be ready for

- **Prometheus gaps / scrape misses:** the forecaster and percentile logic tolerate missing buckets; runs with too few samples are skipped (`MIN_DATA_POINTS`), never guessed.
- **Manifest drift (someone edited resources by hand):** the analyser reads *current* manifest values from Git before proposing a diff, so a hand-edit since the last run is respected rather than clobbered.
- **Concurrent analysis triggers:** the Redis `SET NX` lock coalesces them into one run (§16).

---

## 18. Mitigation Strategies (Failure-Mode Table)

A single consolidated table an interviewer can walk top-to-bottom: what breaks, who feels it, how it's contained.

| # | Failure mode | Blast radius | Detection | Mitigation |
|---|---|---|---|---|
| 1 | Prometheus down / 5xx | All analysis runs | Breaker open; query errors | Breaker + retry; serve last completed run; resume on recovery |
| 2 | Prometheus returns sparse/partial data | One run's accuracy | Sample count &lt; `MIN_DATA_POINTS` | Skip that pod (no recommendation) rather than guess |
| 3 | GitHub API down / rate-limited | Auto-PR only | Breaker open; 403 | Retry w/ backoff; recommendations still stored + shown; PR retried next run |
| 4 | Analysis job fails mid-run | One run | Worker exception; DLQ depth | Job → DLQ with error; last good run stays served; replay |
| 5 | Two triggers race | One cluster | Lock contention | `SET NX` concurrency lock → single run |
| 6 | **Recommendation too aggressive (OOMKill/throttle)** | One workload in prod | **Verification watcher (§15)** | **Auto-rollback PR + widen margin for that workload** |
| 7 | Forecast model degraded/drifted | Recommendation quality | sMAPE/coverage eval (CI) + drift metric (prod) | CI fails the change; prod alert; fallback to P95 |
| 8 | Forecaster unavailable / new pod | Recommendations | `confident=False` | Deterministic trailing-P95 fallback |
| 9 | TimescaleDB down | Persistence + dashboard reads | Health check | Readiness pulls pod; serve Redis-cached recs read-only |
| 10 | Redis down | Cache + queue + status | Health check | Compute live (slower); in-memory dedup window; TimescaleDB still authoritative |
| 11 | Toolkit extension missing | Migration/analysis | Migration error on `percentile_agg` | Use `timescaledb-ha` image (§B) — fixed in Rev 2 |
| 12 | Bad/edited manifest | Auto-PR correctness | YAML parse / schema check | Validate before committing; skip + flag on parse failure |
| 13 | Secret leak (GitHub token) | Security | Audit | K8s Secrets, least-privilege fine-grained PAT (contents+PR scope only), rotate on suspicion |
| 14 | Analyser stuck (won't scale to zero) | Cost (ironic) | KEDA replica metric | cooldownPeriod + max replica cap; alert if &gt;0 replicas while queue empty |

---

## 19. Deployment Strategies

Rev 1 covered Docker/K8s *mechanics* and the self-referential demo. This section covers *strategy*: how KubeThrifty itself ships, and — uniquely for this project — **how a right-sizing change is rolled out safely**, plus the all-important VPA/KRR/Goldilocks defense.

### 19.1 Deploying KubeThrifty itself

| Strategy | Use it here? |
|---|---|
| **Recreate** | MVP only, single instance — acceptable for the first weeks. |
| **Rolling update** | **Default for K8s.** Dashboard is stateless with 2 replicas → zero-downtime rolling updates are native to the Deployment. |
| **Blue-green** | Overkill for a single-service tool; a good talking point for an all-or-nothing cutover. |
| **Canary** | The aspirational answer; meaningful only at multi-tenant scale. |

Helm makes rollback trivial: every deploy is `helm upgrade` and a bad release is `helm rollback kubethrifty <REV>`. Images are tagged by commit SHA (never `latest` — §B), so rollback is deterministic. DB migrations follow **expand-contract**: additive migrations ship with the release; destructive ones wait for a follow-up release after all pods are new.

### 19.2 The interesting part — safely rolling out a *resource* change

A right-sizing PR changes pod `requests`/`limits`. Merging it triggers a **rolling update of the target workload** (changing resources restarts pods). KubeThrifty makes that safe:

1. **Propose conservatively** — forecast-floored requests (§14), 20% margin on requests, 50% on limits, never below absolute minimums.
2. **Human-approve** — the PR is the gate; nothing auto-merges, because a resource change can affect stability.
3. **Roll out gradually** — the workload's own Deployment `RollingUpdate` strategy (`maxUnavailable: 0`, `maxSurge: 1`) means new-resource pods come up before old ones leave; if they crash-loop, the rollout halts on its own.
4. **Verify** — the §15 watcher observes the changed pods for 24–48h.
5. **Auto-rollback** — regression → automatic rollback PR.

> This is "progressive delivery for resource changes": propose → approve → roll → observe → keep or revert. It's the same discipline as a canary, applied to right-sizing.

### 19.3 The VPA / KRR / Goldilocks defense (memorize this)

> **Rev 3: use the updated table in §21.9 instead.** The competitive picture changed in 2026 — in-place resize is GA, VPA's non-disruptive mode is still alpha, and Karpenter/Auto Mode now need addressing explicitly. §28.2 has the full head-to-head grid for the README.

An interviewer *will* say "this already exists." Your answer:

| Tool | What it does | KubeThrifty's deliberate difference |
|---|---|---|
| **VPA** | Auto-applies + restarts pods with new resources | Opaque, no cost framing, no review step; teams distrust auto-apply. KubeThrifty proposes an **auditable PR** a human approves. |
| **KRR** (Robusta) | CLI recommender from Prometheus | Point-in-time, read-only, no GitOps PR, no forecast, no verification loop. |
| **Goldilocks** (Fairwinds) | Dashboards VPA recommendations | Same — surfaces numbers; doesn't forecast, doesn't close the loop, no rupee/node cost story. |

> "I'm not claiming to beat VPA at what VPA does. I'm doing something VPA deliberately doesn't: a **cost-first, GitOps-native, forecast-aware, self-verifying** workflow. The recommendation is a reviewable PR with a rupee figure and a node-count impact; it won't cut a pod that's trending up; and it watches its own change and rolls back on regression. Knowing these tools exist and articulating *why mine is shaped differently* is the actual signal — I researched the space and made deliberate trade-offs."

### 19.4 Topologies

| Stage | Topology | Cost |
|---|---|---|
| **MVP / demo** | local `kind` (pinned 1.36 node image) + kube-prometheus-stack, recreate deploys | $0 |
| **Public** | GKE Autopilot free tier (or EC2 + kind), HTTPS via ingress + cert-manager, rolling deploys | $0–70/mo |
| **Scale story (interview)** | EKS/GKE, dashboard 2–3 replicas + HPA, analyser KEDA-scaled (0→N), managed TimescaleDB + managed Redis/Valkey, canary via Argo Rollouts, Kafka instead of Redis Streams beyond ~10k clusters | discuss, don't build |

---

## 20. README Blueprint with Architecture Diagram

The Rev 1 table of contents promised this section but omitted it. A strong README is the single highest-ROI thing a fresher can ship — most skip it. Structure:

```markdown
# KubeThrifty — Kubernetes Pod Right-Sizing Advisor ⚡

> Stop paying for CPU your pods never touch. KubeThrifty analyses Prometheus
> metrics, forecasts future usage, and opens GitHub PRs that right-size your
> pods — then verifies the change and rolls back if anything regresses.

[![CI](badge)] [![Deploy](badge)] [![License: MIT](badge)] [![GitHub stars](badge)]

[ LIVE DEMO ](https://kubethrifty.example.com) · [ 2-min video ](youtube) · [ Architecture ](#architecture)

![dashboard screenshot](docs/dashboard.png)
![auto-PR screenshot](docs/auto-pr.png)

## The Problem
A 5-service demo app requests 5.5 CPU / 6.5 GB but actually peaks at 1.7 CPU /
2.6 GB — ~₹40,000/month of waste. Multiply across a real cluster and it's the
biggest line item nobody owns.

## What KubeThrifty Does
1. Prometheus scrapes CPU/memory for every pod (cAdvisor + kube-state-metrics).
2. A Python analyser computes P50/P95/P99 AND forecasts the next 14 days.
3. It recommends right-sized requests/limits — never below the forecast.
4. The Next.js dashboard shows waste per pod, projected ₹ savings, and node-count impact.
5. It opens a GitHub PR with a before/after table; a human approves.
6. After merge, it verifies the pod for 24–48h and auto-rolls-back on regression.

## Architecture
[ embed the HLD diagram from §4 ]

## Why not just use VPA / KRR / Goldilocks?
[ the §19.3 table — this question lands on the README too ]

## Tech Stack
Kubernetes 1.36 · Prometheus 3.x · Python 3.14 (statsforecast) · Next.js 16 ·
TimescaleDB (pg18 `-ha`) · Valkey 9.1 · KEDA 2.20 · Helm 4.2 · GitHub Actions

## Quick Start
    kind create cluster --image kindest/node:v1.36.0
    helm install kube-prometheus-stack ...   # pinned chart version
    helm install sample-app ./charts/sample-app
    helm install kubethrifty ./charts/kubethrifty
    # open the dashboard, watch it right-size the sample app (and itself)

## Local Dev (no K8s)
    docker compose up         # dashboard + analyser + timescaledb-ha + redis + prometheus + grafana

## The Self-Referential Demo
KubeThrifty right-sizes its OWN pods and opens a PR against its own Helm values.

## Scaling Decision / MLOps / Resilience
[ link to §11 scaling decision, §14 MLOps, §16 resilience ]

## Contributing · License (MIT)
```

README must-haves that separate you from other freshers: an animated GIF of the graph updating + a PR being created, pinned-version badges, a &lt;2-minute quick start, the architecture diagram inline, and the "why not VPA" section right up top.

---

## 21. Rev 3 Corrections & Amendments

Rev 2 was audited as if a staff engineer were reviewing it before production. Four findings were real defects, not style. Fixing them *in public*, in the README and in the interview, is worth more than never having had them.

### 21.1 The four defects

| # | Where | Defect | Consequence if shipped | Fix |
|---|---|---|---|---|
| **C1** | §1 hook, §3 | Headline savings derived **per pod** (`wasted millicores × unit price`) | Clouds bill per **node** (or per node-hour under Karpenter/Auto Mode). Trimming 3 CPU of requests across 5 pods saves **₹0** until a node disappears. An interviewer asks "so did your bill change?" and the story collapses | Headline number comes from the **bin-packing delta** (§25). Per-pod waste is reported as *waste*, never as *savings* |
| **C2** | §5.2 | Memory sized as `P95 × 1.20`, identical to CPU | A percentile **by construction discards the top 5% of samples**. For CPU that is fine — the kernel throttles and the app gets slower. For memory the kernel **OOMKills** and the app dies. `P95 × 1.2` on a workload whose peak is 3× its P95 is a scheduled outage | Memory sized off **observed peak** (`memory.peak` where available, else max working set) with a separate margin, and `limit == request` for memory by default (§21.3) |
| **C4** | §9.1 | The `docker-compose.yml` pins `prom/prometheus:latest` and `grafana/grafana:latest` — in a document whose headline version rule is "never `latest`" | Local dev drifts from CI and from the cluster; a Grafana major bump silently breaks provisioned dashboards; and the credibility of the entire pinning story dies the moment an interviewer opens the file | All compose images pinned; Renovate owns the bumps (§21.2b) |
| **C3** | §15.3 | Regression check compares raw `container_cpu_cfs_throttled_periods_total` counts across windows (`throt > baseline.throttle * 1.5`) | Period *counts* scale with window length, replica count and CFS period. A quiet pod with more replicas looks "worse"; a busy pod that genuinely regressed can look "better." False rollbacks and missed rollbacks | Compare the **dimensionless throttle ratio** `throttled_periods / periods`, and require both a ratio delta **and** an absolute floor before tripping (§21.4) |

> Interview soundbite: *"The most useful review I did on my own project found three things. Two were maths errors — I was sizing memory with a percentile, which is exactly the wrong statistic for an incompressible resource, and I was comparing raw CFS throttle counts across windows of different lengths. The third was a framing error: I was reporting per-pod waste as if it were money saved, and cloud bills don't work that way. All three are in the CHANGELOG."*

### 21.2 C1 — the headline number, restated correctly

```
waste            = Σ (request − peak_usage)        per pod   → an efficiency metric
savings (real)   = (nodes_before − nodes_after) × node_price → a money metric
```

The demo app still shows 5.5 CPU / 6.5 GB requested against a ~1.7 CPU / 2.6 GB peak. Rev 3 reports that as **70% CPU / 60% memory over-provisioned**, and then reports money only through §25: *"the same workload bin-packs from 4 nodes onto 1, which is where the ₹ figure comes from."* Same headline, defensible arithmetic.

### 21.2b C4 — pin your own compose file

The rule is not "pin production images." It is **pin every image in the repository**, because a local `latest` is how "works on my machine" gets manufactured. Concretely: the compose file gets exact tags (§9.1 as corrected), the Helm chart gets `appVersion` + digest-pinnable OCI charts (§B), CI gets pinned action SHAs, and Renovate opens the bump PRs so the pins stay *current* rather than merely *fixed*. Then the sentence "every image in this repo is pinned, and a bot proposes the upgrades" is true, and checkable in ten seconds.

### 21.3 C2 — corrected sizing module (complete file)

This **replaces** the sizing maths embedded in §5.2. Keep §5.2's dataclasses and action/confidence enums; import from here.

```python
# analyser/src/sizing.py
"""
Resource sizing rules for KubeThrifty.

Design rule (Rev 3): CPU and memory are NOT symmetric.

  CPU     is compressible. Exceeding the limit means CFS throttling — the app
          gets slower, then recovers. A percentile is an acceptable statistic.
  Memory  is incompressible. Exceeding the limit means the kernel OOMKills the
          container. A percentile is the WRONG statistic, because it discards
          the tail that kills you. Size memory off the observed PEAK.

Every number returned here is floored by an absolute minimum and by the
predictive floor from the forecaster (§14) and, when available, the empirical
floor proven by a Resize Rehearsal (§22).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- configuration
MIN_CPU_MILLICORES = 50          # never propose below 50m
MIN_MEMORY_MIB = 64              # never propose below 64Mi

CPU_REQUEST_MARGIN = 1.20        # over P95 — throttling is survivable
CPU_LIMIT_MARGIN = 1.50          # over P99
MEM_REQUEST_MARGIN = 1.25        # over observed PEAK — OOM is not survivable
MEM_HEADROOM_FOR_GC = 1.10       # extra for GC'd runtimes (JVM/Node/Go heaps)

MIN_SAVINGS_THRESHOLD = 0.10     # ignore <10% wins; churn isn't free
HIGH_VARIABILITY_THRESHOLD = 0.50
MIN_DATA_POINTS = 168            # 7 days of hourly samples

# Runtimes whose RSS is a lagging indicator of real demand.
GC_RUNTIME_HINTS = ("java", "jvm", "node", "dotnet", "clr", "golang-heavy")


@dataclass(frozen=True)
class UsageStats:
    """Everything the sizer is allowed to look at, in canonical units."""
    p50: float
    p95: float
    p99: float
    peak: float                   # max working set / cgroup memory.peak
    mean: float
    stddev: float
    samples: int
    # Optional evidence (§23 / §22). None == "not observed".
    psi_stalled_ratio: Optional[float] = None    # 0..1, memory or cpu 'full'
    throttle_ratio: Optional[float] = None       # 0..1
    oom_events: int = 0
    rehearsed_floor: Optional[float] = None      # proven-safe value (§22)


@dataclass(frozen=True)
class Sizing:
    request: float
    limit: float
    rationale: str
    binding_constraint: str       # which floor won — printed in the PR body


def _units_floor(resource: str) -> float:
    return MIN_CPU_MILLICORES if resource == "cpu" else MIN_MEMORY_MIB


def summarize(values: pd.Series, peak_series: Optional[pd.Series] = None) -> UsageStats:
    """
    peak_series: per-interval max (e.g. cgroup memory.peak deltas or
    max_over_time() from Prometheus). If absent, fall back to sample max,
    which UNDERSTATES the true peak between scrapes — record that in the
    rationale so the reviewer knows the number is conservative-by-luck.
    """
    v = values.dropna()
    peak = float(peak_series.max()) if peak_series is not None else float(v.max())
    return UsageStats(
        p50=float(np.percentile(v, 50)),
        p95=float(np.percentile(v, 95)),
        p99=float(np.percentile(v, 99)),
        peak=peak,
        mean=float(v.mean()),
        stddev=float(v.std()),
        samples=int(len(v)),
    )


def size_cpu(s: UsageStats, predictive_floor: float = 0.0) -> Sizing:
    """
    CPU: percentile-based, forecast-floored. We deliberately do NOT set a CPU
    limit equal to the request: a tight CPU limit converts spare node capacity
    into latency via CFS throttling. Default posture is request = sized,
    limit = generous multiple (or unset, see note).
    """
    floors = {
        "p95_margin": s.p95 * CPU_REQUEST_MARGIN,
        "forecast": predictive_floor,
        "rehearsed": s.rehearsed_floor or 0.0,
        "absolute_min": _units_floor("cpu"),
    }
    request = max(floors.values())
    binding = max(floors, key=floors.get)

    limit = max(s.p99 * CPU_LIMIT_MARGIN, request * 1.5)

    rationale = (
        f"CPU: P95={s.p95:.0f}m, P99={s.p99:.0f}m, peak={s.peak:.0f}m. "
        f"request={request:.0f}m (bound by {binding}), limit={limit:.0f}m. "
        "CPU is compressible: over-limit means throttling, not death."
    )
    if s.throttle_ratio is not None and s.throttle_ratio > 0.01:
        rationale += (
            f" NOTE: pod already throttled {s.throttle_ratio:.1%} of periods at the "
            "CURRENT limit — do not reduce the limit; consider raising it."
        )
    return Sizing(request, limit, rationale, binding)


def size_memory(s: UsageStats, predictive_floor: float = 0.0,
                runtime_hint: str = "") -> Sizing:
    """
    Memory: PEAK-based. Also sets limit == request by default, which puts the
    container in the Guaranteed QoS class for memory and makes eviction
    behaviour predictable. (See §24.4 for the QoS-transition warning.)
    """
    gc_factor = MEM_HEADROOM_FOR_GC if any(
        h in runtime_hint.lower() for h in GC_RUNTIME_HINTS
    ) else 1.0

    floors = {
        "peak_margin": s.peak * MEM_REQUEST_MARGIN * gc_factor,
        "forecast": predictive_floor,
        "rehearsed": s.rehearsed_floor or 0.0,
        "absolute_min": _units_floor("memory"),
    }
    request = max(floors.values())
    binding = max(floors, key=floors.get)

    rationale = (
        f"Memory: peak={s.peak:.0f}Mi (P95={s.p95:.0f}Mi — deliberately NOT the "
        f"sizing basis), margin={MEM_REQUEST_MARGIN:.2f}"
        + (f" × GC headroom {gc_factor:.2f}" if gc_factor > 1.0 else "")
        + f". request=limit={request:.0f}Mi (bound by {binding})."
    )
    if s.oom_events:
        rationale += (
            f" BLOCKED-DOWN: {s.oom_events} OOM event(s) observed in-window; "
            "memory may only be increased for this container."
        )
        request = max(request, s.peak * 1.5)
    if s.psi_stalled_ratio is not None and s.psi_stalled_ratio > 0.02:
        rationale += (
            f" PSI shows {s.psi_stalled_ratio:.1%} full-memory stall — the workload is "
            "already under reclaim pressure; treat any reduction as unsafe."
        )
    return Sizing(request, request, rationale, binding)   # limit == request
```

**Why `limit == request` for memory but not CPU** — this is a classic interview follow-up, so have the answer ready: setting memory request equal to limit makes the container's memory allocation predictable and puts it at the *bottom* of the eviction preference order for memory-pressure evictions; leaving a *gap* invites the scheduler to overcommit memory it cannot reclaim. CPU is the opposite: a tight CPU limit throttles a burst that the node could have absorbed for free, so the default is a generous limit (and there is a legitimate school of thought — worth naming — that says omit CPU limits entirely and let requests do the scheduling).

### 21.4 C3 — corrected regression signals (complete file)

This **replaces** the body of §15.3's `verify_change`.

```python
# analyser/src/verification/signals.py
"""
Regression detection for a merged (or rehearsed) resource change.

Rev 3 correction: compare RATIOS, not counters. Throttled-period counters
scale with window length, replica count and the CFS period; the ratio
throttled_periods / periods does not.

Trip rules (any one is sufficient):
  1. OOM      — any OOM kill attributable to the changed container.
  2. Throttle — ratio rose by >= THROTTLE_RATIO_DELTA AND now exceeds
                THROTTLE_RATIO_FLOOR (guards against 0.001 -> 0.004 noise).
  3. Restarts — restart count increased beyond tolerance.
  4. PSI      — 'full' memory or cpu stall share rose above PSI_FULL_CEILING.
  5. SLO      — optional app-level burn-rate breach.
"""
from dataclasses import dataclass, field
from typing import Optional

THROTTLE_RATIO_DELTA = 0.05     # +5 percentage points of throttled periods
THROTTLE_RATIO_FLOOR = 0.02     # ...and must exceed 2% in absolute terms
RESTART_TOLERANCE = 0           # any new restart on a previously stable pod
PSI_FULL_CEILING = 0.05         # 5% of wall time fully stalled = regressed


@dataclass
class Window:
    """Aggregates for one time window (before-change or after-change)."""
    throttled_periods: float
    cfs_periods: float
    restarts: float
    oom_kills: float
    psi_cpu_full: float = 0.0        # seconds stalled / seconds elapsed
    psi_mem_full: float = 0.0

    @property
    def throttle_ratio(self) -> float:
        return self.throttled_periods / self.cfs_periods if self.cfs_periods else 0.0


@dataclass
class Verdict:
    regressed: bool
    reasons: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


def evaluate(before: Window, after: Window,
             slo_burn_rate: Optional[float] = None) -> Verdict:
    reasons, evidence = [], {
        "throttle_ratio_before": round(before.throttle_ratio, 5),
        "throttle_ratio_after": round(after.throttle_ratio, 5),
        "psi_mem_full_after": round(after.psi_mem_full, 5),
        "psi_cpu_full_after": round(after.psi_cpu_full, 5),
        "oom_kills_after": after.oom_kills,
        "restarts_delta": after.restarts - before.restarts,
    }

    if after.oom_kills > before.oom_kills:
        reasons.append("OOMKilled after change — memory floor was too low")

    delta = after.throttle_ratio - before.throttle_ratio
    if delta >= THROTTLE_RATIO_DELTA and after.throttle_ratio >= THROTTLE_RATIO_FLOOR:
        reasons.append(
            f"CPU throttle ratio rose {delta:.1%} to {after.throttle_ratio:.1%}"
        )

    if (after.restarts - before.restarts) > RESTART_TOLERANCE:
        reasons.append("container restarted after change")

    if after.psi_mem_full > PSI_FULL_CEILING:
        reasons.append(f"memory PSI 'full' at {after.psi_mem_full:.1%} of wall time")
    if after.psi_cpu_full > PSI_FULL_CEILING:
        reasons.append(f"cpu PSI 'full' at {after.psi_cpu_full:.1%} of wall time")

    if slo_burn_rate is not None and slo_burn_rate > 1.0:
        reasons.append(f"SLO error budget burning at {slo_burn_rate:.2f}×")

    return Verdict(bool(reasons), reasons, evidence)
```

The matching PromQL (all rates over the same window length, which is the point):

```promql
# throttle ratio, per container
sum by (namespace,pod,container) (rate(container_cpu_cfs_throttled_periods_total[15m]))
/
sum by (namespace,pod,container) (rate(container_cpu_cfs_periods_total[15m]))

# PSI: share of wall time fully stalled on memory (1.34+ beta / 1.36 GA)
sum by (namespace,pod,container) (rate(container_pressure_memory_stalled_seconds_total[15m]))

# OOM kills, counter — not the flapping last_terminated_reason gauge
sum by (namespace,pod,container) (increase(container_oom_events_total[1h]))
```

> Note the second correction hiding in that last query: Rev 2 detected OOM via `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}`, a **gauge on the current pod object** that resets when the pod is replaced. A counter (`container_oom_events_total`, corroborated by cgroup `memory.events`'s `oom_kill` field, §23) survives pod replacement.

### 21.5 Additions to §16 Resilience (rehearsal-specific)

| Pattern | Rev 3 application | Failure it prevents |
|---|---|---|
| **Lease-based mutual exclusion** | A rehearsal takes a Kubernetes `Lease` (`coordination.k8s.io`) named per target workload; no second rehearsal can start | Two rehearsals resizing the same workload into an unpredictable state |
| **Dead-man's-switch watchdog** | The rehearsal writes `revert_deadline` to the DB **before** it resizes, and a separate CronJob reverts anything past its deadline | Analyser pod dies mid-rehearsal, leaving a live pod at experimental sizes |
| **Kill switch** | `kubethrifty.io/rehearsal: disabled` annotation on a namespace/workload, plus a global Helm value, both checked at admission of the job | An owner needs rehearsals off *now* without a redeploy |
| **Preflight admission** | Rehearsal refuses unless: replicas ≥ 2, PDB allows one disrupted pod, no active rollout, target not owned by VPA in `Auto` mode, node has headroom for an *upward* rehearsal | Rehearsing a singleton, or fighting another controller |
| **Blast-radius cap** | Max 1 pod per workload, max N concurrent rehearsals per cluster (Helm value), never in namespaces labelled `kubethrifty.io/tier: critical` | A bad rehearsal wave across the fleet |
| **Bounded observation** | Hard timeout on every rehearsal; timeout ⇒ revert + `INCONCLUSIVE`, never `SAFE` | Silent "success" from a rehearsal nobody watched |

### 21.6 Additions to §6 Database Design (complete migration)

```sql
-- migrations/003_rev3_evidence.sql
-- New in Rev 3: PSI/cgroup evidence, rehearsals, and detective verdicts.

-- 1. PSI + cgroup truth samples (§23). Hypertable, like metric_samples.
CREATE TABLE psi_samples (
    time              TIMESTAMPTZ      NOT NULL,
    cluster           TEXT             NOT NULL,
    namespace         TEXT             NOT NULL,
    pod               TEXT             NOT NULL,
    container         TEXT             NOT NULL,
    cpu_some_seconds  DOUBLE PRECISION,   -- counter
    cpu_full_seconds  DOUBLE PRECISION,
    mem_some_seconds  DOUBLE PRECISION,
    mem_full_seconds  DOUBLE PRECISION,
    io_full_seconds   DOUBLE PRECISION,
    memory_current    BIGINT,             -- cgroup v2 memory.current
    memory_peak       BIGINT,             -- cgroup v2 memory.peak  <-- sizing basis
    memory_max        BIGINT,             -- cgroup v2 memory.max (the real limit)
    oom_kill_total    BIGINT,             -- cgroup v2 memory.events oom_kill
    throttled_periods BIGINT,
    cfs_periods       BIGINT,
    source            TEXT NOT NULL DEFAULT 'cadvisor'  -- 'cadvisor' | 'cgroupfs'
);
SELECT create_hypertable('psi_samples', 'time', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON psi_samples (cluster, namespace, pod, container, time DESC);
ALTER TABLE psi_samples SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'cluster,namespace,pod,container');
SELECT add_compression_policy('psi_samples', INTERVAL '7 days');
SELECT add_retention_policy('psi_samples', INTERVAL '90 days');

-- 2. Rehearsals (§22) — one row per in-place experiment.
CREATE TYPE rehearsal_outcome AS ENUM
    ('pending','running','safe','regressed','inconclusive','reverted_by_watchdog');

CREATE TABLE rehearsals (
    id                BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT REFERENCES recommendations(id) ON DELETE CASCADE,
    cluster           TEXT        NOT NULL,
    namespace         TEXT        NOT NULL,
    pod               TEXT        NOT NULL,
    container         TEXT        NOT NULL,
    original_requests JSONB       NOT NULL,   -- exactly what we must restore
    original_limits   JSONB       NOT NULL,
    candidate_requests JSONB      NOT NULL,
    candidate_limits  JSONB       NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    revert_deadline   TIMESTAMPTZ NOT NULL,  -- watchdog contract (§21.5)
    ended_at          TIMESTAMPTZ,
    outcome           rehearsal_outcome NOT NULL DEFAULT 'pending',
    resize_conditions JSONB,                 -- PodResizePending/InProgress trail
    signals_before    JSONB,                 -- Window (§21.4)
    signals_after     JSONB,
    restarts_observed INT NOT NULL DEFAULT 0,
    reasons           TEXT[],
    evidence_bundle_sha256 TEXT              -- ties to detective bundle (§26)
);
CREATE INDEX ON rehearsals (cluster, namespace, pod, started_at DESC);
CREATE INDEX ON rehearsals (outcome) WHERE outcome IN ('pending','running');

-- 3. ThriftDetective verdicts (§26).
CREATE TABLE verdicts (
    id              BIGSERIAL PRIMARY KEY,
    bundle_sha256   TEXT UNIQUE NOT NULL,     -- content-addressed evidence
    cluster         TEXT NOT NULL,
    namespace       TEXT NOT NULL,
    workload        TEXT NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    rule_id         TEXT NOT NULL,            -- e.g. 'MEM_LIMIT_TOO_LOW'
    confidence      NUMERIC(4,3) NOT NULL,    -- 0.000..1.000, calibrated (§26.5)
    attributed_change JSONB,                  -- {pr, commit, recommendation_id, resize_event}
    evidence        JSONB NOT NULL,
    ruleset_version TEXT NOT NULL,
    engine_sha      TEXT NOT NULL,            -- determinism anchor
    ground_truth    TEXT                      -- backfilled for calibration scoring
);
CREATE INDEX ON verdicts (cluster, namespace, workload, observed_at DESC);
CREATE INDEX ON verdicts (rule_id, observed_at DESC);

-- 4. Sizing basis becomes explicit on recommendations (§21.3).
ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS binding_constraint TEXT,   -- peak_margin|forecast|rehearsed|absolute_min
    ADD COLUMN IF NOT EXISTS sizing_basis TEXT,         -- 'peak' for memory, 'p95' for cpu
    ADD COLUMN IF NOT EXISTS rehearsal_id BIGINT REFERENCES rehearsals(id),
    ADD COLUMN IF NOT EXISTS hpa_coupled BOOLEAN NOT NULL DEFAULT FALSE;
```

### 21.7 Additions to §8 Design Patterns

| Pattern | Rev 3 use | Why it earns its place |
|---|---|---|
| **Saga / compensating transaction** | Rehearsal = `resize → observe → compensate (revert)`. The compensation is registered (in the DB, with a deadline) **before** the forward action | The textbook answer for "a distributed operation you must be able to undo without a rollback log" |
| **State machine (explicit)** | `rehearsal_outcome` enum + guarded transitions; illegal transitions raise | Keeps a long-running, crash-prone workflow auditable |
| **Rules engine + evidence graph** | ThriftDetective (§26): facts → rules → ranked verdicts, no imperative if-tree | Testable, versionable, and *explainable* — an LLM cannot be regression-tested this way |
| **Strategy (already present) extended** | Sizing strategy per resource kind (`size_cpu` / `size_memory` / `size_device`) rather than one function with flags | Makes the CPU-vs-memory asymmetry structural instead of a comment |

### 21.8 Additions to §18 Mitigation Strategies

| # | Failure mode | Blast radius | Detection | Mitigation |
|---|---|---|---|---|
| 15 | Rehearsal resize is rejected (`PodResizePending: Infeasible`) | One pod, no impact | Pod condition | Mark `INCONCLUSIVE`, fall back to forecast-only sizing, record node headroom |
| 16 | Rehearsal stuck `Deferred` (node has no room to grow back) | One pod | Condition + timeout | Revert path is *downward*, always feasible; watchdog reverts at deadline |
| 17 | Analyser dies mid-rehearsal | One pod left at experimental size | Watchdog CronJob scans `revert_deadline < now()` | Automatic revert; row marked `reverted_by_watchdog`; alert fires |
| 18 | Memory rehearsal restarts the container | One pod restart | `restarts_observed > 0` | Memory rehearsals default to `resizePolicy: NotRequired` and refuse targets that declare `RestartContainer` unless explicitly opted in |
| 19 | PSI metrics unavailable (kernel/runtime/Windows node) | Evidence quality | Empty `container_pressure_*` series | Degrade to throttle+OOM signals; label recommendations `evidence: partial` — never silently pretend |
| 20 | cgroup DaemonSet cannot read `/sys/fs/cgroup` | Peak-based sizing | Read error metric | Fall back to `max_over_time()` from Prometheus; annotate that peaks may be understated between scrapes |
| 21 | HPA fights a merged change (§24) | One workload's replica count and cost | Replica-count delta vs prediction | Refuse the recommendation unless the HPA target is co-changed; alert on divergence post-merge |
| 22 | Detective ruleset regression | Recommendation trust | Calibration + accuracy eval in CI (§26.5) | Build fails; ruleset version pinned; verdicts replayable from bundles |

### 21.9 Update to §19.3 — the VPA / KRR / Goldilocks defence, 2026 edition

The competitive answer changed this year, and knowing *why* is the signal.

| Tool | What it does now | KubeThrifty's deliberate difference |
|---|---|---|
| **VPA** | Recommends and applies; the non-disruptive `InPlaceOrRecreate` mode exists but is **alpha and off by default** as of 2026, so in practice VPA still evicts to apply | KubeThrifty uses the **GA** in-place primitive directly — not to actuate silently, but to **rehearse** (§22) and then propose a reviewable PR |
| **KRR** (Robusta) | CLI recommender over Prometheus history | Point-in-time, read-only, no GitOps artefact, no forecast, no experiment, no verification |
| **Goldilocks** (Fairwinds) | Dashboards VPA's recommendations | Surfaces numbers; no cost model, no node-count impact, no closed loop |
| **Cast AI / ScaleOps / commercial rightsizers** | Continuous automated rightsizing, often with node-level optimisation | Closed-source, agent-in-cluster, per-cluster pricing, and *trust-by-vendor*. KubeThrifty's equivalent claim is auditable: every recommendation carries its evidence bundle and, where available, a rehearsal result |
| **Karpenter / EKS Auto Mode** | Optimise **nodes** to fit requests; Auto Mode adds ≈12% on EC2 On-Demand for the managed experience | They optimise the *supply* side and take requests as given. KubeThrifty optimises the *demand* side, which is upstream — and under Auto Mode, over-provisioned requests cost ≈12% more than the same waste self-managed (§25.4) |

> The sentence to say out loud: *"I'm not competing with VPA at actuation or with Karpenter at provisioning. I'm doing the thing both of them assume someone else already did — getting the requests right — and I'm the only one of them that runs an experiment on a live pod before asking a human to merge the change."*

### 21.10 Additions to §12 Interview Prep

**Q: In-place resize is GA — why not just auto-apply and skip the PR?**
Because the resource number is a *config decision with an owner*, and the owner is the team that gets paged. Auto-apply is exactly why teams distrust VPA. I use the in-place primitive for the part that benefits from automation — the *experiment* — and leave the durable change to review. Also: an in-place resize does not update the Deployment template, so the next rollout would silently undo it. The PR is what makes the change survive a rollout.

**Q: Why is sizing memory off P95 wrong?**
Because P95 discards the top 5% of observations, and for memory those observations are the ones that OOMKill you. CPU over-limit degrades gracefully via CFS throttling; memory over-limit is a SIGKILL. So CPU uses P95 with a margin and memory uses observed peak — ideally cgroup `memory.peak`, which is the kernel's own high-water mark rather than a sampled maximum.

**Q: `top` inside my container says the node's memory. Why?**
Because `/proc/meminfo` is not namespaced — it reflects the host. The container's real limits and usage live in cgroup v2 files: `memory.max`, `memory.current`, `memory.peak`, and `memory.events`. `docker stats` and cAdvisor read cgroups; `free` and `top` read `/proc`. That's also why my collector reads cgroupfs rather than trusting anything that shells out to `free` (§23).

**Q: What's the difference between throttling and PSI?**
Throttling tells you the kernel *capped* you against your CPU limit. PSI tells you how much wall-clock time tasks actually spent *stalled waiting* for CPU, memory or I/O — including contention that has nothing to do with your limit. A pod can be throttled and fine (batch work), or unthrottled and suffering (memory reclaim). Sizing on throttling alone gets both cases wrong, which is why the safety gate uses both.

**Q: What happens if two of your components disagree — the forecast says grow, the rehearsal says the smaller size is safe?**
The forecast wins, because it's a statement about the *future* and the rehearsal is a statement about the *observed present*. Both are floors in the same `max()` (§21.3); I never take a minimum of safety signals. The PR body prints which floor bound the decision, so the reviewer sees exactly why the number is what it is.

**Q: Isn't rehearsing in production reckless?**
It's the opposite of reckless — it's the only way to get evidence instead of a prediction. But it's fenced: one pod per workload, replicas ≥ 2, PDB-aware, hard timeout, a revert deadline written to the database *before* the resize, an independent watchdog that reverts anything past deadline, a per-namespace kill switch, and no rehearsals in namespaces labelled critical. Worst case is one pod runs at a different size for a few minutes and then goes back.

---

## 22. Resize Rehearsal — Zero-Restart In-Cluster Experiments (D3, headline differentiator)

### 22.1 The idea

Every right-sizing tool in existence produces a **prediction**: *"P95 says 200m is enough."* KubeThrifty produces an **experiment**:

> *"I set this pod to 250m CPU / 320Mi for 30 minutes under real production traffic. It served 41k requests, was throttled 0.4% of periods (baseline 0.3%), recorded zero OOM events, memory peaked at 268Mi, memory PSI 'full' stayed at 0.0%, and the container never restarted. Here is the evidence bundle. Now, should we merge it?"*

This was **not buildable before Kubernetes 1.35**. Until in-place pod resize went GA, changing a pod's resources meant recreating the pod — so a "trial" was a restart, and nobody trials in production with restarts. The GA `resize` subresource makes a *reversible, restart-free, single-pod* experiment possible, and almost nothing in the ecosystem has adapted to that yet. Building on a three-release-old GA primitive that the incumbents have not caught up to is the definition of a defensible differentiator.

### 22.2 Mechanics you must be able to explain

```bash
# Resources are patched through the resize SUBRESOURCE, not the pod spec.
kubectl patch pod payment-processor-7d9f -n shop --subresource resize --patch \
  '{"spec":{"containers":[{"name":"app","resources":{
      "requests":{"cpu":"250m","memory":"320Mi"},
      "limits":{"cpu":"1","memory":"320Mi"}}}]}}'

# What the container ACTUALLY has now (desired vs actual is the whole model):
kubectl get pod payment-processor-7d9f -n shop \
  -o jsonpath='{.status.containerStatuses[0].resources}{"\n"}'

# Why a resize is stuck:
kubectl get pod payment-processor-7d9f -n shop \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason} {.message}{"\n"}{end}'
```

| Concept | Detail that matters |
|---|---|
| `spec.containers[*].resources` | **Desired** resources; mutable for CPU/memory via the subresource |
| `status.containerStatuses[*].resources` | **Actual** resources currently configured on the running container. The rehearsal asserts on *this*, never on spec |
| `PodResizePending` | API accepted, kubelet can't do it yet. `reason: Deferred` (no room now — may clear) or `reason: Infeasible` (impossible on this node — will not clear). **Neither is an error, and a resize can stay pending indefinitely** — so the rehearsal must have a timeout, not a wait-forever |
| `PodResizeInProgress` | kubelet is applying it |
| CPU resize | Applies transparently; **no restart** |
| Memory resize | Updates the cgroup in place **unless** the container declares `resizePolicy: RestartContainer` for memory. Shrinking memory below current usage is where the kernel's opinion matters — this is precisely why the rehearsal watches OOM events and PSI |
| kubectl version | `--subresource` needs kubectl ≥ 1.32 |
| Template vs pod | An in-place resize **does not** change the Deployment template. The next rollout reverts it. This is a *feature* for a rehearsal (self-cleaning) and the reason the durable change still needs the PR |

### 22.3 The rehearsal state machine

```
                    ┌──────────────────────────────────────────┐
                    │ candidate sizing from §14 + §21.3        │
                    └───────────────────┬──────────────────────┘
                                        ▼
                        ┌───────────────────────────────┐
                        │ PREFLIGHT (§22.4)             │
                        │ replicas>=2? PDB ok? no        │
                        │ rollout? not VPA-Auto? not     │
                        │ critical ns? kill switch off?  │
                        └───────┬───────────────┬───────┘
                            fail│               │pass
                                ▼               ▼
                        skip (forecast-  ┌──────────────────────────┐
                        only sizing)     │ RECORD compensation +    │
                                         │ revert_deadline in DB    │
                                         └────────────┬─────────────┘
                                                      ▼
                                         ┌──────────────────────────┐
                                         │ BASELINE window (T_b)    │
                                         │ signals_before (§21.4)   │
                                         └────────────┬─────────────┘
                                                      ▼
                                         ┌──────────────────────────┐
                                         │ RESIZE (subresource)     │
                                         │ poll conditions          │
                                         └────┬──────────────┬──────┘
                                    Infeasible│              │applied
                                              ▼              ▼
                                     INCONCLUSIVE   ┌──────────────────┐
                                                    │ OBSERVE (T_o)    │
                                                    │ PSI/throttle/OOM │
                                                    └───┬──────────┬───┘
                                                   clean│          │trip
                                                        ▼          ▼
                                                     SAFE      REGRESSED
                                                        │          │
                                                        └────┬─────┘
                                                             ▼
                                              ┌───────────────────────────┐
                                              │ REVERT in place (always)  │
                                              │ assert actual == original │
                                              └───────────┬───────────────┘
                                                          ▼
                                          SAFE → rehearsed_floor feeds sizing;
                                          evidence table goes in the PR body
                                          REGRESSED → widen margin, re-plan,
                                          record as negative training signal
```

**The rehearsal always reverts.** It is an experiment, not an actuation. The only thing that changes the cluster durably is a merged PR.

### 22.4 Preflight safety envelope

```python
# analyser/src/rehearsal/preflight.py
from dataclasses import dataclass

CRITICAL_NS_LABEL = "kubethrifty.io/tier"
DISABLE_ANNOTATION = "kubethrifty.io/rehearsal"     # value "disabled"


@dataclass
class Preflight:
    ok: bool
    reason: str = ""


def check(k8s, cluster_cfg, ns: str, workload: str, pod: str) -> Preflight:
    if not cluster_cfg.rehearsal_enabled:
        return Preflight(False, "rehearsals globally disabled (Helm value)")

    namespace = k8s.core.read_namespace(ns)
    if (namespace.metadata.labels or {}).get(CRITICAL_NS_LABEL) == "critical":
        return Preflight(False, f"namespace {ns} is labelled critical")

    dep = k8s.apps.read_namespaced_deployment(workload, ns)
    if (dep.metadata.annotations or {}).get(DISABLE_ANNOTATION) == "disabled":
        return Preflight(False, "workload opted out via annotation")
    if (dep.spec.replicas or 1) < 2:
        return Preflight(False, "single-replica workload — no safe blast radius")
    if dep.status.updated_replicas != dep.status.replicas:
        return Preflight(False, "rollout in progress")

    # Don't fight another vertical autoscaler.
    if k8s.has_vpa_in_auto_mode(ns, workload):
        return Preflight(False, "VPA in Auto/InPlaceOrRecreate mode owns this workload")

    # PDB must tolerate one disrupted pod even though we don't expect disruption.
    if not k8s.pdb_allows_one_disruption(ns, workload):
        return Preflight(False, "PodDisruptionBudget has no headroom")

    if k8s.concurrent_rehearsals(cluster_cfg.name) >= cluster_cfg.max_concurrent:
        return Preflight(False, "cluster rehearsal concurrency cap reached")

    return Preflight(True)
```

RBAC — deliberately minimal, and a good thing to show an interviewer:

```yaml
# charts/kubethrifty/templates/rbac-rehearsal.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubethrifty-rehearsal
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/resize"]          # the ONLY mutating permission we hold
    verbs: ["patch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets"]
    verbs: ["get", "list"]
  - apiGroups: ["policy"]
    resources: ["poddisruptionbudgets"]
    verbs: ["get", "list"]
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["get", "create", "update"]
  - apiGroups: ["autoscaling"]
    resources: ["horizontalpodautoscalers"]
    verbs: ["get", "list"]              # for §24
```

> Soundbite: *"KubeThrifty's only write permission in the whole cluster is `patch` on `pods/resize`. It cannot delete a pod, cannot edit a Deployment, cannot touch a Secret. When I say the blast radius is one pod for a few minutes, that's enforced by RBAC, not by my good intentions."*

### 22.5 The rehearsal runner (complete file)

The official Python client does not expose the `resize` subresource as a typed method, so this calls it through the generic `call_api` path — worth knowing, because "the SDK doesn't have it yet" is exactly the kind of friction a real 2026 implementation hits.

```python
# analyser/src/rehearsal/runner.py
"""
Resize Rehearsal: apply a candidate sizing in place to ONE live pod, observe,
then always revert. Requires Kubernetes >= 1.35 (in-place resize GA).

Compensation-first: the revert payload and deadline are persisted BEFORE the
forward resize, so a crashed runner cannot leave a pod resized (§21.5).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass
from typing import Optional

from kubernetes import client

from ..verification.signals import Window, evaluate

POLL_INTERVAL_S = 5


@dataclass
class RehearsalPlan:
    namespace: str
    pod: str
    container: str
    candidate_requests: dict          # {"cpu": "250m", "memory": "320Mi"}
    candidate_limits: dict
    baseline_seconds: int = 300
    observe_seconds: int = 1800
    resize_timeout_seconds: int = 120


@dataclass
class RehearsalResult:
    outcome: str                      # safe | regressed | inconclusive
    reasons: list
    evidence: dict
    restarts_observed: int
    proven_floor: Optional[dict] = None   # the candidate, if SAFE


class RehearsalRunner:
    def __init__(self, api: client.ApiClient, prom, store, clock=dt.datetime.utcnow):
        self.api = api
        self.core = client.CoreV1Api(api)
        self.prom = prom
        self.store = store            # DB gateway (rehearsals table, §21.6)
        self.clock = clock

    # ---------------------------------------------------------------- plumbing
    def _patch_resize(self, ns: str, pod: str, container: str,
                      requests: dict, limits: dict) -> None:
        """PATCH /api/v1/namespaces/{ns}/pods/{pod}/resize"""
        body = {"spec": {"containers": [{
            "name": container,
            "resources": {"requests": requests, "limits": limits},
        }]}}
        self.api.call_api(
            "/api/v1/namespaces/{namespace}/pods/{name}/resize", "PATCH",
            path_params={"namespace": ns, "name": pod},
            body=body,
            header_params={"Content-Type": "application/strategic-merge-patch+json",
                           "Accept": "application/json"},
            auth_settings=["BearerToken"], _preload_content=True,
        )

    def _actual_resources(self, ns: str, pod: str, container: str) -> dict:
        p = self.core.read_namespaced_pod(pod, ns)
        for cs in p.status.container_statuses or []:
            if cs.name == container:
                return (cs.resources.to_dict() if cs.resources else {}) or {}
        return {}

    def _restart_count(self, ns: str, pod: str, container: str) -> int:
        p = self.core.read_namespaced_pod(pod, ns)
        for cs in p.status.container_statuses or []:
            if cs.name == container:
                return cs.restart_count or 0
        return 0

    def _conditions(self, ns: str, pod: str) -> dict:
        p = self.core.read_namespaced_pod(pod, ns)
        return {c.type: {"status": c.status, "reason": getattr(c, "reason", None),
                         "message": getattr(c, "message", None)}
                for c in (p.status.conditions or [])}

    def _await_applied(self, plan: RehearsalPlan) -> tuple[bool, dict]:
        """True once status shows the candidate; False on Infeasible/timeout."""
        deadline = time.monotonic() + plan.resize_timeout_seconds
        last = {}
        while time.monotonic() < deadline:
            conds = self._conditions(plan.namespace, plan.pod)
            last = conds
            pending = conds.get("PodResizePending", {})
            if pending.get("status") == "True" and pending.get("reason") == "Infeasible":
                return False, conds
            actual = self._actual_resources(plan.namespace, plan.pod, plan.container)
            req = (actual.get("requests") or {})
            if all(str(req.get(k)) == str(v) for k, v in plan.candidate_requests.items()):
                return True, conds
            time.sleep(POLL_INTERVAL_S)
        return False, last

    def _window(self, plan: RehearsalPlan, seconds: int) -> Window:
        """Collect one signal window from Prometheus (ratios, not counters)."""
        w = f"{seconds}s"
        sel = f'namespace="{plan.namespace}",pod="{plan.pod}",container="{plan.container}"'
        q = self.prom.instant
        return Window(
            throttled_periods=q(f'sum(increase(container_cpu_cfs_throttled_periods_total{{{sel}}}[{w}]))'),
            cfs_periods=q(f'sum(increase(container_cpu_cfs_periods_total{{{sel}}}[{w}]))'),
            restarts=q(f'sum(increase(kube_pod_container_status_restarts_total{{{sel}}}[{w}]))'),
            oom_kills=q(f'sum(increase(container_oom_events_total{{{sel}}}[{w}]))'),
            psi_cpu_full=q(f'sum(rate(container_pressure_cpu_stalled_seconds_total{{{sel}}}[{w}]))'),
            psi_mem_full=q(f'sum(rate(container_pressure_memory_stalled_seconds_total{{{sel}}}[{w}]))'),
        )

    # ------------------------------------------------------------------- driver
    def run(self, plan: RehearsalPlan, recommendation_id: int) -> RehearsalResult:
        original = self._actual_resources(plan.namespace, plan.pod, plan.container)
        orig_req = original.get("requests") or {}
        orig_lim = original.get("limits") or {}
        if not orig_req:
            return RehearsalResult("inconclusive", ["could not read actual resources"], {}, 0)

        # 1. Compensation FIRST (§21.5) — watchdog contract.
        deadline = self.clock() + dt.timedelta(
            seconds=plan.observe_seconds + plan.resize_timeout_seconds + 600)
        rehearsal_id = self.store.open_rehearsal(
            recommendation_id=recommendation_id, plan=plan,
            original_requests=orig_req, original_limits=orig_lim,
            revert_deadline=deadline)

        restarts_before = self._restart_count(plan.namespace, plan.pod, plan.container)
        try:
            # 2. Baseline.
            before = self._window(plan, plan.baseline_seconds)
            time.sleep(plan.baseline_seconds)

            # 3. Forward action.
            self._patch_resize(plan.namespace, plan.pod, plan.container,
                               plan.candidate_requests, plan.candidate_limits)
            applied, conds = self._await_applied(plan)
            self.store.record_conditions(rehearsal_id, conds)
            if not applied:
                return self._finish(rehearsal_id, plan, orig_req, orig_lim,
                                    "inconclusive",
                                    ["resize not applied (Infeasible or timed out)"],
                                    {"conditions": conds}, 0)

            # 4. Observe.
            self.store.mark_running(rehearsal_id)
            time.sleep(plan.observe_seconds)
            after = self._window(plan, plan.observe_seconds)
            restarts = self._restart_count(plan.namespace, plan.pod,
                                          plan.container) - restarts_before

            verdict = evaluate(before, after)
            if restarts > 0:
                verdict.regressed = True
                verdict.reasons.append(f"container restarted {restarts}× during rehearsal")

            outcome = "regressed" if verdict.regressed else "safe"
            return self._finish(rehearsal_id, plan, orig_req, orig_lim, outcome,
                                verdict.reasons, verdict.evidence, restarts)
        except Exception as exc:                       # noqa: BLE001 — must always revert
            self._revert(plan, orig_req, orig_lim)
            self.store.close_rehearsal(rehearsal_id, "inconclusive",
                                       [f"exception: {exc}"], {}, 0)
            raise

    # 5. Revert is unconditional.
    def _revert(self, plan: RehearsalPlan, req: dict, lim: dict) -> None:
        self._patch_resize(plan.namespace, plan.pod, plan.container, req, lim)

    def _finish(self, rehearsal_id, plan, req, lim, outcome, reasons,
                evidence, restarts) -> RehearsalResult:
        self._revert(plan, req, lim)
        actual = self._actual_resources(plan.namespace, plan.pod, plan.container)
        evidence["reverted_to"] = actual.get("requests")
        self.store.close_rehearsal(rehearsal_id, outcome, reasons, evidence, restarts)
        return RehearsalResult(
            outcome=outcome, reasons=reasons, evidence=evidence,
            restarts_observed=restarts,
            proven_floor=(dict(plan.candidate_requests) if outcome == "safe" else None),
        )
```

The watchdog that makes the compensation real (a `CronJob`, deliberately *not* in the same process):

```python
# analyser/src/rehearsal/watchdog.py
def sweep(store, runner) -> int:
    """Revert any rehearsal whose deadline has passed. Runs every 2 minutes."""
    reverted = 0
    for r in store.overdue_rehearsals():
        runner._revert(r.plan, r.original_requests, r.original_limits)
        store.close_rehearsal(r.id, "reverted_by_watchdog",
                              ["revert deadline exceeded"], {}, 0)
        reverted += 1
    return reverted          # exported as kubethrifty_rehearsals_watchdog_reverts_total
```

### 22.6 Sidecars and pod-level rehearsals

Sidecar-heavy pods (service mesh proxies, log shippers) are where a lot of real waste lives, and per-container sizing handles them badly because containers in a pod contend for the same node. With **pod-level resources** (beta since 1.34) the pod holds an aggregate budget, and **in-place pod-level resize** (beta in 1.36) lets you change that budget on a running pod:

```bash
kubectl patch pod shared-pool-app -n shop --subresource resize --patch \
  '{"spec":{"resources":{"limits":{"cpu":"4"}}}}'
```

KubeThrifty detects `spec.resources` on the pod and switches to **pod-level rehearsal mode**: it rehearses the aggregate budget instead of a single container, and reports "istio-proxy requests 500m, uses 30m; the pod budget can drop from 2.5 to 1.2 CPU." Flag this in the plan as **beta-gated**: it goes in the demo and the README's "what's next," and you say the words "pod-level in-place resize is beta, so I gate it behind a Helm value" — which is exactly the maturity signal an interviewer is listening for.

### 22.7 Warm-up-aware two-phase sizing (the trick nobody else can express)

Right-sizing has a structural conflict: JVM/Node/dotnet workloads need several times more CPU during startup (JIT, class loading, cache fill) than in steady state. Size for steady state and startup takes minutes or fails its probes; size for startup and you pay for idle CPU forever. Every percentile-based recommender resolves this by over-provisioning permanently.

In-place resize dissolves the conflict:

```
t=0    pod starts with STARTUP profile   (e.g. 1000m CPU)
t=~45s readiness probe passes + warmup metric settles
t=~60s controller resizes IN PLACE down to STEADY profile (e.g. 200m)
       → no restart, no rollout, no lost traffic
```

KubeThrifty ships this as an **advisory** (a recommendation type `two_phase`, with the two profiles and the trigger condition it measured), not as a controller in the MVP — building a general startup-boost controller is a separate product. But measuring and *proposing* it costs almost nothing on top of §23's data, and it is a genuinely novel recommendation class:

```
RECOMMENDATION two_phase  payment-processor/app
  startup window  : 52s (p95 across 41 pod starts)
  startup peak    : 940m CPU
  steady p95      : 180m CPU
  proposal        : requests.cpu 1000m at start → in-place resize to 250m
                    once readiness=true for 30s
  savings basis   : 750m × 24h/day of steady state, minus 60s/pod-start
```

### 22.8 What the PR body looks like (this is the artefact interviewers remember)

```markdown
## KubeThrifty: right-size `payment-processor` (shop)

| | CPU request | CPU limit | Memory request | Memory limit |
|---|---|---|---|---|
| current | 1000m | 2000m | 1Gi | 1Gi |
| proposed | 250m | 1000m | 320Mi | 320Mi |

**Sizing basis** — CPU: P95 180m × 1.20 margin, floored by the 14-day forecast
upper bound (210m). Memory: observed peak 268Mi × 1.25 (cgroup `memory.peak`,
not P95 — memory is incompressible). Binding constraint: `forecast` (CPU),
`peak_margin` (memory).

### ✅ Rehearsed on a live pod — no restart
`payment-processor-7d9f`, 30 min, 2026-08-16 14:02–14:32 UTC

| Signal | Baseline (before) | Rehearsal (after) | Verdict |
|---|---|---|---|
| CPU throttle ratio | 0.3% | 0.4% | ok (< +5pp) |
| Memory PSI `full` | 0.0% | 0.0% | ok |
| CPU PSI `full` | 0.1% | 0.2% | ok |
| OOM events | 0 | 0 | ok |
| Container restarts | — | 0 | ok |
| Peak memory | 262Mi | 268Mi | 84% of proposed request |

Evidence bundle: `sha256:9f2c…` (replay: `thriftctl replay 9f2c…`)

### 💰 Cost impact
Cluster-level bin-packing (§25): **4 × m5.xlarge → 3 × m5.xlarge**
(-1 node, ≈₹9,400/month at pinned on-demand rates; ≈₹10,500 under EKS Auto
Mode's ~12% surcharge).

### ⚠️ HPA interaction
`payment-processor` is HPA-managed (target: 70% CPU utilisation). Lowering the
request from 1000m → 250m would make the same load read as 4× utilisation and
trigger scale-out. This PR therefore **also** proposes
`averageUtilization: 70 → 55` (see §24). Predicted replicas: 3 → 3.

*Rollback: if OOMKills, throttling or PSI regress within 48h of merge,
KubeThrifty will open a revert PR automatically (§15).*
```

### 22.9 Scaling decision to document (the second one for §11)

> **Decision: rehearsals are serialised per workload and capped per cluster (default 3 concurrent), and they are *not* run for every recommendation.**
> Rehearsing is the most expensive thing KubeThrifty does — it costs wall-clock time (baseline + observe ≈ 35 min) and it touches production. So the analyser ranks candidates by `projected_node_savings × confidence` and rehearses only the top N per run; everything else ships with forecast-only sizing and is labelled `evidence: modelled` instead of `evidence: rehearsed`. That's a deliberate throughput-vs-assurance trade: I'd rather have three changes with proof than thirty with predictions, because the three get merged.

### 22.10 The challenge questions you will get

| Question | Answer |
|---|---|
| "What if the pod gets rescheduled mid-rehearsal?" | The rehearsal keys on pod UID; if the pod disappears the runner records `INCONCLUSIVE` and no revert is needed (the new pod comes up from the template with original values). The watchdog handles the case where the pod exists but the runner died |
| "30 minutes isn't a business cycle." | Correct — a rehearsal proves *absence of immediate harm at this size under current traffic*, not safety across a weekly peak. That's exactly why the rehearsal is one floor among three: the forecast (§14) covers the cycle, the rehearsal covers the mechanism, and post-merge verification (§15) covers the real world for 48h |
| "Why not just rehearse on a canary pod you create?" | Creating a pod changes scheduling and warms nothing; the point is to observe a pod already serving real traffic with real cache state. Also, creating pods would require `create` on pods — I'd rather hold only `patch` on `pods/resize` |
| "Does this work on GKE Autopilot / managed clusters?" | Depends on the provider's supported minor and whether the runtime is containerd 2.x. The rehearsal degrades cleanly: if the resize returns `Infeasible` or PSI series are empty, recommendations fall back to modelled evidence and say so. Never claim rehearsed evidence you don't have |

---

## 23. Evidence-Grade Signals: PSI + cgroup Truth (D4)

### 23.1 Two problems percentile tools cannot solve

**Problem 1 — a percentile cannot tell you whether anyone suffered.** Two containers both run at 90% of their CPU limit. One is a batch encoder that does not care. The other is a checkout API whose p99 latency has doubled because it spent 30% of wall-clock time stalled waiting for CPU. `container_cpu_usage_seconds_total` is identical for both. Every recommender that reads only usage is guessing about harm.

**Problem 2 — container memory numbers lie.** Inside a container, `free`, `top` and anything else that reads `/proc/meminfo` reports the **host's** memory, because `/proc` is not namespaced. The real numbers live in cgroup v2:

| What you want | Wrong source | Right source |
|---|---|---|
| The container's memory limit | `free -m` total | `memory.max` |
| Current usage | `top` RES | `memory.current` |
| **High-water mark (what to size on)** | sampled max of scraped usage | **`memory.peak`** — the kernel's own high-water mark, immune to scrape gaps |
| Was it OOM-killed | flapping pod-status gauge | `memory.events` → `oom_kill` counter |
| Was it throttled | "CPU looks high" | `cpu.stat` → `nr_throttled`, `throttled_usec` |
| Did it stall | *no equivalent exists in usage metrics* | `cpu.pressure`, `memory.pressure`, `io.pressure` (PSI) |

Kubernetes **1.35+ requires cgroup v2**, so this is not an exotic dependency any more — it's the baseline. That's what makes this a 2026-shaped feature.

### 23.2 The signal taxonomy KubeThrifty reasons over

| Layer | Metric | Question it answers |
|---|---|---|
| **Reservation** | `kube_pod_container_resource_requests` | What are we *paying* for? |
| **Consumption** | `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes` | What did we *use*? |
| **High-water** | cgroup `memory.peak` / `max_over_time()` | What is the *worst case we saw*? (memory sizing basis, §21.3) |
| **Enforcement** | `cpu.stat` throttling, `container_cpu_cfs_throttled_periods_total` | Did the *limit* bite? |
| **Suffering** | **PSI** `container_pressure_{cpu,memory,io}_{stalled,waiting}_seconds_total` | Did anyone actually *wait*? |
| **Death** | `memory.events` `oom_kill`, `container_oom_events_total` | Did we *kill* it? |

Right-sizing decisions use all six. Competing tools use the first two.

### 23.3 PSI in Kubernetes — the facts to have straight

- **`some`** = at least one task stalled on the resource (early contention signal). **`full`** = *all* non-idle tasks stalled simultaneously (severe). Kernel exposes rolling `avg10 / avg60 / avg300` plus a `total` counter in nanoseconds.
- Kubernetes surfaces PSI two ways: the kubelet **Summary API** (node/pod/container) and the kubelet **`/metrics/cadvisor`** endpoint in Prometheus format. Beta (default-on) since **1.34** via the `KubeletPSI` gate; **GA in 1.36**.
- Requirements: Linux kernel ≥ 4.20 compiled with `CONFIG_PSI` (some distros need `psi=1` on the kernel command line), **cgroup v2**, and **containerd 2.x** (PSI support is not in the 1.7 line). Not available on Windows nodes.
- Counters are in **seconds** — differentiate them: `rate(container_pressure_cpu_stalled_seconds_total[1m])` gives the fraction of the last minute spent stalled.
- Node-level PSI is *not* a good scale-out trigger on its own (a single starved pod can push node `some` to ~99% while the node is idle). Reason at **container/pod** level — which is exactly the level right-sizing operates at.

```promql
# Per-container stall share (0..1). The core safety signal.
sum by (namespace,pod,container) (rate(container_pressure_memory_stalled_seconds_total[15m]))
sum by (namespace,pod,container) (rate(container_pressure_cpu_stalled_seconds_total[15m]))

# "Waiting" is broader than "stalled" — useful as an early-warning panel.
sum by (namespace,pod,container) (rate(container_pressure_cpu_waiting_seconds_total[15m]))
```

Add the kubelet cAdvisor endpoint to the scrape set (kube-prometheus-stack ships the ServiceMonitor; make sure the `/metrics/cadvisor` path is enabled and the honor-labels config keeps `pod`/`container`).

### 23.4 The cgroup-truth collector (complete file + DaemonSet)

cAdvisor gives sampled usage; the cgroup files give the kernel's own accounting. The collector is a tiny DaemonSet that reads the pod cgroup tree read-only and pushes to a Pushgateway-free textfile-style endpoint (or exposes `/metrics` for Prometheus to scrape).

```python
# collector/cgroup_truth.py
"""
KubeThrifty cgroup-truth collector (DaemonSet, one pod per node).

Reads the kernel's own accounting for every container cgroup:
  memory.current, memory.peak, memory.max, memory.events(oom_kill),
  cpu.stat(nr_throttled, nr_periods), memory.pressure / cpu.pressure

Why this exists: sampled usage from cAdvisor can miss a spike between scrapes;
memory.peak cannot. And nothing inside a container can be trusted to report
its own limits, because /proc is not namespaced.

Runs read-only. Requires cgroup v2 (Kubernetes >= 1.35 mandates it).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional

CGROUP_ROOT = Path(os.getenv("CGROUP_ROOT", "/host/sys/fs/cgroup"))
# kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod<UID>.slice/cri-containerd-<ID>.scope
POD_DIR_RE = re.compile(r"kubepods-(?:burstable-|besteffort-)?pod([0-9a-f_]{36})\.slice$")


@dataclass
class ContainerTruth:
    pod_uid: str
    container_id: str
    memory_current: Optional[int]
    memory_peak: Optional[int]
    memory_max: Optional[int]          # None == "max" (unlimited)
    oom_kill_total: Optional[int]
    nr_periods: Optional[int]
    nr_throttled: Optional[int]
    throttled_usec: Optional[int]
    mem_pressure_full_avg60: Optional[float]
    cpu_pressure_full_avg60: Optional[float]


def _read_int(p: Path) -> Optional[int]:
    try:
        raw = p.read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return None if raw == "max" else int(raw)


def _read_kv(p: Path) -> dict:
    try:
        return {k: int(v) for k, v in
                (line.split() for line in p.read_text().splitlines() if " " in line)}
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return {}


def _read_pressure(p: Path, kind: str = "full", field: str = "avg60") -> Optional[float]:
    """cpu.pressure / memory.pressure format:
       some avg10=0.00 avg60=0.00 avg300=0.00 total=0
       full avg10=0.00 avg60=0.00 avg300=0.00 total=0
    """
    try:
        for line in p.read_text().splitlines():
            parts = line.split()
            if parts and parts[0] == kind:
                for kv in parts[1:]:
                    k, _, v = kv.partition("=")
                    if k == field:
                        return float(v)
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None
    return None


def walk() -> Iterator[ContainerTruth]:
    for pod_dir in CGROUP_ROOT.rglob("kubepods*pod*.slice"):
        m = POD_DIR_RE.search(pod_dir.name)
        if not m:
            continue
        pod_uid = m.group(1).replace("_", "-")
        for cdir in pod_dir.iterdir():
            if not cdir.is_dir():
                continue
            cpu_stat = _read_kv(cdir / "cpu.stat")
            mem_events = _read_kv(cdir / "memory.events")
            yield ContainerTruth(
                pod_uid=pod_uid,
                container_id=cdir.name,
                memory_current=_read_int(cdir / "memory.current"),
                memory_peak=_read_int(cdir / "memory.peak"),
                memory_max=_read_int(cdir / "memory.max"),
                oom_kill_total=mem_events.get("oom_kill"),
                nr_periods=cpu_stat.get("nr_periods"),
                nr_throttled=cpu_stat.get("nr_throttled"),
                throttled_usec=cpu_stat.get("throttled_usec"),
                mem_pressure_full_avg60=_read_pressure(cdir / "memory.pressure"),
                cpu_pressure_full_avg60=_read_pressure(cdir / "cpu.pressure"),
            )


def render_prometheus() -> str:
    """Expose on :9847/metrics; scraped by a PodMonitor."""
    lines = [
        "# HELP kubethrifty_cgroup_memory_peak_bytes Kernel high-water memory mark",
        "# TYPE kubethrifty_cgroup_memory_peak_bytes gauge",
    ]
    for c in walk():
        labels = f'pod_uid="{c.pod_uid}",container_id="{c.container_id}"'
        if c.memory_peak is not None:
            lines.append(f"kubethrifty_cgroup_memory_peak_bytes{{{labels}}} {c.memory_peak}")
        if c.oom_kill_total is not None:
            lines.append(f"kubethrifty_cgroup_oom_kill_total{{{labels}}} {c.oom_kill_total}")
        if c.nr_throttled is not None and c.nr_periods:
            lines.append(
                f"kubethrifty_cgroup_throttle_ratio{{{labels}}} "
                f"{c.nr_throttled / c.nr_periods:.6f}")
    return "\n".join(lines) + "\n"
```

```yaml
# charts/kubethrifty/templates/daemonset-cgroup-truth.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kubethrifty-cgroup-truth
  labels: {app.kubernetes.io/name: kubethrifty, component: cgroup-truth}
spec:
  selector:
    matchLabels: {app.kubernetes.io/name: kubethrifty, component: cgroup-truth}
  template:
    metadata:
      labels: {app.kubernetes.io/name: kubethrifty, component: cgroup-truth}
    spec:
      # No host network, no privileged, no writes anywhere.
      automountServiceAccountToken: false
      nodeSelector: {kubernetes.io/os: linux}
      tolerations: [{operator: Exists}]          # observe every node, incl. tainted
      containers:
        - name: collector
          image: ghcr.io/OWNER/kubethrifty-cgroup-truth:1.0.0   # never :latest
          args: ["--listen=:9847", "--interval=30s"]
          ports: [{name: metrics, containerPort: 9847}]
          env: [{name: CGROUP_ROOT, value: /host/sys/fs/cgroup}]
          securityContext:
            runAsNonRoot: true
            runAsUser: 65534
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
            seccompProfile: {type: RuntimeDefault}
          resources:                              # eat your own dog food
            requests: {cpu: 10m, memory: 32Mi}
            limits: {cpu: 100m, memory: 64Mi}
          volumeMounts:
            - {name: cgroup, mountPath: /host/sys/fs/cgroup, readOnly: true}
      volumes:
        - name: cgroup
          hostPath: {path: /sys/fs/cgroup, type: Directory}
```

> Interview soundbite: *"My collector requests 10m CPU and 32Mi and is capped at 100m/64Mi — because a cost-optimisation tool that is itself over-provisioned is a joke. It's read-only, non-root, no service-account token, drops all capabilities, and its only mount is `/sys/fs/cgroup` read-only. That's the security review answer for 'you want to run a DaemonSet with a hostPath?'"*

**Degradation policy:** if the collector is absent or the node lacks PSI, recommendations are stamped `evidence: partial` and memory falls back to `max_over_time()` with a note that peaks between scrapes may be missed. KubeThrifty never upgrades the confidence of a recommendation it could not corroborate.

### 23.5 The Headroom Index — one number per container

Percentile-only tools give you utilisation. KubeThrifty gives a **safety-adjusted** score in `[0, 1]`, where high means "safe to cut."

```python
# analyser/src/scoring.py
def headroom_index(*, request: float, peak: float,
                   psi_full: float, throttle_ratio: float,
                   oom_events: int, samples: int, min_samples: int = 168) -> float:
    """
    1.0  = large unused reservation AND no observed suffering  -> cut confidently
    0.0  = no slack, or evidence of suffering                  -> do not cut
    """
    if oom_events > 0 or samples < min_samples:
        return 0.0                                  # hard stops, not soft penalties
    slack = max(0.0, 1.0 - (peak / request)) if request > 0 else 0.0
    suffering = min(1.0, psi_full / 0.05) * 0.7 + min(1.0, throttle_ratio / 0.05) * 0.3
    return round(max(0.0, slack * (1.0 - suffering)), 3)
```

Two workloads at 20% utilisation get different answers: the quiet one scores ~0.8 and gets a recommendation; the one stalling on memory reclaim (a cache thrashing against its limit while its *average* usage looks low) scores ~0.0 and gets left alone. **That case — low utilisation, high pressure — is a workload every percentile tool would happily shrink into an outage.** It is the single best example to bring to an interview.

### 23.6 Dashboard and eval wiring

- **Dashboard:** the existing waste heatmap gains a second axis. X = unused reservation, Y = pressure. The top-left quadrant (high waste, no pressure) is the "safe to cut" list; the bottom-right (low waste, high pressure) is the "needs *more* resources" list — and shipping *increase* recommendations is what makes the tool credible as an SRE tool rather than a cost-cutting tool.
- **Grafana panels to add:** per-namespace PSI stall share; throttle-ratio top-10; `memory.peak / request` ratio distribution; collector scrape health.
- **Eval harness (extends §14.4):** the labelled fixture set gains three cases — `low_util_high_pressure` (must produce **no** cut), `spiky_peak_gt_p95` (must size memory off peak, not P95), and `throttled_at_current_limit` (must not lower the CPU limit). These are assertions on *decisions*, and they fail the build if the sizing logic regresses:

```python
# evals/sizing_eval.py — gates CI alongside forecast_eval.py
CASES = [
    ("low_util_high_pressure",      "memory", "expect_no_reduction"),
    ("spiky_peak_gt_p95",           "memory", "expect_request_ge_peak"),
    ("throttled_at_current_limit",  "cpu",    "expect_limit_not_reduced"),
    ("steady_idle",                 "cpu",    "expect_reduction"),
    ("rising_trend",                "cpu",    "expect_forecast_floor_binds"),
    ("noisy_short",                 "cpu",    "expect_needs_review"),
]
# Each case asserts on the DECISION, not on a float. A sizing change that
# breaks a safety case fails the build exactly like a unit test.
```

---

## 24. HPA-Collision Guard: the Right-Sizing Cost Paradox (D5)

### 24.1 The paradox, with arithmetic

The HPA's CPU target is **a percentage of the request**, not an absolute value:

```
utilisation% = actual_cpu / requested_cpu × 100
desired_replicas ≈ ceil(current_replicas × utilisation% / target%)
```

Take a workload: 3 replicas, request 1000m, actual 200m each, HPA target 70%.

```
BEFORE right-sizing:  utilisation = 200/1000  = 20%   → target 70% → stays at 3 replicas
                      total reservation = 3 × 1000m = 3000m

AFTER a naive cut to 250m (P95 × margin):
                      utilisation = 200/250   = 80%   → target 70% → scales OUT
                      desired = ceil(3 × 80/70) = 4 replicas
                      total reservation = 4 × 250m = 1000m   ← still a win
```

That case is fine. Now the same maths with a tighter cut to 220m and a target of 60%:

```
                      utilisation = 200/220 = 91%  → target 60% → desired = ceil(3 × 91/60) = 5
                      total reservation = 5 × 220m = 1100m
                      ...and 5 pods now consume 5 × 200m = 1000m of ACTUAL CPU
                      where 3 pods consumed 600m — because each replica carries
                      fixed overhead (JVM heap, sidecar, connection pools).
```

**Reservation went down, real consumption and pod count went up, and on a bin-packed cluster the node count can go up with them.** Memory is worse: replica count multiplies the per-pod memory floor, so a memory cut plus a scale-out can *increase* total memory reservation outright.

No point-in-time recommender models this. VPA's own documentation warns against combining VPA on CPU with HPA on CPU for exactly this reason — and KRR/Goldilocks simply hand you a number and let you find out.

### 24.2 Detection

```python
# analyser/src/coupling/detect.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class Autoscaler:
    kind: str                 # "hpa" | "keda" | "vpa" | None
    name: str
    metric: str               # "cpu" | "memory" | "external" | ...
    target_utilization: Optional[int]
    min_replicas: int
    max_replicas: int
    current_replicas: int


def find_autoscaler(k8s, ns: str, workload: str) -> Optional[Autoscaler]:
    for hpa in k8s.autoscaling.list_namespaced_horizontal_pod_autoscaler(ns).items:
        ref = hpa.spec.scale_target_ref
        if ref.name != workload:
            continue
        # KEDA creates an HPA it owns; report it as KEDA so the advice differs.
        owned_by_keda = any(o.kind == "ScaledObject"
                            for o in (hpa.metadata.owner_references or []))
        for m in (hpa.spec.metrics or []):
            if m.type == "Resource" and m.resource.name in ("cpu", "memory"):
                return Autoscaler(
                    kind="keda" if owned_by_keda else "hpa",
                    name=hpa.metadata.name,
                    metric=m.resource.name,
                    target_utilization=m.resource.target.average_utilization,
                    min_replicas=hpa.spec.min_replicas or 1,
                    max_replicas=hpa.spec.max_replicas,
                    current_replicas=hpa.status.current_replicas or 1,
                )
        return Autoscaler("keda" if owned_by_keda else "hpa", hpa.metadata.name,
                          "external", None, hpa.spec.min_replicas or 1,
                          hpa.spec.max_replicas, hpa.status.current_replicas or 1)
    return None
```

### 24.3 Coupled simulation

```python
# analyser/src/coupling/simulate.py
import math
from dataclasses import dataclass


@dataclass
class CoupledOutcome:
    replicas_before: int
    replicas_after: int
    reservation_before: float      # millicores or MiB, total across replicas
    reservation_after: float
    verdict: str                   # "safe" | "co_change_target" | "refuse"
    suggested_target: int | None
    note: str


def simulate(*, actual_per_pod: float, request_before: float, request_after: float,
             target_pct: int, replicas: int, min_r: int, max_r: int,
             hysteresis: float = 0.10) -> CoupledOutcome:
    def desired(req: float, tgt: int) -> int:
        util = (actual_per_pod / req) * 100 if req else 0
        return max(min_r, min(max_r, math.ceil(replicas * util / tgt)))

    r_before = desired(request_before, target_pct)
    r_after = desired(request_after, target_pct)
    res_before, res_after = r_before * request_before, r_after * request_after

    if r_after <= r_before and res_after < res_before * (1 - hysteresis):
        return CoupledOutcome(r_before, r_after, res_before, res_after,
                              "safe", None, "no replica growth; reservation falls")

    # Solve for the target that holds replica count flat at the new request.
    util_after = (actual_per_pod / request_after) * 100
    suggested = int(min(90, max(30, math.ceil(util_after / (r_before / replicas)))))
    r_with_target = desired(request_after, suggested)

    if r_with_target <= r_before and r_after * request_after < res_before:
        return CoupledOutcome(
            r_before, r_with_target, res_before, r_with_target * request_after,
            "co_change_target", suggested,
            f"request cut requires HPA target {target_pct}% → {suggested}% to hold "
            f"{r_before} replicas")

    return CoupledOutcome(r_before, r_after, res_before, res_after, "refuse", None,
                          "cut would trigger scale-out that erases or reverses the "
                          "saving; skipping until the HPA target is re-derived")
```

Policy: `safe` → normal PR. `co_change_target` → **one PR containing both the resource change and the HPA target change**, because shipping them separately guarantees a bad intermediate state. `refuse` → no recommendation; the workload appears on the dashboard under "coupled — needs owner decision," with the arithmetic shown.

### 24.4 Two more collision guards in the same module

**QoS class transitions.** Changing requests/limits can move a pod between QoS classes, which changes its **eviction priority** under node pressure. A `Guaranteed` pod (requests == limits on *every* container) is evicted last; `Burstable` next; `BestEffort` first. So a cut that happens to make requests ≠ limits silently demotes a critical workload's survival odds. KubeThrifty computes the class before and after and blocks any **Guaranteed → Burstable** transition unless the workload is explicitly annotated to allow it. Nobody's dashboard tells you this.

**Sibling contention.** Sidecars share the pod; cutting the app container's request while a mesh proxy keeps a fat one relocates rather than removes waste. When pod-level resources are in play (§22.6), reason about the pod budget.

### 24.5 The soundbite

> *"The best question I got about this project was 'does right-sizing always save money?' — and the answer is no, and that's the interesting part. The HPA scales on CPU utilisation *as a percentage of requests*. If I halve the requests, the same traffic reads as double the utilisation, the HPA scales out, and I've traded reserved CPU for more pods — each with its own JVM heap and sidecar. On a bin-packed cluster that can cost more than the waste I removed. So KubeThrifty detects HPA and KEDA ownership, simulates the coupled replica count, and either ships the resource change *together with* a re-derived HPA target in one PR, or refuses and shows the owner the arithmetic. It also refuses any change that would demote a pod from Guaranteed to Burstable, because that quietly changes its eviction priority."*

---

## 25. Consolidation-Aware Savings: requests → nodes → ₹ (D6)

### 25.1 The waste stack (say this in the interview, in this order)

```
you are BILLED for      : node-hours (or vCPU-hours under Auto Mode/Autopilot)
node CAPACITY            : what the instance physically has
node ALLOCATABLE         : capacity − kube-reserved − system-reserved − eviction-threshold
what the SCHEDULER packs : sum of pod REQUESTS (+ DaemonSets on every node)
what the app USES        : actual consumption
```

Cost optimisation is the business of shrinking the gap between **requests** and **usage** *until a node disappears*. Everything before that is a metric, not money. Karpenter and Cluster Autoscaler act on the top half of that stack (supply); KubeThrifty acts on the bottom half (demand). Both are needed, and the demand side is upstream.

### 25.2 Bin-packing with real overheads (complete file)

Rev 2's first-fit-decreasing sketch ignored four things that dominate the answer: DaemonSets land on every node, allocatable ≠ capacity, there is a **pod-count cap** (commonly 110/node), and instance choice is a decision variable.

```python
# analyser/src/packing/binpack.py
"""
Cluster-level 'how many nodes do we actually need' simulation.

First-fit-decreasing over (cpu, memory, pod-count) with per-node overheads.
FFD is chosen deliberately: bin-packing is NP-hard, FFD is within 11/9 of
optimal for the 1-D case, it is deterministic (so results are reproducible in
CI), and it mirrors how the kube-scheduler behaves closely enough for a cost
estimate. We are not writing a scheduler; we are bounding a bill.
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

    # Reserved by kubelet/OS before anything schedules. Approximates GKE/EKS
    # reservation curves; the exact numbers are provider-specific and pinned
    # in instances.json so the estimate is reproducible.
    def allocatable(self) -> tuple[int, int]:
        cpu_reserved = min(self.cpu_millicores * 0.06, 400)
        mem_reserved = min(self.memory_mib * 0.10, 4096) + 100   # + eviction threshold
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
    for ds in daemonsets:                 # DaemonSets tax EVERY node
        if not n.fits(ds):
            raise ValueError(f"{inst.name} cannot host the DaemonSet set")
        n.place(ds)
    return n


def pack(pods: Iterable[PodRequest], inst: InstanceType) -> list[Node]:
    workload = [p for p in pods if not p.daemonset]
    daemonsets = [p for p in pods if p.daemonset]
    # Decreasing by the more binding dimension, normalised to allocatable.
    cpu_alloc, mem_alloc = inst.allocatable()
    workload.sort(key=lambda p: max(p.cpu_millicores / cpu_alloc,
                                    p.memory_mib / mem_alloc), reverse=True)
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
    cpu_utilisation: float          # requests / allocatable, averaged
    mem_utilisation: float


def evaluate(pods: list[PodRequest], catalog: list[InstanceType],
             hours_per_month: float = 730.0) -> list[PackingResult]:
    out = []
    for inst in catalog:
        try:
            nodes = pack(pods, inst)
        except ValueError:
            continue
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
        "headline": (f"{b.nodes} × {b.instance} → {a.nodes} × {a.instance} "
                     f"(−{b.nodes - a.nodes} nodes)"),
    }
```

### 25.3 The pinned instance catalogue

Prices change; a *reproducible estimate* matters more than a live price feed for a portfolio project. Commit the catalogue with an "as of" date, and say so out loud — an interviewer who hears "I pinned the price list with a date and a source so my savings numbers are reproducible" hears a FinOps engineer.

```json
// config/instances.json — prices as of 2026-08-01, Mumbai (ap-south-1), on-demand.
// Source + date recorded so every ₹ figure in the README is reproducible.
{
  "as_of": "2026-08-01",
  "currency": "INR",
  "region": "ap-south-1",
  "pricing_model": "on_demand",
  "instances": [
    {"name": "m5.large",    "cpu_millicores": 2000,  "memory_mib": 8192,   "hourly_inr": 8.9,  "max_pods": 29},
    {"name": "m5.xlarge",   "cpu_millicores": 4000,  "memory_mib": 16384,  "hourly_inr": 17.8, "max_pods": 58},
    {"name": "m5.2xlarge",  "cpu_millicores": 8000,  "memory_mib": 32768,  "hourly_inr": 35.6, "max_pods": 58},
    {"name": "c5.xlarge",   "cpu_millicores": 4000,  "memory_mib": 8192,   "hourly_inr": 15.7, "max_pods": 58},
    {"name": "r5.xlarge",   "cpu_millicores": 4000,  "memory_mib": 32768,  "hourly_inr": 23.4, "max_pods": 58}
  ],
  "scenarios": {
    "self_managed_karpenter": {"surcharge_pct": 0.0},
    "eks_auto_mode":          {"surcharge_pct": 12.0,
                               "note": "managed Karpenter; ~12% premium on EC2 on-demand"}
  }
}
```

### 25.4 The three cost scenarios KubeThrifty reports

| Scenario | What the model does | The line that lands |
|---|---|---|
| **Fixed node group** | Pack into the pinned instance type; savings = node delta × price | "Right-sizing removes one m5.xlarge: ₹13,000/month." |
| **Self-managed Karpenter** | Let the packer *choose* the instance type per scenario (it may consolidate onto a different, cheaper shape) | "Karpenter would consolidate this onto 2 × c5.xlarge instead of 4 × m5.xlarge — but only after the requests come down, because Karpenter provisions to *requests*, not to usage." |
| **EKS Auto Mode** | Same packing, ×1.12 | "Auto Mode is convenient, and it also means my un-right-sized requests cost ~12% *more* than the same waste on self-managed Karpenter. Fixing requests is the highest-leverage thing you can do *before* paying for managed autoscaling." |

Two clarifications worth having ready, because both are common interviewer traps:

1. **Node autoscalers do not fix requests.** Karpenter and Auto Mode both provision capacity to satisfy the requests they're given. Feed them 4× over-provisioned requests and they will faithfully buy 4× the nodes. This is why request optimisation is upstream of node optimisation, and why KubeThrifty is complementary to Karpenter rather than competing with it.
2. **Consolidation needs slack to work.** Karpenter can only consolidate if pods can be rescheduled — which means PDBs, `do-not-disrupt` annotations and topology constraints all bound the theoretical saving. KubeThrifty's report flags workloads whose PDB blocks consolidation, so the number is honest about what is *achievable*, not just arithmetically possible.

### 25.5 Reporting

- **Dashboard "cluster" tab:** the waste stack as a funnel (billed → capacity → allocatable → requested → used), with the packing simulation beneath it and a scenario selector for the three cases above.
- **README headline (corrected per §21.2):** *"The 5-service demo requests 5.5 CPU / 6.5 GB and peaks at 1.7 CPU / 2.6 GB. Right-sized, it bin-packs from 4 nodes onto 1 — ₹X/month at pinned Mumbai on-demand rates (catalogue dated, in-repo), or ₹Y under EKS Auto Mode's ~12% premium."*
- **In every PR:** the node-delta line from `savings_report`, never a bare per-pod rupee figure.

---

## 26. ThriftDetective — Deterministic, Change-Attributed Incident Investigation (D7)

### 26.0 A verification note, stated up front

I could not independently verify the KubeTective project (repo, rule count, or scenario corpus) from public sources while preparing Rev 3 — searches surfaced adjacent tools (HolmesGPT/Robusta, k8sgpt, KubeGraf, detek, MCP-based "RootCause" servers) but not that project itself. So this section treats the description you gave — *deterministic, rule-based, no AI backend, CLI + kubectl plugin + server, 11 failure modes, recorded investigations, regression-tested against 16 real incident scenarios* — as the specification to beat, and everything below is designed against it. **Before you put a competitive claim on your README, check the actual project's current README yourself and adjust the table in §26.9.** Making a comparative claim you haven't verified is the one way this differentiator can backfire in an interview.

### 26.1 The decision: integrate a narrow version, not a clone

| Option | Verdict | Reasoning |
|---|---|---|
| **Clone KubeTective inside KubeThrifty** (general 11-mode cluster debugger) | ❌ **No** | It's a different product with a different job-to-be-done. It doubles the surface area of an 8-week project, and the general-purpose incident-investigation space is crowded (HolmesGPT, k8sgpt, detek, commercial AIOps). You would be the weakest entrant in someone else's category. |
| **Depend on it as an external tool** | ⚠️ Only if it exists and is stable | Adds an unverified dependency and a version to track, for a feature you can implement narrowly in ~300 lines. Reasonable as a *post-MVP integration*: shell out, parse its JSON, attach it as extra evidence. |
| **Ignore incident investigation entirely** | ❌ No | KubeThrifty already *needs* it. §15's verification watcher answers "did it regress?" but not "**why**, and was it my fault?" Without that, an auto-rollback is a shrug. |
| **Build a narrow, change-attributed investigator scoped to resource decisions** | ✅ **Yes — this is the answer** | It's the missing half of the closed loop, it reuses §21.4's signals and §23's evidence, it's ~300 lines of pure deterministic code, and it does one thing no general investigator can: **attribute the incident to a specific resource change that KubeThrifty itself proposed.** |

**The strategic framing to say out loud:** *"I didn't clone the incident-investigation tool. I built the 20% of it that my product can attribute to a decision I made — and I made its confidence numbers honest, which is the part that's actually hard."*

### 26.2 Scope: 8 resource-centric failure modes

A general investigator covers image pulls, DNS, RBAC, admission webhooks, PVC binding, network policy — none of which a right-sizer can cause or fix. ThriftDetective covers only what resource decisions produce:

| Rule ID | Failure mode | Primary evidence | Can a KubeThrifty change cause it? |
|---|---|---|---|
| `MEM_LIMIT_TOO_LOW` | OOMKill | `memory.events.oom_kill` ↑, `memory.peak` ≈ `memory.max`, exit 137 | **Yes** — the one that must never be silent |
| `CPU_LIMIT_TOO_LOW` | Latency from throttling | throttle ratio ↑, `cpu.pressure full` ↑, no OOM | **Yes** |
| `MEM_PRESSURE_NO_KILL` | Reclaim thrash (slow, not dead) | `memory.pressure full` ↑, `memory.current` pinned near max, usage *looks* fine | **Yes** — invisible to every percentile tool |
| `NODE_PRESSURE_EVICTION` | Pod evicted for node-level pressure | Node conditions, eviction event, sibling pods' usage | **Contributory** (over-commit) |
| `QOS_DEMOTION_EVICTION` | Evicted earlier than expected after a class change | QoS class before/after, eviction ordering | **Yes** (§24.4 exists to prevent this) |
| `HPA_COUPLING_STORM` | Replica oscillation / cost spike after a request change | Replica timeline vs request change, HPA events | **Yes** (§24 exists to prevent this) |
| `STARTUP_STARVATION` | Readiness/liveness failures at boot only | Failures inside the startup window, startup CPU peak ≫ steady | **Yes** (§22.7 exists to prevent this) |
| `RESIZE_INFEASIBLE_STUCK` | Resize stuck `Deferred`/`Infeasible` | Pod conditions, node allocatable headroom | **Yes** — self-inflicted by the rehearsal path |

Everything else emits `NO_VERDICT` with an explicit *"outside ThriftDetective's scope — this is not a resource-shaped incident"*, which is a far better answer than a low-confidence guess. **Knowing what your tool refuses to opine on is a seniority signal.**

### 26.3 Architecture: facts → rules → ranked verdicts

```
                 ┌────────────────────────────────────────────────┐
                 │  EVIDENCE COLLECTION (read-only, bounded)      │
                 │  • pod status, conditions, exit codes, events  │
                 │  • PSI + cgroup truth (§23)                    │
                 │  • throttle/OOM/restart windows (§21.4)        │
                 │  • KubeThrifty's OWN change log:               │
                 │    recommendations, merged PRs, resize events  │
                 └───────────────────────┬────────────────────────┘
                                         ▼
                 ┌────────────────────────────────────────────────┐
                 │  EVIDENCE BUNDLE  (immutable, content-addressed│
                 │  JSON; sha256 is the incident's identity)      │
                 └───────────────────────┬────────────────────────┘
                                         ▼
                 ┌────────────────────────────────────────────────┐
                 │  RULE ENGINE (pure function, no I/O, no LLM)   │
                 │  bundle → [Verdict(rule, confidence, evidence,│
                 │            attributed_change)]                 │
                 └───────────────────────┬────────────────────────┘
                        ┌────────────────┼────────────────┐
                        ▼                ▼                ▼
              PR comment /      Recommendation      Optional narrative
              K8s Event /       engine feedback     (LLM adapter — never
              dashboard         (margin learning)   decides, only phrases)
```

The rule engine is a **pure function of the bundle**. That single design choice buys three things a fresher rarely has: deterministic replay, unit-testable verdicts, and byte-identical output in CI.

```python
# detective/engine.py
"""
ThriftDetective rule engine.

CONTRACT: verdict(bundle) is a pure function. Same bundle -> byte-identical
verdicts, forever. No network, no clock, no LLM, no randomness. That property
is asserted in CI (§26.5) by hashing the output of the whole corpus.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Callable, Optional

RULESET_VERSION = "1.3.0"


@dataclass(frozen=True)
class Verdict:
    rule_id: str
    confidence: float                 # calibrated (§26.5), 0..1
    summary: str
    evidence: dict
    attributed_change: Optional[dict] = None
    remediation: str = ""


Rule = Callable[[dict], Optional[Verdict]]
RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    RULES.append(fn)
    return fn


# ---------------------------------------------------------------------- helpers
def _attribute(b: dict, window_hours: int = 48) -> Optional[dict]:
    """
    The moat: did WE cause this? Correlate the incident with KubeThrifty's own
    change log — merged right-sizing PRs, and rehearsal/resize events.
    """
    for ch in sorted(b.get("kubethrifty_changes", []),
                     key=lambda c: c["applied_at"], reverse=True):
        if ch["hours_before_incident"] <= window_hours and ch["container"] == b["container"]:
            return {
                "recommendation_id": ch.get("recommendation_id"),
                "pr": ch.get("pr_url"),
                "commit": ch.get("commit"),
                "kind": ch.get("kind"),               # merged_pr | rehearsal | manual
                "hours_before": ch["hours_before_incident"],
                "delta": ch.get("delta"),             # {"memory": "1Gi -> 320Mi"}
            }
    return None


# ------------------------------------------------------------------------ rules
@rule
def mem_limit_too_low(b: dict) -> Optional[Verdict]:
    if b["signals"]["oom_kills"] <= 0:
        return None
    peak, limit = b["cgroup"].get("memory_peak", 0), b["cgroup"].get("memory_max") or 0
    ratio = (peak / limit) if limit else 0.0
    conf = 0.97 if ratio > 0.95 else 0.85 if b["exit_code"] == 137 else 0.70
    change = _attribute(b)
    if change and "memory" in (change.get("delta") or {}):
        conf = min(0.99, conf + 0.02)
    return Verdict(
        "MEM_LIMIT_TOO_LOW", round(conf, 3),
        "Container was OOMKilled; memory limit is below real demand.",
        {"oom_kills": b["signals"]["oom_kills"], "memory_peak": peak,
         "memory_max": limit, "peak_over_limit": round(ratio, 3),
         "exit_code": b["exit_code"]},
        change,
        "Raise memory request/limit to peak × 1.25 and mark the workload "
        "reduction-blocked for 14 days.",
    )


@rule
def mem_pressure_no_kill(b: dict) -> Optional[Verdict]:
    """The one nobody else catches: reclaim thrash without an OOM kill."""
    psi = b["signals"].get("psi_mem_full", 0.0)
    if psi < 0.05 or b["signals"]["oom_kills"] > 0:
        return None
    cur, limit = b["cgroup"].get("memory_current", 0), b["cgroup"].get("memory_max") or 0
    near_limit = bool(limit) and cur / limit > 0.90
    conf = 0.88 if near_limit else 0.62
    return Verdict(
        "MEM_PRESSURE_NO_KILL", round(conf, 3),
        "Sustained memory stall without an OOM kill — the workload is thrashing "
        "reclaim, so average usage understates real demand.",
        {"psi_mem_full": psi, "memory_current": cur, "memory_max": limit,
         "current_over_limit": round(cur / limit, 3) if limit else None},
        _attribute(b),
        "Increase memory; do NOT size this workload from percentile usage.",
    )


@rule
def cpu_limit_too_low(b: dict) -> Optional[Verdict]:
    tr = b["signals"].get("throttle_ratio", 0.0)
    if tr < 0.05:
        return None
    psi = b["signals"].get("psi_cpu_full", 0.0)
    conf = 0.90 if psi > 0.02 else 0.72       # throttling + stall == real harm
    return Verdict(
        "CPU_LIMIT_TOO_LOW", round(conf, 3),
        "CPU limit is throttling the workload.",
        {"throttle_ratio": tr, "psi_cpu_full": psi},
        _attribute(b),
        "Raise the CPU limit (or remove it and let requests schedule); keep the "
        "request as sized.",
    )


@rule
def hpa_coupling_storm(b: dict) -> Optional[Verdict]:
    hpa = b.get("hpa")
    if not hpa:
        return None
    r_before, r_after = hpa.get("replicas_before"), hpa.get("replicas_after")
    change = _attribute(b)
    if not (change and r_before and r_after and r_after > r_before * 1.5):
        return None
    return Verdict(
        "HPA_COUPLING_STORM", 0.93,
        "Replica count grew sharply after a request reduction — the HPA is "
        "reacting to higher utilisation-of-request, not to more traffic.",
        {"replicas_before": r_before, "replicas_after": r_after,
         "hpa_target": hpa.get("target_utilization"),
         "traffic_delta_pct": hpa.get("traffic_delta_pct")},
        change,
        "Re-derive the HPA target for the new request, or revert the request.",
    )


@rule
def resize_infeasible_stuck(b: dict) -> Optional[Verdict]:
    cond = (b.get("conditions") or {}).get("PodResizePending") or {}
    if cond.get("reason") != "Infeasible":
        return None
    return Verdict(
        "RESIZE_INFEASIBLE_STUCK", 0.99,
        "An in-place resize cannot be satisfied on this node.",
        {"condition": cond, "node_allocatable": b.get("node_allocatable")},
        _attribute(b),
        "Abandon the rehearsal (never wait indefinitely); reschedule or "
        "recommend via PR only.",
    )


# ------------------------------------------------------------------- entrypoint
def investigate(bundle: dict) -> list[Verdict]:
    verdicts = [v for v in (r(bundle) for r in RULES) if v]
    verdicts.sort(key=lambda v: v.confidence, reverse=True)
    return verdicts


def bundle_sha256(bundle: dict) -> str:
    return hashlib.sha256(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verdicts_digest(verdicts: list[Verdict]) -> str:
    """Determinism anchor asserted in CI."""
    return hashlib.sha256(
        json.dumps([asdict(v) for v in verdicts], sort_keys=True,
                   separators=(",", ":")).encode()
    ).hexdigest()
```

### 26.4 Change attribution — the actual moat

A general investigator answers *"this pod was OOMKilled and its limit looks low."* ThriftDetective answers:

> `MEM_LIMIT_TOO_LOW` — confidence **0.97**. OOMKilled 14h after **PR #212** (recommendation #1188) reduced `payment-processor/app` memory from **1Gi → 320Mi**. `memory.peak` reached 318Mi against a 320Mi limit. **This incident is attributable to a KubeThrifty change.** Rollback PR #219 opened automatically; workload marked reduction-blocked for 14 days and its memory margin raised from 1.25 to 1.6.

Three things happen there that no external tool can do: it names **its own change** as the cause, it **acts** (rollback PR), and it **learns** (per-workload margin). That closes the loop §15 opened:

```
detect → propose → rehearse → approve → verify → investigate → attribute → learn
```

The learning path is deliberately dumb and auditable (a per-workload margin table with a reason and a timestamp), not a model. *"I widened the margin for this workload because it OOMKilled once at 1.25×"* is a sentence a reviewer can check; a learned embedding is not.

### 26.5 What makes it *better* than the tool it's inspired by: calibrated confidence + a generated corpus

Any rule engine can print `confidence: 0.9`. The hard question — and the one almost no tool answers — is **"when you say 90%, are you right 90% of the time?"** That's calibration, and it's measurable.

```python
# evals/detective_eval.py — gates CI on accuracy AND calibration AND determinism
"""
Three gates:
  1. ACCURACY    — top-1 verdict matches ground truth on the labelled corpus
  2. CALIBRATION — Brier score + Expected Calibration Error (ECE) on confidences
  3. DETERMINISM — verdicts_digest() is byte-identical to the committed digest
"""
import json, sys
from detective.engine import investigate, verdicts_digest, RULESET_VERSION

TARGET_TOP1_ACCURACY = 0.90
MAX_BRIER = 0.08
MAX_ECE = 0.10


def brier(pairs) -> float:            # pairs: [(confidence, was_correct)]
    return sum((c - int(ok)) ** 2 for c, ok in pairs) / len(pairs)


def ece(pairs, bins: int = 10) -> float:
    total, err = len(pairs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(c, ok) for c, ok in pairs if lo < c <= hi]
        if not bucket:
            continue
        conf = sum(c for c, _ in bucket) / len(bucket)
        acc = sum(int(ok) for _, ok in bucket) / len(bucket)
        err += (len(bucket) / total) * abs(conf - acc)
    return err


def main() -> int:
    corpus = json.load(open("evals/corpus/incidents.json"))
    pairs, correct, digests = [], 0, []
    for case in corpus:
        vs = investigate(case["bundle"])
        digests.append(verdicts_digest(vs))
        top = vs[0] if vs else None
        ok = bool(top) and top.rule_id == case["ground_truth"]
        correct += int(ok)
        if top:
            pairs.append((top.confidence, ok))

    acc, b, e = correct / len(corpus), brier(pairs), ece(pairs)
    frozen = json.load(open("evals/corpus/digests.json"))
    determinism_ok = digests == frozen.get(RULESET_VERSION)

    print(f"cases={len(corpus)} top1={acc:.3f} brier={b:.4f} ece={e:.4f} "
          f"deterministic={determinism_ok}")
    failures = []
    if acc < TARGET_TOP1_ACCURACY: failures.append(f"accuracy {acc:.3f}")
    if b > MAX_BRIER:              failures.append(f"brier {b:.4f}")
    if e > MAX_ECE:                failures.append(f"ECE {e:.4f}")
    if not determinism_ok:         failures.append("non-deterministic verdicts")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**And the corpus is generated, not hand-collected.** A hand-curated set of ~16 incidents is a ceiling; a chaos harness is a factory. `evals/corpus/generate.py` drives a `kind` cluster and manufactures labelled incidents on demand:

| Generator | How it induces the failure | Ground truth |
|---|---|---|
| `oom_squeeze` | Deploy a container with a known allocation profile; set memory limit just below its peak | `MEM_LIMIT_TOO_LOW` |
| `reclaim_thrash` | Working set ≈ 95% of limit with continuous page-cache churn — high PSI, no OOM | `MEM_PRESSURE_NO_KILL` |
| `throttle_squeeze` | Busy-loop with a CPU limit at 40% of demand | `CPU_LIMIT_TOO_LOW` |
| `hpa_paradox` | HPA at 70%, then cut requests 4× in place; observe replica growth with flat traffic | `HPA_COUPLING_STORM` |
| `infeasible_resize` | Request an in-place growth larger than node allocatable | `RESIZE_INFEASIBLE_STUCK` |
| `qos_demotion` | Change limits so `Guaranteed → Burstable`, then apply node memory pressure | `QOS_DEMOTION_EVICTION` |
| `startup_starve` | JVM-like warmup with steady-state-sized CPU; liveness fails during boot only | `STARTUP_STARVATION` |
| `red_herring` | Image-pull failure / DNS failure with healthy resources | `NO_VERDICT` (must refuse!) |

Each generator emits `(bundle, ground_truth)` and the bundles are committed, so CI replays them **hermetically** — no cluster needed in CI, and the cluster is only needed to *regenerate*. Target ≥ 25 cases across 8 rules, including at least three `NO_VERDICT` red herrings, because a tool that never says "I don't know" is worthless.

> Interview gold: *"My investigator has a test suite, and the test suite scores the confidence numbers, not just the verdicts. I measure Brier score and expected calibration error against a labelled corpus, and CI fails if 'confidence 0.9' stops meaning 'right 90% of the time.' I also hash the verdicts for the whole corpus, so any change that makes the engine non-deterministic fails the build. And the corpus is generated by a chaos harness rather than collected by hand, so I can add a failure mode and get twenty labelled cases the same afternoon — including red herrings the engine is required to refuse."*

### 26.6 Evidence bundles: replayable, portable, signed

```json
{
  "schema": "kubethrifty.detective/v1",
  "collected_at": "2026-08-16T14:41:02Z",
  "cluster": "demo", "namespace": "shop",
  "workload": "payment-processor", "pod": "payment-processor-7d9f", "container": "app",
  "exit_code": 137,
  "conditions": {"PodResizePending": {"status": "False"}},
  "signals": {"oom_kills": 1, "restarts_delta": 1, "throttle_ratio": 0.004,
              "psi_mem_full": 0.11, "psi_cpu_full": 0.002},
  "cgroup": {"memory_current": 333447168, "memory_peak": 333447168,
             "memory_max": 335544320, "oom_kill_total": 1},
  "hpa": null,
  "node_allocatable": {"cpu_millicores": 3760, "memory_mib": 14580},
  "kubethrifty_changes": [
    {"kind": "merged_pr", "pr_url": "https://github.com/…/pull/212",
     "recommendation_id": 1188, "commit": "a1b2c3d",
     "applied_at": "2026-08-16T00:12:00Z", "hours_before_incident": 14.5,
     "container": "app", "delta": {"memory": "1Gi -> 320Mi"}}
  ],
  "collector_versions": {"cgroup_truth": "1.0.0", "engine": "1.3.0"}
}
```

- **Content-addressed**: `sha256` of the canonical JSON *is* the incident ID; it links `rehearsals`, `verdicts` and `recommendations` rows (§21.6).
- **Replayable offline**: `thriftctl replay <sha256>` re-runs the engine on the stored bundle with **no cluster access** — the demo moment is replaying a production incident on a laptop, on a plane, and getting the identical verdict.
- **Signed**: bundles and container images are signed with **cosign (keyless / OIDC)** in CI, and the PR body links the attestation. "My evidence is signed and my images have provenance" is a cheap, strong supply-chain answer.
- **kubectl plugin parity**: ship `kubectl-thrift` (a symlink-named binary) so `kubectl thrift investigate pod/x -n shop` works — the same engine, three front doors (library, CLI, in-cluster server), which is the ergonomics story.

### 26.7 The optional LLM layer — and why it never decides

Provider-agnostic adapter (consistent with the rest of the portfolio), used **only** to turn a verdict into prose for the PR comment or a Slack message:

```python
# detective/narrative.py
class Narrator:
    """Turns a Verdict into human prose. NEVER produces or alters a verdict."""
    def narrate(self, verdict, bundle) -> str: ...

class TemplateNarrator(Narrator):        # DEFAULT — deterministic, offline, free
    def narrate(self, verdict, bundle) -> str:
        return (f"{verdict.rule_id} ({verdict.confidence:.0%}): {verdict.summary} "
                f"Evidence: {verdict.evidence}. Suggested: {verdict.remediation}")

class LLMNarrator(Narrator):             # OPTIONAL — behind config, model from config
    def __init__(self, client, model_id: str, max_tokens: int = 400): ...
    def narrate(self, verdict, bundle) -> str:
        # Prompt contains the verdict + evidence and instructs: rephrase only.
        # Output is discarded if it mentions a rule_id not in the verdict list.
        ...
```

The rule that makes this defensible: **the verdict is computed before the LLM is called, and the LLM's output is validated against it.** If the narrator hallucinates a different cause, the output is dropped and the template is used. So the feature is *"AI where it adds clarity, never where it adds risk"* — and it's off by default, which means the whole product works with no API key and no network.

### 26.8 How ThriftDetective aims to beat the general-purpose investigator

| Dimension | Typical deterministic investigator (per the spec you described) | **ThriftDetective** |
|---|---|---|
| **Scope** | Broad: ~11 general cluster failure modes | Narrow: 8 resource-shaped modes, everything else explicitly `NO_VERDICT` |
| **Change attribution** | Identifies a likely cause | Names **the specific PR / recommendation / resize event** that caused it, with a time delta |
| **Acts on its conclusion** | Reports | Opens the rollback PR, blocks further reduction, widens that workload's margin |
| **Evidence depth** | Events, statuses, logs, metrics | + **PSI** and **cgroup truth** (`memory.peak`, `memory.events`) — pressure, not just usage |
| **Confidence numbers** | Confidence scores, provenance unclear | **Calibrated and CI-gated** on Brier score + ECE |
| **Regression corpus** | ~16 hand-collected real scenarios | **≥25 chaos-generated labelled scenarios**, regenerable, with mandatory red herrings |
| **Determinism guarantee** | Replayable investigations | Replayable **plus** a committed hash of all corpus verdicts — non-determinism fails the build |
| **Counterfactual evidence** | None available | The **rehearsal record** (§22): "this size was observed safe at 14:02, and OOMKilled at 22:15 — what changed was traffic, not the size" |
| **Recurrence memory** | Per-investigation | TimescaleDB history: "this workload has produced `MEM_PRESSURE_NO_KILL` 4× in 30 days" — recurrence raises confidence and escalates to a permanent policy |
| **Interfaces** | CLI, kubectl plugin, server | Same three, plus PR comments, Kubernetes Events, and the dashboard timeline |

If you find the real project ships some of these, the honest move is to **narrow the claim, not drop the feature** — the change attribution and calibration are the parts that are genuinely yours because they depend on KubeThrifty owning the change log.

### 26.9 Deliberately NOT building

Logs-based root cause, distributed-trace correlation, network/DNS/RBAC/PVC diagnostics, a UI incident inbox, alert routing/paging, multi-cluster federation of verdicts. Each is a product. Saying *"here's what I chose not to build and why"* is worth more in an interview than a half-built version of any of them.

---

## 27. DRA / GPU / Agent-Density Mode (D8 — scoped: demo + slide, not MVP)

This section exists because the 2026 waste conversation has moved to accelerators and agent fleets, and you should be able to hold that conversation for five minutes — while being disciplined enough not to build it in eight weeks.

### 27.1 Why CPU/memory maths does not transfer to devices

With **Dynamic Resource Allocation (GA in Kubernetes 1.34, `resource.k8s.io/v1`)**, hardware is *claimed*, not requested as a scalar:

| Object | Role |
|---|---|
| `DeviceClass` | The category and its selection constraints (attributes, not just counts) |
| `ResourceClaim` | A specific request for device(s), shareable across pods |
| `ResourceClaimTemplate` | Generates a claim per pod automatically |
| `ResourceSlice` | What a node's driver publishes as available |

The scheduler's `DynamicResources` plugin holds the pod pending until it can match the claim against published slices, then writes the allocation into the claim's status. Consequences for a right-sizer:

1. **There is no "0.3 of a GPU" to recommend.** Sharing and partitioning depend on the driver and hardware (partitionable devices in 1.33, consumable capacity in 1.34), not on your arithmetic.
2. **The waste is binary and huge**: a claimed-but-idle accelerator is the single most expensive line in most clusters. The right recommendation is *"this claim is 4% utilised — consolidate onto a shared claim or move to a smaller device class,"* not a millicore number.
3. **Autoscaler awareness matters**: Karpenter/Cluster Autoscaler must understand the claim before they can add a node for it, so device-claim pods have a different pending-pod story than CPU-bound ones.
4. **Kubelet's PodResources API reports DRA allocations**, which is how a node agent can see claimed devices — the correct integration point if you extend the collector.

So KubeThrifty's DRA mode is a **separate recommendation class**, not an extension of the sizing maths:

```
RECOMMENDATION device_claim  inference-worker/vllm
  claim            : gpu-a100-40g (DeviceClass: nvidia-a100)
  observed         : DCGM utilisation p95 6%, memory 3.1/40 GiB, 71% of hours idle
  proposal         : move to shared claim (consumable capacity) OR smaller device class
  guardrail        : do NOT reduce CPU on this pod — the data loader feeds the GPU;
                     starving it wastes the accelerator, which costs ~30× the CPU
```

That last guardrail is the sophisticated point: **on GPU pods, CPU is not the thing to optimise.** Cutting the loader's CPU to save ₹100 of vCPU can idle ₹3,000 of accelerator. KubeThrifty therefore *refuses* CPU reductions on pods holding device claims unless GPU utilisation is already near zero. One rule, one sentence, and it demonstrates that you reason about cost hierarchies rather than metrics.

### 27.2 Agent fleets: idle-density instead of right-sizing

The agent-hosting numbers making the rounds in 2026 (per-node agent density rising from ~61 in microVM isolation to ~88 with a gVisor-style sandbox layer, and up to ~3.5× density with orchestration that freezes idle agents, cutting cost per agent by ~75%) point at a different optimisation than percentile right-sizing:

| Workload shape | Right optimisation | Wrong optimisation |
|---|---|---|
| Long-running steady service | Right-size requests (§21.3) | — |
| Bursty agent session, idle 80% of the time | **Freeze/scale-to-zero and raise density**; count agents-per-node, not millicores | Shrinking requests — the reservation isn't the dominant cost, the *idle footprint* is |

KubeThrifty adds an **idle-density advisor** that classifies workloads by idle ratio and pressure and reports density-shaped savings:

```python
# analyser/src/profiles.py
def classify(idle_ratio: float, burstiness: float, psi_full: float,
             sessions_per_hour: float) -> str:
    """
    idle_ratio: share of intervals below 5% of request
    burstiness: p99/p50 of usage
    """
    if idle_ratio > 0.70 and sessions_per_hour > 0:
        return "freeze_candidate"       # advise idle-freeze / scale-to-zero + density
    if idle_ratio > 0.70:
        return "scale_to_zero_candidate"
    if burstiness > 8 and psi_full < 0.01:
        return "bursty_headroom"        # size requests to steady, limits generous
    if psi_full > 0.05:
        return "pressure_bound"         # needs MORE, not less
    return "steady"
```

And it accounts for the **sandbox tax**: if agents run in a sandboxed runtime, each sandbox carries its own memory overhead, so per-agent right-sizing must include it or the density estimate is fiction. The honest scope statement for the plan: *measure and advise; do not build a freezing controller.*

### 27.3 How to present this without over-claiming

> *"I scoped a DRA and agent-density mode but deliberately didn't build it in the MVP — I don't have GPUs in a kind cluster, and faking accelerator data would be worse than not having the feature. What I did build is the guardrail: KubeThrifty detects device claims via the DRA API and refuses to right-size CPU on those pods, because starving a data loader to save vCPU can idle an accelerator that costs thirty times more. And I can tell you why percentile right-sizing is the wrong tool for agent fleets: their cost is idle footprint and per-node density, not reservation size."*

That answer demonstrates currency (DRA GA, sandbox density economics), judgement (scope discipline), and cost reasoning — with nothing to disprove.

---

## 28. Build Order, Effort/Impact, and the Competitive Grid

### 28.1 What to actually build in 8 weeks

Nine differentiators exist on paper. Shipping all nine badly is worse than shipping four well, and an interviewer can tell the difference in ninety seconds. Ranked by *interview value per unit of effort*, with the dependency chain respected:

| Tier | Feature | Effort | Interview value | Depends on | Decision |
|---|---|---|---|---|---|
| **0** | MVP (§3): sample app → Prometheus → analyser → TimescaleDB → dashboard → auto-PR → Helm → CI | 4 weeks | Table stakes | — | **Ship** |
| **1** | **§23 PSI + cgroup truth** | 4–5 days | Very high — nobody has it, and it fixes the memory-sizing defect | cgroup v2 + containerd 2.x + kubelet cAdvisor scrape | **Ship first** |
| **1** | **§22 Resize Rehearsal** | 5–7 days | **Highest** — the thing they'll remember | §23 signals, K8s ≥1.35 | **Ship — this is the headline** |
| **1** | **§24 HPA-collision guard** | 2 days | Very high per hour spent — pure reasoning, tiny code | HPA read access | **Ship** |
| **1** | §14 forecasting + eval harness (Rev 2) | already planned | High (the ML/MLOps story) | — | **Keep** |
| **1** | §15 verification + auto-rollback (Rev 2) | already planned | High (the SRE story) | §21.4 corrected signals | **Keep, corrected** |
| **2** | **§25 consolidation-aware savings** | 3–4 days | High — makes the ₹ number defensible (and fixes defect C1) | instance catalogue | **Ship if week 7 is on schedule** |
| **2** | **§26 ThriftDetective (8 rules + calibration + 25-case corpus)** | 5–6 days | High — the "senior" feature | §23, §21.4, chaos harness | **Ship the narrow version; cut to 4 rules if squeezed** |
| **3** | Shift-left waste gate (ValidatingAdmissionPolicy + PR budget check) | 2 days | Medium-high, very cheap | — | **Nice-to-have; one CEL policy is enough to talk about** |
| **3** | §22.7 two-phase warm-up advisory | 1–2 days | Medium-high (novel) | §22, §23 | **Advisory only — do not build a controller** |
| **4** | **§27 DRA / agent-density** | — | Medium (conversation value) | GPUs you don't have | **Slide + guardrail only. Do NOT build** |
| **4** | Multi-cluster, Slack bot, OPA suite, anomaly detection | — | Low | — | **README "roadmap" section** |

**The one-line rule:** if a feature cannot be demoed in the 5-minute script (§28.4), it belongs in the roadmap, not the repo.

### 28.2 The head-to-head grid (put this in the README, verbatim)

| Capability | VPA | KRR | Goldilocks | OpenCost / Kubecost | Karpenter / EKS Auto Mode | Commercial rightsizers | **KubeThrifty** |
|---|---|---|---|---|---|---|---|
| Recommends requests/limits from history | ✅ | ✅ | ✅ (via VPA) | ❌ (cost visibility) | ❌ | ✅ | ✅ |
| **Forecasts** future demand before cutting | ❌ | ❌ | ❌ | ❌ | ❌ | partial | ✅ (§14) |
| **Runs a live zero-restart experiment** before proposing | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ **(§22)** |
| Uses **PSI / cgroup peak**, not just usage percentiles | ❌ | ❌ | ❌ | ❌ | ❌ | partial | ✅ (§23) |
| Models the **HPA coupling** (cost paradox) | ⚠️ warns against combining | ❌ | ❌ | ❌ | ❌ | partial | ✅ (§24) |
| Blocks **Guaranteed → Burstable** QoS demotion | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (§24.4) |
| Reports savings as **node delta**, not per-pod millicores | ❌ | ❌ | ❌ | ✅ (cost, not recs) | ✅ (provisioning) | ✅ | ✅ (§25) |
| Output is a **reviewable GitOps PR** | ❌ (auto-applies) | ❌ | ❌ | ❌ | ❌ | usually agent-applied | ✅ |
| **Verifies its own change** post-merge and auto-reverts | ❌ | ❌ | ❌ | ❌ | ❌ | partial | ✅ (§15) |
| **Attributes an incident to its own change** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (§26) |
| **Calibrated** confidence, CI-gated | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (§26.5) |
| Non-disruptive actuation path | alpha (`InPlaceOrRecreate`) | n/a | n/a | n/a | n/a | ✅ | ✅ (GA primitive, used for rehearsal) |
| Free / self-hostable / auditable | ✅ | ✅ | ✅ | ✅ | ⚠️ (~12% surcharge on Auto Mode) | ❌ | ✅ |

Then the honest line, right under the table — because a table with no losses reads as marketing:

> **Where the incumbents win:** VPA is battle-tested at enormous scale and requires no PR workflow. KRR installs in one command. Kubecost/OpenCost do cluster cost allocation far beyond my scope. Commercial rightsizers do this continuously across thousands of workloads with support contracts. KubeThrifty is not a replacement for any of them — it's a demonstration of the *workflow* I think right-sizing should have: forecast, rehearse, propose, review, verify, attribute.

### 28.3 Revised 8-week schedule (supersedes the schedule in §3)

| Week | Focus | Deliverable that proves it |
|---|---|---|
| **1** | kind 1.36 cluster (cgroup v2 confirmed, containerd 2.x), 5-service over-provisioned demo app with distinct usage shapes, kube-prometheus-stack pinned, **kubelet `/metrics/cadvisor` scrape verified with `container_pressure_*` returning data** | `kubectl get --raw /api/v1/nodes/<n>/proxy/metrics/cadvisor \| grep pressure` returns rows |
| **2** | Analyser core: Prometheus client, percentiles, **`sizing.py` (§21.3) with CPU/memory asymmetry**, JSON report, unit tests | `python -m analyser --window 7d` prints a recommendation table with binding constraints |
| **3** | TimescaleDB (`timescaledb-ha:pg18…`) + Toolkit verified, hypertables, continuous aggregates, **migration 003 (§21.6)**, Redis/Valkey Streams job queue | `SELECT` returns P95 over 7 days in <100 ms; Toolkit extension present |
| **4** | Next.js 16.3 dashboard: waste heatmap with the **pressure axis (§23.6)**, recommendation table, savings panel; auto-PR generator with the §22.8 PR body template | Screenshot + a real PR opened against your own repo |
| **5** | **cgroup-truth DaemonSet (§23.4)** + Headroom Index + sizing eval harness gating CI; forecasting (§14) + backtest eval | Two CI jobs that can fail the build on quality regression |
| **6** | **Resize Rehearsal (§22)**: preflight, runner, watchdog CronJob, RBAC, Lease, kill switch; rehearsal evidence in the PR body | GIF: a live pod resized, observed, reverted — `restartCount` unchanged throughout |
| **7** | **HPA-collision guard (§24)** + **consolidation savings (§25)** + KEDA 2.20 scale-to-zero for the analyser + Helm 4 chart + full CI/CD | Dashboard shows "4 nodes → 1"; analyser scales to zero when idle |
| **8** | **ThriftDetective (§26)** narrow: 8 rules, chaos corpus generator, calibration eval, `thriftctl replay`; README with architecture diagram, screenshots, GIFs; public deploy; 2-minute video | `thriftctl replay <sha>` reproduces a verdict offline; live link on the resume |

Weeks 5–8 are the differentiators, and they're ordered so that **if you run out of time you still have a coherent story**: PSI evidence (5) makes rehearsal (6) possible; rehearsal is the headline; savings (7) makes the money claim defensible; the detective (8) is the one to cut down if needed.

### 28.4 The 5-minute demo script (rehearse this out loud)

1. **(30s) The waste.** Dashboard heatmap. *"Five services request 5.5 CPU and 6.5 GB; they peak at 1.7 and 2.6. Note the second axis — that's pressure, not usage."*
2. **(45s) The trap.** Point at `inventory-cache`: low average usage, high memory PSI. *"Every percentile-based tool would shrink this one. Mine refuses, because the kernel says it's already stalling on reclaim. Headroom Index 0.0."*
3. **(60s) The rehearsal.** Trigger it live. Show `kubectl get pod -w` and `restartCount` staying flat while `status.containerStatuses[].resources` changes and then reverts. *"In-place resize went GA in 1.35. I use it to run an experiment, not to actuate silently."*
4. **(45s) The PR.** Open the generated PR. Point at the evidence table, the binding constraint, the node delta, and the HPA co-change. *"This is the artefact. A human merges it."*
5. **(45s) The paradox.** *"Right-sizing doesn't always save money — here's the HPA arithmetic."* Show the refusal case.
6. **(60s) The loop.** Show a `regressed` rehearsal or a rollback PR, then `thriftctl replay <sha256>` on your laptop with the cluster disconnected. *"Same evidence, same verdict, no cluster. And it names the PR that caused it."*
7. **(15s) The close.** *"Everything is pinned, the sizing logic has an eval harness that fails the build on regression, and the confidence scores are calibrated in CI."*

### 28.5 Resume and README one-liners

- **Resume bullet:** *"KubeThrifty — Kubernetes right-sizing platform (K8s 1.36, Python 3.14, Next.js 16, TimescaleDB, Helm 4). Forecasts pod demand, **rehearses candidate sizes on live pods via the GA in-place-resize API with zero restarts**, and opens GitOps PRs carrying empirical evidence and node-level cost deltas; verifies post-merge and auto-reverts on regression. Live: <link>"*
- **README subtitle:** *"Right-sizing that runs an experiment instead of making a guess."*
- **The two sentences for the interview close:** *"Every right-sizing tool ships a prediction. Mine ships an experiment: it resizes one live pod in place with no restart, watches kernel pressure metrics rather than just usage, reverts, and puts the evidence in the pull request. And when a change does go wrong, it names the PR that caused it, opens the revert, and widens that workload's safety margin — with confidence scores that are calibration-tested in CI."*

---

## 29. Fact-Check Appendix — every version and platform claim, with source and date

Bring this to the interview. Being able to say *"pinned as of 17 Aug 2026, here's where each number came from"* is a credibility multiplier, and it's what stops a stale claim from becoming an embarrassing one six months from now.

| Claim used in this plan | Status / date | Where to re-verify |
|---|---|---|
| Kubernetes 1.36 is current; 1.35 supported to 28 Feb 2027; 1.34 enters maintenance 27 Aug 2026, EOL 27 Oct 2026 | Aug 2026 | `kubernetes.io/releases/` + `/releases/patch-releases/` |
| Kubernetes **1.35+ requires cgroup v2** | Aug 2026 | Provider docs (e.g. OKE/AKS supported-versions pages) + K8s release notes |
| **In-place pod resize GA in 1.35** (beta 1.33, alpha 1.27); `resize` subresource; CPU usually restart-free; memory per `resizePolicy`; `PodResizePending{Deferred,Infeasible}` / `PodResizeInProgress` | Dec 2025 → Aug 2026 | `kubernetes.io/blog/2025/12/19/kubernetes-v1-35-in-place-pod-resize-ga` + "Resize CPU and Memory Resources assigned to Containers" |
| **Pod-level resources** beta in 1.34; **in-place pod-level resize** beta in 1.36 (`InPlacePodLevelResourcesVerticalScaling`) | Aug 2026 | K8s 1.34/1.36 release notes |
| **PSI metrics**: beta 1.34 (`KubeletPSI`), **GA 1.36**; `container_pressure_*_seconds_total` via `/metrics/cadvisor` + Summary API; needs kernel ≥4.20 + `CONFIG_PSI` + cgroup v2 + containerd 2.x; Linux only | Aug 2026 | `kubernetes.io/docs/reference/instrumentation/understand-psi-metrics/`, KEP-4205 |
| **DRA GA in 1.34** (`resource.k8s.io/v1`), enabled by default; consumable capacity + device health in 1.34; status-update RBAC changes in 1.36 | Aug 2026 | `kubernetes.io/blog/2025/09/01/kubernetes-v1-34-dra-updates` + DRA hardening guide |
| **VPA `InPlaceOrRecreate` is alpha, off by default** | 2026 | VPA repo docs / autoscaler releases |
| **Helm 4.0 GA 12 Nov 2025**; latest 4.2.x (May 2026); **Helm 3 bug fixes ended 8 Jul 2026**, security to **10 Feb 2027**; SSA default for new installs; `--wait` needs `watch` RBAC; `--post-renderer` must be a plugin; chart v2 unchanged, chart v3 experimental | Aug 2026 | `helm.sh/blog/helm-v3-end-of-life/` + Helm 4 release notes |
| **KEDA 2.20** (~May 2026); ~4-month cadence, ~2 cycles supported | Aug 2026 | `github.com/kedacore/keda/releases` + ROADMAP.md |
| **Node.js 24 Active LTS** (EOL 30 Apr 2028); 26 is Current until Oct 2026; 22 Maintenance to Apr 2027 | Aug 2026 | `nodejs.org/en/about/previous-releases` |
| **Next.js 16.3.0** (3 Aug 2026); 16 is Active LTS; 15 EOL 21 Oct 2026; 14 EOL Oct 2025 | Aug 2026 | `nextjs.org/blog` |
| **Python 3.14** current stable; 3.15 due 1 Oct 2026; pandas 3.0.x and NumPy 2.5.x ship cp314 wheels | Aug 2026 | `python.org` release schedule, `pyreadiness.org/3.14` |
| **TimescaleDB `timescale/timescaledb-ha:pg18.4-ts2.28.1-all`**; `-ha` image bundles Toolkit (the Rev 2 bug fix); vendor now branded TigerData | Aug 2026 | Docker Hub `timescale/timescaledb-ha` tags + TigerData docs |
| **Valkey 9.1.0** (19 May 2026, BSD-3); **Redis 8.x** current | Aug 2026 | `github.com/valkey-io/valkey/releases`, redis.io |
| **Grafana 13.0** (Apr 2026) | Aug 2026 | grafana.com release notes |
| **kube-prometheus-stack chart ~88.x**, distributed as an OCI artefact | Aug 2026 | `github.com/prometheus-community/helm-charts/releases` |
| **EKS Auto Mode ≈12% premium** on EC2 On-Demand; managed Karpenter; neither optimises workload requests | as reported 2026 | AWS EKS Auto Mode pricing page — **verify the current percentage before quoting it** |
| Karpenter provisions to **requests**, so node count follows requests | 2026 | Karpenter docs / consolidation docs |
| `free`/`top` in a container read non-namespaced `/proc`; real numbers are cgroup v2 (`memory.max/current/peak`, `memory.events`) | evergreen | kernel cgroup-v2 documentation |
| KubeTective's specifics (11 modes, 16 scenarios) | **UNVERIFIED — see §26.0** | Check the project's own README before making any comparative claim |

**Maintenance habit worth mentioning in the interview:** Renovate (or Dependabot) opens the version-bump PRs, CI runs the eval harnesses against them, and this appendix gets a date bump each time. *"My dependency currency is automated; my claims about it are dated."*

---

## Final Word — Why This Project Wins DevOps Interviews (Rev 3)

KubeThrifty hits every box on a DevOps/SRE scorecard, and Rev 3 adds the thing scorecards can't capture: evidence of judgement.

**Kubernetes depth (2026-current, not 2023-current):** requests vs limits, QoS classes and eviction ordering, cgroup v2 and why it's mandatory from 1.35, the `resize` subresource and what actually restarts, `PodResizePending{Deferred,Infeasible}`, pod-level resources, PSI as distinct from throttling, DRA's claim model, HPA's utilisation-of-request arithmetic, and PDB/consolidation interaction. You deployed *to* K8s and built *for* K8s, packaged in Helm 4 charts, and the tool right-sizes its own pods.

**Observability:** Prometheus + cAdvisor + kube-state-metrics, custom PromQL (including the correct *ratio* forms), kubelet PSI metrics, cgroup-level truth from a hardened read-only DaemonSet, Grafana dashboards as code, and self-monitoring of analysis duration, rehearsal outcomes and forecast drift.

**CI/CD and GitOps:** lint → test → **eval harnesses that can fail the build on quality regression** (forecast sMAPE/coverage, sizing decisions, detective accuracy *and calibration* *and determinism*) → build → sign → push → Helm upgrade → scheduled analysis → auto-PR. The output artefact of the whole system is a reviewable pull request, which is the point.

**IaC and supply chain:** everything pinned by tag, charts pinnable by OCI digest, images signed with cosign, prices pinned with an "as of" date so savings claims are reproducible, Renovate keeping it current, and a dated fact-check appendix (§29) so no claim in the document rots silently.

**SRE reasoning — the part that separates you:**
- Memory and CPU are not symmetric, and the statistic you choose encodes that (§21.3).
- A resource change is a hypothesis; you don't trust it until you've observed it (§15, §22).
- Low utilisation plus high pressure means the workload needs *more*, and shipping increase recommendations is what makes a cost tool trustworthy (§23.5).
- Right-sizing does not always save money, and knowing when it costs money is the interesting half (§24).
- Savings are node-shaped, not pod-shaped (§25).
- A confidence score you haven't calibrated is a decoration (§26.5).
- Knowing what your tool refuses to opine on is a feature (§26.2, §27.3).

**The sentence that wins:** *"I built a Kubernetes right-sizing platform that forecasts each pod's next two weeks, then **rehearses** the candidate size on a live pod using the GA in-place-resize API — no restart — watches kernel pressure metrics rather than just usage, reverts, and puts that evidence in a GitOps pull request with the node-count cost delta."*

**The follow-up that closes it:** *"And when a change does go wrong, it names the PR that caused it, opens the revert automatically, and widens that workload's safety margin — with confidence scores that CI checks for calibration. I also caught three defects in my own previous revision: I was sizing memory with a percentile, comparing raw throttle counters across unequal windows, and reporting per-pod waste as if it were money. All three are in the CHANGELOG, because finding your own bugs before an interviewer does is the whole job."*

No other fresher walking into that DevOps interview will have anything close.

---

*Document prepared as a complete implementation guide for KubeThrifty — Kubernetes Pod Right-Sizing Advisor. Revision 3, 17 August 2026. Version claims dated and sourced in §29; re-verify before quoting any of them after ~November 2026.*
