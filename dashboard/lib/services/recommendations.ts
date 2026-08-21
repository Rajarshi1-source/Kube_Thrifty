/**
 * recommendations.ts -- the dashboard's primary read.
 *
 * Two things this file is careful about:
 *
 *   * Filters are composed into a parameterised WHERE clause with positional placeholders. The
 *     values arrive from a URL query string, so `$${n}` placeholders are the only acceptable way to
 *     get them into SQL.
 *   * The row mapper turns database NULLs into TypeScript `null`, never into 0. `Number(null)` is
 *     0 in JavaScript, which is exactly the coercion that would render "PSI not observed" as a
 *     green zero-pressure badge. `num()` exists to make that mistake impossible.
 */
import { query } from '../db';
import { cached } from '../cache/client';
import { TTL_RECOMMENDATIONS, recommendations as recKey } from '../cache/keys';
import { env } from '../env';
import type { Recommendation } from '../schemas';
import type { z } from 'zod';
import type { recommendationsQuery } from '../schemas';

interface Row {
  id: number;
  run_id: string;
  namespace: string;
  workload: string;
  container: string;
  resource: string;
  current_request: string | null;
  current_limit: string | null;
  recommended_request: string;
  recommended_limit: string;
  action: string;
  confidence: string;
  evidence_tier: string;
  binding_constraint: string | null;
  sizing_basis: string | null;
  reduction_blocked: boolean;
  rationale: string;
  observed_p50: string | null;
  observed_p95: string | null;
  observed_p99: string | null;
  observed_peak: string | null;
  throttle_ratio: string | null;
  psi_stalled_ratio: string | null;
  oom_events: number | null;
  samples: number;
  forecast_model: string | null;
  rehearsal_id: string | null;
  hpa_coupled: boolean;
  created_at: Date;
}

/**
 * NULL in, null out.
 *
 * `pg` returns DOUBLE PRECISION as a string to avoid precision loss, so every numeric needs
 * conversion -- and the naive `Number(x)` maps null to 0. This is the single most important
 * three-line function in the dashboard.
 */
const num = (v: string | number | null): number | null =>
  v === null || v === undefined ? null : Number(v);

function toRecommendation(r: Row): Recommendation {
  // UNIT BRIDGE. The analyser persists memory in MiB; this API speaks BYTES for memory, matching
  // Prometheus and the `formatBytes` helper. Without the conversion a 640 MiB request arrives as
  // `640` and renders as "0 Mi" -- a plausible-looking number that is wrong by six orders of
  // magnitude. CPU is already in cores on both sides and needs no scaling.
  const scale = r.resource === 'memory' ? 1024 * 1024 : 1;
  const scaled = (v: string | number | null): number | null => {
    const n = num(v);
    return n === null ? null : n * scale;
  };

  const currentRequest = scaled(r.current_request);
  const recommendedRequest = Number(r.recommended_request) * scale;

  return {
    id: r.id,
    runId: r.run_id,
    namespace: r.namespace,
    workload: r.workload,
    container: r.container,
    resource: r.resource as 'cpu' | 'memory',
    currentRequest,
    currentLimit: scaled(r.current_limit),
    recommendedRequest,
    recommendedLimit: Number(r.recommended_limit) * scale,
    action: r.action as Recommendation['action'],
    confidence: r.confidence as Recommendation['confidence'],
    evidenceTier: r.evidence_tier as Recommendation['evidenceTier'],
    bindingConstraint: r.binding_constraint,
    sizingBasis: r.sizing_basis,
    reductionBlocked: r.reduction_blocked,
    rationale: r.rationale,
    observed: {
      p50: scaled(r.observed_p50),
      p95: scaled(r.observed_p95),
      p99: scaled(r.observed_p99),
      peak: scaled(r.observed_peak),
      // Ratios, NOT scaled. Multiplying a throttle ratio by 1024^2 would be absurd, which is
      // exactly why the scaling is applied per-field rather than blanket-applied to the row.
      throttleRatio: num(r.throttle_ratio),
      psiStalledRatio: num(r.psi_stalled_ratio),
      oomEvents: r.oom_events,
    },
    samples: r.samples,
    forecastModel: r.forecast_model,
    rehearsalId: r.rehearsal_id,
    hpaCoupled: r.hpa_coupled,
    // A percentage, never money. Clouds bill per node, so trimming millicores across pods saves
    // nothing until a node disappears -- and only the bin-packer can say whether one did.
    wastePct:
      currentRequest && currentRequest > 0
        ? (currentRequest - recommendedRequest) / currentRequest
        : null,
    createdAt: r.created_at.toISOString(),
  };
}

