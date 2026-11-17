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
    await _decide_a(tools)
    out = await tools["pcb_autoroute"](expect_file=BOARD_FILE)
    assert out["brief_needed"][0]["net"] == "XIN" and out["job_id"]
    assert "decision_needed" not in out
    store = get_job_store()
    assert store.wait(out["job_id"], timeout=60)
    assert {t["net_name"] for t in store.get(out["job_id"]).result["tracks"]} == {"A"}


@pytest.mark.asyncio
async def test_the_router_lays_only_what_the_ai_decided(tools):
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE)
    assert out["undecided"] == [{"net": "A", "why": "no entry"}] and out["undecided_count"] == 1
    out = await tools["pcb_autoroute"](expect_file=BOARD_FILE, nets=["A"])
    assert "job_id" not in out and out["decision_needed"][0]["net"] == "A"
    assert "pcb_route_brief" in out["next_step"]
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE, nets=["A"])
    assert "job_id" not in out and out["decision_needed_count"] == 1
    # An entry with no layers, or no reason, decides nothing yet.
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[{"nets": ["A"], "width": 8}])
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE, nets=["A"])
    assert out["decision_needed"] == [{"net": "A", "why": "its entry names no layers"}]
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["TopLayer"]}])
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE, nets=["A"])
    assert out["decision_needed"] == [{"net": "A", "why": "its entry gives no reason"}]
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["TopLayer"], "reason": "two resistors, top only",
         "verified": "smuggled in with the entry"}])
    # The facts to check the decision against come back; verified given
    # with the entry is not taken.
    facts = out["to_confirm"][0]["facts"][0]
    assert facts["pads"] == ["R1.1", "R2.1"] and facts["smd_layers"] == ["TopLayer"]
    assert facts["span"] == 400.0
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE, nets=["A"])
    assert out["decision_needed"][0]["why"].startswith("not confirmed")


def test_a_big_group_of_plain_nets_is_confirmed_by_its_hardest_net():
    from eda_agent.layout.route import brief as B

    b = _board()
    smd = [PadCopper("TopLayer", "rect", 20, 20)]
    for i in range(6):
        far = 150 + 40 * i
        b.pads += [Pad(f"R{10 + i}", "1", 100, 20 + i * 10, net=f"S{i}", copper=list(smd)),
                   Pad(f"R{20 + i}", "1", 100 + far, 20 + i * 10, net=f"S{i}", copper=list(smd))]
    e = B.NetRules(nets=tuple(f"S{i}" for i in range(6)), layers=("TopLayer",),
                   reason="plain signals")
    assert B.hardest(b, e.nets) == "S5"
    sure = {"confidence": 90, "alternative": "bottom layer: needs two vias each for nothing"}
    assert "S5" in B.confirm_errors(b, e, "S0 joins R10 and R20", **sure)[0]
    assert B.confirm_errors(b, e, "S5, the longest, R15 to R25: 350 mil clear on top",
                            **sure) == []
    small = B.NetRules(nets=("S0", "S1"), layers=("TopLayer",), reason="x")
    assert "S1 is not named" in B.confirm_errors(b, small, "S0 joins R10 and R20", **sure)[0]


def test_a_decision_below_the_confidence_or_with_no_alternative_is_not_confirmed():
    from eda_agent.layout.route import brief as B

    b = _board()
    e = B.NetRules(nets=("A",), layers=("TopLayer",), reason="short")
    said = "A joins R1 and R2 on top"
    assert "give confidence" in B.confirm_errors(b, e, said)[0]
    low = B.confirm_errors(b, e, said, confidence=60, alternative="bottom")[0]
    assert "under 80" in low and "ask the user" in low
    assert "other choice" in B.confirm_errors(b, e, said, confidence=95)[0]
    assert "other choice" in B.confirm_errors(b, e, said, confidence=95, alternative=said)[0]
    assert B.confirm_errors(b, e, said, confidence=95, alternative="bottom: two vias") == []
    # A confirmation stored below the line does not count.
    e2 = B.NetRules(nets=("A",), layers=("TopLayer",), reason="short", verified=said,
                    confidence=70, alternative="bottom")
    e2.verified_for = e2.decision_key()
    assert not e2.confirmed
    e2.confidence = 85
    assert e2.confirmed


async def _decide_a(tools):
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["TopLayer"], "reason": "two resistors, top only"}])
    return await tools["pcb_route_brief"](expect_file=BOARD_FILE, confirm=[
        {"nets": ["A"], "verified": "A joins R1 and R2, both pads on TopLayer, 400 mil apart "
                                    "in a clear row: one straight track on top",
         "confidence": 95, "alternative": "bottom layer: two vias for a clear straight run"}])


@pytest.mark.asyncio
async def test_a_decision_is_confirmed_in_a_later_call_net_by_net(tools):
    out = await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["TopLayer"], "reason": "short"}],
        confirm=[{"nets": ["A"], "verified": "A joins R1 and R2"}])
    assert out["saved"] is False and "next call" in out["errors"][0]
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["TopLayer"], "reason": "short"}])
    for said, err in (("checked", "A is not named"), ("short", "the reason again"),
                      ("net A is fine", "name the parts")):
        out = await tools["pcb_route_brief"](expect_file=BOARD_FILE, confirm=[
            {"nets": ["A"], "verified": said, "confidence": 90, "alternative": "bottom"}])
        assert out["saved"] is False and err in out["errors"][0], (said, out["errors"])
    out = await _decide_a(tools)
    assert out["undecided"] == [] and "to_confirm" not in out
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE, nets=["A"], advise=False)
    assert out["job_id"] and "decision_needed" not in out
    assert get_job_store().wait(out["job_id"], timeout=60)
    # A change to the decision undoes the confirmation.
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["A"], "layers": ["TopLayer"], "reason": "short", "width": 8}])
    out = await tools["pcb_route_round"](expect_file=BOARD_FILE, nets=["A"])
    assert out["decision_needed"][0]["why"].startswith("not confirmed")


