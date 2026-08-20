# KubeThrifty — Security & API Reference (Rev 3)

Detailed reference for the **Next.js 16.3 (App Router) + Node.js 24** dashboard backend, loaded on demand
for any task touching **security posture, secrets, trust boundaries, cluster-write permissions, or the
REST / analysis-job contract**. The main `SKILL.md` carries the day-to-day rules; this file carries the
depth. All examples are TypeScript, Node 24, App Router Route Handlers.

## Contents
- Part A — Security: A.1 threat model & trust boundaries · A.2 the Prometheus proxy boundary ·
  A.3 the GitHub token (analyser side) · A.4 **cluster-write permissions and the rehearsal boundary
  (new in Rev 3)** · A.5 dashboard auth posture · A.6 secrets in K8s · A.7 rate limiting · A.8 CORS ·
  A.9 input validation & SSRF · A.10 evidence bundles, signing, and PII
- Part B — API contract: B.1 endpoints · B.2 ProblemDetail · B.3 pagination & idempotency ·
  B.4 the analysis-job stream contract · B.5 **rehearsal, verdict, and savings-scenario payloads (new)** ·
  B.6 the verification / rollback record

---

## Part A — Security

### A.1 Threat model & trust boundaries

KubeThrifty is a **read-mostly advisory tool over data it does not own**, with exactly one narrow
exception (A.4). The blast radius is small by design — the worst thing the system can *do* durably is open
a pull request that a human must approve.

1. **Browser ↔ dashboard API.** Untrusted input; validate everything with Zod; never interpolate into SQL
   or PromQL; never reflect raw error strings.
2. **Dashboard API ↔ Prometheus.** The API proxies Prometheus; the browser never talks to it (A.2).
3. **Python analyser ↔ GitHub.** The only component that writes anywhere external. Least-privilege token
   (A.3); opens PRs, never merges, never pushes to `main`.
4. **Python analyser ↔ cluster.** Reads metrics via Prometheus and pod status via the API. Its *only*
   mutating permission anywhere is `patch` on `pods/resize`, used exclusively for rehearsals (A.4).
   Durable right-sizing reaches the cluster through Git, never `kubectl apply`.
5. **Dashboard API ↔ cluster.** No credentials at all. The dashboard cannot resize, evict, apply, or roll
   back anything.

Consistency model: **AP** — a recommendation an hour stale is still useful; a dashboard that errors
because a fresh run hasn't finished is not.

### A.2 The Prometheus proxy boundary (still the most important boundary)

Never let the browser hit Prometheus. The API exposes a **narrow, validated proxy**:

- Only `query_range` / `query`, for an allow-list of metric names the dashboard actually charts.
- Rev 3 adds the pressure family to the allow-list — and nothing else:

```typescript
const ALLOWED_METRICS = new Set([
  'container_cpu_usage_seconds_total',
  'container_memory_working_set_bytes',
  'kube_pod_container_resource_requests',
  'kube_pod_container_resource_limits',
  // Rev 3 — evidence layer
  'container_cpu_cfs_periods_total',
  'container_cpu_cfs_throttled_periods_total',
  'container_oom_events_total',
  'container_pressure_cpu_stalled_seconds_total',
  'container_pressure_memory_stalled_seconds_total',
  'container_pressure_cpu_waiting_seconds_total',
  'kubethrifty_cgroup_memory_peak_bytes',
  'kubethrifty_cgroup_throttle_ratio',
]);

const Query = z.object({
  pod: z.string().regex(/^[a-z0-9.\-]{1,253}$/),
  namespace: z.string().regex(/^[a-z0-9.\-]{1,253}$/),
  resource: z.enum(['cpu', 'memory']),
  window: z.string().regex(/^\d{1,2}[hd]$/),          // e.g. 7d, 24h
  step: z.string().regex(/^\d{1,4}[smh]$/),
});
```

- Label values are bound as PromQL **label matchers**, never string-concatenated → no PromQL injection.
- `step`/`window` are clamped (step ≥ 30s, window ≤ 30d) so a crafted request can't ask Prometheus for
  millions of points.
