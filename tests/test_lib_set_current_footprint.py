# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A named footprint can be made the PCB library's current one.

The footprint the editor shows is the one scope "active_doc" and an
authoring call given no footprint name act on. lib_set_current_footprint
switches it by name, reads the switch back, and writes nothing.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import library
from tests.pascal_source import load

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body() -> str:
    return re.sub(r"\s+", " ", load(_PAS)["Lib_SetCurrentFootprint"])


def test_the_handler_switches_by_name_and_writes_nothing():
    body = _body()
    assert "PcbLibTarget(PcbLib, Name, Problem)" in body
    assert "FocusPcbLibAt(LibPath)" in body
    for write in ("PcbBeginEdit", "MarkDocDirty", "AddPCBObject", "SetModified"):
        assert write not in body
    text = _PAS.read_text(encoding="utf-8-sig")
    assert "'set_current_footprint': Result := Lib_SetCurrentFootprint(Params, RequestId);" in text


def test_the_tool_sends_the_name_and_the_library(monkeypatch):
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(library, "get_bridge", lambda: _Bridge())
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    library.register_library_tools(_Mcp())
    asyncio.run(captured["lib_set_current_footprint"]("SOT23", library_path=r" C:\l\L.PcbLib "))
    asyncio.run(captured["lib_set_current_footprint"]("SOT23"))
    assert sent == [("library.set_current_footprint",
                     {"name": "SOT23", "library_path": r"C:\l\L.PcbLib"}),
                    ("library.set_current_footprint", {"name": "SOT23"})]
