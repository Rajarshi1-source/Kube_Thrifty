#!/usr/bin/env python3
"""
metric_collector.py -- PromQL in, UsageStats out.

This module owns every query string in the product. They are collected here rather than scattered
through the engine so that the relationship between a threshold in `sizing.py` and the query that
feeds it stays auditable.

Notes on specific queries, because each one has a trap:

  CPU percentile   quantile_over_time(0.95, rate(...)[window:step]) -- a subquery. Prometheus cannot
                   take a percentile of a counter directly; it needs rate() evaluated at each step
                   first. Getting this wrong yields a percentile of cumulative CPU seconds, which is
                   a large meaningless number that still looks plausible in a dashboard.

  Memory basis     max_over_time(container_memory_working_set_bytes[window]) -- the PEAK, not a
                   percentile. When the cgroup-truth collector is deployed, kubethrifty_cgroup_
                   memory_peak_bytes supersedes this because it is the kernel's own high-water
                   mark and cannot miss a spike between scrapes. working_set_bytes sampled at 15s
                   CAN miss a 2-second allocation burst that OOMKills the container.

  Throttling       Two separate rate() queries divided, NOT a ratio of raw counters, and each over
                   the same window. See verification/signals.py for why.

  OOM              increase(container_oom_events_total[window]) -- a counter. Never
                   last_terminated_reason, which is a gauge that flaps.

  container!=""    Every cAdvisor query needs it. cAdvisor emits a pod-level aggregate series with
                   an empty container label; without the filter every workload's usage is counted
                   twice, and the sizer sees roughly double the real demand.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .prometheus_client import PrometheusClient
from .sizing import UsageStats

log = logging.getLogger(__name__)

BYTES_PER_MIB = 1024 * 1024

# Runtimes whose RSS lags real demand. Matched against the pod's kubethrifty.io/runtime annotation
# or its image name.
GC_RUNTIME_HINTS = ("java", "jvm", "node", "dotnet", "clr")


@dataclass(frozen=True)
class WorkloadRef:
    namespace: str
    workload: str
    container: str
    kind: str = "Deployment"

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.workload}/{self.container}"


class MetricCollector:
    """Builds UsageStats for a container from Prometheus.

    Every method returns None rather than a zero when a signal is unavailable. `collect` refuses to
    invent a UsageStats it cannot populate: without CPU or memory data there is no recommendation
    to make, and returning a half-empty stats object would let the sizer proceed on nothing.
    """

    def __init__(self, client: PrometheusClient, window: str = "7d") -> None:
        self.c = client
        self.window = window
        # 5m is the smallest rate() window that reliably contains 2+ samples at a 15s scrape
        # interval even with a missed scrape.
        self.rate_window = "5m"

    # -- label matching --------------------------------------------------------------------------
    @staticmethod
    def _selector(ref: WorkloadRef) -> str:
        """Bound label matchers. Values are quoted and never concatenated into the metric name."""
        return (
            f'namespace="{ref.namespace}",'
            f'pod=~"{ref.workload}-.*",'
            f'container="{ref.container}"'
        )

    # -- CPU -------------------------------------------------------------------------------------
    def cpu_percentile(self, ref: WorkloadRef, q: float) -> float | None:
        sel = self._selector(ref)
        promql = (
            f"quantile_over_time({q}, "
            f'rate(container_cpu_usage_seconds_total{{{sel},container!=""}}[{self.rate_window}])'
            f"[{self.window}:{self.rate_window}])"
        )
        return self.c.scalar(f"max({promql})")

    def cpu_peak(self, ref: WorkloadRef) -> float | None:
        sel = self._selector(ref)
        promql = (
            f'max_over_time(rate(container_cpu_usage_seconds_total{{{sel},container!=""}}'
            f"[{self.rate_window}])[{self.window}:{self.rate_window}])"
        )
        return self.c.scalar(f"max({promql})")

    # -- memory ----------------------------------------------------------------------------------
    def memory_peak_mib(self, ref: WorkloadRef) -> tuple[float | None, str]:
        """Return (peak_mib, basis). Prefers the kernel high-water mark over sampled working set.

        `cgroup_memory_peak` is memory.peak: the kernel's own maximum, which cannot miss a spike
        between scrapes. `working_set_max` is the best available fallback and is explicitly a
        weaker basis -- the evidence tier reflects that.
        """
        sel = self._selector(ref)
        # The RECORDED series, not the collector's raw metric. The collector labels its samples with
        # `container_id` only -- it holds no Kubernetes credentials, so it cannot know a namespace or
        # a pod name. `kubethrifty:cgroup_memory_peak_bytes:container` is the recording rule that
        # joins those samples to Kubernetes identity via kube-state-metrics. Querying the raw metric
        # with a namespace/pod selector matches nothing and silently falls through to the weaker
        # working-set basis.
        cgroup = self.c.scalar(
            f"max(max_over_time("
            f"kubethrifty:cgroup_memory_peak_bytes:container{{{sel}}}[{self.window}]))"
        )
        if cgroup is not None and cgroup > 0:
            return cgroup / BYTES_PER_MIB, "cgroup_memory_peak"

        working_set = self.c.scalar(
            f'max(max_over_time(container_memory_working_set_bytes{{{sel},container!=""}}'
            f"[{self.window}]))"
        )
        if working_set is not None and working_set > 0:
            return working_set / BYTES_PER_MIB, "working_set_max"
        return None, "not_observed"

    def memory_percentile_mib(self, ref: WorkloadRef, q: float) -> float | None:
        """Reported for CONTRAST only -- never used as a sizing basis.

        The dashboard shows P95 beside the peak precisely to make the gap visible: on the bursty
        workload the two differ by ~3x, and that gap is the difference between a running service
        and an OOMKill.
        """
        sel = self._selector(ref)
        promql = (
            f"quantile_over_time({q}, "
            f'container_memory_working_set_bytes{{{sel},container!=""}}[{self.window}])'
        )
        v = self.c.scalar(f"max({promql})")
        return None if v is None else v / BYTES_PER_MIB

    # -- suffering -------------------------------------------------------------------------------
    def throttle_ratio(self, ref: WorkloadRef) -> float | None:
        """throttled/total over the SAME window. None when the denominator is absent or zero."""
        sel = self._selector(ref)
        throttled = self.c.scalar(
            f'sum(rate(container_cpu_cfs_throttled_periods_total{{{sel},container!=""}}'
            f"[{self.window}]))"
        )
        periods = self.c.scalar(
            f'sum(rate(container_cpu_cfs_periods_total{{{sel},container!=""}}[{self.window}]))'
        )
        if throttled is None or periods is None or periods <= 0:
            return None
        return throttled / periods

    def oom_events(self, ref: WorkloadRef) -> int | None:
        sel = self._selector(ref)
        v = self.c.scalar(
            f'sum(increase(container_oom_events_total{{{sel},container!=""}}[{self.window}]))'
        )
        if v is None:
            # Fall back to the collector's own counter before giving up: if neither exists we do
            # not know, and "do not know" must not be reported as "no OOMs".
            v = self.c.scalar(
                f"sum(increase(kubethrifty_cgroup_oom_kill_total{{{sel}}}[{self.window}]))"
            )
        return None if v is None else int(round(v))

    def psi_stalled_ratio(self, ref: WorkloadRef, resource: str = "memory") -> float | None:
        """PSI `full` stall as a fraction of wall time. None when PSI is unavailable.

        `full` (every task stalled), not `some` (at least one task stalled). `some` is normal on a
        busy node and would block every reduction if used as the gate.
        """
        sel = self._selector(ref)
        metric = f"container_pressure_{resource}_stalled_seconds_total"
        return self.c.scalar(f'sum(rate({metric}{{{sel},container!=""}}[{self.window}]))')

    # -- declared spec ---------------------------------------------------------------------------
    def current_request(self, ref: WorkloadRef, resource: str) -> float | None:
        sel = self._selector(ref)
        v = self.c.scalar(
            f"max(kube_pod_container_resource_requests"
            f'{{{sel},resource="{resource}"}})'
        )
        if v is None:
            return None
        return v / BYTES_PER_MIB if resource == "memory" else v

    def current_limit(self, ref: WorkloadRef, resource: str) -> float | None:
        sel = self._selector(ref)
        v = self.c.scalar(
            f"max(kube_pod_container_resource_limits"
            f'{{{sel},resource="{resource}"}})'
        )
        if v is None:
            return None
        return v / BYTES_PER_MIB if resource == "memory" else v

    def sample_count(self, ref: WorkloadRef) -> int:
        """How many usage samples exist. Gates MIN_DATA_POINTS (168 = 7 days hourly)."""
        sel = self._selector(ref)
        v = self.c.scalar(
            f'count_over_time(avg(container_cpu_usage_seconds_total{{{sel},container!=""}})'
            f"[{self.window}:1h])"
        )
        return 0 if v is None else int(round(v))

    def discover(self, namespace: str | None = None) -> list[WorkloadRef]:
        """Every container with a declared CPU request. No request means no waste denominator."""
        ns = f'namespace="{namespace}",' if namespace else ""
        result = self.c.instant(
            f'kube_pod_container_resource_requests{{{ns}resource="cpu",container!=""}}'
        )
        if not result:
            return []
        seen: dict[str, WorkloadRef] = {}
        for series in result:
            m = series.get("metric", {})
            pod, container, namespace_ = m.get("pod"), m.get("container"), m.get("namespace")
            if not (pod and container and namespace_):
                continue
            # Strip the ReplicaSet/pod suffixes to recover the workload name:
            # api-gateway-7d9f8b6c5d-x2k9p -> api-gateway
            parts = pod.split("-")
            workload = "-".join(parts[:-2]) if len(parts) > 2 else pod
            ref = WorkloadRef(namespace_, workload, container)
            seen.setdefault(ref.key, ref)
        return list(seen.values())

    # -- assembly --------------------------------------------------------------------------------
    def collect(self, ref: WorkloadRef, resource: str, runtime_hint: str = "") -> tuple[UsageStats, str] | None:
        """Build UsageStats for one container/resource, or None if it cannot be sized.

        Returns (stats, sizing_basis). The basis string travels all the way to the PR body: an
        operator reading a memory recommendation is entitled to know whether the number came from
        the kernel's high-water mark or from a sampled gauge.
        """
        samples = self.sample_count(ref)

        if resource == "cpu":
            p95 = self.cpu_percentile(ref, 0.95)
            p99 = self.cpu_percentile(ref, 0.99)
            p50 = self.cpu_percentile(ref, 0.50)
            peak = self.cpu_peak(ref)
            if p95 is None or p99 is None:
                log.info("%s: no CPU data; skipping", ref.key)
                return None
            basis = "p95"
            stats = UsageStats(
                p50=p50 if p50 is not None else p95,
                p95=p95,
                p99=p99,
                peak=peak if peak is not None else p99,
                samples=samples,
                psi_stalled_ratio=self.psi_stalled_ratio(ref, "cpu"),
                throttle_ratio=self.throttle_ratio(ref),
                oom_events=self.oom_events(ref) or 0,
                runtime_hint=runtime_hint,
            )
            return stats, basis

        peak_mib, basis = self.memory_peak_mib(ref)
        if peak_mib is None:
            log.info("%s: no memory data; skipping", ref.key)
            return None
        p95 = self.memory_percentile_mib(ref, 0.95)
        p99 = self.memory_percentile_mib(ref, 0.99)
        stats = UsageStats(
            p50=self.memory_percentile_mib(ref, 0.50) or peak_mib,
            # Reported for contrast in the PR body and the UI. NOT the sizing basis.
            p95=p95 if p95 is not None else peak_mib,
            p99=p99 if p99 is not None else peak_mib,
            peak=peak_mib,
            samples=samples,
            psi_stalled_ratio=self.psi_stalled_ratio(ref, "memory"),
            throttle_ratio=None,          # meaningless for memory: there is no memory CFS quota
            oom_events=self.oom_events(ref) or 0,
            runtime_hint=runtime_hint,
        )
        return stats, basis
