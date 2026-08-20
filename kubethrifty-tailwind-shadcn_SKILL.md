---
name: kubethrifty-tailwind-shadcn
description: >-
  Style the KubeThrifty dashboard with Tailwind CSS v4 and shadcn/ui. Use for ANY visual
  component, theme, layout, or form -- the dark-mode-first shell, KPI cards, the
  waste-versus-pressure quadrant, the recommendation card with its REDUCE / INCREASE / KEEP /
  NEEDS_REVIEW states, evidence-tier chips (rehearsed / modelled / partial), the not-observed
  treatment for missing signals, rehearsal outcome badges, confidence and attribution chips, the
  sizing-basis note, the savings scenario selector, skeletons and empty states -- or whenever
  Tailwind, shadcn, Radix, components.json, cn(), CSS-variable theming, dark mode, accessibility,
  or react-hook-form plus zod come up. Trigger even without an explicit ask when styling decisions
  are made. MANDATE: Tailwind v4 CSS-first (at-theme, no tailwind.config.js) plus shadcn/ui
  components COPIED INTO components/ui; preserve Radix accessibility; theme via semantic tokens
  including a waste palette AND a pressure palette; a missing signal is styled as not observed,
  never as zero.
---

# Tailwind CSS v4 + shadcn/ui — KubeThrifty UI

You give KubeThrifty its look: a **dark-mode-first cost dashboard** that reads as a serious SRE tool, not a
template. Tailwind v4 (CSS-first) for styling, shadcn/ui (copied in) for accessible primitives, semantic
tokens for everything — including a **waste palette** and, new in Rev 3, a **pressure palette** and an
**evidence** scale, because the UI now has to communicate not just *how much waste* but *how confident we
are and how we know*.

## Version mandate

- **Tailwind CSS v4** — CSS-first configuration via `@theme` in the global stylesheet; no
  `tailwind.config.js` required. Import with `@import "tailwindcss";`.
- **shadcn/ui copied INTO the repo** at `components/ui/**` and edited there. shadcn is not an npm
  dependency — it is a generator that drops Radix-based components you own. The CLI supports Tailwind v4.
- Consistent with the rest of the stack (React 19, Next 16.3); Tremor blocks (kubethrifty-charts) follow the
  same copy-in model, so the whole UI is "components you own," uniformly themed.

## Critical rules (never violate)

