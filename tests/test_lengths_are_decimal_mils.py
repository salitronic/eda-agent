# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Lengths go in and come out in decimal mils, in every handler.

Fixed one handler at a time so far (#40, #49, #99, #124), the same bug was
still in 79 others: a length read with StrToIntDef and converted with
MilsToCoord(Integer), so place_arc made a 0.3937 mil width the default 10,
a 48.2842 mil radius 48, and a schematic pin at -1000.5 landed on -1000;
and replies printed IntToStr(CoordToMils(...)), which rounds every length
to a whole mil. Lengths are now read with StrToFloatDef and converted with
MilsToCoordF, and replies print CoordToMilsF. Measured on AD 25.8: an arc,
fill, text and region placed at 1e-4 mil and read back exactly; a pad at
-96.4567 reported as such; a 39.37 mil designator kept its 0.2 mm stroke;
a pin moved to (-1000.5, -500.25) read back unchanged.

These checks cover every unit, so a handler added later with the old
pattern fails here instead of in someone's footprint.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_UNITS = ("PCB.pas", "PCBGeneric.pas", "Library.pas", "Generic.pas")
# Fixed separately in #127 (place_polygon_rect, decimal corners).
_ELSEWHERE = {"PCB_PlacePolygonRect"}


def _functions(unit):
    return load(_DIR / unit, minimum=20)


def _whole_mil_lengths(body):
    """Names of variables read with StrToIntDef that reach MilsToCoord."""
    ints = set(re.findall(r"(\w+)\s*:=\s*StrToIntDef\(", body))
    found = set()
    for conv in re.findall(r"MilsToCoordF?\(([^;]*?)\)", body):
        found |= {v for v in ints if re.search(r"\b%s\b" % re.escape(v), conv)}
    if re.search(r"MilsToCoordF?\(\s*StrToIntDef\(", body):
        found.add("<direct>")
    return found


def test_no_length_is_read_as_a_whole_number_of_mils():
    bad = []
    for unit in _UNITS:
        for name, body in _functions(unit).items():
            if name in _ELSEWHERE:
                continue
            hits = _whole_mil_lengths(body)
            if hits:
                bad.append(f"{unit} {name}: {sorted(hits)}")
    assert bad == [], "\n".join(bad)


def test_no_reply_rounds_a_length_to_a_whole_mil():
    bad = []
    for unit in _UNITS:
        for name, body in _functions(unit).items():
            if name in _ELSEWHERE:
                continue
            if re.search(r"IntToStr\(\s*CoordToMils\(", body) or \
               re.search(r"JsonInt\([^,()]*,\s*CoordToMils\(", body):
                bad.append(f"{unit} {name}")
    assert bad == [], "\n".join(bad)


def test_place_arc_keeps_every_decimal():
    body = _functions("PCB.pas")["PCB_PlaceArc"]
    for v in ("ArcXC", "ArcYC", "ArcRad", "ArcWidth"):
        assert re.search(r"%s := StrToFloatDef\(" % v, body), v
        assert f"MilsToCoordF({v})" in body, v
        assert f"FloatToJsonStr({v})" in body, v


def test_derived_lengths_are_not_cut_by_integer_division():
    pcb = _functions("PCB.pas")
    assert "TextX := (X1 + X2) / 2;" in pcb["PCB_PlaceDimension"]
    assert "Inset := Border / 2;" in pcb["PCB_Panelize"]
    assert "Bumps := Trunc(AddLen / (2 * Amp));" in pcb["PCB_TuneLength"]
    lib = _functions("Library.pas")
    for name in ("Lib_SetDesignator", "Lib_SetDesignators"):
        assert "WidthMils := NewH / 5;" in lib[name], name
        assert "MilsToCoordF(NewH)" in lib[name], name


def test_symbol_polygon_vertices_and_sheet_entries_keep_decimals():
    lib = _functions("Library.pas")["Lib_AddSymbolPolygon"]
    assert "X := StrToFloatDef(Token, 0);" in lib
    assert "XValues.Add(FloatToJsonStr(X));" in lib
    entry = _functions("Generic.pas")["Gen_PlaceSheetEntry"]
    assert "Abs(GotDist - DistFromTop) < 0.0001" in entry
