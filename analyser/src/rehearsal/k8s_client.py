#!/usr/bin/env python3
"""
k8s_client.py -- the Kubernetes client, narrowed to exactly two operations.

The whole system's mutating surface is ONE call: `PATCH /api/v1/namespaces/{ns}/pods/{name}/resize`.
This class exists to make that literally true in code -- there is no method here that deletes a pod,
writes a Deployment, or reads a Secret, so those things cannot happen by accident from anywhere that
holds this object.

WHY `call_api` AND NOT A TYPED METHOD

The official Python client has no generated method for the `resize` subresource. Every typed
alternative is wrong in a way that matters:

  patch_namespaced_pod()        patches the pod's spec directly. On a running pod the API server
                                rejects most resource changes outright, and where it does not, it is
                                not going through the resize path at all.
  replace_namespaced_pod()      full replacement. Recreates the pod. A rehearsal that restarts the
                                container has failed at its one promise.

So the request is issued through the generic `call_api` escape hatch with `resize` appended to the
path. This is the documented approach until the client generates the subresource.

The content type must be `application/strategic-merge-patch+json`: a JSON merge patch would replace
the entire containers array, dropping every container not named in the patch.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _fmt_cpu(cores: float) -> str:
    """Millicores as an integer string. Kubernetes accepts `0.125`, but `125m` is what an operator
    reading `kubectl describe` expects to see."""
    return f"{int(round(cores * 1000))}m"


def _fmt_memory(mib: float) -> str:
    """Mebibytes. Integer, because a fractional Mi quantity is rejected by the API."""
    return f"{int(round(mib))}Mi"


def format_quantity(resource: str, value: float) -> str:
    return _fmt_cpu(value) if resource == "cpu" else _fmt_memory(value)


class KubernetesResizeClient:
    """
    In-cluster client whose only mutation is `patch pods/resize`.

    Constructed lazily so importing this module does not require a cluster -- the eval gates and unit
    tests import the rehearsal package without one.
    """

    def __init__(self, *, in_cluster: bool | None = None) -> None:
        from kubernetes import client, config

        if in_cluster is None:
            try:
                config.load_incluster_config()
                in_cluster = True
            except Exception:                                         # noqa: BLE001
                config.load_kube_config()
                in_cluster = False
        elif in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()

        self._api = client.CoreV1Api()
        log.info("kubernetes client ready (in_cluster=%s)", in_cluster)

    # ---------------------------------------------------------------------------------------------

    def patch_resize(
        self,
        namespace: str,
        pod: str,
        container: str,
        requests: dict[str, float],
        limits: dict[str, float],
    ) -> None:
        """
        The one mutating call in KubeThrifty.

        An empty `requests` and `limits` is a no-op rather than an error: sending an empty patch
        would be a pointless write, and raising would make callers guard something harmless.
        """
        resources: dict[str, dict[str, str]] = {}
        if requests:
            resources["requests"] = {
                r: format_quantity(r, v) for r, v in requests.items()
            }
        if limits:
            resources["limits"] = {r: format_quantity(r, v) for r, v in limits.items()}

        if not resources:
            log.debug("patch_resize called with nothing to change; skipping")
            return

        body = {"spec": {"containers": [{"name": container, "resources": resources}]}}

        log.info("PATCH pods/resize %s/%s container=%s %s", namespace, pod, container, resources)

        # The generic escape hatch. `resize` is appended as the subresource path segment.
        self._api.api_client.call_api(
            "/api/v1/namespaces/{namespace}/pods/{name}/resize",
            "PATCH",
            path_params={"namespace": namespace, "name": pod},
            body=body,
            # STRATEGIC merge patch. A plain JSON merge patch replaces the whole containers array,
            # which would delete every sidecar not named in this body.
            header_params={
                "Content-Type": "application/strategic-merge-patch+json",
                "Accept": "application/json",
            },
            response_type="object",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
        )

    def get_pod_status(self, namespace: str, pod: str) -> dict[str, Any]:
        """
        Read the pod's STATUS, normalised for the runner.

        `containerStatuses[].resources` is what the kubelet actually applied. The runner asserts on
        this and never on `spec`, because spec reflects the request the instant the API server
        accepts it -- including resizes that are still Deferred or permanently Infeasible.

        A missing pod returns {} rather than raising: to the watchdog, "the pod is gone" is a normal
        and successful outcome, not an error.
        """
        from kubernetes.client.exceptions import ApiException

        try:
            p = self._api.read_namespaced_pod_status(name=pod, namespace=namespace)
        except ApiException as e:
            if e.status == 404:
                return {}
            raise

        statuses = []
        for cs in (p.status.container_statuses or []):
            entry: dict[str, Any] = {"name": cs.name, "restart_count": cs.restart_count}
            resources = getattr(cs, "resources", None)
            if resources is not None:
                entry["resources"] = {
                    "requests": dict(resources.requests or {}),
                    "limits": dict(resources.limits or {}),
                }
            statuses.append(entry)

        return {
            "uid": p.metadata.uid,
            "phase": p.status.phase,
            "qos_class": p.status.qos_class,
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason}
                for c in (p.status.conditions or [])
            ],
            "containerStatuses": statuses,
        }
