import { Badge } from '@/components/ui/badge';
import type { EvidenceTier } from '@/lib/schemas';

/**
 * The evidence-tier badge. Appears next to EVERY recommendation, without exception.
 *
 * The wording is chosen so the badge cannot be misread as stronger than it is:
 *
 *   rehearsed  "ran on a live pod"  -- actually tried, nothing regressed
 *   modelled   "not tried"          -- sound statistics, but no experiment
 *   partial    "signal missing"     -- the safety checks ran on less than they were designed for
 *
 * `modelled` says "not tried" rather than something neutral like "calculated" on purpose. A
 * modelled recommendation must never read as verified, and the most likely way that happens is a
 * label that sounds authoritative.
 */
const COPY: Record<EvidenceTier, { label: string; qualifier: string; title: string }> = {
  rehearsed: {
    label: 'rehearsed',
    qualifier: 'ran on a live pod',
    title:
      'This size was applied to one live pod in place, observed, and reverted. Nothing regressed: ' +
      'no OOM kill, no restart, no throttling increase, no PSI stall above the ceiling.',
  },
  modelled: {
    label: 'modelled',
    qualifier: 'not tried',
    title:
      'Derived from observed percentiles and a forecast. Sound, but this size has NOT been applied ' +
      'to a running pod. It is a prediction, not a verified result.',
  },
  partial: {
    label: 'partial',
    qualifier: 'signal missing',
    title:
      'A signal the sizer wanted was unavailable -- usually PSI, or memory falling back to a ' +
      'sampled gauge instead of the kernel high-water mark. The safety checks ran with less ' +
      'information than they were designed for.',
  },
};

export function EvidenceBadge({
  tier,
  showQualifier = true,
}: {
  tier: EvidenceTier;
  showQualifier?: boolean;
}) {
  const copy = COPY[tier];
  return (
    <Badge variant={`evidence-${tier}`} title={copy.title}>
      {copy.label}
      {showQualifier && (
        <span className="opacity-70">&middot; {copy.qualifier}</span>
      )}
    </Badge>
  );
}
