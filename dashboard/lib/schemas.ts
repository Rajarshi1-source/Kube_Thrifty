/**
 * schemas.ts -- Zod validation for every input, and the shared output types.
 *
 * Nothing reaches a service function unvalidated. That is not ceremony: the values here become
 * SQL parameters and PromQL label matchers, and `namespace` / `workload` / `pod` come from
 * Kubernetes labels, which in a multi-tenant cluster are attacker-influencable.
 *
 * The output types encode the project's central honesty rule in the type system:
 *
 *     number | null        -- observed, or NOT OBSERVED
 *
 * never `number` with a 0 default. A `0` for `throttleRatio` asserts "we watched and nothing was
 * throttled", which is a completely different claim from "we could not see the counter". With
 * `strict` and `noUncheckedIndexedAccess` on, collapsing the two becomes a type error rather than
 * a rendering bug that shows a green badge over missing data.
 */
import { z } from 'zod';

// ------------------------------------------------------------------------------------------------
// Primitives
// ------------------------------------------------------------------------------------------------

/**
 * RFC 1123 label, as Kubernetes defines it. Anchored, length-capped, and restricted to
 * [a-z0-9-] -- which incidentally excludes every character useful for injection.
 */
const kubeName = z
  .string()
  .min(1)
  .max(253)
  .regex(/^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$/, {
    message: 'must be a lowercase RFC 1123 name',
  });

/**
 * A Prometheus-style duration, capped at 30 days.
 *
 * The cap is a resource guard, not a preference: an unbounded window lets one URL ask Prometheus to
 * scan its entire retention and return millions of samples, which is a denial of service with a
 * query string.
 */
const durationWindow = z
  .string()
  .regex(/^\d+[smhdw]$/, { message: 'must look like 30m, 6h, 7d or 2w' })
  .refine((v) => durationToSeconds(v) <= 30 * 86_400, { message: 'window may not exceed 30d' })
  .default('7d');

export function durationToSeconds(d: string): number {
  const match = /^(\d+)([smhdw])$/.exec(d);
  if (!match?.[1] || !match[2]) return 0;
  const n = Number(match[1]);
  const unit = match[2];
  const factor = { s: 1, m: 60, h: 3_600, d: 86_400, w: 604_800 }[unit] ?? 0;
  return n * factor;
}

const sha256Hex = z.string().regex(/^[a-f0-9]{64}$/, { message: 'must be a 64-character sha256' });

export const EVIDENCE_TIERS = ['rehearsed', 'modelled', 'partial'] as const;
export const ACTIONS = ['reduce', 'increase', 'keep', 'needs_review'] as const;
export const RESOURCES = ['cpu', 'memory'] as const;
export const PRICING_SCENARIOS = ['fixed_node_pool', 'self_managed_karpenter', 'eks_auto_mode'] as const;

// ------------------------------------------------------------------------------------------------
// Query schemas
// ------------------------------------------------------------------------------------------------

export const recommendationsQuery = z.object({
  namespace: kubeName.optional(),
  workload: kubeName.optional(),
  action: z.enum(ACTIONS).optional(),
  evidenceTier: z.enum(EVIDENCE_TIERS).optional(),
  resource: z.enum(RESOURCES).optional(),
  // Bounded. An unpaginated list endpoint over a large cluster is a slow query waiting to happen.
  limit: z.coerce.number().int().positive().max(500).default(100),
  offset: z.coerce.number().int().min(0).default(0),
});

export const podMetricsQuery = z.object({
  window: durationWindow,
  resource: z.enum(RESOURCES).default('cpu'),
  // Floor of 30s enforced separately in the proxy; declared here so the error is a 400 rather than
  // a silent clamp the caller cannot see.
  step: z.coerce.number().int().min(30).max(3_600).default(300),
});

export const pressureQuery = z.object({
  window: durationWindow,
  step: z.coerce.number().int().min(30).max(3_600).default(300),
});

export const rehearsalsQuery = z.object({
  namespace: kubeName.optional(),
  workload: kubeName.optional(),
  outcome: z
    .enum(['running', 'safe', 'regressed', 'inconclusive', 'reverted_by_watchdog', 'failed'])
    .optional(),
  limit: z.coerce.number().int().positive().max(200).default(50),
});

export const verdictsQuery = z.object({
  namespace: kubeName.optional(),
  workload: kubeName.optional(),
  ruleId: z.string().max(64).regex(/^[A-Z0-9_]+$/).optional(),
  limit: z.coerce.number().int().positive().max(200).default(50),
});

export const bundleShaParam = z.object({ bundleSha: sha256Hex });

export const savingsQuery = z.object({
  scenario: z.enum(PRICING_SCENARIOS).default('fixed_node_pool'),
});

/**
 * The trigger body.
 *
 * Note what cannot be requested: there is no `autoPr`, no `apply`, no `rehearse`. The API's one
 * write enqueues an ANALYSIS. Whether that analysis opens a PR is the analyser's decision under its
 * own configuration, never a dashboard caller's.
 */
