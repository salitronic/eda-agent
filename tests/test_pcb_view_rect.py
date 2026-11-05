# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The board view can be read as a rectangle and set back exactly.

obj_zoom fits a square round a point and reads nothing back, so a caller
that zooms elsewhere for its own work cannot put the user's view back.
pcb.view reads the editor's viewport rectangle and zooms on one, replying
with what the editor really shows (Altium fits the window's aspect).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _fn(name):
    return load(_PCB)[name]


def test_the_command_is_routed():
    assert "'view':                    Result := PCB_View(Params, RequestId);" in \
        _PCB.read_text(encoding="latin-1")


def test_the_view_is_read_into_a_typed_record_before_its_fields():
    body = _fn("PvRectJson")
    assert "R : TCoordRect;" in body
    read = body.index("R := Board.GraphicalView_ViewportRect;")
    for field in ("R.Left", "R.Bottom", "R.Right", "R.Top"):
        assert body.index(field) > read
    # internal units to mils, decimals kept
    assert "R.Left / 10000.0" in body
    assert "JsonFloat('width', Rt - L)" in body
    assert "JsonFloat('height', T - B)" in body


def test_get_is_the_default_and_reads_the_view():
    body = _fn("PCB_View")
    assert "If Action = '' Then Action := 'get';" in body
    get = body.index("If Action = 'get' Then")
    assert body.index("PvRectJson(Board)", get) < body.index("If Action <> 'set' Then")


def test_no_board_is_refused():
    body = _fn("PCB_View")
    assert body.index("If Board = Nil Then") < body.index("'NO_PCB'")
    assert body.index("'NO_PCB'") < body.index("ExtractJsonValue(Params, 'action')")


def test_set_validates_every_side_before_zooming():
    body = _fn("PCB_View")
    zoom = body.index("PvZoom(Board, L, B, Rt, T);")
    for s in ("LStr", "BStr", "RStr", "TStr"):
        assert f"Not IsFloatStr({s})" in body[:zoom]
    order = body.index("If (Rt <= L) Or (T <= B) Then")
    assert order < zoom
    assert body[:zoom].count("'BAD_RECT'") == 2


def test_set_zooms_in_decimal_mils_and_replies_the_rectangle_read_back():
    zoom_fn = _fn("PvZoom")
    assert "Board.GraphicalView_ZoomOnRect(MilsToCoordF(L), MilsToCoordF(B)," in zoom_fn
    assert "MilsToCoordF(Rt), MilsToCoordF(T));" in zoom_fn
    body = _fn("PCB_View")
    zoom = body.index("PvZoom(Board, L, B, Rt, T);")
    assert body.index("PvRectJson(Board)", zoom) > zoom


def test_set_corrects_the_centre_by_one_more_zoom_and_keeps_the_closer():
    # The view moves in fixed steps (8.9 mil at a whole-board zoom on
    # AD 26.11): the first zoom's centre was 3.45 mil off, one shifted zoom
    # brought it to 1.24.
    off = _fn("PvCentreOff")
    assert "R := Board.GraphicalView_ViewportRect;" in off
    assert "Ex := Cx - (R.Left + R.Right) / 20000.0;" in off
    assert "Ey := Cy - (R.Bottom + R.Top) / 20000.0;" in off
    body = _fn("PCB_View")
    first = body.index("PvZoom(Board, L, B, Rt, T);")
    assert "PvCentreOff(Board, Cx, Cy, Ex1, Ey1);" in body[first:]
    assert "PvZoom(Board, L + Ex1, B + Ey1, Rt + Ex1, T + Ey1);" in body
    assert ("If Abs(Ex2) + Abs(Ey2) > Abs(Ex1) + Abs(Ey1) Then "
            "PvZoom(Board, L, B, Rt, T);") in body
    assert "JsonFloat('centre_error_mils', Sqrt(Ex1 * Ex1 + Ey1 * Ey1))" in body


def test_view_state_does_not_mark_the_board_modified():
    assert "MarkDocDirtyByPath" not in _fn("PCB_View")


def _register(monkeypatch, sent):
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge(), raising=False)
    pcb_tools.register_pcb_tools(_Mcp())
    return tools


def test_no_arguments_reads(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    asyncio.run(tools["pcb_view"]())
    assert sent == [("pcb.view", {"action": "get"})]


def test_set_sends_decimal_mils(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    asyncio.run(tools["pcb_view"](action="Set", left=-12.5, bottom=100,
                                  right=4000.12345, top=3000.5))
    assert sent == [("pcb.view", {"action": "set", "left": "-12.5000",
                                  "bottom": "100.0000", "right": "4000.1235",
                                  "top": "3000.5000"})]


def test_set_with_a_missing_side_sends_nothing(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    out = asyncio.run(tools["pcb_view"](action="set", left=0, bottom=0, right=10))
    assert out["success"] is False and out["error"] == "BAD_RECT"
    assert "top" in out["reason"]
    assert sent == []


def test_set_with_an_empty_rectangle_sends_nothing(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    for r, t in ((0, 10), (10, 0), (-5, 10)):
        out = asyncio.run(tools["pcb_view"](action="set", left=0, bottom=0,
                                            right=r, top=t))
        assert out["error"] == "BAD_RECT"
    assert sent == []


def test_an_unknown_action_sends_nothing(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    out = asyncio.run(tools["pcb_view"](action="zoom", left=0, bottom=0,
                                        right=10, top=10))
    assert out["success"] is False and out["error"] == "BAD_ACTION"
    assert sent == []
