# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A port's or sheet entry's two net names, in proj_get_hierarchy.

MEASURED 2026-11-08 on AD 25.8.1: a sheet entry with the label
PARENT_NET on its wire, and the matching port in the child sheet with
CHILD_NET on its wire, "Higher Level Names Take Priority" on. The
project-wide net was PARENT_NET. Read through each sheet's LOGICAL
document, DM_NetName and DM_FlattenedNetName both gave the name on that
sheet (CHILD_NET for the port); DM_OwnerNetLogical gave the same and
DM_OwnerNetPhysical gave nothing. Only the PHYSICAL documents carry the
project-wide name, as GetCompiledDocs in Main.pas notes for pins.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code() -> str:
    return (SCRIPTS / "Project.pas").read_text(encoding="utf-8", errors="replace")


def _routine(name: str) -> str:
    code = _code()
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_physical_names_come_from_the_physical_documents():
    body = _routine("HierPhysicalNets")
    assert "Project.DM_PhysicalDocuments(I)" in body
    assert body.index("Project.DM_PhysicalDocuments(I)") < body.index(
        "If Physical = Nil Then Continue;") < body.index("Physical.DM_FileName")
    assert "DM_LogicalDocuments" not in body
    assert "Try Net := Item.DM_FlattenedNetName; Except Inc(Misses); End;" in body
    assert "Physical.DM_PhysicalInstanceName" in body
    # Both ports and the entries of every sheet symbol are mapped.
    assert "Physical.DM_Ports(J)" in body and "Sym.DM_SheetEntries(K)" in body
    assert "Key := 'port';" in body and "Key := 'entry';" in body
    # Every copy of a repeated sheet is kept, not just the first.
    assert "Vals.Strings[At] := Vals.Strings[At] + ',' + OneCopy;" in body


def test_items_are_matched_by_sheet_kind_and_place():
    key = _routine("HierItemKey")
    assert "LowerCase(FileName) + '|' + Kind + '|' + IntToStr(X) + '|' + IntToStr(Y)" in key
    assert "Item.DM_LocationX" in key and "Item.DM_LocationY" in key


def test_each_item_carries_both_names_and_its_place():
    body = _routine("HierItemJson")
    assert "At := Keys.IndexOf(HierItemKey(FileName, Kind, Item));" in body
    assert "JsonRaw('physical', '[' + Copies + ']')" in body
    assert "JsonStr('net', NetName)" in body
    assert "JsonStr('port_name', PortName)" in body
    # The logical item's flattened name is NOT the physical net: not used.
    assert "DM_FlattenedNetName" not in body
    assert "JsonFloat('x', X / 10000)" in body and "JsonFloat('y', Y / 10000)" in body
    assert body.index("Name := PortName;") < body.index("If Name = '' Then Name := NetName;")


def test_the_handler_maps_once_and_frees_the_lists():
    body = _routine("Proj_GetHierarchy")
    assert body.index("SmartCompile(Project);") < body.index("HierPhysicalNets(Project, Keys, Vals, Misses);")
    assert "HierItemJson(Item, FileName, 'port', Keys, Vals, Misses)" in body
    assert "HierItemJson(Item, FileName, 'entry', Keys, Vals, Misses)" in body
    assert body.index("Finally") < body.index("Keys.Free;") < body.rindex("End;")
