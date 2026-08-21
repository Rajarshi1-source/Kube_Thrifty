import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EvidenceBadge } from '@/components/evidence-badge';
import { NotObserved } from '@/components/not-observed';
import type { SavingsSummary } from '@/lib/schemas';
import { formatMoney, formatPct } from '@/lib/utils';

interface Summary {
  byAction: Record<string, number>;
  byEvidenceTier: Record<string, number>;
  coupled: number;
  blocked: number;
}

/**
 * The KPI row.
 *
 * The savings card is the one to read carefully. It shows a currency figure ONLY when the bin-packer
 * has produced a node-count delta. Before that it says "not computed" and explains why -- because
 * the alternative, summing per-pod millicores into a rupee total, is the standard lie in this
 * product category and the reason nobody trusts these tools.
 */
export function KpiCards({
  summary,
  savings,
}: {
  summary: Summary | null;
  savings: SavingsSummary | null;
}) {
  const totalActionable = (summary?.byAction.reduce ?? 0) + (summary?.byAction.increase ?? 0);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader>
          <CardTitle>Actionable</CardTitle>
        </CardHeader>
        <CardContent>
          {summary === null ? (
            <NotObserved reason="The recommendation store is unreachable." />
          ) : (
            <>
              <p className="tabular text-3xl">{totalActionable}</p>
              <p className="mt-1 text-xs text-text-secondary">
                {summary.byAction.reduce ?? 0} reduce &middot; {summary.byAction.increase ?? 0}{' '}
                increase
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Monthly saving</CardTitle>
        </CardHeader>
        <CardContent>
          {savings === null || savings.monthlySaving === null ? (
            <>
              <p className="text-lg text-text-secondary">not computed</p>
              <p className="mt-1 text-xs text-text-muted">
                Savings are a node-count delta. Until the bin-packer removes a node there is no
                figure &mdash; trimming requests across pods saves nothing on its own.
              </p>
            </>
          ) : (
            <>
              <p className="tabular text-3xl">
                {formatMoney(savings.monthlySaving, savings.currency)}
              </p>
              <p className="mt-1 text-xs text-text-secondary">
                {savings.nodesRemoved} node(s) removable &middot; prices as of {savings.asOf}
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cluster waste</CardTitle>
        </CardHeader>
        <CardContent>
          {savings?.wasteRatio == null ? (
            <NotObserved reason="No workload has a declared request to measure against." />
          ) : (
            <>
              <p className="tabular text-3xl">{formatPct(savings.wasteRatio)}</p>
              {/* A ratio, deliberately with no currency anywhere near it. */}
              <p className="mt-1 text-xs text-text-muted">
                of requested CPU went unused. A ratio, not an amount.
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Evidence</CardTitle>
        </CardHeader>
        <CardContent>
          {summary === null ? (
            <NotObserved />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {(['rehearsed', 'modelled', 'partial'] as const).map((tier) => {
                const n = summary.byEvidenceTier[tier] ?? 0;
                if (n === 0) return null;
                return (
                  <span key={tier} className="inline-flex items-center gap-1">
                    <EvidenceBadge tier={tier} showQualifier={false} />
                    <span className="tabular text-sm">{n}</span>
                  </span>
                );
              })}
              {Object.keys(summary.byEvidenceTier).length === 0 && (
                <p className="text-sm text-text-muted">no analysis yet</p>
              )}
            </div>
          )}
          {summary && summary.blocked > 0 && (
            <p className="mt-2 text-xs text-text-secondary">
              {summary.blocked} reduction(s) withheld because pressure was observed.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
