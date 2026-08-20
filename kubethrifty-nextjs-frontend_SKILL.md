---
name: kubethrifty-nextjs-frontend
description: >-
  Build the KubeThrifty dashboard frontend on Next.js 16.3 (App Router) with TypeScript strict and
  Node.js 24 LTS. Use whenever creating or editing ANY frontend code -- pages and layouts under
  app/, Server vs Client Components, the dark-mode-first shell, KPI cards, the
  waste-versus-pressure quadrant, per-pod detail with the forecast band and PSI series, the
  rehearsal evidence panel, the verdict view with change attribution, the cluster waste-stack and
  node-delta savings page, the HPA-coupled view, evidence-tier badges, TanStack Query, Trigger
  Analysis polling, Zod types, generateMetadata, and loading/error/empty states. Trigger on App
  Router, server component, use client, RSC, generateMetadata, TanStack Query, Suspense,
  loading.tsx, error.tsx, React 19, quadrant, or evidence badge. MANDATE: Next.js 16.3 App Router,
  TypeScript strict, Server-Components-first, no Pages Router; and the UI must never present
  modelled evidence as observed -- an unobserved signal renders as not observed, never as zero.
---

# Next.js 16 App Router (Frontend) — KubeThrifty Dashboard

You build a dark-mode-first cost dashboard that reads as a serious SRE tool. Its job is not to look
impressive — it is to make three things legible at a glance: **where the waste is**, **whether cutting is
safe**, and **what evidence backs that claim**. Server Components fetch; a thin client layer handles
interactivity.

## Why these versions

- **Next.js 16.3.x** — current line (Aug 2026); Turbopack is the default bundler for `dev` and `build` (no
  `--turbopack` flag); React 19.x runtime. Next 15 hits EOL 21 Oct 2026; Next 14 is already EOL.
- **Node.js 24 LTS** for build and runtime (EOL Apr 2028).
- **App Router only.** Server Components by default; `'use client'` at interactivity leaves.

## Critical rules (never violate)

- **Evidence honesty is a UI requirement, not a backend detail.** Every recommendation renders its
  `evidenceTier` badge (`rehearsed` / `modelled` / `partial`). A `null` signal renders as
  **"not observed"** with a distinct treatment — never as `0`, never as an empty chart that implies calm.
  This is the single most important rule in this skill: the whole product claim is "evidence, not
  prediction," and a UI that blurs the two destroys it.
- **Server-first.** Pages and data fetches are Server Components. Add `'use client'` only to leaves that
  need state, effects or handlers (filters, the trigger button, chart tooltips). Charts live in Client
  Components, dynamically imported with `ssr: false` (see kubethrifty-charts).
- **Zod-parse at the boundary.** Parse every API response with the shared schemas so a backend shape change
  fails loudly, not silently.
- **Never fetch Prometheus or the database from the client.** The browser talks only to `/api/**`.
- **The UI has no mutating power over the cluster.** There is no resize button, no rollback button. Show
  the PR link and let GitHub be the control plane. If a stakeholder asks for a one-click revert, the answer
  is a link to the rollback PR the system already opened.
- **Every async surface has `loading.tsx`, `error.tsx`, and an empty state.** A cost dashboard that shows a
  blank screen during a run is a bug — serve the last completed run with a "refreshing" hint.
- **Trigger flow:** POST → get `runId` → poll `/api/analysis/:id` → invalidate. Never block the UI.

## App Router layout

