---
name: kubethrifty-thrift-detective
description: >-
  Build ThriftDetective -- KubeThrifty's deterministic, change-attributed incident investigator,
  which answers why a workload regressed and whether KubeThrifty's own change caused it. Use for
  ANY work on verdicts or evidence: the pure rule engine over an evidence bundle, the eight
  resource-shaped failure modes (limit too low, memory pressure without an OOM kill, node-pressure
  eviction, QoS demotion, HPA coupling storm, startup starvation, resize infeasible), attribution
  against KubeThrifty's own merged PRs and resize events, signed content-addressed bundles,
  offline replay, confidence CALIBRATION gated in CI (Brier, ECE), the determinism digest, and the
  chaos-generated corpus with red herrings. Trigger on incident investigation, root cause,
  verdict, calibration, Brier, ECE, evidence bundle, replay, thriftctl, rule engine, chaos corpus,
  or KubeTective. MANDATE: verdict(bundle) is pure and deterministic -- no network, clock,
  randomness, or LLM in the decision path; out-of-scope incidents return NO_VERDICT.
---

# ThriftDetective — Deterministic, Change-Attributed Investigation

KubeThrifty's verification loop can tell you a workload regressed. ThriftDetective tells you **why, with
evidence, and whether KubeThrifty itself caused it** — then acts on that conclusion. It closes the loop:

```
detect → propose → rehearse → approve → verify → investigate → attribute → learn
```

## Scope discipline first (this is the design decision, not a limitation)

General-purpose Kubernetes investigators (k8sgpt, HolmesGPT, and the deterministic rule-engine tools in
this space) cover image pulls, DNS, RBAC, admission webhooks, PVC binding, network policy. **Do not clone
them.** That is a different product, a crowded category, and you would be its weakest entrant.
ThriftDetective covers only what *resource decisions* can produce:

| Rule ID | Failure mode | Primary evidence | Can a KubeThrifty change cause it? |
|---|---|---|---|
| `MEM_LIMIT_TOO_LOW` | OOMKill | `memory.events.oom_kill` ↑, `memory.peak` ≈ `memory.max`, exit 137 | **Yes** — must never be silent |
| `CPU_LIMIT_TOO_LOW` | Latency from throttling | throttle ratio ↑, `cpu.pressure full` ↑, no OOM | **Yes** |
| `MEM_PRESSURE_NO_KILL` | Reclaim thrash — slow, not dead | `memory.pressure full` ↑, `memory.current` pinned near max, usage *looks* fine | **Yes** — invisible to percentile tools |
| `NODE_PRESSURE_EVICTION` | Evicted for node-level pressure | node conditions, eviction event, sibling usage | Contributory (over-commit) |
| `QOS_DEMOTION_EVICTION` | Evicted earlier than expected | QoS class before/after, eviction ordering | **Yes** |
| `HPA_COUPLING_STORM` | Replica oscillation / cost spike after a request change | replica timeline vs request change, HPA events | **Yes** |
| `STARTUP_STARVATION` | Probe failures at boot only | failures inside the startup window, startup peak ≫ steady | **Yes** |
| `RESIZE_INFEASIBLE_STUCK` | Resize stuck `Deferred`/`Infeasible` | pod conditions, node allocatable headroom | **Yes** — self-inflicted by the rehearsal path |

Anything else returns **`NO_VERDICT`** with *"outside ThriftDetective's scope — this is not a
resource-shaped incident."* Knowing what your tool refuses to opine on is a seniority signal, and a
confident wrong answer costs more trust than an honest abstention.

## Architecture

```
EVIDENCE COLLECTION (read-only, bounded)
  pod status/conditions/exit codes/events · PSI + cgroup truth · throttle/OOM/restart windows
  · KubeThrifty's OWN change log: recommendations, merged PRs, rehearsals, resize events
        ↓
EVIDENCE BUNDLE — immutable, content-addressed JSON; sha256 IS the incident identity
        ↓
RULE ENGINE — pure function, no I/O, no LLM: bundle -> [Verdict(rule, confidence, evidence, attribution)]
        ↓
PR comment / Kubernetes Event / dashboard timeline · recommendation-engine feedback (margin learning)
· optional narrative (LLM adapter that phrases, never decides)
```

## Critical rules (never violate)

- **`investigate(bundle)` is pure.** No network calls, no `datetime.now()`, no randomness, no model
  inference. Same bundle in, byte-identical verdicts out, forever. That property is what makes verdicts
  replayable, unit-testable, and CI-hashable — and it is the single biggest architectural difference from
  LLM-based investigators.
- **Collection and judgement are separate phases.** Collectors touch the cluster; the engine touches only
  the bundle. If you find yourself calling the Kubernetes API inside a rule, you have broken the contract.
- **Attribution is the moat.** Every rule calls the attributor, which correlates the incident with
  KubeThrifty's own change log inside a window (default 48h) on the same container. A verdict that names
  *"PR #212, recommendation #1188, memory 1Gi → 320Mi, 14h before"* is something no external tool can
  produce, because no external tool owns the change log.
- **Act, then learn — auditably.** On an attributed regression: open the rollback PR, mark the workload
  reduction-blocked, and widen *that workload's* margin in a plain table with a reason and a timestamp.
  Never a learned model — *"I widened this margin because it OOMKilled at 1.25×"* is a sentence a reviewer
  can check.
- **Confidence must be calibrated or it is decoration.** When the engine says 0.9 it must be right about
  90% of the time. Gate CI on Brier score and expected calibration error against the labelled corpus, and
  re-check calibration whenever a rule's confidence arithmetic changes.