- **shadcn components are copied in and edited.** Never `npm i` a shadcn package. Customise in place and
  keep the Radix primitives (don't strip ARIA).
- **Theme via tokens, never hardcoded colours.** Semantic CSS variables in `@theme`, referenced through
  utilities. This is what makes dark mode, the waste palette, and the pressure palette agree.
- **Dark by default**, light as the toggle, `.dark` class strategy with token pairs.
- **Missing data has its own visual language.** `null` signals render through a `NotObserved` treatment
  (dashed border, muted foreground, the literal words "not observed") — never a zero, never an empty bar.
  This is a styling rule because it is where the product's honesty is either preserved or quietly lost.
- **Colour is never the only signal.** Pair every colour with a number and a word (waste %, pressure %,
  action, evidence tier, confidence).
- **Compose with `cn()`** (clsx + tailwind-merge) so variant overrides actually win.
- **Desktop-first density, mobile-aware.** This is an analyst tool: dense tables and grids on desktop,
  stacking gracefully on small screens.

## Tailwind v4 theme — waste, pressure, and evidence tokens

```css
/* app/globals.css */
@import "tailwindcss";

@theme {
  /* base surfaces (dark-first) */
  --color-background: oklch(0.18 0.01 260);
  --color-foreground: oklch(0.96 0 0);
  --color-card:       oklch(0.21 0.012 260);
  --color-border:     oklch(0.30 0.01 260);
  --color-primary:    oklch(0.72 0.15 200);        /* "thrifty" teal-cyan */

  /* WASTE PALETTE — unused reservation. Shared by quadrant, gauge, badges, charts. */
  --color-waste-efficient: oklch(0.72 0.17 150);   /* green  < 20% */
  --color-waste-mild:      oklch(0.85 0.15 95);    /* yellow 20-50% */
  --color-waste-moderate:  oklch(0.74 0.16 60);    /* orange 50-80% */
  --color-waste-severe:    oklch(0.62 0.20 25);    /* red    > 80% */

  /* PRESSURE PALETTE (Rev 3) — observed suffering. Deliberately a DIFFERENT hue family from waste,
     because "wasteful" and "suffering" must never be confusable at a glance. */
  --color-pressure-none:     oklch(0.45 0.02 260); /* slate — nothing stalled */
  --color-pressure-mild:     oklch(0.70 0.13 320); /* magenta-ish */
  --color-pressure-severe:   oklch(0.58 0.22 350); /* hot pink/red — 'full' stall above threshold */
  --color-pressure-unknown:  oklch(0.40 0.00 0);   /* neutral — NOT OBSERVED, never green */

  /* EVIDENCE TIERS (Rev 3) */
  --color-evidence-rehearsed: oklch(0.72 0.17 150);/* observed on a live pod */
  --color-evidence-modelled:  oklch(0.70 0.16 300);/* violet — forward-looking/derived */
  --color-evidence-partial:   oklch(0.62 0.10 90); /* amber — signals missing */

  /* chart tokens consumed by kubethrifty-charts */
  --chart-actual:        var(--color-primary);
  --chart-requested:     var(--color-border);
  --chart-forecast:      var(--color-evidence-modelled);
  --chart-p95:           var(--color-waste-moderate);
  --chart-peak:          var(--color-waste-severe);      /* the memory sizing basis */
  --chart-pressure-mem:  var(--color-pressure-severe);
  --chart-pressure-cpu:  var(--color-pressure-mild);
  --chart-throttle:      var(--color-waste-moderate);
  --chart-threshold:     var(--color-border);
}
```

Two small helpers keep every surface in agreement:

```typescript
// lib/waste.ts
export function wasteToken(pct: number) {
  if (pct < 0.20) return 'var(--color-waste-efficient)';
  if (pct < 0.50) return 'var(--color-waste-mild)';
  if (pct < 0.80) return 'var(--color-waste-moderate)';
  return 'var(--color-waste-severe)';
}

// lib/pressure.ts — null is a first-class case, not an error
export function pressureToken(psiFull: number | null) {
  if (psiFull === null) return 'var(--color-pressure-unknown)';   // NOT green. We simply don't know.
  if (psiFull < 0.01) return 'var(--color-pressure-none)';
  if (psiFull < 0.05) return 'var(--color-pressure-mild)';
  return 'var(--color-pressure-severe)';
}
```

## Component states that carry meaning

**Recommendation card** — the unit of the product. The variant encodes the action *and* the evidence:

| Action | Treatment |
|---|---|
| `REDUCE` | primary/positive accent, rupee node-delta prominent, "🔻" |
| `INCREASE` | warning accent ("under-provisioned: OOMKill/throttle risk"), "🔺" — shipping these is what makes the tool credible |
| `KEEP` | muted/neutral ("already well-sized") |
| `NEEDS_REVIEW` | amber, de-emphasised CTA ("usage too variable — review") |

Alongside it, three chips that are new in Rev 3:

- **Evidence chip** — `rehearsed` (green, "observed on a live pod, 0 restarts") · `modelled` (violet,
  "percentiles + forecast") · `partial` (amber, "PSI unavailable on these nodes"). Always present.
- **Sizing-basis note** — a one-liner under the numbers: *"memory sized from peak 268Mi × 1.25 (not P95 —
  memory is incompressible)"*. It teaches the reader the rule in the place they see the number.
- **Binding-constraint chip** — "forecast floor bound this request" / "rehearsed floor" / "absolute minimum".

