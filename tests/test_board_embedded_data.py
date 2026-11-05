# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Named text records kept inside a board file, out of sight.

A board had no place for an application's own data other than visible
texts, which a designer can move, delete or print. pcb.embedded_data keeps
plain text in Altium's embedded objects (eEmbeddedObject): saved with the
PcbDoc, back after close and reopen, neither drawn nor selectable.

MEASURED (AD 25.8.1): records of 69 bytes and 100 kB written this way read
back whole after a save, close and reopen; nothing showed on the canvas.
IPCB_Embedded is not in the script engine's identifier table, so a record
is held in a Variant.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

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
    return mcp.tools["pcb_embedded_data"], bridge


# ---- the MCP tool ----------------------------------------------------------

def test_each_action_sends_only_what_it_needs(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(action="list"))
    asyncio.run(tool(action=" READ ", name="cards"))
    asyncio.run(tool(action="write", name="cards", text="v1"))
    asyncio.run(tool(action="delete", name="cards"))
    assert bridge.sent == [
        ("pcb.embedded_data", {"action": "list"}),
        ("pcb.embedded_data", {"action": "read", "name": "cards"}),
        ("pcb.embedded_data", {"action": "write", "name": "cards", "text": "v1"}),
        ("pcb.embedded_data", {"action": "delete", "name": "cards"}),
    ]


def test_name_and_text_go_unchanged(monkeypatch):
    # The record is found by an exact name, and the text must come back as
    # written: neither is trimmed.
    tool, bridge = _tool(monkeypatch)
    text = ' {"a": "b\\\\c", "q": "say \\"hi\\""}\r\nline 2\ttab \n'
    asyncio.run(tool(action="write", name=" My Tool ", text=text))
    assert bridge.sent == [("pcb.embedded_data",
                            {"action": "write", "name": " My Tool ", "text": text})]


def test_text_is_offered_as_a_plain_string(monkeypatch):
    # Offered "string or null", Claude Code parsed a JSON-looking text into
    # an object before sending it, and the call was refused. A plain string
    # reached a tool as the text itself.
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    tool, _ = _tool(monkeypatch)
    model = func_metadata(tool).arg_model
    assert model.model_json_schema()["properties"]["text"]["type"] == "string"
    assert "anyOf" not in model.model_json_schema()["properties"]["text"]
    assert model.model_validate({"action": "list"}).text is None
    assert model.model_validate({"action": "write", "text": ""}).text == ""


def test_an_object_is_refused_not_rewritten(monkeypatch):
    # Data goes through untouched, so there is no conversion to fall back on.
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata
    from pydantic import ValidationError

    tool, _ = _tool(monkeypatch)
    model = func_metadata(tool).arg_model
    for value in ({"a": 1}, [1, 2]):
        with pytest.raises(ValidationError):
            model.model_validate({"action": "write", "name": "n", "text": value})
    payload = '{"a": 1, "µ": "±"}'
    assert model.model_validate(
        {"action": "write", "name": "n", "text": payload}).text == payload


def test_an_empty_text_is_a_write_and_a_missing_one_is_refused(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(action="write", name="n", text=""))
    assert bridge.sent == [("pcb.embedded_data",
                            {"action": "write", "name": "n", "text": ""})]
    assert asyncio.run(tool(action="write", name="n"))["ok"] is False
    assert len(bridge.sent) == 1


def test_text_beyond_latin1_is_refused_and_latin1_goes_through(monkeypatch):
    # Measured live: the bridge stored Ω as "O" and ≤ as "="
    # while é and µ came back exact. Refuse rather than store
    # the text changed.
    tool, bridge = _tool(monkeypatch)
    out = asyncio.run(tool(action="write", name="n", text="Ω ≤ 5"))
    assert out["ok"] is False and "U+03A9" in out["reason"]
    assert asyncio.run(tool(action="read", name="résumé →"))["ok"] is False
    assert bridge.sent == []
    asyncio.run(tool(action="write", name="café", text="5 µm ÿ"))
    assert bridge.sent == [("pcb.embedded_data", {"action": "write",
                            "name": "café", "text": "5 µm ÿ"})]


