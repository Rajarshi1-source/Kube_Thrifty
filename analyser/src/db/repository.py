#!/usr/bin/env python3
"""
repository.py -- every SQL statement the analyser issues, in one place.

Two invariants this file exists to enforce:

  * NULL means "not observed". Never 0. `_nz()` is the only sanctioned way a numeric leaves Python
    for the database, and it maps a missing signal to NULL rather than to a value that would read as
    "measured, and it was zero". A 0 in `throttle_ratio` says "we watched, nothing was throttled" --
    a completely different claim from "we could not see the counter".

  * A run's rows land atomically. `persist_run` writes the run row and its recommendations in ONE
    transaction, so a crash mid-write cannot leave a `succeeded` run with half its recommendations.
    A partial result is indistinguishable from a complete one once the process is gone.

Every write is parameterised. No f-strings in SQL, ever -- namespace and workload names come from
cluster labels, which are attacker-influencable in a multi-tenant cluster.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

log = logging.getLogger(__name__)


def _nz(value: Any) -> Any:
    """
    Pass NULL through as NULL.

    Trivial, and the most important function in the file. It exists so that "coerce a missing
    reading to 0" has no convenient spelling anywhere in the codebase.
    """
    return None if value is None else value


class Repository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- clusters ---------------------------------------------------------------------------------

    def ensure_cluster(
        self,
        name: str,
        *,
        provider: str = "unknown",
        region: str | None = None,
        pricing_scenario: str = "fixed_node_pool",
        kubernetes_version: str | None = None,
    ) -> int:
        """
        Upsert the cluster and return its id.

        `ON CONFLICT ... DO UPDATE` rather than `DO NOTHING`: the pricing scenario and Kubernetes
        version legitimately change over a cluster's life, and a stale scenario would silently
        misprice every savings report afterwards.
        """
        stmt = text("""
            INSERT INTO clusters (name, provider, region, pricing_scenario, kubernetes_version)
            VALUES (:name, :provider, :region, :scenario, :kversion)
            ON CONFLICT (name) DO UPDATE
                SET provider           = EXCLUDED.provider,
                    region             = COALESCE(EXCLUDED.region, clusters.region),
                    pricing_scenario   = EXCLUDED.pricing_scenario,
                    kubernetes_version = COALESCE(EXCLUDED.kubernetes_version,
                                                  clusters.kubernetes_version)
            RETURNING id
        """)
        with self._engine.begin() as conn:
            return int(conn.execute(stmt, {
                "name": name, "provider": provider, "region": region,
                "scenario": pricing_scenario, "kversion": kubernetes_version,
            }).scalar_one())

    # --- runs -------------------------------------------------------------------------------------

    def start_run(self, run_id: str, cluster_id: int, window_spec: str) -> None:
        stmt = text("""
            INSERT INTO analysis_runs (run_id, cluster_id, status, window_spec)
            VALUES (:run_id, :cluster_id, 'running', :window)
            ON CONFLICT (run_id) DO NOTHING
        """)
        with self._engine.begin() as conn:
            conn.execute(stmt, {"run_id": run_id, "cluster_id": cluster_id, "window": window_spec})

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        workloads_seen: int = 0,
        recommendations_made: int = 0,
        pr_url: str | None = None,
        degraded_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        """
        Close a run. `status` is one of running | succeeded | failed | degraded.

        'degraded' is not a failure: it records that the run completed with a signal missing, so its
        recommendations carry a lower evidence tier. Collapsing it into 'succeeded' would hide the
        one fact a reviewer most needs.
        """
        stmt = text("""
            UPDATE analysis_runs
               SET status = CAST(:status AS run_status),
                   finished_at = now(),
                   workloads_seen = :seen,
                   recommendations_made = :made,
                   pr_url = :pr_url,
                   degraded_reason = :degraded,
                   error = :error
             WHERE run_id = :run_id
        """)
        with self._engine.begin() as conn:
            conn.execute(stmt, {
                "run_id": run_id, "status": status, "seen": workloads_seen,
                "made": recommendations_made, "pr_url": pr_url,
                "degraded": degraded_reason, "error": error,
            })

    # --- recommendations --------------------------------------------------------------------------

    _INSERT_REC = text("""
        INSERT INTO recommendations (
            run_id, cluster_id, namespace, workload, workload_kind, container, resource,
            current_request, current_limit, recommended_request, recommended_limit,
            action, confidence,
            observed_p50, observed_p95, observed_p99, observed_peak,
            throttle_ratio, psi_stalled_ratio, oom_events, samples,
            forecast_model, rationale, reduction_blocked,
            binding_constraint, sizing_basis, evidence_tier, rehearsal_id, hpa_coupled
        ) VALUES (
            :run_id, :cluster_id, :namespace, :workload, :workload_kind, :container, :resource,
            :current_request, :current_limit, :recommended_request, :recommended_limit,
            CAST(:action AS rec_action), CAST(:confidence AS rec_confidence),
            :p50, :p95, :p99, :peak,
            :throttle_ratio, :psi_stalled_ratio, :oom_events, :samples,
            :forecast_model, :rationale, :reduction_blocked,
            :binding_constraint, :sizing_basis, :evidence_tier, :rehearsal_id, :hpa_coupled
        )
    """)

    def persist_run(
        self,
        *,
        run_id: str,
        cluster_id: int,
        window_spec: str,
        recommendations: Sequence[Any],
        workloads_seen: int,
        status: str = "succeeded",
        pr_url: str | None = None,
        degraded_reason: str | None = None,
    ) -> None:
        """
        Write the run and all of its recommendations in a single transaction.

        Ordering inside the transaction is forced by the foreign key: `recommendations.run_id`
        references `analysis_runs.run_id`, so the run row is inserted first.
        """
        rows = [self._rec_params(run_id, cluster_id, r) for r in recommendations]
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO analysis_runs (run_id, cluster_id, status, window_spec)
                    VALUES (:run_id, :cluster_id, 'running', :window)
                    ON CONFLICT (run_id) DO NOTHING
                """),
                {"run_id": run_id, "cluster_id": cluster_id, "window": window_spec},
            )
            if rows:
                conn.execute(self._INSERT_REC, rows)
            conn.execute(
                text("""
                    UPDATE analysis_runs
                       SET status = CAST(:status AS run_status), finished_at = now(),
                           workloads_seen = :seen, recommendations_made = :made,
                           pr_url = :pr_url, degraded_reason = :degraded
                     WHERE run_id = :run_id
                """),
                {
                    "run_id": run_id, "status": status, "seen": workloads_seen,
                    "made": len(rows), "pr_url": pr_url, "degraded": degraded_reason,
                },
            )
        log.info("persisted run %s: %d recommendations", run_id, len(rows))

    @staticmethod
    def _rec_params(run_id: str, cluster_id: int, rec: Any) -> dict[str, Any]:
        observed = getattr(rec, "observed", {}) or {}
        return {
            "run_id": run_id,
            "cluster_id": cluster_id,
            "namespace": rec.namespace,
            "workload": rec.workload,
            "workload_kind": getattr(rec, "workload_kind", "Deployment"),
            "container": rec.container,
            "resource": rec.resource,
            "current_request": _nz(rec.current_request),
            "current_limit": _nz(rec.current_limit),
            "recommended_request": rec.recommended_request,
            "recommended_limit": rec.recommended_limit,
            "action": str(rec.action),
            "confidence": str(rec.confidence),
            "p50": _nz(observed.get("p50")),
            "p95": _nz(observed.get("p95")),
            "p99": _nz(observed.get("p99")),
            "peak": _nz(observed.get("peak")),
            "throttle_ratio": _nz(observed.get("throttle_ratio")),
            "psi_stalled_ratio": _nz(observed.get("psi_stalled_ratio")),
            "oom_events": _nz(observed.get("oom_events")),
            "samples": rec.samples,
            "forecast_model": rec.forecast_model,
            "rationale": rec.rationale,
            "reduction_blocked": rec.reduction_blocked,
            "binding_constraint": rec.binding_constraint,
            "sizing_basis": rec.sizing_basis,
            "evidence_tier": str(rec.evidence_tier),
            "rehearsal_id": rec.rehearsal_id,
            "hpa_coupled": rec.hpa_coupled,
        }

    def latest_recommendations(self, cluster_id: int) -> list[dict[str, Any]]:
        """
        The dashboard's primary read: the newest recommendation per container/resource.

        `DISTINCT ON` with a matching `ORDER BY` is Postgres' single-pass way to say "latest per
        group" -- cheaper than the window-function equivalent, and it reads as what it means.
        """
        stmt = text("""
            SELECT DISTINCT ON (namespace, workload, container, resource) *
              FROM recommendations
             WHERE cluster_id = :cluster_id
             ORDER BY namespace, workload, container, resource, created_at DESC
        """)
        with self._engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt, {"cluster_id": cluster_id}).mappings()]

    # --- metric snapshots -------------------------------------------------------------------------

    def insert_metric_snapshots(self, rows: Iterable[dict[str, Any]]) -> int:
        """
        Batch-insert into the hypertable.

        One `executemany` inside one transaction. Row-at-a-time inserts against a hypertable pay
        chunk-routing overhead per statement, which dominates at scrape volumes.
        """
        payload = [
            {
                "time": r.get("time") or datetime.now(UTC),
                "cluster_id": r["cluster_id"],
                "namespace": r["namespace"],
                "pod": r["pod"],
                "container": r["container"],
                "cpu_cores": _nz(r.get("cpu_cores")),
                "memory_bytes": _nz(r.get("memory_bytes")),
                "throttle_ratio": _nz(r.get("throttle_ratio")),
                "psi_cpu_full": _nz(r.get("psi_cpu_full")),
                "psi_mem_full": _nz(r.get("psi_mem_full")),
            }
            for r in rows
        ]
        if not payload:
            return 0
        stmt = text("""
            INSERT INTO metric_snapshots (
                time, cluster_id, namespace, pod, container,
                cpu_cores, memory_bytes, throttle_ratio, psi_cpu_full, psi_mem_full
            ) VALUES (
                :time, :cluster_id, :namespace, :pod, :container,
                :cpu_cores, :memory_bytes, :throttle_ratio, :psi_cpu_full, :psi_mem_full
            )
        """)
        with self._engine.begin() as conn:
            conn.execute(stmt, payload)
        return len(payload)

    def insert_psi_samples(self, rows: Iterable[dict[str, Any]]) -> int:
        """
        Batch-insert cgroup/PSI truth.

        `memory_max` arrives as None when the cgroup reports the literal string "max" (unlimited).
        It is stored as NULL, not 0 -- a 0 limit would make every peak/limit ratio nonsense.
        """
        payload = [
            {
                "time": r.get("time") or datetime.now(UTC),
                "cluster_id": r["cluster_id"],
                "namespace": r["namespace"],
                "pod": r["pod"],
                "container": r["container"],
                "psi_cpu_some": _nz(r.get("psi_cpu_some")),
                "psi_cpu_full": _nz(r.get("psi_cpu_full")),
                "psi_mem_some": _nz(r.get("psi_mem_some")),
                "psi_mem_full": _nz(r.get("psi_mem_full")),
                "psi_io_full": _nz(r.get("psi_io_full")),
                "memory_peak": _nz(r.get("memory_peak")),
                "memory_current": _nz(r.get("memory_current")),
                "memory_max": _nz(r.get("memory_max")),
                "oom_kill_total": _nz(r.get("oom_kill_total")),
                "throttled_periods": _nz(r.get("throttled_periods")),
                "cfs_periods": _nz(r.get("cfs_periods")),
                "source": r.get("source", "cgroup"),
            }
            for r in rows
        ]
        if not payload:
            return 0
        stmt = text("""
            INSERT INTO psi_samples (
                time, cluster_id, namespace, pod, container,
                psi_cpu_some, psi_cpu_full, psi_mem_some, psi_mem_full, psi_io_full,
                memory_peak, memory_current, memory_max, oom_kill_total,
                throttled_periods, cfs_periods, source
            ) VALUES (
                :time, :cluster_id, :namespace, :pod, :container,
                :psi_cpu_some, :psi_cpu_full, :psi_mem_some, :psi_mem_full, :psi_io_full,
                :memory_peak, :memory_current, :memory_max, :oom_kill_total,
                :throttled_periods, :cfs_periods, :source
            )
        """)
        with self._engine.begin() as conn:
            conn.execute(stmt, payload)
        return len(payload)

    # --- hourly aggregate reads -------------------------------------------------------------------

    def cpu_percentiles(
        self, cluster_id: int, namespace: str, workload_prefix: str, container: str, window: str = "7 days",
    ) -> dict[str, Any] | None:
        """
        Read CPU percentiles from the continuous aggregate.

        `rollup()` before `approx_percentile()` is what makes this correct: it merges the hourly
        sketches into one sketch and then reads a percentile from the merged result. Averaging
        hourly p95 values instead would be silently wrong -- percentiles do not compose.
        """
        stmt = text("""
            SELECT approx_percentile(0.50, rollup(cpu_percentiles)) AS p50,
                   approx_percentile(0.95, rollup(cpu_percentiles)) AS p95,
                   approx_percentile(0.99, rollup(cpu_percentiles)) AS p99,
                   max(cpu_peak)                                    AS peak,
                   sum(samples)                                     AS samples
              FROM hourly_pod_stats
             WHERE cluster_id = :cluster_id
               AND namespace = :namespace
               AND pod LIKE :pod_prefix
               AND container = :container
               AND bucket > now() - CAST(:window AS INTERVAL)
        """)
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {
                "cluster_id": cluster_id, "namespace": namespace,
                "pod_prefix": f"{workload_prefix}%", "container": container, "window": window,
            }).mappings().first()
        return dict(row) if row and row["samples"] else None

    def memory_peak(
        self, cluster_id: int, namespace: str, workload_prefix: str, container: str, window: str = "7 days",
    ) -> dict[str, Any] | None:
        """
        The memory sizing basis: a MAX over the window, never a percentile.

        A percentile discards the top 5% of samples -- exactly the allocations that OOMKill a pod.
        """
        stmt = text("""
            SELECT max(memory_peak) AS peak,
                   avg(memory_mean) AS mean,
                   sum(samples)     AS samples
              FROM hourly_pod_stats
             WHERE cluster_id = :cluster_id
               AND namespace = :namespace
               AND pod LIKE :pod_prefix
               AND container = :container
               AND bucket > now() - CAST(:window AS INTERVAL)
        """)
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {
                "cluster_id": cluster_id, "namespace": namespace,
                "pod_prefix": f"{workload_prefix}%", "container": container, "window": window,
            }).mappings().first()
        return dict(row) if row and row["samples"] else None

    def hourly_history(
        self, cluster_id: int, namespace: str, workload_prefix: str, container: str,
        resource: str = "cpu", window: str = "21 days",
    ) -> list[tuple[datetime, float]]:
        """
        Hourly series for the forecaster.

        Hourly, and 21 days by default, because `MIN_POINTS` is 168 hourly observations (one week)
        and AutoETS wants materially more than the minimum to fit a weekly season.
        """
        column = "cpu_mean" if resource == "cpu" else "memory_peak"
        stmt = text(f"""
            SELECT bucket, {column} AS value
              FROM hourly_pod_stats
             WHERE cluster_id = :cluster_id
               AND namespace = :namespace
               AND pod LIKE :pod_prefix
               AND container = :container
               AND bucket > now() - CAST(:window AS INTERVAL)
               AND {column} IS NOT NULL
             ORDER BY bucket
        """)  # noqa: S608 -- `column` is chosen from a two-value literal set above, never user input
        with self._engine.connect() as conn:
            return [
                (r.bucket, float(r.value))
                for r in conn.execute(stmt, {
                    "cluster_id": cluster_id, "namespace": namespace,
                    "pod_prefix": f"{workload_prefix}%", "container": container, "window": window,
                })
            ]

    # --- rehearsals -------------------------------------------------------------------------------

    def open_rehearsal(
        self,
        *,
        rehearsal_id: str,
        run_id: str | None,
        cluster_id: int,
        namespace: str,
        workload: str,
        pod: str,
        pod_uid: str,
        container: str,
        original_requests: dict[str, Any],
        original_limits: dict[str, Any],
        candidate_requests: dict[str, Any],
        candidate_limits: dict[str, Any],
        revert_deadline: datetime,
        baseline_signals: dict[str, Any] | None = None,
    ) -> None:
        """
        THE COMPENSATION ROW. Written BEFORE the cluster is touched.

        This is the single most safety-critical write in the system. It carries what the pod looked
        like before, and the deadline by which it must be back. If this process dies one millisecond
        after the resize PATCH, this row is the only thing that knows how to undo it -- and the
        watchdog CronJob acts on it without needing the original process to exist.

        Write it afterwards and there is a window in which a live pod is running experimental limits
        that nothing in the system knows about.
        """
        stmt = text("""
            INSERT INTO rehearsals (
                rehearsal_id, run_id, cluster_id, namespace, workload, pod, pod_uid, container,
                original_requests, original_limits, candidate_requests, candidate_limits,
                revert_deadline, baseline_signals, outcome
            ) VALUES (
                :rehearsal_id, :run_id, :cluster_id, :namespace, :workload, :pod, :pod_uid,
                :container,
                CAST(:orig_req AS JSONB), CAST(:orig_lim AS JSONB),
                CAST(:cand_req AS JSONB), CAST(:cand_lim AS JSONB),
                :deadline, CAST(:baseline AS JSONB), 'running'
            )
        """)
        with self._engine.begin() as conn:
            conn.execute(stmt, {
                "rehearsal_id": rehearsal_id, "run_id": run_id, "cluster_id": cluster_id,
                "namespace": namespace, "workload": workload, "pod": pod, "pod_uid": pod_uid,
                "container": container,
                "orig_req": json.dumps(original_requests),
                "orig_lim": json.dumps(original_limits),
                "cand_req": json.dumps(candidate_requests),
                "cand_lim": json.dumps(candidate_limits),
                "deadline": revert_deadline,
                "baseline": json.dumps(baseline_signals) if baseline_signals else None,
            })

    def close_rehearsal(
        self,
        rehearsal_id: str,
        *,
        outcome: str,
        rehearsed_floor: float | None = None,
        observed_signals: dict[str, Any] | None = None,
        trip_reasons: list[str] | None = None,
        reverted: bool = True,
    ) -> None:
        """
        Record the outcome.

        `rehearsed_floor` is only ever set for a `safe` outcome -- the schema CHECK enforces it, and
        that constraint is the point. An `inconclusive` rehearsal that leaked a floor into sizing
        would be the worst possible failure: an unverified number wearing a verified badge.
        """
        if outcome != "safe":
            rehearsed_floor = None
        stmt = text("""
            UPDATE rehearsals
               SET outcome = CAST(:outcome AS rehearsal_outcome),
                   rehearsed_floor = :floor,
                   observed_signals = CAST(:observed AS JSONB),
                   trip_reasons = :trips,
                   reverted_at = CASE WHEN :reverted THEN now() ELSE reverted_at END,
                   finished_at = now()
             WHERE rehearsal_id = :rehearsal_id
        """)
        with self._engine.begin() as conn:
            conn.execute(stmt, {
                "rehearsal_id": rehearsal_id, "outcome": outcome, "floor": rehearsed_floor,
                "observed": json.dumps(observed_signals) if observed_signals else None,
                "trips": trip_reasons or [], "reverted": reverted,
            })

    def overdue_rehearsals(self) -> list[dict[str, Any]]:
        """The watchdog's query: still `running`, past its deadline."""
        stmt = text("""
            SELECT * FROM rehearsals
             WHERE outcome = 'running' AND revert_deadline < now()
             ORDER BY revert_deadline
        """)
        with self._engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings()]

    def running_rehearsal_count(self, cluster_id: int) -> int:
        """Backs the concurrency cap. Blast radius is bounded by counting, not by hoping."""
        stmt = text("SELECT count(*) FROM rehearsals WHERE cluster_id = :cid AND outcome = 'running'")
        with self._engine.connect() as conn:
            return int(conn.execute(stmt, {"cid": cluster_id}).scalar_one())

    def rehearsed_floors(self, cluster_id: int, max_age: str = "14 days") -> dict[str, float]:
        """
        Load `safe` rehearsal floors, keyed "namespace/workload/container/resource".

        Aged out at 14 days: a rehearsal is evidence about a specific version of a workload, and a
        fortnight of deploys later it is a claim about code that no longer exists.
        """
        stmt = text("""
            SELECT DISTINCT ON (namespace, workload, container)
                   namespace, workload, container, candidate_requests, rehearsed_floor
              FROM rehearsals
             WHERE cluster_id = :cid
               AND outcome = 'safe'
               AND rehearsed_floor IS NOT NULL
               AND finished_at > now() - CAST(:max_age AS INTERVAL)
             ORDER BY namespace, workload, container, finished_at DESC
        """)
        out: dict[str, float] = {}
        with self._engine.connect() as conn:
            for r in conn.execute(stmt, {"cid": cluster_id, "max_age": max_age}).mappings():
                requests = r["candidate_requests"] or {}
                for resource in ("cpu", "memory"):
                    if resource in requests:
                        key = f"{r['namespace']}/{r['workload']}/{r['container']}/{resource}"
                        out[key] = float(requests[resource])
        return out

    # --- savings ----------------------------------------------------------------------------------

    def persist_savings(
        self,
        *,
        cluster_id: int,
        run_id: str | None,
        report: Any,
    ) -> None:
        """
        Store the bin-packer's node-count delta.

        The schema CHECK refuses `nodes_after > nodes_before`, so a run that somehow modelled a node
        INCREASE fails loudly here rather than persisting a negative saving. That is the correct
        direction: a right-sizing that grows the cluster has mis-modelled something (most likely an
        HPA that will scale out when requests shrink), and the fix is to refuse the write, not to
        report a negative number.
        """
        stmt = text("""
            INSERT INTO savings_reports (
                cluster_id, run_id, scenario, nodes_before, nodes_after, instance_type,
                monthly_saving, hourly_price, currency, as_of, region, packing_detail
            ) VALUES (
                :cluster_id, :run_id, :scenario, :nodes_before, :nodes_after, :instance_type,
                :monthly_saving, :hourly_price, :currency, CAST(:as_of AS DATE), :region,
                CAST(:detail AS JSONB)
            )
        """)
        detail = {
            "cpu_utilisation_before": report.before.cpu_utilisation,
            "cpu_utilisation_after": report.after.cpu_utilisation,
            "memory_utilisation_before": report.before.memory_utilisation,
            "memory_utilisation_after": report.after.memory_utilisation,
            "bin_limited_by_after": report.after.bin_limited_by,
            "pods_placed": report.after.pods_placed,
            "unplaceable": list(report.after.unplaceable),
            "note": report.note,
        }
        with self._engine.begin() as conn:
            conn.execute(stmt, {
                "cluster_id": cluster_id,
                "run_id": run_id,
                "scenario": report.scenario,
                "nodes_before": report.nodes_before,
                "nodes_after": report.nodes_after,
                "instance_type": report.instance_type,
                "monthly_saving": report.monthly_saving,
                "hourly_price": report.hourly_price,
                "currency": report.currency,
                # `unknown` would violate the DATE column; a missing date means the catalogue is
                # malformed and the figure should not be stored as though it were dated.
                "as_of": report.as_of if report.as_of != "unknown" else None,
                "region": report.region,
                "detail": json.dumps(detail),
            })
        log.info(
            "persisted savings: %d -> %d node(s), %s %s",
            report.nodes_before, report.nodes_after,
            report.monthly_saving, report.currency,
        )

    # --- verdicts ---------------------------------------------------------------------------------

    def persist_verdicts(
        self,
        *,
        cluster_id: int,
        bundle_sha: str,
        namespace: str,
        workload: str,
        container: str,
        verdicts: Sequence[Any],
        ruleset_version: str,
        verdicts_digest: str,
    ) -> int:
        """
        Store detective verdicts, idempotently.

        `ON CONFLICT DO NOTHING` against `(bundle_sha, rule_id, ruleset_version)` is correct
        precisely because `investigate()` is pure: the same bundle under the same ruleset yields the
        same verdict, so a re-run has nothing new to say and a second row would be pure noise.
        """
        stmt = text("""
            INSERT INTO verdicts (
                bundle_sha, cluster_id, namespace, workload, container,
                rule_id, confidence, summary, remediation, evidence, attributed_change,
                ruleset_version, verdicts_digest
            ) VALUES (
                :bundle_sha, :cluster_id, :namespace, :workload, :container,
                :rule_id, :confidence, :summary, :remediation,
                CAST(:evidence AS JSONB), CAST(:attributed AS JSONB),
                :ruleset_version, :digest
            )
            ON CONFLICT (bundle_sha, rule_id, ruleset_version) DO NOTHING
        """)
        rows = [
            {
                "bundle_sha": bundle_sha, "cluster_id": cluster_id, "namespace": namespace,
                "workload": workload, "container": container,
                "rule_id": getattr(v, "rule_id", None) or v["rule_id"],
                "confidence": getattr(v, "confidence", None) or v["confidence"],
                "summary": getattr(v, "summary", None) or v.get("summary", ""),
                "remediation": getattr(v, "remediation", None) or v.get("remediation", ""),
                "evidence": json.dumps(getattr(v, "evidence", None) or v.get("evidence", {})),
                "attributed": (
                    json.dumps(a) if (a := getattr(v, "attributed_change", None) or (
                        v.get("attributed_change") if isinstance(v, dict) else None)) else None
                ),
                "ruleset_version": ruleset_version, "digest": verdicts_digest,
            }
            for v in verdicts
        ]
        if not rows:
            return 0
        with self._engine.begin() as conn:
            conn.execute(stmt, rows)
        return len(rows)
