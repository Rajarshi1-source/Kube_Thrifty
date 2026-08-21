/**
 * pods.ts -- per-pod metric and pressure series.
 *
 * The pressure path is where this product either tells the truth or quietly lies. An empty
 * `container_pressure_*` series means PSI is NOT BEING COLLECTED. It does not mean there was no
 * pressure. So `getPressure` returns `observed: false` plus a human-readable
 * `unavailableReason`, and the UI renders "not observed" rather than a reassuring flat line at
 * zero.
 *
 * Getting this wrong is worse than showing nothing: a zero-pressure chart is an argument FOR
 * shrinking a workload that may in fact be suffering.
 */
import { queryRange } from '../prometheus/proxy';
import { query } from '../db';
import { cached } from '../cache/client';
import { TTL_POD_METRICS, podMetrics as podMetricsKey } from '../cache/keys';
import { durationToSeconds, type PodMetrics, type PressureSeries, type SeriesPoint } from '../schemas';
import { env } from '../env';

/** Resolve a pod's namespace and container from what the analyser has already recorded. */
async function resolvePod(
  pod: string,
): Promise<{ namespace: string; container: string } | null> {
  // `pod LIKE workload%` because a ReplicaSet pod name is the workload name plus two hash suffixes,
  // and the analyser stores recommendations per workload, not per ephemeral pod.
  const rows = await query<{ namespace: string; container: string }>(
    `SELECT DISTINCT r.namespace, r.container
       FROM recommendations r
       JOIN clusters c ON c.id = r.cluster_id
      WHERE c.name = $1 AND $2 LIKE r.workload || '%'
      LIMIT 1`,
    [env.CLUSTER_NAME, pod],
  );
  return rows[0] ?? null;
}

export async function getPodMetrics(
  pod: string,
  opts: { window: string; resource: 'cpu' | 'memory'; step: number },
): Promise<PodMetrics | null> {
  const resolved = await resolvePod(pod);
  if (!resolved) return null;

  const { namespace, container } = resolved;
  const windowSeconds = durationToSeconds(opts.window);
  // `podPrefix`, not `pod`: the route parameter is a WORKLOAD name, and the `pod` label holds the
  // full generated pod name (`api-gateway-7d9f4c8b6-x2klm`). An exact match matches nothing and
  // would be reported as "not observed" -- a wrong answer wearing a legitimate one's clothes.
  const matchers = { namespace, podPrefix: pod, container };

  return cached(`${podMetricsKey(pod, opts.window)}:${opts.resource}`, TTL_POD_METRICS, async () => {
    const [usage, request, limit] = await Promise.all([
      queryRange(opts.resource === 'cpu' ? 'cpuUsage' : 'memoryWorkingSet', matchers, {
        windowSeconds,
        stepSeconds: opts.step,
      }),
      queryRange('requests', matchers, {
        windowSeconds,
        stepSeconds: opts.step,
        resource: opts.resource,
      }),
      queryRange('limits', matchers, {
        windowSeconds,
        stepSeconds: opts.step,
        resource: opts.resource,
      }),
    ]);

    // The declared request/limit is a step function, so the last non-null point is the current
    // value. `lastValue` returns null for an empty series rather than 0 -- a pod with no declared
    // limit has an UNKNOWN limit, and rendering that as a limit of zero would invert its meaning.
    const stats = await sizingStats(namespace, pod, container, opts.resource);

    return {
      pod,
      container,
      resource: opts.resource,
      window: opts.window,
      usage: usage.points,
      request: lastValue(request.points),
      limit: lastValue(limit.points),
      peak: stats?.peak ?? null,
      p95: stats?.p95 ?? null,
      p99: stats?.p99 ?? null,
      cgroupTruth: stats?.cgroupTruth ?? false,
    } satisfies PodMetrics;
  });
}

