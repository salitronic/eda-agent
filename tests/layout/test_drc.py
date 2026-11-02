# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The DRC referee on boards small enough to check by hand.

Every placer and router result is judged by ``run_drc``, so each rule it
applies is pinned here on a board where the right answer is obvious: a
missing track is one missing connection, a pad 5 mil from foreign copper
under a 6 mil rule is one violation, and so on.
"""

from __future__ import annotations

import math
import random

import pytest

from eda_agent.layout import geom
from eda_agent.layout.drc import BIG_POLY, run_drc
from eda_agent.layout.model import (Layer, LayoutBoard, Pad, PadCopper, Region,
                                    Rule, Track, Via)


def _rule(gap: float) -> Rule:
    return Rule(name="Clearance", kind="0", scope1="All", scope2="All",
                priority=1, values={"gap": gap},
                descriptor=f"Clearance Constraint (Gap={gap}mil) (All),(All)")


def _board(*, gap=6.0, layers=("TopLayer", "BottomLayer"), planes=()) -> LayoutBoard:
    stack = [Layer(name=n, kind="signal", order=i) for i, n in enumerate(layers)]
    for name, net in planes:
        stack.append(Layer(name=name, kind="plane", order=len(stack), plane_net=net))
    stack.sort(key=lambda l: l.order)
    return LayoutBoard(name="t", layers=stack, rules=[_rule(gap)])


def _smd(comp, name, x, y, net, size=10.0, layer="TopLayer") -> Pad:
    return Pad(comp=comp, name=name, x=x, y=y, net=net,
               copper=[PadCopper(layer, "rect", size, size)])


def _th(comp, name, x, y, net, size=60.0, hole=35.0, layers=("TopLayer", "BottomLayer")) -> Pad:
    return Pad(comp=comp, name=name, x=x, y=y, net=net, hole=hole,
               copper=[PadCopper(l, "round", size, size) for l in layers])


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

def test_a_track_joins_two_pads_and_without_it_one_connection_is_missing():
    b = _board()
    b.pads = [_smd("R1", "1", 0, 0, "A"), _smd("R2", "1", 100, 0, "A")]
    b.tracks = [Track("TopLayer", 0, 0, 100, 0, 8, "A")]
    rep = run_drc(b)
    assert rep.unrouted == {} and rep.completion == 1.0

    b.tracks = []
    rep = run_drc(b)
    assert rep.unrouted == {"A": 1}
    assert rep.connections_needed == 1 and rep.completion == 0.0


def test_pads_with_one_name_are_still_separate_pads():
    # Several mounting pads of one footprint can share a name. Keyed by
    # name they collapsed into one pad and read as joined.
    b = _board()
    b.pads = [_smd("J1", "SCREW", 0, 0, "GND"), _smd("J1", "SCREW", 500, 0, "GND")]
    assert run_drc(b).unrouted == {"GND": 1}


def test_a_via_joins_the_copper_on_every_layer_it_spans():
    b = _board()
    b.pads = [_smd("R1", "1", 0, 0, "A"),
              _smd("R2", "1", 200, 0, "A", layer="BottomLayer")]
    b.tracks = [Track("TopLayer", 0, 0, 100, 0, 8, "A"),
                Track("BottomLayer", 100, 0, 200, 0, 8, "A")]
    b.vias = [Via(100, 0, 20, 10, "TopLayer", "BottomLayer", "A")]
    assert run_drc(b).unrouted == {}
    b.vias = []
    assert run_drc(b).unrouted == {"A": 1}


def test_a_pour_joins_the_pads_it_covers():
    b = _board()
    b.pads = [_smd("C1", "2", 50, 50, "GND"), _smd("C2", "2", 350, 50, "GND")]
    b.regions = [Region("TopLayer", _square(0, 0, 400, 100), [], "GND", "pour")]
    assert run_drc(b).unrouted == {}


def test_a_split_plane_joins_a_through_hole_pad_of_its_net_and_not_a_surface_pad():
    b = _board(layers=("TopLayer", "BottomLayer"), planes=[("InternalPlane1", "")])
    b.layers = [Layer("TopLayer", "signal", 0), Layer("InternalPlane1", "plane", 1),
                Layer("BottomLayer", "signal", 2)]
    split = Region("InternalPlane1", _square(0, 0, 1000, 1000), [], "VCC", "plane")
    b.regions = [split]
    b.pads = [_th("J1", "1", 100, 100, "VCC"), _th("J2", "1", 900, 900, "VCC")]
    assert run_drc(b).unrouted == {}, "the split should join both barrels"

    b.pads = [_smd("U1", "1", 100, 100, "VCC"), _smd("U2", "1", 900, 900, "VCC")]
    assert run_drc(b).unrouted == {"VCC": 1}, "a surface pad never reaches a plane"


# ---------------------------------------------------------------------------
# Clearance
# ---------------------------------------------------------------------------

def test_foreign_copper_closer_than_the_rule_is_one_violation():
    b = _board(gap=6.0)
    # Pad edge at x=5; track edge at x=10: a 5 mil gap.
    b.pads = [_smd("R1", "1", 0, 0, "A")]
    b.tracks = [Track("TopLayer", 14, -50, 14, 50, 8, "B")]
    rep = run_drc(b)
    assert len(rep.violations) == 1
    v = rep.violations[0]
    assert v.gap == pytest.approx(5.0) and v.required == 6.0

    b.rules = [_rule(4.0)]
    assert run_drc(b).violations == []


def test_a_gap_equal_to_the_rule_is_not_a_violation():
    b = _board(gap=5.0)
    b.pads = [_smd("R1", "1", 0, 0, "A")]
    b.tracks = [Track("TopLayer", 14, -50, 14, 50, 8, "B")]
    assert run_drc(b).violations == []


def test_pads_of_one_footprint_are_checked_but_its_own_copper_is_not():
    b = _board(gap=6.0)
    # Two pads of one part 4 mil apart: a fine-pitch violation that is
    # real, and exactly where one lives.
    b.pads = [_smd("U1", "1", 0, 0, "A"), _smd("U1", "2", 14, 0, "B")]
    assert len(run_drc(b).violations) == 1

    # The part's own copper track touching its own pad is land pattern.
    b.pads = [_smd("U1", "1", 0, 0, "A")]
    b.tracks = [Track("TopLayer", 0, 0, 30, 0, 4, "", comp="U1")]
    assert run_drc(b).violations == []


def test_a_foreign_pad_in_a_pour_hole_is_measured_to_the_hole_edge():
    b = _board(gap=6.0)
    b.pads = [_smd("R1", "1", 200, 50, "SIG")]      # copper 195..205
    hole = _square(190, 40, 210, 60)                # 5 mil round the pad
    b.regions = [Region("TopLayer", _square(0, 0, 400, 100), [hole], "GND", "pour")]
    rep = run_drc(b)
    assert len(rep.violations) == 1 and rep.violations[0].gap == pytest.approx(5.0)

    b.regions[0].holes = [_square(188, 38, 212, 62)]  # 7 mil
    assert run_drc(b).violations == []


def test_copper_drawn_on_a_plane_layer_is_a_void_not_copper():
    b = _board()
    b.layers = [Layer("TopLayer", "signal", 0), Layer("InternalPlane1", "plane", 1),
                Layer("BottomLayer", "signal", 2)]
    # A split line between two splits, crossing a via of a third net.
    b.tracks = [Track("InternalPlane1", 0, 0, 1000, 0, 20, "")]
    b.vias = [Via(500, 0, 20, 10, "TopLayer", "BottomLayer", "SIG")]
    assert run_drc(b).violations == []


def test_a_removed_via_pad_is_measured_from_its_barrel():
    b = _board(gap=4.0, layers=("TopLayer", "MidLayer1", "BottomLayer"))
    b.vias = [Via(0, 0, 16, 8, "TopLayer", "BottomLayer", "A")]
    b.tracks = [Track("MidLayer1", 10, -50, 10, 50, 4, "B")]  # edge at x=8
    assert len(run_drc(b).violations) == 1, "full pad: 0 mil gap"
    b.vias[0].layer_diameters = {"MidLayer1": 8.0}
    assert run_drc(b).violations == [], "barrel only: a 4 mil gap"


# ---------------------------------------------------------------------------
# The polygon index agrees with the plain walk
# ---------------------------------------------------------------------------

def _star(cx, cy, n, r0, r1, rng):
    return [(cx + (r0 + (r1 - r0) * rng.random()) * math.cos(2 * math.pi * k / n),
             cy + (r0 + (r1 - r0) * rng.random()) * math.sin(2 * math.pi * k / n))
            for k in range(n)]


def test_the_polygon_index_gives_the_plain_walks_distance_within_the_cutoff():
    rng = random.Random(7)
    outer = _star(0, 0, 300, 800, 1000, rng)
    hole = _star(0, 0, 60, 200, 300, rng)
    big = geom.polygon(outer, [hole])
    assert len(outer) + len(hole) > BIG_POLY
    idx = geom.PolyIndex(big)
    cutoff = 40.0
    probes = []
    for _ in range(300):
        x, y = rng.uniform(-1100, 1100), rng.uniform(-1100, 1100)
        kind = rng.choice(["point", "segment", "poly"])
        if kind == "point":
            probes.append(geom.circle(x, y, 10))
        elif kind == "segment":
            a = rng.uniform(0, 2 * math.pi)
            L = rng.uniform(5, 400)
            probes.append(geom.capsule(x, y, x + L * math.cos(a), y + L * math.sin(a), 8))
        else:
            probes.append(geom.polygon(_star(x, y, 8, 10, 60, rng)))
    near = 0
    for p in probes:
        plain = geom.core_distance(big, p)
        fast = idx.core_distance(p, cutoff)
        if plain <= cutoff:
            near += 1
            assert fast == pytest.approx(plain, abs=1e-9), (p.kind, plain, fast)
        else:
            assert fast > cutoff, (p.kind, plain, fast)
    assert near > 60, "too few probes landed near the edges to test anything"


def test_the_chords_of_one_arc_are_not_checked_against_each_other():
    from eda_agent.layout.model import Arc
    b = _board(gap=6.0)
    b.arcs = [Arc("TopLayer", 100, 100, 30, 0, 180, 8.0, "")]
    assert run_drc(b).violations == []


def test_netless_copper_touching_netless_copper_is_not_a_violation():
    b = _board(gap=6.0)
    b.tracks = [Track("TopLayer", 0, 0, 100, 0, 8, ""), Track("TopLayer", 50, -20, 50, 20, 8, "")]
    assert run_drc(b).violations == []
    # ...but netless copper against a net still is.
    b.tracks.append(Track("TopLayer", 0, 8, 100, 8, 4, "A"))
    assert run_drc(b).violations


def test_a_net_ties_pads_and_the_copper_of_its_nets_are_not_a_short():
    # Two pads of one part, on two nets, drawn touching: a net tie.
    b = _board(gap=6.0)
    b.pads = [_smd("NT1", "1", 100, 100, "A", size=12), _smd("NT1", "2", 108, 100, "B", size=12),
              _smd("R1", "1", 300, 100, "B")]
    b.tracks = [Track("TopLayer", 108, 100, 300, 100, 10, "B")]
    assert run_drc(b).violations == []
    # Two DIFFERENT parts touching on two nets is still a short.
    b.pads[1] = _smd("R2", "2", 108, 100, "B", size=12)
    assert run_drc(b).violations


def test_copper_either_side_of_a_hash_cell_boundary_is_still_compared():
    # The pairs to check come from a spatial hash of 72 mil cells here
    # (the typical item, 12 mil, plus twice the 30 mil clearance). The
    # track lies in the cell from 144 to 216 and the pad in the one from
    # 216 to 288, 22.7 mil apart where 30 is required.
    b = _board(gap=30.0)
    b.pads = [_smd("R1", "1", 100, 200, "A", size=12.0), _smd("R2", "1", 900, 200, "A", size=12.0),
              _smd("U1", "1", 500, 232.7, "B", size=12.0)]
    b.tracks = [Track("TopLayer", 100, 200, 900, 200, 8.0, "A")]
    v = run_drc(b).violations
    assert len(v) == 1 and abs(v[0].gap - 22.7) < 0.01


def _holes_board(gap: float) -> LayoutBoard:
    b = _board()
    b.rules.append(Rule("HoleToHoleClearance", "9", "All", "All", 1, True, {},
                        f"Hole To Hole Clearance (Allow Stacked Micro Vias=No) (Gap={gap}mil) "
                        "(All),(All)"))
    return b


def test_two_vias_of_one_net_closer_than_the_hole_rule_are_a_violation():
    from eda_agent.layout.drc import hole_gaps, verdict
    b = _holes_board(10.0)
    # Copper touching (one net), holes 8 mil apart edge to edge.
    b.vias = [Via(100, 100, 20, 10, "TopLayer", "BottomLayer", "A"),
              Via(118, 100, 20, 10, "TopLayer", "BottomLayer", "A")]
    found = hole_gaps(b)
    assert len(found) == 1 and abs(found[0].gap - 8.0) < 1e-6 and found[0].required == 10.0
    v = verdict(b)
    assert v["status"] == "fail" and v["counts"]["hole_to_hole"] == 1
    assert "hole_to_hole" not in v["not_checked"]
    b.vias[1] = Via(121, 100, 20, 10, "TopLayer", "BottomLayer", "A")
    assert hole_gaps(b) == []


def test_a_slot_is_judged_on_its_whole_length():
    from eda_agent.layout.drc import hole_gaps
    b = _holes_board(10.0)
    slot = Pad("J1", "1", 100, 100, net="A", hole=20, hole_type="slot", hole_width=80,
               copper=[PadCopper(l, "round", 40, 40) for l in ("TopLayer", "BottomLayer")])
    b.pads = [slot]
    # 50 mil off the slot's centre along its length: 35 mil clear of a round
    # hole there, 5 mil from the slot's end.
    b.vias = [Via(150, 100, 20, 10, "TopLayer", "BottomLayer", "B")]
    assert len(hole_gaps(b)) == 1
