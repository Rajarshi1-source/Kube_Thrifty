# KubeThrifty Starter — Rev 3

A runnable slice of the three parts of KubeThrifty that are hardest to get right, each wrapped in a CI
gate that can fail a build:

1. **The sizing decision** — and specifically the rule that CPU and memory are **not** symmetric.
2. **The forecast** that floors a downsize, so a growing service is never shrunk to last week's P95.
3. **The incident verdict** — deterministic, attributed to the change that caused it, and with
   **confidence numbers that are calibration-tested**.

Everything here runs with `numpy` + `pandas` only. No cluster, no Prometheus, no network, no API key.

```bash
pip install -r requirements.txt
cd evals
python recommendation_eval.py   # gate 1: categorical correctness of the CPU engine
python sizing_eval.py           # gate 2: Rev 3 sizing DECISIONS (memory peak, pressure, floors)
python forecast_eval.py         # gate 3: forecast accuracy (sMAPE + interval coverage vs baseline)
python detective_eval.py        # gate 4: verdict accuracy + calibration + determinism
```

All four are wired into `.github/workflows/eval.yml` and exit non-zero on regression.

---

## What changed from Rev 2 of this starter

Rev 2 sized **both** CPU and memory as `P95 × 1.20`. That is correct for CPU and **wrong for memory**, and
the difference is the single most important idea in the project:

| | CPU | Memory |
|---|---|---|
| Over-limit behaviour | CFS **throttling** — slower, then recovers | Kernel **OOMKill** — the process dies |
| Correct statistic | P95 × margin (P99 for the limit) | **Observed peak** × margin |
| Limit posture | generous multiple of the request | `limit == request` |
| Why | throttling is survivable, so a percentile is fine | a percentile **discards the top 5% of samples** — exactly the ones that kill you |

`evals/sizing.py` implements that asymmetry. `evals/recommendation.py` is kept **unchanged** as the Rev 2
CPU-only engine so you can diff the two and explain the correction in an interview; new work should use
`sizing.py`.

Two more Rev 3 rules are encoded here:

- **Floors compose with `max()`, never `min()`.** The statistical floor, the forecast upper bound and a
  rehearsal-proven floor can only ever *raise* a request. `binding_constraint` records which one won.
- **Observed suffering is a hard stop.** An OOM event in-window, or PSI showing sustained stall, blocks a
  reduction outright — no soft penalty, no "reduce a bit less".

---

## Gate 2: the sizing decision gate (`sizing_eval.py`)

Nine labeled cases in `evals/fixtures/sizing_cases.json`. The assertions are **categorical** — action,
which floor bound the request, whether a reduction was blocked, and invariants like `request_at_least` /
`no_reduction_below` / `limit_equals_request`. Float tolerances drift; decisions don't.

Two cases carry the message:

| Case | What it proves |
|---|---|
| `spiky_peak_gt_p95` | memory whose peak (620Mi) sits far above its P95 (240Mi) must be sized off the **peak**. A percentile-based sizer passes every float test here and still ships an OOMKill |
| `low_util_high_pressure` | a workload with low average usage **and** 11% memory PSI stall must not be cut — it needs *more*. This is the case every percentile-only tool shrinks into an outage |

The second case's label was **corrected during development**: the first label said `keep`, and the engine
answered `increase`, because a 470Mi peak against a 512Mi limit plus sustained stall means the workload is
genuinely under-provisioned. The engine was right and the label was wrong. That is what a decision gate is
for.

---

## Gate 3: the forecast gate (`forecast_eval.py`)

Backtests a 72-hour holdout on each fixture, scores **sMAPE** and **90% interval coverage**, asserts the
fallback contract, and compares against the committed `baseline.json` (exit 1 on regression).

Two fixtures carry the message: `rising_trend` is the only one where `forecast_floor_applied` flips to
`true` — the regression test proving the ML layer **changes a decision** rather than decorating one — and
`noisy_short` proves the safety contract (bad data → `confident=false` → deterministic trailing-P95 +
`needs_review`, never a confident auto-PR).

The starter runs the dependency-free `SeasonalNaive` path (linear trend + hour-of-week profile + empirical
residual interval), which is also the product's real fallback. Install `statsforecast`, re-run with
`--bootstrap`, and commit the AutoETS baseline when you want production numbers.

---

## Gate 4: the detective gate (`detective_eval.py`)

`evals/detective/engine.py` is a five-rule slice of ThriftDetective: `MEM_LIMIT_TOO_LOW`,
`MEM_PRESSURE_NO_KILL`, `CPU_LIMIT_TOO_LOW`, `HPA_COUPLING_STORM`, `RESIZE_INFEASIBLE_STUCK`. Anything
else returns **no verdict** with an explicit scope statement rather than a low-confidence guess.

