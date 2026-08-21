'use client';

/**
 * The waste-versus-pressure quadrant. The dashboard's most important single view.
 *
 * Waste on x, observed pressure on y, one point per container. The four regions are the product's
 * whole thesis, which is why they are labelled on the chart rather than left to interpretation:
 *
 *   bottom-right  HIGH waste, LOW pressure   -> safe to shrink. The money.
 *   top-left      LOW waste, HIGH pressure   -> must GROW. Under-provisioned and suffering.
 *   top-right     HIGH waste, HIGH pressure  -> the interesting one. Wasteful on one resource while
 *                                              suffering on another, or a limit set below the
 *                                              request. Needs a human.
 *   bottom-left   LOW waste, LOW pressure    -> correctly sized. Leave it alone.
 *
 * A percentile-only right-sizer collapses this to the x-axis and would happily shrink a workload
 * sitting at the top of the y-axis.
 *
 * Containers whose pressure was never observed are NOT plotted at y=0 -- that would drop them into
 * "safe to shrink" purely because nobody measured them. They are excluded and counted separately.
 */

import { useMemo } from 'react';
import {
  CartesianGrid,
  Cell,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';

import type { Recommendation } from '@/lib/schemas';
import { formatPct, formatResource, pressureBucket } from '@/lib/utils';

const WASTE_MIDPOINT = 0.3;
const PRESSURE_MIDPOINT = 0.01;

const BUCKET_COLOUR: Record<string, string> = {
  none: 'var(--color-pressure-none)',
  low: 'var(--color-pressure-low)',
  moderate: 'var(--color-pressure-moderate)',
  high: 'var(--color-pressure-high)',
  critical: 'var(--color-pressure-critical)',
  unobserved: 'var(--color-unobserved-fg)',
};

interface Point {
  x: number;
  y: number;
  z: number;
  label: string;
  resource: string;
  bucket: string;
  currentRequest: number | null;
  recommendedRequest: number;
  evidenceTier: string;
}

export function WastePressureQuadrant({ items }: { items: Recommendation[] }) {
  const { points, unobservedCount } = useMemo(() => {
    const points: Point[] = [];
    let unobservedCount = 0;

    for (const r of items) {
      const bucket = pressureBucket(r.observed.psiStalledRatio, r.observed.throttleRatio);

      // Excluded, not plotted at zero. Plotting an unmeasured container on the pressure axis at 0
      // would place it in "safe to shrink" on the strength of missing data.
      if (bucket === 'unobserved' || r.wastePct === null) {
        unobservedCount++;
        continue;
      }

      points.push({
        x: r.wastePct,
        // null-guard-ok: containers whose pressure was not observed are excluded by the `continue`
        // above, so reaching here means at least one signal was measured. The 0 is a max() identity,
        // not an assertion that the other signal was zero.
        y: Math.max(r.observed.psiStalledRatio ?? 0, r.observed.throttleRatio ?? 0),
        // Bubble size tracks the absolute reclaimable amount, so a big waste ratio on a tiny
        // container does not outshout a modest ratio on a large one.
        z: r.currentRequest ? Math.abs(r.currentRequest - r.recommendedRequest) : 1,
        label: `${r.workload}/${r.container}`,
        resource: r.resource,
        bucket,
        currentRequest: r.currentRequest,
        recommendedRequest: r.recommendedRequest,
        evidenceTier: r.evidenceTier,
      });
    }

    return { points, unobservedCount };
  }, [items]);

  return (
    <div>
      <div className="h-96 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 16, right: 24, bottom: 28, left: 12 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />

            {/* Quadrant tints, drawn behind everything. */}
            <ReferenceArea
              x1={WASTE_MIDPOINT}
              y1={0}
              y2={PRESSURE_MIDPOINT}
              fill="var(--color-waste-severe)"
              fillOpacity={0.07}
              label={{
                value: 'safe to shrink',
                position: 'insideBottomRight',
                fill: 'var(--color-waste-severe)',
                fontSize: 11,
              }}
            />
            <ReferenceArea
              x1={0}
              x2={WASTE_MIDPOINT}
              y1={PRESSURE_MIDPOINT}
              fill="var(--color-pressure-critical)"
              fillOpacity={0.07}
              label={{
                value: 'must grow',
                position: 'insideTopLeft',
                fill: 'var(--color-pressure-critical)',
                fontSize: 11,
              }}
            />
            <ReferenceArea
              x1={WASTE_MIDPOINT}
              y1={PRESSURE_MIDPOINT}
              fill="var(--color-action-review)"
              fillOpacity={0.07}
              label={{
                value: 'needs a human',
                position: 'insideTopRight',
                fill: 'var(--color-action-review)',
                fontSize: 11,
              }}
            />

            <XAxis
              type="number"
              dataKey="x"
              name="waste"
              domain={[0, 1]}
              tickFormatter={(v: number) => formatPct(v, 0)}
              tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
              label={{
                value: 'unused reservation (ratio, never money)',
                position: 'insideBottom',
                offset: -16,
                fill: 'var(--color-text-muted)',
                fontSize: 11,
              }}
            />
            <YAxis
              type="number"
              dataKey="y"
              name="pressure"
              tickFormatter={(v: number) => formatPct(v, 1)}
              tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
              label={{
                value: 'observed pressure',
                angle: -90,
                position: 'insideLeft',
                fill: 'var(--color-text-muted)',
                fontSize: 11,
              }}
            />
            <ZAxis type="number" dataKey="z" range={[40, 400]} />

            <ReferenceLine x={WASTE_MIDPOINT} stroke="var(--color-border-strong)" />
            <ReferenceLine y={PRESSURE_MIDPOINT} stroke="var(--color-border-strong)" />

            <Tooltip
              contentStyle={{
                backgroundColor: 'var(--color-surface-2)',
                border: '1px solid var(--color-border-strong)',
                borderRadius: 6,
                fontSize: 12,
              }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0]?.payload as Point | undefined;
                if (!p) return null;
                return (
                  <div className="rounded-md border border-border-strong bg-surface-2 p-3 text-xs">
                    <p className="font-medium text-text-primary">{p.label}</p>
                    <p className="mt-1 text-text-secondary">
                      {p.resource} &middot; {p.evidenceTier}
                    </p>
                    <p className="mt-1">waste {formatPct(p.x)}</p>
                    <p>pressure {formatPct(p.y, 3)}</p>
                    <p className="mt-1 text-text-secondary">
                      {formatResource(p.currentRequest, p.resource as 'cpu' | 'memory')} &rarr;{' '}
                      {formatResource(p.recommendedRequest, p.resource as 'cpu' | 'memory')}
                    </p>
                  </div>
                );
              }}
            />

            <Scatter data={points} isAnimationActive={false}>
              {points.map((p, i) => (
                <Cell
                  key={`${p.label}-${p.resource}-${i}`}
                  // Falls back to the unobserved token rather than to a pressure colour: an
                  // unrecognised bucket must not be painted as "no pressure".
                  fill={BUCKET_COLOUR[p.bucket] ?? 'var(--color-unobserved-fg)'}
                />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {unobservedCount > 0 && (
        <p className="mt-2 text-xs text-text-muted">
          <span className="not-observed rounded px-1.5 py-0.5">{unobservedCount} not plotted</span>{' '}
          &mdash; pressure was not observed for these containers, so they are excluded rather than
          drawn at zero. Placing them on the axis would put unmeasured workloads in &ldquo;safe to
          shrink&rdquo;.
        </p>
      )}
    </div>
  );
}
