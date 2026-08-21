#!/usr/bin/env python3
"""
main.py -- the analysis run.

    python -m src.main --window 7d
    python -m src.main --window 7d --auto-pr
    python -m src.main --namespace shop --scenario self_managed_karpenter

Ordering here is a safety property, not a style choice:

  1. LOCK, if this run can write. One analysis per cluster: two concurrent runs would open
     competing PRs against the same values.yaml and, with rehearsals on, resize the same pod from
     two directions at once.
  2. PREFLIGHT. If Prometheus is unreachable the run stops before it has computed anything.
     A degraded run must produce NO recommendation rather than a wrong one, and the cheapest way to
     guarantee that is to refuse to start.
  3. Discover, collect, size. Pure computation over whatever evidence exists.
  4. Persist and report. Every number carries its provenance -- binding floor, sizing basis,
     evidence tier -- so the PR body can explain itself.
  5. Open a PR only if explicitly asked AND credentials exist. Half-configured means no PR.

The exit code is meaningful: 0 = ran and reported, 1 = could not run safely.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import ExitStack, nullcontext
from datetime import UTC, datetime

from .cache import Cache, LockUnavailable, make_client, run_lock
from .cloud_pricing import load_catalogue, strategy_for
from .config import get_settings
from .db import Repository, get_engine
from .manifest_generator import ManifestBuilder
from .metric_collector import MetricCollector
from .packing import savings_report
from .packing.from_catalogue import (
    daemonset_pods,
    load_instances,
    pods_from_recommendations,
    surcharge_for,
)
from .prometheus_client import PrometheusClient
from .recommendation_engine import Action, RecommendationEngine, ResourceRecommendation

log = logging.getLogger("kubethrifty")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _summarise(recs: list[ResourceRecommendation]) -> dict:
    by_action: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for r in recs:
        by_action[str(r.action)] = by_action.get(str(r.action), 0) + 1
        by_tier[str(r.evidence_tier)] = by_tier.get(str(r.evidence_tier), 0) + 1
    return {"total": len(recs), "by_action": by_action, "by_evidence_tier": by_tier}


def _print_table(recs: list[ResourceRecommendation]) -> None:
    if not recs:
        print("No recommendations produced.")
        return
    print(f"\n{'workload/container':38s} {'res':7s} {'action':13s} {'current':>10s} "
          f"{'proposed':>10s} {'binding':13s} {'evidence':10s} blocked")
    print("-" * 118)
    for r in sorted(recs, key=lambda x: (x.namespace, x.workload, x.resource)):
        cur = "  n/obs" if r.current_request is None else f"{r.current_request:10.3f}"
        print(f"{(r.workload + '/' + r.container):38s} {r.resource:7s} {str(r.action):13s} "
              f"{cur} {r.recommended_request:10.3f} {r.binding_constraint:13s} "
              f"{str(r.evidence_tier):10s} {r.reduction_blocked}")
    print("-" * 118)


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kubethrifty-analyser")
    ap.add_argument("--window", default="7d",
                    help="lookback window (default 7d; MIN_DATA_POINTS is 168 hourly samples)")
    ap.add_argument("--namespace", default=None, help="restrict to one namespace")
    ap.add_argument("--scenario", default=None,
                    help="pricing scenario: fixed_node_pool | self_managed_karpenter | eks_auto_mode")
    ap.add_argument("--auto-pr", action="store_true",
                    help="open a GitHub PR (requires GITHUB_TOKEN and GITHUB_REPO)")
    ap.add_argument("--json", action="store_true", help="emit recommendations as JSON on stdout")
    ap.add_argument("--run-id", default=None,
                    help="reuse a run id minted by the enqueuer (so the dashboard can follow it)")
    ap.add_argument("--no-persist", action="store_true",
                    help="compute and print without writing to TimescaleDB")
    ap.add_argument("--instance-type", default=None,
                    help="node type to bin-pack against (default: the smallest in the catalogue, "
                         "which gives the most conservative node count)")
    args = ap.parse_args(argv)

    s = get_settings()
    _configure_logging(s.log_level)
    run_id = args.run_id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    log.info("starting %s (cluster=%s window=%s)", run_id, s.cluster_name, args.window)

    want_pr = args.auto_pr or s.auto_pr
    # The lock is MANDATORY for a run that can change something, and best-effort for one that only
    # computes. Two read-only runs racing waste CPU; two writing runs race on a shared branch and a
    # shared pod. Requiring a lock for `--json` on a laptop with no Valkey would make the read-only
    # path unusable for no safety gain.
    lock_required = want_pr or s.rehearsal_enabled

    valkey = None
    try:
        valkey = make_client(s.redis_url)
    except Exception as e:                                            # noqa: BLE001
        log.warning("no Valkey client (%s): running without cache", e)

    with ExitStack() as stack:
        if lock_required:
            try:
                stack.enter_context(run_lock(valkey, s.cluster_name))
            except LockUnavailable as e:
                log.error("refusing to run a writing analysis without exclusivity: %s", e)
                return 1
        else:
            try:
                stack.enter_context(run_lock(valkey, s.cluster_name))
            except LockUnavailable as e:
                log.warning("proceeding read-only without the cluster lock: %s", e)
                stack.enter_context(nullcontext())

        cache = Cache(valkey)
        repo = _repository(s) if not args.no_persist else None

        # Rehearsed floors are loaded from the `rehearsals` table, not recomputed: a floor is
        # evidence from a past experiment, and only a `safe` outcome ever produces one.
        rehearsed_floors: dict[str, float] = {}
        cluster_id = 0
        if repo is not None:
            try:
                cluster_id = repo.ensure_cluster(
                    s.cluster_name, pricing_scenario=args.scenario or s.pricing_scenario)
                rehearsed_floors = repo.rehearsed_floors(cluster_id)
                log.info("loaded %d rehearsed floor(s)", len(rehearsed_floors))
            except Exception as e:                                    # noqa: BLE001
                log.error("database unreachable (%s): continuing without history", e)
                repo = None

        with PrometheusClient() as prom:
            # --- 2. preflight -------------------------------------------------------------------
            if not prom.healthy():
                log.error("Prometheus at %s is not healthy -- refusing to run. A degraded run "
                          "produces no recommendation, never a wrong one.", s.prometheus_url)
                return 1

            collector = MetricCollector(prom, window=args.window)

            # --- 3. discover --------------------------------------------------------------------
            refs = collector.discover(namespace=args.namespace)
            if not refs:
                log.error("no containers with declared resource requests found. Without a declared "
                          "request there is no denominator and no waste to measure.")
                return 1

            # Blast-radius cap: refuse to act on an unbounded number of workloads in one run.
            if len(refs) > s.max_workloads_per_run:
                log.warning("discovered %d workloads; capping at %d (max_workloads_per_run)",
                            len(refs), s.max_workloads_per_run)
                refs = refs[: s.max_workloads_per_run]
            log.info("analysing %d container(s)", len(refs))

            # --- 4. size ------------------------------------------------------------------------
            engine = RecommendationEngine(collector, rehearsed_floors=rehearsed_floors)
            recs = engine.recommend_all(refs)

        if not recs:
            log.error("no recommendations could be produced from the available evidence")
            return 1

        return _finish(args, s, run_id, recs, repo=repo, cache=cache, cluster_id=cluster_id,
                      workloads_seen=len(refs), want_pr=want_pr)


def _repository(s) -> Repository | None:
    try:
        return Repository(get_engine(s.database_url.get_secret_value()))
    except Exception as e:                                            # noqa: BLE001
        log.warning("could not build a database engine (%s): running without persistence", e)
        return None


def _finish(
    args,
    s,
    run_id: str,
    recs: list[ResourceRecommendation],
    *,
    repo: Repository | None,
    cache: Cache,
    cluster_id: int,
    workloads_seen: int,
    want_pr: bool,
) -> int:
    # --- 5. price ------------------------------------------------------------------------------
    #
    # The bin-packer runs here, and its node-count delta is the ONLY figure in this product that
    # earns a currency symbol. Per-pod waste stays a ratio: clouds bill per node, so trimming
    # millicores across pods saves nothing until a node actually disappears.
    savings = None
    scenario = args.scenario or s.pricing_scenario
    try:
        catalogue = load_catalogue(s.instance_catalogue)
        strategy = strategy_for(scenario, catalogue)
        log.info("pricing scenario=%s as_of=%s region=%s",
                 strategy.scenario, strategy.as_of, strategy.region)

        instances, metadata = load_instances(s.instance_catalogue)
        instance = instances.get(args.instance_type) if args.instance_type else None
        if instance is None:
            # Smallest node in the catalogue. A deliberate choice: packing onto the smallest type
            # gives the most conservative (highest) node count, so the reported saving is a floor
            # rather than a best case.
            instance = min(instances.values(), key=lambda i: i.cpu_cores)
            log.info("no --instance-type given; packing against %s (smallest in the catalogue, "
                     "which yields the most conservative node count)", instance.name)

        before_pods, after_pods = pods_from_recommendations(recs)
        if before_pods:
            savings = savings_report(
                before_pods, after_pods, instance,
                daemonset_overhead=daemonset_pods(metadata),
                scenario=scenario,
                surcharge_pct=surcharge_for(scenario, metadata),
                currency=metadata["currency"],
                as_of=metadata["as_of"],
                region=metadata["region"],
            )
            log.info(
                "packing: %d -> %d node(s) of %s (%s-bound after the change)",
                savings.nodes_before, savings.nodes_after, instance.name,
                savings.after.bin_limited_by,
            )
        else:
            log.info("no workload had a declared request for both resources; no packing possible")
    except (FileNotFoundError, KeyError, ValueError) as e:
        log.warning("pricing unavailable (%s); reporting percentages only", e)

    # --- 6. report -----------------------------------------------------------------------------
    summary = _summarise(recs)
    if args.json:
        print(json.dumps({"run_id": run_id, "summary": summary,
                          "recommendations": [r.to_dict() for r in recs]}, indent=2, default=str))
    else:
        _print_table(recs)
        print(f"\n{summary['total']} recommendation(s): {summary['by_action']}")
        print(f"evidence: {summary['by_evidence_tier']}")

        if savings is not None:
            print(f"\nnodes: {savings.nodes_before} -> {savings.nodes_after} "
                  f"({savings.instance_type}, {savings.after.bin_limited_by}-bound)")
            if savings.nodes_removed > 0:
                # The one place a currency symbol is allowed to appear.
                print(f"monthly saving: {savings.monthly_saving:,.2f} {savings.currency} "
                      f"({savings.nodes_removed} node(s) removed, prices as of {savings.as_of})")
            else:
                print("monthly saving: 0.00 -- no node becomes removable, so there is no saving "
                      "to report even though per-pod requests can be trimmed")
            print(f"  {savings.note}")
        partial = summary["by_evidence_tier"].get("partial", 0)
        if partial:
            print(f"\n{partial} recommendation(s) are `partial`: a signal the sizer wanted was "
                  f"unavailable. Modelled, not verified.")

    # --- 7. manifests + PR ---------------------------------------------------------------------
    by_workload: dict[tuple[str, str], list[ResourceRecommendation]] = {}
    for r in recs:
        by_workload.setdefault((r.namespace, r.workload), []).append(r)

    file_changes: dict[str, str] = {}
    for (namespace, workload), group in by_workload.items():
        if not any(r.action in (Action.REDUCE, Action.INCREASE) for r in group):
            continue
        builder = ManifestBuilder(namespace, workload).with_run_id(run_id)
        for r in group:
            builder.with_recommendation(r)
        rendered = builder.build()
        if rendered:
            file_changes[f"k8s/right-sized/{namespace}/{workload}.yaml"] = rendered

    pr_url: str | None = None
    exit_code = 0
    if not want_pr:
        log.info("auto-PR not requested; %d manifest patch(es) computed but not published",
                 len(file_changes))
    elif not (s.github_token and s.github_repo):
        log.error("--auto-pr requested but GITHUB_TOKEN/GITHUB_REPO are not set. Refusing to "
                  "half-configure a write path.")
        exit_code = 1
    else:
        from .github_pr_creator import GitHubPRCreator
        creator = GitHubPRCreator(
            token=s.github_token.get_secret_value(),
            repo=s.github_repo,
            base_branch=s.github_base_branch,
        )
        pr_url = creator.create_pr(file_changes, recs, savings=savings, run_id=run_id)
        if pr_url:
            print(f"\nPR opened: {pr_url}")

    # --- 8. persist ----------------------------------------------------------------------------
    # After the PR, so the run row can record its URL. The run and all of its recommendations land
    # in one transaction, so a crash here cannot leave a `succeeded` run holding half its rows.
    #
    # A `partial` tier anywhere makes the whole run `degraded`: the run completed, but with less
    # evidence than the sizer was designed for, and that fact belongs on the run rather than only on
    # the individual rows.
    if repo is not None:
        partial = summary["by_evidence_tier"].get("partial", 0)
        try:
            repo.persist_run(
                run_id=run_id,
                cluster_id=cluster_id,
                window_spec=args.window,
                recommendations=recs,
                workloads_seen=workloads_seen,
                status="degraded" if partial else "succeeded",
                pr_url=pr_url,
                degraded_reason=(
                    f"{partial} recommendation(s) on the partial evidence tier" if partial else None
                ),
            )
            if savings is not None:
                # Separate transaction from the run: a savings figure is a derived report, and
                # failing to store it must not discard the recommendations themselves.
                try:
                    repo.persist_savings(cluster_id=cluster_id, run_id=run_id, report=savings)
                except Exception as e:                                # noqa: BLE001
                    log.error("could not persist the savings report: %s", e)

            # New recommendations exist, so the dashboard's cached list is now wrong. Invalidated by
            # exact key -- never a SCAN-and-delete sweep, which is O(keyspace) on a routine write.
            cache.invalidate_cluster(str(cluster_id))
        except Exception as e:                                        # noqa: BLE001
            # Deliberately NOT fatal. The recommendations are already printed and the PR is already
            # open; refusing to right-size a cluster because the history database is down would let
            # a reporting dependency block the product's actual job.
            log.error("could not persist run %s (%s): results printed but not stored", run_id, e)

    return exit_code


def main() -> int:
    try:
        return run(sys.argv[1:])
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
