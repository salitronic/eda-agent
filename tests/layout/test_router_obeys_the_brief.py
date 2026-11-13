# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The router obeys the AI's routing brief, or holds the net back.

The brief's layers, width, region, keep-out and via budget are walls to
the search; copper that still breaks one (a stage's own route) is not
part of the proposal, only reported with the rule it broke.
"""

from __future__ import annotations

from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule, Track
from eda_agent.layout.route import brief as B
from eda_agent.layout.route.job import route_job


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=6mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    th = [PadCopper("TopLayer", "round", 40, 40), PadCopper("BottomLayer", "round", 40, 40)]
    b.pads = [Pad("J1", "1", 100, 200, net="A", copper=list(th), hole=20),
              Pad("J2", "1", 500, 200, net="A", copper=list(th), hole=20)]
    return b


def _tracks(res, net="A"):
    return [t for t in res["tracks"] if t["net_name"] == net]


def test_layers_and_width_are_obeyed():
    res = route_job({"board": _board(), "brief": [
        {"nets": ["A"], "layers": ["BottomLayer"], "width": 12, "source": "test"}]})
    ts = _tracks(res)
    assert ts and {t["layer"] for t in ts} == {"BottomLayer"}
    assert {t["width"] for t in ts} == {12.0}
    assert res["summary"]["completion"] == 1.0 and not res["brief"]["outside_brief"]


def test_a_keep_out_is_walked_round_and_a_region_kept_to():
    wall = [(250, 150), (350, 150), (350, 400), (250, 400)]
    res = route_job({"board": _board(), "brief": [
        {"nets": ["A"], "layers": ["TopLayer"], "keep_out": [wall], "max_vias": 0}]})
    ts = _tracks(res)
    assert ts and res["summary"]["completion"] == 1.0
    assert all(max(t["y1"], t["y2"]) < 150 + 1 for t in ts if 250 <= (t["x1"] + t["x2"]) / 2 <= 350)
    assert not res["vias"]
    region = [(50, 150), (550, 150), (550, 260), (50, 260)]
    res = route_job({"board": _board(), "brief": [{"nets": ["A"], "region": [region]}]})
    assert _tracks(res) and all(150 <= t["y1"] <= 260 and 150 <= t["y2"] <= 260 for t in _tracks(res))


def test_a_rule_that_cannot_be_met_leaves_the_net_unrouted_not_bent():
    # A keep-out across the whole board, on the only layer allowed.
    wall = [(250, -50), (350, -50), (350, 450), (250, 450)]
    res = route_job({"board": _board(), "brief": [
        {"nets": ["A"], "layers": ["TopLayer"], "keep_out": [wall]}]})
    assert not _tracks(res) and "A" in res["unrouted"]


def test_copper_breaking_the_brief_is_held_back():
    from eda_agent.layout.model import Track as T, Via as V
    brief = B.Brief.from_list([{"nets": ["A"], "layers": ["TopLayer"], "max_vias": 1}])
    held = B.broken(brief, [T("BottomLayer", 0, 0, 10, 0, 6, net="A")],
                    [V(0, 0, 20, 10, net="A"), V(5, 5, 20, 10, net="A")])
    assert any(r.startswith("layers") for r in held["A"])
    assert any(r.startswith("max_vias") for r in held["A"])


def test_the_check_names_what_the_brief_lacks_and_what_it_gets_wrong():
    b = _board()
    b.pads += [Pad("Y1", "1", 100, 100, net="XTAL", copper=[PadCopper("TopLayer", "rect", 20, 20)]),
               Pad("U1", "1", 200, 100, net="XTAL", copper=[PadCopper("TopLayer", "rect", 20, 20)])]
    got = B.check(b, B.Brief.from_list([
        {"nets": ["A"], "width": 3},
        {"nets": ["NOPE"]},
        {"nets": ["XTAL"], "layers": ["BottomLayer"]}]))
    assert not got["ok"]
    text = " ".join(got["errors"])
    assert "under the board's width rule" in text and "no net 'NOPE'" in text
    assert "surface pads on ['TopLayer']" in text
    assert got["needed"] == [{"net": "XTAL", "kind": "clock", "why": "its entry gives no source",
                              "read": B.CRITICAL["clock"]}]


def test_a_crystal_ground_and_a_rail_behind_an_inductor_are_not_critical():
    smd = [PadCopper("TopLayer", "rect", 20, 20)]

    def pads(net, *pins):
        return [Pad(ref, num, 0, 0, net=net, copper=list(smd)) for ref, num in pins]

    b = _board()
    b.pads += (pads("XIN", ("Y1", "1"), ("C1", "1"), ("U1", "1"))
               # The crystal's case pins on ground, with everything else.
               + pads("GND", ("Y1", "2"), ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"),
                      ("C5", "2"), ("U1", "9"), ("U2", "9"))
               + pads("LX", ("L1", "1"), ("U1", "31"), ("C9", "1"))
               + pads("BST", ("C9", "2"), ("U1", "30"))
               # The regulator's output: the inductor's other end and its capacitors.
               + pads("VOUT", ("L1", "2"), ("C2", "1"), ("C3", "1"), ("U1", "6"))
               # A filtered supply: a ferrite inductor, one capacitor, the IC's supply pin.
               + pads("VDD_F", ("L2", "2"), ("C4", "1"), ("U1", "1")))
    got = B.critical_nets(b)
    assert got == {"XIN": "clock", "LX": "switching"}


def test_a_round_that_cannot_route_a_net_tries_the_options_and_asks():
    b = _board()
    smd = [PadCopper("TopLayer", "rect", 20, 20)]
    b.pads = [Pad("R1", "1", 100, 200, net="A", copper=list(smd)),
              Pad("R2", "1", 500, 200, net="A", copper=list(smd)),
              Pad("R3", "1", 300, 20, net="B", copper=list(smd)),
              Pad("R4", "1", 300, 380, net="B", copper=list(smd))]
    # B laid by hand before this round, straight across A's way.
    b.tracks = [Track("TopLayer", 300, 20, 300, 380, 10, net="B")]
    res = route_job({"board": b, "nets": ["A"], "review": True, "advise_seconds": 60,
                     "stages": {"join_existing": True},
                     "brief": [{"nets": ["A"], "layers": ["TopLayer"], "max_vias": 0,
                                "source": "test"}]})
    assert "A" in res["unrouted"]
    rv = res["review"]
    opts = rv["advice"]["A"]["options"]
    kinds = {o["kind"]: o for o in opts}
    assert kinds["rip_up"]["routes"] and kinds["rip_up"]["apply"] == {"tool": "pcb_unroute", "nets": ["B"]}
    relax = [o for o in opts if o["kind"] == "relax_brief"]
    # Each rule alone still forbids it; with both dropped it routes.
    assert [o["routes"] for o in relax] == [True, False, False]   # what routes, first
    assert relax[0]["apply"]["drop"] == [{"nets": ["A"], "rules": ["max_vias", "layers"]}]
    # Ripping up a net laid before is less of a change than the AI's own rules.
    assert opts.index(kinds["rip_up"]) < opts.index(relax[0])
    assert opts[0]["routes"]
    assert any(q.startswith("A: not routed. Best option tried") for q in rv["questions"])
    assert rv["picture"] is None or rv["picture"].endswith(".png")


def test_the_review_names_a_pad_entered_at_an_angle_or_crossed_off_centre():
    # Live, a crystal net ended diagonally on the crystal's pad and ran
    # across its load capacitor's; DRC passed and no question was asked.
    from eda_agent.layout.route import review

    smd = [PadCopper("TopLayer", "rect", 20, 20)]
    pads = {"X": [Pad("Y1", "1", 0, 0, net="X", copper=list(smd)),
                  Pad("C1", "1", 100, 50, net="X", copper=list(smd)),
                  Pad("U1", "1", 200, 0, net="X", copper=list(smd))]}

    def t(x1, y1, x2, y2):
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": 4, "layer": "TopLayer",
                "net_name": "X"}

    got = review.pad_entries([t(0, 0, 50, 50), t(50, 50, 150, 50), t(150, 50, 200, 50),
                              t(200, 50, 200, 0)], pads)
    assert got == {"X": ["enters Y1.1 at 45 degrees to its axis", "crosses C1.1 off its centre"]}
    b = _board()
    found = review.findings(b, b, [], [], B.Brief(), [], {}, {}, {}, {}, entries=got)
    assert [f["id"] for f in found] == ["pad_entry:X"]
    assert found[0]["text"].startswith("X: enters Y1.1 at 45 degrees")


def test_a_width_too_wide_for_the_gap_is_named_even_after_the_rip_ups():
    # Live, two rip-ups used the time and the brief's width, the real
    # cause, was never tried.
    b = _board()
    smd = [PadCopper("TopLayer", "rect", 20, 20)]
    b.pads = [Pad("R1", "1", 100, 200, net="A", copper=list(smd)),
              Pad("R2", "1", 500, 200, net="A", copper=list(smd))]
    # Walls of two other nets on both layers, a 30 mil gap between them.
    b.tracks = [Track(layer, 300, y0, 300, y1, 10, net=n)
                for layer in ("TopLayer", "BottomLayer")
                for n, y0, y1 in (("B", -20, 180), ("C", 220, 420))]
    res = route_job({"board": b, "nets": ["A"], "review": True, "advise_seconds": 60,
                     "stages": {"join_existing": True},
                     "brief": [{"nets": ["A"], "width": 30, "source": "test"}]})
    assert "A" in res["unrouted"]
    opts = res["review"]["advice"]["A"]["options"]
    width = [o for o in opts if o["kind"] == "relax_brief"]
    assert width and width[0]["routes"]
    assert width[0]["apply"]["drop"] == [{"nets": ["A"], "rules": ["width"]}]
    assert opts[0] is width[0], "the option that routes comes first"
