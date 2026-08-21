'use client';

/**
 * Actual usage versus what was requested, with the sizing basis drawn as a reference line.
 *
 * `connectNulls={false}` appears on every series in this file and is NOT a style choice. A null in
 * the series means Prometheus had no sample -- a scrape outage, or a pod that did not exist yet.
 * With `connectNulls` enabled Recharts draws a straight line across the hole, which invents data and
 * makes a gap look like steady usage. The gap must be visible as a gap.
 *
 * The shaded band between usage and request IS the waste. Rendering it as an area rather than
 * quoting a percentage is the point of the chart: it shows how much of the reservation went unused,
 * and for how long.
 */

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { PodMetrics } from '@/lib/schemas';
import { formatResource } from '@/lib/utils';

export function UsageChart({ metrics }: { metrics: PodMetrics }) {
  const { resource, request, limit, peak, p95 } = metrics;

  const data = metrics.usage.map((point) => ({
    t: point.t,
    // Kept as null, all the way into Recharts. Never coerced to 0.
    usage: point.value,
    request,
    // The waste band only exists where BOTH ends are known. If usage is missing, the gap is
    // unknown -- not zero -- so the band must break too.
    waste: point.value !== null && request !== null ? Math.max(request - point.value, 0) : null,
  }));

  const fmt = (v: number) => formatResource(v, resource);

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />
          <XAxis
            dataKey="t"
            tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
            tickFormatter={(t: string) =>
              new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            }
            minTickGap={48}
          />
          <YAxis
            tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
            tickFormatter={fmt}
            width={72}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--color-surface-2)',
              border: '1px solid var(--color-border-strong)',
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(label) => new Date(String(label)).toLocaleString()}
            formatter={(value, name) => [
              // An explicit label in the tooltip too. A blank cell would read as zero.
              value === null || value === undefined ? 'not observed' : fmt(Number(value)),
              String(name),
            ]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />

          <Area
            type="monotone"
            dataKey="waste"
            name="unused reservation"
            stackId="none"
            stroke="none"
            fill="var(--color-waste-moderate)"
            fillOpacity={0.18}
            connectNulls={false}
            isAnimationActive={false}
          />

          <Line
            type="monotone"
            dataKey="usage"
            name="actual usage"
            stroke="var(--color-waste-none)"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />

          {request !== null && (
            <ReferenceLine
              y={request}
              stroke="var(--color-text-secondary)"
              strokeDasharray="6 3"
              label={{
                value: `request ${fmt(request)}`,
                position: 'insideTopRight',
                fill: 'var(--color-text-secondary)',
                fontSize: 11,
              }}
            />
          )}

          {limit !== null && limit !== request && (
            <ReferenceLine
              y={limit}
              stroke="var(--color-pressure-moderate)"
              strokeDasharray="2 4"
              label={{
                value: `limit ${fmt(limit)}`,
                position: 'insideTopRight',
                fill: 'var(--color-pressure-moderate)',
                fontSize: 11,
              }}
            />
          )}

          {/*
            The sizing basis, drawn so the recommendation can be checked by eye. For memory this is
            the observed PEAK -- the line that explains why memory is not percentile-sized, since
            the peak sits visibly above where a p95 would fall.
          */}
          {resource === 'memory' && peak !== null && (
            <ReferenceLine
              y={peak}
              stroke="var(--color-pressure-high)"
              label={{
                value: `observed peak ${fmt(peak)} \u2190 sizing basis`,
                position: 'insideBottomRight',
                fill: 'var(--color-pressure-high)',
                fontSize: 11,
              }}
            />
          )}

          {resource === 'cpu' && p95 !== null && (
            <ReferenceLine
              y={p95}
              stroke="var(--color-evidence-modelled)"
              label={{
                value: `P95 ${fmt(p95)} \u2190 sizing basis`,
                position: 'insideBottomRight',
                fill: 'var(--color-evidence-modelled)',
                fontSize: 11,
              }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
