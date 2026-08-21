#!/usr/bin/env python3
"""
qos.py -- the QoS transition guard.

Kubernetes DERIVES a pod's QoS class from its resources; you cannot set it directly:

    Guaranteed   limits == requests for every resource in every container
    Burstable    requests set, but limits absent or unequal somewhere
    BestEffort   nothing set

That derivation is why this guard is necessary. A right-sizing change that alters requests can silently
move a pod between classes, and QoS determines EVICTION ORDER under node pressure: BestEffort dies
first, then Burstable, and Guaranteed last. So a "saving" that demotes Guaranteed to Burstable has
bought a discount by making the workload more likely to be killed -- a real availability change that
no resource number reveals on its own.

The guard blocks Guaranteed -> anything unless `kubethrifty.io/allow-qos-demotion=true`. Promotions
are always allowed: moving up the eviction order is a safety improvement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

log = logging.getLogger(__name__)

ALLOW_QOS_DEMOTION = "kubethrifty.io/allow-qos-demotion"

# Ordered best to worst. Index comparison IS the demotion test.
_RANK = {"Guaranteed": 0, "Burstable": 1, "BestEffort": 2}


class QosClass(StrEnum):
    GUARANTEED = "Guaranteed"
    BURSTABLE = "Burstable"
    BEST_EFFORT = "BestEffort"


@dataclass(frozen=True)
class QosCheck:
    before: str
    after: str
    allowed: bool
    reason: str

    @property
    def is_demotion(self) -> bool:
        return _RANK.get(self.after, 99) > _RANK.get(self.before, 99)


def classify(requests: dict[str, float], limits: dict[str, float]) -> str:
    """
    Derive the QoS class exactly as Kubernetes does.

    The subtlety: Guaranteed requires limits == requests for BOTH cpu and memory. A container that
    sets only a CPU limit is Burstable, however carefully its memory is sized -- which is why a
    memory-only change can still demote a pod.
    """
    if not requests and not limits:
        return QosClass.BEST_EFFORT

    for resource in ("cpu", "memory"):
        req = requests.get(resource)
        lim = limits.get(resource)
        if req is None or lim is None:
            return QosClass.BURSTABLE
        if req != lim:
            return QosClass.BURSTABLE

    return QosClass.GUARANTEED


def check_transition(
    current_requests: dict[str, float],
    current_limits: dict[str, float],
    proposed_requests: dict[str, float],
    proposed_limits: dict[str, float],
    *,
    annotations: dict[str, str] | None = None,
) -> QosCheck:
    """
    Compare the QoS class before and after a proposed change.

    Proposed values are MERGED over current ones, not substituted: a recommendation usually covers
    one resource, and treating the partial dict as the complete resource block would compute the
    class of a container that has no memory request at all.
    """
    annotations = annotations or {}

    merged_requests = {**current_requests, **proposed_requests}
    merged_limits = {**current_limits, **proposed_limits}

    before = classify(current_requests, current_limits)
    after = classify(merged_requests, merged_limits)

    if before == after:
        return QosCheck(before, after, True, f"QoS class is unchanged ({before})")

    if _RANK.get(after, 99) < _RANK.get(before, 99):
        # A promotion. Always allowed -- moving up the eviction order is strictly safer.
        return QosCheck(
            before, after, True,
            f"QoS improves {before} -> {after}, raising eviction priority under node pressure",
        )

    # A demotion.
    if annotations.get(ALLOW_QOS_DEMOTION, "").lower() == "true":
        return QosCheck(
            before, after, True,
            f"QoS demotion {before} -> {after} explicitly permitted by {ALLOW_QOS_DEMOTION}=true",
        )

    return QosCheck(
        before, after, False,
        f"the change would demote QoS from {before} to {after}, making this pod a preferred "
        f"eviction target under node pressure. That is an availability change, not just a resource "
        f"change, so it needs {ALLOW_QOS_DEMOTION}=true to proceed.",
    )


def guaranteed_limits_for(requests: dict[str, float]) -> dict[str, float]:
    """
    Build limits that preserve Guaranteed.

    Used when a workload is already Guaranteed and should stay that way: mirroring requests into
    limits keeps the class intact while still shrinking the reservation. For memory this is what the
    sizer does anyway (limit == request); for CPU it means giving up burst headroom, which is the
    price of remaining Guaranteed.
    """
    return dict(requests)
