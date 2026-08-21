#!/usr/bin/env python3
"""
Tests for the HPA-collision guard, the QoS guard, and the bin-packer.

The two properties these protect are the ones that make the product's numbers trustworthy:

  * A reduction that triggers an HPA scale-out costs more than it saves. The guard must catch it.
  * A saving is a NODE-COUNT DELTA. When no node is removed, the figure is zero -- never a sum of
    per-pod millicores dressed up as money.
"""
from __future__ import annotations

import pytest

from src.coupling.detect import CouplingKind, detect
from src.coupling.qos import ALLOW_QOS_DEMOTION, check_transition, classify
from src.coupling.simulate import Verdict, simulate
from src.packing.binpack import (
    InstanceType,
    PodSpec,
    allocatable,
    pack,
    savings_report,
)

# ==================================================================================================
# HPA detection
# ==================================================================================================

def utilisation_hpa(target=70, min_r=2, max_r=10, current=2, resource="cpu"):
    return {
        "metadata": {"name": "api-hpa", "namespace": "shop"},
        "spec": {
            "scaleTargetRef": {"kind": "Deployment", "name": "api-gateway"},
            "minReplicas": min_r,
            "maxReplicas": max_r,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": resource,
                        "target": {"type": "Utilization", "averageUtilization": target},
                    },
                }
            ],
        },
        "status": {"currentReplicas": current},
    }


def test_detects_utilisation_coupling():
    c = detect(utilisation_hpa())
    assert c.kind is CouplingKind.UTILISATION
    assert c.is_coupled
    assert c.target_utilisation == 70
    assert c.edit_target == "HorizontalPodAutoscaler/api-hpa"


def test_average_value_target_is_not_coupled():
    """`AverageValue` is an absolute figure (e.g. 500m). It does not move when the request changes."""
    hpa = utilisation_hpa()
    hpa["spec"]["metrics"][0]["resource"]["target"] = {"type": "AverageValue", "averageValue": "500m"}
    c = detect(hpa)
    assert c.kind is CouplingKind.EXTERNAL
    assert not c.is_coupled


def test_external_metrics_are_not_coupled():
    """Queue depth does not change when a CPU request changes, so refusing here would block a safe
    reduction for no reason."""
    hpa = utilisation_hpa()
    hpa["spec"]["metrics"] = [
        {"type": "External", "external": {"metric": {"name": "queue_depth"}}}
    ]
    assert detect(hpa).kind is CouplingKind.EXTERNAL


def test_keda_ownership_wins_over_metric_shape():
    """
    A KEDA-owned HPA may well contain a Resource/utilisation metric.

    Classifying by metric shape alone would point the PR at an HPA that the KEDA operator reverts on
    its next reconcile -- a change that appears to work and silently does not.
    """
    hpa = utilisation_hpa()
    hpa["metadata"]["ownerReferences"] = [{"kind": "ScaledObject", "name": "api-so"}]
    c = detect(hpa)
    assert c.kind is CouplingKind.KEDA
    assert c.is_coupled
    assert c.edit_target == "ScaledObject/api-so"


def test_keda_detected_by_name_convention():
    """Older KEDA versions omit owner references."""
    hpa = utilisation_hpa()
    hpa["metadata"]["name"] = "keda-hpa-api-so"
    c = detect(hpa, scaled_objects=[{"metadata": {"name": "api-so"}}])
    assert c.kind is CouplingKind.KEDA


def test_no_hpa_is_not_coupled():
    assert detect(None).kind is CouplingKind.NONE


def test_unreadable_hpa_fails_towards_coupled():
    """Guessing 'not coupled' is the direction that causes an outage."""
    hpa = utilisation_hpa()
    hpa["spec"]["metrics"] = []
    c = detect(hpa)
    assert c.kind is CouplingKind.UNKNOWN
    assert c.is_coupled


# ==================================================================================================
# The right-sizing cost paradox
# ==================================================================================================

