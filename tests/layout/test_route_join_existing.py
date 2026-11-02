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