@pytest.mark.asyncio
async def test_a_round_routes_its_group_under_the_brief_and_hands_over_a_review(tools):
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, add=[
        {"nets": ["XIN"], "layers": ["TopLayer"], "max_vias": 0, "source": "datasheet p.12"}])
    await tools["pcb_route_brief"](expect_file=BOARD_FILE, confirm=[
        {"nets": ["XIN"], "verified": "XIN runs Y1 to U1, 200 mil on top, no via, under the "
                                      "datasheet's 10 mm", "confidence": 90,
         "alternative": "under U1 on the bottom: two vias the datasheet rules out"}])
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


def test_a_current_asks_the_width_ipc_2221_gives_on_each_layer():
    from eda_agent.layout.route import brief as B

    # 1 A at 10 C in 1 oz (1.4 mil) on an outer layer: about 11.6 mil.
    assert abs(B.current_width(1.0, 10.0, 1.4, outer=True) - 11.6) < 0.1
    assert B.current_width(1.0, 10.0, 1.4, outer=False) > 2 * 11.6
    b = _board()
    e = B.NetRules(nets=("A",), layers=("TopLayer",), current=1.0, reason="1 A feed")
    err = B.check(b, B.Brief([e]))["errors"]
    assert err and "11.6 mil on TopLayer" in err[0] and "give that width" in err[0]
    ok = B.NetRules(nets=("A",), layers=("TopLayer",), current=1.0, width=12, reason="x")
    assert B.check(b, B.Brief([ok]))["errors"] == []


@pytest.mark.asyncio
async def test_the_review_measures_the_copper_on_the_board(tools, monkeypatch):
    from eda_agent.layout import read_altium
    from eda_agent.layout.model import Track
    from eda_agent.layout.route import brief as B

    board = _board()
    # Hand-laid: A as one 8 mil track where its 1 A needs 11.6 mil.
    board.tracks = [Track("TopLayer", 100, 300, 500, 300, 8, net="A")]
    monkeypatch.setattr(read_altium, "read_live_board", lambda expect: board)
    B.save(BOARD_FILE, B.Brief([B.NetRules(nets=("A",), layers=("TopLayer",), current=1.0,
                                           width=12, reason="1 A feed")]))
    out = await tools["pcb_route_review"](expect_file=BOARD_FILE, picture=False)
    assert out["reviewed"] == 1 and out["open"] == []
    cur = [f for f in out["findings"] if f["check"] == "current"]
    assert cur and cur[0]["measured"] == 8 and cur[0]["limit"] == 11.6, out["findings"]
    assert out["nets"]["A"]["vias"] == 0
    out = await tools["pcb_route_review"](expect_file=BOARD_FILE, nets=["NOPE"])
    assert "no such nets" in out["error"]


def test_return_vias_and_thermal_pads_are_measured(tools, monkeypatch):
    from eda_agent.layout.model import Region, Via
    from eda_agent.layout.route import brief as B
    from eda_agent.layout.route import review as R

    b = _board()
    b.pads.append(Pad("Q1", "TAB", 300, 200, net="SW",
                      copper=[PadCopper("TopLayer", "rect", 100, 100)]))
    b.pads.append(Pad("Q1", "1", 250, 150, net="G", copper=[PadCopper("TopLayer", "rect", 20, 20)]))
    b.vias = [Via(150, 300, 20, 10, "TopLayer", "BottomLayer", net="A"),
              Via(400, 300, 20, 10, "TopLayer", "BottomLayer", net="A"),
              Via(160, 300, 20, 10, "TopLayer", "BottomLayer", net="GND"),
              Via(310, 200, 20, 10, "TopLayer", "BottomLayer", net="SW")]
    b.pads.append(Pad("J1", "1", 50, 50, net="GND", copper=[PadCopper("TopLayer", "rect", 20, 20)]))
    b.regions = [Region("TopLayer", [(200, 100), (400, 100), (400, 300), (200, 300)], net="SW",
                        kind="pour")]
    e = B.NetRules(nets=("A",), layers=("TopLayer", "BottomLayer"), reference="GND",
                   return_via=30, reason="x")
    th = B.ThermalRule(name="Q1", part="Q1", min_area=50000, min_vias=4, power=2.0,
                       theta_ja=40, ambient=50, tj_max=125, source="Q1 datasheet p.3")
    brief = B.Brief([e], thermal=[th])
    assert [x for x in B.check(b, brief)["errors"] if "thermal" in x or "return_via" in x] == []
    m = R.return_via_misses(b, brief, ["A"])
    assert m["A"]["count"] == 1 and m["A"]["at"] == [400.0, 300.0]
    got = B.thermal_measure(b, th)
    assert got["pad"] == "Q1.TAB" and got["area"] == 40000 and got["vias"] == 1
    assert got["tj"] == 130.0
    found = {f["id"] for f in R.thermal_findings(b, brief, [])}
    assert found == {"thermal:Q1:area", "thermal:Q1:vias", "thermal:Q1:tj"}
    bad = B.Brief([], thermal=[B.ThermalRule(name="Q9", part="Q9"),
                              B.ThermalRule(name="Q1b", part="Q1", power=1.0)])
    errs = B.check(b, bad)["errors"]
    assert any("no part 'Q9'" in x for x in errs)
    assert any("go together" in x for x in errs) and any("give its source" in x for x in errs)
