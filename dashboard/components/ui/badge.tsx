import { type VariantProps, cva } from 'class-variance-authority';

import { cn } from '@/lib/utils';

/**
 * Badge, copied into the repo per the shadcn/ui model rather than installed as a package.
 *
 * The variants carry product meaning, which is why this is worth reading rather than skimming:
 *
 *   evidence-*   how strong the evidence is. rehearsed > modelled > partial.
 *   action-*     what the recommendation proposes.
 *   unobserved   a signal that was NOT MEASURED. Hatched, neutral, and never green.
 *
 * There is deliberately no `success` variant applied to missing data anywhere in this app.
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ' +
    'whitespace-nowrap transition-colors',
  {
    variants: {
      variant: {
        default: 'border-border-subtle bg-surface-2 text-text-secondary',

        // Evidence tiers. Colour follows strength, so a scanned page reads correctly.
        'evidence-rehearsed':
          'border-evidence-rehearsed/40 bg-evidence-rehearsed/15 text-evidence-rehearsed',
        'evidence-modelled':
          'border-evidence-modelled/40 bg-evidence-modelled/15 text-evidence-modelled',
        'evidence-partial':
          'border-evidence-partial/40 bg-evidence-partial/15 text-evidence-partial',

        'action-reduce': 'border-action-reduce/40 bg-action-reduce/15 text-action-reduce',
        'action-increase': 'border-action-increase/40 bg-action-increase/15 text-action-increase',
        'action-keep': 'border-action-keep/40 bg-action-keep/15 text-action-keep',
        'action-review': 'border-action-review/40 bg-action-review/15 text-action-review',

        // The absence treatment. Hatched via the .not-observed component class, because a solid
        // fill of any colour reads as a value.
        unobserved: 'not-observed',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
