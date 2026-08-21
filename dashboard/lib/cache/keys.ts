/**
 * keys.ts -- the TypeScript mirror of `analyser/src/cache/keys.py`.
 *
 * These two files MUST agree. The analyser writes `dashboard:recs:{clusterId}` and invalidates it
 * after a run; this process reads it. A divergence does not raise an error anywhere -- it produces
 * a permanent cache miss on one side and a stale entry that is never invalidated on the other, and
 * the only symptom is a dashboard that quietly shows old numbers.
 *
 * Because the failure is silent, `lib/cache/keys.test.ts` asserts these strings literally rather
 * than trusting that a reviewer will check both files.
 */

// TTLs in seconds, matching the Python side exactly.
export const TTL_RECOMMENDATIONS = 6 * 60 * 60;
export const TTL_POD_METRICS = 15 * 60;
export const TTL_PROMQL = 5 * 60;
export const TTL_SAVINGS = 60 * 60;
export const TTL_VERDICT = 24 * 60 * 60;

export const STREAM_ANALYSIS_JOBS = 'analysis-jobs';
export const STREAM_ANALYSIS_DLQ = 'analysis-jobs-dlq';
export const CONSUMER_GROUP = 'analysers';

export const recommendations = (clusterId: string) => `dashboard:recs:${clusterId}`;
export const podMetrics = (pod: string, window: string) => `pod:metrics:${pod}:${window}`;
export const savings = (clusterId: string, scenario: string) =>
  `dashboard:savings:${clusterId}:${scenario}`;
export const verdict = (bundleSha: string) => `verdict:${bundleSha}`;
export const analysisLock = (cluster: string) => `analysis:lock:${cluster}`;

/**
 * Hash the PromQL expression rather than embedding it, matching the Python `promql()` helper:
 * sha256 of `query|start|end|step`, truncated to 32 hex characters.
 *
 * start/end/step are part of the identity because two range queries over the same expression but
 * different windows are different answers. Collapsing them would serve one window's data as
 * another's.
 */
export async function promql(
  query: string,
  start = '',
  end = '',
  step = '',
): Promise<string> {
  const material = `${query}|${start}|${end}|${step}`;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(material));
  const hex = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return `promql:${hex.slice(0, 32)}`;
}
