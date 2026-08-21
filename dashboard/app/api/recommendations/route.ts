/**
 * GET /api/recommendations
 *
 * The dashboard's main list. Filterable by namespace, workload, action, evidence tier and resource.
 *
 * Read-only, like every route in this API. There is no POST, PUT, PATCH or DELETE here and there is
 * no "apply" endpoint anywhere: a durable resource change reaches a cluster only through a merged
 * Git PR, so the UI links to the PR instead of offering a button.
 */
import { handle, json, parseQuery } from '@/lib/route-helpers';
import { listRecommendations, recommendationSummary } from '@/lib/services/recommendations';
import { recommendationsQuery } from '@/lib/schemas';

// `pg` and `redis` are TCP clients, which the edge runtime cannot open.
export const runtime = 'nodejs';
// Recommendations change only when the analyser runs (every 6h). Rendering them at request time
// against live data is still correct; what must not happen is Next.js baking them into the build.
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const parsed = parseQuery(request.url, recommendationsQuery);
  if (!parsed.ok) return parsed.response;

  return handle('GET /api/recommendations', async () => {
    const [{ items, total }, summary] = await Promise.all([
      listRecommendations(parsed.data),
      recommendationSummary(),
    ]);

    return json(
      {
        items,
        total,
        limit: parsed.data.limit,
        offset: parsed.data.offset,
        summary,
      },
      // 30s shared cache. Short enough that a fresh run shows up promptly, long enough to absorb a
      // dashboard refresh storm without re-querying.
      30,
    );
  });
}
