/**
 * GET /api/pods/:name/pressure
 *
 * PSI stall time and the throttle ratio for one pod. The evidence layer, exposed.
 *
 * This route returns `observed: false` with an `unavailableReason` when PSI is not being collected.
 * It does NOT return zeros. That distinction is the whole point: a flat line at zero reads as "this
 * workload is comfortable, shrink it", which is the opposite of what missing data means.
 */
import { handle, json, parseParams, parseQuery } from '@/lib/route-helpers';
import { getPressure } from '@/lib/services/pods';
import { podNameParam, pressureQuery } from '@/lib/schemas';
import { notFound } from '@/lib/problem';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request, ctx: { params: Promise<{ name: string }> }) {
  const raw = await ctx.params;

  const name = parseParams(raw, podNameParam);
  if (!name.ok) return name.response;

  const q = parseQuery(request.url, pressureQuery);
  if (!q.ok) return q.response;

  return handle('GET /api/pods/:name/pressure', async () => {
    const pressure = await getPressure(name.data.name, {
      window: q.data.window,
      step: q.data.step,
    });
    if (!pressure) {
      return notFound(`Pod '${name.data.name}' has no analysed container`);
    }
    return json(pressure, 30);
  });
}
