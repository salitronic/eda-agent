# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_place_region can place a polygon, not only a rectangle.

PCB_PlaceRegion drew an axis-aligned rectangle from x1..y2, read as whole
mils, so a board region of any other shape (a taper from a track onto a
pad, a keepout round an odd outline) had no writer. A footprint has one,
add_footprint_regions; a board did not. It now takes points=x,y/x,y/...
in decimal mils, refuses a malformed list instead of drawing a spike to
0,0, and replies with the outline read back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_PlaceRegion"]


def test_a_malformed_or_short_list_is_refused_before_anything_is_made():
    body = _body()
    count = body.index("NPts := RegionPointCount(PointsStr);")
    refuse = body.index("'BAD_POINTS'")
    assert count < refuse < body.index("PCBServer.PreProcess;")
    assert "If NPts < 3 Then" in body[count:refuse]


def test_the_vertices_keep_their_decimals():
    body = _body()
    assert "Contour.Count := NPts;" in body
    assert ("Contour.X[I] := MilsToCoordF(\n"
            "                    StrToFloatDef(RegionPointCoord(PointsStr, I, True), 0));") \
        in body.replace("\r\n", "\n")
    assert ("Contour.Y[I] := MilsToCoordF(\n"
            "                    StrToFloatDef(RegionPointCoord(PointsStr, I, False), 0));") \
        in body.replace("\r\n", "\n")


def test_the_rectangle_is_still_drawn_without_points():
    body = _body()
    assert "Contour.Count := 4;" in body
    assert body.index("If NPts >= 3 Then") < body.index("Contour.Count := 4;")


def test_the_reply_reads_the_outline_back_from_the_region():
    body = _body()
    assert "ReadBack := Region.MainContour;" in body
    assert "CoordToMilsF(ReadBack.X[I])" in body
    assert "CoordToMilsF(ReadBack.Y[I])" in body
    assert '"vertices":' in body and '"points":[' in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_place_region"](**kw))
    return out, bridge.sent


def test_points_reach_the_script_with_their_decimals(monkeypatch):
    _, sent = _call(monkeypatch, layer="TopLayer", net="GND", points=[
        {"x": 5465, "y": 2995}, {"x": 5474.4719, "y": 2956.7864},
        {"x": 5559.2625, "y": 2977.8036}])
    assert sent[0][0] == "pcb.place_region"
    assert sent[0][1]["points"] == \
        "5465.0,2995.0/5474.4719,2956.7864/5559.2625,2977.8036"
    assert sent[0][1]["net"] == "GND"


def test_pairs_are_accepted_as_well_as_objects(monkeypatch):
    _, sent = _call(monkeypatch, points=[[0, 0], [100, 0], [50, 80.5]])
    assert sent[0][1]["points"] == "0.0,0.0/100.0,0.0/50.0,80.5"


def test_without_points_the_rectangle_goes_as_before(monkeypatch):
    _, sent = _call(monkeypatch, x1=0, y1=0, x2=100, y2=50)
    assert "points" not in sent[0][1]
    assert sent[0][1]["x2"] == "100" and sent[0][1]["y2"] == "50"


def test_a_short_or_malformed_outline_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, points=[{"x": 0, "y": 0}, {"x": 1, "y": 1}])
    assert out["ok"] is False and not sent
    out, sent = _call(monkeypatch, points=[{"x": 0}, {"x": 1, "y": 1},
                                           {"x": 2, "y": 2}])
    assert out["ok"] is False and not sent
