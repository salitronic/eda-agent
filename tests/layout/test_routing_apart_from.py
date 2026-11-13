# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Keeping a net away from the nets that would couple into it: the router
keeps the brief's spacing as it keeps a clearance, and the review measures
the gap and how much of the net runs beside them.

V runs from (100, 100) to (500, 100). S, a short noisy net, runs beside the
straight way, 40 mil off it.
"""

from __future__ import annotations

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout import geom
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.route import brief as B
from eda_agent.layout.route.job import route_job
from eda_agent.layout.route.params import RouteRules
from eda_agent.layout.rules import RuleSet

SMD = [PadCopper("TopLayer", "rect", 20, 20)]


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=6mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    b.pads = [Pad("R1", "1", 100, 100, net="V", copper=list(SMD)),
              Pad("R2", "1", 500, 100, net="V", copper=list(SMD)),
              Pad("L1", "1", 250, 140, net="S", copper=list(SMD)),
              Pad("L2", "1", 350, 140, net="S", copper=list(SMD))]
    return b


def _route(brief):
    store = get_job_store()
    jid = store.submit("layout_route", route_job, {
        "board": _board(), "review": True, "advise": False, "nets": ["S", "V"],
        "brief": brief})
    assert store.wait(jid, timeout=120)
    rec = store.get(jid)
    assert rec.status == "done", rec.summary()
    return rec.result


def _gap(result, a="V", b="S") -> float:
    ts = result["tracks"]
    return min(geom.clearance(geom.capsule(t["x1"], t["y1"], t["x2"], t["y2"], t["width"]),
                              geom.capsule(u["x1"], u["y1"], u["x2"], u["y2"], u["width"]))
               for t in ts if t["net_name"] == a for u in ts
               if u["net_name"] == b and u["layer"] == t["layer"])


TOP = {"layers": ["TopLayer"], "source": "test"}


def test_the_router_keeps_the_spacing_the_brief_asks():
    plain = _route([{"nets": ["V"], **TOP}, {"nets": ["S"], **TOP}])
    assert _gap(plain) < 40, "the straight way passes S closely"
    kept = _route([{"nets": ["V"], "apart_from": ["S"], "spacing": 60, **TOP},
                   {"nets": ["S"], **TOP}])
    assert {t["net_name"] for t in kept["tracks"]} == {"V", "S"}
    assert _gap(kept) >= 60 - 0.5
    assert not [f for f in kept["findings"] if f["check"] == "apart"]


def test_a_run_beside_the_noisy_net_over_max_parallel_is_a_finding():
    got = _route([{"nets": ["V"], "apart_from": ["S"], "max_parallel": 40,
                   "parallel_gap": 45, **TOP}, {"nets": ["S"], **TOP}])
    f = next(f for f in got["findings"] if f["check"] == "apart")
    assert f["id"] == "apart:V:S" and f["parallel"] > 40
    assert "crosstalk simulation" in f["text"]


def test_the_spacing_is_a_clearance_between_those_two_nets_alone():
    b = _board()
    b.pads.append(Pad("R3", "1", 300, 300, net="W", copper=list(SMD)))
    net_id = {n: i + 1 for i, n in enumerate(b.nets())}
    rr = RouteRules(b, RuleSet.from_board(b), net_id)
    rr.spacing = B.Brief.from_list([{"nets": ["V"], "apart_from": ["S"], "spacing": 60,
                                     "source": "test"}]).spacing_pairs()
    assert rr.pair_clearance(net_id["V"], net_id["S"]) == 60
    assert rr.pair_clearance(net_id["S"], net_id["V"]) == 60
    assert rr.pair_clearance(net_id["V"], net_id["W"]) == 6
    assert rr.pair_clearance(net_id["S"], net_id["W"]) == 6
    assert rr.group(net_id["V"]) != rr.group(net_id["W"])


@pytest.mark.parametrize("entry, says", [
    ({"spacing": 20}, "need apart_from"),
    ({"apart_from": ["NOPE"], "spacing": 20}, "names no net"),
    ({"apart_from": ["V"], "spacing": 20}, "its own nets"),
    ({"apart_from": ["S"]}, "needs spacing, max_parallel or max_broadside"),
    ({"apart_from": ["S"], "max_parallel": 100}, "go together"),
    ({"apart_from": ["S"], "spacing": 30, "max_parallel": 100, "parallel_gap": 20},
     "is not over spacing"),
])
def test_an_entry_that_cannot_be_measured_is_refused(entry, says):
    got = B.check(_board(), B.Brief.from_list([{"nets": ["V"], "source": "test", **entry}]))
    assert any(says in e for e in got["errors"]), got["errors"]
