import { Badge } from '@/components/ui/badge';
import type { Action } from '@/lib/schemas';

const VARIANT: Record<Action, 'action-reduce' | 'action-increase' | 'action-keep' | 'action-review'> = {
  reduce: 'action-reduce',
  increase: 'action-increase',
  keep: 'action-keep',
  needs_review: 'action-review',
};

const TITLE: Record<Action, string> = {
  reduce: 'The request can be lowered. Ships as a pull request for review, never applied directly.',
  increase:
    'The request must be RAISED. A floor exceeded the current request -- most often the observed ' +
    'memory peak, which means this workload is at risk of an OOM kill as configured.',
  keep: 'Already appropriately sized, or the change was too small to be worth a pull request.',
  needs_review:
    'Not enough evidence to act. Usually fewer than 168 hourly samples, so the workload is skipped ' +
    'rather than guessed at.',
};

export function ActionBadge({ action }: { action: Action }) {
  return (
    <Badge variant={VARIANT[action]} title={TITLE[action]}>
      {action.replace('_', ' ')}
    </Badge>
  );
}

/**
 * Shown when a reduction was computed but withheld because pressure was observed.
 *
 * Worth its own badge rather than a footnote: it is the clearest evidence the tool is measuring
 * suffering and not just usage. A percentile-only right-sizer would have shipped the cut.
 */
export function BlockedBadge() {
  return (
    <Badge
      variant="action-review"
      title={
        'A reduction was computed but WITHHELD: this container showed observed pressure ' +
        '(CFS throttling above 1%, a PSI full-stall above the ceiling, or an OOM event). ' +
        'Sizing floors only ever raise a request, so the cut was refused.'
      }
    >
      reduction blocked
    </Badge>
  );
}

/**
 * Marks a workload whose requests are coupled to an HPA target.
 *
 * The HPA scales on utilisation-of-request, so shrinking the request raises measured utilisation and
 * can trigger a scale-out that costs more than the right-sizing saves. The resource change and the
 * HPA target change must ship in ONE pull request -- the intermediate state is the outage.
 */
export function HpaCoupledBadge() {
  return (
    <Badge
      variant="default"
      className="border-action-review/40 text-action-review"
      title={
        'An HPA targets this workload on utilisation-of-request. Shrinking the request raises ' +
        'measured utilisation, which can trigger a scale-out that costs more than the saving. ' +
        'The resource change and the HPA target change must ship in the same pull request.'
      }
    >
      HPA-coupled
    </Badge>
  );
}
