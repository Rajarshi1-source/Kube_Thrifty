---
name: kubethrifty-nextjs-backend
description: >-
  Build and maintain the KubeThrifty dashboard backend -- the server side of the Next.js 16.3 App
  Router app on Node.js 24 LTS with TypeScript strict. Use for ANY backend work: Route Handlers
  under app/api (recommendations with evidence tiers, pod metrics and PSI pressure series,
  rehearsal records, detective verdicts, savings scenarios, trigger/history, health), reading
  TimescaleDB with a Valkey or Redis cache-aside, triggering a run by XADD-ing to the analyser's
  stream, the Prometheus proxy with its metric allow-list, Zod validation, RFC 9457 ProblemDetail
  errors, and resilience (timeout, retry, breaker). Trigger on route handler, app/api, Zod,
  ProblemDetail, cache-aside, Redis Streams, XADD, Prometheus proxy, circuit breaker, evidence
  tier, or verdict endpoint. MANDATE: Node 24 plus Next.js 16.3 App Router plus TypeScript strict;
  handlers stay thin; heavy work belongs to the Python analyser; and the API NEVER mutates cluster
  state. For security and the full REST contract read references/security-and-api.md.
---

# Next.js 16 API Routes + Node.js 24 — KubeThrifty Backend

You build the **dashboard API** as the server half of a Next.js 16 App Router app. It is deliberately
**thin**: it reads what the Python analyser already computed, proxies time-series from Prometheus, and
enqueues analysis runs. All heavy computation — sizing, forecasting, rehearsals, packing, verdicts — lives
in the analyser. Adding a third language for the API would be scope creep, so the dashboard's Node runtime
is reused.

## Why these versions

- **Node.js 24 LTS (Krypton):** Active LTS, EOL Apr 2028. Node 26 is *Current* until its Oct 2026 LTS
  promotion — don't ship on Current. Node 22 is Maintenance-only.
- **Next.js 16.3.x:** current line (16.3.0, Aug 2026): large dev-memory reduction, build-cache reuse, and
  `next build` can type-check with TypeScript 7. Turbopack is the default bundler — no `--turbopack` flag.
  Next 15 reaches EOL 21 Oct 2026; Next 14 is already EOL.
- **No custom WebSocket server.** KubeThrifty is request/response: the UI polls after a triggered run.
  Route Handlers + cache-aside is the whole architecture.

## Critical rules (never violate)

- **The API never mutates cluster state.** There is no resize endpoint, no rollback endpoint, no apply.
  The only write the browser can cause is `XADD` of an analysis job. Rehearsals are initiated by the
  analyser (which holds the sole `patch pods/resize` permission) and rollbacks are pull requests. If
  someone asks for a "revert" button, the correct implementation is a link to the PR.
- **Handlers do HTTP only:** Zod-validate, call a `lib/**` service, map to `NextResponse`. No SQL, no
  PromQL, no business logic in the handler.
- **`export const runtime = 'nodejs'`** on every handler — `pg`/Prisma, ioredis and crypto cannot run on
  the edge runtime.
- **The backend never recomputes recommendations.** It reads what the analyser persisted. Triggering a run
  is `XADD` + `202`; the KEDA-scaled worker does the work; the client polls `/api/analysis/:id`.
- **Cache-aside always:** Redis/Valkey first, TimescaleDB on miss, populate cache (TTL ~6h), return. A
  failed or in-progress analysis must never blank the dashboard — serve the last completed run.
- **Never drop the evidence tier.** Every recommendation carries `evidenceTier`
  (`rehearsed | modelled | partial`) and `bindingConstraint`, and the API must pass them through
  unchanged. Losing that field in a DTO turns a modelled guess into an apparent measurement — the one
  correctness bug in this layer that actually matters.
- **Prometheus is proxied, never exposed.** Server-side base URL, metric-name allow-list, validated label
  matchers (no string concatenation → no PromQL injection), clamped `step`/`window`, 8s timeout, breaker,
  short cache.