**Rehearsal outcome badge:** `safe` (green) · `regressed` (red) · `inconclusive` (slate — *not* red; an
infeasible resize is information) · `reverted_by_watchdog` (amber, and it should look like something you
would investigate).

**Verdict components:** confidence badge with the number rendered as text ("0.97"), because a calibrated
number is the claim and a colour alone throws it away; attribution chip linking the PR
(*"14h after PR #212"*); rule-ID pill in monospace.

```tsx
// components/dashboard/EvidenceBadge.tsx (copied shadcn Badge + cn())
const EVIDENCE = {
  rehearsed: { label: 'rehearsed', hint: 'observed on a live pod', cls: 'border-[var(--color-evidence-rehearsed)]' },
  modelled:  { label: 'modelled',  hint: 'percentiles + forecast', cls: 'border-[var(--color-evidence-modelled)]' },
  partial:   { label: 'partial',   hint: 'some signals unavailable', cls: 'border-[var(--color-evidence-partial)]' },
} as const;

export function EvidenceBadge({ tier }: { tier: keyof typeof EVIDENCE }) {
  const e = EVIDENCE[tier];
  return <Badge variant="outline" className={cn('gap-1', e.cls)} title={e.hint} aria-label={`evidence: ${e.hint}`}>
    {e.label}
  </Badge>;
}
```

## Forms

**react-hook-form + zod** with the shadcn `Form` primitives: the savings scenario selector (fixed node group
/ Karpenter / Auto Mode), the analysis window picker, and namespace filters. Validate with the same Zod
enums the API uses so the form cannot submit an unsupported value. There is no form anywhere that mutates
cluster state — by design.

## Layout & density

- Shell: fixed dark nav + scrolling content grid. KPI cards `grid-cols-2 lg:grid-cols-4`; the
  waste/pressure quadrant fills the row below with labelled quadrant regions.
- Tables (percentile, rehearsal signals, verdict list) use shadcn `Table` with sticky headers and
  token-based zebra rows. The rehearsal signal table is a before/after two-column layout with a verdict
  column — it is a screenshot people will look at, so give it room.
- Skeletons mirror real card dimensions to avoid layout shift.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| `npm install` a shadcn/ui package | use the CLI to copy components into `components/ui` and edit them |
| `tailwind.config.js` for theme in v4 | CSS-first `@theme` in `globals.css` |
| hardcoded hex for waste/pressure | `wasteToken()` / `pressureToken()` |
| pressure styled with the waste palette | separate hue family — "wasteful" and "suffering" must not look alike |
| `null` pressure rendered green or as 0 | `--color-pressure-unknown` + the words "not observed" |
| recommendation card without an evidence chip | `EvidenceBadge` is mandatory on every card and row |
| confidence shown as colour only | print the number |
| `inconclusive` rehearsal styled as an error | slate/neutral; it is diagnostic information |
| light-mode-first | dark by default, light as toggle |
| className string concatenation | `cn()` (clsx + tailwind-merge) |
| stripping Radix ARIA from copied components | keep the primitives; restyle only |
| chart colours defined apart from UI tokens | define `--chart-*` in the same `@theme` |

## Quick reference

- **Tailwind v4** CSS-first (`@import "tailwindcss"`, `@theme`); **shadcn/ui copied into `components/ui`**.
- Three token families: **waste** (unused reservation), **pressure** (observed suffering, different hue),
  **evidence** (rehearsed / modelled / partial). Plus `--chart-*` shared with kubethrifty-charts.
- `null` → "not observed" (`--color-pressure-unknown`), never zero, never green.
- Recommendation card = action variant + evidence chip + sizing-basis note + binding-constraint chip.
- Rehearsal badges: safe / regressed / inconclusive (neutral) / reverted-by-watchdog (amber).
- Dark by default; `cn()`; preserve Radix a11y; colour is never the only signal.
- Structure → kubethrifty-nextjs-frontend; charts consume these tokens → kubethrifty-charts.
