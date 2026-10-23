# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Rectangle outlines take their corners in decimal mils (issue #124).

pcb_place_polygon_rect and pcb_set_board_shape read the corners with
StrToIntDef, so a corner given in millimetres ("960.6299" for 24.4 mm)
was not an integer, fell back to 0, and the polygon or board outline
came out with that corner at the origin; the reply echoed the 0. The
tools also declared the corners as int. Both now carry decimals end to
end. The other half of #124, the closing segment, is already guarded
by test_geometry_map (ClosePolySegments).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

_ROUTINES = ("PCB_PlacePolygonRect", "PCB_SetBoardShape")


@pytest.mark.parametrize("name", _ROUTINES)
def test_corners_are_read_as_decimal_mils(name):
    body = load(_PCB)[name]
    for key in ("x1", "y1", "x2", "y2"):
        assert (f"{key.upper()} := StrToFloatDef(ExtractJsonValue(Params, "
                f"'{key}'), 0);") in body
    assert "StrToIntDef" not in body
    assert "X1, Y1, X2, Y2, TmpF : Double;" in body


@pytest.mark.parametrize("name", _ROUTINES)
def test_corners_reach_the_board_unrounded(name):
    body = load(_PCB)[name]
    for corner in ("Cx1 := MilsToCoordF(X1);", "Cy1 := MilsToCoordF(Y1);",
                   "Cx2 := MilsToCoordF(X2);", "Cy2 := MilsToCoordF(Y2);"):
        assert corner in body
    assert "MilsToCoord(" not in body


@pytest.mark.parametrize("name", _ROUTINES)
def test_the_reply_echoes_the_corners_used(name):
    body = load(_PCB)[name]
    for key in ("X1", "Y1", "X2", "Y2"):
        assert f"FloatToJsonStr({key})" in body
    assert "IntToStr(X" not in body and "IntToStr(Y" not in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, tool, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    asyncio.run(mcp.tools[tool](**kw))
    return bridge.sent


@pytest.mark.parametrize("tool, command", [
    ("pcb_place_polygon_rect", "pcb.place_polygon_rect"),
    ("pcb_set_board_shape", "pcb.set_board_shape"),
])
def test_decimal_corners_are_sent_as_given(monkeypatch, tool, command):
    sent = _call(monkeypatch, tool, x1=960.6299, y1=960.6299,
                 x2=7039.3701, y2=5000.0)
    assert len(sent) == 1 and sent[0][0] == command
    params = sent[0][1]
    assert (params["x1"], params["y1"], params["x2"], params["y2"]) == (
        "960.6299", "960.6299", "7039.3701", "5000")
