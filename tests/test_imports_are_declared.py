# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every third-party package src/ imports is declared in pyproject.toml.

CI installs only what pyproject declares, while a local environment
usually has more, so an undeclared import passes here and fails there.
Imports inside functions count too: a lazy import is still a dependency.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "eda_agent"

# Import name -> the distribution that provides it, where they differ.
DISTRIBUTION = {
    "PIL": "pillow", "win32api": "pywin32",
    "win32com": "pywin32", "win32con": "pywin32", "win32gui": "pywin32",
    "win32process": "pywin32", "win32ui": "pywin32", "pythoncom": "pywin32",
    "pywintypes": "pywin32", "win32clipboard": "pywin32",
}
# Provided by a declared distribution rather than declared themselves.
TRANSITIVE = {"werkzeug": "flask"}
# Not packages: modules a running server loads from a directory it puts on
# sys.path itself (the EasyEDA extension's build.py).
LOCAL = {"build"}


def _declared() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    specs = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        specs += extra
    return {re.split(r"[<>=!~;\[ ]", s, 1)[0].strip().lower() for s in specs}


def _imported() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                found.setdefault(top, str(path.relative_to(ROOT)))
    return found


def test_every_third_party_import_is_declared():
    declared = _declared()
    missing = []
    for name, where in sorted(_imported().items()):
        if name in sys.stdlib_module_names or name == "eda_agent" or name in LOCAL:
            continue
        dist = TRANSITIVE.get(name) or DISTRIBUTION.get(name, name).lower()
        if dist not in declared:
            missing.append(f"{name} ({where})")
    assert not missing, (
        f"imported but not declared in pyproject.toml: {missing}. Add the "
        "distribution to dependencies, or to an extra referenced from dev")