function lastValue(points: SeriesPoint[]): number | null {
  for (let i = points.length - 1; i >= 0; i--) {
    const v = points[i]?.value;
    if (v !== null && v !== undefined) return v;
  }
  return null;
}

/**
 * The sizing statistics the analyser actually used, read back from its own recommendation row.
 *
 * Deliberately NOT recomputed from Prometheus here. The chart must show the numbers the decision
 * was made from; recomputing would quietly drift as Prometheus retention rolls over, and the
 * reference line on the chart would stop matching the recommendation it is supposed to explain.
 */
async function sizingStats(
  namespace: string,
  pod: string,
  container: string,
  resource: 'cpu' | 'memory',
): Promise<{ peak: number | null; p95: number | null; p99: number | null; cgroupTruth: boolean } | null> {
  const rows = await query<{
    observed_peak: string | null;
    observed_p95: string | null;
    observed_p99: string | null;
    sizing_basis: string | null;
  }>(
    `SELECT r.observed_peak, r.observed_p95, r.observed_p99, r.sizing_basis
       FROM recommendations r
       JOIN clusters c ON c.id = r.cluster_id
      WHERE c.name = $1 AND r.namespace = $2 AND $3 LIKE r.workload || '%'
        AND r.container = $4 AND r.resource = $5
      ORDER BY r.created_at DESC
      LIMIT 1`,
    [env.CLUSTER_NAME, namespace, pod, container, resource],
  );
  const row = rows[0];
  if (!row) return null;

  // UNIT BRIDGE. The analyser stores memory in MiB (it divides by 1024^2 before persisting), while
  // Prometheus returns bytes. Without this conversion the memory chart draws its peak reference line
  // at ~195 *bytes* against a usage series in the hundreds of millions -- a line pinned to the
  // x-axis, which reads as "no peak" rather than as a unit error.
  //
  // Bytes is the API's memory unit, matching Prometheus and `formatBytes`.
  const scale = resource === 'memory' ? 1024 * 1024 : 1;
  const n = (v: string | null) => (v === null ? null : Number(v) * scale);
  return {
    peak: n(row.observed_peak),
    p95: n(row.observed_p95),
    p99: n(row.observed_p99),
    // Only the cgroup DaemonSet's kernel high-water mark counts as cgroup truth. A scraped
    // working-set gauge is weaker evidence and must not claim otherwise.
    cgroupTruth: row.sizing_basis === 'cgroup_memory_peak',
  };
}

export async function getPressure(
  pod: string,
  opts: { window: string; step: number },
): Promise<PressureSeries | null> {
  const resolved = await resolvePod(pod);
  if (!resolved) return null;

  const { namespace, container } = resolved;
  const windowSeconds = durationToSeconds(opts.window);
  const matchers = { namespace, podPrefix: pod, container };

  const [psiCpu, psiMem, throttle] = await Promise.all([
    queryRange('psiCpuFull', matchers, { windowSeconds, stepSeconds: opts.step }),
    queryRange('psiMemoryFull', matchers, { windowSeconds, stepSeconds: opts.step }),
    queryRange('throttleRatio', matchers, { windowSeconds, stepSeconds: opts.step }),
  ]);

  // PSI observed-ness is judged on the PSI series alone. The throttle ratio is derived from CFS
  // counters, which exist on every cgroup v2 kernel; treating its presence as evidence that PSI
  // works would let a `partial` tier masquerade as `modelled`.
  const psiObserved = psiCpu.observed || psiMem.observed;

  return {
    pod,
    container,
    window: opts.window,
    psiCpuFull: psiCpu.points,
    psiMemFull: psiMem.points,
    throttleRatio: throttle.points,
    observed: psiObserved,
    unavailableReason: psiObserved
      ? null
      : 'No container_pressure_* series for this container. PSI requires a cgroup v2 kernel with ' +
        'psi=1 and a kubelet cAdvisor scrape. Recommendations for this workload carry the ' +
        '`partial` evidence tier.',
  } satisfies PressureSeries;
}
