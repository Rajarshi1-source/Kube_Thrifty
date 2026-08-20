---
name: kubethrifty-python-analyser
description: >-
  Build and maintain the KubeThrifty metric analyser in Python 3.14 with pandas and numpy -- the
  statistical core that reads Prometheus, sizes CPU and memory by DIFFERENT rules, forecasts
  demand, verifies its own merged changes, and opens PRs. Use for ANY analyser work: the
  Prometheus client, PromQL, pandas aggregation, the sizing module (CPU from percentiles, memory
  from the observed PEAK), the three request floors (percentile, forecast, rehearsal-proven), the
  provider-agnostic Forecaster adapter, verification signals comparing throttle RATIOS and OOM
  counters, evidence tiers, cost and manifest generation, PyGithub auto-PRs and rollback PRs, and
  the eval harnesses gating CI on forecast accuracy and sizing DECISIONS. Trigger on Python,
  pandas, Prometheus, PromQL, percentile, P95, memory peak, sizing, forecast, statsforecast,
  sMAPE, eval gate, or PyGithub. MANDATE: Python 3.14; the engine is pure and deterministic;
  memory is NEVER percentile-sized; floors only ever RAISE a request.
---

# Python 3.14 Analyser — KubeThrifty's Statistical Core

You build the brain: it queries Prometheus, aggregates with pandas, sizes resources with hard safety
floors, **forecasts** so it never shrinks a service that is trending up, consumes **rehearsal** evidence
where it exists, prices the waste as a node delta, persists to TimescaleDB, and opens auto-PRs. A second
loop **verifies** a merged change and **rolls it back** if the workload regresses.

## Version mandate

- **Python 3.14** — the current stable line; pandas ≥ 2.3.3 / 3.0.x and NumPy 2.5.x ship cp314 wheels.
  Pin `requires-python = ">=3.14"` and `actions/setup-python` to `3.14`. Python 3.15 arrives Oct 2026 —
  do not pin a beta. Verify `statsforecast` resolves on 3.14 in CI before committing the pin; if it lags,
  3.13 is a defensible fallback and the reason goes in an ADR, not in a comment.
- **Pin pandas and numpy exactly** in `requirements.txt` plus a lockfile. pandas 3.0 has behaviour
  changes versus 2.x; an unpinned `pip install pandas` in CI is a time bomb. Let Renovate open the bumps.
- **statsforecast** (`AutoETS`, `MSTL` for daily+weekly) behind a `Forecaster` adapter. **PyGithub** for
  PRs, **SQLAlchemy** for TimescaleDB, **redis-py** for cache/streams, **httpx** for the Prometheus API,
  **kubernetes** client for pod reads and the `pods/resize` subresource.

## The cardinal architecture rule

> **The sizing engine is pure, deterministic, unit-tested Python. Everything clever — the forecast, the
> rehearsal, the pressure signals — is a FLOOR that can only raise a request, never lower it. The product
> must never break, or produce a dangerous number, because a clever component was unavailable.**

```
PromQL -> pandas aggregation (P50/P95/P99, peak, stddev)  +  PSI / cgroup truth (evidence layer)
                      |
                      v
        sizing.py  (PURE)      size_cpu()   <- percentile-based, throttling is survivable
                               size_memory() <- PEAK-based, OOMKill is not
                      |
        request = max(statistical floor, forecast upper bound, rehearsal-proven floor, absolute min)
                      |
                      v
        coupling guards (HPA simulation, QoS transition)  ->  cost/packing (node delta)
                      |
                      v
        manifest generator -> PyGithub PR   +   TimescaleDB / Redis persistence
```

## CPU and memory are not symmetric (the most important rule in the project)

