import dynamicImport from 'next/dynamic';
import { notFound } from 'next/navigation';
import type { Metadata } from 'next';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EvidenceBadge } from '@/components/evidence-badge';
import { MetricValue, NotObserved } from '@/components/not-observed';
import { getPodMetrics, getPressure } from '@/lib/services/pods';
import { listRecommendations } from '@/lib/services/recommendations';
import { formatPct, formatResource } from '@/lib/utils';

export const dynamic = 'force-dynamic';

const UsageChart = dynamicImport(
  () => import('@/components/charts/usage-chart').then((m) => m.UsageChart),
  { loading: () => <div className="h-72 animate-pulse rounded-lg bg-surface-2" /> },
);

const PressureChart = dynamicImport(
  () => import('@/components/charts/pressure-chart').then((m) => m.PressureChart),
  { loading: () => <div className="h-72 animate-pulse rounded-lg bg-surface-2" /> },
);

export async function generateMetadata({
  params,
}: {
  params: Promise<{ name: string }>;
}): Promise<Metadata> {
  const { name } = await params;
  return { title: name };
}

export default async function PodDetail({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;

  const [cpu, memory, pressure, recs] = await Promise.all([
    getPodMetrics(name, { window: '7d', resource: 'cpu', step: 300 }).catch(() => null),
    getPodMetrics(name, { window: '7d', resource: 'memory', step: 300 }).catch(() => null),
    getPressure(name, { window: '7d', step: 300 }).catch(() => null),
    listRecommendations({ workload: name, limit: 10, offset: 0 }).catch(() => ({
      items: [],
      total: 0,
    })),
  ]);

  if (!cpu && !memory && recs.items.length === 0) notFound();

  return (
    <main className="mx-auto max-w-[1200px] px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Seven days of observed usage, with the sizing basis drawn on each chart so the
          recommendation can be checked by eye.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {recs.items.map((r) => (
          <Card key={`${r.container}-${r.resource}`}>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle>
                  {r.container} &middot; {r.resource}
                </CardTitle>
                <EvidenceBadge tier={r.evidenceTier} showQualifier={false} />
              </div>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs uppercase text-text-muted">current request</dt>
                  <dd>
                    <MetricValue
                      value={r.currentRequest}
                      format={(v) => formatResource(v, r.resource)}
                    />
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-text-muted">proposed</dt>
                  <dd className="tabular">{formatResource(r.recommendedRequest, r.resource)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-text-muted">binding floor</dt>
                  <dd className="font-mono text-xs">{r.bindingConstraint ?? 'unknown'}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-text-muted">waste</dt>
                  <dd>
                    <MetricValue value={r.wastePct} format={(v) => formatPct(v)} />
                  </dd>
                </div>
              </dl>

              {/*
                For memory, state the basis explicitly. `working_set_max` is a SAMPLED gauge: a
                spike between two scrapes is invisible to it, and that is precisely the allocation
                that OOMKills a container. Saying so is what justifies the `partial` tier.
              */}
              {r.resource === 'memory' && (
                <p className="mt-3 text-xs text-text-muted">
                  {r.sizingBasis === 'cgroup_memory_peak'
                    ? 'Sized from the kernel high-water mark (cgroup memory.peak) x 1.25, with limit == request. Memory is never percentile-sized.'
                    : 'Sized from a SAMPLED working-set gauge, not the kernel high-water mark. A spike between scrapes is invisible to it, so this recommendation carries the partial evidence tier until the cgroup collector is deployed.'}
                </p>
              )}

              <p className="mt-3 text-xs leading-relaxed text-text-secondary">{r.rationale}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="mt-8 space-y-6">
        {cpu && (
          <Card>
            <CardHeader>
              <CardTitle>CPU usage versus request</CardTitle>
            </CardHeader>
            <CardContent>
              <UsageChart metrics={cpu} />
            </CardContent>
          </Card>
        )}

        {memory && (
          <Card>
            <CardHeader>
              <CardTitle>Memory usage versus request</CardTitle>
              <p className="text-sm text-text-secondary">
                The peak line is the sizing basis. It sits above where a P95 would fall, which is the
                entire reason memory is not percentile-sized: a percentile discards the top 5% of
                samples, and those are the allocations that cause an OOM kill.
              </p>
            </CardHeader>
            <CardContent>
              <UsageChart metrics={memory} />
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Observed pressure</CardTitle>
            <p className="text-sm text-text-secondary">
              PSI stall time and the CFS throttle ratio. Ratios, never raw period counters &mdash;
              counters scale with window length and replica count, so two of them cannot be compared.
            </p>
          </CardHeader>
          <CardContent>
            {pressure ? (
              <PressureChart pressure={pressure} />
            ) : (
              <NotObserved reason="Prometheus could not be reached for this pod's pressure series." />
            )}
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
