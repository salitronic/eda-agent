# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_get_pin_list reads a named library even while a sheet is in front.

Measured in a demo run: after drawing a sheet, four pin reads in a row
answered "No schematic library is active" because the new sheet was in
front. With ``library_path`` the tool brings that library forward first.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import library as lib_tools

_LIB = r"C:\work\Parts.SchLib"


def _tool(monkeypatch, sent):
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
            if command == "library.get_pin_list":
                return {"component": "U", "count": 1, "pins": [{"name": "VDD"}]}
            return {"success": True}

    monkeypatch.setattr(lib_tools, "get_bridge", lambda: _Bridge())
    lib_tools.register_library_tools(_Mcp())
    return tools["lib_get_pin_list"]


def test_the_library_is_focused_before_the_read(monkeypatch):
    sent = []
    tool = _tool(monkeypatch, sent)
    out = asyncio.run(tool("U", library_path=_LIB))
    assert out["count"] == 1
    assert [c for c, _ in sent] == ["application.set_active_document",
                                    "library.get_pin_list"]
    assert sent[0][1]["file_path"] == _LIB


def test_without_a_path_nothing_is_refocused(monkeypatch):
    sent = []
    tool = _tool(monkeypatch, sent)
    asyncio.run(tool("U"))
    assert [c for c, _ in sent] == ["library.get_pin_list"]
