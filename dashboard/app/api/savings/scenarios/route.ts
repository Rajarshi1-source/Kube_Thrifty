/**
 * GET /api/savings/scenarios
 *
 * All three pricing scenarios side by side.
 *
 * Returned together on purpose. The same node delta is worth very different amounts under a fixed
 * node pool, self-managed Karpenter, and EKS Auto Mode (which adds roughly a 12% managed surcharge),
 * and showing one number without that context invites a reader to treat a modelling assumption as a
 * fact. Every entry carries its own `asOf` date and region.
 */
import { handle, json } from '@/lib/route-helpers';
import { getAllScenarios } from '@/lib/services/savings';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  return handle('GET /api/savings/scenarios', async () => {
    const scenarios = await getAllScenarios();
    return json(
      {
        scenarios,
        note:
          'Savings are a node-count delta multiplied by a pinned node price. Per-pod waste is ' +
          'reported as a ratio only: clouds bill per node, so trimming millicores across pods ' +
          'saves nothing until a node is actually removed.',
      },
      60,
    );
  });
}
