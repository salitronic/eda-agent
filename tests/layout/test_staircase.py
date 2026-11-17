# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A run drawn as a chain of tiny steps is found, refused where tracks are
written, laid again by the gloss pass, and a finding of a routing round."""
from __future__ import annotations

import asyncio
import json
import math

from eda_agent.layout import staircase
from eda_agent.layout.drc import run_drc
from eda_agent.layout.route.gloss import gloss
from tests.layout.test_gloss import _board, _octilinear, _pad, _path, _tracks


def _steps(x0, y0, n, run=1.97, rise=1.97, width=5.9, net="RS485_TERM"):
    """A shallow run as a grid script draws it: a short horizontal piece
    and a short 45, over and over."""
    pts = [(x0, y0)]
    for _ in range(n):
        x, y = pts[-1]
        pts += [(x + run, y), (x + run + rise, y + rise)]
    return pts, [{"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "width": width,
                  "layer": "TopLayer", "net_name": net} for a, b in zip(pts, pts[1:])]


def test_a_chain_of_steps_is_a_staircase_and_ordinary_copper_is_not():
    _, stairs = _steps(1799.21, 1525.59, 40)
    found = staircase.find(stairs)
    assert found["staircase_count"] == 1 and found["staircase_tracks"] == 80
    assert found["staircases"][0]["net"] == "RS485_TERM"
    assert found["staircases"][0]["at"] == [1799.21, 1525.59]

    clean = [
        # A route: long pieces, one short neck into a pad, a two-piece stub.
        {"x1": 0, "y1": 0, "x2": 300, "y2": 0, "width": 10, "net": "A"},
        {"x1": 300, "y1": 0, "x2": 400, "y2": 100, "width": 10, "net": "A"},
        {"x1": 400, "y1": 100, "x2": 406, "y2": 100, "width": 6, "net": "A"},
        {"x1": 406, "y1": 100, "x2": 410, "y2": 104, "width": 6, "net": "A"},
        {"x1": 410, "y1": 104, "x2": 410, "y2": 109, "width": 6, "net": "A"},
        # Short tracks of other nets that only touch in space.
        {"x1": 0, "y1": 50, "x2": 5, "y2": 50, "width": 10, "net": "B"},
        {"x1": 5, "y1": 50, "x2": 10, "y2": 55, "width": 10, "net": "C"},
    ]
    assert staircase.find(clean)["staircase_count"] == 0
    # Four short tracks in one line bend nowhere: tracks to merge, not steps.
    line = [{"x1": i * 3, "y1": 0, "x2": i * 3 + 3, "y2": 0, "width": 10, "net": "A"}
            for i in range(6)]
    assert staircase.find(line)["staircase_count"] == 0


def test_the_gloss_pass_lays_a_staircase_as_two_tracks():
    b = _board()
    pts, _ = _steps(0, 0, 40)
    end = pts[-1]
    b.pads += [_pad("U1", -100, 0, "N"), _pad("U2", end[0] + 100, end[1], "N")]
    _path(b, "N", [(-100, 0)] + pts + [(end[0] + 100, end[1])], width=5.9)
    assert staircase.find(b.tracks)["staircase_count"] == 1
    r = gloss(b)
    ts = _tracks(b, "N")
    assert len(ts) <= 3 and all(_octilinear(t) for t in ts)
    assert min(math.hypot(t.x2 - t.x1, t.y2 - t.y1) for t in ts) > 50
    assert r.staircase_count == 0 and r.staircases_left == []
    assert not run_drc(b).violations and not run_drc(b).unrouted


def test_a_staircase_no_clear_path_replaces_is_named():
    b = _board()
    pts, _ = _steps(0, 0, 40)
    end = pts[-1]
    b.pads += [_pad("U1", -100, 0, "N"), _pad("U2", end[0] + 100, end[1], "N")]
    _path(b, "N", [(-100, 0)] + pts + [(end[0] + 100, end[1])], width=5.9)
    # Copper of another net close along both sides of the stairs.
    for off in (-14.0, 14.0):
        _path(b, "X", [(pts[0][0], pts[0][1] + off), (end[0], end[1] + off)], width=5.9)
    r = gloss(b, nets=["N"])
    assert r.staircase_count == 1 and r.staircases_left[0]["net"] == "N"


def _tools(monkeypatch):
    from eda_agent.tools import pcb

    class _Bridge:
        sent = []

        async def send_command_async(self, command, params=None, **kw):
            self.sent.append(command)
            return {"placed": len((params or {}).get("tracks", "").split("|"))}

    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Server())
    return tools, bridge


def test_placing_tracks_refuses_a_staircase_and_places_clean_tracks(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    _, stairs = _steps(1799.21, 1525.59, 12)
    out = asyncio.run(tools["pcb_place_tracks"](tracks=stairs))
    assert out["error"] == "STAIRCASE_TRACKS" and "RS485_TERM" in out["reason"]
    assert bridge.sent == []
    ok = asyncio.run(tools["pcb_place_tracks"](tracks=[
        {"x1": 0, "y1": 0, "x2": 100, "y2": 0, "width": 6, "net_name": "A"},
        {"x1": 100, "y1": 0, "x2": 150, "y2": 50, "width": 6, "net_name": "A"}]))
    assert ok["placed"] == 2 and bridge.sent == ["pcb.place_tracks"]


def test_a_geometry_map_that_creates_a_staircase_is_refused(monkeypatch, tmp_path):
    tools, bridge = _tools(monkeypatch)
    _, stairs = _steps(0, 0, 12)
    create = [{"kind": "track", "layer": t["layer"], "width": t["width"], "net": "A",
               "geom": [t["x1"], t["y1"], t["x2"], t["y2"]]} for t in stairs]
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"create": create}), encoding="utf-8")
    out = asyncio.run(tools["pcb_apply_geometry_map"](
        map_path=str(path), board_path=r"C:\b\b.PcbDoc"))
    assert out["error"] == "STAIRCASE_TRACKS" and bridge.sent == []


def test_a_round_that_lays_a_staircase_has_a_finding_to_answer():
    from eda_agent.layout.route import review
    from eda_agent.layout.route.brief import Brief

    b = _board()
    _, stairs = _steps(0, 0, 12, net="N")
    got = review.findings(b, b, stairs, [], Brief(), ["N"], {}, {}, {}, {}, judge=False)
    assert [f["check"] for f in got] == ["staircase"] and got[0]["net"] == "N"
    clean = [{"x1": 0, "y1": 0, "x2": 100, "y2": 0, "width": 6, "layer": "TopLayer",
              "net_name": "N"}]
    assert review.findings(b, b, clean, [], Brief(), ["N"], {}, {}, {}, {}, judge=False) == []


def test_a_track_shorter_than_its_width_is_named_unless_it_is_a_stub():
    from types import SimpleNamespace as NS

    from eda_agent.layout import staircase

    blob = {"x1": 100, "y1": 100, "x2": 103, "y2": 100, "width": 6, "layer": "TopLayer",
            "net_name": "A"}
    fine = {"x1": 0, "y1": 0, "x2": 50, "y2": 0, "width": 6, "layer": "TopLayer",
            "net_name": "A"}
    found = staircase.short([blob, fine])
    assert found["short_track_count"] == 1 and found["short_tracks"][0]["at"] == [100, 100]
    assert staircase.short([{**blob, "stub": True}])["short_track_count"] == 0
    pad = NS(x=100, y=100, copper=[NS(layer="TopLayer", w=10, h=10, offset_x=0, offset_y=0)])
    assert staircase.short([blob], pads=[pad])["short_track_count"] == 0
    other = NS(x=100, y=100, copper=[NS(layer="BottomLayer", w=10, h=10, offset_x=0, offset_y=0)])
    assert staircase.short([blob], pads=[other])["short_track_count"] == 1
    via = NS(x=103, y=100, diameter=20)
    assert staircase.short([blob], vias=[via])["short_track_count"] == 0
    refusal = staircase.short_refusal(found, "The list")
    assert refusal["error"] == "SHORT_TRACKS" and refusal["success"] is False


def test_the_layout_audit_fails_a_board_with_staircases_or_short_tracks():
    from eda_agent.layout.audit import run_audits
    from eda_agent.layout.model import Layer, LayoutBoard, Track

    board = LayoutBoard(name="b", layers=[Layer("TopLayer", order=0)])
    board.tracks = [Track("TopLayer", 0, 0, 500, 0, 6, "A")]
    clean = run_audits(board, ["track_shape_audit"])
    assert clean["pass"] and clean["summary"]["track_shape_audit"]["short_tracks"] == 0
    x = y = 0.0
    for i in range(12):
        nx, ny = (x + 2, y) if i % 2 == 0 else (x + 2, y + 2)
        board.tracks.append(Track("TopLayer", x, y + 50, nx, ny + 50, 6, "B"))
        x, y = nx, ny
    board.tracks.append(Track("TopLayer", 900, 900, 902, 900, 6, "C"))
    # Drawings with no net and footprint copper are not judged.
    board.tracks.append(Track("TopLayer", 700, 700, 701, 700, 6, ""))
    board.tracks.append(Track("TopLayer", 800, 800, 801, 800, 6, "A", comp="U1"))
    bad = run_audits(board, ["track_shape_audit"])
    counts = bad["summary"]["track_shape_audit"]
    assert not bad["pass"] and counts["staircases"] == 1 and counts["short_tracks"] == 1
