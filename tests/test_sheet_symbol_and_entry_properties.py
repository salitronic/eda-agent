# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The rest of a sheet symbol's and a sheet entry's Properties panel.

MEASURED 2026-11-08 on AD 25.8.1, one sheet symbol and one entry on a
scratch sheet, compared with the Properties panel and the saved records:
the panel's Fill Color tick box (IsSolid, saved on the symbol), Source
(SymbolType), an entry's Bus Text Style (TextStyle, saved on every entry),
Kind (ArrowKind), Harness Type, and the colour of the name and file-name
labels had no generic route; an entry's Side and DistanceFromTop read but
did not write. Each is read and written through its own typed interface,
by the names the file stores; a name that is none of them is refused.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code() -> str:
    return (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")


def _routine(name: str) -> str:
    code = _code()
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _branch(body: str, head: str) -> str:
    at = body.index(head)
    end = re.compile(r"\n        End\r?\n").search(body, at).end()
    return body[at:end]


def test_a_sheet_symbol_is_a_filled_shape():
    assert "(Obj.ObjectId = eSheetSymbol)" in _routine("SchObjectHasIsSolid")


def test_symbol_type_reads_and_writes_on_a_sheet_symbol_only():
    get = _branch(_routine("GetSchProperty"), "PropName = 'SymbolType'")
    assert get.index("If Obj.ObjectId = eSheetSymbol Then") < get.index("SymObj := Obj;")
    assert "SheetSymbolTypeToStr(SymObj.SymbolType)" in get
    assert "NotePropertyDiag('unreadable', PropName);" in get
    put = _branch(_routine("SetSchProperty"), "PropName = 'SymbolType'")
    assert "EnumVal := SheetSymbolTypeOrdinal(Value);" in put
    assert "(Obj.ObjectId = eSheetSymbol) And (EnumVal >= 0)" in put
    assert put.index("SymObj := Obj;") < put.index("SymObj.SymbolType := EnumVal;")
    assert "Matched := False;" in put


def test_entry_style_kind_and_harness_read_and_write_on_an_entry_only():
    head = "(PropName = 'TextStyle') Or (PropName = 'ArrowKind')"
    get = _branch(_routine("GetSchProperty"), head)
    assert get.index("If Obj.ObjectId = eSheetEntry Then") < get.index("EntryObj := Obj;")
    for call in ("BusTextStyleToStr(EntryObj.TextStyle)",
                 "ArrowKindToStr(EntryObj.ArrowKind)",
                 "Result := EntryObj.HarnessType;"):
        assert call in get, call
    put = _branch(_routine("SetSchProperty"), head)
    assert "(Obj.ObjectId = eSheetEntry) And (EnumVal >= 0)" in put
    for call in ("EntryObj.TextStyle := EnumVal", "EntryObj.ArrowKind := EnumVal",
                 "EntryObj.HarnessType := Value"):
        assert call in put, call


def test_an_entry_side_or_distance_write_puts_it_on_the_edge():
    put = _branch(_routine("SetSchProperty"), "(PropName = 'Side') Or (PropName = 'DistanceFromTop')")
    assert "(Obj.ObjectId = eSheetEntry) And (EnumVal >= 0)" in put
    side = put.index("EntryObj.Side := EnumVal")
    dist = put.index("EntryObj.DistanceFromTop := MilsToCoordF(")
    owner = put.index("SymObj := EntryObj.OwnerSheetSymbol;")
    place = put.index("PositionSheetEntry(SymObj, EntryObj)")
    assert side < owner < place and dist < owner
    pos = _routine("PositionSheetEntry")
    assert "Entry.Location := Point(" in pos
    for side_name in ("eLeftSide", "eRightSide", "eTopSide", "eBottomSide"):
        assert f"Side = {side_name}" in pos, side_name


def test_no_new_property_is_asked_of_the_base_interface():
    for name in ("GetSchProperty", "SetSchProperty"):
        body = _routine(name)
        for member in ("SymbolType", "TextStyle", "ArrowKind", "HarnessType",
                       "OwnerSheetSymbol"):
            assert not re.search(rf"\bObj\.{member}\b", body), (name, member)


def test_a_label_colour_reads_and_writes_and_refuses_a_bad_value():
    assert "(Field <> 'Color')" in _routine("SchLabelPropWhich")
    assert "If Field = 'Color' Then Result := IntToStr(Lbl.Color);" in _routine("GetSchComponentLabelProp")
    put = _routine("SetSchComponentLabelProp")
    refuse = put.index("If (Field = 'Color') And ((StrToIntDef(Value, -1) < 0)")
    write = put.index("If Field = 'Color' Then Lbl.Color := StrToIntDef(Value, 0);")
    assert refuse < put.index("SchBeginModify(Lbl)") < write < put.index("SchEndModify(Lbl)")


# The enum names both ways, compiled with Free Pascal. The constants are
# Altium's; their values here are stand-ins, only the mapping is checked.
_CONSTS = """
const
  eSheetSymbolType_Normal = 0; eSheetSymbolType_DeviceSheet = 1; eSheetSymbolType_DesignItem = 2;
  eBusTextStyle_Full = 0; eBusTextStyle_Prefix = 1;
  eArrowKind_RectAndTri = 0; eArrowKind_Triangle = 1; eArrowKind_Arrow = 2; eArrowKind_ArrowTail = 3;
  eLeftSide = 0; eRightSide = 1; eTopSide = 2; eBottomSide = 3;
"""

_CASES = [
    ("SheetSymbolTypeOrdinal('Normal')", 0), ("SheetSymbolTypeOrdinal('local')", 0),
    ("SheetSymbolTypeOrdinal('DeviceSheet')", 1), ("SheetSymbolTypeOrdinal('Device')", 1),
    ("SheetSymbolTypeOrdinal('DesignItem')", -1), ("SheetSymbolTypeOrdinal('')", -1),
    ("BusTextStyleOrdinal('Full')", 0), ("BusTextStyleOrdinal('prefix')", 1),
    ("BusTextStyleOrdinal('half')", -1),
    ("ArrowKindOrdinal('Block & Triangle')", 0), ("ArrowKindOrdinal('Triangle')", 1),
    ("ArrowKindOrdinal('Arrow')", 2), ("ArrowKindOrdinal('Arrow Tail')", 3),
    ("ArrowKindOrdinal('dart')", -1),
    ("SheetEntrySideOrdinal('left')", 0), ("SheetEntrySideOrdinal('Bottom')", 3),
    ("SheetEntrySideOrdinal('2')", 2), ("SheetEntrySideOrdinal('9')", -1),
    ("SheetEntrySideOrdinal('up')", -1),
]

_NAMES = [
    ("SheetSymbolTypeToStr(1)", "DeviceSheet"), ("SheetSymbolTypeToStr(2)", "DesignItem"),
    ("BusTextStyleToStr(1)", "Prefix"), ("ArrowKindToStr(0)", "Block & Triangle"),
    ("ArrowKindToStr(3)", "Arrow Tail"),
]


def test_the_enum_names_map_both_ways():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    import tempfile
    checks = []
    n = 0
    for expr, want in _CASES:
        n += 1
        checks.append(f"  if {expr} <> {want} then Halt({n});")
    for expr, want in _NAMES:
        n += 1
        checks.append(f"  if {expr} <> '{want}' then Halt({n});")
    # DelphiScript's StringReplace takes -1 for "all"; Free Pascal a flag set.
    routines = [_routine(r).replace("'', -1)", "'', [rfReplaceAll])") for r in (
        "SheetSymbolTypeToStr", "SheetSymbolTypeOrdinal", "BusTextStyleToStr",
        "BusTextStyleOrdinal", "ArrowKindToStr", "ArrowKindOrdinal", "SheetEntrySideOrdinal")]
    program = "\n".join(["program enums;", "{$mode delphi}", "uses SysUtils;", _CONSTS,
                         *routines, "begin", *checks, "end."])
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "enums.pas"
        src.write_text(program, encoding="utf-8")
        built = subprocess.run([fpc, str(src)], cwd=tmp, capture_output=True, text=True)
        assert built.returncode == 0, built.stdout + built.stderr
        exe = Path(tmp) / ("enums.exe" if os.name == "nt" else "enums")
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        allc = _CASES + _NAMES
        assert ran.returncode == 0, f"case {ran.returncode} failed: {allc[ran.returncode - 1]}"
