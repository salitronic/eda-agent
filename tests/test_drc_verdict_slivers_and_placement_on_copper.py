# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Three ways the layout checks reported clean on a board Altium then failed.

* A rule whose scope the evaluator could not read was skipped, and the
  check still said pass. Nothing found is not nothing there: it is now
  ``incomplete``.
* Solder mask slivers were never checked, so a router via between two
  pads passed and the batch DRC after Apply found a sliver.
* The placer ignored the board's own tracks and vias, and the placement
  was judged with the routing taken away, so a moved part put down on a
  track reported no violations.

All boards are synthetic.
"""

from __future__ import annotations

import pytest

from eda_agent.layout.audit import drc_summary, run_audits
from eda_agent.layout.drc import mask_slivers, verdict
from eda_agent.layout.model import (
    Component, Layer, LayoutBoard, Pad, PadCopper, Region, Rule, Track, Via,
)
from eda_agent.layout.place.job import place_job
from eda_agent.layout.rules import RuleSet

CLEARANCE = Rule("Clearance", "clearance", "All", "All", 1, True, {"gap": 6.0},
                 "Clearance Constraint (Gap=6mil) (All),(All)")
SLIVER = Rule("Sliver", "mask_sliver", "All", "All", 1, True, {},
              "Minimum Solder Mask Sliver (Gap=4mil) (All),(All)")
MASK = Rule("Mask", "soldermask", "All", "", 1, True, {},
            "Solder Mask Expansion (Expansion =4mil) (All)")


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _board(*rules, mask_read=True) -> LayoutBoard:
    b = LayoutBoard(name="t", outline=_square(0, 0, 1000, 600),
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=list(rules))
    b.meta["mask_read"] = mask_read
    return b


def _part(b, ref, x, y, net_a, net_b, w=60.0, h=30.0, pitch=40.0):
    b.components.append(Component(ref, x=x, y=y, courtyard=_square(
        x - w / 2, y - h / 2, x + w / 2, y + h / 2), courtyard_source="courtyard"))
    for name, dx, net in (("1", -pitch / 2, net_a), ("2", pitch / 2, net_b)):
        b.pads.append(Pad(ref, name, x + dx, y, net=net,
                          copper=[PadCopper("TopLayer", "rect", 10, 10)]))


# -- 1. an unevaluated rule is incomplete, never a pass ----------------------

def test_a_scope_with_an_unknown_predicate_makes_the_drc_incomplete():
    odd = Rule("Odd", "clearance", "InNamedPolygon('GND')", "All", 2, True, {"gap": 50.0},
               "Clearance Constraint (Gap=50mil) (InNamedPolygon('GND')),(All)")
    b = _board(CLEARANCE, odd)
    _part(b, "R1", 200, 300, "A", "B")
    # It parses, so it used to slip past: the predicate failed only when
    # matched, and the rule looked as if it did not apply.
    assert [u["rule"] for u in RuleSet.from_board(b).unevaluated()] == ["Odd"]
    s = drc_summary(b)
    assert s["status"] == "incomplete"
    assert s["pass"] is False
    assert s["counts"]["violations"] == 0
    assert run_audits(b, ["drc"])["status"] == "incomplete"


def test_an_unknown_scope_on_a_rule_the_drc_does_not_apply_leaves_it_a_pass():
    height = Rule("H", "height", "HasFootprint('X')", "", 1, True, {},
                  "Height Constraint (Min=0mil) (Max=1000mil) (HasFootprint('X'))")
    b = _board(CLEARANCE, height)
    _part(b, "R1", 200, 300, "A", "B")
    s = drc_summary(b)
    assert s["status"] == "pass" and s["pass"] is True


def test_a_violation_outranks_an_unevaluated_rule():
    odd = Rule("Odd", "clearance", "InNamedPolygon('GND')", "All", 2, True, {"gap": 50.0},
               "Clearance Constraint (Gap=50mil) (InNamedPolygon('GND')),(All)")
    b = _board(CLEARANCE, odd)
    _part(b, "R1", 200, 300, "A", "B")
    b.tracks.append(Track("TopLayer", 150, 302, 250, 302, 8, "X"))
    assert drc_summary(b)["status"] == "fail"


# -- 2. solder mask slivers --------------------------------------------------

def _two_pads(gap_copper: float) -> LayoutBoard:
    b = _board(CLEARANCE, SLIVER, MASK)
    b.pads += [Pad("", "1", 100, 100, net="A", copper=[PadCopper("TopLayer", "rect", 20, 20)]),
               Pad("", "2", 120 + gap_copper, 100, net="B",
                   copper=[PadCopper("TopLayer", "rect", 20, 20)])]
    return b


def test_openings_closer_than_the_sliver_rule_are_a_violation():
    # 10 mil of copper gap less 4 mil of mask each side leaves a 2 mil web.
    found = mask_slivers(_two_pads(10.0))
    assert len(found) == 1
    assert found[0].rule == "mask_sliver" and round(found[0].gap, 3) == 2.0
    assert verdict(_two_pads(10.0))["status"] == "fail"


def test_openings_that_merge_or_stand_clear_are_not():
    assert mask_slivers(_two_pads(6.0)) == []      # openings overlap: one opening
    assert mask_slivers(_two_pads(14.0)) == []     # a 6 mil web: wide enough


def test_a_hand_set_expansion_wins_over_the_rule():
    # By the rule, 14 mil of copper less 4 + 4 leaves a 6 mil web; set by
    # hand to 6 each, it leaves 2.
    b = _two_pads(14.0)
    b.pads[0].mask_manual = [6.0, 6.0]
    b.pads[1].mask_manual = [6.0, 6.0]
    found = mask_slivers(b)
    assert len(found) == 1 and round(found[0].gap, 3) == 2.0


def test_a_via_between_two_pads_leaves_a_sliver_unless_it_is_tented():
    b = _board(CLEARANCE, SLIVER, MASK)
    b.pads.append(Pad("", "1", 100, 100, net="A", copper=[PadCopper("TopLayer", "rect", 20, 20)]))
    b.vias.append(Via(135, 100, 16, 8, "TopLayer", "BottomLayer", "B"))
    # pad edge 110, via edge 127: 17 mil of copper, 9 mil of mask.
    b.rules[1] = Rule("Sliver", "mask_sliver", "All", "All", 1, True, {},
                      "Minimum Solder Mask Sliver (Gap=10mil) (All),(All)")
    found = mask_slivers(b)
    assert len(found) == 1 and round(found[0].gap, 3) == 9.0
    b.vias[0].tent_top = True
    assert mask_slivers(b) == []


def test_without_the_mask_read_the_sliver_check_says_it_did_not_run():
    b = _two_pads(10.0)
    b.meta["mask_read"] = False
    assert mask_slivers(b) is None
    v = verdict(b)
    assert v["status"] == "incomplete"
    assert v["not_checked"][0] == "mask_sliver"
    assert any("mask" in n for n in v["notes"])


def test_the_rule_texts_altium_writes_are_read():
    rules = RuleSet.from_board(_board(SLIVER, MASK))
    assert [r.type for r in rules.rules] == ["mask_sliver", "soldermask"]
    assert rules.of_type("mask_sliver")[0].values["gap"] == 4.0
    assert rules.of_type("soldermask")[0].values["expansion"] == 4.0


# -- 3. placement keeps off the board's own copper ---------------------------

def _placement_board() -> LayoutBoard:
    """R2 is fixed and R1 is pulled to it. Left alone, R1 settles just
    above R2 (measured: y 419); tracks of another net run there and just
    below, so the only clean spots are beside R2."""
    b = _board(CLEARANCE)
    _part(b, "R2", 500, 380, "A", "B")
    _part(b, "R1", 500, 200, "A", "B")
    b.tracks += [Track("TopLayer", 300, 340, 700, 340, 10, "X"),
                 Track("TopLayer", 300, 420, 700, 420, 10, "X")]
    return b


@pytest.mark.parametrize("strategy", ["analytic", "blocks"])
def test_a_moved_part_keeps_clear_of_the_tracks_already_there(strategy):
    out = place_job({"board": _placement_board(), "parts": ["R1"], "strategy": strategy})
    s = out["summary"]
    assert s["parts_moved"] == 1
    assert s["drc_status"] == "pass", out["violations"]
    assert s["violations"] == 0


def test_the_placement_is_judged_with_the_routing_it_leaves():
    """A track of another net already runs under fixed R2. Whatever R1
    does, applying the moves leaves that short, and the job must say so:
    judged with the routing taken away, it said 0."""
    b = _placement_board()
    b.tracks.append(Track("TopLayer", 450, 380, 550, 380, 10, "X"))
    out = place_job({"board": b, "parts": ["R1"]})
    assert out["summary"]["drc_status"] == "fail"
    assert out["summary"]["violations"] >= 1


def test_the_analytic_placer_keeps_parts_off_a_keepout():
    b = _placement_board()
    b.tracks.clear()
    keep = [_square(300, 330, 700, 350), _square(300, 410, 700, 430)]
    b.regions += [Region("TopLayer", k, [], "", "keepout", True) for k in keep]
    out = place_job({"board": b, "parts": ["R1"], "strategy": "analytic"})
    r1 = next(m for m in out["moves"] if m["designator"] == "R1")
    # Unturned or turned, R1's pads lie within 25 mil of its centre.
    x0, y0, x1, y1 = r1["x"] - 25, r1["y"] - 25, r1["x"] + 25, r1["y"] + 25
    for kx0, ky0, kx1, ky1 in ((300, 330, 700, 350), (300, 410, 700, 430)):
        assert x1 <= kx0 or x0 >= kx1 or y1 <= ky0 or y0 >= ky1, r1


# -- the read that feeds the sliver check -------------------------------------

def _fake_altium(pads, copper):
    """Canned pcb.get_layout_model replies, one per section."""
    replies = {
        "board": {"file": "C:/x/t.PcbDoc", "outline": [[0, 0], [10000000, 0],
                  [10000000, 10000000], [0, 10000000]],
                  "layers": [{"id": "TopLayer"}, {"id": "BottomLayer"}]},
        "pads": {"pads": pads}, "components": {"components": []},
        "copper": {"copper": copper}, "rules": {"rules": []}, "classes": {},
    }
    return lambda cmd, params: replies[params["section"]]


def _via(**mask):
    return {"k": "via", "v": [5000000, 5000000, 236220, 118110], "low": "TopLayer",
            "high": "BottomLayer", "sizes": [], "layer": "MultiLayer", "net": "A",
            "comp": "", **mask}


def test_the_read_carries_each_vias_mask_and_says_it_was_read():
    from eda_agent.layout.read_altium import read_board
    b = read_board(_fake_altium([], [
        _via(tent=[True, False], from_hole=False, mask=[0, 0]),
        _via(tent=[False, False], from_hole=True, mask=None)]))
    # A board of vias and no pads, as the scratch Tent board is: read live,
    # the flag came from the pads alone and said the mask was not read.
    assert b.meta["mask_read"] is True
    first, second = b.vias
    assert (first.tent_top, first.tent_bottom, first.mask_manual) == (True, False, [0.0, 0.0])
    assert (second.tent_top, second.mask_from_hole, second.mask_manual) == (False, True, None)


def test_a_read_by_an_older_script_is_marked_unread():
    from eda_agent.layout.read_altium import read_board
    b = read_board(_fake_altium([], [_via()]))
    assert b.meta["mask_read"] is False
    assert (b.vias[0].tent_top, b.vias[0].mask_manual) == (False, None)
