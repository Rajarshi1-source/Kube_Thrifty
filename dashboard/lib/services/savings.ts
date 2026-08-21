/**
 * savings.ts -- the money endpoint, and the one place the product is allowed to print a currency
 * symbol.
 *
 * The rule, restated because it is the easiest thing in this codebase to get wrong:
 *
 *     savings = (nodesBefore - nodesAfter) x nodePrice
 *
 * A node-count delta from the bin-packer is the ONLY figure that carries money. Per-pod waste is a
 * percentage. Clouds bill per node, so reclaiming 400 millicores spread across thirty pods saves
 * exactly nothing until a node actually disappears -- and summing per-pod millicores into a rupee
 * total is the single most common lie in this product category.
 *
 * When no packing simulation has run, `monthlySaving` is null and the note says why. It is not 0:
 * "we have not computed this" and "there is nothing to save" are different claims.
 */
import { query } from '../db';
import { cached } from '../cache/client';
import { TTL_SAVINGS, savings as savingsKey } from '../cache/keys';
import { env } from '../env';
import type { SavingsSummary } from '../schemas';
import { PRICING_SCENARIOS } from '../schemas';

type Scenario = (typeof PRICING_SCENARIOS)[number];

/**
 * Normalise a DATE column to YYYY-MM-DD.
 *
 * `lib/db.ts` registers a type parser so DATE arrives as a string, which is the lossless form. The
 * Date branch remains as a guard: if that parser is ever removed, the LOCAL date components are read
 * rather than `toISOString()`, because a DATE parsed to local midnight shifts a day under
 * `toISOString()` on any host east of UTC.
 */
function asOfString(value: Date | string | null): string {
  if (value === null) return 'unknown';
  if (value instanceof Date) {
    const y = value.getFullYear();
    const m = String(value.getMonth() + 1).padStart(2, '0');
    const d = String(value.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
  return value.slice(0, 10);
}

/**
 * Aggregate per-pod waste as a RATIO.
 *
 * Deliberately returns no currency. This is a ranking and sizing signal -- how much of what was
 * requested went unused -- and it is the input to the packer, never a substitute for its output.
 */
async function wasteRatio(): Promise<{ ratio: number | null; workloads: number }> {
  const rows = await query<{ requested: string | null; recommended: string | null; n: string }>(
    `WITH latest AS (
       SELECT DISTINCT ON (r.namespace, r.workload, r.container, r.resource) r.*
         FROM recommendations r
         JOIN clusters c ON c.id = r.cluster_id
        WHERE c.name = $1
        ORDER BY r.namespace, r.workload, r.container, r.resource, r.created_at DESC
     )
     SELECT sum(current_request) AS requested,
            sum(recommended_request) AS recommended,
            count(*) AS n
       FROM latest
      WHERE resource = 'cpu' AND current_request IS NOT NULL`,
    [env.CLUSTER_NAME],
  );

  const row = rows[0];
  if (!row?.requested || !row.recommended) return { ratio: null, workloads: 0 };

  const requested = Number(row.requested);
  const recommended = Number(row.recommended);
  return {
    ratio: requested > 0 ? (requested - recommended) / requested : null,
    workloads: Number(row.n),
  };
}

/**
 * The savings summary for one pricing scenario.
 *
 * `nodesBefore`/`nodesAfter` come from the analyser's packing simulation. Until Phase 8 records
 * one, this reports the honest state: a waste ratio, no node delta, and no money.
 */
export async function getSavingsSummary(scenario: Scenario): Promise<SavingsSummary> {
  return cached(savingsKey(env.CLUSTER_NAME, scenario), TTL_SAVINGS, async () => {
    const waste = await wasteRatio();

    // Deliberately NOT wrapped in a catch that returns []. Swallowing a query failure here would
    // render as "no bin-packing simulation has run yet", which is a different and misleading claim:
    // it conflates "the database could not answer" with "there is no saving". The error propagates
    // to the route handler and becomes a 502 naming the dependency.
    const packing = await query<{
      nodes_before: number;
      nodes_after: number;
      monthly_saving: string | null;
      currency: string;
      // DATE columns come back from `pg` as a JS Date, not a string.
      as_of: Date | string | null;
      region: string;
    }>(
      // Every column is qualified with `s.`. `region` exists on BOTH savings_reports and clusters,
      // so an unqualified reference is a hard "column reference is ambiguous" error -- and the
      // region that matters here is the one the PRICE was quoted for, not the cluster's own.
      `SELECT s.nodes_before, s.nodes_after, s.monthly_saving, s.currency, s.as_of, s.region
         FROM savings_reports s
         JOIN clusters c ON c.id = s.cluster_id
        WHERE c.name = $1 AND s.scenario = $2
        ORDER BY s.created_at DESC
        LIMIT 1`,
      [env.CLUSTER_NAME, scenario],
    );

    const report = packing[0];

    if (!report) {
      return {
        scenario,
        nodesBefore: 0,
        nodesAfter: 0,
        nodesRemoved: 0,
        // NOT 0. No packing simulation has run, so the saving is unknown, and claiming zero would
        // be as wrong as claiming a number.
        monthlySaving: null,
        currency: 'USD',
        asOf: 'unknown',
        region: 'unknown',
        wasteRatio: waste.ratio,
        note:
          `${waste.workloads} workload(s) analysed. No bin-packing simulation has run for this ` +
          `cluster yet, so there is no node-count delta and therefore no monetary figure. ` +
          `Per-pod waste is reported as a ratio only: trimming requests saves nothing until a ` +
          `node is actually removed.`,
      } satisfies SavingsSummary;
    }

    const nodesRemoved = report.nodes_before - report.nodes_after;

    return {
      scenario,
      nodesBefore: report.nodes_before,
      nodesAfter: report.nodes_after,
      nodesRemoved,
      monthlySaving: report.monthly_saving === null ? null : Number(report.monthly_saving),
      currency: report.currency,
      // Surfaced so a stale price catalogue is visible rather than assumed current. Normalised to
      // YYYY-MM-DD: `pg` returns a DATE as a JS Date, which would otherwise serialise to a full
      // ISO timestamp and imply a precision the price catalogue does not have.
      asOf: asOfString(report.as_of),
      region: report.region,
      wasteRatio: waste.ratio,
      note:
        nodesRemoved > 0
          ? `${nodesRemoved} node(s) become removable under the ${scenario} scenario. Prices as of ` +
            `${report.as_of} for ${report.region}.`
          : `No node becomes removable under this scenario, so there is no saving to report even ` +
            `though per-pod requests can be trimmed. This is the honest answer, not a failure.`,
    } satisfies SavingsSummary;
  });
}

/** All three scenarios side by side, so the pricing model is a visible choice rather than a hidden one. */
export async function getAllScenarios(): Promise<SavingsSummary[]> {
  return Promise.all(PRICING_SCENARIOS.map((s) => getSavingsSummary(s)));
}
