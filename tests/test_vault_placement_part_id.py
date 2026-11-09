# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A managed placement chooses the part of a multi-part component.

part_id was refused as an unknown key, so a 16-part module went down as
whichever part the loader picked (the last one) and each copy needed a
second call to change it. It is now taken as the library placement
takes it: written before the part reaches the sheet, written again on
the sheet, and read back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic
from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _fns():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))


def test_the_part_is_written_before_the_add_and_read_back():
    body = _fns()["Gen_PlaceSchVaultComponents"]
    pre = body.index("Comp.CurrentPartID := PartId;")
    add = body.index("PutLoadedComponent(SchDoc, Comp,")
    again = body.index("SetSchPartId(Comp, PartId);")
    readback = body.index("PartAfter := Comp.CurrentPartID;")
    assert pre < add < again < readback
    assert "'PART_REFUSED'" in body
    assert "',\"part_id\":' + IntToStr(PartAfter)" in body


def test_the_part_id_setter_reports_the_one_it_changed():
    body = _fns()["Gen_SetComponentPartId"]
    assert '"matched":' not in body
    assert '"changed":1,"same_designator":' in body


def test_part_id_reaches_the_bridge(monkeypatch):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"placed": 1}

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(generic, "get_bridge", lambda: Bridge())
    monkeypatch.setattr(generic, "_refuse_manual_sheet_layout", nothing)
    monkeypatch.setattr(generic, "_focus_sheet", nothing)
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(Mcp())
    place = tools["sch_place_vault_components"]
    out = asyncio.run(place(placements=[
        {"design_item_id": "SOM-1", "x": 100, "y": 200, "designator": "U1",
         "part_id": 3}]))
    assert out == {"placed": 1}
    assert "part_id=3" in sent[-1][1]["placements"]
    out = asyncio.run(place(placements=[
        {"design_item_id": "SOM-1", "part_id": "three"}]))
    assert out["error"] == "BAD_PART_ID"
