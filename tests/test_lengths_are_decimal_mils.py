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

WHAT THE FIRST VERSION OF THIS GUARD MISSED, and why (follow-up to #130).
It looked at four units only, and only for a StrToIntDef variable that
appeared INSIDE MilsToCoord(...) or a CoordToMils(...) printed in the same
expression. place_via_array read x1 with StrToIntDef, copied it into a loop
variable (Ix := X1) and converted that; snap_to_grid, align_components,
stub_pins and the pin dumps read CoordToMils into a variable and printed
the variable; delete_object and measure_distance never converted at all.
Our StrToIntDef (Utils.pas) returns its DEFAULT for any non-integer
string, so "100.5" did not even round: it became 0. The checks below
follow a value through plain copies and sums, cover every unit, and also
forbid the two whole-mil helpers outright, since every remaining use of
either was a length losing its fraction.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "altium"
# Every unit but SelfTest.pas, which exercises the helpers on purpose.
_UNITS = {"PCB.pas": 20, "PCBGeneric.pas": 20, "Library.pas": 20,
          "Generic.pas": 20, "Audit.pas": 20, "Project.pas": 20,
          "Application.pas": 20, "Utils.pas": 20, "Main.pas": 20,
          "StatusForm.pas": 20, "Dispatcher.pas": 2}
# The helpers themselves.
_DEFINITIONS = {"MilsToCoord", "CoordToMils"}
# Not a length that is read or reported: Proj_Annotate sorts components by
# position, and a whole mil there groups parts that sit on one row.
_SORT_KEY_ONLY = {("Project.pas", "Proj_Annotate")}

# Parameters that are numbers but not lengths, read with StrToIntDef on
# purpose: (unit, function, key).
_NOT_LENGTHS = {
    ("Project.pas", "Proj_ExportImage", "width"),    # pixels
    ("Project.pas", "Proj_ExportImage", "height"),   # pixels
    ("Generic.pas", "Gen_GetFontId", "size"),        # font points
    ("Library.pas", "Lib_AddSymbolText", "font_size"),
    ("Library.pas", "Lib_AddSymbolLine", "width"),   # TSize enum, 0..3
    ("Library.pas", "Lib_AddSymbolArc", "width"),
    ("Library.pas", "Lib_AddSymbolLines", "width"),
    ("Generic.pas", "Gen_PlaceRectangle", "line_width"),  # 1..4 -> eSmall..
    ("Generic.pas", "Gen_PlaceLine", "line_width"),
    ("Library.pas", "Lib_AddFootprintPad", "corner_radius"),   # percent
    ("Library.pas", "Lib_AddFootprintPads", "corner_radius"),
    # x / y here are internal coordinates (TCoord) relative to the
    # footprint origin, already whole numbers.
    ("Library.pas", "Lib_SetDesignator", "x"),
    ("Library.pas", "Lib_SetDesignator", "y"),
}
_LENGTH_KEY = re.compile(
    r"^(?:[xy]\d?|c[xy]|center_[xy]|location_[xy]|offset_[xyz]|pitch|radius"
    r"|size|width|height|length|gap|spacing|clearance|margin|grid_size"
    r"|.*_mils|.*_width|.*_height|.*_size|.*_length|.*_radius)$")


def _units():
    for unit, minimum in _UNITS.items():
        for name, body in load(_DIR / unit, minimum=minimum).items():
            if name not in _DEFINITIONS:
                yield unit, name, body


def _mentions(var, text):
    return re.search(r"(?<![.\w])%s\b" % re.escape(var), text) is not None


def _top_level_terms(expr):
    """The terms of a sum, split on + and - outside parentheses."""
    terms, depth, cur = [], 0, ""
    for ch in expr:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and ch in "+-":
            terms.append(cur.strip())
            cur = ""
        else:
            cur += ch
    terms.append(cur.strip())
    return terms


def _whole_mil_lengths(body):
    """Names of variables read with StrToIntDef that reach MilsToCoord.

    The value is followed through plain copies and sums (Ix := X1;
    EX := PX + StubLen), which is how place_via_array hid it, but not
    through a product: a row count times a board width is not a length
    read as an integer.

    A record field such as Seg.cx is not one: a board-shape vertex arrives
    as an integer internal coordinate, which is already exact."""
    ints = set(re.findall(r"(?<![.\w])(\w+)\s*:=\s*StrToIntDef\(", body))
    source = {v: v for v in ints}
    assigns = re.findall(r"(?<![.\w])(\w+)\s*:=\s*([^;]*);", body)
    grew = True
    while grew:
        grew = False
        for lhs, rhs in assigns:
            if lhs in source:
                continue
            for term in _top_level_terms(rhs):
                if term in source:
                    source[lhs] = source[term]
                    grew = True
                    break
    found = set()
    for conv in re.findall(r"MilsToCoordF?\(([^;]*?)\)", body):
        found |= {f"{v} (from {source[v]})" if v != source[v] else v
                  for v in source if _mentions(v, conv)}
    if re.search(r"MilsToCoordF?\(\s*StrToIntDef\(", body):
        found.add("<direct>")
    return found


def test_no_length_is_read_as_a_whole_number_of_mils():
    bad = []
    for unit, name, body in _units():
        hits = _whole_mil_lengths(body)
        if hits:
            bad.append(f"{unit} {name}: {sorted(hits)}")
    assert bad == [], "\n".join(bad)


def test_no_length_parameter_is_read_with_strtointdef():
    """A length parameter read with StrToIntDef loses more than its
    fraction: our StrToIntDef returns the default for "100.5", so the
    caller's value is replaced, not rounded. Caught whether the value is
    converted or only used in arithmetic (delete_object, measure_distance,
    the angular dimension's x1..y2), and through a string read first."""
    bad = []
    for unit, name, body in _units():
        keys = re.findall(
            r"StrToIntDef\(\s*(?:ExtractJsonValue\(\s*Params\s*,|"
            r"GetBatchField\(\s*\w+\s*,)\s*'(\w+)'", body)
        strs = dict((v, k) for v, k in re.findall(
            r"(?<![.\w])(\w+)\s*:=\s*(?:ExtractJsonValue\(\s*Params\s*,|"
            r"GetBatchField\(\s*\w+\s*,)\s*'(\w+)'\s*\)", body))
        keys += [strs[v] for v in re.findall(r"StrToIntDef\(\s*(\w+)\s*,", body)
                 if v in strs]
        for key in keys:
            if _LENGTH_KEY.match(key) and (unit, name, key) not in _NOT_LENGTHS:
                bad.append(f"{unit} {name}: '{key}'")
    assert bad == [], "\n".join(bad)


def test_no_reply_rounds_a_length_to_a_whole_mil():
    bad = []
    for unit, name, body in _units():
        if (unit, name) in _SORT_KEY_ONLY:
            continue
        if re.search(r"IntToStr\(\s*CoordToMils\(", body) or \
           re.search(r"JsonInt\([^,()]*,\s*CoordToMils\(", body):
            bad.append(f"{unit} {name}")
    assert bad == [], "\n".join(bad)


def test_coord_to_mils_is_used_nowhere():
    """CoordToMils rounds to a whole mil. Every use left after #130 put
    that rounded value somewhere a length belonged: into a variable that
    was printed (pin dumps, sheet entries, snap_to_grid's old_x), into a
    position computed from it (align_components moved every part onto a
    whole mil), or into a check (find_off_grid_components passed a part
    0.4 mil off the grid). CoordToMilsF is the conversion."""
    bad = [f"{unit} {name}" for unit, name, body in _units()
           if (unit, name) not in _SORT_KEY_ONLY
           and re.search(r"(?<!\w)CoordToMils\(", body)]
    assert bad == [], "\n".join(bad)


def test_mils_to_coord_takes_only_whole_constants():
    """MilsToCoord(Integer) is kept for literal tolerances such as
    MilsToCoord(2). Anything computed goes through MilsToCoordF, so no
    upstream Round or StrToIntDef can hide behind it."""
    bad = []
    for unit, name, body in _units():
        for arg in re.findall(r"(?<!\w)MilsToCoord\(([^()]*(?:\([^()]*\)[^()]*)*)\)", body):
            if not re.fullmatch(r"\s*-?\d+\s*", arg):
                bad.append(f"{unit} {name}: MilsToCoord({arg})")
        # The same loss, written the other way round (Lib_Link3DModel,
        # the audits' tolerance and radius).
        for hit in re.findall(r"MilsToCoordF?\(\s*(?:Round|Trunc)\(\w+\)\s*\)", body):
            bad.append(f"{unit} {name}: {hit}")
    assert bad == [], "\n".join(bad)


def test_place_arc_keeps_every_decimal():
    body = load(_DIR / "PCB.pas", minimum=20)["PCB_PlaceArc"]
    for v in ("ArcXC", "ArcYC", "ArcRad", "ArcWidth"):
        assert re.search(r"%s := StrToFloatDef\(" % v, body), v
        assert f"MilsToCoordF({v})" in body, v
        assert f"FloatToJsonStr({v})" in body, v


def test_via_array_positions_do_not_drift_with_a_decimal_pitch():
    body = load(_DIR / "PCB.pas", minimum=20)["PCB_PlaceViaArray"]
    for v in ("X1", "Y1", "X2", "Y2", "Pitch"):
        assert re.search(r"%s := StrToFloatDef\(" % v, body), v
        assert f"FloatToJsonStr({v})" in body, v
    # X1 + n * Pitch, not a running sum of a decimal pitch.
    assert "Ix := X1 + Nx * Pitch;" in body
    assert "Iy := Y1 + Ny * Pitch;" in body
    assert "Via.x := MilsToCoordF(Ix);" in body


def test_a_decimal_location_still_picks_the_symbol():
    """The candidates list reports locations in decimal mils since #130;
    SchComponentAt accepted only an integer string, so a decimal location
    was ignored and the first symbol with the designator was written."""
    body = load(_DIR / "Generic.pas", minimum=20)["SchComponentAt"]
    assert "IsFloatStr(LocX) And IsFloatStr(LocY)" in body
    assert "Abs(CoordToMilsF(CompLoc.X) - WantX) < 0.0001" in body
    assert "IsIntStr" not in body


def test_off_grid_is_measured_in_decimal_mils():
    body = load(_DIR / "Audit.pas", minimum=20)["Audit_FindOffGridComponents"]
    assert "GridMils := StrToFloatDef(GridStr, 100);" in body
    assert "Dx := Abs(XMils - Trunc(XMils / GridMils) * GridMils);" in body
    assert " Mod " not in body


def test_derived_lengths_are_not_cut_by_integer_division():
    pcb = load(_DIR / "PCB.pas", minimum=20)
    assert "TextX := (X1 + X2) / 2;" in pcb["PCB_PlaceDimension"]
    assert "Inset := Border / 2;" in pcb["PCB_Panelize"]
    assert "Bumps := Trunc(AddLen / (2 * Amp));" in pcb["PCB_TuneLength"]
    assert "CenterX := (MinX + MaxX) / 2;" in pcb["PCB_AlignComponents"]
    paste = pcb["PCB_MakePasteGrid"]
    assert "GridXPad := (PadWMils - (GridXCnt * GridSize)) / (GridXCnt + 1);" in paste
    lib = load(_DIR / "Library.pas", minimum=20)
    for name in ("Lib_SetDesignator", "Lib_SetDesignators"):
        assert "WidthMils := NewH / 5;" in lib[name], name
        assert "MilsToCoordF(NewH)" in lib[name], name


def test_symbol_polygon_vertices_and_sheet_entries_keep_decimals():
    lib = load(_DIR / "Library.pas", minimum=20)["Lib_AddSymbolPolygon"]
    assert "X := StrToFloatDef(Token, 0);" in lib
    assert "XValues.Add(FloatToJsonStr(X));" in lib
    entry = load(_DIR / "Generic.pas", minimum=20)["Gen_PlaceSheetEntry"]
    assert "Abs(GotDist - DistFromTop) < 0.0001" in entry
    assert "JsonFloat('x', GotX)" in entry
