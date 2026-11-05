# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pours and stitching vias on a board that is not a rectangle.

A demo run of a round LED board (54 mm disc, three flanges) had only a
rectangular pour, which ran past the edge, and stitching vias on a
rectangle grid that ignored the outline and accepted a same-net pad as a
place for a via. Now: a keep-out drawn on the outline, a pour that takes
the outline's own shape, dead copper removed, and stitching on the grid
points inside the board.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

# A 1000 mil radius disc about (4000, 3000), as pcb_set_board_shape writes
# a circle: four quarter arcs, each from its vertex counter-clockwise.
_DISC = {"point_count": 4, "vertices": [
    {"index": 0, "kind": "arc", "x": 3000, "y": 3000, "cx": 4000, "cy": 3000,
     "angle1": 180, "angle2": 270},
    {"index": 1, "kind": "arc", "x": 4000, "y": 2000, "cx": 4000, "cy": 3000,
     "angle1": 270, "angle2": 360},
    {"index": 2, "kind": "arc", "x": 5000, "y": 3000, "cx": 4000, "cy": 3000,
     "angle1": 0, "angle2": 90},
    {"index": 3, "kind": "arc", "x": 4000, "y": 4000, "cx": 4000, "cy": 3000,
     "angle1": 90, "angle2": 180}]}


def _tools(monkeypatch, sent, outline=_DISC):
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            if command == "pcb.get_board_outline":
                return outline
            return {"success": True}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    pcb_tools.register_pcb_tools(_Mcp())
    return tools


def test_the_disc_outline_is_a_circle():
    ring = pcb_tools._outline_ring(_DISC)
    assert len(ring) > 60
    for x, y in ring:
        assert abs(math.hypot(x - 4000, y - 3000) - 1000) < 0.5


def test_an_arc_whose_vertex_sits_at_angle2_runs_clockwise():
    # The same disc walked the other way round: each vertex at angle2.
    cw = {"vertices": [
        {"kind": "arc", "x": 3000, "y": 3000, "cx": 4000, "cy": 3000, "angle1": 90, "angle2": 180},
        {"kind": "arc", "x": 4000, "y": 4000, "cx": 4000, "cy": 3000, "angle1": 0, "angle2": 90},
        {"kind": "arc", "x": 5000, "y": 3000, "cx": 4000, "cy": 3000, "angle1": 270, "angle2": 360},
        {"kind": "arc", "x": 4000, "y": 2000, "cx": 4000, "cy": 3000, "angle1": 180, "angle2": 270}]}
    ring = pcb_tools._outline_ring(cw)
    assert all(abs(math.hypot(x - 4000, y - 3000) - 1000) < 0.5 for x, y in ring)
    assert ring[1][1] > 3000          # heads up from (3000, 3000), towards (4000, 4000)


def test_inside_keeps_its_margin_from_the_edge():
    ring = pcb_tools._outline_ring(_DISC)
    assert pcb_tools._inside_ring((4000, 3000), ring, 30)
    assert pcb_tools._inside_ring((4960, 3000), ring, 30)
    assert not pcb_tools._inside_ring((4990, 3000), ring, 30)
    assert not pcb_tools._inside_ring((4800, 3800), ring, 0)   # past the edge, in the corner


def test_stitching_on_a_round_board_sends_only_points_on_the_board(monkeypatch):
    sent = []
    out = asyncio.run(_tools(monkeypatch, sent)["pcb_place_stitching_vias"](
        net="GND", spacing_mils=100, dry_run=True))
    command, params = sent[-1]
    assert command == "pcb.place_stitching_vias"
    pts = [tuple(int(v) for v in p.split(",")) for p in params["points"].split(";")]
    assert all(math.hypot(x - 4000, y - 3000) <= 1000 - 30 for x, y in pts)
    # The 21 x 21 grid over the board's square, minus its corners.
    assert len(pts) + out["outside_board"] == 21 * 21
    assert out["outside_board"] > 80


def test_stitching_can_still_fill_a_plain_rectangle(monkeypatch):
    sent = []
    asyncio.run(_tools(monkeypatch, sent)["pcb_place_stitching_vias"](
        net="GND", x1_mils=0, y1_mils=0, x2_mils=100, y2_mils=100,
        within_board=False))
    assert [c for c, _ in sent] == ["pcb.place_stitching_vias"]
    assert "points" not in sent[0][1]


def test_the_board_pour_draws_the_keepout_first(monkeypatch):
    sent = []
    out = asyncio.run(_tools(monkeypatch, sent)["pcb_place_polygon_board"](
        net="GND", layer="BottomLayer", edge_clearance_mils=12))
    assert sent == [
        ("pcb.keepout_board_outline", {"width": "24"}),
        ("pcb.place_polygon_board", {"net": "GND", "layer": "BottomLayer",
                                     "pour_over": "true", "remove_dead": "true",
                                     "pour": "true"})]
    assert out["keepout"] == {"success": True}


def test_a_rectangle_pour_removes_dead_copper_by_default(monkeypatch):
    sent = []
    asyncio.run(_tools(monkeypatch, sent)["pcb_place_polygon_rect"](
        x1=0, y1=0, x2=10, y2=10, net="GND"))
    assert sent[0][1]["remove_dead"] == "true"


def _body(name):
    pas = _PAS.read_text(encoding="utf-8")
    body = pas[pas.index("Function " + name):]
    return body[:body.index("\nFunction ", 10)]


def test_the_handlers_are_wired():
    pas = _PAS.read_text(encoding="utf-8")
    assert "'place_polygon_board':     Result := PCB_PlacePolygonBoard(" in pas
    assert "'keepout_board_outline':   Result := PCB_KeepoutBoardOutline(" in pas
    assert "Polygon.RemoveDead :=" in _body("PCB_PlacePolygonRect")
    assert "Polygon.RemoveDead :=" in _body("PCB_PlacePolygonBoard")
    assert "eKeepOutLayer" in _body("PCB_KeepoutBoardOutline")


def test_stitching_refuses_a_via_in_any_pad():
    body = _body("PCB_PlaceStitchingVias")
    assert "(Hit.ObjectId = ePadObject) Or (Hit.ObjectId = eViaObject)" in body
    assert "ExtractJsonValue(Params, 'points')" in body
    # A board-edge arc's bounding box covers the board: measured, not boxed.
    assert "HasHit := StitchNear(Hit, PX, PY" in body
