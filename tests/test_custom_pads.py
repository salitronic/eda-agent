# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_add_footprint_pads makes a pad whose copper is an outline.

A pad with shape "custom" goes to library.add_footprint_custom_pads with its
designator, layer, anchor and outline, the outline encoded as a region's
(points, and arcs for an edge given a sweep). The bridge draws the outline as
a region and links it to the pad as its custom shape, Altium's own route, and
reads back whether the pad holds a custom shape.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import library as lib_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_LIB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"
_L = [[0, 0], [80, 0], [80, 20], [20, 20], [20, 60], [0, 60]]


def _fields(op: str) -> dict[str, str]:
    return dict(f.split("=", 1) for f in op.split(";"))


def test_the_anchor_defaults_to_the_middle_of_the_outline():
    payload, problems = lib_mod._custom_pads_payload(
        [{"designator": "1", "shape": "custom", "outline": _L}])
    assert problems == []
    f = _fields(payload)
    assert (f["designator"], f["layer"], f["x"], f["y"]) == ("1", "TopLayer", "40", "30")
    assert f["points"] == "0,0/80,0/80,20/20,20/20,60/0,60"
    assert "arcs" not in f


def test_an_arc_edge_goes_out_as_a_region_arc():
    payload, _ = lib_mod._custom_pads_payload([{"designator": "3", "x": 0, "y": 0,
        "outline": [[0, 0], [100, 0], [100, 100, 180], [0, 100]]}])
    f = _fields(payload)
    assert f["arcs"].startswith("3,50,100,50,")


@pytest.mark.parametrize("pad, says", [
    ({"outline": _L}, "no designator"),
    ({"designator": "1", "outline": [[0, 0], [1, 1]]}, "three points"),
    ({"designator": "1", "outline": [[0, "a"], [1, 1], [2, 0]]}, "numbers"),
    ({"designator": "1", "outline": _L, "hole_size": 20}, "SMD"),
])
def test_a_wrong_pad_is_named(pad, says):
    payload, problems = lib_mod._custom_pads_payload([pad])
    assert payload == "" and says in problems[0]


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params or {}))
        return {"added": 1}


@pytest.fixture()
def tools(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(lib_mod, "get_bridge", lambda: rec)
    monkeypatch.setattr(lib_mod, "_unchecked_lands", lambda *a, **k: None)
    reg = ToolRegistry()
    lib_mod.register_library_tools(reg)
    return {t.name: t.fn for t in asyncio.run(reg.list_tools())}, rec


def test_custom_pads_go_to_their_own_command_and_others_as_before(tools):
    found, rec = tools
    asyncio.run(found["lib_add_footprint_pads"](pads=[
        {"designator": "1", "shape": "custom", "outline": _L},
        {"designator": "2", "x": 100, "y": 0, "shape": "rectangular"}]))
    commands = [c for c, _ in rec.sent]
    assert commands == ["library.add_footprint_custom_pads", "library.add_footprint_pads"]
    assert "custom" not in rec.sent[1][1]["pads"]


def test_a_bad_custom_pad_sends_nothing(tools):
    found, rec = tools
    out = asyncio.run(found["lib_add_footprint_pads"](pads=[
        {"designator": "1", "shape": "custom", "outline": [[0, 0]]},
        {"designator": "2", "x": 100, "y": 0}]))
    assert out["ok"] is False and rec.sent == []


@pytest.fixture(scope="module")
def fns() -> dict[str, str]:
    return load(_LIB_PAS, minimum=50)


def test_the_outline_becomes_the_pads_shape_through_altiums_link(fns):
    body = fns["Lib_AddFootprintCustomPads"]
    assert "LibOutlineRegion(Layer, GetBatchField(Op, 'points')" in body
    added = body.index("Footprint.AddPCBObject(Pad);")
    assert added < body.index("Pad.LinkCustomShape(Region);")
    assert "Pad.HasCustomShapes" in body
    assert "Footprint.AddPCBObject(Region)" not in body


def test_the_region_is_built_as_a_footprint_region_is(fns):
    body = fns["LibOutlineRegion"]
    assert "RegionPointsSplit(PointsStr, Xs, Ys);" in body
    assert "Seg.Kind := ePolySegmentArc;" in body
    assert "Region.SetOutlineContour(Contour);" in body
