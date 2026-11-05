# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A new via leaves no solder mask web narrower than the board's sliver
rule, and its opening does not run into a passive's pad."""
from __future__ import annotations

from eda_agent.layout.drc import usual_via_mask, verdict
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule, Track, Via
from eda_agent.layout.route import Router

RULES = [
    Rule("Clearance", "clearance", "All", "All", 1, True, {"gap": 6.0},
         "Clearance Constraint (Gap=6mil) (All),(All)"),
    Rule("Sliver", "mask_sliver", "All", "All", 1, True, {},
         "Minimum Solder Mask Sliver (Gap=10mil) (All),(All)"),
    Rule("Mask", "soldermask", "All", "", 1, True, {},
         "Solder Mask Expansion (Expansion =4mil) (All)"),
]


def _board(mask_read: bool = True) -> LayoutBoard:
    b = LayoutBoard(name="t", outline=[(0, 0), (600, 0), (600, 500), (0, 500)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=list(RULES))
    pad = lambda comp, num, x, y, net: Pad(comp, num, x, y, net=net,
                                           copper=[PadCopper("TopLayer", "rect", 30.0, 30.0)])
    # A on two resistors either side of a wall of X on the top layer,
    # each pad close to it: A must drop through a via beside each pad.
    b.pads = [pad("R1", "1", 300, 200, "A"), pad("R1", "2", 300, 120, "N1"),
              pad("R2", "1", 300, 300, "A"), pad("R2", "2", 300, 380, "N2")]
    b.tracks = [Track("TopLayer", 0, 250, 600, 250, 10.0, "X")]
    b.meta["mask_read"] = mask_read
    return b


def _routed(board: LayoutBoard) -> LayoutBoard:
    r = Router(board)
    r.run()
    return r.apply()


def test_no_via_leaves_a_mask_sliver():
    out = _routed(_board())
    v = verdict(out)
    assert v["report"].completion == 1.0
    assert [x for x in out.vias if x.net == "A"]
    assert v["counts"]["mask_sliver"] == 0, [x.as_dict() for x in v["violations"]]


def test_a_via_opening_keeps_off_a_passives_pad():
    from eda_agent.layout import geom
    out = _routed(_board())
    for x in out.vias:
        for p in out.pads:
            # The two openings and the web between them: 25 + 4 + 4 + 10.
            gap = geom.clearance(geom.circle(x.x, x.y, x.diameter), p.shape_on("TopLayer"))
            assert gap >= 4 + 4 + 10 - 1e-6, (p.comp, p.name, gap)


def test_tented_vias_need_no_room():
    b = _board()
    b.vias = [Via(50, 50, 20.0, 10.0, "TopLayer", "BottomLayer", "N1")]
    b.vias[0].tent_top = b.vias[0].tent_bottom = True
    assert usual_via_mask(b)[:2] == (True, True)
    r = Router(b)
    assert all(len(r._mask_webs(v)) == 0 for v in r.rr.vias("A"))


def test_without_the_mask_read_nothing_is_kept_out():
    b = _board(mask_read=False)
    assert usual_via_mask(b) is None
    r = Router(b)
    assert all(len(r._mask_webs(v)) == 0 for v in r.rr.vias("A"))


def test_the_fanout_check_and_the_grid_agree():
    r = Router(_board())
    v = r.rr.vias("A")[0]
    # R1.1 is 30 mil square at (300, 200): its copper ends at x = 315.
    need = v.diameter / 2 + 4 + 4 + 10
    assert abs(r.pad_web(v, 0) - need) < 1e-6
    assert r.via_leaves_web(v, 315 + need - 1, 200)
    assert not r.via_leaves_web(v, 315 + need + 1, 200)
    i, j = r.grid.spec.cell(315 + need - 1, 200)
    assert (j, i) in {tuple(c) for c in r._mask_webs(v).tolist()}
