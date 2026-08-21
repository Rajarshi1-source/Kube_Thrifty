import { cn } from '@/lib/utils';

/**
 * The rendering of a signal that was NOT MEASURED.
 *
 * This component exists so "not observed" has exactly one appearance across the whole UI, and so
 * nobody has to decide case by case what to show for a null. The rules it enforces:
 *
 *   - Never a 0. A zero is a measurement.
 *   - Never green, and never any colour from the pressure scale. Both would say "no pressure",
 *     which is an argument FOR shrinking a workload that has not actually been observed.
 *   - Always hatched, so it reads as absent even at a glance and even in greyscale.
 *   - Always carries a reason on hover, because "why is this missing" is the immediate next question.
 *
 * Getting this wrong is worse than showing nothing at all: a confident zero invites a reduction that
 * the evidence does not support.
 */
export function NotObserved({
  reason,
  className,
  compact = false,
}: {
  // `| undefined` is explicit because `exactOptionalPropertyTypes` is on: passing a variable that
  // happens to be undefined is not the same as omitting the prop, and the compiler enforces that.
  reason?: string | undefined;
  className?: string | undefined;
  compact?: boolean | undefined;
}) {
  const title =
    reason ??
    'This signal was not observed. It is not zero -- the value is unknown, and any recommendation ' +
      'relying on it carries a reduced evidence tier.';

  return (
    <span
      className={cn(
        'not-observed inline-flex items-center rounded px-2 py-0.5 text-xs',
        className,
      )}
      title={title}
      // Screen readers get the full sentence; sighted users get the short label plus the hatch.
      aria-label={title}
    >
      {compact ? 'n/obs' : 'not observed'}
    </span>
  );
}

/**
 * Render a numeric value, or the not-observed treatment when it is null.
 *
 * The single most-used component in the app. Having it means a developer adding a new column cannot
 * accidentally write `{value ?? 0}` -- the convenient path is also the correct one.
 */
export function MetricValue({
  value,
  format,
  reason,
  className,
}: {
  value: number | null;
  format: (v: number) => string;
  reason?: string | undefined;
  className?: string | undefined;
}) {
  if (value === null) {
    return <NotObserved reason={reason} compact className={className} />;
  }
  return <span className={cn('tabular', className)}>{format(value)}</span>;
}
