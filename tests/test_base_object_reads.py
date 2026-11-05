# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read-only properties every board or schematic object has.

On the board: DetailString, DRCError, UniqueID and IsElectricalPrim, read
off IPCB_Primitive itself, so no cast stands between any object and them.
The query gate and the advertised list name them too, or a query would be
told they do not exist. On the schematic: DescriptionString,
IdentifierString and ErrorString, off ISch_GraphicalObject.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
BOARD = ("DetailString", "DRCError", "UniqueID", "IsElectricalPrim")
SHEET = ("DescriptionString", "IdentifierString", "ErrorString")


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_board_reads_are_read_gated_and_listed():
    reader = _routine("PCBGeneric.pas", "GetPCBProperty")
    gate = _routine("PCBGeneric.pas", "IsKnownPCBProperty")
    listed = _routine("PCBGeneric.pas", "KnownPCBPropertyList")
    for name in BOARD:
        assert re.search(rf"PropName = '{name}' Then Result := ", reader), name
        assert f"(PropName = '{name}')" in gate, name
        assert f"{name}," in listed or f"{name} " in listed, name


def test_the_board_reads_are_never_written():
    setter = _routine("PCBGeneric.pas", "SetPCBProperty")
    for name in BOARD:
        assert f"'{name}'" not in setter, name


def test_the_sheet_reads_come_off_the_base_object():
    reader = _routine("Generic.pas", "GetSchProperty")
    assert "Obj.GetState_DescriptionString" in reader
    assert "Obj.GetState_IdentifierString" in reader
    assert "Result := Obj.ErrorString" in reader
