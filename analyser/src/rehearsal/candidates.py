#!/usr/bin/env python3
"""
candidates.py -- pick which workloads to rehearse, and which sizes to try.

A rehearsal costs 30+ minutes of wall clock on a live pod, and the concurrency cap is 3. So the
choice of what to rehearse is itself a product decision, not a detail: rehearsing the wrong three
workloads wastes the entire budget.

Ranking is by ABSOLUTE reclaimable resource, not by waste percentage. A container wasting 90% of 50m
is worth 45m; one wasting 40% of 4 cores is worth 1.6 cores. Percentage ranking would pick the first
and leave the second unexamined -- and only the second can remove a node.

Candidate sizes are ordered LEAST aggressive first. If the gentlest reduction already regresses, the
more aggressive ones certainly will, and there is no reason to spend an hour proving it on a live
pod.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# How many workloads to rehearse per run. Bounded by the concurrency cap and by the fact that each
# rehearsal ties up a live pod for half an hour.
DEFAULT_TOP_N = 3

# Candidate ladder, as a fraction of the distance between the recommendation and the current
# request. 1.0 is the full proposed reduction; 0.5 is halfway.
#
# Ordered gentlest-first so a regression is discovered as cheaply as possible.
CANDIDATE_LADDER = (0.5, 0.75, 1.0)


@dataclass(frozen=True)
class Candidate:
    """One size to try, with the reasoning that produced it."""

    requests: dict[str, float]
    limits: dict[str, float]
    aggressiveness: float
    label: str


@dataclass(frozen=True)
class RankedWorkload:
    namespace: str
    workload: str
    container: str
    resource: str
    current_request: float
    recommended_request: float
    evidence_tier: str
    # The figure that drives ranking: how much resource is actually reclaimed.
    absolute_saving: float
    waste_pct: float


def rank_workloads(recommendations, top_n: int = DEFAULT_TOP_N) -> list[RankedWorkload]:
    """
    Choose the workloads worth spending rehearsal budget on.

    Only REDUCE recommendations are eligible. An increase needs no experiment: giving a container
    more resource is not a risk to that container, so there is nothing to prove on a live pod.

    Already-`rehearsed` recommendations are skipped -- re-proving a proven floor spends the budget
    on a question that is already answered.
    """
    eligible: list[RankedWorkload] = []

    for r in recommendations:
        if str(getattr(r, "action", "")) != "reduce":
            continue
        if str(getattr(r, "evidence_tier", "")) == "rehearsed":
            continue

        current = getattr(r, "current_request", None)
        proposed = getattr(r, "recommended_request", None)
        if current is None or proposed is None or current <= 0:
            continue
        if proposed >= current:
            continue

        # A reduction that was withheld because pressure was observed must not be rehearsed: the
        # sizer already refused it, and applying it to a live pod would be running an experiment the
        # safety layer has explicitly declined.
        if getattr(r, "reduction_blocked", False):
            continue

        eligible.append(
            RankedWorkload(
                namespace=r.namespace,
                workload=r.workload,
                container=r.container,
                resource=r.resource,
                current_request=current,
                recommended_request=proposed,
                evidence_tier=str(getattr(r, "evidence_tier", "modelled")),
                absolute_saving=current - proposed,
                waste_pct=(current - proposed) / current,
            )
        )

    # Rank by ABSOLUTE saving. Percentage ranking would favour tiny containers with huge ratios,
    # which cannot move a node count.
    #
    # CPU and memory are not comparable in raw units (cores vs MiB), so they are ranked separately
    # and interleaved -- otherwise memory, with its larger numbers, would monopolise every slot.
    by_resource: dict[str, list[RankedWorkload]] = {}
    for w in eligible:
        by_resource.setdefault(w.resource, []).append(w)
    for group in by_resource.values():
        group.sort(key=lambda w: w.absolute_saving, reverse=True)

    interleaved: list[RankedWorkload] = []
    cpu = by_resource.get("cpu", [])
    memory = by_resource.get("memory", [])
    for i in range(max(len(cpu), len(memory))):
        if i < len(cpu):
            interleaved.append(cpu[i])
        if i < len(memory):
            interleaved.append(memory[i])

    chosen = interleaved[:top_n]
    log.info(
        "rehearsal candidates: %d eligible, rehearsing top %d (%s)",
        len(eligible), len(chosen),
        ", ".join(f"{w.workload}/{w.resource}" for w in chosen) or "none",
    )
    return chosen


def build_candidates(w: RankedWorkload) -> list[Candidate]:
    """
    Build the candidate ladder for one workload, gentlest first.

    For MEMORY every candidate sets `limit == request`. That is not a stylistic choice: over-limit on
    memory is an OOMKill, and a rehearsal that left limit > request would be testing a shape the
    sizer would never propose.

    For CPU the limit is left alone. The rehearsal is testing whether the REQUEST can shrink;
    changing the limit at the same time would confound two variables in one experiment.
    """
    span = w.current_request - w.recommended_request
    out: list[Candidate] = []

    for fraction in CANDIDATE_LADDER:
        value = w.current_request - (span * fraction)
        requests = {w.resource: value}

        # Memory: limit == request, always -- over-limit on memory is an OOMKill.
        # CPU: the limit is left UNTOUCHED, so the experiment varies exactly one thing.
        limits = {w.resource: value} if w.resource == "memory" else {}

        out.append(
            Candidate(
                requests=requests,
                limits=limits,
                aggressiveness=fraction,
                label=(
                    f"{fraction:.0%} of the proposed reduction "
                    f"({w.current_request:.3f} -> {value:.3f})"
                ),
            )
        )

    return out
