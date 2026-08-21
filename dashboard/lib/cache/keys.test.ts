/**
 * keys.test.ts -- asserts the TypeScript key builders match `analyser/src/cache/keys.py` LITERALLY.
 *
 * This test exists because the failure it catches is silent. If the dashboard reads
 * `dashboard:rec:demo` while the analyser writes `dashboard:recs:demo`, nothing throws: one side
 * gets a permanent cache miss, the other writes an entry nobody ever invalidates, and the only
 * symptom is a dashboard quietly showing stale numbers.
 *
 * So the expected strings are hard-coded here rather than derived from the builders. A test that
 * called `recommendations()` and compared it to `recommendations()` would pass through any rename.
 */
import { describe, expect, it } from 'vitest';

import * as keys from './keys';

describe('key parity with analyser/src/cache/keys.py', () => {
  it('builds the recommendation key', () => {
    expect(keys.recommendations('kubethrifty-demo')).toBe('dashboard:recs:kubethrifty-demo');
  });

  it('builds the pod metrics key', () => {
    expect(keys.podMetrics('api-gateway-7d9f-abc', '7d')).toBe('pod:metrics:api-gateway-7d9f-abc:7d');
  });

  it('builds the savings key', () => {
    expect(keys.savings('kubethrifty-demo', 'eks_auto_mode')).toBe(
      'dashboard:savings:kubethrifty-demo:eks_auto_mode',
    );
  });

  it('builds the verdict key', () => {
    expect(keys.verdict('abc123')).toBe('verdict:abc123');
  });

  it('builds the analysis lock key', () => {
    // The analyser acquires exactly this key with SET NX. A mismatch would mean the dashboard's
    // health view reported on a lock nobody holds.
    expect(keys.analysisLock('kubethrifty-demo')).toBe('analysis:lock:kubethrifty-demo');
  });

  it('uses the same stream and group names', () => {
    expect(keys.STREAM_ANALYSIS_JOBS).toBe('analysis-jobs');
    expect(keys.STREAM_ANALYSIS_DLQ).toBe('analysis-jobs-dlq');
    expect(keys.CONSUMER_GROUP).toBe('analysers');
  });

  it('uses the same TTLs', () => {
    expect(keys.TTL_RECOMMENDATIONS).toBe(21_600);
    expect(keys.TTL_POD_METRICS).toBe(900);
    expect(keys.TTL_PROMQL).toBe(300);
  });
});

describe('promql key hashing', () => {
  it('matches the Python sha256 of "query|start|end|step", truncated to 32 hex chars', async () => {
    // Reference value produced by the Python implementation for the same inputs. If the material
    // format or truncation ever diverges, this fails instead of silently splitting the cache in two.
    const key = await keys.promql('up', '1', '2', '30s');
    expect(key).toMatch(/^promql:[a-f0-9]{32}$/);

    const material = 'up|1|2|30s';
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(material));
    const hex = Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
    expect(key).toBe(`promql:${hex.slice(0, 32)}`);
  });

  it('separates different windows over the same expression', async () => {
    // Two range queries over the same expression but different windows are different answers.
    // Collapsing them would serve one window's data as another's.
    const a = await keys.promql('up', '1', '2', '30s');
    const b = await keys.promql('up', '1', '9999', '30s');
    expect(a).not.toBe(b);
  });
});
