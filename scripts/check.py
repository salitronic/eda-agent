# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The fast checks to run before a push.

    python scripts/check.py            # changes against origin/main
    python scripts/check.py --base X   # changes against another ref

Runs, in about a minute: the generated Pascal rules check, the Pascal
lint, the repo-wide guards that any change can break (copyright lines,
dispatch tables, declared imports, docs counts), the tests that name a
Python module this branch changed, and the tests that name a Pascal
routine it changed or a routine calling one. The full suite stays with CI.

.githooks/pre-push runs this; enable it once with
    git config core.hooksPath .githooks
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

GUARDS = (
    "test_every_source_file_names_its_copyright.py",
    "test_bridge_handlers_reachable.py",
    "test_imports_are_declared.py",
    "test_atomic_rename_survives_a_scanner.py",
    "test_no_client_design_data.py",
    "test_ui_automation_is_a_toolset.py",
    "test_contributor_docs_match_the_repo.py",
    "test_readme_tool_counts.py",
    "test_pas_project_consistency.py",
    "test_pas_constants_declared.py",
    "test_version_is_consistent.py",
    "test_request_state_is_reset_in_one_place.py",
)


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def changed_files(base: str) -> set[str]:
    files = set(_git("diff", "--name-only", f"{base}...HEAD"))
    files |= set(_git("diff", "--name-only", "HEAD"))
    files |= {line[3:] for line in _git("status", "--porcelain") if line[:2] == "??"}
    return {f.replace("\\", "/") for f in files}


def _changed_lines(base: str, path: str) -> set[int]:
    """Line numbers of the working copy of path that differ from base."""
    if not _git("ls-files", path):
        return set(range(1, 1 + len((ROOT / path).read_text(
            encoding="utf-8", errors="replace").splitlines())))
    lines: set[int] = set()
    for hunk in _git("diff", "-U0", base, "--", path):
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", hunk)
        if m:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            lines.update(range(start, start + max(count, 1)))
    return lines


def changed_routines(base: str, path: str) -> set[str]:
    """Pascal routines a change touched, and the routines that call them.

    Callers count because a test that compiles a routine with fpc names
    the routine it compiles, not every helper that routine calls.
    """
    text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
    starts = [(i + 1, m.group(1)) for i, line in enumerate(text.splitlines())
              if (m := re.match(r"(?:Function|Procedure)\s+(\w+)", line))]
    touched: set[str] = set()
    for line in _changed_lines(base, path):
        owner = [name for start, name in starts if start <= line]
        if owner:
            touched.add(owner[-1])
    bodies = re.split(r"(?m)^(?=(?:Function|Procedure)\s+\w+)", text)
    for body in bodies:
        m = re.match(r"(?:Function|Procedure)\s+(\w+)", body)
        if m and any(re.search(rf"\b{t}\s*\(", body) for t in touched):
            touched.add(m.group(1))
    return touched


def targeted_tests(base: str, changed: set[str]) -> set[Path]:
    picked: set[Path] = set()
    texts = {p: p.read_text(encoding="utf-8", errors="replace")
             for p in TESTS.rglob("test_*.py")}
    names = {Path(f).stem for f in changed
             if f.endswith(".py") and f.startswith("src/") and Path(f).stem != "__init__"}
    for f in changed:
        if f.endswith(".pas") and (ROOT / f).exists() and "Altium_MCP" not in f:
            names |= changed_routines(base, f)
    for path, text in texts.items():
        if any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
            picked.add(path)
    picked |= {ROOT / f for f in changed
               if f.startswith("tests/") and f.endswith(".py") and (ROOT / f).exists()}
    return picked


def main(argv: list[str]) -> int:
    base = argv[argv.index("--base") + 1] if "--base" in argv else "origin/main"
    steps = [
        [sys.executable, "scripts/altium/sync_rules.py", "--check"],
        [sys.executable, "scripts/altium/lint.py"],
    ]
    for step in steps:
        if subprocess.run(step, cwd=ROOT).returncode:
            return 1
    tests = {TESTS / g for g in GUARDS} | targeted_tests(base, changed_files(base))
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    try:
        import xdist  # noqa: F401
        args += ["-n", "auto"]
    except ImportError:
        pass
    print(f"check: {len(tests)} test files", flush=True)
    return subprocess.run(args + sorted(str(t) for t in tests if t.exists()),
                          cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
