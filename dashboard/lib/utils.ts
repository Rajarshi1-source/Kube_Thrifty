import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** The shadcn `cn` helper: clsx for conditionals, tailwind-merge to resolve conflicting classes. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format CPU as millicores or cores.
 *
 * Returns the literal string "not observed" for null. Deliberately NOT "0" and not an em dash: the
 * text has to be readable as an absence, because a reader skimming a column of numbers will
 * otherwise treat any short token as a small value.
 */
export function formatCpu(cores: number | null): string {
  if (cores === null) return 'not observed';
  if (cores < 1) return `${Math.round(cores * 1000)}m`;
  return `${cores.toFixed(2)} cores`;
}

/** Format bytes as MiB/GiB. Same null contract as formatCpu. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null) return 'not observed';
  const mib = bytes / (1024 * 1024);
  if (mib < 1024) return `${Math.round(mib)} Mi`;
  return `${(mib / 1024).toFixed(2)} Gi`;
}

/** Dispatch on resource type, so a caller cannot accidentally format bytes as millicores. */
export function formatResource(value: number | null, resource: 'cpu' | 'memory'): string {
  return resource === 'cpu' ? formatCpu(value) : formatBytes(value);
}

/**
 * Format a ratio as a percentage.
 *
 * Used for waste and for pressure. NEVER used for money: a percentage of a request is not a sum of
 * currency, and the only figure in this product that carries a currency symbol is the bin-packer's
 * node-count delta.
 */
export function formatPct(ratio: number | null, digits = 1): string {
  if (ratio === null) return 'not observed';
  return `${(ratio * 100).toFixed(digits)}%`;
}

/**
 * Money. Only ever called with a node-delta-derived figure.
 *
 * null renders as "not computed" rather than a zero amount: before the packer has run, the saving is
 * unknown, and "$0" would be a claim we have not earned.
 */
export function formatMoney(amount: number | null, currency = 'USD'): string {
  if (amount === null) return 'not computed';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

/** Bucket a waste ratio onto the waste token scale. */
export function wasteBucket(
  ratio: number | null,
): 'unobserved' | 'none' | 'low' | 'moderate' | 'high' | 'severe' {
  if (ratio === null) return 'unobserved';
  if (ratio <= 0.05) return 'none';
  if (ratio <= 0.25) return 'low';
  if (ratio <= 0.5) return 'moderate';
  if (ratio <= 0.75) return 'high';
  return 'severe';
}

/**
 * Bucket observed pressure.
 *
 * The thresholds are low on purpose. PSI `full` above 5% is the rehearsal trip condition, and a
 * throttle ratio over 1% blocks a CPU limit reduction, so by the time either reaches double digits
 * the workload is in real trouble.
 */
export function pressureBucket(
  psiFull: number | null,
  throttleRatio: number | null,
): 'unobserved' | 'none' | 'low' | 'moderate' | 'high' | 'critical' {
  // Both unobserved means unobserved. It does NOT mean "no pressure" -- that conflation is the
  // single most dangerous rendering mistake this UI could make, because it argues for shrinking a
  // workload nobody has actually measured.
  if (psiFull === null && throttleRatio === null) return 'unobserved';

  // null-guard-ok: the early return above has already established that at least ONE of these was
  // observed. Treating the other as 0 inside a max() is correct -- it means "this signal contributes
  // nothing to the worst case" -- and is not a claim that it was measured as zero.
  const psi = psiFull ?? 0;
  const throttle = throttleRatio ?? 0;
  const worst = Math.max(psi, throttle);

  if (worst === 0) return 'none';
  if (worst < 0.01) return 'low';
  if (worst < 0.05) return 'moderate';
  if (worst < 0.15) return 'high';
  return 'critical';
}
