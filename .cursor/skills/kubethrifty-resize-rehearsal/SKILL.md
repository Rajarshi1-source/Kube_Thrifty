---
name: kubethrifty-resize-rehearsal
description: >-
  Build KubeThrifty's headline differentiator -- the Resize Rehearsal, which applies a candidate
  right-sizing to ONE live pod in place with zero restarts, observes kernel pressure signals, then
  always reverts, so the pull request carries evidence instead of a prediction. Use for ANY
  in-place resize or rehearsal work: the pods/resize subresource (and why the Python client needs
  call_api), PodResizePending Deferred vs Infeasible, resizePolicy, desired vs actual resources,
  preflight, compensation-first revert deadlines and the watchdog CronJob, Leases, kill switches,
  minimal pods/resize RBAC, warm-up two-phase sizing, and the PR evidence table. Trigger on
  in-place resize, resize subresource, InPlacePodVerticalScaling, PodResizePending, Infeasible,
  resizePolicy, rehearsal, zero restart, or watchdog. MANDATE: needs Kubernetes 1.35+; a rehearsal
  is an experiment that always reverts; compensation is persisted before the mutation; blast
  radius is one pod. Pair with kubethrifty-cgroup-psi-signals.
---

# Resize Rehearsal — Zero-Restart In-Cluster Experiments

Every right-sizing tool in existence produces a **prediction**: *"P95 says 200m is enough."* KubeThrifty
produces an **experiment**:

> *"I set this pod to 250m CPU / 320Mi for 30 minutes under real production traffic. It served 41k
> requests, was throttled 0.4% of periods (baseline 0.3%), recorded zero OOM events, memory peaked at
> 268Mi, memory PSI `full` stayed at 0.0%, and the container never restarted. Here is the evidence
> bundle. Now — should we merge it?"*

This was **not buildable before Kubernetes 1.35**. Until in-place pod resize went GA, changing a pod's
resources meant recreating it, so a "trial" meant a restart, and nobody trials in production with
restarts. Build on that GA primitive; the incumbents have not caught up to it yet.

## Mechanics you must get right

```bash
# Resources are patched through the resize SUBRESOURCE, not the pod spec.
kubectl patch pod payment-processor-7d9f -n shop --subresource resize --patch \
  '{"spec":{"containers":[{"name":"app","resources":{
      "requests":{"cpu":"250m","memory":"320Mi"},
      "limits":{"cpu":"1","memory":"320Mi"}}}]}}'

# What the container ACTUALLY has now (assert on this, never on spec):
kubectl get pod payment-processor-7d9f -n shop \
  -o jsonpath='{.status.containerStatuses[0].resources}{"\n"}'

# Why a resize is stuck:
kubectl get pod payment-processor-7d9f -n shop \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason} {.message}{"\n"}{end}'
```

| Concept | The detail that matters |
|---|---|
| `spec.containers[*].resources` | **Desired**; mutable for CPU/memory via the subresource |
| `status.containerStatuses[*].resources` | **Actual** resources on the running container — the only thing worth asserting on |
| `PodResizePending` | Accepted but not applied. `reason: Deferred` (no room *now*, may clear) or `reason: Infeasible` (impossible on this node, will not clear). Neither is an error, and **a resize can stay pending indefinitely** — so the runner needs a timeout, never a wait-forever |
| `PodResizeInProgress` | kubelet is applying it |
| CPU resize | Applies transparently, **no restart** |
| Memory resize | Updates the cgroup in place **unless** the container declares `resizePolicy: RestartContainer` for memory. Shrinking below current usage is where the kernel's opinion matters — which is exactly why the rehearsal watches OOM events and PSI |
| kubectl | `--subresource` needs kubectl ≥ 1.32 |
| Template vs pod | An in-place resize **does not** change the Deployment template, so the next rollout undoes it. That is a *feature* for a rehearsal (self-cleaning) and the reason the durable change still needs a PR |
| Pod-level | `PodLevelResources` is beta since 1.34 and `InPlacePodLevelResourcesVerticalScaling` is beta in 1.36 — gate pod-level rehearsals behind a Helm value and say "beta" out loud |

The official Python client does not expose the `resize` subresource as a typed method. Call it through
the generic path — and mention this in an interview, because "the SDK hasn't caught up to the GA API" is
the kind of friction a real 2026 implementation hits:

```python
self.api.call_api(
    "/api/v1/namespaces/{namespace}/pods/{name}/resize", "PATCH",
    path_params={"namespace": ns, "name": pod},
    body={"spec": {"containers": [{"name": container,
                                   "resources": {"requests": requests, "limits": limits}}]}},
    header_params={"Content-Type": "application/strategic-merge-patch+json",
                   "Accept": "application/json"},
    auth_settings=["BearerToken"], _preload_content=True,
)
```

## The state machine

```
candidate sizing (analyser)
   -> PREFLIGHT            fail -> skip: ship modelled evidence only
   -> RECORD compensation + revert_deadline in the DB      (BEFORE touching the cluster)
   -> BASELINE window      collect signals_before
   -> RESIZE (subresource) Infeasible/timeout -> INCONCLUSIVE
   -> OBSERVE window       collect signals_after (PSI, throttle ratio, OOM, restarts)
   -> verdict SAFE | REGRESSED
   -> REVERT in place (ALWAYS), assert actual == original
      SAFE      -> candidate becomes the `rehearsed` floor; evidence table -> PR body
      REGRESSED -> widen that workload's margin, record the negative result, re-plan
```

**The rehearsal always reverts.** It is an experiment, not an actuation. The only thing that changes the
cluster durably is a merged PR.