`investigate(bundle)` is a **pure function** — no network, no clock, no randomness, no LLM. That is what
makes verdicts replayable offline and hashable in CI.

| Assertion | Threshold | Why it matters |
|---|---|---|
| Top-1 accuracy | ≥ 0.90 | the verdicts are right |
| **Brier score** | ≤ 0.08 | the confidence numbers are honest |
| **Expected Calibration Error** | ≤ 0.10 | when it says 0.9, it is right about 90% of the time |
| Determinism digest | exact match | the engine has not silently become non-deterministic |

Current run: **24 cases · accuracy 0.917 · Brier 0.0699 · ECE 0.0610 · deterministic ✅**

### The corpus is generated, and deliberately not all-textbook

`python corpus/generate.py --freeze` rebuilds `incidents.json` and re-anchors `digests.json`. In the real
project the same generators drive a `kind` cluster and induce each failure mode (chaos harness); here the
bundle *shape* is emitted deterministically so CI stays hermetic.

Six cases have ground truth "no verdict": four red herrings the engine must refuse (image pull, a DNS
failure shortly after one of *our* changes, a bad-config crash loop, a healthy pod) and **two deliberate
misfires** the engine gets wrong and is penalised for:

- `thumbnailer__noisy_neighbour` — the stall came from another pod exhausting the node, not from this
  container's limit. The starter slice has no `NODE_PRESSURE_EVICTION` rule, so it answers
  `MEM_PRESSURE_NO_KILL` at 0.62 and loses points. **The corpus is telling you which rule to write next.**
- `video-encoder__harmless_throttle` — a batch encoder with no latency SLO is throttled and does not care.
  The engine answers `CPU_LIMIT_TOO_LOW` at 0.72 and loses points, which is the honest cost of not knowing
  whether a workload has an SLO.

Those two exist because an all-obvious corpus drives accuracy to 1.0 while mean confidence sits near 0.9 —
and the calibration gate correctly reports that as **underconfidence**. The first version of this corpus
failed on exactly that (ECE 0.1125), which was the most useful thing the harness did all day.

**Only re-freeze `digests.json` after looking at Brier/ECE.** Re-freezing blindly defeats the gate; say why
in the commit message.

---

## Layout

```
evals/
  sizing.py                     # Rev 3: size_cpu (percentile) + size_memory (PEAK), floors via max()
  sizing_eval.py                # gate 2 — categorical decision assertions
  fixtures/sizing_cases.json    # 9 labeled sizing decisions
  recommendation.py             # Rev 2 CPU-only engine, kept for the before/after story
  recommendation_eval.py        # gate 1
  forecaster.py                 # provider-agnostic Forecaster + dependency-free fallback
  forecast_eval.py              # gate 3 — sMAPE + coverage vs baseline.json
  generate_fixtures.py          # regenerates the CPU time-series fixtures (seeded)
  fixtures/*.csv, labels.json   # 6 labeled CPU histories (504h hourly)
  baseline.json                 # committed forecast baseline
  detective/engine.py           # 5-rule pure verdict engine + bundle/verdict hashing + explain()
  detective_eval.py             # gate 4 — accuracy + Brier + ECE + determinism
  corpus/generate.py            # manufactures the labeled corpus (--freeze anchors the digests)
  corpus/incidents.json         # 24 labeled incidents (6 no-verdict, 2 of them deliberate misfires)
  corpus/digests.json           # determinism anchor, keyed by ruleset version
```

## Try the narration

```bash
cd evals
python - <<'PY'
import json, sys; sys.path.insert(0, 'detective')
from engine import explain
corpus = json.load(open('corpus/incidents.json'))
case = next(c for c in corpus if c['name'] == 'payment-processor__oom_squeeze')
print(explain(case['bundle']))
PY
```

```
MEM_LIMIT_TOO_LOW (99%): Container was OOMKilled; the memory limit is below real demand.
ATTRIBUTED to merged_pr https://github.com/OWNER/REPO/pull/212 14.5h before
({'memory': '1Gi -> 320Mi'}). -> Raise memory request/limit to peak x 1.25 and mark the
workload reduction-blocked for 14 days.
```

That sentence — the verdict naming the pull request that caused the incident — is the thing no
general-purpose investigator can produce, because none of them owns the change log.

## Interview soundbites this package earns

- *"My sizing logic has an eval gate that asserts decisions, not numbers. Two of its cases exist because a
  percentile-based sizer passes every float test and still ships an OOMKill."*
- *"Once, the label was wrong and the engine was right. I fixed the label and left the story in the README."*
- *"My incident engine's confidence scores are graded in CI with Brier score and expected calibration
  error. My first corpus failed calibration for being too easy, so I added cases the engine gets wrong."*
- *"Same evidence bundle, same verdict, forever — and CI hashes the whole corpus to prove the engine hasn't
  drifted."*
