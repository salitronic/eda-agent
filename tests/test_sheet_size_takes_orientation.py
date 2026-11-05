# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sch_set_sheet_size passes an orientation to the handler that reads it.

Gen_SetSheetSize has always taken orientation on its own, but the tool
required a style and never sent one, so no caller could turn a sheet.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import generic


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, timeout=None):
        self.sent.append((command, params))
        return {"success": True}


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    generic.register_generic_tools(DummyMcp())
    return captured["sch_set_sheet_size"], bridge


@pytest.mark.asyncio
async def test_orientation_alone_reaches_the_handler(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    await tool(orientation="Landscape")
    assert bridge.sent[:1] == [("generic.set_sheet_size", {
        "style": "", "custom_width": "0", "custom_height": "0",
        "orientation": "landscape"})]


@pytest.mark.asyncio
async def test_a_style_alone_sends_what_it_always_sent(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    await tool("A3")
    assert bridge.sent[:1] == [("generic.set_sheet_size", {
        "style": "A3", "custom_width": "0", "custom_height": "0"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"orientation": "sideways"}, {}])
async def test_nothing_is_sent_without_a_usable_request(monkeypatch, kwargs):
    tool, bridge = _tool(monkeypatch)
    reply = await tool(**kwargs)
    assert reply["success"] is False and bridge.sent == []