- **Errors are RFC 9457 ProblemDetail JSON** from one `problem()` helper. No stack traces, no raw
  DB/Prometheus strings.
- **Config through a Zod-validated `env` at boot** — fail fast rather than start half-configured. Pool and
  Redis clients are module singletons with a `globalThis` guard for dev hot-reload.

## Layered architecture

```
app/api/**/route.ts   -> HTTP only: Zod validate, status codes, ProblemDetail
        |
        v
lib/<domain>/*.ts      -> services: recommendations, metrics (Prometheus proxy), pressure,
                          rehearsals, verdicts, savings, analysis (enqueue)
        |
        v
lib/db (TimescaleDB) + lib/cache (Valkey/Redis) + lib/prometheus + lib/events (XADD)
```

## REST contract (Rev 3)

```
GET  /api/recommendations           ?namespace=&action=reduce&minSavings=&evidenceTier=rehearsed
GET  /api/pods/:name/metrics        ?resource=cpu&window=7d&step=1h        # Prometheus proxy
GET  /api/pods/:name/pressure       ?window=7d&step=1h                     # PSI + throttle ratio series
GET  /api/rehearsals                ?namespace=&outcome=safe|regressed|inconclusive
GET  /api/rehearsals/:id                                                    # signals before/after + conditions
GET  /api/verdicts                  ?workload=&ruleId=                      # ThriftDetective results
GET  /api/verdicts/:bundleSha                                               # one verdict + its evidence bundle
GET  /api/savings/summary           ?scenario=fixed|karpenter|auto_mode     # node-delta savings
GET  /api/savings/scenarios                                                 # all three, side by side
POST /api/analysis/trigger                                                  # XADD job -> 202 {runId}
GET  /api/analysis/history          ?cursor=&pageSize=
GET  /api/analysis/:id                                                      # run status + results
GET  /api/health                                                            # TimescaleDB + cache
```

Full request/response shapes, the ProblemDetail body, pagination, idempotency, rate limits, the trust
boundaries, and the analysis-job stream contract are in
[`references/security-and-api.md`](references/security-and-api.md). Read it before adding or changing any
endpoint — especially anything that looks like it should mutate the cluster.

## Route Handler pattern

```typescript
// app/api/recommendations/route.ts
import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getLatestRecommendations } from '@/lib/recommendations/service';
import { problem } from '@/lib/http/problem';

export const runtime = 'nodejs';                       // pg + ioredis -> not edge

const Query = z.object({
  namespace: z.string().optional(),
  action: z.enum(['reduce', 'increase', 'keep', 'needs_review']).optional(),
  evidenceTier: z.enum(['rehearsed', 'modelled', 'partial']).optional(),
  minSavings: z.coerce.number().nonnegative().optional(),
});

export async function GET(req: NextRequest) {
  const parsed = Query.safeParse(Object.fromEntries(req.nextUrl.searchParams));
  if (!parsed.success) return problem(400, 'Invalid query parameters');
  const rows = await getLatestRecommendations(parsed.data);      // cache-aside inside
  return NextResponse.json(rows, { headers: { 'Cache-Control': 'private, max-age=30' } });
}
```

## The pressure endpoint (new in Rev 3)

The dashboard's second axis needs PSI and throttle ratios, and the ratio must be computed **in PromQL**,
not in TypeScript from two separate series — otherwise you are dividing numbers collected over different
windows, which is exactly the bug Rev 3 fixed in the analyser:

```typescript
// lib/pressure/queries.ts
export const pressureQueries = (sel: string, w: string) => ({
  psiMemFull: `sum(rate(container_pressure_memory_stalled_seconds_total{${sel}}[${w}]))`,
  psiCpuFull: `sum(rate(container_pressure_cpu_stalled_seconds_total{${sel}}[${w}]))`,
  throttleRatio:
    `sum(rate(container_cpu_cfs_throttled_periods_total{${sel}}[${w}]))` +
    ` / sum(rate(container_cpu_cfs_periods_total{${sel}}[${w}]))`,
});
```