const SELECT_LATEST = `
  SELECT DISTINCT ON (r.namespace, r.workload, r.container, r.resource) r.*
    FROM recommendations r
    JOIN clusters c ON c.id = r.cluster_id
   WHERE c.name = $1
   ORDER BY r.namespace, r.workload, r.container, r.resource, r.created_at DESC
`;

export async function listRecommendations(
  filters: z.infer<typeof recommendationsQuery>,
): Promise<{ items: Recommendation[]; total: number }> {
  // The unfiltered list is what the landing page requests on every load, so it is the one worth
  // caching. Filtered views are narrow, varied, and cheap; caching each permutation would fill
  // Valkey with entries read once.
  const isDefaultView =
    !filters.namespace &&
    !filters.workload &&
    !filters.action &&
    !filters.evidenceTier &&
    !filters.resource &&
    filters.offset === 0;

  const load = async () => {
    const params: unknown[] = [env.CLUSTER_NAME];
    const conditions: string[] = [];

    // Placeholders are numbered from the params array length, so adding a filter cannot silently
    // shift an earlier one's position.
    if (filters.namespace) {
      params.push(filters.namespace);
      conditions.push(`namespace = $${params.length}`);
    }
    if (filters.workload) {
      params.push(filters.workload);
      conditions.push(`workload = $${params.length}`);
    }
    if (filters.action) {
      params.push(filters.action);
      conditions.push(`action = $${params.length}::rec_action`);
    }
    if (filters.evidenceTier) {
      params.push(filters.evidenceTier);
      conditions.push(`evidence_tier = $${params.length}`);
    }
    if (filters.resource) {
      params.push(filters.resource);
      conditions.push(`resource = $${params.length}`);
    }

    const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
    params.push(filters.limit, filters.offset);

    const sql = `
      WITH latest AS (${SELECT_LATEST})
      SELECT *, count(*) OVER () AS total_count
        FROM latest
        ${where}
       ORDER BY
         -- Biggest reclaimable request first: the ranking a reviewer actually wants.
         CASE WHEN action = 'reduce' THEN 0 WHEN action = 'increase' THEN 1 ELSE 2 END,
         (COALESCE(current_request, 0) - recommended_request) DESC
       LIMIT $${params.length - 1} OFFSET $${params.length}
    `;

    const rows = await query<Row & { total_count: string }>(sql, params);
    return {
      items: rows.map(toRecommendation),
      total: rows.length > 0 ? Number(rows[0]!.total_count) : 0,
    };
  };

  if (!isDefaultView) return load();
  return cached(`${recKey(env.CLUSTER_NAME)}:${filters.limit}`, TTL_RECOMMENDATIONS, load);
}

/** Counts by action and evidence tier, for the KPI cards. */
export async function recommendationSummary(): Promise<{
  byAction: Record<string, number>;
  byEvidenceTier: Record<string, number>;
  coupled: number;
  blocked: number;
}> {
  const sql = `
    WITH latest AS (${SELECT_LATEST})
    SELECT action::text AS action, evidence_tier, hpa_coupled, reduction_blocked, count(*) AS n
      FROM latest
     GROUP BY action, evidence_tier, hpa_coupled, reduction_blocked
  `;
  const rows = await query<{
    action: string;
    evidence_tier: string;
    hpa_coupled: boolean;
    reduction_blocked: boolean;
    n: string;
  }>(sql, [env.CLUSTER_NAME]);

  const byAction: Record<string, number> = {};
  const byEvidenceTier: Record<string, number> = {};
  let coupled = 0;
  let blocked = 0;

  for (const r of rows) {
    const n = Number(r.n);
    byAction[r.action] = (byAction[r.action] ?? 0) + n;
    byEvidenceTier[r.evidence_tier] = (byEvidenceTier[r.evidence_tier] ?? 0) + n;
    if (r.hpa_coupled) coupled += n;
    if (r.reduction_blocked) blocked += n;
  }

  return { byAction, byEvidenceTier, coupled, blocked };
}
