# Resize Rehearsal — Implementation Reference

Complete code and contracts for the rehearsal subsystem. Load this when writing or modifying the runner,
preflight, watchdog, database rows, or PR body. `SKILL.md` carries the rules; this file carries the
details that decide whether the feature is safe.

## Contents
1. Preflight (`rehearsal/preflight.py`)
2. The runner (`rehearsal/runner.py`)
3. The watchdog (`rehearsal/watchdog.py`) and its CronJob
4. Database rows (`rehearsals` table)
5. RBAC + Helm values
6. The PR-body evidence template
7. Failure modes and the challenge questions you will be asked

---

## 1. Preflight

```python
# analyser/src/rehearsal/preflight.py
from dataclasses import dataclass

CRITICAL_NS_LABEL = "kubethrifty.io/tier"
DISABLE_ANNOTATION = "kubethrifty.io/rehearsal"      # value "disabled"


@dataclass
class Preflight:
    ok: bool
    reason: str = ""


def check(k8s, cluster_cfg, ns: str, workload: str, pod: str) -> Preflight:
    if not cluster_cfg.rehearsal_enabled:
        return Preflight(False, "rehearsals globally disabled (Helm value)")

    namespace = k8s.core.read_namespace(ns)
    if (namespace.metadata.labels or {}).get(CRITICAL_NS_LABEL) == "critical":
        return Preflight(False, f"namespace {ns} is labelled critical")

    dep = k8s.apps.read_namespaced_deployment(workload, ns)
    if (dep.metadata.annotations or {}).get(DISABLE_ANNOTATION) == "disabled":
        return Preflight(False, "workload opted out via annotation")
    if (dep.spec.replicas or 1) < 2:
        return Preflight(False, "single-replica workload — no safe blast radius")
    if dep.status.updated_replicas != dep.status.replicas:
        return Preflight(False, "rollout in progress")

    if k8s.has_vpa_in_auto_mode(ns, workload):
        return Preflight(False, "VPA in Auto/InPlaceOrRecreate mode owns this workload")
    if not k8s.pdb_allows_one_disruption(ns, workload):
        return Preflight(False, "PodDisruptionBudget has no headroom")
    if k8s.concurrent_rehearsals(cluster_cfg.name) >= cluster_cfg.max_concurrent:
        return Preflight(False, "cluster rehearsal concurrency cap reached")

    return Preflight(True)
```

Preflight failure is **not** an error path — it means the recommendation ships with `evidence: modelled`
instead of `evidence: rehearsed`. Record the reason on the recommendation so the reviewer knows why there
is no experiment attached.

---

## 2. The runner

Requires Kubernetes ≥ 1.35. Compensation is persisted before the forward action; the revert is
unconditional.

