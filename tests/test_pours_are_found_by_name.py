# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A pour is written to by its name, not only by a position that moves.

pcb_modify_polygon took only an index, and a pour's index shifts after
every modify and repour, so a second call with the index read earlier
changed another pour, twice, reporting success both times. It now takes the
name, the index, or both. Names repeat on real boards (Altium names every
net-less pour of a layer alike), so a name held by several pours is refused
with the candidates, never resolved to the first; pcb_grow_pour did exactly
that and now shares the same lookup.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import pcb

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


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


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return mcp.tools, bridge


def test_modify_sends_the_name_alone_or_with_the_index(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    modify = tools["pcb_modify_polygon"]
    asyncio.run(modify(name=" GND Top ", net="GND"))
    assert bridge.sent[-1] == ("pcb.modify_polygon",
                               {"name": "GND Top", "net": "GND"})
    asyncio.run(modify(index=4, name="GND Top", hatch_style="Solid"))
    assert bridge.sent[-1] == ("pcb.modify_polygon",
                               {"index": "4", "name": "GND Top",
                                "hatch_style": "Solid"})
    asyncio.run(modify(0, remove_dead=False))
    assert bridge.sent[-1] == ("pcb.modify_polygon",
                               {"index": "0", "remove_dead": "false"})


def test_grow_sends_the_name_alone_or_with_the_index(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    grow = tools["pcb_grow_pour"]
    asyncio.run(grow(name="GND Top", distance_mils=5))
    assert bridge.sent[-1] == ("pcb.grow_pour",
                               {"name": "GND Top", "distance_mils": "5.0"})
    asyncio.run(grow(distance_mils=-2, index=7))
    assert bridge.sent[-1] == ("pcb.grow_pour",
                               {"index": "7", "distance_mils": "-2.0"})


def test_no_pour_named_is_refused_before_altium_is_asked(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    calls = ((tools["pcb_modify_polygon"], {"net": "GND"}),
             (tools["pcb_grow_pour"], {"distance_mils": 1}))
    for tool, rest in calls:
        assert asyncio.run(tool(name="  ", **rest))["ok"] is False
        for bad in (-1, True, 1.5):
            assert asyncio.run(tool(index=bad, **rest))["ok"] is False
    assert bridge.sent == []


def test_a_repeated_name_is_counted_not_resolved_to_the_first():
    find = _routine("PourByName")
    # The walk visits every pour: a Break on the first match would hide the
    # second pour of the name, which is the case being refused.
    assert "Break" not in find
    assert "UpperCase(Poly.Name) = UpperCase(Wanted)" in find
    assert "Inc(Hits);" in find
    assert "If Hits = 1 Then Result := Match;" in find
    # Every candidate is listed with what tells them apart.
    for field in ("'index', At", "'layer'", "'net', Net"):
        assert field in find, field
    assert "AddFilter_ObjectSet(MkSet(ePolyObject))" in find


def test_the_selector_refuses_what_does_not_pick_one_pour():
    choose = _routine("PourFromParams")
    assert "'MISSING_PARAM'" in choose
    assert re.search(r"If Hits = 0 Then\s+Begin\s+Refusal := BuildErrorResponse"
                     r"\(RequestId, 'NOT_FOUND'", choose)
    assert re.search(r"If Hits > 1 Then\s+Begin\s+Refusal := "
                     r"BuildErrorResponseDetailed\(RequestId, 'AMBIGUOUS_NAME'",
                     choose)
    assert "JsonRaw('candidates', '[' + Listing + ']')" in choose
    # With an index, the pour there must still carry the name asked for.
    assert ("(NameStr <> '') And (UpperCase(NameStr) <> UpperCase(Result.Name))"
            in choose)
    changed = choose[choose.index("'POUR_CHANGED'"):]
    assert "Result := Nil;" in changed[:changed.index("Exit;")]


def test_both_writers_use_the_selector_and_walk_no_pours_themselves():
    for handler in ("PCB_ModifyPolygon", "PCB_GrowPour"):
        body = _routine(handler)
        assert "PourFromParams(Board, Params, RequestId," in body, handler
        assert "Result := Refusal;" in body, handler
        assert "BoardIterator_Create" not in body, handler
    assert "JsonInt('index', Position)" in _routine("PCB_GrowPour")
    assert "JsonInt('index', TargetIdx)" in _routine("PCB_ModifyPolygon")
