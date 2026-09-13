# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Persisted DelphiScript-fault state for the dashboard banner (roadmap 1.2).

When the bridge detects an engine fault it records the diagnosis + recovery
steps to ``last_fault.json`` in the workspace; the web dashboard reads it to
show a recovery banner ("the loop is down: here's how to restart it"). The
bridge clears it once a command succeeds again, so the banner disappears the
moment the loop recovers.

Kept out of the bridge's hot path: the bridge only touches these files when a
fault has actually been recorded (guarded by an in-memory flag), so a healthy
session pays nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from eda_agent.atomicfile import replace_with_retry

FAULT_FILE = "last_fault.json"


def record_fault(workspace_dir: Path, guidance: dict, *, when: str = "") -> None:
    """Write the current fault + recovery guidance to the workspace."""
    path = Path(workspace_dir) / FAULT_FILE
    payload = {"recovery": guidance, "when": when}
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        replace_with_retry(tmp, path)
    except OSError:
        pass  # a fault record is best-effort; never mask the real error


def read_fault(workspace_dir: Path) -> Optional[dict]:
    """Return the recorded fault payload, or None if the loop is healthy."""
    path = Path(workspace_dir) / FAULT_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_fault(workspace_dir: Path) -> None:
    """Remove the fault record (a command has succeeded; loop recovered)."""
    path = Path(workspace_dir) / FAULT_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