```
app/
  layout.tsx                      # dark theme, nav shell, providers (Server)
  page.tsx                        # KPI cards + waste/pressure quadrant (Server-fetched)
  pods/[podName]/page.tsx         # time-series + forecast band + PSI series + recommendation card
  namespaces/[namespace]/page.tsx
  cluster/page.tsx                # NEW: waste stack funnel + node-delta savings + scenario selector
  rehearsals/page.tsx             # NEW: rehearsal log (safe / regressed / inconclusive)
  rehearsals/[id]/page.tsx        # NEW: before/after signal table + resize conditions + PR link
  verdicts/page.tsx               # NEW: ThriftDetective timeline
  verdicts/[bundleSha]/page.tsx   # NEW: verdict + evidence bundle + change attribution
  coupled/page.tsx                # NEW: HPA-coupled workloads needing an owner decision
  history/page.tsx  savings/page.tsx  loading.tsx  error.tsx  not-found.tsx
components/
  dashboard/  (KPICards, WastePressureQuadrant, NamespaceBreakdown, TriggerAnalysis, EvidenceBadge)
  pods/       (ResourceChart, PressureChart, RecommendationCard, PercentileTable, SizingBasisNote)
  rehearsals/ (RehearsalEvidenceTable, RehearsalOutcomeBadge, ResizeConditionTrail)
  verdicts/   (VerdictCard, AttributionChip, ConfidenceBadge, EvidenceBundleViewer)
  cluster/    (WasteStackFunnel, NodeDeltaCard, ScenarioSelector, PdbBlockedList)
  common/     (LoadingSkeleton, EmptyState, NotObserved, ErrorBoundary)
hooks/    (useRecommendations, usePodMetrics, usePodPressure, useRehearsals, useVerdicts, useTriggerAnalysis)
services/ (api.ts)   types/ (index.ts — Zod schemas + inferred types)
```

## Server Component data fetch (the default)

```tsx
// app/page.tsx  (Server Component)
export default async function DashboardPage() {
  const [recs, summary] = await Promise.all([
    getRecommendations({}), getSavingsSummary({ scenario: 'fixed' }),
  ]);
  return (
    <main className="space-y-6 p-6">
      <KPICards summary={summary} />
      {/* X = unused reservation, Y = pressure. Top-left = safe to cut; bottom-right = needs MORE. */}
      <WastePressureQuadrant recommendations={recs} />
    </main>
  );
}
```

The quadrant replaces Rev 2's single-axis heatmap because a one-dimensional waste view cannot express the
project's central insight: **low utilisation plus high pressure means the workload needs more, not less.**
Render that quadrant with its own label ("Needs more resources"), not as a colour variation.

## The three views that carry Rev 3's story

**1. Pod detail — sizing basis made visible.** Show CPU and memory sized by *different rules* and say so:
CPU from P95 × margin, memory from the observed peak (`sizingBasis`). Render the `bindingConstraint` as a
chip ("forecast floor bound this request"), and the forecast band on the chart. When a rehearsal exists,
the recommendation card leads with its outcome.

**2. Rehearsal detail — the evidence table.** Baseline versus rehearsal for throttle ratio, memory PSI, CPU
PSI, OOM events, restarts, peak memory; `restartsObserved: 0` displayed prominently, because "we changed a
running pod's resources with no restart" is the claim. Show the `PodResizePending` / `PodResizeInProgress`
trail for inconclusive runs — an `Infeasible` reason is *information*, not an error to hide.

**3. Verdict detail — attribution.** Rule, calibrated confidence, evidence, and the attribution chip:
*"OOMKilled 14h after PR #212 reduced memory 1Gi → 320Mi."* Link the PR, the recommendation, and the
rollback PR. Include the bundle hash with a copy button and the `thriftctl replay <sha>` command — the
demo moment is replaying an incident offline.

## Client interactivity with TanStack Query

```tsx
'use client';
export function TriggerAnalysis({ window = '7d' }: { window?: string }) {
  const qc = useQueryClient();
  const trigger = useMutation({
    mutationFn: () => fetch('/api/analysis/trigger', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ window }),
    }).then(r => r.json()),
    onSuccess: ({ runId }) => poll(runId, qc),      // poll /api/analysis/:id then invalidate recs
  });
  return <button onClick={() => trigger.mutate()} disabled={trigger.isPending}>Run analysis</button>;
}
```

