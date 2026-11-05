# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The board view can be read whole and changed in one call.

get_layer_display walks the legacy layer range, which stops at
Mechanical16, and set_layer_visibility takes one layer at a time; neither
says which layer is current, whether single layer mode is on, or which
mechanical layers stay drawn in it (the Ctrl+click lock in View
Configuration, DisplayInSingleLayerMode). A caller putting the user's
view back after a working view needs all of it.
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
    assert "'layer_view':              Result := PCB_LayerView(Params, RequestId);" in \
        _PCB.read_text(encoding="utf-8")


def test_the_reply_reaches_past_mechanical16():
    body = _fn("PCB_LayerView")
    above = body.index("For N := 17 To 1024 Do")
    assert body.index("Lyr := MechLayerFromNumber(N);", above) > above
    # only the layers the board uses, not 1008 empty slots
    assert "If Not Enabled Then Continue;" in body[above:]


def test_a_user_name_above_16_resolves():
    body = _fn("LvResolve")
    assert body.index("Result := ResolveLayerId(Board, Name);") < body.index(
        "For N := 17 To 1024 Do")
    assert "NormalizeLayerName(ThisName) = Wanted" in body


def test_every_write_is_read_back():
    body = _fn("PCB_LayerView")
    assert "Got := Board.LayerIsDisplayed[Lyr];" in body
    assert "Got := MechObj.DisplayInSingleLayerMode;" in body
    assert "Cur := Board.CurrentLayer;" in body
    assert "Got := Opts.SingleLayerMode;" in body
    assert body.count("NotApplied := LvAddNote(NotApplied") >= 4


def test_the_current_layer_is_checked_after_the_view_update():
    """Altium reads a hidden layer back as current, then falls back to
    another one when the layer tabs are rebuilt (seen live)."""
    body = _fn("PCB_LayerView")
    update = body.index("Try Board.ViewManager_UpdateLayerTabs; Except End;")
    check = body.index("If (WantCur <> eNoLayer) And (Cur <> WantCur) Then")
    assert update < check


def test_the_lock_is_only_tried_on_a_mechanical_layer():
    body = _fn("PCB_LayerView")
    guard = body.index("If Not LvIsMech(Lyr) Then")
    assert guard < body.index("MechObj.DisplayInSingleLayerMode := Lock;")


def test_the_lock_is_read_only_on_a_mechanical_layer():
    body = _fn("LvLayerJson")
    assert body.index("If Mech Then") < body.index("MechObj.DisplayInSingleLayerMode")


def test_view_state_does_not_mark_the_board_modified():
    assert "MarkDocDirtyByPath" not in _fn("PCB_LayerView")


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


def test_no_arguments_only_reads(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    asyncio.run(tools["pcb_layer_view"]())
    assert sent == [("pcb.layer_view", {})]


def test_lists_go_as_text_and_the_mode_as_true_false(monkeypatch):
    sent = []
    tools = _register(monkeypatch, sent)
    asyncio.run(tools["pcb_layer_view"](
        show=["TopLayer", "Fluidgon"], hide='["BottomLayer"]', lock="Fluidgon",
        current_layer="TopLayer", single_layer_mode=False))
    assert sent == [("pcb.layer_view", {
        "show": "TopLayer,Fluidgon", "hide": "BottomLayer", "lock": "Fluidgon",
        "current_layer": "TopLayer", "single_layer_mode": "false"})]
