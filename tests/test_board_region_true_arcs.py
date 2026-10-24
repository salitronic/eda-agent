# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A board region can hold a true arc, and a region's shape can be read back.

lib_add_footprint_regions builds a footprint region from shape segments
with true arcs; pcb_place_region could only lay a contour of points, so a
board region's rounded corner was a run of short lines. It now takes the
same [x, y, sweep] points, sends the same arcs=i,cx,cy,r,a1,a2 field, and
builds the region the same way.

Neither side could read a region's shape back: get_footprint_primitives
counted arc_edges and gave no vertices, get_layout_model the flattened
contour only. Both now give [x, y, sweep] per vertex, the form the two
writers take, read from ShapeSegments.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools
from eda_agent.tools.library import _regions_payload

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _pcb(name):
    return load(_ALTIUM / "PCB.pas")[name]


def _lib(name):
    return load(_ALTIUM / "Library.pas")[name]


def test_place_region_refuses_a_bad_arc_before_anything_is_made():
    body = _pcb("PCB_PlaceRegion")
    read = body.index("ArcsStr := Trim(ExtractJsonValue(Params, 'arcs'));")
    check = body.index("If Not IsFloatStr(RegionArcField(ArcsStr, I, K)) Then")
    refuse = body.index("'BAD_ARCS'")
    assert read < check < refuse < body.index("PCBServer.PreProcess;")
    assert "For K := 0 To 4 Do" in body[read:refuse]
    # a bad field clears the flag, and the flag alone decides the refusal
    assert "ArcsOk := False;" in body[check:refuse]
    gate = body.rindex("If Not ArcsOk Then", 0, refuse)
    assert body[gate:refuse].count("Begin") == 1 and "End" not in body[gate:refuse]


def test_place_region_builds_arcs_from_shape_segments_as_the_footprint_writer_does():
    board = _pcb("PCB_PlaceRegion")
    footprint = _lib("Lib_AddFootprintRegions")
    for line in ("Seg.Kind := ePolySegmentArc;",
                 "Seg.Kind := ePolySegmentLine;",
                 "Region.ShapeSegmentCount := I;",
                 "Region.ShapeSegments[I - 1] := Seg;",
                 "Region.UpdateContourFromShape(True);",
                 "Seg.Angle1 := StrToFloatDef(RegionArcField(ArcsStr, I, 3), 0);",
                 "Seg.Angle2 := StrToFloatDef(RegionArcField(ArcsStr, I, 4), 0);"):
        assert line in board, line
        assert line in footprint, line
    assert "Seg.cx := MilsToCoordF(StrToFloatDef(RegionArcField(ArcsStr, I, 0), 0));" in board
    assert "Seg.Radius := MilsToCoordF(StrToFloatDef(RegionArcField(ArcsStr, I, 2), 0));" in board


def test_without_arcs_the_contour_route_is_unchanged():
    body = _pcb("PCB_PlaceRegion")
    contour = body.index("Region.SetOutlineContour(Contour);")
    arcs = body.index("If ArcsStr <> '' Then", body.index("PCBServer.PreProcess;"))
    shape = body.index("Region.UpdateContourFromShape(True);")
    assert contour < arcs < shape
    assert "Contour := Region.MainContour.Replicate;" in body[:contour]


def test_the_reply_reads_the_shape_back():
    body = _pcb("PCB_PlaceRegion")
    assert "RegionShapeJson(Region, 10000.0, 0, 0, HasArc)" in body
    assert "'\"arc_edges\":' + IntToStr(ArcEdges)" in body
    assert "If Region.ShapeSegments[I].Kind = ePolySegmentArc Then" in body


def test_the_shape_reader_walks_the_segments_and_signs_the_sweep():
    body = _lib("RegionShapeJson")
    assert "For I := 0 To N - 1 Do" in body
    assert "Seg := Region.ShapeSegments(I);" in body
    assert "If Seg.Kind <> ePolySegmentLine Then" in body
    # the vertex is the arc's start: nearer Angle2 means clockwise
    assert "If D2 < D1 Then Sweep := -Sweep;" in body
    assert "FloatToJsonStr((Ix - OffX) / Scale)" in body


def test_the_footprint_reader_gives_each_region_its_shape():
    body = _lib("Lib_GetFootprintPrimitives")
    assert "RegionShapeJson(Region, 10000000.0 / 25.4, XOrg, YOrg, RgnArc)" in body
    assert "',\"shape_mm\":' + RgnShape" in body


def test_the_layout_model_gives_a_region_with_arcs_its_shape():
    body = _pcb("LmRegionShape")
    assert "Shape := RegionShapeJson(Region, 1.0, 0, 0, HasArc);" in body
    assert "If HasArc Then Body := Body + ',\"shape\":' + Shape;" in body


def test_the_shape_reader_builds_before_both_units_use_it():
    src = (_ALTIUM / "Library.pas").read_text(encoding="latin-1")
    assert src.index("Function RegionShapeJson(") < src.index("Function Lib_GetFootprintPrimitives(")


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


# An L-shaped outline with one concave and five convex 1-unit corners,
# as read back from a hand-drawn region.
_L = [[151, 94, 90], [150, 93, 0], [150, 91, -90], [149, 90, 0], [148, 90, 90],
      [147, 89, 0], [147, 88, 90], [148, 87, 0], [152, 87, 90], [153, 88, 0],
      [153, 93, 90], [152, 94, 0]]


def test_arcs_go_out_in_the_footprint_writers_form(monkeypatch):
    _, sent = _call(monkeypatch, layer="TopLayer", points=_L)
    payload, problems = _regions_payload([{"layer": "TopLayer", "points": _L}])
    assert not problems
    footprint_arcs = payload.split(";arcs=")[1]
    assert sent[0][1]["arcs"] == footprint_arcs
    assert sent[0][1]["arcs"].count("/") == 5
    assert sent[0][1]["points"].startswith("151.0,94.0/150.0,93.0/")


def test_a_sweep_can_be_given_on_an_object_point(monkeypatch):
    _, sent = _call(monkeypatch, points=[{"x": 0, "y": 0}, {"x": 10, "y": 0, "sweep": 90},
                                         {"x": 11, "y": 1}, {"x": 0, "y": 1}])
    assert sent[0][1]["arcs"].startswith("2,")


def test_straight_points_send_no_arcs(monkeypatch):
    _, sent = _call(monkeypatch, points=[[0, 0], [100, 0], [50, 80.5]])
    assert "arcs" not in sent[0][1]
    assert sent[0][1]["points"] == "0.0,0.0/100.0,0.0/50.0,80.5"


def test_an_arc_that_cannot_be_drawn_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, points=[[0, 0, 360], [10, 0], [5, 5]])
    assert out["ok"] is False and not sent
    out, sent = _call(monkeypatch, points=[[0, 0, 90], [0, 0], [5, 5]])
    assert out["ok"] is False and not sent
