# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Isolation barriers in the brief: the router keeps the distance between
the sides, and the review measures creepage along the surface (round any
slot) on the outer layers and clearance through air on every layer.
"""

from __future__ import annotations

import math

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout import geom
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.route import brief as B
from eda_agent.layout.route import creepage as C
from eda_agent.layout.route import review
from eda_agent.layout.route.job import route_job
from eda_agent.layout.route.params import NETLESS, RouteRules
from eda_agent.layout.rules import RuleSet

SMD = [PadCopper("TopLayer", "rect", 20, 20)]
SRC = "IEC 62368-1 table, 250 V working, PD2, group IIIb"


def _board(pads) -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=6mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    b.pads = [Pad(ref, name, x, y, net=net, copper=list(SMD)) for ref, name, x, y, net in pads]
    return b


def test_the_surface_way_goes_round_a_slot():
    slot = [(40, -50), (60, -50), (60, 50), (40, 50)]
    assert C.surface_distance((0, 0), (100, 0), []) == 100
    got = C.surface_distance((0, 0), (100, 0), [slot])
    assert got == pytest.approx(2 * math.hypot(40, 50) + 20)
    # A slot to the board's edge: the way round on the board is the other end.
    edge = [(40, -10), (60, -10), (60, 400), (40, 400)]
    got = C.surface_distance((0, 0), (100, 0), [edge], [(-100, -100), (200, -100),
                                                        (200, 300), (-100, 300)])
    assert got == pytest.approx(2 * math.hypot(40, 10) + 20)


def test_creepage_falls_short_until_a_slot_cuts_between_the_sides():
    b = _board([("J1", "1", 100, 200, "MAINS"), ("U1", "1", 200, 200, "LOGIC")])
    got = C.measure(b, {"MAINS"}, None, 100, None)
    assert got["TopLayer"]["creepage"]["gap"] == pytest.approx(80, abs=C.SAMPLE)
    b.cutouts = [[(140, 100), (160, 100), (160, 300), (140, 300)]]
    assert C.measure(b, {"MAINS"}, None, 100, None) == {}
    # Clearance is straight through air: the slot does not lengthen it.
    got = C.measure(b, {"MAINS"}, None, 100, 90)
    assert got["TopLayer"]["clearance"]["gap"] == pytest.approx(80)
    assert "creepage" not in got["TopLayer"]


def test_a_parts_own_pins_across_the_barrier_are_its_rating_not_the_layouts():
    b = _board([("U2", "1", 100, 200, "MAINS"), ("U2", "8", 140, 200, "LOGIC")])
    assert C.measure(b, {"MAINS"}, None, 100, 100) == {}


def test_the_distance_is_a_clearance_between_the_sides_alone():
    b = _board([("J1", "1", 100, 200, "MAINS"), ("U1", "1", 300, 200, "LOGIC"),
                ("U1", "2", 300, 300, "OTHER")])
    net_id = {n: i + 1 for i, n in enumerate(b.nets())}
    rr = RouteRules(b, RuleSet.from_board(b), net_id)
    rr.barriers = B.Brief.from_list([], barriers=[{"name": "iso", "nets": ["MAINS"],
                                                   "creepage": 100, "clearance": 60,
                                                   "source": SRC}]).barrier_sides(b)
    assert rr.pair_clearance(net_id["LOGIC"], net_id["MAINS"]) == 100
    assert rr.pair_clearance(net_id["MAINS"], NETLESS) == 100
    assert rr.pair_clearance(net_id["LOGIC"], net_id["OTHER"]) == 6
    # Every net on the far side is alike: one clearance group, not one each.
    assert rr.group(net_id["LOGIC"]) == rr.group(net_id["OTHER"])
    assert rr.group(net_id["LOGIC"]) != rr.group(net_id["MAINS"])


def test_the_router_keeps_the_barrier_and_the_review_names_what_the_placement_breaks():
    b = _board([("R1", "1", 100, 100, "V"), ("R2", "1", 500, 100, "V"),
                ("F1", "1", 250, 140, "MAINS"), ("F1", "2", 350, 140, "MAINS")])
    brief = {"entries": [{"nets": ["V", "MAINS"], "layers": ["TopLayer"], "source": "test"}],
             "barriers": [{"name": "iso", "nets": ["MAINS"], "creepage": 60, "source": SRC}]}
    store = get_job_store()
    jid = store.submit("layout_route", route_job, {"board": b, "review": True, "advise": False,
                                                   "nets": ["V", "MAINS"], "brief": brief})
    assert store.wait(jid, timeout=120)
    res = store.get(jid).result
    v = [t for t in res["tracks"] if t["net_name"] == "V"]
    assert v, "V is routed"
    mains = [p.shape_on("TopLayer") for p in b.pads if p.net == "MAINS"]
    gap = min(geom.clearance(geom.capsule(t["x1"], t["y1"], t["x2"], t["y2"], t["width"]), s)
              for t in v for s in mains)
    assert gap >= 60 - 0.5
    assert not [f for f in res["findings"] if f["check"] == "barrier"]
    # Pads placed too close are the placement's: the review names them.
    b2 = _board([("J1", "1", 100, 200, "MAINS"), ("U1", "1", 160, 200, "LOGIC")])
    bb = B.Brief.from_list([], barriers=brief["barriers"])
    got = review.barrier_misses(b2, b2, bb, ["LOGIC"])
    assert [(x[0].name, x[1], x[2]) for x in got] == [("iso", "TopLayer", "creepage")]
    found = review.findings(b2, b2, [], [], bb, ["LOGIC"], {}, {}, {}, {}, judge=False)
    assert [f["id"] for f in found] == ["barrier:iso:TopLayer:creepage"]


@pytest.mark.parametrize("change, says", [
    ({"name": ""}, "give it a name"),
    ({"nets": []}, "names no nets"),
    ({"nets": ["NOPE"]}, "no net or net class"),
    ({"apart_from": ["MAINS"]}, "on both sides"),
    ({"creepage": None}, "give creepage, clearance or both"),
    ({"source": ""}, "give its source"),
])
def test_a_barrier_that_cannot_be_kept_is_refused(change, says):
    b = _board([("J1", "1", 100, 200, "MAINS"), ("U1", "1", 300, 200, "LOGIC")])
    bar = {"name": "iso", "nets": ["MAINS"], "creepage": 100, "source": SRC, **change}
    got = B.check(b, B.Brief.from_list([], barriers=[bar]))
    assert any(says in e for e in got["errors"]), got["errors"]


def test_a_net_class_stands_for_its_nets():
    b = _board([("J1", "1", 100, 200, "L"), ("J1", "2", 100, 300, "N"),
                ("U1", "1", 300, 200, "LOGIC")])
    b.net_classes = {"HV": ["L", "N"]}
    bar = {"name": "iso", "nets": ["HV"], "creepage": 100, "source": SRC}
    assert B.check(b, B.Brief.from_list([], barriers=[bar]))["ok"]
    assert B.Brief.from_list([], barriers=[bar]).barrier_sides(b)[0][0] == {"L", "N"}
