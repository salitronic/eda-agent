# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The layout audits on boards small enough to check by hand.

Each audit is the number a design stage's exit gate names, so each one
must find the defect it exists for and stay quiet on a board without it.
One clean board is built here and every test plants one defect on a copy:
two bodies overlapping, two pads too close, a part on a keepout or a
mounting hole, a part off the edge, a right-angle and an acute corner, a
signal via with no ground via near, a pour broken in two, a missing track.
All boards are synthetic.
"""

from __future__ import annotations

import math

import pytest

from eda_agent.layout.audit import (
    CHECKS,
    body_outline,
    connectivity_summary,
    corner_audit,
    placement_audit,
    plane_region_audit,
    return_via_audit,
    run_audits,
)
from eda_agent.layout.bench import copy_board
from eda_agent.layout.model import (
    Component, DiffPair, Layer, LayoutBoard, Pad, PadCopper, Region, Rule, Track, Via,
)


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _part(ref, x, y, net_a, net_b, *, w=60.0, h=30.0, pitch=40.0, body=True,
          locked=False, side="top"):
    layer = "TopLayer" if side == "top" else "BottomLayer"
    comp = Component(ref, x=x, y=y, locked=locked, side=side,
                     courtyard=_square(x - w / 2, y - h / 2, x + w / 2, y + h / 2),
                     courtyard_source="courtyard" if body else "pads")
    pads = [Pad(ref, "1", x - pitch / 2, y, net=net_a,
                copper=[PadCopper(layer, "rect", 10, 10)]),
            Pad(ref, "2", x + pitch / 2, y, net=net_b,
                copper=[PadCopper(layer, "rect", 10, 10)])]
    return comp, pads


def _add(board, ref, x, y, net_a, net_b, **kw):
    c, p = _part(ref, x, y, net_a, net_b, **kw)
    board.components.append(c)
    board.pads += p


def _route(board, pts, net, layer="TopLayer", width=8.0):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        board.tracks.append(Track(layer, x1, y1, x2, y2, width, net))


B_ROUTE = [(220, 300), (300, 300), (350, 350), (430, 350), (480, 300)]


def _clean() -> LayoutBoard:
    """Two parts joined by net B with 45 degree bends, a signal via with a
    ground via 36 mil away, one piece of ground pour, a keepout and a
    mounting hole that nothing sits on."""
    b = LayoutBoard(
        name="audit", outline=_square(0, 0, 1000, 600),
        layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
        rules=[Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                    "Clearance Constraint (Gap=6mil) (All),(All)")])
    _add(b, "R1", 200, 300, "A", "B")
    _add(b, "R2", 500, 300, "B", "C")
    _route(b, B_ROUTE, "B")
    b.vias = [Via(350, 350, 20, 10, "TopLayer", "BottomLayer", "B"),
              Via(370, 380, 20, 10, "TopLayer", "BottomLayer", "GND")]
    b.regions = [Region("BottomLayer", _square(600, 50, 950, 550), [], "GND", "pour"),
                 Region("KeepOutLayer", _square(50, 450, 150, 550), [], "", "keepout", True)]
    b.pads.append(Pad("", "MH", 100, 100, hole=120.0, plated=False))
    return b


def test_the_clean_board_passes_every_check():
    rep = run_audits(_clean())
    assert rep["pass"], rep["summary"]
    assert set(rep["checks"]) == set(CHECKS)
    s = rep["summary"]
    # Each check looked at something: a clean verdict over nothing is
    # a false clean.
    assert s["placement_audit"]["parts"] == 2
    assert rep["status"] == "pass"
    assert s["connectivity_summary"] == {"pass": True, "status": "pass", "nets": 1, "routed": 1,
                                         "partly_routed": 0, "not_started": 0,
                                         "unreached_pads": 0, "missing_connections": 0}
    assert s["corner_audit"]["junctions"] == 3
    assert s["return_via_audit"]["signal_vias"] == 1
    assert s["plane_region_audit"]["pieces"] == 1


def test_run_audits_runs_only_the_named_checks_and_refuses_an_unknown_one():
    rep = run_audits(_clean(), ["corner_audit"])
    assert list(rep["checks"]) == ["corner_audit"]
    with pytest.raises(ValueError, match="no_such_check"):
        run_audits(_clean(), ["no_such_check"])


def test_the_top_level_verdict_fails_when_one_check_fails():
    b = _clean()
    b.tracks = b.tracks[:-1]
    rep = run_audits(b)
    assert not rep["pass"]
    assert not rep["summary"]["connectivity_summary"]["pass"]
    assert rep["summary"]["placement_audit"]["pass"]


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def test_two_overlapping_bodies_are_one_overlap_and_touching_ones_none():
    b = _clean()
    _add(b, "R3", 200, 325, "D", "E")      # courtyard 5 mil into R1's
    rep = placement_audit(b)
    assert rep["counts"]["overlaps"] == 1 and not rep["pass"]
    hit = rep["overlaps"][0]
    assert {hit["a"], hit["b"]} == {"R1", "R3"}
    assert hit["area_sq_mils"] == pytest.approx(300.0)
    assert hit["at"] == [200.0, 312.5]

    b = _clean()
    _add(b, "R3", 200, 330, "D", "E")      # courtyards share an edge only
    rep = placement_audit(b)
    assert rep["counts"]["overlaps"] == 0 and rep["pass"]


def test_bodies_on_opposite_sides_do_not_overlap():
    b = _clean()
    _add(b, "R3", 200, 325, "D", "E", side="bottom")
    assert placement_audit(b)["counts"]["overlaps"] == 0


def test_two_pads_closer_than_the_rule_are_one_gap():
    b = _clean()
    # R4.2's edge at x=225, R5.1's at x=229: 4 mil under a 6 mil rule.
    _add(b, "R4", 200, 150, "F", "G", body=False)
    _add(b, "R5", 254, 150, "H", "I", body=False)
    rep = placement_audit(b)
    assert rep["counts"]["pad_gaps_below_rule"] == 1 and not rep["pass"]
    gap = rep["pad_gaps"][0]
    assert {gap["a"], gap["b"]} == {"R4.2", "R5.1"}
    assert gap["gap"] == pytest.approx(4.0) and gap["required"] == pytest.approx(6.0)
    assert gap["at"] == [227.0, 150.0]
    assert rep["counts"]["overlaps"] == 0

    b = _clean()
    _add(b, "R4", 200, 150, "F", "G", body=False)
    _add(b, "R5", 260, 150, "H", "I", body=False)    # 10 mil apart
    assert placement_audit(b)["counts"]["pad_gaps_below_rule"] == 0


def test_pads_too_close_inside_one_footprint_are_listed_but_do_not_fail():
    b = _clean()
    _add(b, "U1", 200, 150, "F", "G", pitch=14.0, body=False)   # 4 mil apart
    rep = placement_audit(b)
    assert rep["counts"]["within_footprint_pad_gaps"] == 1
    assert rep["counts"]["pad_gaps_below_rule"] == 0
    assert rep["pass"]


def test_a_part_on_a_keepout_is_found_and_a_keepout_on_the_other_side_is_not():
    b = _clean()
    _add(b, "R6", 100, 500, "J", "K")
    rep = placement_audit(b)
    assert rep["counts"]["on_keepouts"] == 1 and not rep["pass"]
    assert rep["on_keepouts"][0]["part"] == "R6"
    assert rep["on_keepouts"][0]["keepout"] == "region#1"

    b = _clean()
    b.regions[1] = Region("BottomLayer", _square(50, 450, 150, 550), [], "", "keepout", True)
    _add(b, "R6", 100, 500, "J", "K")       # top side, surface pads
    assert placement_audit(b)["counts"]["on_keepouts"] == 0


def test_a_part_on_a_mounting_hole_is_found():
    b = _clean()
    _add(b, "R7", 100, 100, "J", "K", body=True)
    rep = placement_audit(b)
    assert rep["counts"]["on_mounting_holes"] == 1 and not rep["pass"]
    assert rep["on_mounting_holes"][0] == {"part": "R7", "hole": "~MH@100.00,100.00",
                                          "at": [100.0, 100.0]}


def test_a_part_off_the_board_is_found_and_a_fixed_one_does_not_fail():
    b = _clean()
    _add(b, "R8", 990, 300, "J", "K", body=False)
    rep = placement_audit(b)
    assert rep["counts"]["off_board"] == 1 and not rep["pass"]
    assert rep["off_board"][0]["part"] == "R8"

    b = _clean()
    _add(b, "R8", 990, 300, "J", "K", body=True)    # body crosses the edge
    assert placement_audit(b)["counts"]["off_board"] == 1

    b = _clean()
    _add(b, "J1", 990, 300, "J", "K", locked=True)
    rep = placement_audit(b)
    assert rep["counts"]["off_board"] == 0 and rep["counts"]["off_board_fixed"] == 1
    assert rep["pass"]


def test_a_body_over_a_cutout_is_off_the_board():
    b = _clean()
    b.cutouts = [_square(190, 80, 210, 220)]      # a slot through the body
    _add(b, "R9", 200, 150, "J", "K", w=80.0, h=30.0)
    assert placement_audit(b)["counts"]["off_board"] == 1


def test_the_body_is_the_one_the_placer_keeps_apart():
    """The audit and the placer must agree on what a body is, or the gate
    fails boards the placer calls legal (or passes ones it would not)."""
    from eda_agent.layout.place.placer import _footprint
    from eda_agent.layout.place.transform import _Frame

    b = _clean()
    for source in ("courtyard", "body", "primitives", "pads", ""):
        c, pads = _part("Q1", 300, 150, "X", "Y")
        c.courtyard_source = source
        b.components.append(c)
        b.pads += pads
        frame = _Frame(c.x, c.y, c.rotation, c.side)
        ids = [i for i, p in enumerate(b.pads) if p.comp == "Q1"]
        placer_bodies = [[frame.to_world(x, y) for x, y in poly]
                         for poly, kind in _footprint(b, c, frame, ids) if kind == "body"]
        mine = body_outline(c)
        if not placer_bodies:
            assert mine == [], source
        else:
            assert len(placer_bodies) == 1
            assert [(round(x, 6), round(y, 6)) for x, y in placer_bodies[0]] == \
                [(round(x, 6), round(y, 6)) for x, y in mine], source
        b.components.pop()
        del b.pads[-2:]


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

def test_a_missing_track_leaves_the_net_unrouted_with_its_pad_listed():
    b = _clean()
    b.tracks = b.tracks[:-1]        # the last leg into R2.1
    rep = connectivity_summary(b)
    assert rep["counts"]["routed"] == 0 and rep["counts"]["nets"] == 1
    assert rep["counts"]["partly_routed"] == 0 and rep["counts"]["not_started"] == 1
    assert not rep["pass"]
    assert rep["unreached"] == [{"net": "B", "pad": "R2.1", "layers": ["TopLayer"],
                                 "at": [480.0, 300.0]}]


def test_a_pad_with_no_copper_is_still_a_pad_to_reach():
    # Copper is what connectivity sees, so a pad the read gave no copper
    # is in no group at all; dropped, its net would read as routed.
    b = _clean()
    b.pads.append(Pad("R9", "1", 700, 100, net="B"))
    rep = connectivity_summary(b)
    assert rep["counts"]["routed"] == 0 and rep["counts"]["partly_routed"] == 1
    assert [u["pad"] for u in rep["unreached"]] == ["R9.1"]


def test_a_net_part_way_routed_is_partly_routed():
    b = _clean()
    _add(b, "R3", 800, 150, "B", "Z")       # a third pad on B, not reached
    b.regions = b.regions[1:]               # no pour under it
    rep = connectivity_summary(b)
    assert rep["counts"]["partly_routed"] == 1 and rep["partly_routed"] == ["B"]
    assert [u["pad"] for u in rep["unreached"]] == ["R3.1"]


# ---------------------------------------------------------------------------
# Corners
# ---------------------------------------------------------------------------

def _corner_board(route):
    b = _clean()
    b.tracks = []
    _route(b, route, "B")
    return b


def test_45_degree_bends_and_straight_runs_pass():
    rep = corner_audit(_corner_board(B_ROUTE + [(480, 300)]))
    assert rep["pass"] and rep["counts"]["junctions"] == 3
    rep = corner_audit(_corner_board([(220, 300), (300, 300), (480, 300)]))
    assert rep["pass"] and rep["counts"]["junctions"] == 1


def test_a_right_angle_corner_fails():
    rep = corner_audit(_corner_board([(220, 300), (300, 300), (300, 400), (480, 400)]))
    assert not rep["pass"]
    assert rep["counts"]["sharper_than_45"] == 2 and rep["counts"]["right_angle"] == 2
    assert {tuple(c["at"]) for c in rep["corners"]} == {(300.0, 300.0), (300.0, 400.0)}
    assert all(c["angle"] == pytest.approx(90.0) for c in rep["corners"])


def test_an_acute_corner_fails():
    # Out along x, then back at 30 degrees to it.
    back = (300 - 100 * math.cos(math.radians(30)), 300 + 100 * math.sin(math.radians(30)))
    rep = corner_audit(_corner_board([(220, 300), (300, 300), back]))
    assert rep["counts"]["sharper_than_45"] == 1 and rep["counts"]["acute"] == 1
    assert rep["corners"][0]["angle"] == pytest.approx(30.0)
    assert rep["corners"][0]["at"] == [300.0, 300.0]


def test_a_60_degree_bend_is_sharper_than_45_but_not_a_right_angle():
    end = (300 + 100 * math.cos(math.radians(60)), 300 + 100 * math.sin(math.radians(60)))
    rep = corner_audit(_corner_board([(220, 300), (300, 300), end]))
    assert rep["counts"]["sharper_than_45"] == 1
    assert rep["corners"][0]["kind"] == "steep"


def test_a_branch_at_a_right_angle_is_not_a_corner():
    b = _corner_board([(220, 300), (300, 300), (480, 300)])
    _route(b, [(300, 300), (300, 200)], "B")
    assert corner_audit(b)["pass"]


def test_tracks_of_different_nets_or_layers_make_no_junction():
    b = _corner_board([(220, 300), (300, 300)])
    _route(b, [(300, 300), (300, 400)], "Q")
    _route(b, [(300, 300), (300, 200)], "B", layer="BottomLayer")
    rep = corner_audit(b)
    assert rep["pass"] and rep["counts"]["junctions"] == 0


# ---------------------------------------------------------------------------
# Return vias
# ---------------------------------------------------------------------------

def test_a_signal_via_with_no_ground_via_near_is_an_exception():
    b = _clean()
    b.vias[1] = Via(370, 450, 20, 10, "TopLayer", "BottomLayer", "GND")   # 102 mil
    rep = return_via_audit(b)
    assert not rep["pass"] and rep["counts"]["exceptions"] == 1
    assert rep["exceptions"][0]["net"] == "B"
    assert rep["exceptions"][0]["at"] == [350.0, 350.0]
    assert rep["exceptions"][0]["nearest_return_mils"] == pytest.approx(101.98, abs=0.01)
    assert return_via_audit(b, max_distance_mils=110)["pass"]


def test_with_no_ground_via_at_all_the_nearest_is_none():
    b = _clean()
    del b.vias[1]
    rep = return_via_audit(b)
    assert rep["exceptions"][0]["nearest_return_mils"] is None


def test_a_via_of_a_plane_net_counts_as_a_return_via():
    b = _clean()
    b.vias[1] = Via(370, 380, 20, 10, "TopLayer", "BottomLayer", "REF0")
    assert not return_via_audit(b)["pass"]
    b.regions.append(Region("BottomLayer", _square(360, 370, 380, 390), [], "REF0", "pour"))
    assert return_via_audit(b)["pass"]


def test_the_scope_is_the_differential_pairs_when_the_board_has_them():
    b = _clean()
    b.vias.append(Via(700, 300, 20, 10, "TopLayer", "BottomLayer", "USB_P"))
    # Every signal via: the lone USB_P via has no return via near.
    assert return_via_audit(b)["counts"]["signal_vias"] == 2
    b.diff_pairs = [DiffPair("USB", "USB_P", "USB_N")]
    rep = return_via_audit(b)
    assert rep["counts"]["signal_vias"] == 1 and rep["exceptions"][0]["net"] == "USB_P"
    assert "differential" in rep["scope"]
    # Named nets win over the board's own scope.
    rep = return_via_audit(b, nets=["B"])
    assert rep["counts"]["signal_vias"] == 1 and rep["pass"]


def test_a_high_speed_class_sets_the_scope():
    b = _clean()
    b.vias.append(Via(700, 300, 20, 10, "TopLayer", "BottomLayer", "CK"))
    b.net_classes = {"Clock": ["CK"], "Slow": ["B"]}
    rep = return_via_audit(b)
    assert rep["counts"]["signal_vias"] == 1 and rep["exceptions"][0]["net"] == "CK"


# ---------------------------------------------------------------------------
# Pours and planes
# ---------------------------------------------------------------------------

def _split_pour(board):
    board.regions[0] = Region("BottomLayer", _square(600, 50, 760, 550), [], "GND", "pour")
    board.regions.insert(1, Region("BottomLayer", _square(790, 50, 950, 550), [], "GND", "pour"))


def test_a_pour_split_in_two_is_one_island_and_dead_copper():
    b = _clean()
    _split_pour(b)
    rep = plane_region_audit(b)
    assert not rep["pass"]
    assert rep["counts"]["pieces"] == 2 and rep["counts"]["islands"] == 1
    assert rep["counts"]["dead_islands"] == 1
    island = rep["islands"][0]
    assert island["layer"] == "BottomLayer" and island["net"] == "GND"
    assert island["area_sq_mils"] == pytest.approx(160 * 500)
    assert island["joined_elsewhere"] is False
    assert rep["nets"] == [{"layer": "BottomLayer", "net": "GND", "pieces": 2,
                            "one_piece": False}]


def test_a_track_on_the_layer_joins_the_two_pieces():
    b = _clean()
    _split_pour(b)
    b.tracks.append(Track("BottomLayer", 750, 300, 800, 300, 10, "GND"))
    rep = plane_region_audit(b)
    assert rep["pass"] and rep["nets"][0]["one_piece"]


def test_pieces_joined_through_another_layer_are_still_islands_but_not_dead():
    b = _clean()
    _split_pour(b)
    b.vias += [Via(700, 300, 20, 10, "TopLayer", "BottomLayer", "GND"),
               Via(850, 300, 20, 10, "TopLayer", "BottomLayer", "GND")]
    b.tracks.append(Track("TopLayer", 700, 300, 850, 300, 10, "GND"))
    rep = plane_region_audit(b)
    assert rep["counts"]["islands"] == 1 and rep["counts"]["dead_islands"] == 0
    assert rep["islands"][0]["joined_elsewhere"] is True


def test_a_split_plane_layer_is_judged_like_a_pour():
    b = _clean()
    b.layers = [Layer("TopLayer", "signal", 0), Layer("GND_PLANE", "plane", 1, "GND"),
                Layer("BottomLayer", "signal", 2)]
    b.regions = [Region("GND_PLANE", _square(0, 0, 480, 600), [], "GND", "plane"),
                 Region("GND_PLANE", _square(520, 0, 1000, 600), [], "GND", "plane")]
    rep = plane_region_audit(b)
    assert rep["counts"]["islands"] == 1, rep
    # The only ground via is in the left piece: the right one reaches
    # nothing.
    assert rep["islands"][0]["joined_elsewhere"] is False
    # A ground via in the right piece too. The DRC takes a plane as one
    # sheet joining every barrel of its net, so the island now reaches
    # the rest, and on its own layer it is still an island.
    b.vias.append(Via(700, 300, 20, 10, "TopLayer", "BottomLayer", "GND"))
    rep = plane_region_audit(b)
    assert rep["counts"]["islands"] == 1
    assert rep["islands"][0]["joined_elsewhere"] is True


def test_findings_are_cut_at_the_limit_but_counts_are_not():
    b = _clean()
    b.tracks = []
    for k in range(5):
        _add(b, f"T{k}", 100 + 150 * k, 500, f"N{k}", f"M{k}", body=False)
        b.pads.append(Pad(f"T{k}", "3", 100 + 150 * k, 560, net=f"N{k}",
                          copper=[PadCopper("TopLayer", "rect", 10, 10)]))
    b.regions = []
    rep = connectivity_summary(b, limit=2)
    assert rep["counts"]["unreached_pads"] == 6 and len(rep["unreached"]) == 2
    assert rep["truncated"] is True


def test_audits_do_not_change_the_board():
    b = _clean()
    _split_pour(b)
    before = copy_board(b).to_dict()
    run_audits(b)
    assert b.to_dict() == before


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_tool():
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    from eda_agent.tools.design import register_design_tools
    register_design_tools(_Mcp())
    return captured["pcb_layout_audit"]


def _run(fn, **kw):
    import asyncio
    return asyncio.run(fn(**kw))


def test_the_tool_audits_a_saved_board(audit_tool, tmp_path):
    b = _clean()
    _add(b, "R3", 200, 325, "D", "E")
    path = tmp_path / "board.json.gz"
    b.save(path)
    out = _run(audit_tool, board_json_path=str(path))
    assert out["source"] == "file" and out["board"] == "audit"
    assert out["pass"] is False
    assert out["summary"]["placement_audit"]["overlaps"] == 1
    out = _run(audit_tool, board_json_path=str(path), checks=["corner_audit"])
    assert list(out["checks"]) == ["corner_audit"] and out["pass"] is True


def test_the_tool_reads_the_live_board_through_the_engine_read(audit_tool, monkeypatch):
    import eda_agent.layout.read_altium as reader

    asked = []

    def fake_read(expect_file, page=reader.PAGE):
        asked.append(expect_file)
        return _clean()

    monkeypatch.setattr(reader, "read_live_board", fake_read)
    out = _run(audit_tool, expect_file="C:/boards/x.PcbDoc")
    assert asked == ["C:/boards/x.PcbDoc"]
    assert out["source"] == "live" and out["pass"] is True

    def wrong(expect_file, page=reader.PAGE):
        raise reader.WrongBoard("asked for x but y is focused")

    monkeypatch.setattr(reader, "read_live_board", wrong)
    assert _run(audit_tool, expect_file="C:/boards/x.PcbDoc") == {
        "error": "asked for x but y is focused"}


def test_the_tool_refuses_before_reading_anything(audit_tool, monkeypatch, tmp_path):
    import eda_agent.layout.read_altium as reader

    def boom(*a, **k):
        raise AssertionError("read the board")

    monkeypatch.setattr(reader, "read_live_board", boom)
    assert "error" in _run(audit_tool)
    assert "unknown check" in _run(audit_tool, expect_file="x", checks=["nope"])["error"]
    assert "no such file" in _run(audit_tool, board_json_path=str(tmp_path / "x.json"))["error"]