def test_the_cost_paradox_is_caught():
    """
    THE headline case.

    500m request, 100m used = 20% utilised against a 70% target: comfortable at 2 replicas.
    Cut to 125m and the same real workload reads 80% utilised, which scales OUT to 3 replicas.

    The "saving" trimmed 375m per replica and then added a whole replica. Every per-pod waste metric
    would call that a success.

    80% is chosen deliberately: it is past the HPA's tolerance band (so a scale-out really fires) but
    a 88% co-changed target still sits under the 90% safety ceiling -- so this is the
    `co_change_target` case rather than the outright refusal tested below.
    """
    result = simulate(
        detect(utilisation_hpa(target=70, current=2)),
        current_request=0.5,
        proposed_request=0.125,
        observed_usage=0.1,
    )
    assert result.verdict is Verdict.CO_CHANGE_TARGET
    assert result.would_scale_out
    assert result.replicas_after > result.replicas_before
    assert result.recommended_target is not None
    assert result.recommended_target > 70
    # The reason must name the trade explicitly -- this is what lands in the PR body.
    assert "same pull request" in result.reason.lower()


def test_modest_reduction_is_safe():
    """500m -> 200m with 100m used lands at 50% against a 70% target: no scale-out."""
    result = simulate(
        detect(utilisation_hpa(target=70, current=2)),
        current_request=0.5,
        proposed_request=0.2,
        observed_usage=0.1,
    )
    assert result.verdict is Verdict.SAFE
    assert not result.would_scale_out


def test_tolerance_band_prevents_phantom_scale_outs():
    """The HPA ignores deviations within 10%. Omitting that predicts scale-outs that never fire."""
    # 100m used / 133m request = 75% against a 70% target -> ratio 1.07, inside tolerance.
    result = simulate(
        detect(utilisation_hpa(target=70, current=2)),
        current_request=0.5,
        proposed_request=0.133,
        observed_usage=0.1,
    )
    assert result.verdict is Verdict.SAFE


def test_refuses_when_the_needed_target_is_unsafe():
    """
    A target above 90% leaves no headroom to absorb a traffic spike.

    Refusing here trades a paper saving for real availability, which is the correct direction.
    """
    result = simulate(
        detect(utilisation_hpa(target=70, current=2)),
        current_request=0.5,
        proposed_request=0.105,
        observed_usage=0.1,
    )
    assert result.verdict is Verdict.REFUSE
    assert "ceiling" in result.reason or "headroom" in result.reason


def test_refuses_when_usage_is_not_observed():
    """Predicting an autoscaler without knowing its input is guessing, and the cost of guessing
    wrong is a scale-out storm."""
    result = simulate(
        detect(utilisation_hpa()),
        current_request=0.5,
        proposed_request=0.2,
        observed_usage=None,
    )
    assert result.verdict is Verdict.REFUSE


def test_refuses_on_unknown_coupling():
    hpa = utilisation_hpa()
    hpa["spec"]["metrics"] = []
    result = simulate(
        detect(hpa), current_request=0.5, proposed_request=0.2, observed_usage=0.1
    )
    assert result.verdict is Verdict.REFUSE


def test_uncoupled_workload_is_always_safe():
    result = simulate(
        detect(None), current_request=0.5, proposed_request=0.05, observed_usage=0.1
    )
    assert result.verdict is Verdict.SAFE


def test_max_replicas_clamps_the_prediction():
    result = simulate(
        detect(utilisation_hpa(target=70, current=2, max_r=2)),
        current_request=0.5,
        proposed_request=0.12,
        observed_usage=0.1,
    )
    # Already at maxReplicas, so no scale-out is possible -- the workload just runs hotter.
    assert result.replicas_after == 2
    assert result.verdict is Verdict.SAFE


# ==================================================================================================
# QoS
# ==================================================================================================

def test_classify():
    assert classify({"cpu": 1.0, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0}) == "Guaranteed"
    assert classify({"cpu": 1.0, "memory": 512.0}, {"cpu": 2.0, "memory": 512.0}) == "Burstable"
    assert classify({}, {}) == "BestEffort"


