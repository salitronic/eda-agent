# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A board region's Name can be read, written, filtered on and placed.

The generic PCB layer knew Name only for components and pads, and
pcb_place_region could not set it, so a tool that places regions could not
find its own again except by geometry, which the user edits. IPCB_Region.Name
is a plain string: Altium's IterateRegions.pas reads it, GenerateSamples.pas
writes it. Empty is allowed and clears the tag; a pad keeps refusing an
empty name.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic():
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)


def _name_read_branch():
    body = _generic()["GetPCBProperty"]
    start = body.index("Else If PropName = 'Name' Then")
    return body[start:body.index("Else If", start + 10)]


def _set_body():
    return _generic()["SetPCBProperty"]


def test_a_region_reads_its_name():
    assert "If Oid = eRegionObject Then Begin Rgn := Obj; Result := Rgn.Name; End;" \
        in _name_read_branch()


def test_a_region_writes_its_name_before_the_pad_checks_can_refuse_it():
    body = _set_body()
    start = body.index("Else If (PropName = 'Name') Or (PropName = 'Designator') Then")
    branch = body[start:body.index("Else If (PropName", start + 10)]
    region = branch.index("If (Oid = eRegionObject) And (PropName = 'Name') Then")
    pad = branch.index("Else If Oid <> ePadObject Then")
    assert region < pad
    assert "Rgn.Name := Value;" in branch[region:pad]
    assert "Refused" not in branch[region:pad], "an empty name clears a region's tag"


def test_a_pad_still_refuses_an_empty_name():
    body = _set_body()
    pad = body.index("Else If (PropName = 'Name') Or (PropName = 'Designator') Then")
    assert "Else If Trim(Value) = '' Then\n                Refused := True" in \
        body[pad:pad + 400].replace("\r\n", "\n")


def test_place_region_names_the_region_before_it_is_added_and_echoes_it():
    body = load(_ALTIUM / "PCB.pas")["PCB_PlaceRegion"]
    assert "NameStr := ExtractJsonValue(Params, 'name');" in body
    named = body.index("If NameStr <> '' Then Region.Name := NameStr;")
    assert named < body.index("Board.AddPCBObject(Region);")
    assert "EscapeJsonString(Region.Name)" in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    asyncio.run(mcp.tools["pcb_place_region"](**kw))
    return bridge.sent


def test_a_name_reaches_the_script(monkeypatch):
    sent = _call(monkeypatch, x1=0, y1=0, x2=100, y2=50, name="ATK-Loft")
    assert sent[0][0] == "pcb.place_region"
    assert sent[0][1]["name"] == "ATK-Loft"


def test_no_name_is_sent_when_none_is_given(monkeypatch):
    sent = _call(monkeypatch, x1=0, y1=0, x2=100, y2=50)
    assert "name" not in sent[0][1]
