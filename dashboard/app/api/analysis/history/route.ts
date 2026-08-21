/**
 * GET /api/analysis/history
 *
 * Recent analysis runs, newest first. `degraded` runs keep their `degradedReason`: a run that
 * completed with a signal missing produced lower-tier recommendations, and hiding that would let
 * modelled evidence pass for observed.
 */
import { handle, json, parseQuery } from '@/lib/route-helpers';
import { listRuns } from '@/lib/services/analysis';
import { analysisHistoryQuery } from '@/lib/schemas';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const q = parseQuery(request.url, analysisHistoryQuery);
  if (!q.ok) return q.response;

  return handle('GET /api/analysis/history', async () => {
    const runs = await listRuns(q.data.limit);
    return json({ items: runs }, 10);
  });
}