- 8s timeout, circuit breaker, short cache. A Prometheus outage degrades to a cached series, never a crash.
- Ratios (throttle) are computed **inside** the PromQL expression the proxy issues, so the client cannot
  combine mismatched windows.

### A.3 The GitHub token (analyser side)

A **fine-grained PAT or GitHub App installation token scoped to exactly two permissions on the manifests
repo: Contents (read/write) and Pull requests (read/write).** Nothing else — no admin, no workflow, no org
scope.

- Stored as a K8s `Secret` (`kubethrifty-github`), mounted as an env var into the analyser pod only.
- Never logged, never in a ConfigMap, never committed. Rotate on suspicion.
- The analyser **only opens PRs** — never merges, never force-pushes, never touches `main`.

> Soundbite: *"The only thing my system can do to the outside world is open a pull request, with a token
> scoped to contents plus PRs on one repository. The worst case of a total compromise is an unwanted PR a
> human declines."*

### A.4 Cluster-write permissions and the rehearsal boundary (new in Rev 3)

Rev 3 introduces exactly one in-cluster mutation: the Resize Rehearsal patches `pods/resize` on **one**
pod, then reverts. This is the boundary an interviewer will probe hardest, so the controls are explicit:

```yaml
# The complete mutating surface of the entire platform.
- apiGroups: [""]
  resources: ["pods/resize"]
  verbs: ["patch"]
```

- **No `create`/`delete` on pods, no writes to Deployments, no Secret reads** for the rehearsal service
  account. It cannot evict, cannot scale, cannot deploy.
- **The dashboard API holds none of this.** There is no HTTP path from the browser to a resize. If a
  "rehearse now" button is ever requested, it must enqueue an analyser job — and even then the analyser
  re-runs preflight and can refuse.
- **Preflight gates** every rehearsal: rehearsals enabled globally, namespace not labelled
  `kubethrifty.io/tier: critical`, workload not annotated `kubethrifty.io/rehearsal: disabled`,
  `replicas ≥ 2`, no rollout in progress, PDB headroom, no VPA in `Auto` mode, concurrency cap.
- **Compensation is persisted before the mutation** (revert payload + `revert_deadline`), and an
  independent watchdog CronJob reverts anything past its deadline. A crashed analyser cannot leave a pod
  resized.
- **Kill switches:** the namespace label, the workload annotation, and a global Helm value — all checked at
  the start of every rehearsal, so disabling is immediate and needs no redeploy.
- **Audit:** every rehearsal writes a row (`rehearsals`) with original values, candidate values, outcome,
  and the signal windows; every rehearsal is reconstructable from an evidence bundle hash.

### A.5 Dashboard auth posture

For the MVP/demo the dashboard is read-only and can sit behind cluster ingress without per-user login;
`POST /api/analysis/trigger` is the only write and it merely enqueues a job. For any public or multi-tenant
deployment:

- Put the app behind an auth proxy (oauth2-proxy / your IdP) or add a session (signed, httpOnly, `Secure`,
  `SameSite=Lax` cookie via Auth.js).
- The trigger endpoint requires an authenticated session, is rate-limited (A.7) and idempotent (B.3).
- The session carries `userId` only — no tokens, no PII.
- Verdict and rehearsal endpoints leak internal architecture (workload names, incident history), so treat
  them as internal even on a "public" demo: consider a read-only demo dataset instead of live production
  data.

### A.6 Secrets in Kubernetes

All credentials (`DATABASE_URL`, `REDIS_URL`, `PROMETHEUS_URL` if authed, the GitHub token) live in K8s
`Secret`s, referenced from Helm `values.yaml` (`secretName`/`secretKey`), never inlined. Prefer
sealed-secrets or external-secrets beyond local `kind`. Validate presence at boot with the Zod `env`
schema — fail fast.

### A.7 Rate limiting

