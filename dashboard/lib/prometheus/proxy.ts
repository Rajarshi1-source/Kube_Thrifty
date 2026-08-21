/**
 * proxy.ts -- the only path from the dashboard to Prometheus.
 *
 * Everything here is a clamp. The client names a template and supplies label values; this module
 * decides the window, the step, the timeout and the point budget. Nothing the caller sends can
 * widen any of them.
 *
 *   window  <= PROM_MAX_WINDOW_DAYS (default 30d)
 *   step    >= PROM_MIN_STEP_SECONDS (default 30s), and raised further if the point budget demands
 *   timeout  = PROM_TIMEOUT_MS (default 8s), enforced with a real AbortSignal
 *   breaker  = 5 consecutive failures, 30s cooldown
 *
 * The step floor is the important one. `step=1s` over 30 days asks Prometheus for 2.6 million
 * points per series -- enough to exhaust its memory, and far more than any chart can draw.
 */
import { env } from '../env';
import { UpstreamError } from '../problem';
import { CircuitBreaker, withRetry, withTimeout } from '../resilience';
import { cached } from '../cache/client';
import { TTL_PROMQL, promql as promqlKey } from '../cache/keys';
import { METRIC_TEMPLATES, type LabelMatchers, type TemplateId } from './allowlist';

declare global {
  // eslint-disable-next-line no-var
  var __ktPromBreaker: CircuitBreaker | undefined;
}

const breaker = globalThis.__ktPromBreaker ?? new CircuitBreaker('Prometheus', 5, 30_000);
if (env.NODE_ENV !== 'production') globalThis.__ktPromBreaker = breaker;

/**
 * Cap on points per series. Beyond this the step is widened rather than the request refused: a
 * coarser chart is a better answer than a 400, and a 30-day view does not need 15s resolution.
 */
const MAX_POINTS = 1_500;

export interface RangeResult {
  /** value is null where Prometheus reported no sample. A gap, never a zero. */
  points: { t: string; value: number | null }[];
  /** False when the series came back completely empty -- "not observed", not "zero". */
  observed: boolean;
}

interface PromRangeResponse {
  status: 'success' | 'error';
  error?: string;
  data?: {
    resultType: string;
    result: { metric: Record<string, string>; values: [number, string][] }[];
  };
}

/**
 * Run a range query built from an allow-listed template.
 *
 * `templateId` is a key of METRIC_TEMPLATES, so TypeScript rejects an arbitrary string at compile
 * time and the route handler's Zod enum rejects one at runtime.
 */
export async function queryRange(
  templateId: TemplateId,
  matchers: LabelMatchers,
  opts: { windowSeconds: number; stepSeconds: number; resource?: 'cpu' | 'memory' },
): Promise<RangeResult> {
  const windowSeconds = Math.min(opts.windowSeconds, env.PROM_MAX_WINDOW_DAYS * 86_400);

  // Two floors compose with max(): the configured minimum, and whatever the point budget requires.
  const stepFromBudget = Math.ceil(windowSeconds / MAX_POINTS);
  const step = Math.max(opts.stepSeconds, env.PROM_MIN_STEP_SECONDS, stepFromBudget);

  // The rate window must span at least a few scrape intervals, or `rate()` sees one sample and
  // returns nothing. 4x the step is the usual safe multiple.
  const rateWindow = `${Math.max(step * 4, 120)}s`;

  const end = Math.floor(Date.now() / 1000);
  const start = end - windowSeconds;

  const template = METRIC_TEMPLATES[templateId];
  const query =
    templateId === 'requests' || templateId === 'limits'
      ? (template as (m: LabelMatchers, r: 'cpu' | 'memory') => string)(matchers, opts.resource ?? 'cpu')
      : (template as (m: LabelMatchers, rate: string) => string)(matchers, rateWindow);

  // Aligning start/end to the step makes the cache key stable across requests a few seconds apart.
  // Without it, every page refresh is a fresh key and the 5m TTL never gets used.
  const alignedEnd = Math.floor(end / step) * step;
  const alignedStart = Math.floor(start / step) * step;

  const key = await promqlKey(query, String(alignedStart), String(alignedEnd), String(step));

  return cached(key, TTL_PROMQL, async () => {
    const body = await breaker.run(() =>
      // Retried: a range query is a pure read, so a repeat is free of side effects.
      withRetry(
        () =>
          withTimeout(env.PROM_TIMEOUT_MS, 'Prometheus', async (signal) => {
            const url = new URL('/api/v1/query_range', env.PROMETHEUS_URL);
            url.searchParams.set('query', query);
            url.searchParams.set('start', String(alignedStart));
            url.searchParams.set('end', String(alignedEnd));
            url.searchParams.set('step', String(step));

            const res = await fetch(url, {
              signal,
              headers: { accept: 'application/json' },
              // Prometheus data is cached deliberately in Valkey; letting the fetch layer cache it
              // too would add a second, invisible TTL that nothing can invalidate.
              cache: 'no-store',
            });
            if (!res.ok) {
              throw new UpstreamError('Prometheus', `HTTP ${res.status}`);
            }
            return (await res.json()) as PromRangeResponse;
          }),
        { attempts: 2, baseDelayMs: 150 },
      ),
    );

    if (body.status !== 'success' || !body.data) {
      throw new UpstreamError('Prometheus', body.error ?? 'query failed');
    }

    const series = body.data.result[0];
    if (!series || series.values.length === 0) {
      // An empty result is "NOT OBSERVED". It is emphatically not a series of zeros, and the caller
      // must be able to tell the difference -- an empty PSI series downgrades the evidence tier.
      return { points: [], observed: false };
    }

    // Densify onto the step grid so a scrape gap becomes an explicit null rather than two adjacent
    // points that Recharts would join with a straight line through missing data.
    const byBucket = new Map<number, number | null>();
    for (const [ts, raw] of series.values) {
      const parsed = Number(raw);
      byBucket.set(Math.floor(ts / step) * step, Number.isFinite(parsed) ? parsed : null);
    }

    const points: { t: string; value: number | null }[] = [];
    for (let ts = alignedStart; ts <= alignedEnd; ts += step) {
      points.push({
        t: new Date(ts * 1000).toISOString(),
        value: byBucket.get(ts) ?? null,
      });
    }

    return { points, observed: true };
  });
}

export async function promHealthy(): Promise<boolean> {
  try {
    return await withTimeout(2_000, 'Prometheus', async (signal) => {
      const res = await fetch(new URL('/-/healthy', env.PROMETHEUS_URL), {
        signal,
        cache: 'no-store',
      });
      return res.ok;
    });
  } catch {
    return false;
  }
}

export const promBreakerStatus = () => breaker.status;
