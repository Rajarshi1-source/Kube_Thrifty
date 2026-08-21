/**
 * verdicts.ts -- ThriftDetective output.
 *
 * `bundleSha` is the identity, not an incidental column: it is the sha256 of the evidence bundle's
 * canonical JSON, so a verdict is reproducible offline via `thriftctl replay <sha>`. The UI shows
 * it for exactly that reason -- a verdict you cannot re-derive is an opinion.
 *
 * `rulesetVersion` travels with every row because a confidence value is only meaningful next to the
 * ruleset it was calibrated under. Rendering 0.82 without saying which rules produced it invites
 * comparison across incompatible calibrations.
 */
import { query } from '../db';
import { env } from '../env';
import type { Verdict } from '../schemas';
import type { z } from 'zod';
import type { verdictsQuery } from '../schemas';

interface Row {
  bundle_sha: string;
  namespace: string;
  workload: string;
  container: string;
  rule_id: string;
  confidence: string;
  summary: string;
  remediation: string;
  evidence: Record<string, unknown>;
  attributed_change: Record<string, unknown> | null;
  ruleset_version: string;
  verdicts_digest: string;
  created_at: Date;
}

const toVerdict = (r: Row): Verdict => ({
  bundleSha: r.bundle_sha,
  namespace: r.namespace,
  workload: r.workload,
  container: r.container,
  ruleId: r.rule_id,
  confidence: Number(r.confidence),
  summary: r.summary,
  remediation: r.remediation,
  evidence: r.evidence ?? {},
  attributedChange: r.attributed_change,
  rulesetVersion: r.ruleset_version,
  verdictsDigest: r.verdicts_digest,
  createdAt: r.created_at.toISOString(),
});

export async function listVerdicts(filters: z.infer<typeof verdictsQuery>): Promise<Verdict[]> {
  const params: unknown[] = [env.CLUSTER_NAME];
  const conditions: string[] = ['c.name = $1'];

  if (filters.namespace) {
    params.push(filters.namespace);
    conditions.push(`v.namespace = $${params.length}`);
  }
  if (filters.workload) {
    params.push(filters.workload);
    conditions.push(`v.workload = $${params.length}`);
  }
  if (filters.ruleId) {
    params.push(filters.ruleId);
    conditions.push(`v.rule_id = $${params.length}`);
  }

  params.push(filters.limit);

  const rows = await query<Row>(
    `SELECT v.* FROM verdicts v
       JOIN clusters c ON c.id = v.cluster_id
      WHERE ${conditions.join(' AND ')}
      ORDER BY v.created_at DESC, v.confidence DESC
      LIMIT $${params.length}`,
    params,
  );
  return rows.map(toVerdict);
}

/**
 * Every verdict for one evidence bundle, ranked by confidence.
 *
 * A bundle can yield several verdicts -- the rules are not mutually exclusive, and a real incident
 * often trips more than one. Returning them all, ordered, is what lets the UI show the leading
 * explanation without discarding the alternatives a human might prefer.
 */
export async function getVerdictsByBundle(bundleSha: string): Promise<Verdict[]> {
  const rows = await query<Row>(
    `SELECT v.* FROM verdicts v
       JOIN clusters c ON c.id = v.cluster_id
      WHERE c.name = $1 AND v.bundle_sha = $2
      ORDER BY v.confidence DESC`,
    [env.CLUSTER_NAME, bundleSha],
  );
  return rows.map(toVerdict);
}

/**
 * Verdicts attributed to one of KubeThrifty's own merged changes.
 *
 * The product's sharpest claim, and the one that has to be easiest to audit: if our own
 * right-sizing PR caused a regression, we say so, unprompted.
 */
export async function selfAttributedVerdicts(limit = 20): Promise<Verdict[]> {
  const rows = await query<Row>(
    `SELECT v.* FROM verdicts v
       JOIN clusters c ON c.id = v.cluster_id
      WHERE c.name = $1 AND v.attributed_change IS NOT NULL
      ORDER BY v.created_at DESC
      LIMIT $2`,
    [env.CLUSTER_NAME, limit],
  );
  return rows.map(toVerdict);
}
