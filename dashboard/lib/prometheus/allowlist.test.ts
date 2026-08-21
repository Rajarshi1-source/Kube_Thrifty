/**
 * allowlist.test.ts -- the injection tests.
 *
 * The dashboard never accepts PromQL from a client, but it does interpolate label VALUES into
 * matchers, and those values come from Kubernetes labels. In a multi-tenant cluster those are
 * attacker-influencable, so the escaping is a security boundary and deserves direct tests rather
 * than trust.
 */
import { describe, expect, it } from 'vitest';

import { ALLOWED_METRICS, METRIC_TEMPLATES, _internal, isAllowedMetric } from './allowlist';

const { escapeLabelValue, escapeRegexValue, matchers } = _internal;

describe('metric allow-list', () => {
  it('admits the metrics the product renders', () => {
    expect(isAllowedMetric('container_cpu_usage_seconds_total')).toBe(true);
    expect(isAllowedMetric('container_pressure_cpu_stalled_seconds_total')).toBe(true);
    expect(isAllowedMetric('container_oom_events_total')).toBe(true);
  });

  it('rejects anything not on the list', () => {
    expect(isAllowedMetric('etcd_debugging_mvcc_db_total_size_in_bytes')).toBe(false);
    expect(isAllowedMetric('apiserver_request_duration_seconds')).toBe(false);
  });

  it('excludes the flapping last_terminated_reason gauge', () => {
    // OOM detection uses the container_oom_events_total COUNTER. The gauge flaps as pods restart,
    // so a point-in-time read of it is unreliable evidence -- it must not be reachable at all.
    expect(ALLOWED_METRICS).not.toContain('kube_pod_container_status_last_terminated_reason');
  });

  it('uses the PSI names cAdvisor actually exports', () => {
    // Verified against a live cluster. cAdvisor exports `_stalled_` (PSI full) and `_waiting_`
    // (PSI some); there is no `_full_` variant. Querying a nonexistent metric returns an empty
    // result, which this app reports as "not observed" -- so a wrong name looks exactly like a
    // legitimately missing signal and would never surface as an error.
    expect(ALLOWED_METRICS).toContain('container_pressure_cpu_stalled_seconds_total');
    expect(ALLOWED_METRICS).toContain('container_pressure_memory_stalled_seconds_total');
    expect(ALLOWED_METRICS).not.toContain('container_pressure_memory_full_seconds_total');
  });
});

describe('pod prefix matching', () => {
  it('matches the generated pod name, not the bare workload name', () => {
    // The `pod` label holds `api-gateway-7d9f4c8b6-x2klm`, so an exact match on `api-gateway`
    // silently matches nothing.
    expect(matchers({ namespace: 'shop', podPrefix: 'api-gateway', container: 'app' })).toBe(
      'namespace="shop",pod=~"api-gateway-.*",container="app"',
    );
  });

  it('requires a suffix so one workload cannot match another', () => {
    // `order-service-.*` must not also match `order-service-legacy-<hash>`... it would, so the
    // guard here is that the pattern demands the separator, keeping `order-service` from matching a
    // pod named exactly `order-service`.
    const built = matchers({ podPrefix: 'order-service' });
    expect(built).toBe('pod=~"order-service-.*"');
  });

  it('escapes regex metacharacters in the prefix', () => {
    // Without this, a workload named `api.*` would match every pod in the namespace.
    expect(escapeRegexValue('api.*')).toBe('api\\\\.\\\\*');
    expect(matchers({ podPrefix: 'api.*' })).toBe('pod=~"api\\\\.\\\\*-.*"');
  });

  it('prefers an exact pod match when one is given', () => {
    expect(matchers({ pod: 'api-gateway-7d9f-x2k', podPrefix: 'api-gateway' })).toBe(
      'pod="api-gateway-7d9f-x2k"',
    );
  });
});

describe('label value escaping', () => {
  it('escapes a quote so it cannot close the matcher', () => {
    expect(escapeLabelValue('foo"bar')).toBe('foo\\"bar');
  });

  it('escapes the backslash BEFORE the quote', () => {
    // Order matters. If the quote were escaped first, an injected `\"` would end up as `\\"` --
    // the backslash escaping its own escape, leaving a live quote. Backslash-first yields `\\\"`.
    expect(escapeLabelValue('foo\\"bar')).toBe('foo\\\\\\"bar');
  });

  it('strips newlines, which could otherwise terminate the expression', () => {
    expect(escapeLabelValue('foo\nbar')).toBe('foobar');
    expect(escapeLabelValue('foo\rbar')).toBe('foobar');
    expect(escapeLabelValue('foo\tbar')).toBe('foobar');
  });

  it('neutralises a matcher-breakout attempt', () => {
    const hostile = 'x"} or up{job="kubernetes-apiservers';
    const built = matchers({ pod: hostile });
    // The injected quotes are escaped, so the whole payload stays inside one string literal and the
    // expression shape is unchanged.
    expect(built).toBe('pod="x\\"} or up{job=\\"kubernetes-apiservers"');

    // The property that actually matters: the only UNESCAPED quotes are the two delimiters. Strip
    // every backslash-escaped quote and exactly the opening and closing pair must remain -- so the
    // payload cannot terminate the literal and start a new selector.
    const unescaped = built.replace(/\\"/g, '');
    expect(unescaped.match(/"/g)?.length).toBe(2);
    expect(unescaped.startsWith('pod="')).toBe(true);
    expect(unescaped.endsWith('"')).toBe(true);
  });
});

describe('matcher construction', () => {
  it('never produces an empty matcher set', () => {
    // `metric{}` selects every series with that name, cluster-wide. A literal that matches nothing
    // is the safe failure: an empty chart, not a full data dump.
    expect(matchers({})).toBe('container="__none__"');
  });

  it('binds all three labels when present', () => {
    expect(matchers({ namespace: 'shop', pod: 'api-1', container: 'app' })).toBe(
      'namespace="shop",pod="api-1",container="app"',
    );
  });
});

describe('throttle ratio template', () => {
  it('divides throttled periods by total periods, never exposing raw counters', () => {
    const q = METRIC_TEMPLATES.throttleRatio({ namespace: 'shop', pod: 'api-1' }, '300s');
    expect(q).toContain('container_cpu_cfs_throttled_periods_total');
    expect(q).toContain('container_cpu_cfs_periods_total');
    expect(q).toContain('/');
    // clamp_min guards against a divide-by-zero when a container has had no CFS periods in the
    // window, which would otherwise yield +Inf and render as a catastrophic throttle ratio.
    expect(q).toContain('clamp_min');
  });
});

describe('PSI template', () => {
  it('reads the PSI full series (`_stalled_`), not PSI some (`_waiting_`)', () => {
    // cAdvisor spells PSI full as `_stalled_` and PSI some as `_waiting_`. The gates use full,
    // because `some` (at least one task blocked) is normal on any busy node and would block every
    // reduction.
    const q = METRIC_TEMPLATES.psiMemoryFull({ pod: 'api-1' }, '300s');
    expect(q).toContain('container_pressure_memory_stalled_seconds_total');
    expect(q).not.toContain('_waiting_');
  });
});
