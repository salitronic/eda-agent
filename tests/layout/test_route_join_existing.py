# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""With join_existing the router routes only what the board's own copper
leaves apart."""
from __future__ import annotations

from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule, Track
from eda_agent.layout.route import Router
from eda_agent.layout.route.stages import StageOptions


def _board() -> LayoutBoard:
    rules = [
        Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
             "Clearance Constraint (Gap=6mil) (All),(All)"),
        Rule("Width", "1", "All", "All", 1, True, {},
             "Width Constraint (Min=6mil) (Max=6mil) (Preferred=6mil) (All)"),
        Rule("RoutingVias", "2", "All", "All", 1, True, {},
             "Routing Via (MinHoleWidth=10mil) (MaxHoleWidth=10mil) (PreferredHoleWidth=10mil) "
             "(MinWidth=20mil) (MaxWidth=20mil) (PreferedWidth=20mil) (All)"),
    ]
    b = LayoutBoard(name="t", outline=[(0, 0), (700, 0), (700, 500), (0, 500)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    pad = lambda comp, x, y, net: Pad(comp, "1", x, y, net=net,
                                      copper=[PadCopper("TopLayer", "rect", 20.0, 20.0)])
    b.pads = [pad("P1", 100, 100, "A"), pad("P2", 400, 100, "A"), pad("P3", 250, 300, "A"),
              pad("Q1", 100, 420, "B"), pad("Q2", 600, 420, "B")]
    # A is joined from P1 to P2 by hand; B is joined whole.
    b.tracks = [Track("TopLayer", 100, 100, 400, 100, 6.0, "A"),
                Track("TopLayer", 100, 420, 600, 420, 6.0, "B")]
    return b


def _routed(join: bool) -> tuple[LayoutBoard, int]:
    b = _board()
    before = len(b.tracks)
    r = Router(b, stages=StageOptions(join_existing=join))
    r.run()
    return r.apply(), before


def test_only_what_the_copper_leaves_apart_is_routed():
    out, before = _routed(True)
    drc = run_drc(out)
    assert drc.completion == 1.0 and not drc.violations
    new = out.tracks[before:]
    assert new and not [t for t in new if t.net == "B"]
    # The one piece left, P3, meets A's copper: nothing new reaches P1 or
    # P2, which the copper already joins.
    from eda_agent.layout import geom
    for p in out.pads[:2]:
        assert all(geom.clearance(t.shape(), p.shape_on("TopLayer")) > 0 for t in new), p.comp


def test_a_net_the_copper_joins_whole_is_no_job():
    r = Router(_board(), stages=StageOptions(join_existing=True))
    assert {j.name for j in r.jobs} == {"A"}


def test_without_it_a_joined_net_is_routed_again():
    out, before = _routed(False)
    assert [t for t in out.tracks[before:] if t.net == "B"]


def _four_layers() -> LayoutBoard:
    b = _board()
    b.layers = [Layer("TopLayer", "signal", 0), Layer("MidLayer1", "signal", 1),
                Layer("MidLayer2", "signal", 2), Layer("BottomLayer", "signal", 3)]
    b.tracks = []
    return b


def test_an_excluded_layer_carries_no_track():
    import pytest
    b = _four_layers()
    r = Router(b, stages=StageOptions(exclude_layers=("MidLayer1",)))
    r.run()
    out = r.apply()
    assert run_drc(out).completion == 1.0
    assert "MidLayer1" not in r.grid.layers
    assert not [t for t in out.tracks if t.layer == "MidLayer1"]
    with pytest.raises(ValueError, match="surface pad"):
        Router(_four_layers(), stages=StageOptions(exclude_layers=("TopLayer",)))
    with pytest.raises(ValueError, match="no copper layer"):
        Router(_four_layers(), stages=StageOptions(exclude_layers=("Nope",)))


def _through(top_x, bottom_x, y=250):
    """Net A from a pad on top to one on the bottom: one layer change."""
    b = _four_layers()
    b.pads = [Pad("P1", "1", top_x, y, net="A", copper=[PadCopper("TopLayer", "rect", 20, 20)]),
              Pad("P2", "1", bottom_x, y, net="A",
                  copper=[PadCopper("BottomLayer", "rect", 20, 20)])]
    return b


def _verdict(out):
    from eda_agent.layout.drc import verdict
    return verdict(out)


def test_a_via_clears_copper_on_a_layer_no_route_uses():
    from eda_agent.layout.model import Region
    b = _through(150, 550)
    # MidLayer1, not routed on, is another net's copper but for a window.
    b.regions = [Region("MidLayer1", [(20, 20), (680, 20), (680, 480), (20, 480)],
                        holes=[[(320, 200), (380, 200), (380, 300), (320, 300)]], net="Z")]
    r = Router(b, stages=StageOptions(exclude_layers=("MidLayer1",)))
    r.run()
    out = r.apply()
    v = _verdict(out)
    assert v["report"].completion == 1.0 and not v["violations"], v["violations"][:2]
    assert out.vias and all(320 < x.x < 380 for x in out.vias)


def test_a_new_via_keeps_the_hole_to_hole_gap_from_another_nets_via():
    from eda_agent.layout.drc import hole_gaps
    from eda_agent.layout.model import Via
    b = _through(150, 550)
    b.rules.append(Rule("HoleToHole", "9", "All", "All", 1, True, {},
                        "Hole To Hole Clearance (Allow Stacked Micro Vias=No) (Gap=40mil) "
                        "(All),(All)"))
    # Another net's vias in a row 30 mil off the straight path: copper
    # clearance allows a via on the path, the hole gap allows none there.
    b.vias = [Via(x, 280, 16, 8, "TopLayer", "BottomLayer", "Z") for x in range(110, 600, 60)]
    r = Router(b)
    r.run()
    out = r.apply()
    assert run_drc(out).completion == 1.0
    assert not hole_gaps(out)


def test_a_route_changes_layer_through_its_own_via_already_there():
    from eda_agent.layout.model import Via
    b = _through(150, 550)
    b.vias = [Via(350, 250, 20, 10, "TopLayer", "BottomLayer", "A")]
    r = Router(b, stages=StageOptions(reuse_vias=True))
    r.run()
    out = r.apply()
    assert run_drc(out).completion == 1.0
    assert len(out.vias) == 1            # the one that was there