Sliding-window limiter in Redis/Valkey on mutating and proxy endpoints:

```typescript
const n = await redis.incr(`rl:${ip}:${minuteBucket}`);
if (n === 1) await redis.expire(`rl:${ip}:${minuteBucket}`, 60);
if (n > LIMIT) return problem(429, 'Too many requests');
```

`/api/analysis/trigger` gets a tight limit (analysis is expensive); the Prometheus proxy gets a moderate
one (it can generate real load upstream); read endpoints served from cache get a generous one.

### A.8 CORS

The API serves its own first-party UI — keep CORS closed (same-origin) by default. If another origin must
call it, allow-list exact origins; never reflect `Origin`, never `*` with credentials.

### A.9 Input validation & SSRF

Every handler validates with Zod before doing anything. The Prometheus proxy is the SSRF-sensitive
surface: the upstream base URL comes from `env.PROMETHEUS_URL` (server-controlled) and only query
*parameters* are user-influenced — a client must never be able to supply the Prometheus host or path. The
same rule applies to the verdict endpoint: `bundleSha` is validated as `^[a-f0-9]{64}$` and used as a
database key, never as a filesystem path.

### A.10 Evidence bundles, signing, and PII

- Evidence bundles are **immutable and content-addressed**: the `sha256` of the canonical JSON is the
  identity. Serve them read-only; never accept a client-supplied bundle for evaluation on the server.
- Bundles and container images are signed with **cosign keyless (OIDC)** in CI; PR bodies link the
  attestation. "My evidence is signed and my images have provenance" is a cheap, strong answer.
- KubeThrifty stores pod/namespace/workload names, resource numbers, PR links, and rupee figures — **no
  personal data**. Workload names still hint at internal architecture, so the recommendations, rehearsals
  and verdicts APIs are internal by default.

---

## Part B — API contract

