# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Argument shapes a demo run sent and was refused on.

pcb_set_layer_visibility got ``layers`` as a JSON list in text, and
app_press_dialog_button got ``button``; both failed validation before
doing anything, while Altium sat behind a modal in the second case.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import pcb as pcb_tools
from eda_agent.tools import uiauto


def _register(module, register, monkeypatch, sent):
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

    monkeypatch.setattr(module, "get_bridge", lambda: _Bridge(), raising=False)
    register(_Mcp())
    return tools


def test_several_layers_at_once_even_as_json_text(monkeypatch):
    sent = []
    tools = _register(pcb_tools, pcb_tools.register_pcb_tools, monkeypatch, sent)
    out = asyncio.run(tools["pcb_set_layer_visibility"](
        layers='["TopLayer", "BottomLayer"]', visible=True))
    assert [p["layer"] for _, p in sent] == ["TopLayer", "BottomLayer"]
    assert out["success"] is True and out["failed"] == []


def test_one_layer_still_goes_as_before(monkeypatch):
    sent = []
    tools = _register(pcb_tools, pcb_tools.register_pcb_tools, monkeypatch, sent)
    asyncio.run(tools["pcb_set_layer_visibility"](layer="TopOverlay", visible=False))
    assert sent == [("pcb.set_layer_visibility", {"layer": "TopOverlay", "visible": "false"})]


def test_press_takes_button_for_the_caption(monkeypatch):
    sent = []
    tools = _register(uiauto, uiauto.register_uiauto_tools, monkeypatch, sent)
    seen = {}

    def _pid(reads_only=False):
        seen["asked"] = True
        return None, {"ok": False, "reason": "stop here"}

    monkeypatch.setattr(uiauto, "_altium_pid", _pid)
    out = asyncio.run(tools["app_press_dialog_button"](button="OK"))
    assert seen.get("asked") and out["reason"] == "stop here"
    out = asyncio.run(tools["app_press_dialog_button"]())
    assert out["ok"] is False and "caption" in out["reason"]