```python
# analyser/src/rehearsal/runner.py
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Optional

from kubernetes import client

from ..verification.signals import Window, evaluate

POLL_INTERVAL_S = 5


@dataclass
class RehearsalPlan:
    namespace: str
    pod: str
    container: str
    candidate_requests: dict            # {"cpu": "250m", "memory": "320Mi"}
    candidate_limits: dict
    baseline_seconds: int = 300
    observe_seconds: int = 1800
    resize_timeout_seconds: int = 120


@dataclass
class RehearsalResult:
    outcome: str                        # safe | regressed | inconclusive
    reasons: list
    evidence: dict
    restarts_observed: int
    proven_floor: Optional[dict] = None # the candidate, iff SAFE


class RehearsalRunner:
    def __init__(self, api: client.ApiClient, prom, store, clock=dt.datetime.utcnow):
        self.api, self.core = api, client.CoreV1Api(api)
        self.prom, self.store, self.clock = prom, store, clock

    # ---------------------------------------------------------------- plumbing
    def _patch_resize(self, ns, pod, container, requests, limits) -> None:
        """PATCH /api/v1/namespaces/{ns}/pods/{pod}/resize — the typed client has no method for this."""
        body = {"spec": {"containers": [{"name": container,
                                         "resources": {"requests": requests, "limits": limits}}]}}
        self.api.call_api(
            "/api/v1/namespaces/{namespace}/pods/{name}/resize", "PATCH",
            path_params={"namespace": ns, "name": pod}, body=body,
            header_params={"Content-Type": "application/strategic-merge-patch+json",
                           "Accept": "application/json"},
            auth_settings=["BearerToken"], _preload_content=True,
        )

    def _pod(self, ns, pod):
        return self.core.read_namespaced_pod(pod, ns)

    def _actual_resources(self, ns, pod, container) -> dict:
        for cs in self._pod(ns, pod).status.container_statuses or []:
            if cs.name == container:
                return (cs.resources.to_dict() if cs.resources else {}) or {}
        return {}

    def _restart_count(self, ns, pod, container) -> int:
        for cs in self._pod(ns, pod).status.container_statuses or []:
            if cs.name == container:
                return cs.restart_count or 0
        return 0

    def _conditions(self, ns, pod) -> dict:
        return {c.type: {"status": c.status, "reason": getattr(c, "reason", None),
                         "message": getattr(c, "message", None)}
                for c in (self._pod(ns, pod).status.conditions or [])}

    def _await_applied(self, plan) -> tuple[bool, dict]:
        """True once STATUS shows the candidate; False on Infeasible or timeout."""
        deadline, last = time.monotonic() + plan.resize_timeout_seconds, {}
        while time.monotonic() < deadline:
            conds = last = self._conditions(plan.namespace, plan.pod)
            pending = conds.get("PodResizePending", {})
            if pending.get("status") == "True" and pending.get("reason") == "Infeasible":
                return False, conds
            req = (self._actual_resources(plan.namespace, plan.pod, plan.container).get("requests") or {})
            if all(str(req.get(k)) == str(v) for k, v in plan.candidate_requests.items()):
                return True, conds
            time.sleep(POLL_INTERVAL_S)
        return False, last

    def _window(self, plan, seconds: int) -> Window:
        w = f"{seconds}s"
        sel = f'namespace="{plan.namespace}",pod="{plan.pod}",container="{plan.container}"'
        q = self.prom.instant
        return Window(
            throttled_periods=q(f'sum(increase(container_cpu_cfs_throttled_periods_total{{{sel}}}[{w}]))'),
            cfs_periods=q(f'sum(increase(container_cpu_cfs_periods_total{{{sel}}}[{w}]))'),
            restarts=q(f'sum(increase(kube_pod_container_status_restarts_total{{{sel}}}[{w}]))'),
            oom_kills=q(f'sum(increase(container_oom_events_total{{{sel}}}[{w}]))'),
            psi_cpu_full=q(f'sum(rate(container_pressure_cpu_stalled_seconds_total{{{sel}}}[{w}]))'),
            psi_mem_full=q(f'sum(rate(container_pressure_memory_stalled_seconds_total{{{sel}}}[{w}]))'),
        )

    # ------------------------------------------------------------------ driver
    def run(self, plan: RehearsalPlan, recommendation_id: int) -> RehearsalResult:
        original = self._actual_resources(plan.namespace, plan.pod, plan.container)
        orig_req, orig_lim = original.get("requests") or {}, original.get("limits") or {}
        if not orig_req:
            return RehearsalResult("inconclusive", ["could not read actual resources"], {}, 0)

        pod_uid = self._pod(plan.namespace, plan.pod).metadata.uid   # pin identity

        # 1. Compensation FIRST — the watchdog contract.
        deadline = self.clock() + dt.timedelta(
            seconds=plan.observe_seconds + plan.resize_timeout_seconds + 600)
        rid = self.store.open_rehearsal(recommendation_id=recommendation_id, plan=plan, pod_uid=pod_uid,
                                        original_requests=orig_req, original_limits=orig_lim,
                                        revert_deadline=deadline)

        restarts_before = self._restart_count(plan.namespace, plan.pod, plan.container)
        try:
            before = self._window(plan, plan.baseline_seconds)
            time.sleep(plan.baseline_seconds)

            self._patch_resize(plan.namespace, plan.pod, plan.container,
                               plan.candidate_requests, plan.candidate_limits)
            applied, conds = self._await_applied(plan)
            self.store.record_conditions(rid, conds)
            if not applied:
                return self._finish(rid, plan, orig_req, orig_lim, "inconclusive",
                                    ["resize not applied (Infeasible or timed out)"],
                                    {"conditions": conds}, 0)

            self.store.mark_running(rid)
            time.sleep(plan.observe_seconds)
            after = self._window(plan, plan.observe_seconds)
            restarts = self._restart_count(plan.namespace, plan.pod, plan.container) - restarts_before

            verdict = evaluate(before, after)
            if restarts > 0:                       # a restart invalidates the zero-restart claim
                verdict.regressed = True
                verdict.reasons.append(f"container restarted {restarts}× during rehearsal")

            outcome = "regressed" if verdict.regressed else "safe"
            return self._finish(rid, plan, orig_req, orig_lim, outcome,
                                verdict.reasons, verdict.evidence, restarts)
        except Exception as exc:                    # noqa: BLE001 — must always revert
            self._revert(plan, orig_req, orig_lim)
            self.store.close_rehearsal(rid, "inconclusive", [f"exception: {exc}"], {}, 0)
            raise

    def _revert(self, plan, req, lim) -> None:
        self._patch_resize(plan.namespace, plan.pod, plan.container, req, lim)

    def _finish(self, rid, plan, req, lim, outcome, reasons, evidence, restarts):
        self._revert(plan, req, lim)
        evidence["reverted_to"] = (self._actual_resources(
            plan.namespace, plan.pod, plan.container).get("requests"))
        self.store.close_rehearsal(rid, outcome, reasons, evidence, restarts)
        return RehearsalResult(outcome, reasons, evidence, restarts,
                               dict(plan.candidate_requests) if outcome == "safe" else None)
```

