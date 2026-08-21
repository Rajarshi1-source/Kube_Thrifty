/**
 * analysis.ts -- run history, and the one write.
 *
 * `triggerAnalysis` XADDs a job and returns immediately with the run id. It does NOT wait for the
 * analysis, and that is the design: the analyser sits at `minReplicaCount: 0` behind a KEDA
 * consumer-group scaler, so a synchronous API would block on a cold start and then on a multi-minute
 * run. The caller gets `202 Accepted` and polls `/api/analysis/:id`.
 *
 * Note what the trigger cannot do. It cannot request a PR, cannot request a rehearsal, cannot name a
 * resource value. It asks for an ANALYSIS. Everything consequential is the analyser's decision under
 * its own configuration, guarded by its own lock.
 */
import { query } from '../db';
import { enqueueAnalysis } from '../cache/client';
import { env } from '../env';
import type { AnalysisRun } from '../schemas';

interface Row {
  run_id: string;
  status: AnalysisRun['status'];
  window_spec: string;
  started_at: Date;
  finished_at: Date | null;
  workloads_seen: number;
  recommendations_made: number;
  pr_url: string | null;
  degraded_reason: string | null;
  error: string | null;
}

const toRun = (r: Row): AnalysisRun => ({
  runId: r.run_id,
  status: r.status,
  windowSpec: r.window_spec,
  startedAt: r.started_at.toISOString(),
  finishedAt: r.finished_at?.toISOString() ?? null,
  workloadsSeen: r.workloads_seen,
  recommendationsMade: r.recommendations_made,
  prUrl: r.pr_url,
  // Surfaced, not swallowed. 'degraded' means the run completed with a signal missing, so its
  // recommendations carry a lower evidence tier -- exactly what a reviewer needs to know.
  degradedReason: r.degraded_reason,
  error: r.error,
});

export async function listRuns(limit: number): Promise<AnalysisRun[]> {
  const rows = await query<Row>(
    `SELECT a.* FROM analysis_runs a
       JOIN clusters c ON c.id = a.cluster_id
      WHERE c.name = $1
      ORDER BY a.started_at DESC
      LIMIT $2`,
    [env.CLUSTER_NAME, limit],
  );
  return rows.map(toRun);
}

export async function getRun(runId: string): Promise<AnalysisRun | null> {
  const rows = await query<Row>(
    `SELECT a.* FROM analysis_runs a
       JOIN clusters c ON c.id = a.cluster_id
      WHERE c.name = $1 AND a.run_id = $2`,
    [env.CLUSTER_NAME, runId],
  );
  const row = rows[0];
  return row ? toRun(row) : null;
}

/**
 * Enqueue an analysis. Returns the run id to hand straight back as `202 { runId }`.
 *
 * There is deliberately no client-side concurrency check here. The analyser holds the authoritative
 * `SET NX` lock, and duplicating that decision in the dashboard would be a check with a race window
 * that disagrees with the real one. Queueing two jobs is harmless: the second worker finds the lock
 * held and declines.
 */
export async function triggerAnalysis(input: {
  window: string;
  namespaces?: string[];
}): Promise<{ runId: string }> {
  const runId = await enqueueAnalysis({
    cluster: env.CLUSTER_NAME,
    window: input.window,
    ...(input.namespaces ? { namespaces: input.namespaces } : {}),
    trigger: 'api',
  });
  return { runId };
}
