# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""library.get_footprints says which footprint the editor has current.

Writers called without footprint_name write into the library's current
footprint, Board.XOrigin reads the origin of the current one only, and
processes such as PCB:SetComponentReference act on the footprint on
screen, yet nothing told a client which one that is. The reply now
carries ``current``: the name of PcbLib.CurrentComponent, or "" when
there is none, read in the same library the footprints were listed from.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import library as library_tools

from tests.pascal_source import load

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body():
    return load(_LIB, minimum=20)["Lib_GetFootprints"]


def test_the_reply_carries_the_current_name():
    body = _body()
    assert "',\"current\":\"' + EscapeJsonString(CurName) + '\"'" in body


def test_current_is_read_by_name_through_typed_locals():
    """Name only: I_ObjectAddress off a footprint faulted on AD 26.11."""
    body = _body()
    assert "Cur : IPCB_LibComponent;" in body
    assert "Try Cur := PcbLib.CurrentComponent; Except End;" in body
    assert "If Cur <> Nil Then Try CurName := Cur.Name; Except End;" in body
    assert "I_ObjectAddress" not in body


def test_current_starts_empty_and_comes_from_the_listed_library():
    body = _body()
    read = body.index("Try Cur := PcbLib.CurrentComponent;")
    # Cleared before the read, so a library with no current footprint
    # answers "" rather than whatever the variable held.
    assert body.index("CurName := '';") < read
    assert body.index("Cur := Nil;") < read
    # The same PcbLib the iterator walked, not a second lookup.
    assert body.index("PcbLib := PCBServer.GetCurrentPCBLibrary;") < read
    assert body.count("GetCurrentPCBLibrary") == 1


def test_the_command_is_routed():
    assert "'get_footprints':       Result := Lib_GetFootprints(Params, RequestId);" \
        in _LIB.read_text(encoding="utf-8")


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self, reply):
        self.reply = reply
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return self.reply


def _tool(monkeypatch, reply):
    bridge = _Bridge(reply)
    monkeypatch.setattr(library_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    library_tools.register_library_tools(mcp)
    return mcp.tools["lib_get_footprints"], bridge


def test_the_tool_passes_current_through(monkeypatch):
    reply = {"library_path": "C:/x.PcbLib", "count": 2, "current": "SO8",
             "footprints": [{"name": "SO8", "description": ""},
                            {"name": "QFN", "description": ""}]}
    tool, bridge = _tool(monkeypatch, reply)
    out = asyncio.run(tool())
    assert out["current"] == "SO8"
    assert bridge.sent == [("library.get_footprints", {})]


def test_the_docstring_names_the_field(monkeypatch):
    tool, _ = _tool(monkeypatch, {})
    doc = tool.__doc__ or ""
    assert "``current``" in doc
