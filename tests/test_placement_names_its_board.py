# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Bulk placement can name its board, so a focus change by another
session between a focus and a placement cannot land copper on the wrong
board (measured 2026-11-05: 17 tracks went onto another session's board)."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import pcb

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"
_HANDLERS = ("PCB_PlaceTracks", "PCB_PlaceVias", "PCB_PlaceArcs", "PCB_PlacePads")


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kw):
        self.sent.append((command, params))
        return {"placed": 1}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Mcp())
    return tools, bridge


BOARD = "C:\\Work\\Mine.PcbDoc"
CALLS = {
    "pcb_place_tracks": {"tracks": [{"x1": 0, "y1": 0, "x2": 10, "y2": 0}]},
    "pcb_place_vias": {"vias": [{"x": 0, "y": 0}]},
    "pcb_place_arcs": {"arcs": [{"x_center": 0, "y_center": 0, "radius": 10}]},
    "pcb_place_pads": {"pads": [{"x": 0, "y": 0}], "x_size": 20, "y_size": 20},
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_the_board_is_sent_only_when_named(monkeypatch, name):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools[name](**CALLS[name], board_path=BOARD))
    asyncio.run(tools[name](**CALLS[name]))
    assert bridge.sent[0][1].get("board_path") == BOARD
    assert "board_path" not in bridge.sent[1][1]


@pytest.mark.parametrize("handler", _HANDLERS)
def test_each_handler_takes_its_board_by_that_path(handler):
    text = _PCB.read_text(encoding="utf-8", errors="replace")
    start = text.index(f"Function {handler}(")
    body = re.sub(r"\{[^}]*\}", "", text[start:text.index("\nEnd;", start)])
    # Through StackBoard, which resolves board_path: a direct call into
    # Main from these handlers stopped the whole PCB unit loading.
    assert "StackBoard(Params, RequestId" in body
    assert "GetPCBBoardAnywhere" not in body and "ResolvePCBBoard" not in body
