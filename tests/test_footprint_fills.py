# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Vincent Himpe
"""Rectangular fills reach a PcbLib footprint as fills, exactly as asked.

lib_add_footprint_fills sends opposite corners unrounded and refuses, before
anything is sent, a fill that has no layer, a corner that is not a number,
or no area. A fill with no layer is not defaulted, for the reason the regions
tool gives: a solid shape defaulted onto copper is a short. A fill with no
area is refused because it would be invisible in the editor and still be
counted as drawn.

The script side is held by the guards that already cover every footprint
writer: it is reachable from Python (test_bridge_handlers_reachable) and it
is settled after the write (test_footprint_adds_are_listed_once).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import library as library_module

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"success": True, "added": 1}


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(library_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(DummyMcp())
    return captured["lib_add_footprint_fills"], bridge


def test_corners_go_out_exact_and_in_order():
    payload, problems = library_module._fills_payload([
        {"layer": "TopPaste", "x1": -1.2, "y1": -0.65, "x2": 1.625, "y2": 0.65},
        {"layer": "Mechanical13", "x1": 0, "y1": 0, "x2": 2, "y2": 1, "rotation": 45},
    ])
    assert problems == []
    first, second = payload.split("~~")
    assert first == "layer=TopPaste;x1=-1.2;y1=-0.65;x2=1.625;y2=0.65"
    assert second == "layer=Mechanical13;x1=0;y1=0;x2=2;y2=1;rotation=45"


@pytest.mark.parametrize("fill,reason", [
    ({"x1": 0, "y1": 0, "x2": 1, "y2": 1}, "no layer"),
    ({"layer": " ", "x1": 0, "y1": 0, "x2": 1, "y2": 1}, "no layer"),
    ({"layer": "TopPaste", "x1": "a", "y1": 0, "x2": 1, "y2": 1}, "numeric"),
    ({"layer": "TopPaste", "x1": 0, "y1": 0, "x2": 1}, "numeric"),
    ({"layer": "TopPaste", "x1": 1, "y1": 0, "x2": 1, "y2": 1}, "no area"),
    ({"layer": "TopPaste", "x1": 0, "y1": 2, "x2": 1, "y2": 2}, "no area"),
    ("TopPaste", "not an object"),
])
def test_a_fill_that_cannot_be_drawn_is_named_and_left_out(fill, reason):
    good = {"layer": "TopPaste", "x1": 0, "y1": 0, "x2": 1, "y2": 1}
    payload, problems = library_module._fills_payload([good, fill])
    assert payload == "layer=TopPaste;x1=0;y1=0;x2=1;y2=1"
    assert len(problems) == 1 and reason in problems[0]
    assert problems[0].startswith("fill 1 ")


def test_nothing_valid_is_refused_before_sending(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    out = asyncio.run(tool(fills=[{"x1": 0, "y1": 0, "x2": 1, "y2": 1}]))
    assert out["error"] == "No valid fills"
    assert out["refused"] == ["fill 0 has no layer"]
    assert bridge.calls == []


def test_the_call_names_the_command_footprint_and_refusals(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    out = asyncio.run(tool(
        fills=[{"layer": "TopPaste", "x1": 0, "y1": 0, "x2": 1, "y2": 1},
               {"layer": "TopPaste", "x1": 0, "y1": 0, "x2": 0, "y2": 1}],
        footprint_name="QFN-56", units="mm"))
    [(command, params)] = bridge.calls
    assert command == "library.add_footprint_fills"
    assert params["footprint_name"] == "QFN-56"
    assert params["units"] == "mm"
    assert params["fills"] == "layer=TopPaste;x1=0;y1=0;x2=1;y2=1"
    assert out["refused"] == ["fill 1 has no area"]


def test_the_handler_targets_by_name_and_refuses_a_missing_layer():
    """The script handler goes through PcbLibTarget, as the other writers
    do, and treats an empty layer as unresolved rather than defaulting."""
    text = LIBRARY.read_text(encoding="utf-8-sig")
    start = re.search(r"^Function Lib_AddFootprintFills\(", text, re.M)
    assert start, "Lib_AddFootprintFills is missing from Library.pas"
    body = text[start.start():text.index("\nEnd;", start.start())]
    assert "PcbLibTarget(" in body
    assert re.search(r"If LayerStr = '' Then Layer := eNoLayer", body)
    assert "PcbLib.Board.AddPCBObject(Fill)" in body