| | CPU | Memory |
|---|---|---|
| Over-limit behaviour | CFS **throttling** — the app gets slower, then recovers | Kernel **OOMKill** — the app dies |
| Correct statistic | P95 (+ margin), P99 for the limit | **Observed peak** (cgroup `memory.peak`), + margin |
| Limit posture | Generous multiple of the request, or no limit at all (a tight CPU limit converts free node capacity into latency) | `limit == request` by default: predictable allocation, bottom of the eviction preference order, and no QoS demotion |
| Why not a percentile for memory | — | A percentile **by construction discards the top 5% of samples**, and those are exactly the samples that kill you |

Defaults: `CPU_REQUEST_MARGIN = 1.20` over P95, `CPU_LIMIT_MARGIN = 1.50` over P99,
`MEM_REQUEST_MARGIN = 1.25` over peak, `MEM_HEADROOM_FOR_GC = 1.10` for GC'd runtimes (JVM/Node/dotnet,
where RSS lags real demand), `MIN_CPU = 50m`, `MIN_MEMORY = 64Mi`, `MIN_DATA_POINTS = 168`,
`MIN_SAVINGS_THRESHOLD = 0.10`. Full module, including the `binding_constraint` reporting and the
OOM/PSI hard stops, is in `references/sizing-rules.md` — read it before touching sizing maths.

## Floors compose with `max()`, never `min()`

```python
request = max(
    statistical_floor,        # P95 × margin (cpu) or peak × margin (memory)
    predictive_floor,         # forecast upper bound, iff the forecast is confident
    rehearsed_floor,          # a value proven safe on a live pod (kubethrifty-resize-rehearsal)
    absolute_minimum,         # 50m / 64Mi
)
```

Record which floor won as `binding_constraint` and print it in the PR body. When the forecast says "grow"
and a rehearsal said "this smaller size was fine," the forecast wins — it is a statement about the future,
the rehearsal about the observed present. Never take a minimum of safety signals.

## Evidence tiers — never overstate what you know

| Tier | Meaning | Where it comes from |
|---|---|---|
| `rehearsed` | The candidate ran on a live pod and nothing regressed | kubethrifty-resize-rehearsal |
| `modelled` | Percentiles + forecast only | this skill |
| `partial` | PSI or cgroup signals unavailable on those nodes | kubethrifty-cgroup-psi-signals |

Stamp the tier on the recommendation row, render it in the UI, and state it in the PR. A recommendation
that could not be rehearsed says *why* (`single-replica workload`), rather than implying an experiment that
never happened.

## The Forecaster adapter (unchanged design, still correct)

```python
# forecasting/types.py
@dataclass
class Forecast:
    horizon_hours: int
    point: pd.Series          # predicted mean per future hour
    upper: pd.Series          # upper bound of the interval (e.g. 90%) — used as the FLOOR
    model_id: str             # "statsforecast:AutoETS" — provenance on every recommendation
    confident: bool           # False -> caller falls back to the trailing statistical floor

class Forecaster:
    """Default wraps statsforecast; swap for Prophet/cloud without touching the engine."""
    def forecast(self, history: pd.Series, horizon_hours: int) -> Forecast: ...
```

`MIN_POINTS = 24 * 14` — below two weeks of hourly data, return `confident=False` and let the caller fall
back. That fallback is a product guarantee, not an error path.

## Verification signals — compare ratios, not counters

Rev 3 correction: throttled-*period counters* scale with window length, replica count and the CFS period,
so comparing raw counts across a before/after window produces false rollbacks and missed regressions.

```python
THROTTLE_RATIO_DELTA = 0.05    # +5 percentage points of throttled periods
THROTTLE_RATIO_FLOOR = 0.02    # ...and must exceed 2% absolute (kills 0.001 -> 0.004 noise)
PSI_FULL_CEILING = 0.05        # 5% of wall time fully stalled == regressed

@property
def throttle_ratio(self):      # on the Window aggregate
    return self.throttled_periods / self.cfs_periods if self.cfs_periods else 0.0
```

Trip on: OOM increase (by **counter**: `container_oom_events_total` / cgroup `memory.events`, never the
flapping `last_terminated_reason` gauge) · throttle-ratio delta ≥ 5pp **and** ratio ≥ 2% · any restart ·
PSI `full` above 5% · optional SLO burn-rate breach. The same `evaluate(before, after)` function serves
post-merge verification **and** the rehearsal — one implementation, one set of thresholds.

