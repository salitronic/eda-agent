# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A component's ComponentKind is read and written by the same seven names,
on components only."""
from __future__ import annotations

import re
from pathlib import Path

GENERIC = (Path(__file__).resolve().parents[1] / "scripts" / "altium"
           / "Generic.pas").read_text(encoding="utf-8-sig")


def _routine(name: str) -> str:
    at = GENERIC.index(f"Function {name}(")
    return GENERIC[at:GENERIC.index("\nEnd;", at)]


def test_the_names_read_are_the_names_written():
    read = dict(re.findall(r"Kind = (eComponentKind_\w+) Then Result := '(\w+)'",
                           _routine("ComponentKindName")))
    written = {k: n for n, k in re.findall(
        r"N = '(\w+)' Then Result := (eComponentKind_\w+)", _routine("ComponentKindOf"))}
    assert read == written and len(read) == 7
    assert "standard_no_bom" in read.values() and "jumper" in read.values()


def test_the_property_is_gated_read_and_written_on_components_only():
    gate = _routine("UnsupportedSchProperty")
    assert "(PropName = 'ComponentKind'))\n                    And (Obj.ObjectId <> eSchComponent)" in gate
    getter = _routine("GetSchProperty")
    assert "Result := ComponentKindName(Comp.ComponentKind)" in getter
    setter = _routine("SetSchProperty")
    branch = setter[setter.index("Else If PropName = 'ComponentKind' Then"):]
    assert branch.index("If ComponentKindOf(Value) < 0 Then") < branch.index(
        "Comp.ComponentKind := ComponentKindOf(Value);") < branch.index(
        "If Comp.ComponentKind <> ComponentKindOf(Value) Then WroteOK := False;")
