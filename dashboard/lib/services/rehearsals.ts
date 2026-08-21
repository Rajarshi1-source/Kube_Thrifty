/**
 * rehearsals.ts -- read the record of live-pod experiments.
 *
 * Read-only, like everything else in this API. There is no `startRehearsal` here and there never
 * will be: a rehearsal mutates a live pod via `patch pods/resize`, and that capability belongs to
 * the analyser, which holds the RBAC, writes the compensation row before touching anything, and has
 * a watchdog covering it. Exposing a "rehearse" button would mean the dashboard needed cluster
 * credentials, which is precisely the property this design refuses to give up.
 *
 * `rehearsedFloor` is surfaced only for a `safe` outcome. The database CHECK enforces it too, but
 * the mapper repeats the rule so a future migration cannot quietly leak an unverified floor into
 * the UI wearing a verified badge.
 */
import { query } from '../db';
import { env } from '../env';
import type { Rehearsal } from '../schemas';
import type { z } from 'zod';
import type { rehearsalsQuery } from '../schemas';

interface Row {
  rehearsal_id: string;
  run_id: string | null;
  namespace: string;
  workload: string;
  pod: string;
  container: string;
  outcome: Rehearsal['outcome'];
  original_requests: Record<string, number>;
  original_limits: Record<string, number>;
  candidate_requests: Record<string, number>;
  candidate_limits: Record<string, number>;
  rehearsed_floor: string | null;
  baseline_signals: Record<string, unknown> | null;
  observed_signals: Record<string, unknown> | null;
  trip_reasons: string[] | null;
  revert_deadline: Date;
  reverted_at: Date | null;
  started_at: Date;
  finished_at: Date | null;
}

function toRehearsal(r: Row): Rehearsal {
  return {
    rehearsalId: r.rehearsal_id,
    runId: r.run_id,
    namespace: r.namespace,
    workload: r.workload,
    pod: r.pod,
    container: r.container,
    outcome: r.outcome,
    originalRequests: r.original_requests ?? {},
    candidateRequests: r.candidate_requests ?? {},
    // Belt and braces with the schema CHECK. An inconclusive or timed-out rehearsal is NOT safe,
    // and must never contribute a floor.
    rehearsedFloor: r.outcome === 'safe' && r.rehearsed_floor !== null ? Number(r.rehearsed_floor) : null,
    baselineSignals: r.baseline_signals,
    observedSignals: r.observed_signals,
    tripReasons: r.trip_reasons ?? [],
    revertDeadline: r.revert_deadline.toISOString(),
    revertedAt: r.reverted_at?.toISOString() ?? null,
    startedAt: r.started_at.toISOString(),
    finishedAt: r.finished_at?.toISOString() ?? null,
  };
}

export async function listRehearsals(
  filters: z.infer<typeof rehearsalsQuery>,
): Promise<Rehearsal[]> {
  const params: unknown[] = [env.CLUSTER_NAME];
  const conditions: string[] = ['c.name = $1'];

  if (filters.namespace) {
    params.push(filters.namespace);
    conditions.push(`r.namespace = $${params.length}`);
  }
  if (filters.workload) {
    params.push(filters.workload);
    conditions.push(`r.workload = $${params.length}`);
  }
  if (filters.outcome) {
    params.push(filters.outcome);
    conditions.push(`r.outcome = $${params.length}::rehearsal_outcome`);
  }

  params.push(filters.limit);

  const rows = await query<Row>(
    `SELECT r.* FROM rehearsals r
       JOIN clusters c ON c.id = r.cluster_id
      WHERE ${conditions.join(' AND ')}
      ORDER BY r.started_at DESC
      LIMIT $${params.length}`,
    params,
  );
  return rows.map(toRehearsal);
}

export async function getRehearsal(rehearsalId: string): Promise<Rehearsal | null> {
  const rows = await query<Row>(
    `SELECT r.* FROM rehearsals r
       JOIN clusters c ON c.id = r.cluster_id
      WHERE c.name = $1 AND r.rehearsal_id = $2`,
    [env.CLUSTER_NAME, rehearsalId],
  );
  const row = rows[0];
  return row ? toRehearsal(row) : null;
}

/**
 * Rehearsals still running past their revert deadline.
 *
 * Surfaced in the UI as a warning, because it means the watchdog has work to do -- or, worse, that
 * the watchdog is not running. A pod left on experimental limits with nothing watching it is the
 * one failure mode this whole subsystem is built to prevent, so it must be visible rather than
 * merely logged.
 */
export async function overdueRehearsals(): Promise<Rehearsal[]> {
  const rows = await query<Row>(
    `SELECT r.* FROM rehearsals r
       JOIN clusters c ON c.id = r.cluster_id
      WHERE c.name = $1 AND r.outcome = 'running' AND r.revert_deadline < now()
      ORDER BY r.revert_deadline`,
    [env.CLUSTER_NAME],
  );
  return rows.map(toRehearsal);
}
