# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""3D models take cuts, rounded and tapered bodies, flush marks and
repeated sub-assemblies, and come with a picture to review.

A model built only from added boxes cannot show a castellation, a notch,
a hollow shell, a moulded body's draft or a lid's rounded corners, and a
mark drawn as a plate stands proud of the face or wraps over its edge.
"""

from __future__ import annotations

import asyncio
import importlib.util
import math

import pytest

from eda_agent.design import models3d as m
from eda_agent.design.model_drawing import check_model_dims
from eda_agent.tools import library as lib_tools

SRC = "invented drawing, figure 1"
BODY = {"kind": "box", "role": "body", "x0": -1, "y0": -1, "z0": 0, "x1": 1, "y1": 1, "z1": 0.5}


def _plan(*solids, groups=None):
    plan, problems = m.plan_from_solids(list(solids), SRC, groups)
    assert plan is not None, problems
    return plan


def _problems(*solids, groups=None):
    plan, problems = m.plan_from_solids(list(solids), SRC, groups)
    assert plan is None
    return " | ".join(problems)


def test_new_kinds_and_roles_are_read():
    plan = _plan(
        {**BODY, "role": "substrate", "corner_r": 0.2, "edge_r": 0.05},
        {"kind": "extrude", "role": "nickel", "outline": [[-0.8, -0.8], [0.8, -0.8], [0.8, 0.8], [-0.8, 0.8]],
         "z0": 0.5, "z1": 0.7, "taper": 30},
        {"kind": "text", "role": "laser", "text": "AB", "size": 0.3, "cx": 0, "cy": 0, "z1": 0.7},
        {"kind": "dot", "cx": -0.5, "cy": 0.5, "r": 0.05, "z1": 0.7},
        {"kind": "box", "role": "cut", "x0": 0.9, "y0": -0.1, "z0": 0, "x1": 1.1, "y1": 0.1, "z1": 0.5})
    assert [s.kind for s in plan.solids] == ["box", "extrude", "text", "dot", "box"]
    assert plan.solids[3].role == "ink"           # a mark's default colour
    assert plan.height == pytest.approx(0.7)      # the cut adds no height


@pytest.mark.parametrize("bad,needle", [
    ({**BODY, "corner_r": 1.0}, "half the narrower side"),
    ({**BODY, "edge_r": 0.5}, "under the height"),
    ({**BODY, "taper": 60}, "taper is degrees"),
    ({**BODY, "taper": 45, "z1": 1.2}, "closes the top"),
    ({"kind": "cylinder", "role": "metal", "cx": 0, "cy": 0, "r": 1, "z0": 0, "z1": 1, "taper": 5},
     "for a box or an extrude"),
    ({"kind": "text", "role": "body", "text": "X", "size": 1, "cx": 0, "cy": 0, "z1": 1}, "a mark's role"),
    ({**BODY, "role": "ink"}, "colours of a text or dot mark"),
    ({**BODY, "cut_from": ["x"]}, "cut_from is for a solid of role cut"),
    ({"kind": "extrude", "outline": [[0, 0], [1, 0]], "z0": 0, "z1": 1}, "three points"),
    ({**BODY, "clip_z0": 0}, "for a sphere"),
])
def test_a_shape_that_cannot_be_built_is_refused(bad, needle):
    assert needle in _problems(bad)


def test_a_cut_names_its_targets_or_is_refused():
    assert "which no solid is named" in _problems(
        BODY, {**BODY, "role": "cut", "cut_from": ["shell"]})
    assert "nothing is left to draw" in _problems({**BODY, "role": "cut"})


def test_an_instance_places_its_group_and_keeps_its_cuts_its_own():
    group = [{**BODY, "name": "shell", "dx": 0.5},
             {**BODY, "role": "cut", "cut_from": ["shell"], "x0": -0.2, "x1": 0.2}]
    plan = _plan({"kind": "instance", "group": "g", "dx": 10},
                 {"kind": "instance", "group": "g", "dx": -10, "rot": 90},
                 groups={"g": group})
    shells = [s for s in plan.solids if s.role != "cut"]
    assert [s.name for s in shells] == ["shell@1", "shell@2"]
    assert [s.cut_from for s in plan.solids if s.role == "cut"] == [["shell@1"], ["shell@2"]]
    first = m.solid_bounds(shells[0])
    assert first[:2] == pytest.approx((9.5, 11.5))         # its own dx, then the instance's
    turned = m.solid_bounds(shells[1])
    assert turned[2:4] == pytest.approx((-0.5, 1.5)) and turned[:2] == pytest.approx((-11, -9))
    assert "no group 'h'" in _problems({"kind": "instance", "group": "h"}, groups={"g": group})


def test_a_seated_ball_is_cut_flat_at_the_board_and_the_underside():
    ball = m.seated_ball(1.0, 2.0, 0.5, 0.4)
    assert (ball.cz, ball.r, ball.clip_z0, ball.clip_z1) == (0.2, 0.25, 0.0, 0.4)
    assert m.solid_bounds(ball)[4:] == pytest.approx((0.0, 0.4))


def test_a_package_bga_seats_its_balls_too():
    from tests.design import _landpattern_cases as cases
    from eda_agent.design.landpattern import build_footprint

    spec = cases.bga80()
    plan = m.plan_model(spec, build_footprint(spec))
    balls = [s for s in plan.solids if s.role == "ball"]
    assert len(balls) == 80
    assert all(m.solid_bounds(b)[4] >= 0 for b in balls), "a ball reaches below the board"


def test_a_chamfered_land_becomes_a_chamfered_plate():
    lands = [{"name": "1", "x": 0, "y": 0, "w": 0.4, "h": 0.3, "shape": "rect"}]
    plates, problems = m.terminal_solids(lands, "plate", 0.02,
                                         chamfered={"1": {"size": 0.1, "corners": ["top_left"]}})
    assert not problems and plates[0].kind == "extrude"
    pts = [(round(x, 6), round(y, 6)) for x, y in plates[0].outline]
    assert pts == [(-0.2, -0.15), (0.2, -0.15), (0.2, 0.15), (-0.1, 0.15), (-0.2, 0.05)]
    _, bad = m.terminal_solids(lands, "plate", 0.02, chamfered={"1": {"size": 0.5, "corner": "top_left"}})
    assert "below the land's width" in bad[0]


def test_the_drawing_check_measures_placed_solids_without_the_cuts():
    plan = _plan({**BODY, "dx": 5}, {**BODY, "role": "cut", "x0": -3, "x1": 3})
    rows = check_model_dims(plan.solids, {"body_x": 2, "body_y": 2, "height": 0.5})
    assert rows["ok"], rows


def test_only_cadquery_draws_the_new_shapes():
    plan = _plan({**BODY, "corner_r": 0.1}, {"kind": "dot", "cx": 0, "cy": 0, "r": 0.1, "z1": 0.5})
    assert m._cadquery_only(plan) == ["dot", "rounded or tapered boxes"]
    out = m.write_step(plan, "unused.step", writers=["freecad"])
    assert not out["ok"] and "does not draw" in out["tried"][0]["error"]


def test_compact_pad_rows():
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
            return {"name": "BGA", "pad_count": 2, "pads": [
                {"name": "A1", "x_mm": -0.4, "y_mm": 0.4, "w_mm": 0.3, "shape": "round"},
                {"name": "A2", "x_mm": 0.4, "y_mm": 0.4, "w_mm": 0.3, "shape": "round"}]}

    lib_tools.get_bridge = lambda: _Bridge()
    try:
        lib_tools.register_library_tools(_Mcp())
        out = asyncio.run(tools["lib_get_pad_geometry"]("BGA", compact=True))
    finally:
        from eda_agent.bridge import get_bridge
        lib_tools.get_bridge = get_bridge
    assert out["columns"] == ["name", "x_mm", "y_mm"]
    assert out["pads"] == [["A1", -0.4, 0.4], ["A2", 0.4, 0.4]] and out["pad_count"] == 2


CADQUERY = importlib.util.find_spec("cadquery") is not None


@pytest.mark.skipif(not CADQUERY, reason="CadQuery is optional")
def test_cuts_marks_and_tapers_build(tmp_path):
    plan = _plan(
        {**BODY, "name": "body", "corner_r": 0.1},
        {"kind": "box", "role": "nickel", "x0": -0.8, "y0": -0.8, "z0": 0.5, "x1": 0.8, "y1": 0.8,
         "z1": 0.7, "taper": 30},
        {"kind": "dot", "cx": -0.5, "cy": 0.5, "r": 0.05, "z1": 0.7},
        {"kind": "cylinder", "role": "cut", "cut_from": ["body"], "cx": 1, "cy": 0, "r": 0.2, "z0": 0, "z1": 0.5})
    shapes = m.build_shapes(plan)
    body = shapes[0][1].val()
    assert body.Volume() == pytest.approx(2 * 2 * 0.5 - (4 - math.pi) * 0.01 * 0.5
                                          - math.pi * 0.04 * 0.5 / 2, rel=1e-3)
    lid = shapes[1][1].val().BoundingBox()
    top = shapes[1][1].faces(">Z").val().BoundingBox()
    assert lid.xlen == pytest.approx(1.6) and top.xlen == pytest.approx(1.6 - 2 * 0.2 * math.tan(math.radians(30)), rel=1e-3)
    dot = shapes[2][1].val().BoundingBox()
    assert dot.zmax == pytest.approx(0.7) and dot.zmin == pytest.approx(0.7 - m.MARK_DEPTH)
    lid_volume = shapes[1][1].val().Volume()
    full = _plan({"kind": "box", "role": "nickel", "x0": -0.8, "y0": -0.8, "z0": 0.5,
                  "x1": 0.8, "y1": 0.8, "z1": 0.7, "taper": 30})
    assert lid_volume < m.build_shapes(full)[0][1].val().Volume()    # the dot's pocket


@pytest.mark.skipif(not CADQUERY, reason="CadQuery is optional")
@pytest.mark.parametrize("mark,needle", [
    ({"kind": "dot", "cx": 0.95, "cy": 0, "r": 0.2, "z1": 0.5}, "reaches past the face"),
    ({"kind": "dot", "cx": 0, "cy": 0, "r": 0.2, "z1": 0.7}, "not on a top face"),
])
def test_a_mark_off_its_face_is_refused(mark, needle):
    with pytest.raises(ValueError, match=needle):
        m.build_shapes(_plan(BODY, mark))
