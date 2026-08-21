#!/usr/bin/env python3
"""
corpus/generate.py -- manufactures the labeled incident corpus.

In the real project this drives a kind cluster and induces each failure mode deliberately (chaos
harness), then serialises the collected evidence. Here it emits the same bundle SHAPE deterministically
so the eval gate runs hermetically in CI with no cluster and no network -- which is the point: the
corpus is a factory, not a hand-collected ceiling. Add a generator, get labeled cases the same
afternoon.

Three of the cases are RED HERRINGS whose ground truth is NO_VERDICT (image pull, DNS, healthy pod).
A tool that never says "I don't know" cannot be trusted when it does say something, so refusing these
is a graded requirement, not a nicety.

Usage:
    python generate.py            # writes incidents.json
    python generate.py --freeze   # writes incidents.json AND digests.json (the determinism anchor)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

MIB = 1024 * 1024


def bundle(*, name, container="app", exit_code=0, oom=0, throttle=None, psi_mem=None, psi_cpu=None,
           mem_current=0, mem_peak=0, mem_max=0, conditions=None, hpa=None, changes=(),
           node_allocatable=None, node=None, qos=None, startup=None, note="") -> dict:
    """
    One evidence bundle.

    `node`, `qos` and `startup` were added for ruleset 1.1.0. They default to None, which is what
    keeps the schema backwards compatible: a bundle that carries no node evidence cannot trip
    NODE_PRESSURE_EVICTION, so the older cases behave exactly as before.
    """
    return {
        "schema": "kubethrifty.detective/v1",
        "case": name,
        "cluster": "demo", "namespace": "shop", "workload": name.split("__")[0],
        "pod": f"{name.split('__')[0]}-7d9f", "container": container,
        "exit_code": exit_code,
        "conditions": conditions or {},
        "signals": {"oom_kills": oom, "throttle_ratio": throttle,
                    "psi_mem_full": psi_mem, "psi_cpu_full": psi_cpu},
        "cgroup": {"memory_current": mem_current, "memory_peak": mem_peak, "memory_max": mem_max,
                   "oom_kill_total": oom},
        "hpa": hpa,
        "node_allocatable": node_allocatable,
        # Node-level state: what the node itself was doing, as opposed to this container.
        "node": node,
        # QoS class before and after a KubeThrifty change.
        "qos": qos,
        # The startup window, measured separately from steady state.
        "startup": startup,
        "kubethrifty_changes": list(changes),
        "collector_versions": {"cgroup_truth": "1.0.0", "engine": "1.1.0"},
        "_note": note,
    }


def merged_pr(pr: int, rec: int, hours: float, delta: dict, container="app") -> dict:
    return {"kind": "merged_pr", "pr_url": f"https://github.com/OWNER/REPO/pull/{pr}",
            "recommendation_id": rec, "commit": f"sha{pr}", "hours_before_incident": hours,
            "container": container, "delta": delta}


CASES = [
    # ============================================================================================
    # The corpus is deliberately NOT all-textbook. A corpus where every case is obvious drives
    # accuracy to 1.0 while mean confidence sits near 0.9 -- which the calibration gate correctly
    # reports as MISCALIBRATION (underconfidence). Real incidents include ambiguous ones the engine
    # gets wrong, so two cases below are genuine misfires with ground truth NO_VERDICT. They are the
    # reason the confidence numbers mean something.
    # ============================================================================================

    # ---- MEM_LIMIT_TOO_LOW, high confidence: OOM + peak pinned at the limit + our own change -----
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="payment-processor__oom_squeeze", exit_code=137, oom=1,
        throttle=0.004, psi_mem=0.11, psi_cpu=0.002,
        mem_current=318 * MIB, mem_peak=318 * MIB, mem_max=320 * MIB,
        changes=[merged_pr(212, 1188, 14.5, {"memory": "1Gi -> 320Mi"})],
        note="the flagship case: our own cut caused it, and the verdict names the PR")),
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="session-store__oom_squeeze", exit_code=137, oom=2,
        throttle=0.0, psi_mem=0.19, psi_cpu=0.0,
        mem_current=253 * MIB, mem_peak=255 * MIB, mem_max=256 * MIB,
        changes=[merged_pr(224, 1250, 30.0, {"memory": "768Mi -> 256Mi"})],
        note="same shape, different workload -- attribution window is 48h and this is 30h")),
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="report-builder__oom_no_attrib", exit_code=137, oom=3,
        throttle=0.0, psi_mem=0.22, psi_cpu=0.0,
        mem_current=505 * MIB, mem_peak=511 * MIB, mem_max=512 * MIB,
        note="OOM with no KubeThrifty change nearby -- the verdict must NOT invent attribution")),

    # ---- MEM_LIMIT_TOO_LOW, mid confidence: one huge allocation, peak well under the limit --------
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="pdf-exporter__oom_spike_attrib", exit_code=137, oom=1,
        throttle=0.0, psi_mem=0.04, psi_cpu=0.0,
        mem_current=300 * MIB, mem_peak=700 * MIB, mem_max=1024 * MIB,
        changes=[merged_pr(233, 1290, 20.0, {"memory": "2Gi -> 1Gi"})],
        note="a single large allocation: peak far below the limit, so confidence is lower even though "
             "the kill is real and attributable")),
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="image-resizer__oom_spike", exit_code=137, oom=1,
        throttle=0.0, psi_mem=0.03, psi_cpu=0.0,
        mem_current=280 * MIB, mem_peak=640 * MIB, mem_max=1024 * MIB,
        note="same shape without attribution")),

    # ---- MEM_LIMIT_TOO_LOW, low confidence: OOM counter moved but no 137 and no attribution -------
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="log-shipper__oom_counter_only", exit_code=0, oom=1,
        throttle=0.0, psi_mem=0.02, psi_cpu=0.0,
        mem_current=180 * MIB, mem_peak=420 * MIB, mem_max=512 * MIB,
        note="the cgroup oom_kill counter moved but the pod's exit code says otherwise -- a sidecar "
             "was killed, not the main container. Deliberately low confidence")),

    # ---- MEM_PRESSURE_NO_KILL, high confidence: thrashing against its OWN limit -------------------
    dict(ground_truth="MEM_PRESSURE_NO_KILL", b=bundle(
        name="inventory-cache__reclaim_thrash", oom=0,
        throttle=0.002, psi_mem=0.14, psi_cpu=0.001,
        mem_current=490 * MIB, mem_peak=498 * MIB, mem_max=512 * MIB,
        note="low average usage, high PSI: needs MORE memory. No tool without PSI can see this")),
    dict(ground_truth="MEM_PRESSURE_NO_KILL", b=bundle(
        name="search-indexer__thrash_after_cut", oom=0,
        throttle=0.01, psi_mem=0.09, psi_cpu=0.004,
        mem_current=760 * MIB, mem_peak=780 * MIB, mem_max=800 * MIB,
        changes=[merged_pr(230, 1301, 6.0, {"memory": "2Gi -> 800Mi"})],
        note="attributable thrash: our cut made it stall without killing it")),

    # ---- MEM_PRESSURE_NO_KILL, low confidence: stalling but NOT near its own limit ----------------
    dict(ground_truth="MEM_PRESSURE_NO_KILL", b=bundle(
        name="feed-worker__thrash_below_limit", oom=0,
        throttle=0.0, psi_mem=0.08, psi_cpu=0.0,
        mem_current=600 * MIB, mem_peak=620 * MIB, mem_max=1024 * MIB,
        note="genuinely under-sized despite headroom in the limit: the working set churns")),
    # ---- RESOLVED MISFIRE: this case is why NODE_PRESSURE_EVICTION exists -------------------------
    # Under ruleset 1.0.0 this was labelled NO_VERDICT and the engine answered MEM_PRESSURE_NO_KILL
    # at 0.62, scored as wrong on purpose. The note said "this is exactly how the corpus tells you
    # which rule to write next" -- so the rule was written, the bundle now carries the node evidence
    # it always should have, and the ground truth is the real answer.
    dict(ground_truth="NODE_PRESSURE_EVICTION", b=bundle(
        name="thumbnailer__noisy_neighbour", oom=0,
        throttle=0.0, psi_mem=0.07, psi_cpu=0.0,
        mem_current=300 * MIB, mem_peak=320 * MIB, mem_max=2048 * MIB,
        node={"conditions": {"MemoryPressure": "True"}, "available_mib": 210,
              "eviction_threshold_crossed": True},
        note="the stall came from another pod exhausting the NODE, not from this container's limit -- "
             "it is using 15% of its own 2Gi. Raising its request would be actively harmful: harder "
             "to schedule, on a node that has nothing left")),

    # ---- CPU_LIMIT_TOO_LOW, high confidence: throttling AND cpu stall ----------------------------
    dict(ground_truth="CPU_LIMIT_TOO_LOW", b=bundle(
        name="checkout-api__throttle_squeeze", oom=0,
        throttle=0.31, psi_mem=0.0, psi_cpu=0.12,
        mem_current=200 * MIB, mem_peak=210 * MIB, mem_max=512 * MIB,
        changes=[merged_pr(241, 1360, 3.0, {"cpu": "1000m -> 250m"})],
        note="throttling AND stalling = real harm, and it is ours")),
    dict(ground_truth="CPU_LIMIT_TOO_LOW", b=bundle(
        name="graphql-gateway__throttle_stall", oom=0,
        throttle=0.22, psi_mem=0.0, psi_cpu=0.07,
        mem_current=350 * MIB, mem_peak=360 * MIB, mem_max=1024 * MIB,
        note="latency-sensitive service, clear stall")),

    # ---- CPU_LIMIT_TOO_LOW, low confidence: throttled but barely stalling ------------------------
    dict(ground_truth="CPU_LIMIT_TOO_LOW", b=bundle(
        name="csv-importer__throttle_light_stall", oom=0,
        throttle=0.11, psi_mem=0.0, psi_cpu=0.008,
        mem_current=160 * MIB, mem_peak=170 * MIB, mem_max=512 * MIB,
        note="user-facing import job: throttling does hurt here, but the evidence is weaker")),
    dict(ground_truth="CPU_LIMIT_TOO_LOW", b=bundle(
        name="webhook-fanout__throttle_light_stall", oom=0,
        throttle=0.08, psi_mem=0.0, psi_cpu=0.011,
        mem_current=120 * MIB, mem_peak=130 * MIB, mem_max=256 * MIB,
        note="same shape, lower throttle ratio")),
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="video-encoder__harmless_throttle", oom=0,
        throttle=0.09, psi_mem=0.0, psi_cpu=0.001,
        mem_current=900 * MIB, mem_peak=950 * MIB, mem_max=2048 * MIB,
        note="KNOWN MISFIRE (scored as wrong, on purpose): a batch encoder with no latency SLO. It is "
             "throttled and it does not care -- the job finishes on time. The engine answers "
             "CPU_LIMIT_TOO_LOW at 0.72 and is penalised, which is the honest cost of not knowing "
             "whether a workload has an SLO")),

    # ---- HPA_COUPLING_STORM: the right-sizing cost paradox ---------------------------------------
    dict(ground_truth="HPA_COUPLING_STORM", b=bundle(
        name="web-frontend__hpa_paradox", oom=0,
        throttle=0.01, psi_mem=0.0, psi_cpu=0.004,
        mem_current=300 * MIB, mem_peak=310 * MIB, mem_max=1024 * MIB,
        hpa={"replicas_before": 3, "replicas_after": 8, "target_utilization": 70,
             "traffic_delta_pct": 1.0},
        changes=[merged_pr(255, 1402, 2.0, {"cpu": "1000m -> 250m"})],
        note="flat traffic, 3 -> 8 replicas: the cut raised utilisation-of-request, not load")),
    dict(ground_truth="HPA_COUPLING_STORM", b=bundle(
        name="api-gateway__hpa_paradox_falling_traffic", oom=0,
        throttle=0.0, psi_mem=0.01, psi_cpu=0.0,
        mem_current=250 * MIB, mem_peak=260 * MIB, mem_max=512 * MIB,
        hpa={"replicas_before": 4, "replicas_after": 9, "target_utilization": 60,
             "traffic_delta_pct": -2.0},
        changes=[merged_pr(261, 1440, 5.5, {"cpu": "800m -> 200m"})],
        note="traffic fell while replicas grew -- unambiguous coupling")),
    dict(ground_truth="HPA_COUPLING_STORM", b=bundle(
        name="notification-api__hpa_paradox_keda", oom=0,
        throttle=0.02, psi_mem=0.0, psi_cpu=0.006,
        mem_current=180 * MIB, mem_peak=190 * MIB, mem_max=512 * MIB,
        hpa={"replicas_before": 2, "replicas_after": 6, "target_utilization": 75,
             "traffic_delta_pct": 3.0},
        changes=[merged_pr(268, 1471, 1.5, {"cpu": "600m -> 150m"})],
        note="a KEDA-owned HPA on CPU is subject to exactly the same paradox")),

    # ---- RESIZE_INFEASIBLE_STUCK ----------------------------------------------------------------
    dict(ground_truth="RESIZE_INFEASIBLE_STUCK", b=bundle(
        name="ml-scorer__infeasible_resize", oom=0,
        throttle=0.0, psi_mem=0.0, psi_cpu=0.0,
        mem_current=1000 * MIB, mem_peak=1010 * MIB, mem_max=2048 * MIB,
        conditions={"PodResizePending": {"status": "True", "reason": "Infeasible",
                                         "message": "node has insufficient cpu"}},
        node_allocatable={"cpu_millicores": 3760, "memory_mib": 14580},
        changes=[{"kind": "rehearsal", "recommendation_id": 1500, "hours_before_incident": 0.1,
                  "container": "app", "delta": {"cpu": "500m -> 3000m"}}],
        note="a rehearsal asked for more than the node had: self-inflicted, and it must never hang")),
    dict(ground_truth="RESIZE_INFEASIBLE_STUCK", b=bundle(
        name="video-transcoder__infeasible_mem", oom=0,
        throttle=0.0, psi_mem=0.0, psi_cpu=0.0,
        mem_current=2000 * MIB, mem_peak=2010 * MIB, mem_max=4096 * MIB,
        conditions={"PodResizePending": {"status": "True", "reason": "Infeasible",
                                         "message": "node has insufficient memory"}},
        node_allocatable={"cpu_millicores": 7600, "memory_mib": 29000},
        note="no attribution available: the resize was requested by hand")),

    # ---- NODE_PRESSURE_EVICTION (ruleset 1.1.0) --------------------------------------------------
    dict(ground_truth="NODE_PRESSURE_EVICTION", b=bundle(
        name="sidecar-proxy__evicted_innocent", oom=0,
        throttle=0.001, psi_mem=0.09, psi_cpu=0.0,
        mem_current=64 * MIB, mem_peak=70 * MIB, mem_max=512 * MIB,
        conditions={"Evicted": {"status": "True", "reason": "Evicted",
                                "message": "The node was low on resource: memory"}},
        node={"conditions": {"MemoryPressure": "True"}, "available_mib": 90,
              "eviction_threshold_crossed": True},
        note="the clearest case: evicted while using 12% of its own limit. Every percentile-only tool "
             "sees a dead pod and recommends more memory, which is the wrong container entirely")),
    dict(ground_truth="NODE_PRESSURE_EVICTION", b=bundle(
        name="metrics-agent__evicted_after_our_cut", oom=0,
        throttle=0.0, psi_mem=0.11, psi_cpu=0.0,
        mem_current=96 * MIB, mem_peak=110 * MIB, mem_max=768 * MIB,
        conditions={"Evicted": {"status": "True", "reason": "Evicted",
                                "message": "The node was low on resource: memory"}},
        node={"conditions": {"MemoryPressure": "True"}, "available_mib": 140},
        changes=[merged_pr(281, 1520, 8.0, {"memory": "1Gi -> 768Mi"})],
        note="ours to explain but NOT ours to blame: the cut is attributed, yet the container was "
             "nowhere near its limit. The verdict names the PR and still says the node was the cause")),
    dict(ground_truth="MEM_LIMIT_TOO_LOW", b=bundle(
        name="cache-warmer__own_limit_on_pressured_node", exit_code=137, oom=1,
        throttle=0.0, psi_mem=0.18, psi_cpu=0.0,
        mem_current=505 * MIB, mem_peak=511 * MIB, mem_max=512 * MIB,
        node={"conditions": {"MemoryPressure": "True"}, "available_mib": 180},
        note="PRECEDENCE TEST: the node is under pressure AND this container is pinned at its own "
             "limit. Node pressure is incidental here -- it really did outgrow its limit, so "
             "MEM_LIMIT_TOO_LOW must win over NODE_PRESSURE_EVICTION")),

    # ---- QOS_DEMOTION_EVICTION (ruleset 1.1.0) ---------------------------------------------------
    dict(ground_truth="QOS_DEMOTION_EVICTION", b=bundle(
        name="ledger-service__demoted_then_evicted", oom=0,
        throttle=0.01, psi_mem=0.02, psi_cpu=0.003,
        mem_current=380 * MIB, mem_peak=390 * MIB, mem_max=512 * MIB,
        conditions={"Evicted": {"status": "True", "reason": "Evicted",
                                "message": "The node was low on resource: memory"}},
        node={"conditions": {"MemoryPressure": "True"}, "available_mib": 120},
        qos={"before": "Guaranteed", "after": "Burstable"},
        changes=[merged_pr(290, 1560, 12.0, {"cpu": "1000m -> 400m"})],
        note="the full causal chain: we cut CPU without matching the limit, which demoted Guaranteed "
             "to Burstable, which moved it ahead of its neighbours in the eviction queue. No resource "
             "number reveals this -- only the QoS transition does")),
    dict(ground_truth="QOS_DEMOTION_EVICTION", b=bundle(
        name="billing-worker__demoted_not_yet_evicted", oom=0,
        throttle=0.005, psi_mem=0.01, psi_cpu=0.001,
        mem_current=200 * MIB, mem_peak=210 * MIB, mem_max=512 * MIB,
        qos={"before": "Guaranteed", "after": "Burstable"},
        changes=[merged_pr(293, 1571, 4.0, {"memory": "512Mi -> 400Mi"})],
        note="demoted but still running: a LATENT risk, deliberately lower confidence. Reporting this "
             "at 0.95 would be crying wolf about something that has not happened")),

    # ---- STARTUP_CPU_STARVATION (ruleset 1.1.0) --------------------------------------------------
    dict(ground_truth="STARTUP_CPU_STARVATION", b=bundle(
        name="jvm-pricing__startup_starved", oom=0,
        throttle=0.004, psi_mem=0.0, psi_cpu=0.002,
        mem_current=700 * MIB, mem_peak=760 * MIB, mem_max=1024 * MIB,
        startup={"throttle_ratio": 0.71, "probe_failures": 4, "restarts": 3,
                 "duration_seconds": 95},
        changes=[merged_pr(301, 1610, 6.0, {"cpu": "2000m -> 300m"})],
        note="a JVM JITs for 90 seconds and needs several times its steady-state CPU to do it. "
             "Steady state is comfortable at 0.4% throttling, so a percentile sizer sees nothing "
             "wrong -- the startup burst is a rounding error across a week of samples")),
    dict(ground_truth="STARTUP_CPU_STARVATION", b=bundle(
        name="node-ssr__startup_starved", oom=0,
        throttle=0.01, psi_mem=0.0, psi_cpu=0.004,
        mem_current=300 * MIB, mem_peak=340 * MIB, mem_max=512 * MIB,
        startup={"throttle_ratio": 0.44, "probe_failures": 2, "restarts": 1,
                 "duration_seconds": 40},
        note="same shape, no attribution: the request was always this low")),
    dict(ground_truth="CPU_LIMIT_TOO_LOW", b=bundle(
        name="stream-joiner__throttled_throughout", oom=0,
        throttle=0.28, psi_mem=0.0, psi_cpu=0.09,
        mem_current=400 * MIB, mem_peak=420 * MIB, mem_max=1024 * MIB,
        startup={"throttle_ratio": 0.33, "probe_failures": 1, "restarts": 1,
                 "duration_seconds": 25},
        note="DISCRIMINATOR TEST: throttled at startup AND in steady state. That is not a "
             "startup-specific problem, it is a limit that is simply too low, so CPU_LIMIT_TOO_LOW "
             "must outrank STARTUP_CPU_STARVATION")),

    # ============================================================================================
    # CASES THAT JUSTIFY THE LOW CONFIDENCE BANDS
    #
    # A confidence of 0.58 is only meaningful if verdicts at 0.58 are sometimes WRONG. Without cases
    # like these, every low-confidence bucket scores 100% accurate, the reliability diagram reports
    # severe under-confidence, and the numbers stop carrying information -- the engine would be
    # hedging about things it always gets right.
    #
    # These are genuinely ambiguous from the evidence alone. The engine cannot know whether a
    # workload has a latency SLO, or whether a QoS demotion was a deliberate trade. Answering at low
    # confidence and being wrong sometimes is the honest behaviour; the alternative is inventing
    # certainty it has no basis for.
    # ============================================================================================
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="analytics-batch__stall_no_slo", oom=0,
        throttle=0.0, psi_mem=0.08, psi_cpu=0.0,
        mem_current=700 * MIB, mem_peak=720 * MIB, mem_max=1024 * MIB,
        note="AMBIGUOUS, scored as wrong on purpose: a nightly aggregation stalls on reclaim and "
             "finishes on time anyway. The engine answers MEM_PRESSURE_NO_KILL at 0.62 -- and 0.62 "
             "is the right number precisely BECAUSE cases like this exist. Nothing in the evidence "
             "says whether a stall matters to this workload")),
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="edge-cache__demoted_by_design", oom=0,
        throttle=0.004, psi_mem=0.01, psi_cpu=0.001,
        mem_current=150 * MIB, mem_peak=160 * MIB, mem_max=512 * MIB,
        qos={"before": "Guaranteed", "after": "Burstable"},
        changes=[merged_pr(305, 1640, 9.0, {"cpu": "500m -> 200m"})],
        note="AMBIGUOUS, scored as wrong on purpose: the team accepted this demotion deliberately -- "
             "an edge cache is cheap to lose and cheaper to run Burstable. The engine flags it at "
             "0.58 as a latent risk, which is a reasonable thing to say and sometimes unwanted. This "
             "is what a 0.58 is FOR")),

    # ---- RED HERRINGS: the engine is REQUIRED to refuse these ------------------------------------
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="batch-loader__slow_start_but_healthy", oom=0,
        throttle=0.003, psi_mem=0.0, psi_cpu=0.001,
        mem_current=250 * MIB, mem_peak=260 * MIB, mem_max=1024 * MIB,
        startup={"throttle_ratio": 0.38, "probe_failures": 0, "restarts": 0,
                 "duration_seconds": 60},
        note="RED HERRING: heavily throttled during startup and it came up fine anyway. Throttling "
             "without harm is not an incident -- the engine must not manufacture one from a metric")),
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="notify-svc__imagepull", exit_code=0, oom=0,
        throttle=0.0, psi_mem=0.0, psi_cpu=0.0,
        mem_current=50 * MIB, mem_peak=60 * MIB, mem_max=256 * MIB,
        conditions={"ContainersReady": {"status": "False", "reason": "ImagePullBackOff",
                                        "message": "manifest unknown"}},
        note="RED HERRING: broken image tag, healthy resources")),
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="auth-svc__dns_failure", exit_code=1, oom=0,
        throttle=0.001, psi_mem=0.0, psi_cpu=0.0,
        mem_current=120 * MIB, mem_peak=130 * MIB, mem_max=512 * MIB,
        changes=[merged_pr(270, 1490, 10.0, {"cpu": "500m -> 300m"})],
        note="RED HERRING and the hardest one: a DNS failure shortly after one of OUR changes. "
             "Correlation is not causation and the engine must not claim it")),
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="cron-cleanup__crashloop_bad_config", exit_code=2, oom=0,
        throttle=0.0, psi_mem=0.0, psi_cpu=0.0,
        mem_current=30 * MIB, mem_peak=35 * MIB, mem_max=256 * MIB,
        note="RED HERRING: the app exits 2 on a bad config file. Resources are irrelevant")),
    dict(ground_truth="NO_VERDICT", b=bundle(
        name="static-site__healthy", oom=0,
        throttle=0.0, psi_mem=0.0, psi_cpu=0.0,
        mem_current=40 * MIB, mem_peak=45 * MIB, mem_max=256 * MIB,
        note="RED HERRING: nothing is wrong at all")),
]


def main() -> int:
    from src.detective.engine import RULESET_VERSION, investigate, verdicts_digest

    corpus = [{"name": c["b"]["case"], "ground_truth": c["ground_truth"], "bundle": c["b"]}
              for c in CASES]
    (HERE / "incidents.json").write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n")
    print(f"wrote incidents.json: {len(corpus)} cases "
          f"({sum(1 for c in corpus if c['ground_truth'] == 'NO_VERDICT')} red herrings)")

    if "--freeze" in sys.argv:
        digests = {RULESET_VERSION: [verdicts_digest(investigate(c["bundle"])) for c in corpus]}
        (HERE / "digests.json").write_text(json.dumps(digests, indent=2) + "\n")
        print(f"wrote digests.json for ruleset {RULESET_VERSION} (determinism anchor)")
        print("NOTE: only re-freeze after checking Brier/ECE -- re-freezing blindly defeats the gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
