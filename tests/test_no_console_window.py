# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every console program the server starts is started without a window.

From a windowless host (pythonw, a Tk application) a console child started
without CREATE_NO_WINDOW opens a visible console window; the dialog OCR's
PowerShell flashed one up while Altium was busy (issue #142). Each process
start in the package must pass no_window() or its own creationflags.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from eda_agent.noconsole import no_window

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "src" / "eda_agent", ROOT / "extensions"]

_STARTERS = {"run", "Popen", "call", "check_call", "check_output",
             "create_subprocess_exec", "create_subprocess_shell"}

#: Starts that need no flag, each with the reason.
_EXEMPT = {
    # Altium itself, a windowed program: there is no console to open.
    ("cli.py", "Popen"): "starts X2.EXE",
    # Run on the Linux side under WSL, where the flag does not exist.
    ("process_manager.py", "run"): "tasklist from WSL",
    ("config.py", "run"): "wslpath under WSL",
    # A developer build script, run from a terminal.
    ("build.py", "run"): "extension build in a terminal",
}


def _starts(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        owner = node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else \
            owner.attr if isinstance(owner, ast.Attribute) else ""
        if name not in _STARTERS:
            continue
        if owner_name == "asyncio":
            if not name.startswith("create_subprocess"):
                continue          # asyncio.run runs a coroutine, not a program
        elif owner_name not in ("subprocess", "_sp"):
            continue
        yield node, name


def _windowless(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "creationflags":
            return True
        if kw.arg is None and isinstance(kw.value, ast.Call) \
                and getattr(kw.value.func, "id", "") == "no_window":
            return True
    return False


def test_every_process_start_passes_no_window():
    missing, seen = [], 0
    for base in SOURCES:
        for path in base.rglob("*.py"):
            if "node_modules" in path.parts:
                continue
            for node, name in _starts(path):
                seen += 1
                if (path.name, name) in _EXEMPT or _windowless(node):
                    continue
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}")
    assert seen >= 15, f"the walk found only {seen} process starts"
    assert not missing, "started without no_window(): " + ", ".join(missing)


def test_the_dialog_ocr_start_is_windowless():
    # The start the issue was found on, named so a rename cannot hide it.
    path = ROOT / "src" / "eda_agent" / "ui" / "ocr.py"
    starts = list(_starts(path))
    assert starts and all(_windowless(node) for node, _ in starts)


def test_no_window_gives_the_flag_where_it_exists():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        assert no_window() == {"creationflags": flags}
    else:
        assert no_window() == {}