## Critical rules (never violate)

- **Compensation before action.** Write the revert payload (`original_requests`/`original_limits`) and a
  `revert_deadline` to the database *before* issuing the resize. A crashed runner must never be able to
  leave a pod at experimental sizes. This is a saga with a persisted compensating transaction, and
  saying that out loud is worth a lot.
- **An independent watchdog owns the deadline.** A separate CronJob (not the runner process) sweeps
  `revert_deadline < now()` and reverts, marking the row `reverted_by_watchdog` and firing an alert.
  Export `kubethrifty_rehearsals_watchdog_reverts_total` — a non-zero value is a bug worth paging on.
- **Preflight or don't run.** Refuse unless: rehearsals enabled globally; namespace not labelled
  `kubethrifty.io/tier: critical`; workload not annotated `kubethrifty.io/rehearsal: disabled`;
  `replicas >= 2`; no rollout in progress; PDB tolerates one disruption; no VPA in `Auto` /
  `InPlaceOrRecreate` mode owning the workload; cluster concurrency cap not reached.
- **One pod, per workload, at a time.** Take a `coordination.k8s.io` `Lease` named per workload. Cap
  concurrent rehearsals per cluster via a Helm value (default 3).
- **Assert on `status`, poll with a deadline.** Success is "actual resources equal the candidate," not
  "the PATCH returned 200." `Infeasible` ends the rehearsal immediately; `Deferred` ends it at timeout.
  Either way the outcome is `INCONCLUSIVE` — never `SAFE`.
- **Memory rehearsals default to `resizePolicy: NotRequired`** and refuse targets that declare
  `RestartContainer` for memory unless explicitly opted in — otherwise a "zero-restart" rehearsal
  restarts the container, which destroys the whole claim.
- **Minimal RBAC is the safety story.** The only mutating verb KubeThrifty holds anywhere is `patch` on
  `pods/resize`. It cannot delete a pod, edit a Deployment, or read a Secret. Blast radius is enforced
  by RBAC, not good intentions.

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/resize"]        # the ONLY mutating permission in the cluster
    verbs: ["patch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets"]
    verbs: ["get", "list"]
  - apiGroups: ["policy"]
    resources: ["poddisruptionbudgets"]
    verbs: ["get", "list"]
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["get", "create", "update"]
```

## Verdict signals (borrowed, not reinvented)

The rehearsal reuses the same signal module as post-merge verification — ratios over equal windows, PSI,
OOM counters, restart delta. See kubethrifty-cgroup-psi-signals for the metric definitions and
kubethrifty-python-analyser for `evaluate(before, after)`. Trip on any of: OOM increase; throttle-ratio
delta ≥ 5 points **and** absolute ratio ≥ 2%; any restart; PSI `full` (cpu or memory) above 5% of wall
time; optional SLO burn-rate breach.

## Full implementation

`references/rehearsal-implementation.md` carries the complete runner, preflight, watchdog, DB schema for
the `rehearsals` table, and the PR-body evidence template. Read it when writing or changing any of that
code — it is the part where the details (polling, revert-on-exception, UID pinning) decide whether the
feature is safe.

## Warm-up two-phase sizing (advisory only)

In-place resize dissolves the startup-versus-steady-state conflict that forces every percentile tool to
over-provision GC'd runtimes permanently:

```
t=0    pod starts on the STARTUP profile   (e.g. 1000m CPU)
t=~45s readiness passes and the warmup metric settles
t=~60s in-place resize down to the STEADY profile (e.g. 250m) — no restart, no rollout
```

Ship this as a **recommendation type** (`two_phase`) carrying both profiles and the measured trigger
condition — **not** as a controller. Building a general startup-boost controller is a separate product;
measuring and proposing it is nearly free once the signals exist, and it is a genuinely novel
recommendation class.

## Anti-patterns to fix on sight

| Anti-pattern | Fix |
|---|---|
| patching `pod.spec.resources` directly | use the `resize` subresource |
| asserting the resize landed because PATCH returned 200 | poll `status.containerStatuses[*].resources` |
| waiting indefinitely on `PodResizePending` | bounded timeout → `INCONCLUSIVE` |
| revert only on the happy path | revert in `finally` / on exception, and again via the watchdog |
| writing the compensation record after the resize | persist compensation + deadline first |
| rehearsing a single-replica or critical workload | preflight refuses |
| rehearsing while VPA `Auto` owns the workload | preflight refuses — don't fight another controller |
| rehearsing every recommendation | rank by `projected_node_savings × confidence`, rehearse the top N |
| calling a timed-out rehearsal "safe" | `INCONCLUSIVE`, and the recommendation ships as `modelled` |
| claiming zero restarts without checking `restartCount` | compare `restart_count` before/after and fail the verdict on any delta |

## Quick reference

- Kubernetes **≥ 1.35** (in-place resize GA); patch `pods/resize`; kubectl ≥ 1.32 for `--subresource`.
- Experiment, not actuation: baseline → resize → observe → **always revert** → evidence in the PR.
- Compensation-first + watchdog CronJob + Lease + preflight + concurrency cap + kill switch.
- Only mutating RBAC verb in the cluster: `patch pods/resize`.
- Outcomes: `safe` (becomes the rehearsed floor) · `regressed` (widen margin) · `inconclusive` · `reverted_by_watchdog`.
- Signals → kubethrifty-cgroup-psi-signals; sizing/floors → kubethrifty-python-analyser; RBAC/manifests → kubethrifty-devops-k8s; code depth → `references/rehearsal-implementation.md`.
