/**
 * allowlist.ts -- which metrics the dashboard may ask Prometheus for, and how a query is built.
 *
 * The threat this file exists to close: a proxy that forwards a client-supplied `query` parameter to
 * Prometheus is a server-side request forgery primitive AND an unbounded read of every metric the
 * cluster collects. Prometheus has no per-metric authorisation, so anything the scrape can see, the
 * caller could exfiltrate -- including metrics from other tenants' namespaces.
 *
 * So the client never sends PromQL. It names a template from `METRIC_TEMPLATES`, and the label
 * values are BOUND as escaped literals by `buildQuery`. There is no code path from an HTTP
 * parameter to a raw PromQL expression.
 */

/**
 * The allow-list. Every entry is a metric this product actually renders.
 *
 * `container_pressure_*` is here because PSI is the differentiator, and
 * `container_oom_events_total` is here as a COUNTER -- the flapping
 * `kube_pod_container_status_last_terminated_reason` gauge is deliberately absent.
 */
export const ALLOWED_METRICS = [
  'container_cpu_usage_seconds_total',
  'container_memory_working_set_bytes',
  'container_memory_max_usage_bytes',
  'container_cpu_cfs_throttled_periods_total',
  'container_cpu_cfs_periods_total',
  'container_oom_events_total',
  // cAdvisor's PSI naming, verified against the running cluster. `stalled` is PSI **full** (every
  // task blocked) and `waiting` is PSI **some** (at least one task blocked). There is no
  // `container_pressure_*_full_seconds_total` -- querying that name returns an empty result, which
  // this app would then correctly but uselessly report as "not observed" forever.
  'container_pressure_cpu_stalled_seconds_total',
  'container_pressure_cpu_waiting_seconds_total',
  'container_pressure_memory_stalled_seconds_total',
  'container_pressure_memory_waiting_seconds_total',
  'kube_pod_container_resource_requests',
  'kube_pod_container_resource_limits',
  'kube_pod_info',
  'kube_node_status_allocatable',
  'kube_node_status_capacity',
] as const;

export type AllowedMetric = (typeof ALLOWED_METRICS)[number];

const allowed = new Set<string>(ALLOWED_METRICS);

export function isAllowedMetric(name: string): name is AllowedMetric {
  return allowed.has(name);
}

/**
 * Named query templates. The client picks one of these by id; it never writes PromQL.
 *
 * Each template is a function of already-escaped label literals, so the shape of the expression is
 * fixed at build time and only the label VALUES vary at request time.
 */
export const METRIC_TEMPLATES = {
  cpuUsage: (m: LabelMatchers, rate: string) =>
    `sum(rate(container_cpu_usage_seconds_total{${matchers(m)}}[${rate}]))`,

  memoryWorkingSet: (m: LabelMatchers) =>
    `max(container_memory_working_set_bytes{${matchers(m)}})`,

  /**
   * The throttle RATIO, computed in PromQL so the dashboard never sees raw period counters.
   *
   * Raw counters scale with window length, replica count and CFS period, so comparing them across
   * two windows is meaningless. The ratio is comparable; the counters are not. Computing it here
   * removes the temptation to compare them client-side.
   */
  throttleRatio: (m: LabelMatchers, rate: string) =>
    `sum(rate(container_cpu_cfs_throttled_periods_total{${matchers(m)}}[${rate}])) / ` +
    `clamp_min(sum(rate(container_cpu_cfs_periods_total{${matchers(m)}}[${rate}])), 1)`,

  /**
   * PSI `full` -- EVERY task stalled.
   *
   * cAdvisor spells PSI full as `_stalled_` and PSI some as `_waiting_`. The gates use full, because
   * `some` (at least one task blocked) is normal on any busy node and would block every reduction.
   */
  psiCpuFull: (m: LabelMatchers, rate: string) =>
    `sum(rate(container_pressure_cpu_stalled_seconds_total{${matchers(m)}}[${rate}]))`,

  psiMemoryFull: (m: LabelMatchers, rate: string) =>
    `sum(rate(container_pressure_memory_stalled_seconds_total{${matchers(m)}}[${rate}]))`,

  /** OOM by counter. `increase()` over the window, never a point-in-time gauge read. */
  oomEvents: (m: LabelMatchers, rate: string) =>
    `sum(increase(container_oom_events_total{${matchers(m)}}[${rate}]))`,

  requests: (m: LabelMatchers, resource: 'cpu' | 'memory') =>
    `max(kube_pod_container_resource_requests{${matchers(m)},resource="${resource}"})`,

  limits: (m: LabelMatchers, resource: 'cpu' | 'memory') =>
    `max(kube_pod_container_resource_limits{${matchers(m)},resource="${resource}"})`,
} as const;

export type TemplateId = keyof typeof METRIC_TEMPLATES;

export interface LabelMatchers {
  namespace?: string;
  /** Exact pod name. */
  pod?: string;
  /**
   * Workload name, matched as a PREFIX against the pod label.
   *
   * Necessary because the `pod` label carries the full generated name --
   * `api-gateway-7d9f4c8b6-x2klm`, not `api-gateway`. An exact match on the workload name silently
   * matches nothing, which this app then reports as "not observed": a wrong answer that looks like
   * a legitimate missing signal, and therefore the worst kind of bug here.
   */
  podPrefix?: string;
  container?: string;
}

/**
 * Escape a label value for a PromQL string literal.
 *
 * Backslash FIRST, then the quote -- reversing the order would let an injected `\"` survive: the
 * quote's escape backslash would itself be escaped by the later pass and cancel out. Control
 * characters go too, since a newline inside a matcher can terminate the expression.
 */
function escapeLabelValue(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    // eslint-disable-next-line no-control-regex
    .replace(/[\n\r\t\u0000-\u001f]/g, '');
}

/**
 * Escape a value destined for a REGEX matcher (`=~`).
 *
 * Separate from `escapeLabelValue` because `=~` interprets its operand as an RE2 pattern, so on top
 * of the string escaping every regex metacharacter has to be neutralised too. Without this, a
 * workload literally named `api.*` would match every pod in the namespace.
 */
function escapeRegexValue(value: string): string {
  return escapeLabelValue(value).replace(/[.*+?^${}()|[\]\\]/g, '\\\\$&');
}

function matchers(m: LabelMatchers): string {
  const parts: string[] = [];
  if (m.namespace) parts.push(`namespace="${escapeLabelValue(m.namespace)}"`);
  if (m.pod) {
    parts.push(`pod="${escapeLabelValue(m.pod)}"`);
  } else if (m.podPrefix) {
    // Anchored at the front and closed with the ReplicaSet/StatefulSet suffix shape, so
    // `order-service` cannot also match `order-service-legacy`. Prometheus anchors `=~` fully, so
    // the trailing `.*` is required for the suffix.
    parts.push(`pod=~"${escapeRegexValue(m.podPrefix)}-.*"`);
  }
  if (m.container) parts.push(`container="${escapeLabelValue(m.container)}"`);
  // Never an empty matcher set: `metric{}` selects every series with that name, cluster-wide. A
  // literal that matches nothing is the safe failure -- an empty chart, not a full data dump.
  if (parts.length === 0) parts.push('container="__none__"');
  return parts.join(',');
}

/** Exported for the unit tests, which assert the escaping directly. */
export const _internal = { escapeLabelValue, escapeRegexValue, matchers };
