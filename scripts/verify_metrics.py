#!/usr/bin/env python3
"""
Phase 2 exit criterion: prove every metric family the analyser depends on is actually populated.

This exists because the expensive failure mode is not "Prometheus is down" -- that is obvious. It
is a scrape config that looks fine and silently omits one family, so the analyser runs, produces
recommendations, and quietly bases them on a partial signal set. Notably:

  * container_pressure_*     absent => no PSI => `partial` evidence tier for everything. This must
                             be a recorded decision, never a discovery in week five.
  * container_oom_events_total
                             absent => the sizer cannot see observed death, and will happily
                             recommend cutting a workload that OOMKills every night.
  * kube_pod_container_resource_requests
                             absent => no denominator => no waste percentage at all.

A family that returns zero series is reported as NOT OBSERVED, never as "zero pressure". That
distinction is the same null-safety rule the rest of the codebase follows.

Usage:
    kubectl -n monitoring port-forward svc/monitoring-prometheus 9090:9090 &
    python scripts/verify_metrics.py [--url http://localhost:9090]

(The service name comes from the Helm release name: `monitoring` + `-prometheus`. It is NOT
`monitoring-kube-prom-prometheus`; that form belongs to older chart versions.)

Exit codes: 0 = all required families present. 1 = a required family is missing.
Optional families being absent lowers the evidence tier but does not fail the check.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# (metric, required, why it matters)
FAMILIES: list[tuple[str, bool, str]] = [
    ("container_cpu_usage_seconds_total", True,
     "CPU usage. Without it there is no P95 and no CPU recommendation."),
    ("container_memory_working_set_bytes", True,
     "Memory usage. The fallback basis when cgroup memory.peak is unavailable."),
    ("kube_pod_container_resource_requests", True,
     "What the workload ASKED for. The denominator of every waste percentage."),
    ("kube_pod_container_resource_limits", True,
     "Limits, needed to detect QoS class and to avoid proposing a limit below a live one."),
    ("container_cpu_cfs_periods_total", True,
     "Throttle denominator. Ratios only; raw counters scale with window and replica count."),
    ("container_cpu_cfs_throttled_periods_total", True,
     "Throttle numerator. >1% blocks any CPU limit reduction."),
    ("container_oom_events_total", True,
     "Observed death. A counter, deliberately -- not the flapping last_terminated_reason gauge."),
    ("kube_pod_container_status_restarts_total", True,
     "RESTART_TOLERANCE is 0: any restart in a rehearsal window forces `regressed`."),
    ("container_pressure_cpu_stalled_seconds_total", False,
     "PSI CPU stall. Absent => partial evidence tier (see ADR 0001)."),
    ("container_pressure_memory_stalled_seconds_total", False,
     "PSI memory stall. This is what distinguishes 'ran hot and was fine' from 'ran hot and stalled'."),
    ("kube_horizontalpodautoscaler_spec_target_metric", False,
     "HPA targets, for the collision guard. Absent only means no HPAs are deployed yet."),
    ("kube_poddisruptionbudget_status_desired_healthy", False,
     "PDB state, required by the rehearsal preflight before it will touch a live pod."),
]


def query(base: str, expr: str, timeout: float = 8.0) -> tuple[bool, int, str]:
    """Return (ok, series_count, note). Never raises: a probe that crashes tells us nothing."""
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed scheme)
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return False, 0, f"HTTP {e.code}"
    except Exception as e:                                    # noqa: BLE001
        return False, 0, type(e).__name__
    if payload.get("status") != "success":
        return False, 0, str(payload.get("error", "query failed"))
    return True, len(payload.get("data", {}).get("result", [])), ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:9090")
    args = ap.parse_args(argv)

    ok, _, note = query(args.url, "up")
    if not ok:
        print(f"cannot reach Prometheus at {args.url}: {note}", file=sys.stderr)
        return 1

    print(f"{'metric family':52s} {'req':>4s} {'series':>7s}  status")
    print("-" * 92)

    missing_required: list[tuple[str, str]] = []
    missing_optional: list[tuple[str, str]] = []

    for metric, required, why in FAMILIES:
        ok, count, note = query(args.url, f"count({metric})")
        # count() over an empty family returns an empty result, not 0 -- which is exactly the
        # null-vs-zero distinction the whole project insists on.
        present = ok and count > 0
        status = "OK" if present else ("MISSING" if required else "not observed")
        print(f"{metric:52s} {'yes' if required else ' no':>4s} {count:7d}  {status}")
        if not present:
            (missing_required if required else missing_optional).append((metric, why))

    print("-" * 92)

    for metric, why in missing_optional:
        print(f"  note: {metric} is absent -> {why}")

    if missing_optional and any("pressure" in m for m, _ in missing_optional):
        print("\nPSI is NOT available: every recommendation must ship evidence_tier='partial'.")
        print("That is a supported path, but it has to be a recorded decision -- update ADR 0001.")

    if missing_required:
        print("\nFAIL: required metric families missing:", file=sys.stderr)
        for metric, why in missing_required:
            print(f"  - {metric}: {why}", file=sys.stderr)
        print("\nMost likely cause: kubelet.serviceMonitor.cAdvisor is false, or honorLabels is not "
              "set so every series collapsed onto the kubelet target.", file=sys.stderr)
        return 1

    print("\nPASS: every required metric family is populated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