If the pod disappears mid-rehearsal (rescheduled, scaled down), the UID check fails on the next poll:
record `INCONCLUSIVE` and skip the revert — the replacement pod comes up from the template with the
original values.

---

## 3. The watchdog

Deliberately a separate process (CronJob every 2 minutes), because a compensation owned by the same
process that can crash is not a compensation.

```python
# analyser/src/rehearsal/watchdog.py
def sweep(store, runner) -> int:
    reverted = 0
    for r in store.overdue_rehearsals():          # revert_deadline < now() AND outcome IN (pending,running)
        runner._revert(r.plan, r.original_requests, r.original_limits)
        store.close_rehearsal(r.id, "reverted_by_watchdog", ["revert deadline exceeded"], {}, 0)
        reverted += 1
    return reverted        # -> kubethrifty_rehearsals_watchdog_reverts_total (alert on > 0)
```

```yaml
# charts/kubethrifty/templates/rehearsal-watchdog-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata: {name: kubethrifty-rehearsal-watchdog}
spec:
  schedule: "*/2 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: OnFailure
          serviceAccountName: kubethrifty-rehearsal
          containers:
            - name: watchdog
              image: ghcr.io/OWNER/kubethrifty-analyser:1.0.0   # never :latest
              args: ["-m", "src.rehearsal.watchdog"]
              resources:
                requests: {cpu: 10m, memory: 64Mi}
                limits: {cpu: 200m, memory: 128Mi}
```

---

## 4. Database rows

```sql
CREATE TYPE rehearsal_outcome AS ENUM
    ('pending','running','safe','regressed','inconclusive','reverted_by_watchdog');

CREATE TABLE rehearsals (
    id                 BIGSERIAL PRIMARY KEY,
    recommendation_id  BIGINT REFERENCES recommendations(id) ON DELETE CASCADE,
    cluster            TEXT NOT NULL,
    namespace          TEXT NOT NULL,
    pod                TEXT NOT NULL,
    pod_uid            TEXT NOT NULL,             -- identity, so a replaced pod is not "our" pod
    container          TEXT NOT NULL,
    original_requests  JSONB NOT NULL,            -- exactly what must be restored
    original_limits    JSONB NOT NULL,
    candidate_requests JSONB NOT NULL,
    candidate_limits   JSONB NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revert_deadline    TIMESTAMPTZ NOT NULL,      -- watchdog contract
    ended_at           TIMESTAMPTZ,
    outcome            rehearsal_outcome NOT NULL DEFAULT 'pending',
    resize_conditions  JSONB,                     -- PodResizePending/InProgress trail
    signals_before     JSONB,
    signals_after      JSONB,
    restarts_observed  INT NOT NULL DEFAULT 0,
    reasons            TEXT[],
    evidence_bundle_sha256 TEXT                   -- links to the detective bundle
);
CREATE INDEX ON rehearsals (cluster, namespace, pod, started_at DESC);
CREATE INDEX ON rehearsals (outcome) WHERE outcome IN ('pending','running');
```

