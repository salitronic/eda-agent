# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Current loops in the brief: named pad by pad, measured on the routed
copper in each round's review, and a finding when one encloses more than
its source allows.

A converter's input loop: the capacitor C1 between VIN and GND, the chip U1
taking VIN at pin 1 and returning it at pin 3, 60 mil below.
"""

from __future__ import annotations

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout.model import Component, Layer, LayoutBoard, Pad, PadCopper, Region, Rule
from eda_agent.layout.route import brief as B
from eda_agent.layout.route import review
from eda_agent.layout.route.job import route_job

SMD = [PadCopper("TopLayer", "rect", 20, 20)]
HOT = {"name": "input", "kind": "hot_loop", "members": ["C1.1", "U1.1", "U1.3", "C1.2"],
       "max_area": 5000, "source": "regulator datasheet, layout section"}


def _board(cap_x: float) -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=10mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    b.components = [Component("C1", x=cap_x, y=170), Component("U1", x=300, y=170)]
    b.pads = [Pad("C1", "1", cap_x, 200, net="VIN", copper=list(SMD)),
              Pad("C1", "2", cap_x, 140, net="GND", copper=list(SMD)),
              Pad("U1", "1", 300, 200, net="VIN", copper=list(SMD)),
              Pad("U1", "3", 300, 140, net="GND", copper=list(SMD))]
    return b


def _round(board):
    store = get_job_store()
    jid = store.submit("layout_route", route_job, {
        "board": board, "review": True, "advise": False, "nets": ["VIN", "GND"],
        "brief": {"entries": [{"nets": ["VIN", "GND"], "layers": ["TopLayer"],
                               "source": "test"}], "loops": [HOT]}})
    assert store.wait(jid, timeout=120)
    rec = store.get(jid)
    assert rec.status == "done", rec.summary()
    return rec.result


def test_a_loop_over_its_max_area_is_a_finding_and_a_tight_one_is_not():
    far = _round(_board(100))
    m = far["review"]["loops"][0]
    assert m["name"] == "input" and m["complete"] and m["area"] > 5000
    f = next(f for f in far["findings"] if f["check"] == "loop")
    assert f["id"] == "loop:input" and f["limit"] == 5000 and "nH" in f["text"]
    near = _round(_board(250))
    m = near["review"]["loops"][0]
    assert m["complete"] and 0 < m["area"] <= 5000
    assert not [f for f in near["findings"] if f["check"] == "loop"]


def test_a_poured_return_is_taken_straight_under_the_path():
    b = _board(100)
    b.regions = [Region("BottomLayer", [(0, 0), (600, 0), (600, 400), (0, 400)],
                        net="GND", kind="pour")]
    brief = B.Brief.from_list([], [], [HOT])
    got = review.loop_measures(b, b, brief, ["VIN"])
    # VIN unrouted: one hop of two follows no copper.
    assert got[0]["routed_hops"] == "1/2" and not got[0]["complete"]
    assert review.loop_measures(b, b, brief, ["NOT_IN_IT"]) == []


def test_the_brief_keeps_its_loops():
    brief = B.Brief.from_list([], [], [HOT])
    assert brief and brief.loops_list()[0]["members"] == HOT["members"]


@pytest.mark.parametrize("change, says", [
    ({"name": ""}, "give it a name"),
    ({"source": ""}, "give its source"),
    ({"max_area": None}, "give max_area"),
    ({"members": ["C1.1", "U1.1"]}, "needs 3 pads"),
    ({"members": ["C1.1", "U1.1", "U9.3"]}, "no pad"),
    ({"members": ["C1.1", "U1.3", "U1.1", "C1.2"]}, "is neither one part nor one net"),
])
def test_a_loop_that_cannot_be_measured_is_refused(change, says):
    got = B.check(_board(100), B.Brief.from_list([], [], [{**HOT, **change}]))
    assert any(says in e for e in got["errors"]), got["errors"]


def test_a_brief_read_lists_the_loops_the_board_shows_until_one_is_named():
    from eda_agent.tools.route import _loops_found

    b = _board(100)
    found = _loops_found(b, B.Brief())
    assert found[0]["members"] == ["C1.1", "U1.1", "U1.3", "C1.2"]
    assert found[0]["area_sq_mils"] == pytest.approx(12000)
    assert _loops_found(b, B.Brief.from_list([], [], [HOT])) == []
