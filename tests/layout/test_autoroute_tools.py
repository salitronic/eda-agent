# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The routing job, and the tool that writes its result to a board.

Offline: the job runs on a synthetic board, and the apply tool talks to a
fake bridge through the suite's fail-closed isolation, so nothing here
can reach a running Altium.
"""

from __future__ import annotations

import pytest

from eda_agent.design.jobs import get_job_store
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Region, Rule, Track
from eda_agent.layout.route.job import route_job
from tests.conftest import install_bridge_fake

BOARD_FILE = r"C:\boards\demo\demo.PcbDoc"


def _board() -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5.5mil) (Max=5.5mil) (Preferred=5.5mil) (All)")]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    b.pads = [Pad("R1", "1", 100, 200, net="A", copper=[PadCopper("TopLayer", "rect", 20, 20)]),
              Pad("R2", "1", 500, 200, net="A", copper=[PadCopper("TopLayer", "rect", 20, 20)])]
    b.tracks = [Track("TopLayer", 300, -10, 300, 410, 20.0, keepout=True)]
    return b


def test_the_job_returns_placeable_copper_and_the_drc_verdict():
    res = route_job({"board": _board()})
    s = res["summary"]
    assert s["completion"] == 1.0 and s["violations"] == 0
    assert s["tracks"] == len(res["tracks"]) > 0 and s["vias"] == len(res["vias"]) >= 2
    t = res["tracks"][0]
    assert set(t) == {"x1", "y1", "x2", "y2", "width", "layer", "net_name"}
    assert t["width"] == 5.5, "the rule's width, fraction and all"
    v = res["vias"][0]
    assert set(v) == {"x", "y", "size", "hole_size", "low_layer", "high_layer", "net"}


def test_the_job_routes_only_the_nets_asked_for():
    b = _board()
    b.pads += [Pad("R3", "1", 100, 100, net="B", copper=[PadCopper("TopLayer", "rect", 20, 20)]),
               Pad("R4", "1", 200, 100, net="B", copper=[PadCopper("TopLayer", "rect", 20, 20)])]
    res = route_job({"board": b, "nets": ["B"]})
    assert {t["net_name"] for t in res["tracks"]} == {"B"}
    assert set(res["unrouted"]) <= {"B"}


class _Bridge:
    def __init__(self, focused):
        self.focused = focused
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command == "pcb.get_layout_model":
            return {"file": self.focused}
        if command in ("pcb.place_tracks", "pcb.place_vias"):
            key = "tracks" if command.endswith("tracks") else "vias"
            return {"placed": len(params[key].split("|")), "failed": 0}
        if command == "pcb.repour_polygons":
            return {"success": True}
        raise AssertionError(command)


def _apply_tool(monkeypatch, tmp_path, bridge):
    install_bridge_fake(monkeypatch, tmp_path, bridge)
    from eda_agent.tools import route as route_module
    monkeypatch.setattr(route_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    route_module.register_route_tools(DummyMcp())
    return captured["pcb_autoroute_apply"]


def _finished_job(board=None):
    store = get_job_store()
    jid = store.submit("layout_route", route_job, {"board": board or _board()})
    assert store.wait(jid, timeout=60)
    return jid


def _poured_board() -> LayoutBoard:
    b = _board()
    b.pads += [Pad("R3", "2", 100, 100, net="GND", copper=[PadCopper("TopLayer", "rect", 20, 20)]),
               Pad("R4", "2", 200, 100, net="GND", copper=[PadCopper("TopLayer", "rect", 20, 20)])]
    b.regions = [Region("TopLayer", [(0, 0), (600, 0), (600, 400), (0, 400)], [], "GND",
                        "pour_boundary", source="polygon:GND")]
    return b


@pytest.mark.asyncio
async def test_apply_refuses_when_another_board_is_focused(monkeypatch, tmp_path):
    bridge = _Bridge(focused=r"C:\clients\other\other.PcbDoc")
    tool = _apply_tool(monkeypatch, tmp_path, bridge)
    out = await tool(job_id=_finished_job(), expect_file=BOARD_FILE, checkpoint=False)
    assert "error" in out
    assert [c for c, _ in bridge.calls] == ["pcb.get_layout_model"], "nothing was written"


@pytest.mark.asyncio
async def test_apply_writes_every_track_and_via_with_their_decimals(monkeypatch, tmp_path):
    bridge = _Bridge(focused=BOARD_FILE.replace("\\", "/").upper())
    tool = _apply_tool(monkeypatch, tmp_path, bridge)
    jid = _finished_job()
    res = get_job_store().get(jid).result
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False)
    assert out["tracks"]["placed"] == len(res["tracks"])
    assert out["vias"]["placed"] == len(res["vias"])
    sent = "|".join(p["tracks"] for c, p in bridge.calls if c == "pcb.place_tracks")
    assert ",5.5," in sent, "the width went out with its fraction"


def _old_pour_board(outline=True) -> LayoutBoard:
    # The keepout on top forces net A down to the bottom layer, which a
    # polygon's copper covers as its last pour left it.
    b = _board()
    whole = [(0, 0), (600, 0), (600, 400), (0, 400)]
    b.regions.append(Region("BottomLayer", whole, [], "GND", "pour", source="polygon:P1"))
    if outline:
        b.regions.append(Region("BottomLayer", whole, [], "GND", "pour_boundary",
                                source="polygon:P1"))
    return b


def test_a_polygons_old_copper_is_not_routed_round():
    res = route_job({"board": _old_pour_board()})
    s = res["summary"]
    assert s["old_pours"] == ["BottomLayer:GND"]
    assert s["completion"] == 1.0 and s["violations"] == 0


def test_copper_with_no_polygon_outline_is_kept():
    # Not a pour the apply step can repour: it stays in the way.
    res = route_job({"board": _old_pour_board(outline=False)})
    assert res["summary"]["old_pours"] == []
    assert res["summary"]["completion"] < 1.0


@pytest.mark.asyncio
async def test_apply_repours_what_the_job_routed_through(monkeypatch, tmp_path):
    bridge = _Bridge(focused=BOARD_FILE)
    tool = _apply_tool(monkeypatch, tmp_path, bridge)
    jid = _finished_job(_old_pour_board())
    summary = get_job_store().get(jid).result["summary"]
    assert not summary["pours"] and not summary["planes"], "only the old copper asks for it"
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False)
    assert out["repoured"] is True


def test_a_job_joined_by_a_pour_says_so():
    res = route_job({"board": _poured_board()})
    assert res["summary"]["pours"] == ["TopLayer:GND"]
    assert any("pour" in n for n in res["notes"])


@pytest.mark.asyncio
async def test_apply_repours_when_the_job_relied_on_a_pour(monkeypatch, tmp_path):
    bridge = _Bridge(focused=BOARD_FILE)
    tool = _apply_tool(monkeypatch, tmp_path, bridge)
    jid = _finished_job(_poured_board())
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False)
    assert out["repoured"] is True
    assert [c for c, _ in bridge.calls][-1] == "pcb.repour_polygons", "after the copper"
    bridge.calls.clear()
    out = await tool(job_id=jid, expect_file=BOARD_FILE, checkpoint=False, repour=False)
    assert out["repoured"] is False and "pcb.repour_polygons" not in [c for c, _ in bridge.calls]


@pytest.mark.asyncio
async def test_apply_does_not_repour_a_board_with_no_pour_or_plane(monkeypatch, tmp_path):
    bridge = _Bridge(focused=BOARD_FILE)
    tool = _apply_tool(monkeypatch, tmp_path, bridge)
    out = await tool(job_id=_finished_job(), expect_file=BOARD_FILE, checkpoint=False)
    assert out["repoured"] is False and "pcb.repour_polygons" not in [c for c, _ in bridge.calls]


def _autoroute(monkeypatch, tmp_path, board):
    from eda_agent.layout import read_altium
    install_bridge_fake(monkeypatch, tmp_path, object())
    monkeypatch.setattr(read_altium, "read_live_board", lambda path: board)
    sent = {}

    class Store:
        def submit(self, kind, fn, params):
            sent.update(params)
            return "job-1"

    from eda_agent.tools import route as route_module
    monkeypatch.setattr("eda_agent.design.jobs.get_job_store", lambda: Store())
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    route_module.register_route_tools(DummyMcp())
    return captured["pcb_autoroute"], sent


@pytest.mark.asyncio
async def test_autoroute_passes_the_entry_and_join_options_to_the_job(monkeypatch, tmp_path):
    tool, sent = _autoroute(monkeypatch, tmp_path, _board())
    out = await tool(expect_file=BOARD_FILE, axis_entry=True, neck_pads=True,
                     join_existing=True, exclude_layers=["BottomLayer"])
    assert out["job_id"] == "job-1"
    st = sent["stages"]
    assert st["axis_entry"] and st["neck_pads"] and st["join_existing"]
    assert st["exclude_layers"] == ("BottomLayer",)


@pytest.mark.asyncio
async def test_autoroute_refuses_to_exclude_a_layer_pads_are_on(monkeypatch, tmp_path):
    tool, sent = _autoroute(monkeypatch, tmp_path, _board())
    out = await tool(expect_file=BOARD_FILE, exclude_layers=["TopLayer"])
    assert "surface pad" in out["error"] and not sent
    out = await tool(expect_file=BOARD_FILE, exclude_layers=["Nope"])
    assert "no copper layer" in out["error"] and not sent
