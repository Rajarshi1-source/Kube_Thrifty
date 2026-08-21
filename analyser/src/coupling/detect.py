#!/usr/bin/env python3
"""
detect.py -- find the autoscalers that a right-sizing change would collide with.

THE RIGHT-SIZING COST PARADOX, which this module exists to prevent:

An HPA scaling on `Resource` / `averageUtilization` measures utilisation AS A FRACTION OF THE
REQUEST. Shrink the request and measured utilisation rises for exactly the same real workload:

    500m request, 100m used  ->  20% utilised, target 70%, 2 replicas
    150m request, 100m used  ->  67% utilised, target 70%, still 2 replicas... just barely
    120m request, 100m used  ->  83% utilised, target 70%  ->  SCALE OUT to 3 replicas

The "saving" trimmed 380m per replica and then added a whole replica. Net cost: higher. This is the
single most expensive mistake a naive right-sizer makes, and it looks like a success in every
per-pod waste metric.

So the resource change and the HPA target change must ship in ONE pull request. The intermediate
state -- new requests, old target -- is the outage.

Two ownership cases that are easy to get wrong:

  KEDA        An HPA created by a ScaledObject is RECONCILED by the KEDA operator. Editing that HPA
              directly appears to work and is silently reverted on the next reconcile loop, so the
              PR must target the ScaledObject instead.
  external    An HPA scaling on `External` or `Pods` metrics (queue depth, requests per second) is
              NOT coupled to requests at all. Shrinking a request does not move its input, so
              treating it as coupled would refuse a perfectly safe reduction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

log = logging.getLogger(__name__)

# KEDA stamps this on the HPAs it owns.
KEDA_OWNER_KIND = "ScaledObject"


class CouplingKind(StrEnum):
    """How a workload's autoscaler relates to its resource requests."""

    # HPA on Resource/averageUtilization: directly coupled. Shrinking the request raises measured
    # utilisation.
    UTILISATION = "utilisation"
    # Owned by a KEDA ScaledObject. Coupled, but the HPA is not the thing to edit.
    KEDA = "keda"
    # External/Pods metrics. NOT coupled to requests.
    EXTERNAL = "external"
    # No autoscaler at all.
    NONE = "none"
    # An HPA exists but its shape could not be determined. Treated as coupled, because guessing
    # "not coupled" is the direction that causes an outage.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Coupling:
    kind: CouplingKind
    namespace: str
    workload: str

    hpa_name: str | None = None
    scaled_object_name: str | None = None

    resource: str | None = None
    target_utilisation: int | None = None
    min_replicas: int | None = None
    max_replicas: int | None = None
    current_replicas: int | None = None

    @property
    def is_coupled(self) -> bool:
        """True when a request change can move the autoscaler's input.

        UNKNOWN counts as coupled: an HPA whose shape we could not read might well be
        utilisation-based, and assuming otherwise risks a scale-out storm.
        """
        return self.kind in (CouplingKind.UTILISATION, CouplingKind.KEDA, CouplingKind.UNKNOWN)

    @property
    def edit_target(self) -> str:
        """What a PR must modify to co-change the target."""
        if self.kind is CouplingKind.KEDA:
            # NOT the HPA: the KEDA operator reconciles it and would revert a direct edit.
            return f"ScaledObject/{self.scaled_object_name}"
        if self.hpa_name:
            return f"HorizontalPodAutoscaler/{self.hpa_name}"
        return "none"


def detect(hpa: dict | None, scaled_objects: list[dict] | None = None) -> Coupling:
    """
    Classify one workload's autoscaler.

    `hpa` is the HPA object as returned by the API (or None). `scaled_objects` is the list of KEDA
    ScaledObjects in the namespace, used to establish ownership.
    """
    if hpa is None:
        return Coupling(CouplingKind.NONE, namespace="", workload="")

    metadata = hpa.get("metadata") or {}
    spec = hpa.get("spec") or {}
    status = hpa.get("status") or {}

    namespace = metadata.get("namespace", "")
    target_ref = spec.get("scaleTargetRef") or {}
    workload = target_ref.get("name", "")
    hpa_name = metadata.get("name")

    # --- KEDA ownership --------------------------------------------------------------------------
    # Checked FIRST. A KEDA-owned HPA may well contain a Resource/utilisation metric, so classifying
    # by metric shape alone would point the PR at an HPA that the operator overwrites.
    for owner in metadata.get("ownerReferences") or []:
        if owner.get("kind") == KEDA_OWNER_KIND:
            return Coupling(
                CouplingKind.KEDA,
                namespace=namespace,
                workload=workload,
                hpa_name=hpa_name,
                scaled_object_name=owner.get("name"),
                min_replicas=spec.get("minReplicas"),
                max_replicas=spec.get("maxReplicas"),
                current_replicas=status.get("currentReplicas"),
            )

    # A ScaledObject may also be matched by name convention (`keda-hpa-<so-name>`) when owner
    # references are absent, which happens on older KEDA versions.
    for so in scaled_objects or []:
        so_name = (so.get("metadata") or {}).get("name")
        if so_name and hpa_name == f"keda-hpa-{so_name}":
            return Coupling(
                CouplingKind.KEDA,
                namespace=namespace,
                workload=workload,
                hpa_name=hpa_name,
                scaled_object_name=so_name,
                min_replicas=spec.get("minReplicas"),
                max_replicas=spec.get("maxReplicas"),
                current_replicas=status.get("currentReplicas"),
            )

    # --- metric shape ----------------------------------------------------------------------------
    metrics = spec.get("metrics") or []
    if not metrics:
        # An HPA with no readable metrics. Fails towards "coupled".
        return Coupling(
            CouplingKind.UNKNOWN,
            namespace=namespace, workload=workload, hpa_name=hpa_name,
            min_replicas=spec.get("minReplicas"),
            max_replicas=spec.get("maxReplicas"),
            current_replicas=status.get("currentReplicas"),
        )

    for metric in metrics:
        if metric.get("type") != "Resource":
            continue
        resource_block = metric.get("resource") or {}
        target = resource_block.get("target") or {}
        # ONLY `Utilization` is coupled. `AverageValue` is an absolute figure (e.g. 500m) and does
        # not move when the request changes.
        if target.get("type") == "Utilization":
            return Coupling(
                CouplingKind.UTILISATION,
                namespace=namespace,
                workload=workload,
                hpa_name=hpa_name,
                resource=resource_block.get("name"),
                target_utilisation=target.get("averageUtilization"),
                min_replicas=spec.get("minReplicas"),
                max_replicas=spec.get("maxReplicas"),
                current_replicas=status.get("currentReplicas"),
            )

    # Every metric is External, Pods, or Object -- none of which is a fraction of the request.
    return Coupling(
        CouplingKind.EXTERNAL,
        namespace=namespace, workload=workload, hpa_name=hpa_name,
        min_replicas=spec.get("minReplicas"),
        max_replicas=spec.get("maxReplicas"),
        current_replicas=status.get("currentReplicas"),
    )
