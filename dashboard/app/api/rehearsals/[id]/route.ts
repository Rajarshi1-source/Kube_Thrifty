/**
 * GET /api/rehearsals/:id
 *
 * One rehearsal in full: baseline signals, observed signals, trip reasons, and the compensation that
 * was recorded before the pod was touched. This is the evidence a PR cites.
 */
import { handle, json, parseParams } from '@/lib/route-helpers';
import { getRehearsal } from '@/lib/services/rehearsals';
import { notFound } from '@/lib/problem';
import { z } from 'zod';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const idParam = z.object({
  id: z.string().min(1).max(128).regex(/^[A-Za-z0-9:_-]+$/),
});

export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const raw = await ctx.params;

  const parsed = parseParams(raw, idParam);
  if (!parsed.ok) return parsed.response;

  return handle('GET /api/rehearsals/:id', async () => {
    const rehearsal = await getRehearsal(parsed.data.id);
    if (!rehearsal) return notFound(`Rehearsal '${parsed.data.id}'`);
    return json(rehearsal, 10);
  });
}