def test_bad_requests_are_refused_before_altium_is_asked(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    for kwargs in ({"action": "append", "name": "n"},
                   {"action": ""},
                   {"action": "read"},
                   {"action": "read", "name": "   "},
                   {"action": "delete", "name": ""},
                   {"action": "write", "name": "", "text": "x"}):
        assert asyncio.run(tool(**kwargs))["ok"] is False, kwargs
    assert bridge.sent == []


def test_the_pascal_side_finds_every_key_in_the_request_as_sent():
    # ExtractJsonValue finds a key by its first '"key"' in the request.
    # A text holding the same words must not be taken for them: inside a
    # JSON string every quote is escaped, so '"name"' cannot match there.
    params = {"action": "write", "name": "real",
              "text": '{"name": "decoy", "action": "delete", "text": "x"}'}
    request = json.dumps(params, indent=2, ensure_ascii=False)
    for key in ("action", "name", "text"):
        assert request.count(f'"{key}"') == 1
    assert json.loads(request) == params


def test_the_unescape_handles_every_escape_a_text_can_need():
    # The text arrives through UnescapeJsonString, which must know each
    # escape json.dumps writes for a plain-text record.
    main = (ALTIUM / "Main.pas").read_text(encoding="utf-8-sig")
    at = main.index("Function UnescapeJsonString(")
    body = main[at:main.index("\nEnd;", at)]
    for esc, char in (("\\", "'\\'"), ("n", "#10"), ("r", "#13"), ("t", "#9"),
                      ('"', "'\"'")):
        assert f"NextCh = '{esc}' Then Begin Result := Result + {char};" in body, esc


# ---- the Pascal handler ----------------------------------------------------

def test_the_command_is_dispatched():
    dispatch = _routine("HandlePCBCommand")
    assert "'embedded_data':" in dispatch
    assert "PCB_EmbeddedData(Params, RequestId)" in dispatch


def test_the_walk_is_filtered_to_embedded_objects():
    body = _routine("PCB_EmbeddedData")
    assert "Iter.AddFilter_ObjectSet(MkSet(eEmbeddedObject));" in body
    assert "AllObjects" not in body
    # IPCB_Embedded is not in the engine's identifier table: a Variant.
    assert re.search(r"\bE\s*:\s*Variant;", body)
    assert "IPCB_Embedded" not in body.split("Begin", 1)[0]


def test_records_are_removed_only_after_the_iterator_is_destroyed():
    body = _routine("PCB_EmbeddedData")
    destroyed = body.index("Board.BoardIterator_Destroy(Iter);")
    assert "Board.RemovePCBObject(P);" in body
    assert body.index("Board.RemovePCBObject(P);") > destroyed
    assert body.index("E.Description := Text;") > destroyed
    # Collected in a list that is never freed (it holds board references).
    assert "Matches := TInterfaceList.Create;" in body
    assert "Matches.Free" not in body
    collect = body[:destroyed]
    assert "Matches.Add(P);" in collect
    assert "(Action = 'list') Or (E.Name = Name)" in collect


def test_bad_requests_are_refused():
    body = _routine("PCB_EmbeddedData")
    assert "'BAD_ACTION'" in body
    assert "(Action <> 'list') And (Trim(Name) = '')" in body
    assert "'MISSING_PARAM'" in body
    assert "'NO_PCB'" in body
    # Refused before the board is touched.
    assert body.index("'MISSING_PARAM'") < body.index("Board := GetPCBBoardAnywhere(0);")


def test_a_write_updates_in_place_or_creates_on_mechanical_1():
    body = _routine("PCB_EmbeddedData")
    assert "E.BeginModify;" in body and "E.EndModify;" in body
    create = body[body.index("If (Action = 'write') And (Matches.Count = 0) Then"):]
    assert "PCBServer.PCBObjectFactory(eEmbeddedObject, eNoDimension, eCreate_Default)" in create
    assert "E.Name := Name;" in create
    assert "E.Description := Text;" in create
    assert "E.Layer := LayerUtils.MechanicalLayer(1);" in create
    assert "Board.AddPCBObject(E);" in create
    # The reply says what the board holds now, read back.
    # Both the updated records and a new one are read back.
    assert body.count("If E.Description <> Text Then Exact := False;") == 2
    assert "JsonBool('exact', Exact)" in body


def test_reads_carry_the_whole_text_and_lists_a_head():
    body = _routine("PCB_EmbeddedData")
    assert "JsonStr('text', E.Description)" in body
    assert "JsonStr('head', Copy(E.Description, 1, 200))" in body
    assert "JsonStr('layer', LayerDisplayName(E.Layer))" in body


def test_a_change_marks_the_board_modified():
    body = _routine("PCB_EmbeddedData")
    assert "If (Updated + Created + Deleted) > 0 Then MarkDocDirtyByPath(Board.FileName);" in body


def test_a_write_or_delete_is_one_undo_step():
    body = _routine("PCB_EmbeddedData")
    assert "Edits := (Action = 'write') Or (Action = 'delete');" in body
    begin = body.index("If Edits Then PcbBeginEdit(0);")
    end = body.index("If Edits Then PcbEndEdit(0);")
    assert re.search(r"Finally\s+If Edits Then PcbEndEdit\(0\);", body)
    edits = body[begin:end]
    register = r"SendMessageToRobots\(Board\.I_ObjectAddress, c_Broadcast,\s+PCBM_BoardRegisteration, "
    assert re.search(register + r"P\.I_ObjectAddress\);\s+Board\.RemovePCBObject\(P\);", edits)
    assert re.search(r"Board\.AddPCBObject\(E\);\s+PCBServer\." + register + r"E\.I_ObjectAddress\);",
                     edits)
