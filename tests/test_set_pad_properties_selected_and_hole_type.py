# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""set_pad_properties can act on the selected pads and set the drill type.

A footprint can hold an SMD pad "1" beside a thru-hole pad "1", so a pad
name cannot say which pad the user means; selected=true changes the pads
selected in the editor instead. hole_type (round, square or slot) is
written before the hole sizes, so a round hole can be made a slot and a
slot round again; hole_width still makes a slot, so it is refused beside a
round or square hole_type. get_pad_geometry reports each pad's selected
flag and stack mode, so a caller can see what a selection write will touch.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import library as library_tools

from tests.pascal_source import load

_LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _lib():
    return load(_LIBRARY)


def test_the_pad_read_reports_selected_and_the_stack_mode():
    body = _lib()["Lib_GetPadGeometry"]
    assert "',\"selected\":' + BoolToJsonStr(Pad.Selected)" in body
    assert "',\"mode\":' + IntToStr(Pad.Mode)" in body


def _set():
    return _lib()["Lib_SetPadProperties"]


def test_a_selection_replaces_the_name_match():
    body = _set()
    assert "BySelection := (Str = 'true') Or (Str = '1');" in body
    assert "If (FpWanted = '') Or ((PadWanted = '') And (Not BySelection)) Then" in body
    assert "((BySelection And Pad.Selected)" in body
    assert "((Not BySelection)" in body


def test_a_bad_or_conflicting_hole_type_is_refused_before_anything_is_touched():
    body = _set()
    first = body.index("'INVALID_HOLE_TYPE'")
    assert body.count("'INVALID_HOLE_TYPE'") == 2
    assert "And (ExtractJsonValue(Params, 'hole_width') <> '') Then" in body
    assert first < body.index("Workspace := GetWorkspace;")
    assert body.rindex("'INVALID_HOLE_TYPE'") < body.index("PCBServer.PreProcess;")


def test_the_drill_type_is_written_before_the_hole_sizes():
    body = _set()
    kind = body.index("If HoleTypeStr = 'slot' Then Pad.HoleType := eSlotHole")
    assert kind < body.index("Str := ExtractJsonValue(Params, 'hole_size');")
    assert kind < body.index("Str := ExtractJsonValue(Params, 'hole_width');")
    assert "Else If HoleTypeStr = 'square' Then Pad.HoleType := eSquareHole" in body
    assert "Else Pad.HoleType := eRoundHole;" in body


def test_each_pad_reads_its_drill_type_back():
    assert "',\"hole_type\":\"' + PadHoleKind(Pad) + '\"'" in _set()
    kind = _lib()["PadHoleKind"]
    assert "If Pad.HoleType = eSlotHole Then Result := 'slot'" in kind
    assert "Else If Pad.HoleType = eSquareHole Then Result := 'square';" in kind


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


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(library_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    library_tools.register_library_tools(mcp)
    out = asyncio.run(mcp.tools["lib_set_pad_properties"](**kw))
    return out, bridge.sent


def test_selected_and_hole_type_reach_the_script(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="F", selected=True,
                    hole_type="slot", hole_size=0.7, hole_width=1.34, units="mm")
    assert sent[0][0] == "library.set_pad_properties"
    params = sent[0][1]
    assert params["selected"] == "true"
    assert params["hole_type"] == "slot"
    assert params["pad_name"] == ""


def test_a_name_without_selected_sends_no_selected_flag(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="F", pad_name="1", x_size=60)
    assert "selected" not in sent[0][1] and sent[0][1]["pad_name"] == "1"


def test_neither_a_name_nor_a_selection_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, footprint_name="F", x_size=60)
    assert out["success"] is False and not sent


def test_a_bad_or_conflicting_hole_type_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, footprint_name="F", pad_name="1", hole_type="oval")
    assert out["success"] is False and not sent
    out, sent = _call(monkeypatch, footprint_name="F", pad_name="1",
                      hole_type="round", hole_width=1.2)
    assert out["success"] is False and not sent


def test_a_hole_type_alone_is_a_change(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="F", selected=True, hole_type="round")
    assert sent and sent[0][1]["hole_type"] == "round"