def test_guaranteed_needs_both_resources():
    """A container that sets only a CPU limit is Burstable however carefully memory is sized --
    which is why a memory-only change can still demote a pod."""
    assert classify({"cpu": 1.0}, {"cpu": 1.0}) == "Burstable"


def test_demotion_is_blocked_by_default():
    check = check_transition(
        {"cpu": 1.0, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0},
        {"cpu": 0.5}, {},
    )
    assert not check.allowed
    assert check.is_demotion
    assert "eviction" in check.reason


def test_demotion_allowed_with_annotation():
    check = check_transition(
        {"cpu": 1.0, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0},
        {"cpu": 0.5}, {},
        annotations={ALLOW_QOS_DEMOTION: "true"},
    )
    assert check.allowed


def test_promotion_is_always_allowed():
    check = check_transition(
        {"cpu": 1.0, "memory": 512.0}, {"cpu": 2.0, "memory": 512.0},
        {"cpu": 1.0}, {"cpu": 1.0},
    )
    assert check.allowed
    assert not check.is_demotion


def test_proposed_values_are_merged_not_substituted():
    """
    A recommendation usually covers ONE resource.

    Treating the partial dict as the whole resource block would compute the class of a container
    that has no memory request at all -- BestEffort -- and report a catastrophic demotion that is not
    happening.
    """
    check = check_transition(
        {"cpu": 1.0, "memory": 512.0}, {"cpu": 1.0, "memory": 512.0},
        {"memory": 256.0}, {"memory": 256.0},
    )
    # Still Guaranteed: cpu is unchanged and memory limit == request.
    assert check.after == "Guaranteed"
    assert check.allowed


# ==================================================================================================
# Bin-packing
# ==================================================================================================

M5_LARGE = InstanceType("m5.large", cpu_cores=2, memory_mib=8192, hourly_price=0.096, max_pods=29)
M5_4XLARGE = InstanceType("m5.4xlarge", cpu_cores=16, memory_mib=65536, hourly_price=0.768)
T3_SMALL = InstanceType("t3.small", cpu_cores=2, memory_mib=2048, hourly_price=0.0208, max_pods=11)


def test_allocatable_subtracts_reservations():
    """Packing against capacity assumes resource no pod can ever be scheduled onto."""
    cpu, mem = allocatable(M5_LARGE)
    # 2000m * 0.06 = 120m reserved.
    assert cpu == pytest.approx(1.88)
    # 8192 * 0.10 = 819.2 reserved, plus a 100 MiB eviction threshold.
    assert mem == pytest.approx(8192 - 819.2 - 100)


def test_reservation_cap_applies_on_large_nodes():
    """The fraction dominates small nodes, the cap dominates large ones: a 16-core node does not
    reserve 960m."""
    cpu, mem = allocatable(M5_4XLARGE)
    assert cpu == pytest.approx(16.0 - 0.4)          # capped at 400m
    assert mem == pytest.approx(65536 - 4096 - 100)  # capped at 4096 MiB


def test_daemonsets_are_seeded_onto_every_node():
    """Every node runs the full DaemonSet set the moment it joins, so that cost is not optional."""
    ds = [PodSpec("kube-system", "cni", 0.1, 128.0)]
    pods = [PodSpec("shop", f"w{i}", 0.9, 512.0) for i in range(4)]

    without = pack(pods, M5_LARGE)
    with_ds = pack(pods, M5_LARGE, daemonset_overhead=ds)

    # DaemonSet overhead consumes allocatable, so it can only ever need the same or more nodes.
    assert with_ds.nodes >= without.nodes
    assert with_ds.pods_placed == 4


def test_max_pods_can_be_the_binding_constraint():
    """
    A t3.small caps at 11 pods because of ENI limits.

    Twenty tiny pods fit easily on CPU and memory and still need two nodes. Ignoring max_pods
    under-counts nodes, which OVERSTATES the saving.
    """
    tiny = [PodSpec("shop", f"w{i}", 0.01, 16.0) for i in range(20)]
    result = pack(tiny, T3_SMALL)
    assert result.nodes == 2


