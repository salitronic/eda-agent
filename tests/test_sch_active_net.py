# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The compiled net of the object selected on a sheet.

A wire carries no net name of its own, so the generic schematic query
has none to give. sch_get_active_net finds the first selected net label,
port, power port or wire with one filtered walk and asks the compiled
netlist for the net holding it, by the object's handle. What the tool
sends, what it refuses before Altium sees it, and the Pascal shapes that
keep the read from writing, from walking the whole sheet, or from using
the member that stops the script.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import generic

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(name: str) -> str:
    text = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return captured["sch_get_active_net"], bridge


def test_sends_route_path_and_compile(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool())
    assert bridge.sent == [("generic.get_active_net", {"route": "auto"})]
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(document_path=" C:/p/A.SchDoc ", route="Lines", compile=True))
    assert bridge.sent == [("generic.get_active_net",
                            {"route": "lines", "path": "C:/p/A.SchDoc",
                             "compile": "true"})]


@pytest.mark.parametrize("route", ["wire", "all", "net"])
def test_unknown_route_refused_before_altium(monkeypatch, route):
    tool, bridge = _tool(monkeypatch)
    result = asyncio.run(tool(route=route))
    assert bridge.sent == [] and result["ok"] is False


def test_dispatched():
    text = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    assert re.search(r"'get_active_net':\s+Result := Gen_GetActiveNet\(Params, RequestId\);",
                     text)


def test_one_filtered_walk_stopping_at_the_first_selected():
    body = _body("Gen_GetActiveNet")
    assert "AddFilter_ObjectSet(MkSet(eNetLabel, ePort, ePowerObject, eWire))" in body
    assert "SetState_IterationDepth(eIterateFirstLevel)" in body
    walk = body[body.index("If Obj.Selection Then"):body.index("SchIterator_Destroy(It)")]
    assert "Break;" in walk
    # The netlist is asked only after the walk is closed.
    assert body.index("SchIterator_Destroy(It)") < body.index("DM_GetNet(Handle)")
    assert body.index("SchIterator_Destroy(It)") < body.index("GanNetByHandle(")


def test_read_only_and_compiles_only_when_asked():
    body = _body("Gen_GetActiveNet")
    for writer in ("SchBeginEdit", "SCHM_BeginModify", "PreProcess", "AddSchObject",
                   "RemoveSchObject", "MarkDocDirty"):
        assert writer not in body, writer
    assert re.search(r"If ExtractJsonValue\(Params, 'compile'\) = 'true' Then\s+Begin\s+"
                     r"SmartCompile\(Project\);", body)


def test_routes():
    body = _body("Gen_GetActiveNet")
    assert "'BAD_ROUTE'" in body
    # auto tries the one lookup first and walks the nets only when it found nothing.
    assert re.search(r"If \(Route = 'auto'\) Or \(Route = 'getnet'\) Then", body)
    assert re.search(r"If \(Net = ''\) And \(\(Route = 'auto'\) Or \(Route = 'lines'\)\) Then",
                     body)
    walk = _body("GanNetByHandle")
    # A wire is found among the net's lines, anything else among its items.
    assert "If IsWire Then Item := Net.DM_Lines(J) Else Item := Net.DM_AllNetItems(J);" in walk
    assert "If Item.DM_SchHandle = Handle Then" in walk


def test_no_member_that_stops_the_script():
    # Reading Count on the IStrings DM_GetNetNamesForObject returns puts the
    # script in break on AD 25.
    assert "DM_GetNetNamesForObject" not in _body("Gen_GetActiveNet")
    assert "DM_GetNetNamesForObject" not in _body("GanNetByHandle")


def test_nothing_selected_and_no_net_are_told_apart():
    body = _body("Gen_GetActiveNet")
    assert "Net := '<none>';" in body
    # Found: the net starts empty, so "" means the netlist had none.
    found = body[body.index("If Found Then"):]
    assert found.index("Net := '';") < found.index("DM_GetNet(Handle)")
    assert "'found'" in body or '"found"' in body
