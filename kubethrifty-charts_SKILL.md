---
name: kubethrifty-charts
description: >-
  Build the KubeThrifty data visualisations with Recharts and Tremor. Use for ANY chart, KPI, or
  data-viz component: the per-pod actual-versus-requested time-series with a shaded waste area,
  the peak reference line showing the memory sizing basis, the forecast line with its 90 percent
  interval band, the PSI pressure and throttle-ratio chart, the waste-versus-pressure quadrant
  scatter, the rehearsal before-and-after comparison, the cluster waste-stack funnel, the
  node-delta savings chart, bar lists, KPI cards, and gauges. Trigger on Recharts, Tremor, chart,
  AreaChart, ComposedChart, ScatterChart, Funnel, BarList, KPI card, sparkline, quadrant, gauge,
  percentile band, forecast band, PSI chart, or node delta. MANDATE: Recharts is the time-series
  layer (React 19 compatible); Tremor supplies dashboard blocks via the COPY-AND-PASTE model -- do
  NOT add the frozen at-tremor-react npm package; charts are Client Components, dynamically
  imported; and a gap in a signal series is rendered as a GAP, never as zero.
---

# Recharts + Tremor — KubeThrifty Visualisations

Your charts make the argument. Three of them carry the whole product thesis: the **forecast band** (why the
floor sits where it does), the **pressure series** (why some cuts are refused), and the **rehearsal
before/after** (why this recommendation is evidence rather than a guess).

## The Tremor decision (state it confidently)

| Option | Verdict |
|---|---|
| **Tremor copy-and-paste components** (on Recharts + Radix + Tailwind v4) | **USE THIS.** Components are copied *into* `components/` and owned there — same philosophy as shadcn/ui, which this project already uses. Works with Tailwind v4 + React 19 |
| Legacy `@tremor/react` npm package (v3.x) | **AVOID.** Effectively frozen (last meaningful publish ~early 2025), tied to the Tailwind v3 config/token model, fights a Tailwind v4 + React 19 + Next 16 stack |

So the only chart dependency is **`recharts` 3.x**. Tremor blocks are *files in your repo*.

## Critical rules (never violate)

- **A gap is a gap.** Missing PSI or cgroup data must render as a break in the line (pass `null`, set
  `connectNulls={false}`) with a "not observed" annotation — **never zero**, which reads as "no pressure"
  and is the exact misreading the evidence layer exists to prevent.
- **Charts are Client Components** (`'use client'`) and dynamically imported
  (`dynamic(() => import('./ResourceChart'), { ssr: false })`) so they stay out of the server bundle.
- **Theme through tokens, never hardcoded hex.** Waste palette and `--chart-*` variables come from
  kubethrifty-tailwind-shadcn, so dark mode and the semantic colour scale just work.
- **Always `ResponsiveContainer`** for layout charts; never fixed pixel widths.
- **Encode meaning twice.** Every colour is paired with a number or a label, and every chart has an
  accessible name. Colour-only encoding fails both accessibility and screenshots in a README.
- **Downsample before charting.** Request `step=1h` for 7–30d windows; Recharts is smooth at a few hundred
  points and janky at thousands. Memoise derived data and disable animation on dense dashboards.

## Signature chart 1 — actual vs requested vs peak vs forecast

```tsx
'use client';
import { ResponsiveContainer, ComposedChart, Area, Line, ReferenceLine,
         XAxis, YAxis, Tooltip, Legend } from 'recharts';

// data: { t, actual, requested, forecast?, forecastHi? }[]
export function ResourceChart({ data, p95, peak, resource }:
  { data: Point[]; p95: number; peak: number; resource: 'cpu' | 'memory' }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={data}>
        <XAxis dataKey="t" /><YAxis /><Tooltip /><Legend />
        {/* what you pay for */}
        <Line dataKey="requested" stroke="var(--chart-requested)" dot={false} strokeDasharray="4 4" />
        {/* what you used — the gap to `requested` IS the waste */}
        <Area dataKey="actual" stroke="var(--chart-actual)" fill="var(--chart-actual)"
              fillOpacity={0.15} connectNulls={false} />
        {/* the forecast interval — why a downsize is provably safe */}
        <Area dataKey="forecastHi" stroke="none" fill="var(--chart-forecast)" fillOpacity={0.12} />
        <Line dataKey="forecast" stroke="var(--chart-forecast)" dot={false} strokeDasharray="2 2" />
        {/* the sizing basis, made visible: memory sizes off PEAK, cpu off P95 */}
        {resource === 'memory'
          ? <ReferenceLine y={peak} stroke="var(--chart-peak)" label="peak (sizing basis)" />
          : <ReferenceLine y={p95} stroke="var(--chart-p95)" label="P95 (sizing basis)" />}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
```

Labelling the reference line "sizing basis" does real work: it shows an interviewer, from the screenshot
alone, that CPU and memory are sized by different rules.

## Signature chart 2 — the pressure series (new in Rev 3)

