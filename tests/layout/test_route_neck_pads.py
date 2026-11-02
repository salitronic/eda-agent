# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""With neck_pads a net is routed at its rule's width and necks down only
on the stub into a pad narrower than its track."""
from __future__ import annotations

from eda_agent.layout import geom
from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.route import Router
from eda_agent.layout.route.stages import StageOptions


def _board() -> LayoutBoard:
    rules = [
        Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
             "Clearance Constraint (Gap=6mil) (All),(All)"),
        Rule("Width", "1", "All", "All", 1, True, {},
             "Width Constraint (Min=6mil) (Max=30mil) (Preferred=20mil) (All)"),
        Rule("RoutingVias", "2", "All", "All", 1, True, {},
             "Routing Via (MinHoleWidth=10mil) (MaxHoleWidth=10mil) (PreferredHoleWidth=10mil) "
             "(MinWidth=20mil) (MaxWidth=20mil) (PreferedWidth=20mil) (All)"),
    ]
    b = LayoutBoard(name="t", outline=[(0, 0), (800, 0), (800, 500), (0, 500)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    # A supply pin of an IC, 12 mil wide, and a big pad far off.
    for k, x in enumerate((200, 250, 300, 350), 1):
        b.pads.append(Pad("U1", str(k), x, 250, net="VIN" if k == 2 else f"N{k}",
                          copper=[PadCopper("TopLayer", "rect", 12.0, 60.0)]))
    b.pads.append(Pad("J1", "1", 650, 400, net="VIN",
                      copper=[PadCopper("TopLayer", "rect", 80.0, 80.0)]))
    return b


def _routed(neck: bool) -> LayoutBoard:
    r = Router(_board(), stages=StageOptions(neck_pads=neck))
    r.run()
    return r.apply()


def test_the_net_keeps_its_width_and_necks_only_into_the_narrow_pin():
    out = _routed(True)
    drc = run_drc(out)
    assert drc.completion == 1.0 and not drc.violations
    vin = [t for t in out.tracks if t.net == "VIN"]
    assert {round(t.width, 3) for t in vin} == {12.0, 20.0}
    pin = next(p for p in out.pads if p.comp == "U1" and p.name == "2")
    s = pin.shape_on("TopLayer")
    for t in vin:
        if t.width == 20.0:
            assert geom.clearance(t.shape(), s) >= 0     # full width never on the pin
        else:
            near = min(geom.clearance(geom.circle(x, y, 0.0), s) for x, y in
                       ((t.x1, t.y1), (t.x2, t.y2)))
            assert near <= 30.0                          # the neck is the stub at the pin


def test_without_it_the_narrowest_pad_sets_the_width_all_the_way():
    out = _routed(False)
    assert run_drc(out).completion == 1.0
    assert {round(t.width, 3) for t in out.tracks if t.net == "VIN"} == {12.0}


def test_a_narrow_pad_no_stub_reaches_routes_the_net_at_its_width():
    b = _board()
    # A round pad has no axis to leave along: the net falls back to it.
    pin = next(p for p in b.pads if p.net == "VIN" and p.comp == "U1")
    pin.copper = [PadCopper("TopLayer", "round", 12.0, 12.0)]
    r = Router(b, stages=StageOptions(neck_pads=True))
    r.run()
    out = r.apply()
    drc = run_drc(out)
    assert drc.completion == 1.0 and not drc.violations
    assert {round(t.width, 3) for t in out.tracks if t.net == "VIN"} == {12.0}
