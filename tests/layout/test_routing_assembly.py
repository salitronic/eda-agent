# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Assembly in the brief: the router keeps vias off surface pads and from
under part bodies, and the review names vias, part gaps and orientation
that break the AI's rules.

The board: two pads of S, 400 mil apart on the top, with the top between
them barred, so S changes layer at both ends.
"""

from __future__ import annotations

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout import geom
from eda_agent.layout.model import Component, Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.route import assembly as A
from eda_agent.layout.route import brief as B
from eda_agent.layout.route import review
from eda_agent.layout.route.job import route_job

SMD = [PadCopper("TopLayer", "rect", 20, 20)]
SRC = "assembler's design rules, via to pad"
WALL = {"name": "wall", "polygons": [[[130, 0], [470, 0], [470, 400], [130, 400]]],
        "layers": ["TopLayer"], "source": "test"}


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=8mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    b.components = [Component("R1", x=100, y=200), Component("R2", x=500, y=200)]
    b.pads = [Pad("R1", "1", 100, 200, net="S", copper=list(SMD)),
              Pad("R2", "1", 500, 200, net="S", copper=list(SMD))]
    return b


def _route(b: LayoutBoard, assembly=None) -> dict:
    brief = {"entries": [{"nets": ["S"], "source": "test"}], "areas": [WALL]}
    if assembly:
        brief["assembly"] = assembly
    store = get_job_store()
    jid = store.submit("layout_route", route_job, {"board": b, "review": True, "advise": False,
                                                   "nets": ["S"], "brief": brief})
    assert store.wait(jid, timeout=120)
    return store.get(jid).result


def _gaps(b: LayoutBoard, vias) -> list[float]:
    pads = [p.shape_on("TopLayer") for p in b.pads]
    return [min(geom.clearance(geom.circle(v["x"], v["y"], v["size"]), s) for s in pads)
            for v in vias]


def test_the_router_keeps_vias_the_rule_away_from_surface_pads():
    plain = _route(_board())
    assert len(plain["vias"]) >= 2 and min(_gaps(_board(), plain["vias"])) < 40
    res = _route(_board(), {"via_to_pad": 40, "source": SRC})
    assert len(res["vias"]) >= 2 and not res["unrouted"]
    assert min(_gaps(_board(), res["vias"])) >= 40 - 0.5
    assert not [f for f in res["findings"] if f["check"] == "assembly"]


def test_the_router_keeps_vias_from_under_a_body():
    b = _board()
    b.components.append(Component("U5", x=150, y=200, courtyard=[
        (120, 120), (190, 120), (190, 280), (120, 280)], courtyard_source="courtyard"))
    res = _route(b, {"vias_under_parts": False, "source": SRC})
    body = geom.polygon([(120, 120), (190, 120), (190, 280), (120, 280)])
    assert res["vias"] and not res["unrouted"]
    for v in res["vias"]:
        assert geom.clearance(geom.circle(v["x"], v["y"], v["size"]), body) > 0


def test_a_part_whose_pads_take_vias_is_left_out():
    # A BGA's balls or an exposed pad: the router passes them as via pads.
    b = _board()
    b.components.append(Component("U9", x=300, y=300))
    b.pads += [Pad("U9", "EP", 300, 300, net="GND",
                   copper=[PadCopper("TopLayer", "rect", 120, 120)]),
               Pad("U9", "1", 380, 300, net="X", copper=list(SMD))]
    free = {2}
    rule = B.AssemblyRule(via_to_pad=20, vias_under_parts=False, source=SRC)
    owners = {bar[3] for bar in A.via_bars(b, rule, free)}
    assert "R1.1" in owners and not any(o.startswith("U9") for o in owners)


def test_the_review_names_vias_gaps_and_orientation():
    b = _board()
    b.components = [Component("R1", x=100, y=200, rotation=0,
                              courtyard=[(80, 190), (120, 190), (120, 210), (80, 210)],
                              courtyard_source="courtyard"),
                    Component("R2", x=130, y=200, rotation=90,
                              courtyard=[(125, 190), (135, 190), (135, 210), (125, 210)],
                              courtyard_source="courtyard")]
    b.pads.append(Pad("R2", "2", 130, 230, net="GND", copper=list(SMD)))
    brief = B.Brief.from_list(
        [], assembly={"via_to_pad": 30, "part_gap": 10, "source": SRC},
        orient=[{"name": "row", "parts": ["R1", "R2"], "source": "assembly drawing"},
                {"name": "pol", "pins": {"R2.2": "S"}, "source": "datasheet polarity"}])
    vias = [{"x": 125, "y": 200, "size": 20, "net": "S"}]
    got = {f["id"]: f for f in review.assembly_findings(b, vias, brief, ["S"])}
    assert set(got) == {"assembly:via:S:R1.1", "assembly:gap:R1:R2",
                        "orient:row:R2:rotation", "orient:pol:R2:pin:2"}
    assert got["assembly:gap:R1:R2"]["measured"] == pytest.approx(5)
    # At the rotation given, both are measured against it.
    at = B.OrientRule.from_dict({"name": "at", "parts": ["R1", "R2"], "rotation": 450,
                                 "source": "x"})
    assert [m[1] for m in A.orient_misses(b, [at])] == ["R1"]


@pytest.mark.parametrize("assembly, orient, says", [
    ({"source": SRC}, [], "give via_to_pad"),
    ({"via_to_pad": -1, "source": SRC}, [], "cannot be negative"),
    ({"via_to_pad": 10}, [], "give its source"),
    (None, [{"name": "o", "parts": ["R9"], "rotation": 0, "source": "x"}], "no part"),
    (None, [{"name": "o", "parts": ["R1"], "source": "x"}], "two parts or more"),
    (None, [{"name": "o", "pins": {"R1.9": "S"}, "source": "x"}], "no pad R1.9"),
    (None, [{"name": "o", "pins": {"R1.1": "NOPE"}, "source": "x"}], "no net 'NOPE'"),
    (None, [{"name": "o", "parts": ["R1", "R2"]}], "give its source"),
])
def test_an_assembly_rule_that_cannot_be_kept_is_refused(assembly, orient, says):
    got = B.check(_board(), B.Brief.from_list([], assembly=assembly, orient=orient))
    assert any(says in e for e in got["errors"]), got["errors"]


def test_the_rules_are_saved_with_the_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_folder", lambda: tmp_path)
    brief = B.Brief.from_list([], assembly={"via_to_pad": 10, "vias_under_parts": False,
                                            "source": SRC},
                              orient=[{"name": "o", "parts": ["R1", "R2"], "source": "x"}])
    B.save("C:/x/demo.PcbDoc", brief)
    back = B.load("C:/x/demo.PcbDoc")
    assert back.assembly == brief.assembly and back.orient == brief.orient
    assert back.keeping([]).assembly == brief.assembly
