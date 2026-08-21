/**
 * GET /api/rehearsals
 *
 * The record of live-pod experiments. Note the absence of a POST: starting a rehearsal mutates a
 * live pod through `patch pods/resize`, and that capability lives with the analyser, which holds the
 * RBAC and writes its compensation row before touching anything.
 */
import { handle, json, parseQuery } from '@/lib/route-helpers';
import { listRehearsals, overdueRehearsals } from '@/lib/services/rehearsals';
import { rehearsalsQuery } from '@/lib/schemas';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const q = parseQuery(request.url, rehearsalsQuery);
  if (!q.ok) return q.response;

  return handle('GET /api/rehearsals', async () => {
    const [items, overdue] = await Promise.all([listRehearsals(q.data), overdueRehearsals()]);
    return json(
      {
        items,
        // Surfaced at the top level rather than buried in the list: a rehearsal past its deadline
        // means a live pod may still be carrying experimental limits, which is the one thing about
        // this subsystem an operator must never have to hunt for.
        overdue: overdue.map((r) => ({
          rehearsalId: r.rehearsalId,
          pod: r.pod,
          namespace: r.namespace,
          revertDeadline: r.revertDeadline,
        })),
      },
      10,
    );
  });
}
