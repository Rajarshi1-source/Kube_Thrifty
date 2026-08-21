#!/usr/bin/env python3
"""
github_pr_creator.py -- the ONLY path by which a durable resource change reaches a cluster.

Design constraints, all of them hard:

  * Opens pull requests. Never merges one, never force-pushes, never commits to the base branch.
    There is no code path in this module that can modify `main`.
  * The token needs `contents:write` + `pull_requests:write` on ONE repository. Not admin, not org
    scope, no workflow scope.
  * A PR body is an argument, not a diff. It states what changed, which floor was binding, what
    evidence tier it carries, what the node-delta saving is, and how to reproduce the verdict
    offline. A reviewer who cannot tell WHY from the PR body will rubber-stamp it, and a
    rubber-stamped right-sizing PR is how this class of tool causes outages.

`kubectl apply` appears nowhere in this project. GitOps is the audit trail: the PR is the review,
the merge is the change, and `git revert` is the rollback.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .cloud_pricing import SavingsReport
from .recommendation_engine import Action, EvidenceTier, ResourceRecommendation

log = logging.getLogger(__name__)

BRANCH_PREFIX = "kubethrifty/right-size"


class GitHubPRCreator:
    def __init__(self, token: str, repo: str, base_branch: str = "main") -> None:
        # Imported lazily so the module can be imported (and unit-tested) without PyGithub or a
        # network stack present.
        from github import Auth, Github

        self._gh = Github(auth=Auth.Token(token))
        self.repo = self._gh.get_repo(repo)
        self.base_branch = base_branch

    @staticmethod
    def branch_name(now: datetime | None = None) -> str:
        ts = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M")
        return f"{BRANCH_PREFIX}-{ts}"

    # -- body ------------------------------------------------------------------------------------
    @staticmethod
    def build_body(
        recommendations: list[ResourceRecommendation],
        savings: SavingsReport | None = None,
        rehearsal_rows: list[dict] | None = None,
        bundle_sha: str | None = None,
    ) -> str:
        actionable = [r for r in recommendations if r.action in (Action.REDUCE, Action.INCREASE)]
        blocked = [r for r in recommendations if r.reduction_blocked]
        review = [r for r in recommendations if r.action is Action.NEEDS_REVIEW]

        lines: list[str] = ["## Right-sizing proposal", ""]

        if savings is not None:
            lines += [f"**{savings.headline()}**", "", f"> {savings.caveat}", ""]
            if savings.nodes_removed <= 0:
                lines += [
                    "> No node-count change results from these sizes, so there is no saving to "
                    "claim. The changes are still worth making -- they reduce the blast radius of "
                    "a future scale-up -- but this PR does not reduce the bill.",
                    "",
                ]
        else:
            lines += [
                "_No packing simulation available for this run, so no monetary figure is claimed._",
                "",
            ]

        lines += [
            "### Changes",
            "",
            "| workload | container | resource | current | proposed | binding floor | basis | evidence | confidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in actionable:
            cur = "not observed" if r.current_request is None else f"{r.current_request:g}"
            lines.append(
                f"| `{r.namespace}/{r.workload}` | `{r.container}` | {r.resource} | {cur} | "
                f"{r.recommended_request:g} | {r.binding_constraint} | {r.sizing_basis} | "
                f"{r.evidence_tier} | {r.confidence} |"
            )
        lines.append("")

        if rehearsal_rows:
            lines += [
                "### Rehearsal evidence",
                "",
                "These sizes were applied to a live pod, observed, and reverted. `safe` means "
                "nothing regressed under the same judge used for post-merge verification.",
                "",
                "| workload | candidate | outcome | throttle before -> after | PSI full | restarts | OOM |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in rehearsal_rows:
                lines.append(
                    f"| `{row.get('workload')}` | {row.get('candidate')} | "
                    f"**{row.get('outcome')}** | {row.get('throttle_before')} -> "
                    f"{row.get('throttle_after')} | {row.get('psi_full')} | "
                    f"{row.get('restarts')} | {row.get('oom_events')} |"
                )
            lines.append("")

        if blocked:
            lines += [
                "### Reductions deliberately blocked",
                "",
                "Observed suffering overrides a statistical case for cutting. These workloads look "
                "over-provisioned on averages and are not.",
                "",
            ]
            for r in blocked:
                lines.append(f"- `{r.workload}/{r.container}` ({r.resource}): {r.rationale}")
            lines.append("")

        if review:
            lines += [
                "### Needs review (not changed)",
                "",
            ]
            for r in review:
                lines.append(
                    f"- `{r.workload}/{r.container}` ({r.resource}): only {r.samples} samples "
                    f"(minimum 168). Not enough history to change this automatically."
                )
            lines.append("")

        partial = [r for r in actionable if r.evidence_tier is EvidenceTier.PARTIAL]
        if partial:
            lines += [
                "### Evidence caveat",
                "",
                f"{len(partial)} recommendation(s) carry the `partial` tier: a signal the sizer "
                "wanted (usually PSI, sometimes cgroup `memory.peak`) was unavailable, so the "
                "safety checks ran with less information than they are designed for. These are "
                "modelled, not verified.",
                "",
            ]

        lines += [
            "### How these numbers were derived",
            "",
            "- CPU request = P95 x 1.20, limit = P99 x 1.50. CPU is compressible: exceeding the "
            "limit throttles, it does not kill.",
            "- Memory request = observed **peak** x 1.25 (x1.10 more for GC'd runtimes), and "
            "`limit == request`. Memory is never percentile-sized -- a percentile discards the top "
            "5% of samples, which are exactly the ones that OOMKill a container.",
            "- Floors compose with `max()`, never `min()`. The **binding floor** column says which "
            "one won.",
            "- Savings are a node-count delta only. Per-pod waste is a percentage, because clouds "
            "bill per node.",
            "",
        ]

        if bundle_sha:
            lines += [
                "### Reproduce offline",
                "",
                "```",
                f"thriftctl replay {bundle_sha}",
                "```",
                "",
                "The evidence bundle is content-addressed by sha256 of its canonical JSON, and the "
                "verdict function is pure, so this replays byte-identically on any machine.",
                "",
            ]

        lines += ["---", "", "_Opened by KubeThrifty. Review and merge; do not expect the tool to "
                  "merge for you. `git revert` is the rollback._"]
        return "\n".join(lines)

    # -- PR --------------------------------------------------------------------------------------
    def create_pr(
        self,
        file_changes: dict[str, str],
        recommendations: list[ResourceRecommendation],
        savings: SavingsReport | None = None,
        rehearsal_rows: list[dict] | None = None,
        bundle_sha: str | None = None,
        run_id: str | None = None,
    ) -> str | None:
        """Commit `file_changes` on a fresh branch and open a PR. Returns the PR URL, or None.

        Returns None (rather than raising) when there is nothing actionable: an empty PR is noise,
        and noise is how a review process stops being a review process.
        """
        if not file_changes:
            log.info("no manifest changes; not opening a PR")
            return None

        branch = self.branch_name()
        base = self.repo.get_branch(self.base_branch)

        # Create the branch. Note what is NOT here: any call that writes to self.base_branch.
        self.repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base.commit.sha)
        log.info("created branch %s from %s@%s", branch, self.base_branch, base.commit.sha[:8])

        for path, content in file_changes.items():
            message = f"chore(right-size): update {path}"
            try:
                existing = self.repo.get_contents(path, ref=branch)
                self.repo.update_file(path, message, content, existing.sha, branch=branch)
            except Exception:                                     # noqa: BLE001  (404 -> new file)
                self.repo.create_file(path, message, content, branch=branch)

        actionable = sum(1 for r in recommendations if r.action in (Action.REDUCE, Action.INCREASE))
        title = f"Right-size {actionable} container resource(s)"
        if savings is not None and savings.nodes_removed > 0:
            title += f" -- frees {savings.nodes_removed} node(s)"

        pr = self.repo.create_pull(
            title=title,
            body=self.build_body(recommendations, savings, rehearsal_rows, bundle_sha),
            head=branch,
            base=self.base_branch,
        )

        labels = ["kubethrifty", "right-sizing"]
        if any(r.evidence_tier is EvidenceTier.REHEARSED for r in recommendations):
            labels.append("rehearsal-verified")
        if any(r.evidence_tier is EvidenceTier.PARTIAL for r in recommendations):
            labels.append("partial-evidence")
        try:
            pr.add_to_labels(*labels)
        except Exception:                                         # noqa: BLE001
            # A missing label must not fail the run; the PR is what matters.
            log.debug("could not apply labels %s", labels)

        log.info("opened PR %s", pr.html_url)
        return pr.html_url