`recommendations` gains `rehearsal_id`, `evidence_tier` (`rehearsed|modelled|partial`), and
`binding_constraint` (`peak_margin|forecast|rehearsed|absolute_min`).

---

## 5. Helm values

```yaml
rehearsal:
  enabled: true
  maxConcurrentPerCluster: 3
  baselineSeconds: 300
  observeSeconds: 1800
  resizeTimeoutSeconds: 120
  topNPerRun: 3               # rank by projected_node_savings × confidence
  allowMemoryRestartPolicy: false
  skipNamespaceLabels:
    kubethrifty.io/tier: critical
```

---

## 6. PR-body evidence template

```markdown
### ✅ Rehearsed on a live pod — no restart
`payment-processor-7d9f`, 30 min, 2026-08-16 14:02–14:32 UTC

| Signal | Baseline (before) | Rehearsal (after) | Verdict |
|---|---|---|---|
| CPU throttle ratio | 0.3% | 0.4% | ok (< +5pp) |
| Memory PSI `full` | 0.0% | 0.0% | ok |
| CPU PSI `full` | 0.1% | 0.2% | ok |
| OOM events | 0 | 0 | ok |
| Container restarts | — | 0 | ok |
| Peak memory | 262Mi | 268Mi | 84% of proposed request |

Evidence bundle: `sha256:9f2c…` (replay: `thriftctl replay 9f2c…`)
Sizing basis — CPU: P95 180m × 1.20, floored by the 14-day forecast upper bound (210m).
Memory: cgroup `memory.peak` 268Mi × 1.25. Binding constraint: `forecast` (CPU), `peak_margin` (memory).
```

Recommendations without a rehearsal say so explicitly: `evidence: modelled — no rehearsal (single-replica
workload)`. Never imply an experiment that did not happen.

---

## 7. Failure modes and challenge questions

| Failure | Handling |
|---|---|
| `Infeasible` | End immediately, `INCONCLUSIVE`, record node allocatable for the detective's `RESIZE_INFEASIBLE_STUCK` rule |
| `Deferred` forever | Timeout → `INCONCLUSIVE`; revert is *downward*, always feasible |
| Runner crash | Watchdog reverts at deadline; row marked `reverted_by_watchdog`; alert |
| Pod rescheduled mid-run | UID mismatch → `INCONCLUSIVE`, no revert needed |
| Memory resize restarts container | Verdict fails on `restarts_observed > 0`; targets with `RestartContainer` are refused by default |
| PSI unavailable | Verdict falls back to throttle + OOM + restarts; evidence tier `partial` |

| Question | Answer |
|---|---|
| "30 minutes isn't a business cycle." | Correct. A rehearsal proves *absence of immediate harm at this size under current traffic*. It is one floor of three: the forecast covers the cycle, the rehearsal covers the mechanism, post-merge verification covers the real world for 24–48h |
| "Why not create a canary pod instead?" | A new pod has cold caches and different scheduling, so it proves nothing about the pod actually serving traffic — and creating pods would need `create` on pods, widening RBAC beyond `patch pods/resize` |
| "Isn't experimenting in production reckless?" | Worst case is one pod of ≥2 running at a different size for minutes, then reverting — fenced by preflight, timeout, a persisted deadline, an independent watchdog, and a kill switch |
| "Does this work on managed clusters?" | Depends on the provider's supported minor and containerd 2.x. It degrades cleanly: `Infeasible` or empty PSI → `modelled` evidence, and the PR says so |