On regression: open a **rollback PR** restoring the previous values, mark `regressed=true`, widen *that
workload's* margin with a reason and a date, and hand the bundle to kubethrifty-thrift-detective for
attribution.

## The MLOps wrapper — three CI gates, not one

| Gate | Script | Fails the build when |
|---|---|---|
| Forecast accuracy | `evals/forecast_eval.py` | mean sMAPE or 90% interval coverage regress against `baseline.json` |
| **Sizing decisions** | `evals/sizing_eval.py` | a labelled fixture's *decision* changes — e.g. `low_util_high_pressure` produces a cut, `spiky_peak_gt_p95` sizes memory below peak, `throttled_at_current_limit` lowers the CPU limit |
| Detective accuracy + calibration | `evals/detective_eval.py` | top-1 accuracy, Brier, ECE, or the determinism digest regress |

The sizing gate is the one freshers never have: it asserts on **decisions**, not floats, so a refactor that
quietly re-enables percentile-based memory sizing fails like a unit test. Fixtures, harnesses, and
baselines ship as the **starter** (`kubethrifty-starter`); add pod shapes there and re-baseline only when
you genuinely improve something.

## Resilience around flaky externals

Timeout bounds one call; retry with exponential backoff and **full jitter** handles a blip; a circuit
breaker handles a sustained Prometheus/GitHub outage; a Redis `SET NX` lock on `analysis:lock:{cluster}`
guarantees one run per cluster; runs are idempotent on `(cluster, window, started_at_bucket)` and PR
creation is idempotent on branch name, so a redelivered stream job never double-writes or opens a second
PR; a job past max retries goes to `analysis-jobs-dlq`. The most important property: **a degraded run
produces no recommendation, never a wrong one.**

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| memory sized as `P95 × margin` | `peak × margin` (cgroup `memory.peak`, else `max_over_time`) |
| memory `limit > request` by default | `limit == request` for memory; guard QoS transitions |
| CPU limit set tight to "save money" | limits do not affect cost; a tight CPU limit only buys throttling |
| `from statsforecast import ...` inside the engine | go through the `Forecaster` adapter; the engine imports `types` only |
| forecast used as the request directly | it is a **floor** inside `max()` |
| `min()` anywhere in floor composition | `max()` — floors only raise |
| comparing throttled-period counts across windows | compare ratios |
| OOM via `last_terminated_reason` gauge | `container_oom_events_total` counter |
| recommending a cut while PSI shows stalls | hard stop; surface as an *increase* candidate instead |
| right-sizing with `< MIN_DATA_POINTS` samples | skip — never guess |
| calling a modelled recommendation "verified" | evidence tiers: `rehearsed` / `modelled` / `partial` |
| reporting per-pod rupees as savings | node delta only (kubethrifty-cost-and-packing) |
| `python:3.12` / `3.13` base image | `python:3.14-slim`, non-root |

## Quick reference

- **Python 3.14**, pandas/numpy pinned exactly; statsforecast behind the `Forecaster` adapter.
- CPU: P95 × 1.20, limit P99 × 1.50 (generous). Memory: **peak** × 1.25 (+GC headroom), `limit == request`.
- `request = max(statistical, forecast_upper, rehearsed, absolute_min)`; record `binding_constraint`.
- Evidence tiers `rehearsed` > `modelled` > `partial`; never upgrade what you did not observe.
- Verification: ratios, PSI, OOM counters → rollback PR + margin widening + detective bundle.
- Three CI gates: forecast accuracy · **sizing decisions** · detective accuracy/calibration.
- Sizing depth → `references/sizing-rules.md`; signals → kubethrifty-cgroup-psi-signals; experiments → kubethrifty-resize-rehearsal; money → kubethrifty-cost-and-packing; infra → kubethrifty-devops-k8s; API → kubethrifty-nextjs-backend.
