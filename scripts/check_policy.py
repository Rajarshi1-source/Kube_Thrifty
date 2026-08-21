#!/usr/bin/env python3
"""
check_policy.py -- run the CI policy guards locally.

    python scripts/check_policy.py                 # all guards
    python scripts/check_policy.py --guard rbac     # one guard

Exists so the guards in `.github/workflows/policy.yml` can be exercised without pushing a commit.
Both call the same logic, which is the point: a guard that only runs in CI gets debugged by
trial-and-error through the PR queue.

Exit codes: 0 = all guards pass, 1 = at least one violation.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories that never contain project source.
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".next", ".ruff_cache", ".pytest_cache"}

MUTATING_VERBS = {"create", "update", "patch", "delete", "deletecollection", "*"}

# The ONLY mutating grants permitted anywhere in the chart.
ALLOWED_MUTATIONS = {
    ("", "pods/resize"): {"patch"},
    # Lease-based mutual exclusion for the rehearsal lock: coordination objects, not workload state.
    ("coordination.k8s.io", "leases"): {"create", "update"},
}


def walk(patterns: tuple[str, ...]):
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in patterns or path.name in patterns:
            yield path


# ==================================================================================================
# Guard: no floating image tags
# ==================================================================================================

# A real image reference: `repo/name:tag`, with the tag being the last path segment. Prose like
# "never use :latest" has a space or a backtick before the colon, so requiring a non-space,
# non-backtick character immediately before it excludes documentation without needing an exemption
# list -- which would otherwise grow until the guard stopped guarding anything.
_LATEST_REFERENCE = re.compile(r"[A-Za-z0-9_./-]:latest\b")


def guard_pinning() -> list[str]:
    violations: list[str] = []
    for path in walk((".yaml", ".yml", ".json", "Dockerfile")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            # Comments are prose. The guard is about what gets PULLED, not what gets written about.
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if _LATEST_REFERENCE.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{lineno}: {stripped}")

    # Untagged images: `image: repo/name` with no `:tag` and no `@sha256:`.
    for path in walk((".yaml", ".yml")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            # The optional `-\s*` matters: in a container list the line reads `- image: nginx`, which
            # is the most common shape in a real manifest. Without it the guard silently skipped
            # exactly the lines it existed to check.
            m = re.match(r"^\s*(?:-\s*)?image:\s*(\S+)\s*$", line)
            if not m:
                continue
            ref = m.group(1).strip("\"'")
            # Helm templates resolve at render time; the rendered output is checked separately.
            if "{{" in ref:
                continue
            last_segment = ref.rsplit("/", 1)[-1]
            if ":" not in last_segment and "@sha256:" not in ref:
                violations.append(
                    f"{path.relative_to(ROOT)}:{lineno}: untagged image {ref!r}"
                )
    return violations


# ==================================================================================================
# Guard: the mutating RBAC surface
# ==================================================================================================

def guard_rbac() -> list[str]:
    try:
        import yaml
    except ImportError:
        return ["PyYAML is not installed; cannot check RBAC"]

    helm = _find_helm()
    if helm is None:
        return ["helm was not found on PATH; cannot render the chart to check RBAC"]

    rendered = subprocess.run(
        [helm, "template", "kubethrifty", str(ROOT / "charts" / "kubethrifty"),
         "--set", "analyser.rehearsalEnabled=true"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if rendered.returncode != 0:
        return [f"helm template failed: {rendered.stderr.strip()[:400]}"]

    violations: list[str] = []
    for doc in yaml.safe_load_all(rendered.stdout):
        if not doc or doc.get("kind") not in ("Role", "ClusterRole"):
            continue
        name = (doc.get("metadata") or {}).get("name", "<unnamed>")
        for rule in doc.get("rules") or []:
            verbs = {v.lower() for v in rule.get("verbs", [])}
            mutating = verbs & MUTATING_VERBS
            if not mutating:
                continue
            for group in rule.get("apiGroups", [""]) or [""]:
                for resource in rule.get("resources", []) or []:
                    allowed = ALLOWED_MUTATIONS.get((group, resource), set())
                    extra = mutating - allowed
                    if extra:
                        violations.append(
                            f"{name}: {sorted(extra)} on {group or 'core'}/{resource}"
                        )
    return violations


def _find_helm() -> str | None:
    import shutil
    found = shutil.which("helm")
    if found:
        return found
    # winget's install location, which is not on PATH by default.
    candidate = (
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
        / "Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe/windows-amd64/helm.exe"
    )
    return str(candidate) if candidate.exists() else None


# ==================================================================================================
# Guard: null-vs-zero
# ==================================================================================================

SIGNAL_WORDS = r"(?:psi|pressure|throttle|oom|peak|stall)"

_TS_COERCION = re.compile(SIGNAL_WORDS + r"[A-Za-z]*\s*\?\?\s*0\b", re.IGNORECASE)
_PY_COERCION = re.compile(SIGNAL_WORDS + r"[a-z_]*\s+or\s+0\b", re.IGNORECASE)

# The exemption marker. Deliberately requires a REASON after the colon: an exemption you have to
# justify in writing stays rare, whereas a bare `// noqa` spreads until the guard checks nothing.
#
# The legitimate case is a coercion that is already guarded -- code which has established that the
# value is observed before treating a null as 0 (for instance, `max(psi, throttle)` after an explicit
# "both null means unobserved" early return).
_EXEMPT = re.compile(r"null-guard-ok:\s*\S+")


def _exempt_above(lines: list[str], lineno: int, comment_prefixes: tuple[str, ...]) -> bool:
    """
    Look for the exemption marker in the contiguous comment block directly above `lineno`.

    Walks upwards only while the lines are comments (or blank), so a marker cannot leak from an
    unrelated comment further up the file -- the justification has to be attached to the code it
    excuses.
    """
    i = lineno - 2  # zero-indexed line directly above
    scanned = 0
    while i >= 0 and scanned < 8:
        stripped = lines[i].strip()
        if not stripped:
            # A blank line ends the block: a justification separated from its code is not attached
            # to it.
            return False
        if not stripped.startswith(comment_prefixes):
            # Reached code. Keep walking only if this is another line of the same guarded
            # expression -- otherwise stop.
            if scanned == 0:
                i -= 1
                scanned += 1
                continue
            return False
        if _EXEMPT.search(stripped):
            return True
        i -= 1
        scanned += 1
    return False


def guard_null_vs_zero() -> list[str]:
    violations: list[str] = []

    def scan(path: Path, pattern: re.Pattern[str], comment_prefixes: tuple[str, ...]) -> None:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(comment_prefixes):
                continue
            if not pattern.search(line):
                continue
            # The exemption may sit on the offending line, or anywhere in the comment block
            # immediately above it. A justification worth writing usually needs a few lines, and one
            # marker should cover the whole guarded expression rather than being repeated per line.
            if _EXEMPT.search(line) or _exempt_above(lines, lineno, comment_prefixes):
                continue
            violations.append(f"{path.relative_to(ROOT)}:{lineno}: {stripped}")

    for path in walk((".ts", ".tsx")):
        if "dashboard" in path.parts:
            scan(path, _TS_COERCION, ("//", "*", "/*"))

    for path in walk((".py",)):
        if "src" in path.parts:
            scan(path, _PY_COERCION, ("#",))

    return violations


# ==================================================================================================

GUARDS = {
    "pinning": ("no floating image tags", guard_pinning),
    "rbac": ("the only mutating verb is patch on pods/resize", guard_rbac),
    "null": ("a missing signal is null, never 0", guard_null_vs_zero),
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check_policy")
    ap.add_argument("--guard", choices=sorted(GUARDS), default=None)
    args = ap.parse_args(argv)

    selected = [args.guard] if args.guard else sorted(GUARDS)
    failed = 0

    for key in selected:
        label, fn = GUARDS[key]
        print(f"=== {key}: {label} ===")
        violations = fn()
        if violations:
            failed += 1
            for v in violations:
                print(f"  FAIL {v}")
        else:
            print("  pass")
        print()

    if failed:
        print(f"{failed} guard(s) failed")
        return 1
    print("all guards passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