Server fetch paints first; TanStack Query handles refetch-after-trigger and background refresh
(stale-while-revalidate). Rehearsals take ~35 minutes, so the rehearsal list polls slowly (60s) and shows
elapsed time plus the revert deadline — never a spinner that implies something is wrong.

## Zod-validated types at the boundary

```typescript
// types/index.ts
export const Recommendation = z.object({
  podName: z.string(), namespace: z.string(), resourceType: z.enum(['cpu', 'memory']),
  currentRequest: z.number(), recommendedRequest: z.number(), recommendedLimit: z.number(),
  savingsMonthlyInr: z.number(), savingsPercentage: z.number(),
  confidence: z.enum(['high', 'medium', 'low']),
  action: z.enum(['reduce', 'increase', 'keep', 'needs_review']),
  evidenceTier: z.enum(['rehearsed', 'modelled', 'partial']),
  bindingConstraint: z.enum(['peak_margin', 'p95_margin', 'forecast', 'rehearsed', 'absolute_min']),
  sizingBasis: z.enum(['peak', 'p95']),
  hpaCoupled: z.boolean(), rehearsalId: z.number().nullable(),
  modelId: z.string(), prUrl: z.string().url().nullable(), regressed: z.boolean(),
  reason: z.string(),
});

// Signals use nullable numbers on purpose: null == NOT OBSERVED, and the UI must not coerce it to 0.
export const SignalWindow = z.object({
  throttleRatio: z.number().nullable(), psiMemFull: z.number().nullable(),
  psiCpuFull: z.number().nullable(), oomKills: z.number(), restarts: z.number(),
});
```

## States, SEO, accessibility

- `loading.tsx` renders skeletons sized like the real cards (no layout shift).
- `error.tsx` is a Client Component with retry; never show a raw error.
- Empty states are specific: "No recommendations yet — run an analysis" versus "No rehearsals yet — the
  next analysis run will rehearse the top 3 candidates."
- `generateMetadata` per route; the landing route gets a real description and an OG screenshot (this is a
  portfolio piece and the live link goes on the résumé).
- Quadrant cells, gauges, badges and evidence chips carry `aria-label`s (waste %, pressure, action,
  evidence tier). **Colour is never the only signal** — every colour is paired with a number or a word.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| rendering a `null` signal as `0` | the `NotObserved` component; distinct treatment and an `aria-label` |
| showing recommendations without the evidence tier | `EvidenceBadge` on every card and row |
| a "resize" or "rollback" button | link the PR; the UI has no cluster write path |
| single-axis waste heatmap | two-axis waste/pressure quadrant |
| hiding `Infeasible` rehearsals | show the condition trail; an infeasible resize is diagnostic |
| per-pod rupee figure as the headline | node delta from `/api/savings/*` |
| `'use client'` on a whole page to fetch data | Server Component fetch; client at leaves |
| Recharts imported in a Server Component | Client Component + `dynamic(..., { ssr: false })` |
| untyped `fetch().then(r => r.json())` | Zod-parse at the boundary |
| blocking the UI on a triggered run | POST → poll → invalidate; stale-while-revalidate |
| Pages Router / `getServerSideProps` | App Router + Server Components |
| `next dev --turbopack` | Turbopack is the default in 16 — drop the flag |

## Quick reference

- **Next.js 16.3 App Router**, React 19, TS strict, Node 24; Turbopack default; Server-first.
- Evidence honesty: `evidenceTier` badge everywhere; `null` = "not observed", never `0`.
- New routes: `/cluster` (waste stack + node delta + scenarios), `/rehearsals`, `/verdicts`, `/coupled`.
- Quadrant, not heatmap: waste × pressure, with an explicit "needs more resources" quadrant.
- No cluster mutation from the UI — PR links only.
- Charts → kubethrifty-charts; tokens/components → kubethrifty-tailwind-shadcn; API → kubethrifty-nextjs-backend.
