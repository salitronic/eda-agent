# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A PcbLib pad is moved, renumbered and deleted by its designator, and a
footprint's description is written and read back."""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import library

LIBRARY = (Path(__file__).resolve().parents[1] / "scripts" / "altium"
           / "Library.pas").read_text(encoding="utf-8-sig")


def _routine(name: str) -> str:
    at = LIBRARY.index(f"Function {name}(")
    return LIBRARY[at:LIBRARY.index("\nEnd;", at)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    library.register_library_tools(_Server())
    return tools, bridge


def test_a_move_takes_one_pad_from_the_footprint_origin_and_a_rename_is_read_back():
    body = _routine("Lib_SetPadProperties")
    move = body[body.index("If (XStr <> '') Or (YStr <> '') Then"):]
    assert move.index("If MovedOne Then") < move.index("Ok := False;") < move.index(
        "MovedOne := True;")
    assert "Pad.X := XOrg + CoordFromUnits(StrToFloatDef(XStr, 0), UnitsStr);" in move
    assert "Pad.Y := YOrg + CoordFromUnits(StrToFloatDef(YStr, 0), UnitsStr);" in move
    assert "If Pad.Name <> NewName Then Ok := False;" in body
    assert body.index("Not IsFloatStr(XStr)") < body.index("Workspace := GetWorkspace;")


def test_the_pad_edit_arguments_reach_the_bridge(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["lib_set_pad_properties"](
        footprint_name="SOT23", pad_name="3", x=-37.5, y=0, new_name="4"))
    sent = bridge.sent[-1][1]
    assert sent["x"] == "-37.5" and sent["y"] == "0" and sent["new_name"] == "4"


def test_a_pad_delete_names_its_designator_and_nothing_else(monkeypatch):
    picked = _routine("FootprintPrimPicked")
    assert "If Prim.ObjectId = ePadObject Then" in picked
    assert "Result := UpperCase(Name) = UpperCase(PadName);" in picked
    count = _routine("CountFootprintMatches")
    assert "FootprintPrimPicked(Prim, IdentStr, PadName)" in count
    delete = _routine("Lib_DeleteFootprintPrimitives")
    assert delete.count("FootprintPrimPicked(Prim, IdentStr, PadName)") == 1
    assert "IncludePads := True;" in delete and "If ObjTypeStr = '' Then ObjTypeStr := 'pad';" in delete
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["lib_delete_footprint_primitives"](
        footprint_name="SOT23", pad_name="2", confirm=True))
    assert bridge.sent[-1][1]["pad_name"] == "2"
    refused = asyncio.run(tools["lib_delete_footprint_primitives"](
        footprint_name="SOT23", pad_name="2", object_type="track", confirm=True))
    assert refused["ok"] is False and len(bridge.sent) == 1


def test_a_footprint_description_is_read_back_after_the_write():
    body = _routine("Lib_SetFootprintDescription")
    assert body.index("Target.Description := Wanted;") < body.index("Got := Target.Description;")
    assert "BoolToJsonStr(Got = Wanted)" in body
    assert "'set_footprint_description': Result := Lib_SetFootprintDescription(Params, RequestId);" in LIBRARY


def test_a_named_footprint_is_handed_back_as_the_editor_holds_it():
    body = _routine("PcbLibTarget")
    tail = body[body.index("If CurName <> Name Then"):]
    assert "Else\n" in tail and tail.rstrip().endswith("Result := Cur;")
