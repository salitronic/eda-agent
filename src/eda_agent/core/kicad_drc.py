# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Geometric DRC for KiCad via the official ``kicad-cli``.

KiCad's IPC API exposes no DRC runner, so the design-rule check is delegated to
``kicad-cli pcb drc`` -- KiCad's own bundled command-line tool, not a
third-party engine. It runs on the board file on disk, so the report reflects
the last saved state; callers should save pending edits first.

The JSON report is normalized into the same finding vocabulary the rest of the
review uses (severity + description + count), so a DRC result reads the same way
regardless of backend.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

from ..noconsole import no_window

_DRC_TIMEOUT = 180.0


async def run_kicad_cli_drc(cli_path: str, board_path: str) -> dict[str, Any]:
    """Run ``kicad-cli pcb drc`` on ``board_path`` and return a normalized dict.

    Raises RuntimeError with the tool's own message on a hard failure (bad
    invocation, crash); a clean board simply yields zero violations.
    """
    if not os.path.exists(cli_path):
        raise RuntimeError(f"kicad-cli not found at {cli_path}")
    if not os.path.exists(board_path):
        raise RuntimeError(
            f"board file not found at {board_path}; save the board first")

    fd, report_path = tempfile.mkstemp(suffix=".drc.json", prefix="eda_agent_")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path, "pcb", "drc",
            "--output", report_path,
            "--format", "json",
            "--exit-code-violations",
            board_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **no_window(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_DRC_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("kicad-cli DRC timed out") from None

        # Exit 0 = clean, 5 = violations found (both produce a report). Any
        # other code is a real failure.
        if proc.returncode not in (0, 5):
            msg = (stderr or stdout or b"").decode(errors="replace").strip()
            raise RuntimeError(
                f"kicad-cli DRC failed (exit {proc.returncode}): {msg}")

        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    finally:
        try:
            os.remove(report_path)
        except OSError:
            pass

    return _normalize(report, board_path)


async def run_kicad_cli_erc(cli_path: str, sch_path: str) -> dict[str, Any]:
    """Run ``kicad-cli sch erc`` on ``sch_path`` and return a normalized dict.

    KiCad's ERC JSON groups violations per sheet; they are flattened here into
    the same finding vocabulary as DRC.
    """
    if not os.path.exists(cli_path):
        raise RuntimeError(f"kicad-cli not found at {cli_path}")
    if not os.path.exists(sch_path):
        raise RuntimeError(
            f"schematic not found at {sch_path}; save the schematic first")

    fd, report_path = tempfile.mkstemp(suffix=".erc.json", prefix="eda_agent_")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path, "sch", "erc",
            "--output", report_path,
            "--format", "json",
            "--severity-all",
            "--exit-code-violations",
            sch_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **no_window(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_DRC_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("kicad-cli ERC timed out") from None
        if proc.returncode not in (0, 5):
            msg = (stderr or stdout or b"").decode(errors="replace").strip()
            raise RuntimeError(
                f"kicad-cli ERC failed (exit {proc.returncode}): {msg}")
        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    finally:
        try:
            os.remove(report_path)
        except OSError:
            pass

    return _normalize_erc(report, sch_path)


def _normalize_erc(report: dict[str, Any], sch_path: str) -> dict[str, Any]:
    sheets = report.get("sheets", []) or []
    flat: list[dict[str, Any]] = []
    for sheet in sheets:
        for v in sheet.get("violations", []) or []:
            flat.append(v)
    rows = []
    summary = {"error": 0, "warning": 0}
    for v in flat:
        sev = (v.get("severity", "") or "").lower()
        rows.append({"type": v.get("type", ""), "severity": sev,
                     "description": v.get("description", ""),
                     "item_count": len(v.get("items", []) or [])})
        summary[sev] = summary.get(sev, 0) + 1
    return {
        "ok": True,
        "source": "kicad",
        "schematic": os.path.basename(sch_path),
        "violation_count": len(flat),
        "sheet_count": len(sheets),
        "summary": summary,
        "violations": rows[:200],
        "note": "ERC runs on the schematic on disk; save pending edits first.",
    }


def _normalize(report: dict[str, Any], board_path: str) -> dict[str, Any]:
    violations = report.get("violations", []) or []
    unconnected = report.get("unconnected_items", []) or []
    parity = report.get("schematic_parity", []) or []

    def _row(v: dict[str, Any]) -> dict[str, Any]:
        items = v.get("items", []) or []
        return {
            "type": v.get("type", ""),
            "severity": (v.get("severity", "") or "").lower(),
            "description": v.get("description", ""),
            "item_count": len(items),
        }

    rows = [_row(v) for v in violations]
    summary = {"error": 0, "warning": 0}
    for r in rows:
        sev = r["severity"]
        if sev in summary:
            summary[sev] += 1
        else:
            summary[sev] = summary.get(sev, 0) + 1

    return {
        "ok": True,
        "source": "kicad",
        "board": os.path.basename(board_path),
        "violation_count": len(violations),
        "unconnected_count": len(unconnected),
        "schematic_parity_count": len(parity),
        "summary": summary,
        "violations": rows[:200],
        "coordinate_units": report.get("coordinate_units", ""),
        "note": ("DRC runs on the board file on disk; save pending edits for "
                 "current results. Schematic-parity requires the schematic."),
    }
