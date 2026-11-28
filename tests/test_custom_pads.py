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


def test_the_anchor_defaults_to_a_point_inside_the_copper():
    """The L's box middle (40, 30) is in its notch, off the copper; the
    anchor, where a track is routed to, goes deep inside instead."""
    payload, problems = lib_mod._custom_pads_payload(
        [{"designator": "1", "shape": "custom", "outline": _L}])
    assert problems == []
    f = _fields(payload)
    assert (f["designator"], f["layer"]) == ("1", "TopLayer")
    poly = lib_mod._outline_polygon(_L)
    assert lib_mod._depth(poly, float(f["x"]), float(f["y"])) > 9.5   # legs are 20 wide
    assert f["points"] == "0,0/80,0/80,20/20,20/20,60/0,60"
    assert "arcs" not in f


def test_a_u_gets_its_anchor_in_a_leg_and_a_square_keeps_its_middle():
    u = [[0, 0], [6, 0], [6, 6], [4, 6], [4, 2], [2, 2], [2, 6], [0, 6]]
    f = _fields(lib_mod._custom_pads_payload([{"designator": "1", "outline": u}])[0])
    assert lib_mod._depth(lib_mod._outline_polygon(u), float(f["x"]), float(f["y"])) > 0.9
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    f = _fields(lib_mod._custom_pads_payload([{"designator": "2", "outline": square}])[0])
    assert (f["x"], f["y"]) == ("5", "5")


def test_a_thru_hole_custom_pad_sends_its_hole():
    hexagon = [[1.5, 0], [0.75, 1.3], [-0.75, 1.3], [-1.5, 0], [-0.75, -1.3], [0.75, -1.3]]
    payload, problems = lib_mod._custom_pads_payload(
        [{"designator": "1", "outline": hexagon, "x": 0, "y": 0, "hole_size": 1.0}])
    assert problems == [] and _fields(payload)["hole_size"] == "1"


def test_the_script_puts_a_holed_custom_pad_on_the_multi_layer(fns):
    body = fns["Lib_AddFootprintCustomPads"]
    assert "GetBatchField(Op, 'hole_size')" in body
    assert "Pad.Layer := eMultiLayer" in body and "Pad.HoleSize := CoordFromUnits" in body


def test_an_anchor_given_outside_the_copper_is_refused():
    payload, problems = lib_mod._custom_pads_payload(
        [{"designator": "1", "outline": _L, "x": 40, "y": 30}])
    assert payload == "" and "outside the outline" in problems[0]


def test_an_arc_edge_goes_out_as_a_region_arc():
    payload, _ = lib_mod._custom_pads_payload([{"designator": "3", "x": 0, "y": 0,
        "outline": [[0, 0], [100, 0], [100, 100, 180], [0, 100]]}])
    f = _fields(payload)
    assert f["arcs"].startswith("3,50,100,50,")


@pytest.mark.parametrize("pad, says", [
    ({"outline": _L}, "no designator"),
    ({"designator": "1", "outline": [[0, 0], [1, 1]]}, "three points"),
    ({"designator": "1", "outline": [[0, "a"], [1, 1], [2, 0]]}, "numbers"),
    ({"designator": "1", "outline": _L, "hole_size": 30}, "inside the copper"),
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


def test_an_outline_is_rounded_by_kind_of_corner():
    """An L walked counterclockwise: five convex corners take corner_radius
    and turn left (+90), the one inside corner takes inside_radius and
    turns right (-90)."""
    out, problem = lib_mod.round_outline([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]],
                                         0.1, 0.2)
    assert problem == ""
    sweeps = [round(p[2], 6) for p in out if len(p) > 2]
    assert sweeps == [90, 90, 90, -90, 90, 90]
    # The inside corner at (1, 1): tangent points 0.2 along each edge.
    k = next(i for i, p in enumerate(out) if len(p) > 2 and p[2] < 0)
    assert [round(v, 9) for v in out[k][:2]] == [1.2, 1.0]
    assert [round(v, 9) for v in out[k + 1]] == [1.0, 1.2]


def test_a_clockwise_outline_rounds_the_same_corners():
    ccw = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]]
    out, _ = lib_mod.round_outline(list(reversed(ccw)), 0.1, 0.2)
    sweeps = sorted(round(p[2]) for p in out if len(p) > 2)
    assert sweeps == [-90, -90, -90, -90, -90, 90]


def test_an_edge_too_short_for_its_fillets_names_what_fits():
    _, problem = lib_mod.round_outline([[0, 0], [0.15, 0], [0.15, 1], [0, 1]], 0.1)
    assert "edge 0" in problem and "corner_radius 0.075 at most" in problem


def test_a_rounded_custom_pad_goes_out_with_its_fillet_arcs():
    f = _fields(lib_mod._custom_pads_payload([{
        "designator": "1", "shape": "custom", "outline": _L,
        "corner_radius_length": 4, "inside_radius": 6}])[0])
    assert len(f["arcs"].split("/")) == 6
    assert len(f["points"].split("/")) == 12


def test_rounding_an_outline_that_already_has_arcs_is_refused():
    _, problems = lib_mod._custom_pads_payload([{
        "designator": "1", "outline": [[0, 0], [10, 0, 90], [10, 10], [0, 10]],
        "inside_radius": 1}])
    assert problems and "plain corners" in problems[0]
