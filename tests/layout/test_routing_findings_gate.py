# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The brief's measured rules and areas, and the gate that will not write a
round until every finding is answered.

The AI sets the values with their source; the router walls what it can
(areas), measures the rest on the copper (length, skew, return plane), and
pcb_autoroute_apply refuses while a finding is unanswered.
"""

from __future__ import annotations

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Region, Rule
from eda_agent.layout.route import brief as B
from eda_agent.layout.route.job import route_job
from tests.conftest import install_bridge_fake

BOARD_FILE = r"C:\boards\demo\demo.PcbDoc"
SMD = [PadCopper("TopLayer", "rect", 20, 20)]
WALL = [(250, 100), (350, 100), (350, 300), (250, 300)]


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=30mil) (Preferred=6mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    b.pads = [Pad("R1", "1", 100, 200, net="A", copper=list(SMD)),
              Pad("R2", "1", 500, 200, net="A", copper=list(SMD))]
    return b


def _inside(x, y, ring=WALL):
    return B._inside([ring], x, y)


# ---- the antenna feed and areas ---------------------------------------------------

def test_an_antenna_feed_needs_a_sourced_entry_and_an_antenna_area_that_lets_it_in():
    b = _board()
    b.pads += [Pad("ANT1", "1", 300, 350, net="RF_OUT", copper=list(SMD)),
               Pad("U1", "5", 300, 250, net="RF_OUT", copper=list(SMD))]
    got = B.check(b, B.Brief())
    assert {"net": "RF_OUT", "kind": "antenna", "why": "no entry",
            "read": B.CRITICAL["antenna"]} in got["needed"]
    entry = {"nets": ["RF_OUT"], "kind": "antenna", "source": "module datasheet p.20"}
    got = B.check(b, B.Brief.from_list([entry]))
    assert got["needed"][0]["why"] == "no area of kind antenna allows it"
    area = {"name": "ANT", "kind": "antenna", "polygons": [[(250, 320), (350, 320),
                                                           (350, 390), (250, 390)]],
            "allow": ["RF_OUT"], "source": "module datasheet p.21, keep-out drawing"}
    got = B.check(b, B.Brief.from_list([entry], [area]))
    assert got["ok"] and not [n for n in got["needed"] if n["net"] == "RF_OUT"]


def test_an_area_with_no_source_or_a_barred_pad_inside_is_refused():
    b = _board()
    got = B.check(b, B.Brief.from_list([], [{"name": "X", "polygons": [[(80, 180), (120, 180),
                                                                       (120, 220)]]}]))
    text = " ".join(got["errors"])
    assert "give its source" in text and "A's pad R1.1 is inside it" in text


def test_every_net_routes_round_an_area_but_the_ones_it_allows():
    area = {"name": "ANT", "kind": "antenna", "polygons": [WALL], "source": "test"}
    res = route_job({"board": _board(), "brief": {"entries": [], "areas": [area]}})
    assert res["summary"]["completion"] == 1.0
    for t in res["tracks"]:
        mid = ((t["x1"] + t["x2"]) / 2, (t["y1"] + t["y2"]) / 2)
        assert not any(_inside(*p) for p in ((t["x1"], t["y1"]), mid, (t["x2"], t["y2"])))
    assert not any(_inside(v["x"], v["y"]) for v in res["vias"])
    straight = route_job({"board": _board(), "brief": {"entries": [], "areas": [
        dict(area, allow=["A"])]}})
    assert sum(abs(t["x2"] - t["x1"]) + abs(t["y2"] - t["y1"]) for t in straight["tracks"]) < \
        sum(abs(t["x2"] - t["x1"]) + abs(t["y2"] - t["y1"]) for t in res["tracks"])


def test_a_pour_already_inside_an_area_is_a_finding():
    b = _board()
    b.regions = [Region("BottomLayer", [(260, 150), (340, 150), (340, 250), (260, 250)], [],
                        "GND", "pour", source="polygon:GND")]
    b.pads += [Pad("R3", "1", 50, 50, net="GND", copper=list(SMD))]
    res = route_job({"board": b, "review": True, "nets": ["A"], "advise": False,
                     "brief": {"entries": [], "areas": [
                         {"name": "ANT", "kind": "antenna", "polygons": [WALL],
                          "source": "test"}]}})
    ids = [f["id"] for f in res["findings"]]
    assert "area:ANT:GND:BottomLayer" in ids
    f = next(f for f in res["findings"] if f["id"] == "area:ANT:GND:BottomLayer")
    assert "polygon cutout" in f["text"]


# ---- measured on the copper ---------------------------------------------------------

def test_length_skew_and_return_plane_are_measured_and_put_to_the_ai():
    b = _board()
    b.pads += [Pad("R3", "1", 100, 100, net="B", copper=list(SMD)),
               Pad("R4", "1", 200, 100, net="B", copper=list(SMD)),
               Pad("R5", "1", 50, 350, net="GND", copper=list(SMD))]
    res = route_job({"board": b, "review": True, "advise": False, "nets": ["A", "B"],
                     "brief": [{"nets": ["A"], "max_length": 100, "match": "bus", "skew": 5,
                                "reference": "GND", "source": "test"},
                               {"nets": ["B"], "match": "bus", "source": "test"}]})
    by = {f["id"]: f for f in res["findings"]}
    assert by["length:A"]["measured"] > 100 and by["length:A"]["limit"] == 100
    assert by["skew:bus"]["measured"] > 5
    assert "no GND on a layer next to it" in by["reference:A"]["text"]
    assert res["review"]["questions"] == [f["text"] for f in res["findings"]]
    # A GND plane on the next layer is the return path the rule asks for.
    b.layers[1] = Layer("BottomLayer", "plane", 1, plane_net="GND")
    res = route_job({"board": b, "review": True, "advise": False, "nets": ["A"],
                     "brief": [{"nets": ["A"], "reference": "GND", "source": "test"}]})
    assert not [f for f in res["findings"] if f["check"] == "reference"]


def test_a_round_reaches_pads_along_their_axes_and_the_review_has_nothing_to_ask():
    # Live, nearly every round's review found a track ending on a pad at 45
    # degrees; rounds now route with axis entries.
    from eda_agent.layout.route import review

    smd = [PadCopper("TopLayer", "rect", 30, 20)]
    b = _board()
    b.pads = [Pad("C1", "1", 100, 100, net="X", copper=list(smd)),
              Pad("C1", "2", 160, 100, net="Y", copper=list(smd)),
              Pad("U1", "1", 300, 250, net="X", copper=list(smd)),
              Pad("U1", "2", 300, 300, net="Y", copper=list(smd)),
              Pad("C2", "1", 450, 120, net="X", copper=list(smd))]
    loose = route_job({"board": b, "nets": ["X", "Y"], "stages": {"join_existing": True}})
    assert review.pad_entries(loose["tracks"], b.pads_by_net()), "the case still shows it"
    res = route_job({"board": b, "nets": ["X", "Y"],
                     "stages": {"join_existing": True, "axis_entry": True}})
    assert res["summary"]["completion"] == 1.0
    assert review.pad_entries(res["tracks"], b.pads_by_net()) == {}


def _jog_board(ox, oy):
    smd = [PadCopper("TopLayer", "rect", 30, 20)]
    b = _board()
    b.rules[1] = Rule("Width", "1", "All", "All", 1, True, {},
                      "Width Constraint (Min=5mil) (Max=30mil) (Preferred=10mil) (All)")
    b.pads = [Pad("C1", "1", 100.37 + ox, 100.81 + oy, net="X", copper=list(smd)),
              Pad("C1", "2", 160.37 + ox, 100.81 + oy, net="Y", copper=list(smd)),
              Pad("U1", "1", 300.53, 250.29, net="X", copper=list(smd)),
              Pad("U1", "2", 300.53, 300.29, net="Y", copper=list(smd)),
              Pad("C2", "1", 450.11, 120.66, net="X", copper=list(smd), rotation=90)]
    return b


def test_a_stub_meets_the_grid_route_without_an_off_angle_jog(monkeypatch):
    # The stub runs on the pad's axis, the grid route a fraction of a cell
    # beside it; the join came out at 18 or 64 degrees.
    from eda_agent.layout.route import review
    from eda_agent.layout.route.router import Router

    def jogs():
        n = 0
        for ox, oy in ((0, 0), (1.3, 0.9), (2.7, 0)):
            res = route_job({"board": _jog_board(ox, oy), "nets": ["X", "Y"],
                             "stages": {"join_existing": True, "axis_entry": True}})
            assert res["summary"]["completion"] == 1.0
            n += sum(k for k, _ in review.off_angle(res["tracks"]).values())
        return n

    squared = jogs()
    monkeypatch.setattr(Router, "_square_jogs", lambda self, job, li, pts, ends=(): list(pts))
    assert squared * 3 < jogs(), "most jogs are squared"


def test_the_axis_meets_the_next_corner_on_45_degrees():
    from eda_agent.layout.route.router import Router

    # Up the axis from (0, 0), the corner at (10, 30): turn 45 at (0, 20).
    assert Router._axis_then_45((0.0, 0.0), (0.0, 1.0), (10.0, 30.0)) == pytest.approx((0, 20))
    # A corner just short of 45 degrees from the stub's end: X a little
    # before it, within ``back``, else none at 45 (a 90 degree turn).
    x = Router._axis_then_45((0.0, 0.0), (0.0, 1.0), (10.0, 9.5), back=1.0)
    assert x == pytest.approx((0, -0.5))
    assert Router._axis_then_45((0.0, 0.0), (0.0, 1.0), (10.0, 9.5)) == pytest.approx((0, 9.5))
    # On the axis itself: the corner.
    assert Router._axis_then_45((0.0, 0.0), (0.0, 1.0), (0.0, 7.0)) == (0.0, 7.0)


def test_a_piece_off_the_45_degree_grid_is_a_finding():
    from eda_agent.layout.route import review

    t = {"x1": 0, "y1": 0, "x2": 3, "y2": 1, "width": 6, "layer": "TopLayer", "net_name": "N"}
    ok = dict(t, x2=3, y2=3)
    b = _board()
    found = review.findings(b, b, [t, ok], [], B.Brief(), ["N"], {}, {}, {}, {})
    assert [f["id"] for f in found] == ["angle:N"] and found[0]["measured"] == 1


def test_the_round_tool_asks_for_axis_entries():
    import inspect

    from eda_agent.tools import route as route_module
    src = inspect.getsource(route_module)
    body = src[src.index("async def pcb_route_round("):]
    body = body[:body.index("\n    @mcp.tool()")]
    assert '"axis_entry": True' in body


def test_the_fallback_gives_up_axis_entries_before_a_connection():
    import inspect

    from eda_agent.layout.route import router
    src = inspect.getsource(router._fallback)
    assert "axis_entry=False" in src


# ---- the gate -------------------------------------------------------------------------

class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == "pcb.get_layout_model":
            return {"file": BOARD_FILE}
        if command in ("pcb.place_tracks", "pcb.place_vias"):
            key = "tracks" if command.endswith("tracks") else "vias"
            return {"placed": len(params[key].split("|")), "failed": 0}
        if command == "pcb.repour_polygons":
            return {"success": True}
        raise AssertionError(command)


@pytest.fixture()
def apply(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    from eda_agent.tools import route as route_module

    bridge = _Bridge()
    install_bridge_fake(monkeypatch, tmp_path, bridge)
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    monkeypatch.setattr(route_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    route_module.register_route_tools(DummyMcp())
    return captured["pcb_autoroute_apply"], bridge


def _job():
    store = get_job_store()
    jid = store.submit("layout_route", route_job, {
        "board": _board(), "review": True, "advise": False, "nets": ["A"],
        "brief": [{"nets": ["A"], "max_length": 100, "source": "test"}]})
    assert store.wait(jid, timeout=60)
    return jid


@pytest.mark.asyncio
async def test_a_round_is_not_written_while_a_finding_is_unanswered(apply):
    tool, bridge = apply
    jid = _job()
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False)
    assert "unanswered" in out and [f["id"] for f in out["unanswered"]] == ["length:A"]
    assert bridge.calls == [], "nothing was sent to Altium"
    # A reason with no text answers nothing.
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False,
                     accept=[{"id": "length:A", "reason": " "}])
    assert "unanswered" in out
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False,
                     accept=[{"id": "length:A", "reason": "the datasheet's limit is 450 mil",
                              "evidence": "sim/a_len.txt"}])
    assert out["tracks"]["placed"] > 0
    assert out["decisions"][0]["reason"] == "the datasheet's limit is 450 mil"
    assert out["decisions"][0]["evidence"] == "sim/a_len.txt"
    kept = B.load_decisions(BOARD_FILE)
    assert kept["length:A"]["reason"] == "the datasheet's limit is 450 mil"


@pytest.mark.asyncio
async def test_an_answer_stands_for_the_same_finding_and_not_for_a_changed_one(apply):
    tool, _ = apply
    jid = _job()
    await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False,
               accept=[{"check": "length", "reason": "accepted for the test"}])
    out = await tool(job_id=_job(), expect_file=BOARD_FILE, checkpoint=False)
    assert "error" not in out, "the same finding, answered before"
    # The same id with another measurement is a new finding.
    kept = B.load_decisions(BOARD_FILE)
    kept["length:A"]["text"] = "something else"
    B.record_decisions(BOARD_FILE, [kept["length:A"]])
    out = await tool(job_id=_job(), expect_file=BOARD_FILE, checkpoint=False)
    assert [f["id"] for f in out["unanswered"]] == ["length:A"]
