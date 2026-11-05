# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_set_board_shape draws a round or polygonal outline too.

A demo run of a round LED board had only rectangles here and fell back
to drawing a circle on a mechanical layer and running Altium's "Define
Board Shape from Selected Objects" menu.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_tools

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _tool(monkeypatch, sent):
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
            return {"success": True}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    pcb_tools.register_pcb_tools(_Mcp())
    return tools["pcb_set_board_shape"]


def test_a_circle_goes_as_centre_and_radius(monkeypatch):
    sent = []
    asyncio.run(_tool(monkeypatch, sent)(shape="circle", center_x=4000,
                                         center_y=3000, radius=1378))
    assert sent == [("pcb.set_board_shape", {"shape": "circle", "cx": "4000",
                                             "cy": "3000", "radius": "1378"})]


def test_a_polygon_drops_a_repeated_first_point(monkeypatch):
    sent = []
    asyncio.run(_tool(monkeypatch, sent)(
        shape="polygon", points=[[0, 0], [1000, 0], [500, 800], [0, 0]]))
    assert sent[0][1] == {"shape": "polygon",
                          "points": "0,0;10000000,0;5000000,8000000"}


def test_a_round_board_with_a_tab_has_arc_edges(monkeypatch):
    """A 1000 mil circle whose right side carries a tab: an arc of the
    board circle from the tab's top side round to its bottom side, then
    the tab's straight sides and its round end about the hole."""
    import math
    sent = []
    r, w = 1000.0, 150.0
    y = w
    x = math.sqrt(r * r - y * y)
    pts = [
        {"x": x, "y": y, "arc_center": [0, 0]},          # board arc, CCW
        {"x": x, "y": -y},                               # tab bottom side
        {"x": 1200, "y": -y, "arc_center": [1200, 0]},   # tab end, CCW
        [1200, y],                                       # tab top side
    ]
    out = asyncio.run(_tool(monkeypatch, sent)(shape="polygon", points=pts))
    assert out["success"] is True
    fields = [v.split(",") for v in sent[0][1]["points"].split(";")]
    assert [len(f) for f in fields] == [7, 2, 7, 2]
    a1, a2 = float(fields[0][5]), float(fields[0][6])
    assert a1 == pytest.approx(math.degrees(math.atan2(y, x)))
    assert a2 == pytest.approx(360 - a1)
    assert (float(fields[2][5]), float(fields[2][6])) == (270.0, 90.0)


def test_an_arc_whose_ends_are_off_its_circle_is_refused(monkeypatch):
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent)(shape="polygon", points=[
        {"x": 1000, "y": 0, "arc_center": [0, 0]}, [0, 500], [-1000, 0]]))
    assert out["success"] is False and "circle" in out["error"]
    assert sent == []


def test_bad_shapes_send_nothing(monkeypatch):
    sent = []
    tool = _tool(monkeypatch, sent)
    assert asyncio.run(tool(shape="circle"))["success"] is False
    assert asyncio.run(tool(shape="polygon", points=[[0, 0], [1, 1]]))["success"] is False
    assert asyncio.run(tool(shape="hexagon"))["success"] is False
    assert sent == []


def test_a_rectangle_is_unchanged(monkeypatch):
    sent = []
    asyncio.run(_tool(monkeypatch, sent)(0, 0, 2000, 1000))
    assert sent[0][1] == {"x1": "0", "y1": "0", "x2": "2000", "y2": "1000"}


def test_the_handler_writes_quarter_arcs():
    text = _PAS.read_text(encoding="utf-8")
    body = text[text.index("Function PCB_SetBoardShape("):]
    body = body[:body.index("End;\n\n{")]
    assert "ShapeStr = 'circle'" in body and "ePolySegmentArc" in body
    assert "ShapeStr = 'polygon'" in body
    assert body.count("RefreshBoardOutline(Board)") >= 3


def test_arc_vertices_pass_the_servers_argument_check(monkeypatch):
    """Measured in a demo run: ``points`` typed as a list of number lists
    made the server refuse every arc vertex before the tool ran. Called
    through FastMCP, as a client calls it."""
    from mcp.server.fastmcp import FastMCP

    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    m = FastMCP("t")
    pcb_tools.register_pcb_tools(m)
    asyncio.run(m.call_tool("pcb_set_board_shape", {
        "shape": "polygon",
        "points": [{"x": 1000, "y": 0, "arc_center": [0, 0]},
                   [0, 1000], [-1000, 0]]}))
    assert sent and sent[0][1]["shape"] == "polygon"
