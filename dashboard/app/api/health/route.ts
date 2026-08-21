/**
 * GET /api/health          readiness (dependency-aware)
 * GET /api/health?probe=live   liveness (dependency-free)
 *
 * The split matters. Liveness must NEVER depend on Prometheus or the database: if it did, a
 * dependency outage would make Kubernetes restart this pod, which cannot fix the dependency and
 * converts one outage into a crash-loop.
 *
 * Readiness requires only the database. Prometheus and the cache are reported but not required --
 * without them the dashboard still serves stored recommendations, with charts showing "not observed".
 * That is a degraded service, and a degraded service should stay in the load balancer.
 */
import { handle, json } from '@/lib/route-helpers';
import { liveness, readiness } from '@/lib/services/health';
import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const probe = new URL(request.url).searchParams.get('probe');

  if (probe === 'live') {
    return json(liveness(), 0);
  }

  return handle('GET /api/health', async () => {
    const report = await readiness();
    // 503 only when a REQUIRED dependency is down. 'degraded' returns 200 on purpose: pulling a
    // degraded replica out of service just moves its traffic onto equally degraded siblings.
    const status = report.status === 'unhealthy' ? 503 : 200;
    return NextResponse.json(report, {
      status,
      headers: { 'cache-control': 'no-store' },
    });
  });
}
