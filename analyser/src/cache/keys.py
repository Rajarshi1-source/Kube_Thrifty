#!/usr/bin/env python3
"""
keys.py -- every Valkey key name and TTL, in one file.

Keys are built by functions rather than f-strings at the call site. Two reasons that matter:

  * A typo'd key is not an error, it is a permanent cache miss. `f"dashboard:rec:{cid}"` (singular)
    silently never hits the entry written under `dashboard:recs:{cid}`, and nothing fails -- the
    system just quietly gets slower and no test catches it.
  * The dashboard (TypeScript) and the analyser (Python) share this namespace. Keeping the shapes
    in one auditable place is what lets `dashboard/lib/cache/keys.ts` mirror it exactly.

TTLs encode how fast each thing goes stale, not a uniform guess:

  RECS   6h  -- recommendations only change when the analyser runs, every 6h.
  METRIC 15m -- pod metrics move continuously; 15m is fresh enough to act on, long enough to absorb
                a dashboard refresh storm.
  PROMQL 5m  -- the proxy's own layer, short because it fronts arbitrary range queries.
"""
from __future__ import annotations

import hashlib

# --- TTLs, in seconds -----------------------------------------------------------------------------
TTL_RECOMMENDATIONS = 6 * 60 * 60
TTL_POD_METRICS = 15 * 60
TTL_PROMQL = 5 * 60
TTL_SAVINGS = 60 * 60
TTL_VERDICT = 24 * 60 * 60

# --- stream / lock names --------------------------------------------------------------------------
STREAM_ANALYSIS_JOBS = "analysis-jobs"
STREAM_ANALYSIS_DLQ = "analysis-jobs-dlq"
CONSUMER_GROUP = "analysers"


def recommendations(cluster_id: str) -> str:
    return f"dashboard:recs:{cluster_id}"


def pod_metrics(pod: str, window: str) -> str:
    return f"pod:metrics:{pod}:{window}"


def savings(cluster_id: str, scenario: str) -> str:
    return f"dashboard:savings:{cluster_id}:{scenario}"


def verdict(bundle_sha: str) -> str:
    return f"verdict:{bundle_sha}"


def promql(query: str, start: str = "", end: str = "", step: str = "") -> str:
    """
    Hash the query rather than embed it.

    PromQL contains `{`, `}`, `"`, spaces and newlines -- all legal in a Valkey key, all miserable
    in a MONITOR trace or a `--scan` glob. The hash also bounds key length, which raw queries do
    not: a 4KB selector would otherwise become a 4KB key.

    start/end/step are part of the identity. Two range queries over the same expression but
    different windows are different answers, and collapsing them would serve one window's data as
    another's.
    """
    material = f"{query}|{start}|{end}|{step}".encode()
    return f"promql:{hashlib.sha256(material).hexdigest()[:32]}"


def analysis_lock(cluster: str) -> str:
    """
    The concurrency guard.

    One analysis per cluster at a time. Two concurrent runs would query the same windows, produce
    the same recommendations and open two competing PRs against the same `values.yaml` -- and if
    rehearsals are enabled, would resize the same pod from two directions at once.
    """
    return f"analysis:lock:{cluster}"


def rehearsal_lock(namespace: str, workload: str) -> str:
    """Blast radius is one pod per workload; this is what enforces 'per workload'."""
    return f"rehearsal:lock:{namespace}:{workload}"