### B.1 Endpoints

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/api/recommendations` | `namespace`, `action`, `minSavings`, `evidenceTier` | `Recommendation[]` for the latest completed run |
| GET | `/api/pods/:name/metrics` | `resource`, `window`, `step` | `{ series, requested, limit, peak }` |
| GET | `/api/pods/:name/pressure` | `window`, `step` | `{ psiMemFull, psiCpuFull, throttleRatio, partial }` |
| GET | `/api/rehearsals` | `namespace`, `outcome` | `Rehearsal[]` |
| GET | `/api/rehearsals/:id` | — | `Rehearsal` + `signalsBefore`/`signalsAfter` + `resizeConditions` |
| GET | `/api/verdicts` | `workload`, `ruleId` | `Verdict[]` |
| GET | `/api/verdicts/:bundleSha` | — | `Verdict` + the evidence bundle |
| GET | `/api/savings/summary` | `scenario` | `{ nodesBefore, nodesAfter, monthlyInr, byNamespace[] }` |
| GET | `/api/savings/scenarios` | — | the three scenarios side by side + catalogue `asOf` |
| POST | `/api/analysis/trigger` | body `{ namespace?, window }` | `202 { runId, status: 'queued' }` |
| GET | `/api/analysis/history` | `cursor`, `pageSize` | `{ runs, nextCursor? }` |
| GET | `/api/analysis/:id` | — | `AnalysisRun` (running/completed/failed) |
| GET | `/api/health` | — | `200 { db: 'ok', cache: 'ok' }` or `503` |

`Recommendation` (subset): `{ podName, containerName, namespace, resourceType, currentRequest,
currentLimit, recommendedRequest, recommendedLimit, savingsMonthlyInr, savingsPercentage, confidence,
action, evidenceTier, bindingConstraint, sizingBasis, hpaCoupled, rehearsalId, modelId, prUrl, regressed,
reason }`.

`sizingBasis` is `'peak'` for memory and `'p95'` for CPU — it exists so the UI can show *why* the two
resources are sized by different rules. `bindingConstraint` is one of
`peak_margin | p95_margin | forecast | rehearsed | absolute_min`.

### B.2 ProblemDetail error shape (RFC 9457)

```typescript
// lib/http/problem.ts
import { NextResponse } from 'next/server';
export function problem(status: number, detail: string, type = 'about:blank') {
  return NextResponse.json(
    { type, title: TITLES[status] ?? 'Error', status, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}
```

One shape for every error. Never leak stack traces or raw DB/Prometheus strings.

### B.3 Pagination & idempotency

- **Pagination:** `/api/analysis/history`, `/api/rehearsals` and `/api/verdicts` use cursor pagination on
  their timestamp column (`nextCursor` = the oldest timestamp returned). Never offset-paginate the
  time-series tables.
- **Idempotency:** `POST /api/analysis/trigger` is idempotent within a short window; a run is keyed
  `(cluster, window, started_at_bucket)` and a duplicate trigger returns the existing `runId`. This
  matches the analyser's idempotent-run guarantee, so a redelivered stream message never double-writes
  recommendations or opens a second PR.

### B.4 The analysis-job stream contract (shared with the Python worker)

```
XADD analysis-jobs * runId <uuid> namespace <ns|""> window <e.g. 7d>
```

- The Python worker reads via consumer group `analysers` (`XREADGROUP`), processes, `XACK`s on success,
  and writes the `analysis_runs` row (`running` → `completed`/`failed`).
- A job past max retries goes to `analysis-jobs-dlq` with the error; DLQ depth is an alertable metric.
- KEDA's `redis-streams` scaler reads pending-entry count to scale the worker 0→N→0.
- The backend only ever **produces** to this stream. Status is read from `analysis_runs`.

### B.5 Rehearsal, verdict, and savings payloads (new in Rev 3)

```typescript
type Rehearsal = {
  id: number; recommendationId: number;
  namespace: string; pod: string; container: string;
  startedAt: string; endedAt: string | null;
  outcome: 'pending' | 'running' | 'safe' | 'regressed' | 'inconclusive' | 'reverted_by_watchdog';
  originalRequests: Record<string, string>;  candidateRequests: Record<string, string>;
  signalsBefore: SignalWindow | null;        signalsAfter: SignalWindow | null;
  restartsObserved: number;                  reasons: string[];
  resizeConditions: Record<string, { status: string; reason?: string; message?: string }> | null;
  evidenceBundleSha256: string | null;
};

type SignalWindow = {
  throttleRatio: number | null;   // null == not observed (NOT zero)
  psiMemFull: number | null;
  psiCpuFull: number | null;
  oomKills: number;
  restarts: number;
};

type Verdict = {
  bundleSha256: string; ruleId: string; confidence: number;
  workload: string; namespace: string; observedAt: string;
  attributedChange: { pr?: string; recommendationId?: number; kind?: string;
                      hoursBefore?: number; delta?: Record<string, string> } | null;
  evidence: Record<string, unknown>; rulesetVersion: string; remediation: string;
};

type SavingsScenario = {
  scenario: 'fixed' | 'karpenter' | 'auto_mode';
  instance: string; nodesBefore: number; nodesAfter: number;
  monthlyInrBefore: number; monthlyInrAfter: number; monthlySavingsInr: number;
  surchargePct: number; catalogueAsOf: string;      // dated prices -> reproducible figures
};
```

Two contract rules that carry the project's honesty: `null` in a `SignalWindow` means *not observed* and
must render differently from `0`; and `monthlySavingsInr` is always derived from a **node delta**, never
from per-pod millicores — the API must not expose a per-pod rupee field that invites the wrong headline.

### B.6 The verification / rollback record

When the post-merge watcher finds a regression it sets `regressed=true` on the recommendation, opens a
rollback PR, and hands the evidence bundle to ThriftDetective. The dashboard surfaces this through the
existing recommendations query (`regressed`, `prUrl`) plus `/api/verdicts` for the *why*. **Never expose a
write API that performs a rollback from the dashboard** — a rollback is always a reviewable pull request,
and that constraint is the reason teams would trust this tool in the first place.
