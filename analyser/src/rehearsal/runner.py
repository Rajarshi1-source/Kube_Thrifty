#!/usr/bin/env python3
"""
runner.py -- the Resize Rehearsal.

Apply a candidate size to ONE live pod in place, watch it, then ALWAYS put it back. The pull request
then carries evidence instead of a prediction.

THE ORDER OF OPERATIONS IS THE SAFETY PROPERTY. Read it before changing anything here:

  1. Persist the compensation row -- original requests, original limits, revert deadline -- BEFORE
     the cluster is touched. If this process is killed one millisecond after the PATCH, that row is
     the only thing in the universe that knows how to undo it, and the watchdog CronJob acts on it
     without needing this process to exist.
  2. Capture the baseline.
  3. PATCH pods/resize. The only mutating call in the entire system.
  4. Observe.
  5. Revert, in a `finally`. Unconditionally. On success, on regression, on exception, on
     KeyboardInterrupt.

Writing the compensation row after the patch would open a window in which a live pod carries
experimental limits that nothing in the system knows about. That window is the whole risk.

Three further rules that are easy to get wrong:

  * Assert on `status.containerStatuses[].resources`, NEVER on `spec`. The spec reflects what was
    ASKED FOR the instant the API server accepts the patch; the status reflects what the kubelet
    actually applied. Checking spec makes every resize look instantly successful, including the ones
    that are still Deferred or permanently Infeasible.
  * A timeout or `Infeasible` is `inconclusive`, never `safe`. Absence of observed regression is not
    evidence of safety when the change never took effect.
  * If the pod UID changes, the pod was rescheduled. The experiment is measuring a different
    container, so the outcome is `inconclusive` and NOTHING is reverted -- reverting would patch a
    pod we never modified.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

log = logging.getLogger(__name__)

# Extra slack added to the revert deadline beyond observe + resize timeout. The watchdog only acts
# past the deadline, so this margin stops it stealing a rehearsal that is merely slow.
DEADLINE_SLACK = timedelta(seconds=600)

# How long to wait for the kubelet to actually apply the resize before calling it inconclusive.
DEFAULT_RESIZE_TIMEOUT = 120
DEFAULT_OBSERVE_SECONDS = 1800
DEFAULT_BASELINE_SECONDS = 300


class Outcome(StrEnum):
    SAFE = "safe"
    REGRESSED = "regressed"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


class ResizeState(StrEnum):
    """What the kubelet did with the resize request."""

    APPLIED = "applied"
    # PodResizePending with reason Deferred: not possible right now, may become possible.
    DEFERRED = "deferred"
    # PodResizePending with reason Infeasible: will NEVER be possible on this node.
    INFEASIBLE = "infeasible"
    TIMEOUT = "timeout"
    # The pod was rescheduled mid-experiment.
    POD_REPLACED = "pod_replaced"


@dataclass
class RehearsalResult:
    rehearsal_id: str
    outcome: Outcome
    resize_state: ResizeState | None = None
    trip_reasons: tuple[str, ...] = ()
    baseline: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    rehearsed_floor: dict[str, float] | None = None
    reverted: bool = False
    detail: str = ""

    @property
    def contributes_floor(self) -> bool:
        """Only a `safe` outcome may contribute a sizing floor.

        An inconclusive rehearsal that leaked a floor into sizing would be the worst failure this
        system can produce: an unverified number wearing a verified badge.
        """
        return self.outcome is Outcome.SAFE and self.rehearsed_floor is not None


class ResizeClient(Protocol):
    """
    The narrow Kubernetes surface a rehearsal needs.

    Deliberately a Protocol: the real implementation needs `call_api` because the official Python
    client has no typed method for the `resize` subresource, and that awkwardness should not leak
    into the experiment logic or into the tests.
    """

    def patch_resize(
        self, namespace: str, pod: str, container: str,
        requests: dict[str, float], limits: dict[str, float],
    ) -> None: ...

    def get_pod_status(self, namespace: str, pod: str) -> dict[str, Any]: ...


class SignalSource(Protocol):
    def sample(self, namespace: str, pod: str, container: str, seconds: int) -> dict[str, Any]: ...


class CompensationStore(Protocol):
    """Where the compensation row lives. Must be durable BEFORE the cluster is touched."""

    def open_rehearsal(self, **kwargs: Any) -> None: ...
    def close_rehearsal(self, rehearsal_id: str, **kwargs: Any) -> None: ...


@dataclass
class RehearsalRequest:
    namespace: str
    workload: str
    pod: str
    pod_uid: str
    container: str
    cluster_id: int
    run_id: str | None

    original_requests: dict[str, float]
    original_limits: dict[str, float]
    candidate_requests: dict[str, float]
    candidate_limits: dict[str, float]

    observe_seconds: int = DEFAULT_OBSERVE_SECONDS
    baseline_seconds: int = DEFAULT_BASELINE_SECONDS
    resize_timeout_seconds: int = DEFAULT_RESIZE_TIMEOUT


class Rehearsal:
    def __init__(
        self,
        client: ResizeClient,
        signals: SignalSource,
        store: CompensationStore,
        *,
        evaluate,  # verification.signals.evaluate
        sleep=time.sleep,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.signals = signals
        self.store = store
        self.evaluate = evaluate
        self._sleep = sleep
        self._now = now

    # ---------------------------------------------------------------------------------------------

    def run(self, req: RehearsalRequest) -> RehearsalResult:
        rehearsal_id = f"reh-{uuid.uuid4().hex[:12]}"
        deadline = self._now() + timedelta(
            seconds=req.observe_seconds + req.resize_timeout_seconds
        ) + DEADLINE_SLACK

        # ==========================================================================================
        # STEP 1 -- COMPENSATION FIRST. Before the cluster is touched.
        #
        # If this write fails, the rehearsal does not happen. Refusing to experiment is always
        # preferable to experimenting with no way to undo it.
        # ==========================================================================================
        try:
            self.store.open_rehearsal(
                rehearsal_id=rehearsal_id,
                run_id=req.run_id,
                cluster_id=req.cluster_id,
                namespace=req.namespace,
                workload=req.workload,
                pod=req.pod,
                pod_uid=req.pod_uid,
                container=req.container,
                original_requests=req.original_requests,
                original_limits=req.original_limits,
                candidate_requests=req.candidate_requests,
                candidate_limits=req.candidate_limits,
                revert_deadline=deadline,
            )
        except Exception as e:                                        # noqa: BLE001
            log.error(
                "could not persist the compensation row for %s/%s (%s); NOT touching the pod",
                req.namespace, req.pod, e,
            )
            return RehearsalResult(
                rehearsal_id=rehearsal_id,
                outcome=Outcome.FAILED,
                detail=f"compensation row could not be persisted: {e}",
            )

        log.info(
            "rehearsal %s: compensation persisted, revert deadline %s", rehearsal_id, deadline
        )

        patched = False
        try:
            # ======================================================================================
            # STEP 2 -- baseline, BEFORE the change.
            # ======================================================================================
            baseline = self.signals.sample(
                req.namespace, req.pod, req.container, req.baseline_seconds
            )
            log.info("rehearsal %s: baseline captured", rehearsal_id)

            # ======================================================================================
            # STEP 3 -- the resize. The only mutating call in the system.
            # ======================================================================================
            self.client.patch_resize(
                req.namespace, req.pod, req.container,
                req.candidate_requests, req.candidate_limits,
            )
            patched = True

            state = self._await_resize(req, rehearsal_id)

            if state is ResizeState.POD_REPLACED:
                # The pod we patched no longer exists. Do NOT revert: the new pod was never
                # modified, and patching it would apply original values to a container that never
                # left them -- a write with no justification.
                patched = False
                return self._finish(
                    rehearsal_id, Outcome.INCONCLUSIVE, state, baseline, {},
                    ("pod was rescheduled during the rehearsal",),
                    reverted=False,
                    detail="pod UID changed; the experiment measured a different container",
                )

            if state is not ResizeState.APPLIED:
                # Deferred, Infeasible or timed out. NOT safe: the candidate never took effect, so
                # nothing was tested. Absence of regression is not evidence here.
                return self._finish(
                    rehearsal_id, Outcome.INCONCLUSIVE, state, baseline, {},
                    (f"resize did not apply: {state}",),
                    reverted=True,
                    detail=(
                        "the kubelet reported the resize as "
                        f"{state}, so the candidate size was never actually in effect"
                    ),
                )

            # ======================================================================================
            # STEP 4 -- observe.
            # ======================================================================================
            log.info("rehearsal %s: observing for %ds", rehearsal_id, req.observe_seconds)
            self._sleep(req.observe_seconds)

            # Re-check identity. A pod replaced during observation invalidates the measurement.
            if self._pod_uid(req) != req.pod_uid:
                patched = False
                return self._finish(
                    rehearsal_id, Outcome.INCONCLUSIVE, ResizeState.POD_REPLACED, baseline, {},
                    ("pod was rescheduled during observation",),
                    reverted=False,
                    detail="pod UID changed mid-observation",
                )

            observed = self.signals.sample(
                req.namespace, req.pod, req.container, req.observe_seconds
            )

            verdict = self.evaluate(baseline, observed)
            trips = tuple(getattr(verdict, "reasons", ()) or ())
            regressed = bool(getattr(verdict, "regressed", bool(trips)))

            if regressed:
                return self._finish(
                    rehearsal_id, Outcome.REGRESSED, state, baseline, observed, trips,
                    reverted=True,
                    detail="the candidate size degraded the workload; it will not be proposed",
                )

            # Only here does a floor get produced. `rehearsed_floor` is the CANDIDATE, because the
            # candidate is what was proven to work -- not the observed usage, which is merely what
            # happened to be needed during this window.
            return self._finish(
                rehearsal_id, Outcome.SAFE, state, baseline, observed, (),
                reverted=True,
                floor=dict(req.candidate_requests),
                detail="ran on a live pod with no observed regression, then reverted",
            )

        except Exception as e:                                        # noqa: BLE001
            log.exception("rehearsal %s failed: %s", rehearsal_id, e)
            return self._finish(
                rehearsal_id, Outcome.FAILED, None, {}, {}, (f"exception: {type(e).__name__}",),
                reverted=True, detail=str(e),
            )

        finally:
            # ======================================================================================
            # STEP 5 -- REVERT, UNCONDITIONALLY.
            #
            # In `finally` so it runs on success, on regression, on exception, and on
            # KeyboardInterrupt. This is the line that makes a rehearsal an experiment rather than a
            # change.
            # ======================================================================================
            if patched:
                self._revert(req, rehearsal_id)

    # ---------------------------------------------------------------------------------------------

    def _await_resize(self, req: RehearsalRequest, rehearsal_id: str) -> ResizeState:
        """
        Poll until the kubelet has ACTUALLY applied the resize.

        Reads `status.containerStatuses[].resources`, never `spec`. The spec changes the moment the
        API server accepts the patch, so asserting on it would report success for a resize that is
        still Deferred or permanently Infeasible.
        """
        deadline = time.monotonic() + req.resize_timeout_seconds

        while time.monotonic() < deadline:
            status = self.client.get_pod_status(req.namespace, req.pod)

            if status.get("uid") and status["uid"] != req.pod_uid:
                return ResizeState.POD_REPLACED

            # Kubernetes surfaces a pending resize as a condition, and `Infeasible` is terminal:
            # the node cannot satisfy the request, so waiting longer cannot help.
            for condition in status.get("conditions", []) or []:
                if condition.get("type") == "PodResizePending":
                    reason = (condition.get("reason") or "").lower()
                    if reason == "infeasible":
                        log.warning(
                            "rehearsal %s: resize is Infeasible on this node", rehearsal_id
                        )
                        return ResizeState.INFEASIBLE

            for cs in status.get("containerStatuses", []) or []:
                if cs.get("name") != req.container:
                    continue
                actual = (cs.get("resources") or {}).get("requests") or {}
                if self._matches(actual, req.candidate_requests):
                    log.info("rehearsal %s: kubelet applied the resize", rehearsal_id)
                    return ResizeState.APPLIED

            self._sleep(2)

        log.warning("rehearsal %s: resize did not apply within %ds",
                    rehearsal_id, req.resize_timeout_seconds)
        return ResizeState.TIMEOUT

    @staticmethod
    def _matches(actual: dict[str, Any], wanted: dict[str, float]) -> bool:
        """
        Compare the kubelet's applied resources against the candidate.

        Quantities are compared as parsed numbers, not strings: the API may normalise `1000m` to
        `1`, or `512Mi` to `536870912`, and a string comparison would never match.
        """
        for resource, want in wanted.items():
            raw = actual.get(resource)
            if raw is None:
                return False
            got = parse_quantity(str(raw))
            if got is None:
                return False
            # Tolerance, because Kubernetes rounds millicores and byte quantities.
            if abs(got - want) > max(abs(want) * 0.01, 1e-6):
                return False
        return True

    def _pod_uid(self, req: RehearsalRequest) -> str | None:
        try:
            return self.client.get_pod_status(req.namespace, req.pod).get("uid")
        except Exception:                                             # noqa: BLE001
            # Unknown UID must not be treated as "unchanged": returning req.pod_uid here would let a
            # replaced pod pass the identity check.
            return None

    def _revert(self, req: RehearsalRequest, rehearsal_id: str) -> None:
        """
        Put the pod back. Retried, because this is the one operation that must not be skipped.

        If every attempt fails, the compensation row stays `running` and past its deadline, and the
        watchdog CronJob picks it up. There is no path where a pod is silently left resized.
        """
        for attempt in range(3):
            try:
                self.client.patch_resize(
                    req.namespace, req.pod, req.container,
                    req.original_requests, req.original_limits,
                )
                log.info("rehearsal %s: reverted %s/%s", rehearsal_id, req.namespace, req.pod)
                return
            except Exception as e:                                    # noqa: BLE001
                log.error(
                    "rehearsal %s: revert attempt %d/3 failed: %s", rehearsal_id, attempt + 1, e
                )
                self._sleep(2 ** attempt)

        log.critical(
            "rehearsal %s: COULD NOT REVERT %s/%s. The compensation row remains open and past its "
            "deadline; the watchdog CronJob will sweep it.",
            rehearsal_id, req.namespace, req.pod,
        )

    def _finish(
        self,
        rehearsal_id: str,
        outcome: Outcome,
        state: ResizeState | None,
        baseline: dict[str, Any],
        observed: dict[str, Any],
        trips: tuple[str, ...],
        *,
        reverted: bool,
        floor: dict[str, float] | None = None,
        detail: str = "",
    ) -> RehearsalResult:
        # Belt and braces with the database CHECK constraint: a floor is only ever recorded for a
        # `safe` outcome.
        if outcome is not Outcome.SAFE:
            floor = None

        try:
            self.store.close_rehearsal(
                rehearsal_id,
                outcome=str(outcome),
                rehearsed_floor=(min(floor.values()) if floor else None),
                observed_signals=observed or None,
                trip_reasons=list(trips),
                reverted=reverted,
            )
        except Exception as e:                                        # noqa: BLE001
            # The outcome could not be recorded, but the pod has still been reverted by the
            # `finally`. Leaving the row `running` means the watchdog will inspect it, which is the
            # safe direction.
            log.error("could not record outcome for %s: %s", rehearsal_id, e)

        return RehearsalResult(
            rehearsal_id=rehearsal_id,
            outcome=outcome,
            resize_state=state,
            trip_reasons=trips,
            baseline=baseline,
            observed=observed,
            rehearsed_floor=floor,
            reverted=reverted,
            detail=detail,
        )


_SUFFIXES = {
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
    "Ki": 1024.0, "Mi": 1024.0**2, "Gi": 1024.0**3, "Ti": 1024.0**4,
}


def parse_quantity(raw: str) -> float | None:
    """
    Parse a Kubernetes resource quantity.

    Needed because the API returns whatever form was written -- `100m`, `0.1`, `512Mi`,
    `536870912` -- and a rehearsal has to compare the applied value against the requested one. A
    naive `float()` fails on every suffixed form, and string equality fails on every normalised one.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Two-character binary suffixes first: "Mi" must not be read as "M".
    for suffix in ("Ki", "Mi", "Gi", "Ti"):
        if raw.endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * _SUFFIXES[suffix]
            except ValueError:
                return None
    last = raw[-1]
    if last in _SUFFIXES and not last.isdigit():
        try:
            return float(raw[:-1]) * _SUFFIXES[last]
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None
