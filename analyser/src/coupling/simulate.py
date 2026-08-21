#!/usr/bin/env python3
"""
simulate.py -- would this reduction trigger a scale-out?

Given a coupled HPA and a proposed request, compute the replica count the autoscaler would settle on
AFTER the change, and decide:

    safe              utilisation stays below target; ship the resource change alone
    co_change_target  a scale-out would occur, but lowering the HPA target in the SAME pull request
                      prevents it
    refuse            no target adjustment can make this safe

The HPA algorithm, from the Kubernetes source:

    desiredReplicas = ceil(currentReplicas * (currentMetricValue / desiredMetricValue))

with a 10% tolerance band, so it does not act on small deviations. Both details matter: omitting the
ceiling under-predicts, and omitting the tolerance predicts scale-outs that would never fire.

The output is the whole reason this product does not accidentally cost money. `co_change_target` is
the interesting verdict: the reduction is fine PROVIDED the HPA target moves with it, and both edits
must land in one PR because the intermediate state -- new requests, old target -- is the outage.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum

from .detect import Coupling, CouplingKind

log = logging.getLogger(__name__)

# The HPA ignores deviations inside this band. Hard-coded in Kubernetes as
# `horizontal-pod-autoscaler-tolerance`, default 0.1.
HPA_TOLERANCE = 0.10

# Hysteresis applied to the RECOMMENDED target so the new target is not sitting exactly on the line
# where the next percentage point of load triggers a scale-out.
TARGET_HYSTERESIS = 0.10

# Never propose a target above this: an HPA aiming for >90% utilisation has no room to react to a
# traffic spike before the workload is already saturated.
MAX_SAFE_TARGET = 90
# Below this an HPA scales out constantly and the workload never consolidates.
MIN_SENSIBLE_TARGET = 30


class Verdict(StrEnum):
    SAFE = "safe"
    CO_CHANGE_TARGET = "co_change_target"
    REFUSE = "refuse"


@dataclass(frozen=True)
class SimulationResult:
    verdict: Verdict
    replicas_before: int
    replicas_after: int
    utilisation_before: float | None
    utilisation_after: float | None
    recommended_target: int | None
    reason: str

    @property
    def would_scale_out(self) -> bool:
        return self.replicas_after > self.replicas_before


def simulate(
    coupling: Coupling,
    *,
    current_request: float,
    proposed_request: float,
    observed_usage: float | None,
    current_replicas: int | None = None,
) -> SimulationResult:
    """
    Predict the HPA's behaviour after a request change.

    `observed_usage` is the per-replica usage the HPA would see -- the same average the metrics
    server reports. None means unknown, which REFUSES: predicting an autoscaler's behaviour without
    knowing its input is guessing, and the cost of guessing wrong is a scale-out storm.
    """
    replicas = current_replicas or coupling.current_replicas or 1

    if not coupling.is_coupled:
        return SimulationResult(
            Verdict.SAFE, replicas, replicas, None, None, None,
            "no utilisation-coupled autoscaler; a request change cannot move its input",
        )

    if coupling.kind is CouplingKind.UNKNOWN:
        return SimulationResult(
            Verdict.REFUSE, replicas, replicas, None, None, None,
            "an HPA exists but its metric shape could not be read. Refusing rather than assuming "
            "it is not utilisation-based.",
        )

    target = coupling.target_utilisation
    if target is None or target <= 0:
        return SimulationResult(
            Verdict.REFUSE, replicas, replicas, None, None, None,
            "the HPA target utilisation is unknown, so the post-change replica count cannot be "
            "predicted",
        )

    if observed_usage is None:
        return SimulationResult(
            Verdict.REFUSE, replicas, replicas, None, None, None,
            "per-replica usage was not observed, so the HPA's input after the change is unknown",
        )

    if current_request <= 0 or proposed_request <= 0:
        return SimulationResult(
            Verdict.REFUSE, replicas, replicas, None, None, None,
            "a request of zero cannot be used as a utilisation denominator",
        )

    util_before = (observed_usage / current_request) * 100.0
    util_after = (observed_usage / proposed_request) * 100.0

    replicas_after = _desired_replicas(replicas, util_after, target, coupling)

    # --- no scale-out: the reduction is safe on its own ------------------------------------------
    if replicas_after <= replicas:
        return SimulationResult(
            Verdict.SAFE, replicas, replicas_after, util_before, util_after, None,
            f"utilisation moves {util_before:.0f}% -> {util_after:.0f}% against a {target}% "
            f"target, which stays within the HPA's tolerance band at {replicas} replica(s)",
        )

    # --- a scale-out would occur. Can a target change prevent it? -------------------------------
    #
    # The target must sit far enough above the post-change utilisation that the HPA does not act.
    # Adding hysteresis keeps it off the exact boundary.
    needed_target = int(math.ceil(util_after * (1.0 + TARGET_HYSTERESIS)))

    if needed_target > MAX_SAFE_TARGET:
        return SimulationResult(
            Verdict.REFUSE, replicas, replicas_after, util_before, util_after, needed_target,
            f"preventing a scale-out would need an HPA target of {needed_target}%, above the "
            f"{MAX_SAFE_TARGET}% ceiling. At that utilisation the workload has no headroom to "
            f"absorb a traffic spike, so this reduction is refused: it would trade a real "
            f"availability risk for a paper saving.",
        )

    if needed_target < MIN_SENSIBLE_TARGET:
        # Reachable when usage is tiny relative to the request; the reduction is fine but the target
        # arithmetic is degenerate.
        needed_target = MIN_SENSIBLE_TARGET

    return SimulationResult(
        Verdict.CO_CHANGE_TARGET, replicas, replicas_after, util_before, util_after, needed_target,
        f"shrinking the request raises measured utilisation {util_before:.0f}% -> "
        f"{util_after:.0f}%, which would scale {replicas} -> {replicas_after} replica(s) and cost "
        f"more than the saving. Raising the HPA target to {needed_target}% in the SAME pull request "
        f"prevents it. The intermediate state -- new requests, old target -- is the outage.",
    )


def _desired_replicas(current: int, utilisation: float, target: int, coupling: Coupling) -> int:
    """
    The HPA's own arithmetic.

        desiredReplicas = ceil(currentReplicas * (currentMetric / desiredMetric))

    with the tolerance band applied first, and then clamped to min/max.
    """
    ratio = utilisation / target

    # Inside the tolerance band the HPA does nothing at all.
    if abs(ratio - 1.0) <= HPA_TOLERANCE:
        return current

    desired = int(math.ceil(current * ratio))

    if coupling.min_replicas is not None:
        desired = max(desired, coupling.min_replicas)
    if coupling.max_replicas is not None:
        desired = min(desired, coupling.max_replicas)
    return desired
