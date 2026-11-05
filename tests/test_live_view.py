# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The live layout view: snapshots, the decision log, the diff, the
dashboard endpoints and the engine jobs' hooks.

Offline, on synthetic boards. The jobs run for real; where a test needs
the publisher to fail or to be watched, it is replaced on the module the
hooks call through, so a hook that bypassed it would show.
"""

from __future__ import annotations

import json
import random
import time

import pytest

from eda_agent.design import live
from eda_agent.layout.model import (Component, Layer, LayoutBoard, Pad, PadCopper,
                                    Region, Rule, Track, Via)

LAYERS = [Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)]
CLEARANCE = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)")]


def _pad(comp, name, x, y, net, layer="TopLayer"):
    return Pad(comp, name, x, y, net=net, copper=[PadCopper(layer, "rect", 20, 20)])


def _part(ref, x, y, net_a, net_b, locked=False):
    comp = Component(ref, x=x, y=y, locked=locked,
                     courtyard=[(x - 30, y - 15), (x + 30, y - 15),
                                (x + 30, y + 15), (x - 30, y + 15)])
    return comp, [_pad(ref, "1", x - 15, y, net_a), _pad(ref, "2", x + 15, y, net_b)]


def _board(name="demo") -> LayoutBoard:
    b = LayoutBoard(name=name, outline=[(0, 0), (800, 0), (800, 500), (0, 500)],
                    layers=list(LAYERS), rules=list(CLEARANCE))
    for ref, x, y, a, bb in (("R1", 100, 100, "A", "B"), ("R2", 400, 100, "A", "C"),
                             ("R3", 400, 300, "C", "D")):
        c, p = _part(ref, x, y, a, bb)
        b.components.append(c)
        b.pads += p
    return b


# ---------------------------------------------------------------------------
# publish: atomic, versioned
# ---------------------------------------------------------------------------

def test_publish_replaces_the_file_whole_and_the_version_rises(tmp_path, monkeypatch):
    seen = []
    real = live.replace_with_retry

    def watched(tmp, target):
        # At the moment of the rename the old file must still be whole:
        # the new board was staged elsewhere, never written in place.
        old = json.loads(open(target, encoding="utf-8").read()) if seen else None
        seen.append((str(tmp), str(target), old and old["version"]))
        real(tmp, target)

    monkeypatch.setattr(live, "replace_with_retry", watched)
    first = live.publish(_board(), root=tmp_path)
    second = live.publish(_board(), root=tmp_path)

    assert (first["version"], second["version"]) == (1, 2)
    doc = live.read_board(tmp_path)
    assert doc["version"] == 2 and doc["snapshot"]["board"] == "demo"
    board_writes = [s for s in seen if s[1].endswith(live.BOARD_FILE)]
    assert len(board_writes) == 2
    assert all(tmp != target for tmp, target, _ in board_writes)
    assert board_writes[1][2] == 1, "the old version was intact until the rename"
    assert not list(tmp_path.glob("*.tmp")), "no staged file left behind"


def test_the_version_continues_from_the_file_in_a_new_process(tmp_path):
    live.publish(_board(), root=tmp_path)
    live._memory.clear()      # a restarted server knows only the file
    assert live.publish(_board(), root=tmp_path)["version"] == 2


def test_the_snapshot_carries_what_the_page_draws(tmp_path):
    b = _board()
    b.components[0].locked = True
    b.tracks = [Track("TopLayer", 115, 100, 385, 100, 8.0, net="A"),
                Track("TopLayer", 0, 250, 800, 250, 10.0, keepout=True),
                Track("Mechanical1", 0, 0, 800, 0, 5.0)]
    b.vias = [Via(250, 100, 20, 10, "TopLayer", "BottomLayer", net="A")]
    b.regions = [Region("BottomLayer", [(0, 0), (800, 0), (800, 500), (0, 500)],
                        net="GND", kind="pour", source="polygon:GND")]
    extra = {"blocks": [{"name": "Filter", "rect": [350, 50, 450, 350],
                         "members": ["R2", "R3"]}],
             "lanes": [{"name": "C", "net": "C", "layer": "TopLayer", "width": 8,
                        "path": [[415, 100], [415, 300]]}],
             "fixed": ["R3"]}
    live.publish(b, extra=extra, root=tmp_path)
    s = live.read_board(tmp_path)["snapshot"]

    comps = {c["ref"]: c for c in s["components"]}
    assert comps["R1"]["fixed"] and comps["R3"]["fixed"] and not comps["R2"]["fixed"]
    assert comps["R2"]["block"] == comps["R3"]["block"] == "Filter"
    assert comps["R1"]["bbox"] == [70, 85, 130, 115]
    assert [t["layer"] for t in s["tracks"]] == ["TopLayer"], \
        "keepouts and mechanical lines are not copper"
    assert s["keepouts"][0]["type"] == "track"
    assert s["vias"][0]["net"] == "A" and s["pours"][0]["net"] == "GND"
    assert s["blocks"][0]["rect"] == [350, 50, 450, 350]
    assert s["lanes"][0]["path"] == [[415, 100], [415, 300]]
    assert {p["net"] for p in s["pads"]} == {"A", "B", "C", "D"}
    assert len({it["k"] for name in ("components", "pads", "tracks", "vias")
                for it in s[name]}) == 3 + 6 + 1 + 1, "every item has its own key"


# ---------------------------------------------------------------------------
# Status, from layout/drc.py's connectivity
# ---------------------------------------------------------------------------

def test_status_tells_routed_partly_and_not_started_apart(tmp_path):
    b = LayoutBoard(name="s", outline=[(0, 0), (900, 0), (900, 400), (0, 400)],
                    layers=list(LAYERS), rules=list(CLEARANCE))
    b.pads = [_pad("U1", "1", 100, 100, "DONE"), _pad("U2", "1", 300, 100, "DONE"),
              _pad("U1", "2", 100, 200, "HALF"), _pad("U2", "2", 300, 200, "HALF"),
              _pad("U3", "2", 600, 200, "HALF"),
              _pad("U1", "3", 100, 300, "OPEN"), _pad("U2", "3", 300, 300, "OPEN"),
              # Two touching pads of one part: joined, but nothing routed.
              _pad("U4", "1", 700, 300, "SELF"), _pad("U4", "2", 715, 300, "SELF"),
              _pad("U5", "1", 850, 300, "SELF")]
    b.tracks = [Track("TopLayer", 100, 100, 300, 100, 8.0, net="DONE"),
                Track("TopLayer", 100, 200, 300, 200, 8.0, net="HALF")]
    live.publish(b, root=tmp_path)
    st = live.read_board(tmp_path)["snapshot"]["status"]

    assert (st["nets"], st["routed"], st["partly"], st["not_started"]) == (4, 1, 1, 2)
    assert st["unrouted"] == {"HALF": "partly", "OPEN": "open", "SELF": "open"}
    assert st["missing_connections"] == 3
    rats = live.read_board(tmp_path)["snapshot"]["rats"]
    assert len(rats) == 3 and {r[4] for r in rats} == {"HALF", "OPEN", "SELF"}
    half = [r for r in rats if r[4] == "HALF"][0]
    assert half[:4] in ([300, 200, 600, 200], [600, 200, 300, 200]), \
        "the missing link runs from the nearest joined pad"


# ---------------------------------------------------------------------------
# The decision log
# ---------------------------------------------------------------------------

def test_decisions_append_in_order_and_read_back_after_since(tmp_path):
    a = live.note("placement", "Put U1 by J1: the USB pair stays under 10 mm.", root=tmp_path)
    b = live.note("routing", "Route the clock first.", root=tmp_path)
    c = live.note("routing", "Then the rest.", root=tmp_path)
    assert (a["seq"], b["seq"], c["seq"]) == (1, 2, 3)

    every = live.decisions(0, root=tmp_path)
    assert [d["text"] for d in every] == [a["text"], b["text"], c["text"]]
    assert [d["seq"] for d in live.decisions(2, root=tmp_path)] == [3]
    assert live.decisions(3, root=tmp_path) == []
    assert every[0]["section"] == "placement" and every[0]["ts"]


def test_a_torn_last_line_is_skipped_and_numbering_goes_on(tmp_path):
    live.note("x", "one", root=tmp_path)
    with open(tmp_path / live.DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write('{"seq": 2, "text": "half a li')
    live._seq.clear()         # a restarted server reads the file again
    assert [d["text"] for d in live.decisions(0, root=tmp_path)] == ["one"]
    after = live.note("x", "two", root=tmp_path)
    assert after["seq"] == 2, "numbered on from the last whole entry"
    assert [d["text"] for d in live.decisions(1, root=tmp_path)] == ["two"], \
        "the next entry is not swallowed by the torn line"


def test_a_snapshot_note_is_logged_against_its_version(tmp_path):
    live.publish(_board(), note="Placing 3 parts.", kind="place", root=tmp_path)
    row = live.decisions(0, root=tmp_path)[-1]
    assert (row["section"], row["text"], row["version"]) == ("place", "Placing 3 parts.", 1)


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------

def test_diff_tags_added_moved_and_removed_by_key():
    before = _board()
    before.tracks = [Track("TopLayer", 115, 100, 385, 100, 8.0, net="A")]
    before.vias = [Via(250, 300, 20, 10, "TopLayer", "BottomLayer", net="C")]
    after = _board()
    after.components[0].x += 50                         # R1 moves
    after.pads[0].x += 50
    after.pads[1].x += 50
    c, p = _part("R4", 600, 400, "D", "E")              # R4 is new
    after.components.append(c)
    after.pads += p
    after.tracks = [Track("TopLayer", 165, 100, 385, 100, 8.0, net="A")]   # rerouted
    after.vias = list(before.vias)                      # the via stays

    prev, cur = live.snapshot(before), live.snapshot(after)
    d = live.diff_snapshots(prev, cur)
    key = {(n, it.get("ref") or it.get("comp", "") + "." + it.get("name", "")): it["k"]
           for n in ("components", "pads") for it in cur[n]}

    assert key[("components", "R4")] in d["added"]
    assert key[("pads", "R4.1")] in d["added"]
    assert key[("components", "R1")] in d["moved"]
    assert key[("pads", "R1.1")] in d["moved"] and key[("pads", "R1.2")] in d["moved"]
    assert cur["tracks"][0]["k"] in d["added"]
    removed = {(r["list"], r["k"]) for r in d["removed"]}
    assert removed == {("tracks", prev["tracks"][0]["k"])}, "only the old track went"
    assert d["removed"][0]["x1"] == 115, "a removed item keeps its geometry for the ghost"
    untouched = {key[("components", "R2")], key[("components", "R3")], cur["vias"][0]["k"]}
    assert not untouched & (set(d["added"]) | set(d["moved"]))


def test_a_first_board_or_another_board_is_not_a_change(tmp_path):
    first = live.publish(_board("a"), root=tmp_path)
    assert first["changes"] == {"added": 0, "moved": 0, "removed": 0}
    # Another board whose parts share the first one's designators, all
    # elsewhere: a new board, not three moves.
    other_board = _board("b")
    for c in other_board.components:
        c.x += 100
    other = live.publish(other_board, root=tmp_path)
    assert other["changes"] == {"added": 0, "moved": 0, "removed": 0}
    other_board.components[0].x += 10
    assert live.publish(other_board, root=tmp_path)["changes"]["moved"] == 1


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from eda_agent.web.dashboard import create_app
    app = create_app(workspace_dir=tmp_path)
    app.testing = True
    yield app.test_client(), tmp_path / "live"
    # The activity tailer polls with time.sleep; left running it spins in
    # any later test that patches sleep out.
    app.config["TAILER"].stop()


def test_api_live_is_empty_before_anything_is_published(client):
    c, _ = client
    r = c.get("/api/live").get_json()
    assert r["ok"] and r["empty"] and r["version"] == 0


def test_api_live_serves_the_snapshot_and_its_diff_then_only_the_header(client):
    c, root = client
    live.publish(_board(), note="start", kind="place", root=root)
    moved = _board()
    moved.components[1].y += 40
    live.publish(moved, note="placed", kind="place", root=root)
    live.progress("iteration 2", "route", root=root)

    full = c.get("/api/live").get_json()
    assert full["version"] == 2 and full["note"] == "placed"
    assert full["snapshot"]["status"]["nets"] == 2
    r2 = [it["k"] for it in full["snapshot"]["components"] if it["ref"] == "R2"]
    assert full["diff"]["moved"] == r2, "only R2's body moved; its pads were left"
    assert full["progress"]["text"] == "iteration 2"

    same = c.get("/api/live?since=2").get_json()
    assert same["unchanged"] and same["version"] == 2 and "snapshot" not in same
    assert "snapshot" in c.get("/api/live?since=1").get_json()


def test_api_live_decisions_returns_what_came_after_since(client):
    c, root = client
    for text in ("one", "two", "three"):
        live.note("routing", text, root=root)
    r = c.get("/api/live/decisions?since=1").get_json()
    assert [d["text"] for d in r["decisions"]] == ["two", "three"] and r["last"] == 3
    assert c.get("/api/live/decisions?since=3").get_json()["decisions"] == []
    assert len(c.get("/api/live/decisions").get_json()["decisions"]) == 3


def test_the_page_has_the_layout_tab_wired_to_the_endpoints(client):
    c, _ = client
    html = c.get("/classic").get_data(as_text=True)
    assert 'data-tab="layout"' in html and 'data-pane="layout"' in html
    assert "/api/live" in html and "/api/live/decisions" in html
    assert 'name === "layout"' in html and "loadLayout" in html


# ---------------------------------------------------------------------------
# The engine jobs' hooks
# ---------------------------------------------------------------------------

def _route_board() -> LayoutBoard:
    b = LayoutBoard(name="r", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=list(LAYERS), rules=list(CLEARANCE))
    b.pads = [_pad("R1", "1", 100, 200, "A"), _pad("R2", "1", 500, 200, "A")]
    b.components = [Component("R1", x=100, y=200), Component("R2", x=500, y=200)]
    return b


def _place_board() -> LayoutBoard:
    from eda_agent.layout.bench import scramble_placement
    b = _board("p")
    c, p = _part("J1", 60, 250, "A", "Z", locked=True)
    b.components.append(c)
    b.pads += p
    return scramble_placement(b, seed=3)


def test_the_place_job_publishes_before_and_after(monkeypatch):
    from eda_agent.layout.place.job import place_job
    calls = []
    monkeypatch.setattr(live, "publish",
                        lambda board, note="", kind="", extra=None, **k: calls.append(
                            (board, note, kind, extra)))
    board = _place_board()
    res = place_job({"board": board})

    assert [c[2] for c in calls] == ["place", "place"]
    assert calls[0][0] is board, "the start shows the board as it came in"
    assert "J1" in calls[0][3]["fixed"] and "J1" in calls[1][3]["fixed"]
    after = {c.ref: (c.x, c.y) for c in calls[1][0].components}
    for m in res["moves"]:
        assert after[m["designator"]] == (m["x"], m["y"]), "the end shows the moves"


def test_the_route_job_publishes_before_during_and_after(monkeypatch):
    from eda_agent.layout.route.job import route_job
    calls, lines = [], []
    monkeypatch.setattr(live, "publish",
                        lambda board, note="", kind="", extra=None, **k: calls.append(
                            (board, note, kind)))
    monkeypatch.setattr(live, "progress", lambda text, kind="", **k: lines.append((kind, text)))
    res = route_job({"board": _route_board()})

    assert [c[2] for c in calls] == ["route", "route"]
    assert not calls[0][0].tracks and len(calls[1][0].tracks) >= len(res["tracks"]) > 0
    assert lines and lines[0][0] == "route", "the router's log reaches the view"


def _broken(*a, **k):
    raise RuntimeError("disk full")


def test_a_failing_publish_never_fails_a_job(monkeypatch):
    from eda_agent.layout.place.job import place_job
    from eda_agent.layout.route.job import route_job
    monkeypatch.setattr(live, "publish", _broken)
    monkeypatch.setattr(live, "progress", _broken)

    assert route_job({"board": _route_board()})["summary"]["completion"] == 1.0
    assert place_job({"board": _place_board()})["summary"]["parts_moved"] > 0


def test_progress_is_throttled(tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(live, "progress", lambda text, kind="", **k: lines.append(text))
    log = live.progress_logger("route", every=60.0, root=tmp_path)
    for i in range(50):
        log(f"iteration {i}")
    assert lines == ["iteration 0"]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def _big_board() -> LayoutBoard:
    rng = random.Random(0)
    b = LayoutBoard(name="big", outline=[(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
                    layers=list(LAYERS), rules=list(CLEARANCE))
    for k in range(400):
        x, y = 100 + (k % 20) * 290, 100 + (k // 20) * 190
        b.components.append(Component(f"U{k}", x=x, y=y, courtyard=[
            (x - 60, y - 40), (x + 60, y - 40), (x + 60, y + 40), (x - 60, y + 40)]))
        for j in range(4):
            net = f"N{(k * 4 + j) % 700}" if j < 3 else "GND"
            b.pads.append(Pad(f"U{k}", str(j + 1), x - 45 + 30 * j, y, net=net,
                              copper=[PadCopper("TopLayer", "rect", 16, 30)]))
    for _ in range(2000):
        x, y = rng.uniform(0, 6000), rng.uniform(0, 4000)
        b.tracks.append(Track(rng.choice(["TopLayer", "BottomLayer"]), x, y,
                              x + rng.uniform(-200, 200), y, 8.0, net=f"N{rng.randrange(700)}"))
    for _ in range(400):
        b.vias.append(Via(rng.uniform(0, 6000), rng.uniform(0, 4000), 20, 10,
                          "TopLayer", "BottomLayer", net=f"N{rng.randrange(700)}"))
    return b


def test_publish_stays_well_under_a_second_on_a_few_thousand_objects(tmp_path):
    b = _big_board()
    assert len(b.pads) + len(b.tracks) + len(b.vias) + len(b.components) == 4400
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        live.publish(b, root=tmp_path)
        best = min(best, time.perf_counter() - t0)
    assert best < 1.0, f"publish took {best:.2f} s"


# ---------------------------------------------------------------------------
# The MCP tools: no bridge
# ---------------------------------------------------------------------------

class _NoBridge:
    async def send_command_async(self, *a, **k):
        raise AssertionError("the live view tools must not call the bridge")

    def send_command(self, *a, **k):
        raise AssertionError("the live view tools must not call the bridge")


def _tools(monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake
    ws = install_bridge_fake(monkeypatch, tmp_path, _NoBridge())
    from eda_agent.tools import live as live_tools
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    live_tools.register_live_tools(DummyMcp())
    return captured, ws


async def test_the_note_tool_logs_into_the_workspace(monkeypatch, tmp_path):
    tools, ws = _tools(monkeypatch, tmp_path)
    out = await tools["design_live_note"](section="routing", text="Route USB first.")
    assert out["seq"] == 1
    assert live.decisions(0, root=ws / "live")[0]["text"] == "Route USB first."
    assert "error" in await tools["design_live_note"](section="x", text="  ")


async def test_the_snapshot_tool_publishes_a_board_file(monkeypatch, tmp_path):
    tools, ws = _tools(monkeypatch, tmp_path)
    path = tmp_path / "board.json"
    _board("file").save(path)
    out = await tools["design_live_snapshot"](note="by hand", board_json_path=str(path))
    assert out["board"] == "file" and out["url"].endswith("/#layout")
    doc = live.read_board(ws / "live")
    assert doc["version"] == out["version"] and doc["kind"] == "manual"
    again = await tools["design_live_snapshot"](note="once more")
    assert again["version"] == out["version"] + 1, "the last board is republished"
    missing = await tools["design_live_snapshot"](board_json_path=str(tmp_path / "nope.json"))
    assert "error" in missing


async def test_the_view_tool_reports_the_url_and_the_board(monkeypatch, tmp_path):
    from eda_agent.tools import live as live_tools
    tools, ws = _tools(monkeypatch, tmp_path)
    monkeypatch.setattr(live_tools, "_ensure_dashboard", lambda: ("running", ""))
    live.publish(_board("shown"), root=ws / "live")
    out = await tools["design_live_view"]()
    assert out["url"] == "http://127.0.0.1:8766/#layout" and out["dashboard"] == "running"
    assert out["board"] == "shown" and out["version"] >= 1 and out["status"]["nets"] == 2


def test_the_dashboard_is_started_when_nothing_answers(monkeypatch):
    from eda_agent.tools import live as live_tools
    started = []

    class _Srv:
        def serve_forever(self):
            started.append(True)

    monkeypatch.setattr(live_tools, "_probe", lambda *a, **k: "none")
    monkeypatch.setattr(live_tools, "_disabled_by_env", lambda: False)
    monkeypatch.setattr(live_tools, "_server", {})
    import werkzeug.serving
    monkeypatch.setattr(werkzeug.serving, "make_server", lambda *a, **k: _Srv())
    import eda_agent.web.dashboard as dash
    monkeypatch.setattr(dash, "create_app", lambda: object())
    state, _ = live_tools._ensure_dashboard()
    live_tools._server["thread"].join(timeout=5)
    assert state == "started" and started == [True]

    monkeypatch.setattr(live_tools, "_disabled_by_env", lambda: True)
    monkeypatch.setattr(live_tools, "_server", {})
    assert live_tools._ensure_dashboard()[0] == "disabled"

    # An older process holding the port is not a running live view.
    monkeypatch.setattr(live_tools, "_probe", lambda *a, **k: "other")
    state, reason = live_tools._ensure_dashboard()
    assert state == "failed" and "older eda-agent process" in reason


def test_the_probe_tells_the_live_dashboard_from_anything_else(tmp_path):
    """Against real loopback servers on free ports: the dashboard, a server
    without /api/live (an older dashboard), and a port nobody holds."""
    import socket
    import threading

    from flask import Flask
    from werkzeug.serving import make_server

    from eda_agent.tools.live import _probe
    from eda_agent.web.dashboard import create_app

    old = Flask("old-dashboard")
    old.add_url_rule("/", "index", lambda: "an older dashboard")
    # Its 404 is JSON, so the verdict rests on the status and "ok", not
    # on the body failing to parse.
    old.register_error_handler(404, lambda e: ({"error": "not found"}, 404))
    app = create_app(workspace_dir=tmp_path)
    servers = [make_server("127.0.0.1", 0, app, threaded=True),
               make_server("127.0.0.1", 0, old, threaded=True)]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    try:
        assert _probe("127.0.0.1", servers[0].server_port) == "live"
        assert _probe("127.0.0.1", servers[1].server_port) == "other"
        assert _probe("127.0.0.1", free) == "none"
    finally:
        for srv in servers:
            srv.shutdown()
        app.config["TAILER"].stop()
