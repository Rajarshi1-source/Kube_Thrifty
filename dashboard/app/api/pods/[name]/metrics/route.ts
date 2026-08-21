/**
 * GET /api/pods/:name/metrics
 *
 * Usage over time for one pod, plus the reference lines that explain the recommendation: the
 * declared request and limit, and the sizing basis (p95 for CPU, the observed peak for memory).
 *
 * The series can contain nulls, and they are preserved all the way to the client. A gap means "no
 * sample", and the chart draws it as a gap.
 */
import { handle, json, parseParams, parseQuery } from '@/lib/route-helpers';
import { getPodMetrics } from '@/lib/services/pods';
import { podMetricsQuery, podNameParam } from '@/lib/schemas';
import { notFound } from '@/lib/problem';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request, ctx: { params: Promise<{ name: string }> }) {
  // Next.js 16 delivers route params as a promise.
  const raw = await ctx.params;

  const name = parseParams(raw, podNameParam);
  if (!name.ok) return name.response;

  const q = parseQuery(request.url, podMetricsQuery);
  if (!q.ok) return q.response;

  return handle('GET /api/pods/:name/metrics', async () => {
    const metrics = await getPodMetrics(name.data.name, {
      window: q.data.window,
      resource: q.data.resource,
      step: q.data.step,
    });
    if (!metrics) {
      return notFound(`Pod '${name.data.name}' has no analysed container`);
    }
    return json(metrics, 30);
  });
}
