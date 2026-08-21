/**
 * GET /api/verdicts
 *
 * ThriftDetective's output. Filterable by namespace, workload and rule id.
 *
 * `selfAttributed` is broken out separately because it is the product's sharpest claim: verdicts
 * where KubeThrifty's own merged change is the suspected cause. Making it a filter the caller has to
 * know about would let the most important number stay hidden by default.
 */
import { handle, json, parseQuery } from '@/lib/route-helpers';
import { listVerdicts, selfAttributedVerdicts } from '@/lib/services/verdicts';
import { verdictsQuery } from '@/lib/schemas';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const q = parseQuery(request.url, verdictsQuery);
  if (!q.ok) return q.response;

  return handle('GET /api/verdicts', async () => {
    const [items, selfAttributed] = await Promise.all([
      listVerdicts(q.data),
      selfAttributedVerdicts(10),
    ]);
    return json({ items, selfAttributed }, 30);
  });
}
