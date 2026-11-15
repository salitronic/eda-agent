# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tracked files name no Altium program module or crash address, and never
say eda-agent works without a licensed Altium."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Built from parts so this file does not match itself.
_MODULE = re.compile(
    r"\b(ScriptingSystem|Adv(Pcb|Sch|Sim)|DXP|WorkspaceManager)\." + "dll\\b",
    re.IGNORECASE)
_ADDRESS = re.compile(r"read of address|" + "F" * 16, re.IGNORECASE)
_NO_LICENCE = re.compile(
    r"\bno licen[cs]e|without (an? )?(altium )?licen[cs]e|unlicen[cs]ed|"
    r"licen[cs]e[- ]free", re.IGNORECASE)

_TEXT = {".py", ".pas", ".md", ".txt", ".html", ".js", ".json", ".toml",
         ".cfg", ".ini", ".yml", ".yaml", ".css"}


def _tracked() -> list[Path]:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=False)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [ROOT / p for p in out.stdout.splitlines()
            if Path(p).suffix.lower() in _TEXT and Path(p).name != "LICENSE"]


@pytest.mark.parametrize("name,pattern", [
    ("Altium module name", _MODULE),
    ("crash address", _ADDRESS),
    ("no-licence claim", _NO_LICENCE),
])
def test_no_tracked_file_carries_it(name, pattern):
    hits = []
    for path in _tracked():
        if path.name == Path(__file__).name or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert not hits, f"{name} in tracked files:\n" + "\n".join(hits)
