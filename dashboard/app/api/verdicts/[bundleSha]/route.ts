/**
 * GET /api/verdicts/:bundleSha
 *
 * Every verdict derived from one evidence bundle, ranked by confidence.
 *
 * The path parameter is validated as a 64-character sha256, which is both correctness and safety: it
 * is the content address of the canonical evidence JSON, and the pattern admits no character that
 * could matter to SQL.
 *
 * A bundle usually yields more than one verdict -- the rules are not mutually exclusive, and a real
 * incident often trips several. All of them are returned so the UI can lead with the strongest
 * without discarding the alternatives.
 */
import { handle, json, parseParams } from '@/lib/route-helpers';
import { getVerdictsByBundle } from '@/lib/services/verdicts';
import { bundleShaParam } from '@/lib/schemas';
import { notFound } from '@/lib/problem';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(_request: Request, ctx: { params: Promise<{ bundleSha: string }> }) {
  const raw = await ctx.params;

  const parsed = parseParams(raw, bundleShaParam);
  if (!parsed.ok) return parsed.response;

  return handle('GET /api/verdicts/:bundleSha', async () => {
    const verdicts = await getVerdictsByBundle(parsed.data.bundleSha);
    if (verdicts.length === 0) {
      return notFound(`Evidence bundle '${parsed.data.bundleSha.slice(0, 12)}...'`);
    }
    return json(
      {
        bundleSha: parsed.data.bundleSha,
        verdicts,
        // Stated on the response so the UI can show it verbatim. A verdict that cannot be
        // reproduced is an opinion, and this is the command that reproduces it.
        replayCommand: `thriftctl replay ${parsed.data.bundleSha}`,
      },
      // Verdicts are immutable: investigate() is pure, so the same bundle under the same ruleset
      // always yields the same answer. A long cache is safe.
      600,
    );
  });
}
