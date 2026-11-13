# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_route_brief keeps the AI's brief; pcb_route_round routes a round
under it and stops for review.

Offline: the live board read is replaced by a synthetic board and the
brief is kept in a temporary workspace.
"""

from __future__ import annotations

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule

BOARD_FILE = r"C:\boards\demo\demo.PcbDoc"


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=6mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    smd = [PadCopper("TopLayer", "rect", 20, 20)]
    b.pads = [Pad("R1", "1", 100, 300, net="A", copper=list(smd)),
              Pad("R2", "1", 500, 300, net="A", copper=list(smd)),
              Pad("Y1", "1", 100, 100, net="XIN", copper=list(smd)),
              Pad("U1", "1", 300, 100, net="XIN", copper=list(smd))]
    return b


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    from eda_agent.layout import read_altium
    from eda_agent.tools import route as route_module

    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    board = _board()
    monkeypatch.setattr(read_altium, "read_live_board", lambda expect: board)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    route_module.register_route_tools(DummyMcp())
    return captured


@pytest.mark.asyncio
async def test_the_brief_names_what_is_critical_and_keeps_what_is_given(tools):
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE)
    assert [x["net"] for x in out["needed"]] == ["XIN"] and out["needed"][0]["kind"] == "clock"
    assert "file" not in out, "a read saves nothing"
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["XIN"], "kind": "clock", "layers": ["TopLayer"], "max_vias": 0,
         "source": "crystal datasheet p.12: under 10 mm, no vias"}])
    assert out["needed"] == [] and out["file"]
    assert out["brief"][0]["max_vias"] == 0
    assert [r["kind"] for r in out["rounds"]] == ["clock", "rest"]
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE, drop=[
        {"nets": ["XIN"], "rules": ["max_vias"]}])
    assert "max_vias" not in out["brief"][0] and out["brief"][0]["layers"] == ["TopLayer"]


@pytest.mark.asyncio
async def test_a_brief_that_cannot_be_obeyed_is_not_saved(tools):
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["XIN"], "layers": ["TopLayer"], "source": "datasheet"}])
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["BottomLayer"], "source": "x"}])
    assert out["saved"] is False and "surface pads on ['TopLayer']" in out["errors"][0]
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE)
    assert [e["nets"] for e in out["brief"]] == [["XIN"]], "the brief stayed as it was"


@pytest.mark.asyncio
async def test_a_round_stops_for_a_critical_net_without_a_brief(tools):
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE)
    assert out["round"] == {"kind": "clock", "nets": ["XIN"]}
    assert out["brief_needed"][0]["net"] == "XIN" and "job_id" not in out
    # The whole-board router leaves it out rather than guess.
    out = await tools["pcb_autoroute"](expect_file=BOARD_FILE)
    assert out["brief_needed"][0]["net"] == "XIN" and out["job_id"]
    store = get_job_store()
    assert store.wait(out["job_id"], timeout=60)
    assert {t["net_name"] for t in store.get(out["job_id"]).result["tracks"]} == {"A"}


@pytest.mark.asyncio
async def test_a_round_routes_its_group_under_the_brief_and_hands_over_a_review(tools):
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["XIN"], "layers": ["TopLayer"], "max_vias": 0, "source": "datasheet p.12"}])
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE)
    assert out["round"] == {"kind": "clock", "nets": ["XIN"]}
    assert out["rounds_left"] == [{"kind": "rest", "nets": 1}]
    store = get_job_store()
    assert store.wait(out["job_id"], timeout=60)
    res = store.get(out["job_id"]).result
    assert {t["net_name"] for t in res["tracks"]} == {"XIN"}
    assert {t["layer"] for t in res["tracks"]} == {"TopLayer"} and not res["vias"]
    rv = res["review"]
    assert rv["obeyed"] == ["XIN"] and rv["nets"]["XIN"]["vias"] == 0
    assert rv["picture"] is None or rv["picture"].endswith(".png")
