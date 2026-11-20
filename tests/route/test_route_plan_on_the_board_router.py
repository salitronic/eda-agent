# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""route_plan routes a geometry dict with the board router.

What comes back is placed as it stands, so it must join what it says it
joins, keep the rules it was given, clear every other net by the exact
DRC, turn no sharper than 45 degrees, and keep vias out of a passive's
pads. Pads at odd rotations keep their own shape: a pad turned 329.5
degrees on a 0.5 mm pitch connector is reached between its neighbours.
"""

from __future__ import annotations

import math

import pytest

from eda_agent.design.jobs import _run_route
from eda_agent.layout.route.geometry import board_from_geometry, plan

RULES = {"clearance_mils": 6, "track_width_mils": {"default": 8, "power": 16},
         "via_size_mils": 24, "via_drill_mils": 12}


def _pad(x, y, net, comp, name="1", w=40.0, h=50.0, rot=0.0, layer="TopLayer"):
    return {"x": x, "y": y, "x_size": w, "y_size": h, "rotation": rot, "shape": "rectangle",
            "layer": layer, "net": net, "comp": comp, "name": name}


def _geometry(pads, tracks=(), box=(0, 0, 1200, 800)):
    return {"bbox": dict(zip(("x1", "y1", "x2", "y2"), box)), "pads": list(pads),
            "tracks": list(tracks)}


def _bends(tracks):
    """Turn, degrees, at every joint of two tracks of one net and layer."""
    out = []
    for a in tracks:
        for b in tracks:
            if a is b or a["net_name"] != b["net_name"] or a["layer"] != b["layer"]:
                continue
            if math.hypot(a["x2"] - b["x1"], a["y2"] - b["y1"]) < 1e-2:
                u = (a["x2"] - a["x1"], a["y2"] - a["y1"])
                v = (b["x2"] - b["x1"], b["y2"] - b["y1"])
                lu, lv = math.hypot(*u), math.hypot(*v)
                if lu and lv:
                    c = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (lu * lv)))
                    out.append(math.degrees(math.acos(c)))
    return out


def test_two_nets_route_clean_at_the_rules_widths():
    pads = [_pad(100, 100, "A", "J1"), _pad(1000, 600, "A", "J2"),
            _pad(100, 600, "VCC", "J1", "2"), _pad(1000, 100, "VCC", "J2", "2")]
    r = plan(_geometry(pads), RULES, net_classes={"VCC": "power"})
    assert r["ok"] and r["summary"]["completion"] == 1.0, r["summary"]
    assert r["validation"]["violation_count"] == 0, r["validation"]
    assert {t["width"] for t in r["tracks"] if t["net_name"] == "A"} == {8.0}
    assert {t["width"] for t in r["tracks"] if t["net_name"] == "VCC"} == {16.0}
    assert all((v["size"], v["hole_size"]) == (24.0, 12.0) for v in r["vias"])


def test_no_turn_is_sharper_than_45_degrees():
    pads = [_pad(100, 100, "A", "U1"), _pad(1050, 400, "A", "U2"),
            _pad(150, 700, "B", "U1", "2"), _pad(900, 120, "B", "U2", "2")]
    r = plan(_geometry(pads), RULES)
    assert r["summary"]["completion"] == 1.0
    assert all(b <= 45.0 + 1e-6 for b in _bends(r["tracks"])), _bends(r["tracks"])


def test_only_the_nets_asked_for_are_routed():
    pads = [_pad(100, 100, "A", "J1"), _pad(1000, 600, "A", "J2"),
            _pad(100, 600, "B", "J1", "2"), _pad(1000, 100, "B", "J2", "2")]
    r = plan(_geometry(pads), RULES, nets=["A", "NOPE"])
    assert {t["net_name"] for t in r["tracks"]} == {"A"}
    assert r["unknown_nets"] == ["NOPE"]
    assert set(r["nets"]) == {"A"}


def test_a_wall_is_crossed_on_the_other_layer_and_no_via_lands_in_a_pad():
    # A track of another net from edge to edge: A has to change layer.
    pads = [_pad(100, 400, "A", "R1", w=30, h=30), _pad(1100, 400, "A", "R2", w=30, h=30)]
    wall = [{"x1": 600, "y1": 0, "x2": 600, "y2": 800, "width": 10, "layer": "TopLayer",
             "net": "X"}]
    r = plan(_geometry(pads, wall), RULES)
    assert r["summary"]["completion"] == 1.0 and r["vias"], r
    assert r["validation"]["violation_count"] == 0
    for v in r["vias"]:
        for p in pads:
            assert math.hypot(v["x"] - p["x"], v["y"] - p["y"]) > 15 + 12, (v, p)


def test_pads_turned_329_5_degrees_on_a_fine_pitch_keep_their_shape():
    pitch, w, h = 19.685, 11.811, 45.276
    a = math.radians(329.5)
    ux, uy = math.cos(a), math.sin(a)
    pads = []
    for i in range(6):
        x, y = 400 + i * pitch * ux, 400 + i * pitch * uy
        pads.append(_pad(x, y, f"S{i}", "J1", str(i + 1), w=w, h=h, rot=329.5))
    board = board_from_geometry(_geometry(pads), RULES)
    assert all(p.rotation == 329.5 and p.copper[0].w == w for p in board.pads)
    # Each pin's net runs to a pad well away from the row.
    far = [_pad(400 + i * 120, 750, f"S{i}", "J2", str(i + 1), w=30, h=30) for i in range(6)]
    rules = dict(RULES, clearance_mils=4, track_width_mils=4)
    r = plan(_geometry(pads + far), rules)
    assert r["summary"]["completion"] == 1.0, r["nets"]
    assert r["validation"]["violation_count"] == 0, r["validation"]


def test_malformed_input_is_refused():
    with pytest.raises(ValueError):
        plan({"pads": [_pad(0, 0, "A", "J1")]}, {"via_size_mils": 10, "via_drill_mils": 20})
    with pytest.raises(ValueError):
        board_from_geometry({}, RULES)


def test_the_route_job_takes_route_plans_arguments_only():
    pads = [_pad(100, 100, "A", "J1"), _pad(700, 500, "A", "J2")]
    r = _run_route({"geometry": _geometry(pads), "rules": RULES})
    assert r["summary"]["completion"] == 1.0
    with pytest.raises(ValueError, match="grid_pitch_mils"):
        _run_route({"geometry": _geometry(pads), "grid_pitch_mils": 5})
