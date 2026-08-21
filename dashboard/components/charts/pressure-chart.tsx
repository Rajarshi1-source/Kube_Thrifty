'use client';

/**
 * PSI stall time and the CFS throttle ratio: observed suffering over time.
 *
 * When PSI was not collected this component renders an EXPLANATION, not an empty chart and
 * emphatically not a flat line at zero. An axis pinned at 0 with a line along the bottom is the most
 * persuasive possible argument that a workload is comfortable, and it would be a fabrication.
 *
 * The reference lines are the actual decision thresholds, so a reader can see how close a workload
 * sits to the gates rather than taking the verdict on trust:
 *   PSI full  > 5%  a rehearsal trips and is recorded as `regressed`
 *   throttle  > 1%  a CPU limit reduction is refused outright
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { PressureSeries } from '@/lib/schemas';
import { formatPct } from '@/lib/utils';

const PSI_FULL_CEILING = 0.05;
const THROTTLE_CEILING = 0.01;

export function PressureChart({ pressure }: { pressure: PressureSeries }) {
  if (!pressure.observed) {
    return (
      <div className="not-observed flex h-72 w-full flex-col items-center justify-center gap-3 rounded-lg p-8 text-center">
        <p className="text-sm font-medium">Pressure was not observed</p>
        <p className="max-w-md text-xs leading-relaxed">
          {pressure.unavailableReason}
        </p>
        <p className="max-w-md text-xs opacity-80">
          This is not a reading of zero pressure. The signal is absent, so nothing here supports
          shrinking this workload.
        </p>
      </div>
    );
  }

  // Merged on the timestamp so all three series share one x-axis. Missing entries stay null rather
  // than defaulting, so a gap in one series does not fabricate a zero in it.
  const byTime = new Map<string, { t: string; psiCpu: number | null; psiMem: number | null; throttle: number | null }>();

  const ensure = (t: string) => {
    let row = byTime.get(t);
    if (!row) {
      row = { t, psiCpu: null, psiMem: null, throttle: null };
      byTime.set(t, row);
    }
    return row;
  };

  for (const p of pressure.psiCpuFull) ensure(p.t).psiCpu = p.value;
  for (const p of pressure.psiMemFull) ensure(p.t).psiMem = p.value;
  for (const p of pressure.throttleRatio) ensure(p.t).throttle = p.value;

  const data = [...byTime.values()].sort((a, b) => a.t.localeCompare(b.t));

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
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
            tickFormatter={(v: number) => formatPct(v, 0)}
            width={56}
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
              value === null || value === undefined ? 'not observed' : formatPct(Number(value), 3),
              String(name),
            ]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />

          <ReferenceLine
            y={PSI_FULL_CEILING}
            stroke="var(--color-pressure-critical)"
            strokeDasharray="4 4"
            label={{
              value: 'PSI full ceiling (5%) \u2014 rehearsal trips',
              position: 'insideTopRight',
              fill: 'var(--color-pressure-critical)',
              fontSize: 10,
            }}
          />
          <ReferenceLine
            y={THROTTLE_CEILING}
            stroke="var(--color-pressure-moderate)"
            strokeDasharray="2 4"
            label={{
              value: 'throttle ceiling (1%) \u2014 blocks CPU cut',
              position: 'insideBottomRight',
              fill: 'var(--color-pressure-moderate)',
              fontSize: 10,
            }}
          />

          <Line
            type="monotone"
            dataKey="psiCpu"
            name="PSI cpu full"
            stroke="var(--color-pressure-high)"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="psiMem"
            name="PSI memory full"
            stroke="var(--color-pressure-critical)"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          {/*
            The throttle RATIO, never raw period counters. Counters scale with window length,
            replica count and CFS period, so two counter values from different windows cannot be
            compared. The ratio can.
          */}
          <Line
            type="monotone"
            dataKey="throttle"
            name="throttle ratio"
            stroke="var(--color-pressure-low)"
            strokeWidth={2}
            strokeDasharray="5 3"
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