If a series comes back empty, return `null` for that metric and a `partial: true` flag — never `0`. Zero
means "no pressure"; null means "we could not observe pressure," and the UI renders those differently.

## Trigger an analysis run — enqueue, don't compute

```typescript
// lib/analysis/trigger.ts
export async function triggerAnalysis(input: { namespace?: string; window: string }) {
  const runId = crypto.randomUUID();
  // KEDA's redis-streams scaler watches this stream and spins the Python worker 0 -> N
  await redis.xadd('analysis-jobs', '*', 'runId', runId,
    'namespace', input.namespace ?? '', 'window', input.window);
  return { runId, status: 'queued' };
}
```

Idempotent within a short window: a run is keyed `(cluster, window, started_at_bucket)`, so a duplicate
trigger returns the existing `runId` instead of enqueueing a second job — matching the analyser's
idempotency guarantee so a redelivered message never double-writes or opens a second PR.

## Resilience

```typescript
// lib/resilience/breaker.ts
import CircuitBreaker from 'opossum';
export function breaker<T extends (...a: any[]) => Promise<any>>(fn: T, name: string, fallback?: any) {
  const cb = new CircuitBreaker(fn, {
    timeout: 8000, errorThresholdPercentage: 50, resetTimeout: 30000, volumeThreshold: 5,
  });
  if (fallback) cb.fallback(fallback);     // e.g. last cached recommendations when the DB is down
  return (...a: Parameters<T>) => cb.fire(...a) as ReturnType<T>;
}
```

Timeout bounds one call; retry with backoff and jitter handles a blip; the breaker handles a sustained
outage; the cache is an availability buffer so the dashboard degrades to slightly-stale-but-useful rather
than blank.

## Standalone build for Docker

`next.config.ts` sets `output: 'standalone'`; the multi-stage `node:24-alpine` image copies
`.next/standalone` + `.next/static` + `public` and runs `node server.js`. No custom server is needed
because there are no long-lived sockets. Dockerfile and Deployment live in kubethrifty-devops-k8s.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| any endpoint that resizes, applies, or rolls back | reads only; the analyser owns `pods/resize`, rollbacks are PRs |
| dropping `evidenceTier` / `bindingConstraint` from a DTO | pass them through; the UI must be able to distinguish observed from modelled |
| computing a throttle ratio client-side from two series | compute the ratio in PromQL over one window |
| returning `0` for an unobserved PSI series | return `null` + `partial: true` |
| SQL or PromQL inside a route handler | move to `lib/**` |
| recomputing recommendations in the API | read what the analyser persisted |
| `runtime = 'edge'` with `pg`/ioredis | `runtime = 'nodejs'` |
| Prometheus URL or arbitrary PromQL reachable from the browser | server-side proxy with a metric allow-list |
| blank dashboard while a run is in progress | serve the last completed run from cache |
| ad-hoc `{ error: '...' }` | RFC 9457 `problem()` |
| `new Pool()` per request | module singleton + `globalThis` guard |
| `node:20-alpine` / Next 14 / Next 15 | `node:24-alpine` / Next 16.3 |

## Quick reference

- **Node 24 LTS**, **Next.js 16.3 App Router**, TypeScript strict, `output: 'standalone'`.
- Handlers thin (`runtime='nodejs'`) → `lib/**` services; cache-aside (Valkey/Redis → TimescaleDB).
- Read-only toward the cluster: trigger = `XADD` + 202. No resize/rollback endpoints, ever.
- New Rev 3 reads: `/api/pods/:name/pressure`, `/api/rehearsals`, `/api/verdicts`, `/api/savings/scenarios`.
- Ratios in PromQL; unobserved series are `null`, not `0`; `evidenceTier` always propagated.
- Security, trust boundaries, full contract → `references/security-and-api.md`; analyser → kubethrifty-python-analyser; UI → kubethrifty-nextjs-frontend.