```tsx
'use client';
// data: { t, psiMemFull, psiCpuFull, throttleRatio }[]  — values are 0..1 shares, or null if unobserved
export function PressureChart({ data }: { data: PressurePoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <ComposedChart data={data}>
        <XAxis dataKey="t" /><YAxis tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
        <Tooltip formatter={(v: number | null) =>
          v === null ? 'not observed' : `${(v * 100).toFixed(2)}%`} />
        <Legend />
        <Area dataKey="psiMemFull" name="memory stall (full)" connectNulls={false}
              stroke="var(--chart-pressure-mem)" fill="var(--chart-pressure-mem)" fillOpacity={0.2} />
        <Line dataKey="psiCpuFull" name="cpu stall (full)" connectNulls={false}
              stroke="var(--chart-pressure-cpu)" dot={false} />
        <Line dataKey="throttleRatio" name="throttled periods" connectNulls={false}
              stroke="var(--chart-throttle)" dot={false} strokeDasharray="3 3" />
        {/* the 5% line is the decision threshold used by the verification logic */}
        <ReferenceLine y={0.05} stroke="var(--chart-threshold)" label="regression threshold" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
```

`connectNulls={false}` everywhere, and a formatter that prints "not observed" for `null`. Drawing a line
through a gap invents evidence.

## Signature chart 3 — the waste/pressure quadrant

A Recharts `ScatterChart`: X = unused reservation share (0–1), Y = pressure (max of PSI full / throttle
ratio), bubble size = monthly rupee impact, colour = action.

- Draw `ReferenceArea`s and label the four quadrants: **safe to cut** (high waste, low pressure),
  **needs more** (low waste, high pressure), **well sized**, **investigate**.
- The "needs more" quadrant is the point of the chart — it is the visual proof that the tool is an SRE tool
  and not a cost-cutting bot. Give it a label, not just a colour.
- Bubbles are clickable to the pod detail page; each carries an `aria-label` with pod, waste %, pressure %,
  action, and evidence tier.

## Signature chart 4 — rehearsal before/after

Not a time series: a **grouped bar chart or a small-multiples strip** comparing baseline versus rehearsal
for throttle ratio, memory PSI, CPU PSI, and peak memory, plus a plain table for OOM events and restarts
(discrete counts belong in a table, not a bar). Annotate the observation window and the outcome badge. The
number to make unmissable is `restarts = 0`.

## Signature chart 5 — the cluster waste stack + node delta

- **Funnel** (Tremor block or a stacked horizontal bar): billed → capacity → allocatable → requested →
  used. Each step labelled with its absolute value and the percentage lost at that step, because the
  interesting part is *where* capacity disappears.
- **Node delta:** a two-bar comparison (before / after) annotated `4 × m5.xlarge → 3 × m5.xlarge`, plus a
  scenario selector (fixed node group · Karpenter · Auto Mode +≈12%). Always print the catalogue `asOf`
  date under the chart — a dated price list is what makes the rupee figure defensible.
- **Never** chart per-pod rupees as "savings". Per-pod waste is a percentage; money is a node delta.

## Tremor blocks (copied into the repo)

KPI/metric cards (total pods, over-provisioned count, projected ₹/month, last-run age, rehearsals passed),
BarList (most wasteful namespaces by rupee impact), Tracker/category bars (per-namespace efficiency),
spark charts (per-pod trend inside quadrant tooltips). Edit them in `components/ui/` like any shadcn
component and theme them with project tokens; they render Recharts underneath, so they compose with the
bespoke charts.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| `npm install @tremor/react` | copy-paste Tremor blocks + `recharts` |
| `null` signal plotted as `0`, or `connectNulls` left default | `connectNulls={false}` + "not observed" in the tooltip |
| chart in a Server Component | `'use client'` + `dynamic(..., { ssr: false })` |
| hardcoded hex | semantic tokens (`var(--chart-*)`, waste palette) |
| fixed pixel width on a layout chart | `ResponsiveContainer` |
| no forecast band on the pod chart | draw the forecast line + 90% interval area |
| no peak reference line on the memory chart | label it "peak (sizing basis)" — it explains the whole memory rule |
| pressure chart without the 5% threshold line | add the `ReferenceLine` used by the regression logic |
| OOM/restart counts drawn as bars | small integers belong in a table |
| per-pod ₹ presented as savings | node delta + scenario + catalogue date |
| colour-only encoding | add text/`aria-label` (waste %, pressure %, action, evidence tier) |
| charting raw 1m data over 30d | request `step=1h`; downsample; memoise |

## Quick reference

- **Recharts 3.x** for time-series and bespoke charts; **Tremor copy-paste blocks** for dashboard furniture. No `@tremor/react`.
- Client Components, dynamically imported, `ssr: false`, `ResponsiveContainer`.
- Five signature visuals: forecast band · pressure series · waste/pressure quadrant · rehearsal before/after · waste-stack funnel + node delta.
- Gaps stay gaps (`connectNulls={false}`, "not observed"); reference lines name the sizing basis and the regression threshold.
- Tokens from kubethrifty-tailwind-shadcn; data shapes from kubethrifty-nextjs-frontend; savings semantics from kubethrifty-cost-and-packing.
