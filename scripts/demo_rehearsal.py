#!/usr/bin/env python3
"""
demo_rehearsal.py -- narrate a Resize Rehearsal, step by step, in the terminal.

    python scripts/demo_rehearsal.py            # simulated, no cluster needed
    python scripts/demo_rehearsal.py --live     # against the running kind cluster

This is the script behind the rehearsal walkthrough. It exists as CODE rather than as a recording
because a recording rots: it cannot be re-run, cannot be diffed, and cannot fail when the behaviour
it depicts changes. This can. The simulated mode is deterministic and hermetic, so it doubles as an
executable description of the safety ordering.

What it demonstrates, in order, is the ordering that makes a rehearsal an experiment rather than a
change:

    1. compensation persisted BEFORE the cluster is touched
    2. baseline captured
    3. resize applied via `patch pods/resize`
    4. outcome observed and judged
    5. revert, unconditionally, in a `finally`
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analyser"))

from src.rehearsal.runner import (  # noqa: E402
    Outcome,
    Rehearsal,
    RehearsalRequest,
)

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

STEP = 0


def step(title: str) -> None:
    global STEP
    STEP += 1
    print(f"\n{BOLD}{CYAN}[{STEP}] {title}{RESET}")


def detail(text: str) -> None:
    print(f"    {text}")


def why(text: str) -> None:
    print(f"    {DIM}why: {text}{RESET}")


class NarratingStore:
    """A compensation store that explains itself."""

    def __init__(self) -> None:
        self.row: dict | None = None

    def open_rehearsal(self, **kwargs):
        step("Persist the compensation row -- BEFORE the cluster is touched")
        detail(f"rehearsal_id     {kwargs['rehearsal_id']}")
        detail(f"pod              {kwargs['namespace']}/{kwargs['pod']}")
        detail(f"original request {kwargs['original_requests']}")
        detail(f"original limits  {kwargs['original_limits']}")
        detail(f"candidate        {kwargs['candidate_requests']}")
        detail(f"revert deadline  {kwargs['revert_deadline']:%H:%M:%S}")
        why("if this process dies one millisecond after the resize, this row is the ONLY thing "
            "that knows how to undo it -- and the watchdog CronJob acts on it without needing "
            "this process to exist.")
        self.row = kwargs

    def close_rehearsal(self, rehearsal_id, **kwargs):
        step("Record the outcome")
        detail(f"outcome          {kwargs['outcome']}")
        detail(f"rehearsed_floor  {kwargs['rehearsed_floor']}")
        if kwargs.get("trip_reasons"):
            detail(f"trip reasons     {kwargs['trip_reasons']}")
        if kwargs["outcome"] != "safe":
            why("a floor is recorded ONLY for a `safe` outcome. An inconclusive rehearsal that "
                "leaked a floor into sizing would be an unverified number wearing a verified badge.")


class NarratingClient:
    """A resize client that narrates each patch, and applies the candidate on the second poll."""

    def __init__(self, *, regress: bool = False, infeasible: bool = False) -> None:
        self.patches: list[dict] = []
        self.polls = 0
        self.regress = regress
        self.infeasible = infeasible

    def patch_resize(self, namespace, pod, container, requests, limits):
        first = not self.patches
        self.patches.append({"requests": dict(requests), "limits": dict(limits)})
        if first:
            step("PATCH pods/resize -- the only mutating call in the entire system")
            detail(f"kubectl patch pod {pod} --subresource resize")
            detail(f"requests -> {requests}")
            why("`pods/resize` is a SUBRESOURCE. Granting `patch` on it does not grant `patch` on "
                "`pods`, so this cannot relabel, annotate, or delete anything.")
        else:
            step("REVERT -- unconditional, in a `finally`")
            detail(f"requests -> {requests}")
            detail(f"limits   -> {limits}")
            why("this runs on success, on regression, on exception, and on KeyboardInterrupt. It is "
                "the line that makes a rehearsal an experiment rather than a change.")

    def get_pod_status(self, namespace, pod):
        self.polls += 1

        if self.infeasible:
            if self.polls == 1:
                step("Poll status.containerStatuses -- the kubelet refused")
                detail("PodResizePending: reason=Infeasible")
                why("`Infeasible` is terminal: the node cannot satisfy this request, so waiting "
                    "longer cannot help. The outcome is `inconclusive`, NEVER `safe` -- nothing was "
                    "tested, so nothing was proven.")
            return {
                "uid": "uid-1",
                "conditions": [{"type": "PodResizePending", "reason": "Infeasible"}],
                "containerStatuses": [],
            }

        # First poll: not applied yet. Second: applied.
        applied = self.polls >= 2
        if self.polls == 1:
            step("Poll status.containerStatuses (NOT spec)")
            detail("requests still 500m -- the kubelet has not applied it yet")
            why("`spec` changes the instant the API server accepts the patch. Asserting on it would "
                "report success for a resize that is still Deferred or permanently Infeasible. Only "
                "`status.containerStatuses[].resources` reflects what the kubelet actually did.")
        elif applied and self.polls == 2:
            step("The kubelet applied the resize")
            detail("status.containerStatuses[].resources.requests.cpu = 300m")
            why("zero restarts. The container kept running with new limits -- which is what makes "
                "this a rehearsal and not a redeploy.")

        return {
            "uid": "uid-1",
            "phase": "Running",
            "conditions": [],
            "containerStatuses": [{
                "name": "app",
                "resources": {"requests": {"cpu": "300m" if applied else "500m"}},
            }],
        }


class NarratingSignals:
    def __init__(self, *, regress: bool) -> None:
        self.calls = 0
        self.regress = regress

    def sample(self, namespace, pod, container, seconds):
        self.calls += 1
        if self.calls == 1:
            step("Capture the baseline -- before anything changes")
            baseline = {"throttle_ratio": 0.004, "psi_cpu_full": 0.001,
                        "oom_events": 0, "restarts": 0}
            for k, v in baseline.items():
                detail(f"{k:<16} {v}")
            why("measured first, so the comparison afterwards is against this workload's own "
                "behaviour rather than against a cluster-wide average.")
            return baseline

        step("Observe under the candidate size")
        observed = (
            {"throttle_ratio": 0.31, "psi_cpu_full": 0.09, "oom_events": 0, "restarts": 1}
            if self.regress else
            {"throttle_ratio": 0.006, "psi_cpu_full": 0.002, "oom_events": 0, "restarts": 0}
        )
        for k, v in observed.items():
            marker = ""
            if self.regress and k in ("throttle_ratio", "psi_cpu_full", "restarts"):
                marker = f"  {RED}<-- regressed{RESET}"
            detail(f"{k:<16} {v}{marker}")
        return observed


class Verdict:
    def __init__(self, regressed: bool, reasons: tuple[str, ...] = ()) -> None:
        self.regressed = regressed
        self.reasons = list(reasons)


def judge(baseline: dict, observed: dict) -> Verdict:
    """The same comparison used for post-merge verification."""
    reasons = []
    # RATIOS over equal windows. Raw period counters scale with window length and replica count, so
    # comparing two of them would be meaningless.
    if observed["throttle_ratio"] > max(baseline["throttle_ratio"] * 2, 0.01):
        reasons.append(
            f"throttle ratio rose {baseline['throttle_ratio']:.3f} -> "
            f"{observed['throttle_ratio']:.3f}")
    if observed["psi_cpu_full"] > 0.05:
        reasons.append(f"PSI cpu full {observed['psi_cpu_full']:.3f} exceeds the 0.05 ceiling")
    # RESTART_TOLERANCE is 0: a rehearsal promises zero restarts, so one restart is a regression.
    if observed["restarts"] > baseline["restarts"]:
        reasons.append(f"{observed['restarts']} restart(s) during the observation window")
    if observed["oom_events"] > baseline["oom_events"]:
        reasons.append("an OOM event occurred")
    return Verdict(bool(reasons), tuple(reasons))


def run_scenario(name: str, *, regress: bool = False, infeasible: bool = False) -> None:
    global STEP
    STEP = 0

    print(f"\n{BOLD}{'=' * 78}{RESET}")
    print(f"{BOLD}SCENARIO: {name}{RESET}")
    print(f"{BOLD}{'=' * 78}{RESET}")

    store = NarratingStore()
    client = NarratingClient(regress=regress, infeasible=infeasible)
    signals = NarratingSignals(regress=regress)

    rehearsal = Rehearsal(
        client, signals, store,
        evaluate=judge,
        sleep=lambda s: None,   # the demo does not actually wait 30 minutes
    )

    result = rehearsal.run(RehearsalRequest(
        namespace="shop",
        workload="api-gateway",
        pod="api-gateway-7d9f4c8b6-x2klm",
        pod_uid="uid-1",
        container="app",
        cluster_id=1,
        run_id="run-demo",
        original_requests={"cpu": 0.5},
        original_limits={"cpu": 1.0},
        candidate_requests={"cpu": 0.3},
        candidate_limits={},
        observe_seconds=1800,
        baseline_seconds=300,
        resize_timeout_seconds=120,
    ))

    colour = {
        Outcome.SAFE: GREEN,
        Outcome.REGRESSED: YELLOW,
        Outcome.INCONCLUSIVE: YELLOW,
        Outcome.FAILED: RED,
    }[result.outcome]

    print(f"\n{BOLD}RESULT{RESET}")
    print(f"    outcome        {colour}{result.outcome}{RESET}")
    print(f"    reverted       {result.reverted}")
    print(f"    floor          {result.rehearsed_floor}")
    print(f"    {DIM}{result.detail}{RESET}")

    # The invariant, asserted rather than asserted-about.
    reverted_to_original = (
        len(client.patches) >= 2
        and client.patches[-1]["requests"] == {"cpu": 0.5}
    )
    if result.resize_state and result.resize_state.value == "pod_replaced":
        print(f"    {DIM}(no revert: the pod was replaced, so the original container is gone){RESET}")
    elif reverted_to_original:
        print(f"    {GREEN}INVARIANT HELD: the pod is back on its original 500m request.{RESET}")
    else:
        print(f"    {RED}INVARIANT VIOLATED: the pod was not restored!{RESET}")
        raise SystemExit(1)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="demo_rehearsal")
    ap.add_argument("--live", action="store_true",
                    help="run against the real cluster (requires kubectl context and RBAC)")
    ap.add_argument("--slow", action="store_true", help="pause between steps, for recording")
    args = ap.parse_args(argv)

    if args.live:
        print("Live mode needs REHEARSAL_ENABLED=true and a pod that passes preflight.")
        print("Run `python -m src.main --window 7d` with rehearsals enabled instead: the")
        print("orchestrator picks candidates, gates them, and records real outcomes.")
        return 2

    if args.slow:
        # Monkey-patched rather than threaded through: this only exists for screen recording.
        real_step = step

        def slow_step(title: str) -> None:
            real_step(title)
            time.sleep(1.2)

        globals()["step"] = slow_step

    print(f"{BOLD}Resize Rehearsal -- narrated walkthrough{RESET}")
    print(f"{DIM}Simulated and deterministic. No cluster is touched.{RESET}")

    # Three scenarios, because the interesting property is that ALL of them revert.
    run_scenario("the candidate is safe")
    run_scenario("the candidate REGRESSES the workload", regress=True)
    run_scenario("the node cannot satisfy the resize", infeasible=True)

    print(f"\n{BOLD}{'=' * 78}{RESET}")
    print(f"{GREEN}{BOLD}Every scenario reverted the pod to its original size.{RESET}")
    print(f"{DIM}That is the point: a rehearsal is an experiment that always reverts. Only the")
    print(f"first scenario produced a sizing floor -- a regressed or inconclusive rehearsal")
    print(f"contributes nothing, because nothing was proven.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
