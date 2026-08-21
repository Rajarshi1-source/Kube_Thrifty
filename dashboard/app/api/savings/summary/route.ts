/**
 * GET /api/savings/summary?scenario=...
 *
 * The money endpoint. `monthlySaving` is null when no bin-packing simulation has run -- not 0,
 * because "not computed" and "nothing to save" are different answers and only one of them is honest
 * before the packer runs.
 */
import { handle, json, parseQuery } from '@/lib/route-helpers';
import { getSavingsSummary } from '@/lib/services/savings';
import { savingsQuery } from '@/lib/schemas';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const q = parseQuery(request.url, savingsQuery);
  if (!q.ok) return q.response;

  return handle('GET /api/savings/summary', async () => {
    const summary = await getSavingsSummary(q.data.scenario);
    return json(summary, 60);
  });
}
