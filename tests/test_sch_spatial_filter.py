# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic spatial queries must filter by Location, not bounding box.

ISch_Iterator.AddFilter_Area matches an object's bounding box. A net
label's box is the text, which extends hundreds of mils from Location,
so a 1-mil square at a pin still hits every nearby label in the column.
That is how obj_explain_pin reported I_PHASE_B_OUT on R22 pin 2 (400 mil
away) and how audit_find_net_label_conflicts reported 213 'conflicts'
that were just 100-mil pin-pitch neighbours.

These tests cannot open Altium. They pin the Pascal-side contract: after
AddFilter_Area, both handlers re-check Location (or pin electrical end,
or wire vertices) with CoordWithinTol.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PASCAL = REPO / "scripts" / "altium"


def _fn_body(source: str, fn_name: str) -> str:
    marker = f"Function {fn_name}("
    start = source.find(marker)
    assert start >= 0, f"{fn_name} not found"
    nxt = source.find("\nFunction ", start + len(marker))
    nxt2 = source.find("\nProcedure ", start + len(marker))
    ends = [i for i in (nxt, nxt2) if i > start]
    end = min(ends) if ends else len(source)
    return source[start:end]


def test_utils_defines_coord_within_tol_and_point_near_segment():
    text = (PASCAL / "Utils.pas").read_text(encoding="utf-8")
    assert "Function CoordWithinTol(" in text
    assert "Function PointNearSegment(" in text


def test_explain_pin_rejects_bbox_only_hits():
    body = _fn_body(
        (PASCAL / "Generic.pas").read_text(encoding="utf-8"),
        "Gen_ExplainPin",
    )
    assert "AddFilter_Area(" in body
    assert "CoordWithinTol(" in body
    assert "PointNearSegment(" in body
    assert "BOUNDING BOXES" in body or "bounding box" in body.lower()
    # Hits must carry coordinates so a leak is visible even if the
    # filter regresses.
    assert "JsonInt('x_mils'" in body
    assert "JsonInt('y_mils'" in body


def test_net_label_conflict_audit_requires_location_match():
    body = _fn_body(
        (PASCAL / "Audit.pas").read_text(encoding="utf-8"),
        "Audit_FindNetLabelConflicts",
    )
    assert "AddFilter_Area(" in body
    assert "CoordWithinTol(OtherLoc.X, LX, Tol)" in body
    assert "CoordWithinTol(OtherLoc.Y, LY, Tol)" in body


def test_highlight_net_prefers_focused_schematic():
    body = _fn_body(
        (PASCAL / "Generic.pas").read_text(encoding="utf-8"),
        "Gen_HighlightNet",
    )
    assert "ExtractJsonValue(Params, 'context')" in body
    assert "PreferSch" in body
    assert "DM_FocusedDocument" in body
    assert "DM_DocumentKind" in body
    # Selection-only paint must not take the robot lock: MCP poller +
    # PreProcess + a debugger break deadlocks Altium (observed 2026-08-26).
    assert "SchServer.ProcessControl.PreProcess" not in body
    assert "SchDeselectAllObjects" in body
