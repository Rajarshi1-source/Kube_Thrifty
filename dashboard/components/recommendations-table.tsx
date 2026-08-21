import Link from 'next/link';

import { ActionBadge, BlockedBadge, HpaCoupledBadge } from '@/components/action-badge';
import { EvidenceBadge } from '@/components/evidence-badge';
import { MetricValue, NotObserved } from '@/components/not-observed';
import type { Recommendation } from '@/lib/schemas';
import { formatPct, formatResource } from '@/lib/utils';

/**
 * The recommendation table. A Server Component -- it is static markup over data fetched on the
 * server, and shipping a client bundle for it would buy nothing.
 *
 * Every row carries its evidence tier and its binding constraint. That is the difference between a
 * tool that says "set this to 120m" and one that says "set this to 120m BECAUSE P95 x 1.2 was the
 * highest of the four floors, measured over 504 samples, and nothing was throttled".
 *
 * There is no apply button, and no row action of any kind. The only affordance is a link to the pull
 * request, because durable resource changes reach a cluster through a merged PR and nowhere else.
 */
export function RecommendationsTable({ items }: { items: Recommendation[] }) {
  if (items.length === 0) {
    return (
      <p className="px-5 py-8 text-center text-sm text-text-muted">
        No recommendations yet. Trigger an analysis, or wait for the 6-hourly enqueuer.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border-subtle text-left text-xs uppercase tracking-wide text-text-muted">
            <th className="px-4 py-3 font-medium">workload / container</th>
            <th className="px-4 py-3 font-medium">res</th>
            <th className="px-4 py-3 font-medium">action</th>
            <th className="px-4 py-3 text-right font-medium">current</th>
            <th className="px-4 py-3 text-right font-medium">proposed</th>
            <th className="px-4 py-3 text-right font-medium">waste</th>
            <th className="px-4 py-3 font-medium">binding floor</th>
            <th className="px-4 py-3 font-medium">pressure</th>
            <th className="px-4 py-3 font-medium">evidence</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr
              key={`${r.namespace}/${r.workload}/${r.container}/${r.resource}`}
              className="border-b border-border-subtle/50 hover:bg-surface-2/40"
            >
              <td className="px-4 py-3">
                <Link
                  href={`/pods/${r.workload}`}
                  className="font-medium text-text-primary hover:underline"
                >
                  {r.workload}
                </Link>
                <span className="text-text-muted">/{r.container}</span>
                <div className="mt-0.5 text-xs text-text-muted">{r.namespace}</div>
              </td>

              <td className="px-4 py-3 text-text-secondary">{r.resource}</td>

              <td className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-1">
                  <ActionBadge action={r.action} />
                  {r.reductionBlocked && <BlockedBadge />}
                  {r.hpaCoupled && <HpaCoupledBadge />}
                </div>
              </td>

              <td className="px-4 py-3 text-right">
                <MetricValue
                  value={r.currentRequest}
                  format={(v) => formatResource(v, r.resource)}
                  reason="No declared request. Without one there is no denominator and no waste to measure."
                />
              </td>

              <td className="px-4 py-3 text-right tabular">
                {formatResource(r.recommendedRequest, r.resource)}
              </td>

              <td className="px-4 py-3 text-right">
                {/* A ratio. Never a currency amount -- see the savings page for the node delta. */}
                <MetricValue value={r.wastePct} format={(v) => formatPct(v)} />
              </td>

              <td className="px-4 py-3">
                <span
                  className="font-mono text-xs text-text-secondary"
                  title={r.rationale}
                >
                  {r.bindingConstraint ?? 'unknown'}
                </span>
                {r.sizingBasis && (
                  <div
                    className="mt-0.5 text-xs text-text-muted"
                    title={
                      r.sizingBasis === 'cgroup_memory_peak'
                        ? 'The kernel high-water mark from cgroup memory.peak. The strongest memory evidence available.'
                        : r.sizingBasis === 'working_set_max'
                          ? 'A SAMPLED gauge, not the kernel high-water mark. A spike between scrapes is invisible to it, which is why this downgrades the evidence tier.'
                          : 'The percentile the CPU request was derived from.'
                    }
                  >
                    from {r.sizingBasis}
                  </div>
                )}
              </td>

              <td className="px-4 py-3">
                {r.observed.psiStalledRatio === null && r.observed.throttleRatio === null ? (
                  <NotObserved
                    compact
                    reason="Neither PSI nor CFS throttle counters were available for this container."
                  />
                ) : (
                  <div className="space-y-0.5 text-xs">
                    <div>
                      psi{' '}
                      <MetricValue
                        value={r.observed.psiStalledRatio}
                        format={(v) => formatPct(v, 3)}
                      />
                    </div>
                    <div>
                      thr{' '}
                      <MetricValue
                        value={r.observed.throttleRatio}
                        format={(v) => formatPct(v, 3)}
                      />
                    </div>
                  </div>
                )}
              </td>

              <td className="px-4 py-3">
                <EvidenceBadge tier={r.evidenceTier} showQualifier={false} />
                <div className="mt-0.5 text-xs text-text-muted tabular">
                  {r.samples} samples
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
