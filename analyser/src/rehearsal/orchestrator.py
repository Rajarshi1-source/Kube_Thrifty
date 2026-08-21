#!/usr/bin/env python3
"""
orchestrator.py -- run a rehearsal campaign for one analysis.

Ties the pieces together: rank the workloads worth the budget, gate each through preflight, run the
gentlest candidate that passes, and hand back the floors that were actually proven.

Two behaviours here matter more than the plumbing:

  * A regression STOPS the ladder for that workload. If the gentlest candidate already degrades the
    pod, a more aggressive one certainly will, and continuing would spend another half hour on a live
    pod to learn nothing.
  * Every outcome that is not `safe` produces NO floor. `inconclusive` in particular is not a
    partial pass -- it means the experiment never actually ran, so there is nothing to conclude.
"""
from __future__ import annotations

import logging

from .candidates import build_candidates, rank_workloads
from .preflight import PodFacts, preflight
from .runner import Outcome, Rehearsal, RehearsalRequest

log = logging.getLogger(__name__)


class RehearsalCampaign:
    def __init__(
        self,
        rehearsal: Rehearsal,
        *,
        fact_source,
        cluster_id: int,
        enabled: bool,
        kill_switch_engaged: bool,
        max_concurrent: int,
        running_rehearsals: int = 0,
        observe_seconds: int = 1800,
        baseline_seconds: int = 300,
        resize_timeout_seconds: int = 120,
    ) -> None:
        self.rehearsal = rehearsal
        self.facts = fact_source
        self.cluster_id = cluster_id
        self.enabled = enabled
        self.kill_switch_engaged = kill_switch_engaged
        self.max_concurrent = max_concurrent
        self.running = running_rehearsals
        self.observe_seconds = observe_seconds
        self.baseline_seconds = baseline_seconds
        self.resize_timeout_seconds = resize_timeout_seconds

    def run(self, recommendations, run_id: str, top_n: int = 3) -> dict:
        """
        Rehearse the top-ranked candidates.

        Returns {"floors": {...}, "rows": [...], "skipped": [...]}, where `floors` is keyed
        "namespace/workload/container/resource" for the recommendation engine, and `rows` is the
        evidence table for the pull request body.
        """
        if not self.enabled:
            log.info("rehearsals disabled; skipping the campaign entirely")
            return {"floors": {}, "rows": [], "skipped": []}

        ranked = rank_workloads(recommendations, top_n=top_n)
        floors: dict[str, float] = {}
        rows: list[dict] = []
        skipped: list[dict] = []
        # Tracked locally as well as in the store so the cap is honoured WITHIN one campaign, not
        # just across concurrent runs.
        in_flight_workloads: set[str] = set()

        for w in ranked:
            facts: PodFacts | None = self.facts(w.namespace, w.workload, w.container)
            if facts is None:
                skipped.append({
                    "workload": w.workload,
                    "resource": w.resource,
                    "reason": "no live pod could be resolved for this workload",
                })
                continue

            candidates = build_candidates(w)
            outcome_recorded = False

            for candidate in candidates:
                gate = preflight(
                    facts, candidate.requests, candidate.limits,
                    enabled=self.enabled,
                    kill_switch_engaged=self.kill_switch_engaged,
                    running_rehearsals=self.running + len(in_flight_workloads),
                    max_concurrent=self.max_concurrent,
                    workload_has_active_rehearsal=f"{w.namespace}/{w.workload}" in in_flight_workloads,
                )
                if not gate.ok:
                    skipped.append({
                        "workload": w.workload,
                        "resource": w.resource,
                        "candidate": candidate.label,
                        "reason": "; ".join(gate.reasons),
                    })
                    # Preflight failures are properties of the WORKLOAD, not of the candidate size,
                    # so a more aggressive candidate would be refused for the same reason. Stop.
                    break

                in_flight_workloads.add(f"{w.namespace}/{w.workload}")

                result = self.rehearsal.run(
                    RehearsalRequest(
                        namespace=w.namespace,
                        workload=w.workload,
                        pod=facts.pod,
                        pod_uid=facts.pod_uid,
                        container=w.container,
                        cluster_id=self.cluster_id,
                        run_id=run_id,
                        original_requests=dict(facts.current_requests),
                        original_limits=dict(facts.current_limits),
                        candidate_requests=candidate.requests,
                        candidate_limits=candidate.limits,
                        observe_seconds=self.observe_seconds,
                        baseline_seconds=self.baseline_seconds,
                        resize_timeout_seconds=self.resize_timeout_seconds,
                    )
                )

                in_flight_workloads.discard(f"{w.namespace}/{w.workload}")
                rows.append(self._row(w, candidate, result))
                outcome_recorded = True

                if result.outcome is Outcome.SAFE and result.rehearsed_floor:
                    for resource, value in result.rehearsed_floor.items():
                        key = f"{w.namespace}/{w.workload}/{w.container}/{resource}"
                        floors[key] = value
                    # A safe result at this aggressiveness is enough. Trying harder risks a
                    # regression on a live pod for a marginal extra saving.
                    break

                if result.outcome is Outcome.REGRESSED:
                    # The gentlest candidate already degraded the pod; more aggressive ones will
                    # too. Stop rather than spend another observation window proving it.
                    log.info(
                        "%s/%s regressed at %s; abandoning the remaining candidates",
                        w.namespace, w.workload, candidate.label,
                    )
                    break

                # INCONCLUSIVE or FAILED: the experiment did not run. Trying a different size will
                # not fix an Infeasible node or a dead API server.
                break

            if not outcome_recorded and not skipped:
                skipped.append({
                    "workload": w.workload,
                    "resource": w.resource,
                    "reason": "no candidate was attempted",
                })

        log.info(
            "rehearsal campaign: %d rehearsed, %d proven floor(s), %d skipped",
            len(rows), len(floors), len(skipped),
        )
        return {"floors": floors, "rows": rows, "skipped": skipped}

    @staticmethod
    def _row(w, candidate, result) -> dict:
        """One row of the pull request's rehearsal evidence table.

        Missing signals render as "not observed", never as 0 -- the PR body is held to the same
        honesty rule as the UI.
        """
        def sig(source: dict, key: str) -> str:
            value = (source or {}).get(key)
            return "not observed" if value is None else f"{value}"

        return {
            "workload": f"{w.workload}/{w.container}",
            "resource": w.resource,
            "candidate": candidate.label,
            "outcome": str(result.outcome),
            "throttle_before": sig(result.baseline, "throttle_ratio"),
            "throttle_after": sig(result.observed, "throttle_ratio"),
            "psi_full": sig(result.observed, "psi_stalled_ratio"),
            "restarts": sig(result.observed, "restarts"),
            "oom_events": sig(result.observed, "oom_events"),
            "detail": result.detail,
            "trip_reasons": list(result.trip_reasons),
        }
