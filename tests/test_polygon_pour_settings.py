# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A polygon's pour settings are read and written through the generic layer.

generic.query_objects / modify_objects reached only RemoveDead,
RemoveNarrowNecks and RemoveIslandsByArea on a polygon, so its grid, track
width, thresholds, hatch style, pour-over mode and name could not even be
read, let alone copied to another polygon (issue #125). These checks hold
the rules the new properties rest on: polygons only, lengths as decimal
mils, the island area as square mils, the ordinals bounded, bad values
refused before anything is written, the read-only two never written, and
every name advertised to a caller.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"

_LENGTHS = ("Grid", "TrackSize", "MinTrack", "ArcApproximation", "NeckWidthThreshold")
_WRITABLE = _LENGTHS + ("IslandAreaThreshold", "PolyHatchStyle", "PourOver", "UseOctagons")
_READ_ONLY = ("PourIndex", "Poured")
_ALL = _WRITABLE + _READ_ONLY


def _fns():
    return load(_GENERIC, minimum=20)


def _branch(body: str, prop: str) -> str:
    """The getter's or setter's Else If branch whose condition names prop."""
    starts = [m.start() for m in re.finditer(r"Else If \(?PropName = '", body)]
    for a, b in zip(starts, starts[1:] + [len(body)]):
        head = body[a:body.index("Then", a)]
        if f"'{prop}'" in head:
            return body[a:b]
    raise AssertionError(f"no branch names {prop}")


def test_every_setting_is_read_from_a_polygon_only():
    fns = _fns()
    getter = fns["GetPCBProperty"]
    for prop in _ALL:
        branch = _branch(getter, prop)
        assert "If Oid = ePolyObject Then Result := PCBGetPoly(Obj, PropName);" in branch, prop
    reader = fns["PCBGetPoly"]
    # The type test sits in the getter: an ePolyObject test in a helper
    # above GetPCBProperty would read as a position resolver.
    assert "ePolyObject" not in reader
    for prop in _ALL:
        assert re.search(rf"PropName = '{prop}' Then\s*(?:Begin\s*)?.*Poly\.{prop}",
                         reader, re.S), prop


def test_lengths_read_and_write_as_decimal_mils():
    fns = _fns()
    reader, writer = fns["PCBGetPoly"], fns["PCBSetPoly"]
    for prop in _LENGTHS:
        assert f"FloatToJsonStr(CoordToMilsF(Poly.{prop}))" in reader, prop
        assert f"Poly.{prop} := MilsToCoordF(F)" in writer, prop
    # A length goes through the shared check, so a non-number is refused
    # before any branch runs, and a filter compares it as a number.
    lengths = re.findall(r"PropName\s*=\s*'(\w+)'", fns["IsLengthProperty"])
    assert set(_LENGTHS) <= set(lengths)


def test_the_island_area_is_square_mils():
    # The API holds coordinates squared: 10000 coordinates to the mil, so
    # 1e8 to the square mil, and the dialog's default reads back as 2500.
    fns = _fns()
    reader, writer = fns["PCBGetPoly"], fns["PCBSetPoly"]
    assert "Area := Poly.IslandAreaThreshold;" in reader
    assert "FloatToJsonStr(Area / 100000000.0)" in reader
    assert "Poly.IslandAreaThreshold := F * 100000000.0" in writer
    assert "IslandAreaThreshold" not in fns["IsLengthProperty"]


def test_every_writable_setting_is_written_on_a_polygon_only():
    setter = _fns()["SetPCBProperty"]
    for prop in _WRITABLE:
        branch = _branch(setter, prop)
        assert "If Oid <> ePolyObject Then Matched := False" in branch, prop
        assert "Else If Not PCBSetPoly(Obj, PropName, Value) Then Refused := True" in branch, prop


def test_a_negative_or_non_number_is_refused_before_anything_is_written():
    writer = _fns()["PCBSetPoly"]
    check = writer.index("If Not IsFloatStr(Value) Then Exit;")
    negative = writer.index("If F < 0 Then Exit;")
    first_write = writer.index("Poly.Grid :=")
    assert check < negative < first_write
    assert writer.index("Result := False;") < check
    assert writer.rindex("Result := True;") > first_write


def test_the_ordinals_are_whole_and_in_range():
    writer = _fns()["PCBSetPoly"]
    first_write = writer.index("Poly.Grid :=")
    guard = writer[:first_write]
    assert "If Round(F) <> F Then Exit;" in guard
    assert "(PropName = 'PolyHatchStyle') And (Round(F) > 5) Then Exit;" in guard
    assert "(PropName = 'PourOver') And (Round(F) > 2) Then Exit;" in guard
    assert "Poly.PolyHatchStyle := Round(F)" in writer
    assert "Poly.PourOver := Round(F)" in writer
    reader = _fns()["PCBGetPoly"]
    assert "IntToStr(Poly.PolyHatchStyle)" in reader
    assert "IntToStr(Poly.PourOver)" in reader


def test_use_octagons_takes_true_or_false_and_nothing_else():
    writer = _fns()["PCBSetPoly"]
    start = writer.index("If PropName = 'UseOctagons' Then")
    arm = writer[start:writer.index("If Not IsFloatStr(Value)")]
    assert "If LowerCase(Value) = 'true' Then Poly.UseOctagons := True" in arm
    assert "Else If LowerCase(Value) = 'false' Then Poly.UseOctagons := False" in arm
    assert re.search(r"Else Exit;\s*Result := True;", arm)
    assert "StrToBool" not in writer


def test_pour_index_and_poured_are_read_and_never_written():
    fns = _fns()
    for prop in _READ_ONLY:
        assert f"'{prop}'" not in fns["SetPCBProperty"], prop
        assert not re.search(rf"\.{prop}\s*:=", fns["PCBSetPoly"]), prop
        assert f"Poly.{prop}" in fns["PCBGetPoly"], prop


def test_a_polygon_name_is_read_and_written_and_an_empty_one_refused():
    fns = _fns()
    getter = _branch(fns["GetPCBProperty"], "Name")
    assert "If Oid = ePolyObject Then Begin Poly := Obj; Result := Poly.Name; End;" in getter
    setter = fns["SetPCBProperty"]
    start = setter.index("Else If (PropName = 'Name') And (Oid = ePolyObject) Then")
    branch = setter[start:setter.index("Else If (PropName = 'Name') Or", start)]
    assert "If Trim(Value) = '' Then Refused := True" in branch
    assert "Else If Not PCBSetPoly(Obj, PropName, Value) Then Matched := False;" in branch
    writer = fns["PCBSetPoly"]
    arm = writer[writer.index("If PropName = 'Name' Then"):writer.index("If PropName = 'UseOctagons'")]
    assert re.search(r"If Trim\(Value\) = '' Then Exit;\s*Poly\.Name := Value;\s*Result := True;", arm)


def test_every_setting_is_a_known_and_advertised_property():
    fns = _fns()
    allowed = set(re.findall(r"PropName\s*=\s*'(\w+)'", fns["IsKnownPCBProperty"]))
    listed = set(re.findall(r"[A-Za-z]+", fns["KnownPCBPropertyList"].split("Begin", 1)[1]))
    for prop in _ALL:
        assert prop in allowed, prop
        assert prop in listed, prop
