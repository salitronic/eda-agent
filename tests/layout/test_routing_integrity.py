# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Termination, guard, broadside coupling, net classes kept apart, and
decoupling (pdn): written in the brief, measured on the copper, put to the
AI as findings with the simulation that would settle them.

Boards are drawn by hand here, so each measure is checked against copper
whose answer is known.
"""

from __future__ import annotations

import pytest

from eda_agent.layout.model import (Component, Layer, LayoutBoard, Pad, PadCopper, Rule,
                                    Track, Via)
from eda_agent.layout.route import brief as B
from eda_agent.layout.route import integrity as I
from eda_agent.layout.route import review

SMD = [PadCopper("TopLayer", "rect", 20, 20)]


def _board(layers=("TopLayer", "BottomLayer"), kinds=None) -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=10mil) (All)")]
    kinds = kinds or {}
    return LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (1200, 0), (1200, 600), (0, 600)],
                       layers=[Layer(n, kinds.get(n, "signal"), i) for i, n in enumerate(layers)],
                       rules=rules)


def _pad(comp, name, x, y, net):
    return Pad(comp, name, x, y, net=net, copper=list(SMD))


def _t(x1, y1, x2, y2, net, layer="TopLayer", w=8.0):
    return Track(layer, x1, y1, x2, y2, w, net=net)


# --- termination -------------------------------------------------------------------

def _bus(branch: float) -> LayoutBoard:
    """U1.1 drives CLK along y=300 to the terminator R1 at x=1000; U2.1 at
    x=500 hangs ``branch`` mil off the line."""
    b = _board()
    b.pads = [_pad("U1", "1", 100, 300, "CLK"), _pad("U2", "1", 500, 300 + branch, "CLK"),
              _pad("R1", "1", 1000, 300, "CLK"), _pad("R1", "2", 1040, 300, "VTT")]
    b.tracks = [_t(100, 300, 500, 300, "CLK"), _t(500, 300, 1000, 300, "CLK")]
    if branch:
        b.tracks.append(_t(500, 300, 500, 300 + branch, "CLK"))
    return b


def _term_brief(**kw):
    e = {"nets": ["CLK"], "terminate": "R1", "driver": "U1.1", "max_stub": 50,
         "source": "the driver's datasheet, termination section", **kw}
    return B.Brief.from_list([e])


def test_a_load_hanging_off_the_trunk_is_a_stub_finding():
    got = I.termination_misses(_bus(200), _term_brief(), ["CLK"])
    assert got["CLK"]["what"] == "stub" and got["CLK"]["far"] == "R1.1"
    assert got["CLK"]["stubs"] == {"U2.1": 200.0}
    assert not I.termination_misses(_bus(30), _term_brief(), ["CLK"])


def test_a_series_terminator_far_from_its_driver_is_a_finding():
    brief = _term_brief(terminate_at="source")
    got = I.termination_misses(_bus(0), brief, ["CLK"])
    assert got["CLK"] == {"what": "source", "length": 900.0, "limit": 50, "part": "R1"}
    f = review.findings(_bus(0), _bus(0), [], [], brief, ["CLK"], {}, {}, {}, {})
    assert any(x["id"] == "termination:CLK" and x["limit"] == 50 for x in f)


@pytest.mark.parametrize("entry,said", [
    ({"terminate": "R1", "max_stub": 50}, "give driver"),
    ({"terminate": "R9", "driver": "U1.1", "max_stub": 50}, "no part R9"),
    ({"terminate": "R1", "driver": "U1.1"}, "give max_stub"),
    ({"terminate": "R1", "driver": "U1.1", "max_stub": 5, "terminate_at": "middle"},
     "end or source"),
    ({"guard": "GND"}, "needs guard_gap"),
    ({"guard_gap": 10}, "need guard"),
])
def test_an_entry_that_cannot_be_measured_is_refused(entry, said):
    b = _bus(0)
    b.pads.append(_pad("J1", "1", 50, 50, "GND"))
    chk = B.check(b, B.Brief.from_list([{"nets": ["CLK"], "source": "x", **entry}]))
    assert any(said in e for e in chk["errors"]), chk["errors"]


# --- guard ------------------------------------------------------------------------------

def _guarded(both: bool) -> LayoutBoard:
    b = _board()
    b.pads = [_pad("U1", "1", 100, 300, "SENSE"), _pad("U2", "1", 900, 300, "SENSE"),
              _pad("J1", "1", 100, 100, "GND")]
    b.tracks = [_t(100, 300, 900, 300, "SENSE"), _t(100, 320, 900, 320, "GND")]
    if both:
        b.tracks.append(_t(100, 280, 900, 280, "GND"))
    return b


def test_a_guard_on_one_side_only_is_a_finding():
    brief = B.Brief.from_list([{"nets": ["SENSE"], "guard": "GND", "guard_gap": 15,
                                "source": "the ADC's datasheet, layout"}])
    got = I.guard_misses(_guarded(False), brief, ["SENSE"])
    # The pieces at the net's own pads are left out of the measure.
    assert 700 <= got["SENSE"]["unguarded"] <= 800
    assert not I.guard_misses(_guarded(True), brief, ["SENSE"])


# --- broadside and classes ---------------------------------------------------------

def _stacked(kinds=None) -> LayoutBoard:
    b = _board(("TopLayer", "Mid1", "BottomLayer"), kinds)
    b.pads = [_pad("U1", "1", 100, 300, "ADC"), _pad("U2", "1", 900, 300, "ADC"),
              _pad("U1", "2", 100, 340, "SW"), _pad("U3", "1", 900, 340, "SW")]
    b.tracks = [_t(100, 300, 900, 300, "ADC"), _t(100, 305, 900, 305, "SW", layer="Mid1"),
                _t(100, 340, 100, 305, "SW", layer="Mid1")]
    return b


def test_a_track_over_another_on_the_next_layer_is_broadside():
    near, at = I.broadside(_stacked(), "ADC", {"SW"}, 10.0)
    assert near > 700 and at is not None
    # A plane layer between them screens them.
    b = _board(("TopLayer", "Plane", "Mid1", "BottomLayer"), {"Plane": "plane"})
    assert I.neighbour_layers(b, "TopLayer") == []
    brief = B.Brief.from_list([{"nets": ["ADC"], "apart_from": ["NOISY"], "max_broadside": 100,
                                "parallel_gap": 10, "source": "app note"}])
    s = _stacked()
    s.net_classes = {"NOISY": ["SW"]}
    got = review.apart_misses(s, brief, ["ADC"])
    assert got[("ADC", "SW")]["broadside"] > 700
    assert B.check(s, brief)["errors"] == []


def test_a_net_class_in_apart_from_spaces_each_of_its_nets():
    s = _stacked()
    s.net_classes = {"NOISY": ["SW"]}
    brief = B.Brief.from_list([{"nets": ["ADC"], "apart_from": ["NOISY"], "spacing": 25,
                                "source": "app note"}])
    assert brief.spacing_pairs(s) == {("ADC", "SW"): 25.0, ("SW", "ADC"): 25.0}
    assert B.check(s, brief)["errors"] == []


# --- pdn -------------------------------------------------------------------------------

def _pdn_board(cap_x: float, vias: int) -> LayoutBoard:
    b = _board()
    b.components = [Component("U1", x=300, y=300), Component("C1", x=cap_x, y=300)]
    b.pads = [_pad("U1", "1", 300, 300, "3V3"), _pad("U1", "2", 300, 260, "GND"),
              _pad("C1", "1", cap_x, 300, "3V3"), _pad("C1", "2", cap_x, 340, "GND")]
    b.tracks = [_t(300, 300, cap_x, 300, "3V3")]
    b.vias = [Via(300 + 15, 330 + 10 * k, 20, 10, "TopLayer", "BottomLayer", net="3V3")
              for k in range(vias)]
    b.vias += [Via(cap_x + 20, 360, 20, 10, "TopLayer", "BottomLayer", net="GND")]
    return b


PDN = {"name": "U1 core", "part": "U1", "rails": ["3V3"], "max_distance": 100, "min_vias": 2,
       "via_reach": 50, "source": "the MCU's datasheet, power supply guidelines"}


def test_a_decap_too_far_and_too_few_vias_are_findings():
    brief = B.Brief.from_list([], pdn=[PDN])
    got = {(m["pin"], m["what"]): m for m in I.pdn_misses(_pdn_board(600, 1), brief, ["3V3"])}
    assert got[("U1.1", "distance")]["measured"] == 300.0
    assert got[("U1.1", "distance")]["along_copper"]
    assert got[("U1.1", "vias")]["measured"] == 1
    assert got[("C1.2", "return_vias")]["measured"] == 1
    assert not [m for m in I.pdn_misses(_pdn_board(360, 2), brief, ["3V3"])
                if m["what"] != "return_vias"]
    f = review.findings(_pdn_board(600, 1), _pdn_board(600, 1), [], [], brief, ["3V3"],
                        {}, {}, {}, {})
    pdn = [x for x in f if x["check"] == "pdn"]
    assert {x["id"] for x in pdn} >= {"pdn:U1 core:U1.1:distance", "pdn:U1 core:U1.1:vias"}
    assert all(x["simulate"] == {"tool": "pcb_route_simulate", "what": "pdn",
                                 "name": "U1 core"} for x in pdn)


@pytest.mark.parametrize("rule,said", [
    ({"part": "U9"}, "no part"),
    ({"rails": []}, "give rails"),
    ({"rails": ["NOPE"]}, "no net"),
    ({"via_reach": None}, "needs via_reach"),
    ({"source": ""}, "give its source"),
    ({"decaps": ["C7"]}, "no capacitor C7"),
])
def test_a_pdn_rule_that_cannot_be_measured_is_refused(rule, said):
    chk = B.check(_pdn_board(600, 1), B.Brief.from_list([], pdn=[{**PDN, **rule}]))
    assert any(said in e for e in chk["errors"]), chk["errors"]


def test_the_brief_keeps_its_pdn_rules(tmp_path, monkeypatch):
    from eda_agent import config
    monkeypatch.setattr(config.get_config(), "workspace_dir", tmp_path)
    brief = B.Brief.from_list([{"nets": ["CLK"], "guard": "GND", "guard_gap": 10,
                                "source": "x"}], pdn=[PDN])
    B.save("c:/x/demo.PcbDoc", brief)
    back = B.load("c:/x/demo.PcbDoc")
    assert back.pdn_list() == [PDN] and back.of["CLK"].guard == "GND"


# --- loops steered ---------------------------------------------------------------------

def test_a_loop_net_pays_to_leave_its_loops_hull():
    from eda_agent.layout.route.router import LOOP_STEER, Router

    b = _board()
    b.components = [Component("C1", x=100, y=300), Component("U1", x=300, y=300)]
    b.pads = [_pad("C1", "1", 100, 320, "VIN"), _pad("C1", "2", 100, 260, "GND"),
              _pad("U1", "1", 300, 320, "VIN"), _pad("U1", "3", 300, 260, "GND"),
              _pad("U2", "1", 900, 320, "SIG"), _pad("U3", "1", 1000, 320, "SIG")]
    hot = {"name": "input", "kind": "hot_loop", "members": ["C1.1", "U1.1", "U1.3", "C1.2"],
           "max_area": 5000, "source": "datasheet"}
    r = Router(b, brief=B.Brief.from_list([], loops=[hot]), stages=False)
    hull = r.loop_hulls["VIN"]
    spec = r.grid.spec
    i_in, j_in = spec.cell(200, 290)
    i_out, j_out = spec.cell(700, 290)
    assert hull[j_in, i_in] and not hull[j_out, i_out]
    assert "SIG" not in r.loop_hulls and LOOP_STEER > 1


def test_the_router_keeps_a_loop_net_inside_its_loop(monkeypatch):
    """A wall between the loop's VIN pads: over it is shorter but leaves
    the loop's hull; under it is longer and stays in. Steered, the router
    goes under; with the steer at 1 it would go over."""
    import sys
    from pathlib import Path

    from eda_agent.layout.model import Region
    from eda_agent.layout.route import router as R

    sys.path.insert(0, str(Path(__file__).parent))
    from test_routing_loops import HOT, _board as loop_board

    def route(steer):
        monkeypatch.setattr(R, "LOOP_STEER", steer)
        b = loop_board(100)
        for p in b.pads:
            if p.net == "GND":
                p.hole = 12
                p.copper = [PadCopper("TopLayer", "round", 24, 24),
                            PadCopper("BottomLayer", "round", 24, 24)]
        b.regions = [Region("BottomLayer", [(0, 0), (600, 0), (600, 400), (0, 400)],
                            net="GND", kind="pour")]
        b.tracks = [Track("TopLayer", 200, 135, 200, 225, 8, net="X")]
        brief = B.Brief.from_list([{"nets": ["VIN"], "layers": ["TopLayer"], "source": "t"}],
                                  loops=[HOT])
        out = R.route_adaptive(b, nets=frozenset(["VIN"]), brief=brief).apply()
        return [y for t in out.tracks[len(b.tracks):] if t.net == "VIN" for y in (t.y1, t.y2)]

    assert max(route(1.0)) > 225
    assert max(route(4.0)) <= 205
