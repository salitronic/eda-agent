# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A placed part's board-side parameters on Altium 25 and 26, by group.

CreatePrimitiveParametersWithoutVariant is an Altium 26 call. On 25.8.1
it is not in the script engine, and calling it stopped the polling loop
with an error box no Try caught. Altium 25 answers the variant form,
CreatePrimitiveParametersOfVariant(part, '') with no variant named, so
the factory call is chosen by the product version, read BEFORE the call,
in one helper every caller goes through.

The list has groups, chosen by SwitchAppurtenance with an ordinal (the
enum names are not reliably in every engine's table, and an unknown one
fails the whole unit at compile time): 0 the part's own parameters, 1 the
system ones (Component.VaultGUID ...), 2 the footprint's. ``add`` creates
a parameter only, and refuses a name present in any group.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import pcb
from tests.pascal_source import load

_ALT = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _pcb() -> dict[str, str]:
    return load(_ALT / "PCB.pas")


# ---- the version-gated factory --------------------------------------------

def test_the_factory_call_is_chosen_by_version_before_it_is_made():
    body = _pcb()["PcbPartParameters"]
    ver = body.index("Ver := Client.GetProductVersion")
    major = body.index("Major := StrToIntDef(Copy(Ver, 1, Pos('.', Ver) - 1), 0)")
    gate = body.index("If Major >= 26 Then")
    without = body.index("CreatePrimitiveParametersWithoutVariant(Comp)")
    of = body.index("CreatePrimitiveParametersOfVariant(Comp, '')")
    # The version is read and tested before either call is reached.
    assert ver < major < gate < without
    assert gate < of
    # WithoutVariant only on 26 and later; 25 and older take the variant
    # form with an empty variant name.
    branch_26 = body[gate:body.index("Else", gate)]
    assert "CreatePrimitiveParametersWithoutVariant" in branch_26
    assert "CreatePrimitiveParametersOfVariant" not in branch_26
    older = body[body.index("Else", gate):]
    assert "CreatePrimitiveParametersOfVariant(Comp, '')" in older
    assert "CreatePrimitiveParametersWithoutVariant" not in older


def test_no_other_code_calls_the_altium_26_factory_directly():
    for unit in sorted(_ALT.glob("*.pas")):
        for name, body in load(unit, minimum=1).items():
            if name == "PcbPartParameters":
                continue
            assert "CreatePrimitiveParametersWithoutVariant" not in body, (
                f"{unit.name}: {name} calls the Altium 26 factory itself; "
                f"go through PcbPartParameters so Altium 25 is not halted")


def test_the_handler_gets_its_list_from_the_helper():
    body = _pcb()["PCB_ComponentParameters"]
    assert "Prims := PcbPartParameters(Comp);" in body
    assert "GetParametersFactory" not in body


# ---- the groups -----------------------------------------------------------

def test_the_groups_map_to_their_ordinals():
    body = _pcb()["PCB_ComponentParameters"]
    assert "Items := PcbPartParameterItems(Prims, 1, 'system'," in body
    assert "Items := PcbPartParameterItems(Prims, 2, 'footprint'," in body
    # own reads the list as it stands: no switch at all.
    assert "Items := PcbPartParameterItems(Prims, -1, 'own'," in body


def test_all_reads_every_group_and_names_each_row():
    body = _pcb()["PCB_ComponentParameters"]
    block = body[body.index("If Group = 'all' Then"):]
    block = block[:block.index("End;")]
    assert "More := PcbPartParameterItems(Prims, 1, 'system'," in block
    assert "More := PcbPartParameterItems(Prims, 2, 'footprint'," in block
    items = _pcb()["PcbPartParameterItems"]
    assert "JsonStr('group', GroupName)" in items


def test_the_walk_switches_by_ordinal_never_by_enum_name():
    items = _pcb()["PcbPartParameterItems"]
    assert re.search(r"If Ordinal >= 0 Then\s+Begin\s+Try Prims\.SwitchAppurtenance\(Ordinal\);",
                     items)
    for unit in sorted(_ALT.glob("*.pas")):
        text = unit.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"\bpa(User|System|Footprint)\b", text), unit.name


def test_only_list_takes_a_group_and_bad_groups_are_refused():
    body = _pcb()["PCB_ComponentParameters"]
    assert ("If (Group <> 'own') And (Group <> 'system') And (Group <> 'footprint') "
            "And (Group <> 'all') Then") in body
    assert "If (Action <> 'list') And (Group <> 'own') Then" in body


# ---- add ------------------------------------------------------------------

def test_add_refuses_a_name_present_in_any_group():
    body = _pcb()["PCB_ComponentParameters"]
    start = body.index("If Action = 'add' Then")
    block = body[start:body.index("If (Action = 'set') Or (Action = 'add') Then", start)]
    # A list of its own for the scan, so the write goes through one that
    # was never switched.
    assert "Check := PcbPartParameters(Comp);" in block
    assert re.search(r"For G := 0 To 2 Do\s+Begin\s+PcbPartParameterItems\(Check, G,", block)
    assert re.search(r"If Taken Then\s+Begin\s+Result := BuildErrorResponse\(RequestId, 'EXISTS',",
                     block)
    assert "Exit;" in block[block.index("If Taken Then"):]


def test_add_writes_through_the_existing_path_and_reads_back():
    body = _pcb()["PCB_ComponentParameters"]
    write = body[body.index("If (Action = 'set') Or (Action = 'add') Then"):]
    assert "Prims.SetOrNewValueByName(PName, PValue)" in write[:write.index("End;")+200]
    assert ("If ((Action = 'set') Or (Action = 'add')) And (Problem = '') "
            "And ((Not Present) Or (ReadBack <> PValue)) Then") in body


# ---- the MCP tool ---------------------------------------------------------

class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Mcp())
    return captured["pcb_component_parameters"], bridge


@pytest.mark.parametrize("group", ["own", "system", "footprint", "all"])
def test_list_sends_the_group(monkeypatch, group):
    tool, bridge = _tools(monkeypatch)
    asyncio.run(tool("C60", group=group))
    assert bridge.sent == [("pcb.component_parameters", {
        "designator": "C60", "action": "list", "name": "", "value": "", "group": group})]


def test_list_defaults_to_own(monkeypatch):
    tool, bridge = _tools(monkeypatch)
    asyncio.run(tool("C60"))
    assert bridge.sent[0][1]["group"] == "own"


def test_add_is_sent(monkeypatch):
    tool, bridge = _tools(monkeypatch)
    asyncio.run(tool("C60", action="add", name="Note", value="x"))
    assert bridge.sent == [("pcb.component_parameters", {
        "designator": "C60", "action": "add", "name": "Note", "value": "x", "group": "own"})]


@pytest.mark.parametrize("kwargs", [
    {"action": "add"},
    {"action": "add", "value": "x"},
    {"group": "user"},
    {"action": "set", "name": "Note", "value": "x", "group": "system"},
    {"action": "add", "name": "Note", "value": "x", "group": "all"},
    {"action": "remove", "name": "Note", "group": "footprint"},
    {"action": "rename", "name": "Note"},
])
def test_refusals_never_reach_altium(monkeypatch, kwargs):
    tool, bridge = _tools(monkeypatch)
    out = asyncio.run(tool("C60", **kwargs))
    assert out["ok"] is False and bridge.sent == []
