/**
 * GET /api/analysis/:id
 *
 * The polling target for a triggered run.
 *
 * A run id that is not in the database yet is NOT a 404. The analyser mints nothing -- the id was
 * minted by the producer at XADD time, so between `202` and the worker's first write the run
 * legitimately exists as "queued, no row yet". Returning 404 there would make the UI show "not
 * found" for a run the user just started, which reads as a failure.
 */
import { handle, json, parseParams } from '@/lib/route-helpers';
import { getRun } from '@/lib/services/analysis';
import { runIdParam } from '@/lib/schemas';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const raw = await ctx.params;

  const parsed = parseParams(raw, runIdParam);
  if (!parsed.ok) return parsed.response;

  return handle('GET /api/analysis/:id', async () => {
    const run = await getRun(parsed.data.id);

    if (!run) {
      return json(
        {
          runId: parsed.data.id,
          status: 'queued' as const,
          message:
            'The job is on the stream but no worker has claimed it yet. The analyser scales from ' +
            'zero, so a cold start can take a minute.',
        },
        // Never cached: the whole point of this endpoint is to observe a change.
        0,
      );
    }

    return json(run, 0);
  });
}
