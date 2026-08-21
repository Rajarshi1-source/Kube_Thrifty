#!/usr/bin/env python3
"""
Tests for the CI policy guards themselves.

A guard that always passes is indistinguishable from no guard. These tests confirm each one actually
FIRES on a violation, and that the exemption mechanism requires a written justification rather than a
bare marker.

The guards live in `scripts/check_policy.py`, outside the analyser package, so they are imported by
path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_policy.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_policy", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_policy"] = module
    spec.loader.exec_module(module)
    return module


policy = _load()


# ==================================================================================================
# The null-vs-zero guard must FIRE
# ==================================================================================================

def _scan_snippet(tmp_path: Path, monkeypatch, filename: str, body: str) -> list[str]:
    """Run the null guard against a single synthetic file."""
    root = tmp_path
    target_dir = root / ("dashboard" if filename.endswith((".ts", ".tsx")) else "src")
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / filename).write_text(body, encoding="utf-8")
    monkeypatch.setattr(policy, "ROOT", root)
    return policy.guard_null_vs_zero()


def test_ts_coercion_is_caught(tmp_path, monkeypatch):
    violations = _scan_snippet(
        tmp_path, monkeypatch, "bad.ts",
        "const psi = psiStalledRatio ?? 0;\n",
    )
    assert len(violations) == 1
    assert "bad.ts" in violations[0]


def test_python_coercion_is_caught(tmp_path, monkeypatch):
    violations = _scan_snippet(
        tmp_path, monkeypatch, "bad.py",
        "value = throttle_ratio or 0\n",
    )
    assert len(violations) == 1


def test_exemption_on_the_same_line_is_honoured(tmp_path, monkeypatch):
    violations = _scan_snippet(
        tmp_path, monkeypatch, "ok.ts",
        "const psi = psiStalledRatio ?? 0; // null-guard-ok: guarded by an early return above\n",
    )
    assert violations == []


def test_exemption_in_the_comment_block_above_is_honoured(tmp_path, monkeypatch):
    violations = _scan_snippet(
        tmp_path, monkeypatch, "ok.ts",
        "// null-guard-ok: the caller already established that at least one signal was observed,\n"
        "// so the 0 here is a max() identity rather than a measurement.\n"
        "const psi = psiStalledRatio ?? 0;\n",
    )
    assert violations == []


def test_bare_marker_without_a_reason_is_rejected(tmp_path, monkeypatch):
    """
    The marker requires a REASON.

    An exemption you must justify in writing stays rare; a bare `noqa` spreads until the guard checks
    nothing at all.
    """
    violations = _scan_snippet(
        tmp_path, monkeypatch, "bad.ts",
        "const psi = psiStalledRatio ?? 0; // null-guard-ok:\n",
    )
    assert len(violations) == 1


def test_exemption_separated_by_a_blank_line_does_not_apply(tmp_path, monkeypatch):
    """A justification detached from its code is not attached to it."""
    violations = _scan_snippet(
        tmp_path, monkeypatch, "bad.ts",
        "// null-guard-ok: this belongs to something else entirely\n"
        "\n"
        "const psi = psiStalledRatio ?? 0;\n",
    )
    assert len(violations) == 1


def test_non_signal_defaults_are_not_flagged(tmp_path, monkeypatch):
    """The guard is about SIGNALS. Defaulting a limit or a count to 0 is unremarkable."""
    violations = _scan_snippet(
        tmp_path, monkeypatch, "fine.ts",
        "const limit = requestedLimit ?? 0;\nconst count = items.length ?? 0;\n",
    )
    assert violations == []


# ==================================================================================================
# The pinning guard must FIRE
# ==================================================================================================

def _scan_pinning(tmp_path: Path, monkeypatch, filename: str, body: str) -> list[str]:
    (tmp_path / filename).write_text(body, encoding="utf-8")
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    return policy.guard_pinning()


def test_latest_tag_is_caught(tmp_path, monkeypatch):
    violations = _scan_pinning(
        tmp_path, monkeypatch, "bad.yaml",
        "spec:\n  containers:\n    - image: nginx:latest\n",
    )
    assert len(violations) == 1
    assert "bad.yaml" in violations[0]


def test_untagged_image_is_caught(tmp_path, monkeypatch):
    violations = _scan_pinning(
        tmp_path, monkeypatch, "bad.yaml",
        "spec:\n  containers:\n    - image: nginx\n",
    )
    assert len(violations) == 1
    assert "untagged" in violations[0]


def test_pinned_image_passes(tmp_path, monkeypatch):
    violations = _scan_pinning(
        tmp_path, monkeypatch, "good.yaml",
        "spec:\n  containers:\n    - image: nginx:1.29.4-alpine\n",
    )
    assert violations == []


def test_digest_pinned_image_passes(tmp_path, monkeypatch):
    violations = _scan_pinning(
        tmp_path, monkeypatch, "good.yaml",
        "image: nginx@sha256:" + "a" * 64 + "\n",
    )
    assert violations == []


def test_prose_about_latest_is_not_flagged(tmp_path, monkeypatch):
    """
    A comment saying "never use :latest" must not fail the build.

    Without this the guard is unusable: the project's own documentation states the rule, and an
    exemption list would grow until the guard stopped guarding.
    """
    violations = _scan_pinning(
        tmp_path, monkeypatch, "doc.yaml",
        "# Never `:latest`, anywhere in this file.\n"
        "# Do not use :latest for any image.\n"
        "image: nginx:1.29.4\n",
    )
    assert violations == []


def test_helm_template_expression_is_not_flagged(tmp_path, monkeypatch):
    """Templated refs resolve at render time; the rendered output is checked separately."""
    violations = _scan_pinning(
        tmp_path, monkeypatch, "tpl.yaml",
        'image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"\n',
    )
    assert violations == []


# ==================================================================================================
# The RBAC guard's allow-list
# ==================================================================================================

def test_rbac_allow_list_permits_only_pods_resize_patch():
    """
    The allow-list IS the security claim, so it is asserted directly.

    If somebody widens it, this test is what makes that a visible, reviewed change rather than a
    quiet one.
    """
    allowed = policy.ALLOWED_MUTATIONS
    assert allowed[("", "pods/resize")] == {"patch"}
    # Leases are coordination objects for the rehearsal lock, not workload state.
    assert allowed[("coordination.k8s.io", "leases")] == {"create", "update"}
    # Nothing else may mutate anything.
    assert len(allowed) == 2

    # The verbs the guard treats as mutating.
    assert "delete" in policy.MUTATING_VERBS
    assert "*" in policy.MUTATING_VERBS
    assert "patch" in policy.MUTATING_VERBS


@pytest.mark.parametrize(
    "group,resource,verb",
    [
        ("", "pods", "delete"),          # would allow restarting a workload
        ("apps", "deployments", "patch"),  # would bypass the Git PR path
        ("", "secrets", "create"),        # a right-sizer has no business here
        ("", "pods/exec", "create"),      # a shell into any container
    ],
)
def test_dangerous_grants_are_not_in_the_allow_list(group, resource, verb):
    allowed = policy.ALLOWED_MUTATIONS.get((group, resource), set())
    assert verb not in allowed
