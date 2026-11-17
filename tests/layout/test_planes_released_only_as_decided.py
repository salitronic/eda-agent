# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which inner layers stay planes is the AI's decision.

route_adaptive gives a plane back to routing while connections stay
unmade. With ``release`` it gives only the layers named there; when the
plane it would give next is not among them it stops and names that plane,
its net and what is left unmade, for the AI to decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from eda_agent.layout.model import Layer, LayoutBoard
from eda_agent.layout.route import router as R

ORDER = ["TopLayer", "MidLayer1", "MidLayer2", "MidLayer3", "BottomLayer"]


@dataclass
class _Plane:
    layer: str
    net: str


class _Stub:
    """Fails 4 connections with every plane kept, none once MidLayer2 is
    given to routing; MidLayer1 alone closes 2. ``aborts``: each run's
    stall_abort."""
    aborts: list = []

    def __init__(self, board, released=frozenset(), log=None, **kw):
        self.board = board
        self.released = frozenset(released)
        self.pour_planes = [_Plane(l, "GND") for l in ("MidLayer1", "MidLayer2", "MidLayer3")
                            if l not in self.released]
        self.declined = None

    def run(self, stall_abort=False, warm=None):
        _Stub.aborts.append(stall_abort)
        failed = 0 if "MidLayer2" in self.released else 2 if "MidLayer1" in self.released else 4
        return SimpleNamespace(failed=failed, unsettled=False)


def _board():
    return LayoutBoard(name="t", layers=[Layer(n, "signal", i) for i, n in enumerate(ORDER)])


def _route(monkeypatch, release):
    monkeypatch.setattr(R, "Router", _Stub)
    # The second pass without the constraining stages is not what this is about.
    monkeypatch.setattr(R, "_fallback", lambda board, best, log, kw: best)
    monkeypatch.setattr(LayoutBoard, "copper_layers", lambda self: list(ORDER))
    monkeypatch.setattr(LayoutBoard, "pads_by_net", lambda self: {})
    return R.route_adaptive(_board(), release=release)


def test_any_plane_may_go_when_nothing_is_decided(monkeypatch):
    r = _route(monkeypatch, None)
    assert "MidLayer2" in r.released and r.report.failed == 0 and r.declined is None


def test_a_plane_not_named_is_kept_and_named_for_the_ai(monkeypatch):
    r = _route(monkeypatch, [])
    assert not r.released and r.report.failed == 4
    assert r.declined == {"layer": "MidLayer2", "net": "GND", "unrouted": 4}


def test_only_the_layers_named_are_given(monkeypatch):
    r = _route(monkeypatch, ["TopLayer", "MidLayer1"])
    assert r.released == {"MidLayer1"} and r.report.failed == 2
    # The plane named is the one the router would give next, with what is
    # unmade: MidLayer2, since MidLayer3 is the bottom layer's reference.
    assert r.declined == {"layer": "MidLayer2", "net": "GND", "unrouted": 2}
    r = _route(monkeypatch, ["MidLayer2"])
    assert r.released == {"MidLayer2"} and r.report.failed == 0 and r.declined is None


def test_the_tools_allow_the_layers_the_brief_names_unless_discipline_is_off():
    from eda_agent.layout.route.brief import Brief
    from eda_agent.tools.route import _release

    b = Brief.from_list([{"nets": ["A"], "layers": ["TopLayer", "MidLayer2"], "reason": "r"},
                         {"nets": ["B"], "reason": "no layers named"}])
    assert _release(b, "off") is None
    assert _release(b, "enforced") == ["MidLayer2", "TopLayer"]
    assert _release(b, "advisory") == ["MidLayer2", "TopLayer"]
    assert _release(Brief(), "enforced") == []


def test_a_run_stops_early_on_a_stall_only_when_a_plane_may_follow(monkeypatch):
    _Stub.aborts.clear()
    _route(monkeypatch, [])
    # Nothing may be given: the one run is not cut short for a release that
    # will not come.
    assert _Stub.aborts == [False]
    _Stub.aborts.clear()
    _route(monkeypatch, None)
    assert _Stub.aborts[0] is True


def test_a_plane_next_to_an_outer_layer_goes_after_a_supply_layer(monkeypatch):
    from eda_agent.layout.model import Pad

    class Six(_Stub):
        # Top, GND, GND, supply, GND, Bottom; the middle GND gone already.
        def __init__(self, board, released=frozenset(), log=None, **kw):
            super().__init__(board, released, log, **kw)
            self.pour_planes = [_Plane("MidLayer1", "GND"), _Plane("MidLayer3", "GND"),
                                _Plane("MidLayer2", "+3V3")]
            self.pour_planes = [p for p in self.pour_planes if p.layer not in self.released]

    monkeypatch.setattr(LayoutBoard, "copper_layers", lambda self: list(ORDER))
    monkeypatch.setattr(LayoutBoard, "pads_by_net", lambda self: {"+3V3": [Pad("U1", "1", 0, 0)]})
    r = Six(_board())
    # GND's two planes both face an outer layer; the supply layer does not.
    assert R.releasable(r) == "MidLayer2"
