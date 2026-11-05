# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sch_set_sheet_size reopens the sheet so ERC judges the new frame.

Measured on AD 26.11: a label at y=9500 on a sheet made A3 was still
"Off sheet" in the ERC through a save and a forced recompile, and only
cleared once the sheet was closed and opened again. A demo run had
switched the ERC check off to get a clean result.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import generic as gen_tools

_SHEET = r"C:\work\LEDs.SchDoc"


def _tool(monkeypatch, sent, active=_SHEET):
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
            if command == "application.get_active_document":
                return {"file_path": active}
            return {"success": True}

    monkeypatch.setattr(gen_tools, "get_bridge", lambda: _Bridge())
    gen_tools.register_generic_tools(_Mcp())
    return tools["sch_set_sheet_size"]


def test_the_sheet_is_saved_closed_reopened_and_focused(monkeypatch):
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent)(style="A3"))
    assert out["reopened"]["done"] is True
    assert [c for c, _ in sent] == [
        "generic.set_sheet_size", "application.get_active_document",
        "application.save_document", "application.close_document",
        "application.run_process", "application.set_active_document"]
    assert sent[-1][1]["file_path"] == _SHEET


def test_a_document_that_is_not_a_saved_sheet_is_left_open(monkeypatch):
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent, active="")(style="A3"))
    assert out["reopened"]["done"] is False
    assert "application.close_document" not in [c for c, _ in sent]
