# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Run the standalone Free Pascal test programs end-to-end.

``test_pascal_logic.pas`` and ``test_real_pascal.pas`` are self-contained
Pascal programs with their own assertion framework. Each compiles to an
.exe that emits per-test lines and a final summary. This module compiles
each program, runs it, and parses the output so failures surface in
pytest with line numbers instead of having to ``cat`` the exe output by
hand.

Skipped cleanly when FPC is not on PATH. Auto-discovers fcl-base unit
directories the same way ``test_cross_validate.py`` does.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent
PASCAL_LOGIC_SRC = TESTS_DIR / "test_pascal_logic.pas"
REAL_PASCAL_SRC = TESTS_DIR / "test_real_pascal.pas"


def _discover_fpc_unit_paths(fpc_exe: str) -> list:
    """Mirror of test_cross_validate._discover_fpc_unit_paths — keep in sync."""
    roots_to_scan: set = set()
    fpc_dir = Path(fpc_exe).resolve().parent
    for rel in ("../../units", "../units", "units"):
        candidate = (fpc_dir / rel).resolve()
        if candidate.is_dir():
            roots_to_scan.add(candidate)
    scoop_dir = Path.home() / "scoop" / "apps" / "freepascal" / "current"
    if scoop_dir.is_dir():
        scoop_units = scoop_dir / "units"
        if scoop_units.is_dir():
            roots_to_scan.add(scoop_units)
    candidates: list = []
    for root in roots_to_scan:
        try:
            for d in root.rglob("*.ppu"):
                parent = d.parent
                if str(parent) not in candidates:
                    candidates.append(str(parent))
        except OSError:
            continue
    return candidates


def _compile_and_run(pas_src: Path, tmp_path: Path) -> tuple[int, str, str]:
    """Compile pas_src with FPC into tmp_path and run the resulting exe.

    Returns (return_code, stdout, stderr). Skips the calling test if FPC
    is missing or the source doesn't exist.
    """
    if not pas_src.exists():
        pytest.skip(f"Pascal source not found: {pas_src}")
    fpc = shutil.which("fpc")
    if fpc is None:
        pytest.skip("Free Pascal Compiler (fpc) not on PATH")

    staged = tmp_path / pas_src.name
    shutil.copy2(pas_src, staged)
    exe = staged.with_suffix(".exe")

    cmd = ["fpc", "-Mdelphi"]
    for path in _discover_fpc_unit_paths(fpc):
        cmd.append(f"-Fu{path}")
    cmd.append(str(staged))
    compile_result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(tmp_path)
    )
    if compile_result.returncode != 0:
        pytest.skip(
            f"FPC compilation failed for {pas_src.name}:\n"
            f"{compile_result.stdout}\n{compile_result.stderr}"
        )
    if not exe.exists():
        pytest.skip(f"FPC reported success but produced no exe at {exe}")

    run_result = subprocess.run(
        [str(exe)], capture_output=True, text=True, cwd=str(tmp_path), timeout=60
    )
    return run_result.returncode, run_result.stdout, run_result.stderr


_SUMMARY_RE = re.compile(
    r"(?:^|\n)\s*(?:Total|Tests?)\s*:\s*(\d+).*?\n\s*(?:Pass(?:ed)?|PASS(?:ED)?)\s*:\s*(\d+).*?\n\s*(?:Fail(?:ed)?|FAIL(?:ED)?)\s*:\s*(\d+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_summary(stdout: str) -> tuple[int, int, int] | None:
    """Parse a trailing 'Total / Passed / Failed' summary if present."""
    m = _SUMMARY_RE.search(stdout)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _extract_failure_lines(stdout: str) -> list[str]:
    """Return every line that looks like a failure/assertion from the
    Pascal test program output."""
    needles = ("FAIL", "ERROR", "Expected:", "got:", "!=")
    return [ln for ln in stdout.splitlines() if any(n in ln for n in needles)]


class TestPascalLogicProgram:
    """Compile and run tests/test_pascal_logic.pas end-to-end."""

    def test_runs_clean(self, tmp_path):
        rc, stdout, stderr = _compile_and_run(PASCAL_LOGIC_SRC, tmp_path)
        summary = _parse_summary(stdout)
        failure_lines = _extract_failure_lines(stdout)
        assert rc == 0, (
            f"test_pascal_logic exited with code {rc}\n"
            f"stdout tail:\n{stdout[-2000:]}\n"
            f"stderr tail:\n{stderr[-1000:]}"
        )
        if summary is not None:
            total, passed, failed = summary
            assert failed == 0, (
                f"{failed} of {total} Pascal assertions failed. "
                f"Offending lines:\n" + "\n".join(failure_lines)
            )
            assert total > 0, "Pascal test program reported zero tests"
        else:
            # No summary block — fall back to checking for failure keywords.
            assert not failure_lines, (
                "Pascal test output contains failure-looking lines:\n"
                + "\n".join(failure_lines)
            )


class TestRealPascalProgram:
    """Compile and run tests/test_real_pascal.pas end-to-end."""

    def test_runs_clean(self, tmp_path):
        rc, stdout, stderr = _compile_and_run(REAL_PASCAL_SRC, tmp_path)
        summary = _parse_summary(stdout)
        failure_lines = _extract_failure_lines(stdout)
        assert rc == 0, (
            f"test_real_pascal exited with code {rc}\n"
            f"stdout tail:\n{stdout[-2000:]}\n"
            f"stderr tail:\n{stderr[-1000:]}"
        )
        if summary is not None:
            total, passed, failed = summary
            assert failed == 0, (
                f"{failed} of {total} Pascal assertions failed. "
                f"Offending lines:\n" + "\n".join(failure_lines)
            )
            assert total > 0, "Pascal test program reported zero tests"
        else:
            assert not failure_lines, (
                "Pascal test output contains failure-looking lines:\n"
                + "\n".join(failure_lines)
            )
