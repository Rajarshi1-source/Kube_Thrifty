/**
 * health.ts -- liveness and readiness.
 *
 * The distinction is operational, not cosmetic, and getting it backwards causes outages:
 *
 *   LIVE   the process is running. Never depends on a dependency. If liveness failed because
 *          Prometheus was down, Kubernetes would RESTART this pod -- which cannot possibly fix
 *          Prometheus, and turns one dependency outage into a crash-loop.
 *   READY  the process can serve useful traffic. Requires the database, because every meaningful
 *          endpoint reads it. Prometheus and the cache are reported but NOT required: charts
 *          degrade to "not observed" and reads fall through to Postgres, which is a reduced
 *          service rather than no service.
 */
import { dbBreakerStatus, dbHealthy } from '../db';
import { cacheHealthy, queueDepth } from '../cache/client';
import { promBreakerStatus, promHealthy } from '../prometheus/proxy';
import { overdueRehearsals } from './rehearsals';

export interface HealthReport {
  status: 'ok' | 'degraded' | 'unhealthy';
  checks: {
    database: { ok: boolean; required: true; breaker: string };
    prometheus: { ok: boolean; required: false; breaker: string };
    cache: { ok: boolean; required: false };
  };
  /** null means unknown, not empty. */
  queueDepth: number | null;
  /**
   * Rehearsals past their revert deadline. Non-zero means the watchdog has work outstanding -- or
   * is not running, which is the serious case: a live pod left on experimental limits.
   */
  overdueRehearsals: number;
  warnings: string[];
  checkedAt: string;
}

export async function readiness(): Promise<HealthReport> {
  const [db, prom, cache, depth, overdue] = await Promise.all([
    dbHealthy(),
    promHealthy(),
    cacheHealthy(),
    queueDepth(),
    overdueRehearsals().catch(() => []),
  ]);

  const warnings: string[] = [];
  if (!prom) {
    warnings.push(
      'Prometheus is unreachable: metric and pressure charts will render as "not observed". ' +
        'Stored recommendations are unaffected.',
    );
  }
  if (!cache) {
    warnings.push('Cache unavailable: responses are served uncached and will be slower.');
  }
  if (overdue.length > 0) {
    warnings.push(
      `${overdue.length} rehearsal(s) are past their revert deadline. Verify the watchdog CronJob ` +
        `is running -- a pod may be sitting on experimental resource limits.`,
    );
  }

  return {
    // Only the database can make this unhealthy. A missing optional dependency is `degraded`, which
    // keeps the pod in service instead of pulling it out and shifting load onto its siblings.
    status: !db ? 'unhealthy' : warnings.length > 0 ? 'degraded' : 'ok',
    checks: {
      database: { ok: db, required: true, breaker: dbBreakerStatus().state },
      prometheus: { ok: prom, required: false, breaker: promBreakerStatus().state },
      cache: { ok: cache, required: false },
    },
    queueDepth: depth,
    overdueRehearsals: overdue.length,
    warnings,
    checkedAt: new Date().toISOString(),
  };
}

/** Liveness. Deliberately dependency-free -- see the note at the top of this file. */
export function liveness(): { status: 'ok'; checkedAt: string } {
  return { status: 'ok', checkedAt: new Date().toISOString() };
}
