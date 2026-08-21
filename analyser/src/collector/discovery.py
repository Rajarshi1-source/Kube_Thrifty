#!/usr/bin/env python3
"""
discovery.py -- map cgroup paths back to Kubernetes pods and containers.

Reading `memory.peak` is easy. Knowing WHICH container it belongs to is the hard part, and without
that the number is useless: an unlabelled high-water mark cannot be joined to a recommendation.

There is no API for this. The mapping is encoded in the cgroup directory names, and the encoding
differs by cgroup driver:

  systemd driver (the default for kubelet, and what kind uses):
    /sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice/kubelet-kubepods-burstable.slice/
      kubelet-kubepods-burstable-pod<UID_WITH_UNDERSCORES>.slice/
        cri-containerd-<CONTAINER_ID>.scope

  cgroupfs driver:
    /sys/fs/cgroup/kubepods/burstable/pod<UID_WITH_DASHES>/<CONTAINER_ID>

Two details that are easy to get wrong and produce silently unjoinable data:

  * The systemd driver replaces every '-' in the pod UID with '_', because '-' is the slice
    hierarchy separator. The UID must be converted back before it will match the Kubernetes API.
  * The QoS class appears as a path component for Burstable and BestEffort, but Guaranteed pods sit
    directly under kubepods with no QoS component. Assuming three levels everywhere silently drops
    every Guaranteed pod -- which, being the ones with limit == request, are exactly the ones this
    product cares most about.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# systemd driver: ...-pod<uid>.slice, with underscores substituted for dashes in the UID.
_SYSTEMD_POD = re.compile(r"pod([0-9a-fA-F_]{32,40})\.slice$")
# cgroupfs driver: pod<uid> with real dashes.
_CGROUPFS_POD = re.compile(r"^pod([0-9a-fA-F-]{32,40})$")
# Container scope, systemd driver. The runtime prefix varies: containerd, crio, docker.
_SYSTEMD_CONTAINER = re.compile(r"^cri-(?:containerd|crio|docker)-([0-9a-f]{12,64})\.scope$")
# cgroupfs driver: the directory name IS the container id.
_CGROUPFS_CONTAINER = re.compile(r"^([0-9a-f]{64})$")

QOS_COMPONENTS = ("burstable", "besteffort")


@dataclass(frozen=True)
class CgroupTarget:
    """One container's cgroup, with enough identity to join it to the Kubernetes API."""

    path: Path
    pod_uid: str
    container_id: str
    qos_class: str


def normalise_pod_uid(raw: str) -> str:
    """
    Convert a cgroup-encoded pod UID back to its Kubernetes form.

    The systemd driver writes underscores where the UID has dashes. Skipping this step yields a UID
    that matches nothing in the API, so every sample is collected correctly and then discarded --
    the worst kind of bug, because the collector looks healthy.
    """
    uid = raw.replace("_", "-")
    # A canonical UUID is 8-4-4-4-12. Some drivers strip the dashes entirely.
    if "-" not in uid and len(uid) == 32:
        return f"{uid[0:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}"
    return uid


def _qos_from_path(path: Path) -> str:
    """
    Infer the QoS class from the path.

    Guaranteed pods have NO QoS path component -- they sit directly under kubepods. So the absence
    of a marker is itself the signal, and code that requires a component to be present drops every
    Guaranteed pod on the node.
    """
    lowered = [p.lower() for p in path.parts]
    for part in lowered:
        if "burstable" in part:
            return "Burstable"
        if "besteffort" in part:
            return "BestEffort"
    return "Guaranteed"


def discover(root: Path) -> list[CgroupTarget]:
    """
    Walk the cgroup tree and return every container cgroup found.

    Deliberately driver-agnostic: rather than deciding which layout applies and walking it
    precisely, this recurses and matches directory NAMES. A node whose kubelet was reconfigured from
    cgroupfs to systemd should not silently stop reporting.
    """
    targets: list[CgroupTarget] = []
    if not root.exists():
        log.error("cgroup root %s does not exist; is /sys/fs/cgroup mounted?", root)
        return targets

    for candidate in root.rglob("*"):
        if not candidate.is_dir():
            continue

        name = candidate.name
        match = _SYSTEMD_CONTAINER.match(name) or _CGROUPFS_CONTAINER.match(name)
        if not match:
            continue

        container_id = match.group(1)

        # Walk up for the pod cgroup. The container scope is a direct child of the pod slice, but
        # searching the ancestry rather than assuming `parent` keeps this robust against a runtime
        # that inserts an extra level.
        pod_uid: str | None = None
        for ancestor in candidate.parents:
            pod_match = _SYSTEMD_POD.search(ancestor.name) or _CGROUPFS_POD.match(ancestor.name)
            if pod_match:
                pod_uid = normalise_pod_uid(pod_match.group(1))
                break
            if ancestor == root:
                break

        if pod_uid is None:
            # A container cgroup with no pod ancestor is not a Kubernetes container -- most often a
            # system service. Skipped silently; this is normal, not an error.
            continue

        targets.append(
            CgroupTarget(
                path=candidate,
                pod_uid=pod_uid,
                container_id=container_id,
                qos_class=_qos_from_path(candidate),
            )
        )

    log.info("discovered %d container cgroup(s) under %s", len(targets), root)
    return targets
