/**
 * POST /api/analysis/trigger
 *
 * THE ONLY WRITE IN THIS ENTIRE API. It XADDs a job onto `analysis-jobs` and returns
 * `202 Accepted { runId }`.
 *
 * What it cannot do, by construction rather than by policy:
 *   - It cannot touch the cluster. This process holds no kubeconfig and no service-account token.
 *   - It cannot open a PR. `auto_pr` is hard-coded false in the enqueuer; whether a run opens a PR
 *     is the analyser's decision under its own configuration.
 *   - It cannot resize anything, roll anything back, or apply a manifest. Durable resource changes
 *     reach a cluster only through a merged Git PR.
 *
 * 202, not 200: the work has been ACCEPTED, not done. The analyser runs at `minReplicaCount: 0`
 * behind a KEDA consumer-group scaler, so the run may not even have a pod yet. Returning 200 would
 * imply a completed analysis and invite the client to fetch results that do not exist.
 */
import { NextResponse } from 'next/server';

import { handle, parseJsonBody } from '@/lib/route-helpers';
import { triggerAnalysis } from '@/lib/services/analysis';
import { triggerBody } from '@/lib/schemas';
import { upstreamUnavailable } from '@/lib/problem';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  const body = await parseJsonBody(request, triggerBody);
  if (!body.ok) return body.response;

  return handle('POST /api/analysis/trigger', async () => {
    let runId: string;
    try {
      ({ runId } = await triggerAnalysis({
        window: body.data.window,
        ...(body.data.namespaces ? { namespaces: body.data.namespaces } : {}),
      }));
    } catch (err) {
      // Not retried anywhere in the stack: a retried XADD enqueues the analysis twice. The caller is
      // told plainly that nothing was enqueued and can decide for itself.
      return upstreamUnavailable(
        'Valkey',
        `The analysis could not be enqueued, so nothing was started. ${
          err instanceof Error ? err.message : ''
        }`.trim(),
      );
    }

    return NextResponse.json(
      {
        runId,
        status: 'accepted',
        message:
          'Analysis enqueued. Poll /api/analysis/' +
          runId +
          ' for progress. This endpoint does not modify cluster state; any resource change ships as ' +
          'a pull request for review.',
      },
      {
        status: 202,
        headers: {
          // Where to poll, so a client need not construct the URL itself.
          location: `/api/analysis/${runId}`,
          'cache-control': 'no-store',
        },
      },
    );
  });
}