def test_unplaceable_pod_is_reported_not_dropped():
    """Silently dropping it would understate the node count; looping to add nodes would never
    terminate."""
    huge = [PodSpec("shop", "monolith", 8.0, 4096.0)]
    result = pack(huge, M5_LARGE)
    assert result.unplaceable
    assert "monolith" in result.unplaceable[0]


def test_daemonset_larger_than_the_node_is_an_error():
    ds = [PodSpec("kube-system", "fat", 5.0, 512.0)]
    with pytest.raises(ValueError, match="does not fit"):
        pack([PodSpec("shop", "w", 0.1, 128.0)], M5_LARGE, daemonset_overhead=ds)


def test_savings_are_a_node_delta():
    """
    THE central rule.

    Eight pods at 1.8 cores need one node each on an m5.large (1.88 allocatable). Right-sized to
    0.4 cores, four fit per node -- so eight nodes become two.
    """
    before = [PodSpec("shop", f"w{i}", 1.8, 1024.0) for i in range(8)]
    after = [PodSpec("shop", f"w{i}", 0.4, 1024.0) for i in range(8)]

    report = savings_report(before, after, M5_LARGE, as_of="2026-08-01", region="ap-south-1")

    assert report.nodes_before == 8
    assert report.nodes_after > 0
    assert report.nodes_removed == report.nodes_before - report.nodes_after
    assert report.nodes_removed > 0
    # nodes_removed x hourly x 730
    assert report.monthly_saving == pytest.approx(
        report.nodes_removed * M5_LARGE.hourly_price * 730
    )


def test_no_node_removed_means_no_saving():
    """
    A reduction that removes no node is worth ZERO, and the note must say so.

    Reporting a per-pod figure here would invent a saving the cloud bill will never show.
    """
    before = [PodSpec("shop", "w", 0.5, 512.0)]
    after = [PodSpec("shop", "w", 0.1, 128.0)]

    report = savings_report(before, after, M5_LARGE)

    assert report.nodes_before == 1
    assert report.nodes_after == 1
    assert report.nodes_removed == 0
    assert report.monthly_saving == 0.0
    assert "no saving to report" in report.note


def test_auto_mode_surcharge_is_applied():
    """EKS Auto Mode's ~12% management surcharge makes the same node delta worth measurably less."""
    before = [PodSpec("shop", f"w{i}", 1.8, 1024.0) for i in range(4)]
    after = [PodSpec("shop", f"w{i}", 0.4, 1024.0) for i in range(4)]

    plain = savings_report(before, after, M5_LARGE)
    auto = savings_report(
        before, after, M5_LARGE, scenario="eks_auto_mode", surcharge_pct=12.0
    )

    assert auto.monthly_saving > plain.monthly_saving
    assert auto.monthly_saving == pytest.approx(plain.monthly_saving * 1.12)
    assert "surcharge" in auto.note


def test_packing_reports_the_binding_resource():
    """A memory-bound cluster gains nothing from trimming CPU, and saying which resource binds is
    more useful than a number an operator cannot act on."""
    memory_heavy = [PodSpec("shop", f"w{i}", 0.05, 3500.0) for i in range(4)]
    result = pack(memory_heavy, M5_LARGE)
    assert result.bin_limited_by == "memory"

    cpu_heavy = [PodSpec("shop", f"w{i}", 1.8, 64.0) for i in range(4)]
    assert pack(cpu_heavy, M5_LARGE).bin_limited_by == "cpu"


def test_packing_is_deterministic():
    """The same input must always produce the same node count, or the savings figure is not
    auditable."""
    pods = [PodSpec("shop", f"w{i}", 0.3 + (i % 5) * 0.1, 256.0 + (i % 3) * 128) for i in range(30)]
    first = pack(pods, M5_LARGE)
    for _ in range(5):
        assert pack(list(reversed(pods)), M5_LARGE).nodes == first.nodes