export const triggerBody = z.object({
  window: durationWindow,
  namespaces: z.array(kubeName).max(20).optional(),
});

export const analysisHistoryQuery = z.object({
  limit: z.coerce.number().int().positive().max(100).default(20),
});

export const runIdParam = z.object({
  id: z.string().regex(/^run-[a-zA-Z0-9-]{1,32}$/, { message: 'must be a run id' }),
});

export const podNameParam = z.object({ name: kubeName });

// ------------------------------------------------------------------------------------------------
// Response types
// ------------------------------------------------------------------------------------------------

export type EvidenceTier = (typeof EVIDENCE_TIERS)[number];
export type Action = (typeof ACTIONS)[number];

export interface Recommendation {
  id: number;
  runId: string;
  namespace: string;
  workload: string;
  container: string;
  resource: 'cpu' | 'memory';

  currentRequest: number | null;
  currentLimit: number | null;
  recommendedRequest: number;
  recommendedLimit: number;

  action: Action;
  confidence: 'high' | 'medium' | 'low';
  evidenceTier: EvidenceTier;

  /** Which floor won the max(). */
  bindingConstraint: string | null;
  /** What the number was derived from -- for memory, whether it was the kernel high-water mark. */
  sizingBasis: string | null;
  reductionBlocked: boolean;
  rationale: string;

  observed: {
    p50: number | null;
    p95: number | null;
    p99: number | null;
    peak: number | null;
    throttleRatio: number | null;
    /** null renders as "not observed". Never 0, never green. */
    psiStalledRatio: number | null;
    oomEvents: number | null;
  };

  samples: number;
  forecastModel: string | null;
  rehearsalId: string | null;
  hpaCoupled: boolean;
  /** Proportional over-provisioning. A ratio, never a currency amount. */
  wastePct: number | null;
  createdAt: string;
}

/**
 * A time series point.
 *
 * `value: number | null` and a gap stays null all the way to Recharts, where `connectNulls={false}`
 * renders it as a GAP. Interpolating across a scrape outage would draw a confident line through
 * data that does not exist.
 */
export interface SeriesPoint {
  t: string;
  value: number | null;
}

export interface PodMetrics {
  pod: string;
  container: string;
  resource: 'cpu' | 'memory';
  window: string;
  usage: SeriesPoint[];
  request: number | null;
  limit: number | null;
  /** The memory sizing basis, when known. */
  peak: number | null;
  p95: number | null;
  p99: number | null;
  /** True when these came from the cgroup DaemonSet rather than a scraped gauge. */
  cgroupTruth: boolean;
}

export interface PressureSeries {
  pod: string;
  container: string;
  window: string;
  /** Empty array + observed:false is "not observed". It is NOT a flat line at zero. */
  psiCpuFull: SeriesPoint[];
  psiMemFull: SeriesPoint[];
  throttleRatio: SeriesPoint[];
  observed: boolean;
  /** Why pressure is missing, when it is. Rendered in the UI instead of a zero line. */
  unavailableReason: string | null;
}

export interface Rehearsal {
  rehearsalId: string;
  runId: string | null;
  namespace: string;
  workload: string;
  pod: string;
  container: string;
  outcome: 'running' | 'safe' | 'regressed' | 'inconclusive' | 'reverted_by_watchdog' | 'failed';
  originalRequests: Record<string, number>;
  candidateRequests: Record<string, number>;
  /** Only ever non-null for a `safe` outcome. */
  rehearsedFloor: number | null;
  baselineSignals: Record<string, unknown> | null;
  observedSignals: Record<string, unknown> | null;
  tripReasons: string[];
  revertDeadline: string;
  revertedAt: string | null;
  startedAt: string;
  finishedAt: string | null;
}

export interface Verdict {
  bundleSha: string;
  namespace: string;
  workload: string;
  container: string;
  ruleId: string;
  confidence: number;
  summary: string;
  remediation: string;
  evidence: Record<string, unknown>;
  /** Did KubeThrifty's own merged change cause this? The differentiator. */
  attributedChange: Record<string, unknown> | null;
  rulesetVersion: string;
  verdictsDigest: string;
  createdAt: string;
}

export interface SavingsSummary {
  scenario: (typeof PRICING_SCENARIOS)[number];
  nodesBefore: number;
  nodesAfter: number;
  /** The node delta. The ONLY figure in the product that carries a currency symbol. */
  nodesRemoved: number;
  monthlySaving: number | null;
  currency: string;
  /** Pinned price date, so a stale catalogue is visible rather than assumed current. */
  asOf: string;
  region: string;
  /** Aggregate waste as a RATIO. Per-pod waste is never expressed in money. */
  wasteRatio: number | null;
  note: string;
}

export interface AnalysisRun {
  runId: string;
  status: 'running' | 'succeeded' | 'failed' | 'degraded';
  windowSpec: string;
  startedAt: string;
  finishedAt: string | null;
  workloadsSeen: number;
  recommendationsMade: number;
  prUrl: string | null;
  degradedReason: string | null;
  error: string | null;
}
