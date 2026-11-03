# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A board's special strings: the names Altium offers and their values.

MEASURED (AD 25.8.1): the converter's name walk (FirstSpecialStringName /
NextSpecialStringName) gave 66 names and GetBoardSpecificSpecialStrings one
more. Only a text placed ON the board resolved: a placed ".Layer_Count" read
"8", while the converter's Convert answered True with an empty string, an
unplaced text's ConvertedString was empty and the project's DM_TryToResolve*
answered False. So resolve places a temporary text per name, reads its
ConvertedString and removes the text again in the same call.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import pcb

REPO = Path(__file__).resolve().parents[1]
ALTIUM = REPO / "scripts" / "altium"


def _routine(name: str) -> str:
    text = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    head = f"Function {name}("
    at = text.index(head)
    return text[at:text.index("\nEnd;", at)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def keep(fn):
            self.tools[fn.__name__] = fn
            return fn
        return keep

    def __getattr__(self, name):
        return lambda *a, **k: (lambda fn: fn)


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return mcp.tools["pcb_special_strings"], bridge


# ---- the MCP tool ----------------------------------------------------------

def test_list_is_the_default_and_sends_no_names(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool())
    asyncio.run(tool(action=" LIST ", names=".Layer_Count"))
    assert bridge.sent == [("pcb.special_strings", {"action": "list"})] * 2


def test_resolve_sends_the_names_with_empty_items_dropped(monkeypatch):
    # Mixed text is sent as written: its spaces are part of what is drawn.
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(action="resolve",
                     names=".Layer_Count||Rev .Layer_Count/.Via_Count| |"))
    assert bridge.sent == [("pcb.special_strings", {
        "action": "resolve", "names": ".Layer_Count|Rev .Layer_Count/.Via_Count"})]


def test_text_beyond_latin1_goes_through_unchanged(monkeypatch):
    # A text carries any character (measured: Ω, ≤ and 中 came
    # back exact from a placed text), so nothing is refused here.
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(action="resolve", names=".Layer_Count Ω|中 .Via_Count"))
    assert bridge.sent == [("pcb.special_strings", {
        "action": "resolve", "names": ".Layer_Count Ω|中 .Via_Count"})]


def test_bad_requests_are_refused_before_altium_is_asked(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    for kwargs in ({"action": "convert"},
                   {"action": "convert", "names": ".Layer_Count"},
                   {"action": ""},
                   {"action": "resolve"},
                   {"action": "resolve", "names": " | |"},
                   {"action": "resolve", "names": "|".join([".X"] * 201)}):
        assert asyncio.run(tool(**kwargs))["ok"] is False, kwargs
    assert bridge.sent == []


def test_two_hundred_names_go_through(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(action="resolve", names="|".join([".X"] * 200)))
    assert len(bridge.sent) == 1


# ---- the Pascal handler ----------------------------------------------------

def test_the_command_is_dispatched():
    dispatch = _routine("HandlePCBCommand")
    assert "'special_strings':" in dispatch
    assert "PCB_SpecialStrings(Params, RequestId)" in dispatch


def test_bad_requests_are_refused_before_the_board_is_touched():
    body = _routine("PCB_SpecialStrings")
    assert "If Action = '' Then Action := 'list';" in body
    assert "(Action <> 'list') And (Action <> 'resolve')" in body
    assert "'BAD_ACTION'" in body
    assert "(Action = 'resolve') And (Trim(Names) = '')" in body
    board = body.index("Board := GetPCBBoardAnywhere(0);")
    assert body.index("'MISSING_PARAM'") < board
    assert "If Asked.Count > 200 Then" in body and "'TOO_MANY'" in body


def test_list_walks_the_converter_names_with_a_cap():
    body = _routine("PCB_SpecialStrings")
    # Held in a Variant, as proven live.
    assert re.search(r"\bConv, Wide\s*:\s*Variant;", body)
    assert "Conv := PCBServer.SpecialStringConverter;" in body
    assert "S := Conv.FirstSpecialStringName;" in body
    loop = body[body.index("S := Conv.FirstSpecialStringName;"):
                body.index("Capped := S <> '';")]
    assert "While (S <> '') And (N < 2000) Do" in loop
    assert "S := Conv.NextSpecialStringName;" in loop
    assert "Inc(N);" in loop
    assert "JsonBool('capped', Capped)" in body


def test_list_adds_the_board_specific_names():
    body = _routine("PCB_SpecialStrings")
    assert "Wide := Conv.GetBoardSpecificSpecialStrings(Board);" in body
    assert "BoardCount := Wide.Count;" in body
    assert "S := Wide.Strings[I];" in body
    assert '"board_names":[' in body


def test_every_name_is_placed_on_the_board_and_read_through_convertedstring():
    body = _routine("PCB_SpecialStrings")
    loop = body[body.index("For I := 0 To Asked.Count - 1 Do"):body.index("Finally")]
    assert "PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default)" in loop
    assert "Txt.Text := One;" in loop
    assert "Txt.Layer := eTopOverlay;" in loop
    add = loop.index("Board.AddPCBObject(Txt);")
    read = loop.index("Value := Txt.ConvertedString;")
    # Read only once the text is on the board: an unplaced text reads empty.
    assert add < read
    assert "Texts.Add(Txt);" in loop
    assert "JsonBool('resolved', (Value <> '') And (Value <> One))" in loop
    # Routes that returned nothing live are not used.
    assert ".Convert(" not in body
    assert "DM_TryToResolve" not in body


def test_the_temporary_texts_are_removed_in_the_same_edit():
    body = _routine("PCB_SpecialStrings")
    begin = body.index("PcbBeginEdit(0);")
    fin = body.index("Finally", begin)
    end = body.index("PcbEndEdit(0);")
    assert begin < body.index("Board.AddPCBObject(Txt);") < fin < end
    cleanup = body[fin:end]
    register = r"SendMessageToRobots\(Board\.I_ObjectAddress, c_Broadcast,\s+PCBM_BoardRegisteration, "
    assert "For I := 0 To Texts.Count - 1 Do" in cleanup
    assert re.search(register + r"Txt\.I_ObjectAddress\);\s+Board\.RemovePCBObject\(Txt\);",
                     cleanup)
    assert "Inc(Removed);" in cleanup
    # Closed in the Finally, after the texts are gone.
    assert re.search(r"Inc\(Removed\);\s+End;\s+PcbEndEdit\(0\);\s+End;", body)
    # One edit for the whole call: a single begin / end pair.
    assert body.count("PcbBeginEdit(0);") == 1 and body.count("PcbEndEdit(0);") == 1
    # The list holds board references and is never freed.
    assert "Texts := TInterfaceList.Create;" in body
    assert "Texts.Free" not in body
    assert "JsonInt('removed', Removed)" in body


def test_nothing_is_left_so_the_board_is_not_marked_modified_by_us():
    body = _routine("PCB_SpecialStrings")
    assert "MarkDocDirtyByPath" not in body