- **The LLM never decides.** Compute the verdict first, then optionally narrate. Validate the narration
  against the verdict (drop it if it mentions a rule that is not in the verdict list) and fall back to the
  deterministic template. The product must work with no API key and no network.
- **Bundles are immutable and content-addressed.** `sha256` of the canonical JSON (sorted keys, tight
  separators) is the incident ID; it links `rehearsals`, `verdicts`, and `recommendations`. Sign bundles
  and images with cosign keyless in CI and link the attestation from the PR.

## Rule shape

```python
RULESET_VERSION = "1.3.0"

@rule
def mem_pressure_no_kill(b: dict) -> Verdict | None:
    """The one nobody else catches: reclaim thrash without an OOM kill."""
    psi = b["signals"].get("psi_mem_full", 0.0)
    if psi < 0.05 or b["signals"]["oom_kills"] > 0:
        return None                                  # not this rule's incident
    cur, limit = b["cgroup"].get("memory_current", 0), b["cgroup"].get("memory_max") or 0
    near_limit = bool(limit) and cur / limit > 0.90
    return Verdict(
        "MEM_PRESSURE_NO_KILL", 0.88 if near_limit else 0.62,
        "Sustained memory stall without an OOM kill — the workload is thrashing reclaim, so average "
        "usage understates real demand.",
        {"psi_mem_full": psi, "memory_current": cur, "memory_max": limit},
        _attribute(b),
        "Increase memory; do NOT size this workload from percentile usage.",
    )
```

Every rule: return `None` when it does not apply (never a 0.1-confidence verdict), put the numbers that
justified the call in `evidence`, and pass `_attribute(b)` through. Rank by confidence; report all
matching rules, not just the top one — co-occurring `MEM_LIMIT_TOO_LOW` + `NODE_PRESSURE_EVICTION` is a
different story from either alone.

## The corpus is generated, not collected

A hand-curated set of ~16 real incidents is a ceiling. A chaos harness is a factory: drive a `kind`
cluster, induce each failure mode deliberately, and emit `(bundle, ground_truth)` pairs. Commit the
bundles so CI replays them **hermetically** — no cluster in CI; the cluster is only needed to regenerate.

Target ≥ 25 cases across the 8 rules, including **at least three red herrings** (image-pull failure, DNS
failure, healthy resources) whose ground truth is `NO_VERDICT`. A tool that never says "I don't know"
cannot be trusted when it does say something.

## CI gates

Three, all in one script (`evals/detective_eval.py`), all exit-1 on failure:

1. **Accuracy** — top-1 verdict matches ground truth on ≥ 90% of the corpus.
2. **Calibration** — Brier score ≤ 0.08 and ECE ≤ 0.10 over the confidence/correctness pairs.
3. **Determinism** — the sha256 digest of all corpus verdicts equals the committed digest for this
   `RULESET_VERSION`.

> Interview gold: *"My investigator has a test suite, and the test suite scores the confidence numbers,
> not just the verdicts. CI fails if 'confidence 0.9' stops meaning 'right 90% of the time,' and it fails
> if the engine stops being deterministic. The corpus is chaos-generated, so I can add a failure mode and
> have twenty labelled cases the same afternoon — including red herrings the engine is required to
> refuse."*

## Interfaces

One engine, several front doors, identical output: Python library · `thriftctl investigate` /
`thriftctl replay <sha256>` (offline, no cluster) · `kubectl thrift investigate pod/x -n shop` via a
symlink-named binary · in-cluster server posting PR comments, Kubernetes Events, and the dashboard
timeline. Replaying a production incident on a laptop with the cluster disconnected and getting the
identical verdict is the demo moment.

## Depth

`references/rules-and-calibration.md` carries the engine skeleton, the attributor, all eight rules, the
bundle schema, the calibration/determinism harness, the chaos generators, and the competitive comparison
grid. Read it before writing or changing rules, confidence arithmetic, or the corpus.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| Kubernetes API call inside a rule | collect into the bundle first; rules are pure |
| `datetime.now()` / randomness in the engine | pass timestamps in the bundle; determinism is the contract |
| LLM produces or edits the verdict | verdict first, narration second, validated against the verdict |
| a low-confidence guess for an out-of-scope incident | `NO_VERDICT` with a scope explanation |
| confidence constants nobody measures | calibration gate (Brier + ECE) in CI |
| hand-collected corpus that never grows | chaos generators emitting labelled bundles, red herrings included |
| verdict that stops at "OOMKilled" | attribute it: which PR, which recommendation, how long before |
| widening every workload's margin after one incident | widen *that workload's* margin, with a reason and a date |
| mutable "investigation report" rows | immutable content-addressed bundles; verdicts reference the sha |

## Quick reference

- Pure `investigate(bundle) -> [Verdict]`; 8 resource-shaped rules; everything else `NO_VERDICT`.
- Attribution against KubeThrifty's own change log is the moat; then act (rollback PR) and learn (margin table).
- Bundles: canonical JSON, sha256 identity, cosign-signed, replayable offline.
- CI gates: accuracy ≥ 0.90 · Brier ≤ 0.08 · ECE ≤ 0.10 · determinism digest match.
- Corpus: ≥ 25 chaos-generated labelled cases, ≥ 3 red herrings.
- Signals → kubethrifty-cgroup-psi-signals; rehearsal counterfactuals → kubethrifty-resize-rehearsal; code depth → `references/rules-and-calibration.md`.
