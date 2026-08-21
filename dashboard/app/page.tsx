import dynamicImport from 'next/dynamic';
import Link from 'next/link';

import { KpiCards } from '@/components/kpi-cards';
import { RecommendationsTable } from '@/components/recommendations-table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { listRecommendations, recommendationSummary } from '@/lib/services/recommendations';
import { getSavingsSummary } from '@/lib/services/savings';
import { listRuns } from '@/lib/services/analysis';
import { readiness } from '@/lib/services/health';

/**
 * The landing page. A Server Component that reads the service layer directly rather than fetching
 * its own API -- an HTTP round trip to the same process would buy nothing.
 *
 * Every read is individually fault-tolerant. A dashboard whose whole purpose is reporting on
 * dependency health must not 500 when a dependency is unhealthy; each section degrades to "not
 * observed" on its own.
 */
export const dynamic = 'force-dynamic';

// The quadrant is a Client Component (Recharts needs the DOM), so it is imported dynamically to keep
// it out of the initial server-rendered payload.
const WastePressureQuadrant = dynamicImport(
  () => import('@/components/charts/waste-pressure-quadrant').then((m) => m.WastePressureQuadrant),
  {
    loading: () => <div className="h-96 animate-pulse rounded-lg bg-surface-2" />,
  },
);

export default async function Home() {
  const [recs, summary, savings, runs, health] = await Promise.all([
    listRecommendations({ limit: 100, offset: 0 }).catch(() => ({ items: [], total: 0 })),
    recommendationSummary().catch(() => null),
    getSavingsSummary('fixed_node_pool').catch(() => null),
    listRuns(1).catch(() => []),
    readiness().catch(() => null),
  ]);

  const lastRun = runs[0];

  return (
    <main className="mx-auto max-w-[1400px] px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">KubeThrifty</h1>
        <p className="mt-1 max-w-3xl text-sm text-text-secondary">
          CPU is sized from percentiles; memory is sized from the observed peak. Sizing floors only
          ever raise a request. Resource changes ship as pull requests &mdash; this dashboard has no
          apply button and holds no cluster credentials.
        </p>
      </header>

      {health && health.warnings.length > 0 && (
        <div className="mb-6 rounded-lg border border-action-review/40 bg-action-review/10 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-action-review">
            degraded
          </p>
          <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-text-secondary">
            {health.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <KpiCards summary={summary} savings={savings} />

      <section className="mt-8">
        <Card>
          <CardHeader>
            <CardTitle>Waste versus observed pressure</CardTitle>
            <p className="text-sm text-text-secondary">
              Wasteful and comfortable is safe to shrink. Lean and suffering must grow. A
              percentile-only tool sees only the horizontal axis, and would shrink whatever sits at
              the top of the vertical one.
            </p>
          </CardHeader>
          <CardContent>
            <WastePressureQuadrant items={recs.items} />
          </CardContent>
        </Card>
      </section>

      <section className="mt-8">
        <Card>
          <CardHeader>
            <div className="flex items-baseline justify-between gap-4">
              <div>
                <CardTitle>Recommendations</CardTitle>
                <p className="text-sm text-text-secondary">
                  {recs.total} container/resource pair(s), newest analysis per pair.
                </p>
              </div>
              {lastRun && (
                <p className="text-xs text-text-muted">
                  last run{' '}
                  <span className="font-mono">{lastRun.status}</span>
                  {lastRun.degradedReason && (
                    <span className="text-action-review"> &middot; {lastRun.degradedReason}</span>
                  )}
                  {lastRun.prUrl && (
                    <>
                      {' '}
                      &middot;{' '}
                      <Link
                        href={lastRun.prUrl}
                        className="underline hover:text-text-secondary"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        pull request
                      </Link>
                    </>
                  )}
                </p>
              )}
            </div>
          </CardHeader>
          <CardContent className="px-0">
            <RecommendationsTable items={recs.items} />
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
