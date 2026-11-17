# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Schematic shapes take their geometry through the generic layer (#384).

obj_create resolved every drawing shape of Altium's menu (arc, elliptical
arc, ellipse, polyline, polygon, bezier, image) and registered it on the
sheet, but the setter had no branch for what makes most of them a shape:
an arc's radius and angles were read-only, an elliptical arc and an
ellipse had neither radius, an image had no corner or file, a polyline no
end shapes. Callers drew curves as straight segments and arrowheads as
filled polygons.

These members live on each shape's own interface, and asked of any other
type they stop the script, so they are rows of the member table
(eda_agent.sch_members, generated into SchMemberGate), which answers first,
and each member is reached through a typed local, as Corner and
CornerXRadius are. Every write is read back.

Found on the way and fixed with it:

* container=component added to the symbol with AddSchObject, the route
  that leaves an object undrawn on the symbol on screen; the library
  writers already go through AddLibSymbolPrim.
* batch_create read each operation's scope and dropped it, so an op naming
  another sheet was placed on the one in front without a word.
* a create never said which property it had not written.

The routines with logic in them are lifted out of the script and run
under Free Pascal; the rest is checked in the source.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.sch_members import TYPES_BY_MEMBER
from tests.pascal_source import load

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_GENERIC = SCRIPTS / "Generic.pas"

#: Each shape member, and the object types that carry it.
OWNERS = {
    "Radius": {"eArc", "eEllipticalArc", "eEllipse"},
    "SecondaryRadius": {"eEllipticalArc", "eEllipse"},
    "StartAngle": {"eArc", "eEllipticalArc"},
    "EndAngle": {"eArc", "eEllipticalArc"},
    "EmbedImage": {"eImage"},
    "KeepAspect": {"eImage"},
    "StartLineShape": {"ePolyline"},
    "EndLineShape": {"ePolyline"},
    "LineShapeSize": {"ePolyline"},
    "Symbol": {"eSymbol"},
    "ScaleFactor": {"eSymbol"},
}

#: Typed local -> the ObjectId branch it may be used in.
LOCALS = {"ArcObj": "eArc", "ElArcObj": "eEllipticalArc", "EllObj": "eEllipse",
          "ImgObj": "eImage", "PolyObj": "ePolyline"}
#: Read only, so the reader alone has them.
READ_ONLY = {"SymObj": "eSymbol"}


def _units():
    return load(_GENERIC, minimum=20)


def _raw(name: str) -> str:
    """One routine with its comments removed but its string literals kept."""
    code = _GENERIC.read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^(Function|Procedure) {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def _branches(body: str) -> dict[str, str]:
    """The text of each `If Obj.ObjectId = eX Then` branch, by type."""
    marks = list(re.finditer(r"If Obj\.ObjectId = (e\w+) Then", body))
    out = {}
    for k, m in enumerate(marks):
        stop = marks[k + 1].start() if k + 1 < len(marks) else len(body)
        out[m.group(1)] = body[m.end():stop]
    return out


# --------------------------------------------------------------- the members

def test_each_shape_member_is_a_row_of_the_member_table():
    for member, types in OWNERS.items():
        assert set(TYPES_BY_MEMBER.get(member, ())) == types, member


def test_every_member_is_reached_through_its_own_typed_local():
    for name, locals_ in (("SchGetShape", {**LOCALS, **READ_ONLY}), ("SchSetShape", LOCALS)):
        branches = _branches(_raw(name))
        assert set(branches) == set(locals_.values()), name
        for local, kind in locals_.items():
            for other, text in branches.items():
                used = re.search(rf"\b{local}\.", text) is not None
                assert used == (other == kind), f"{name}: {local} in {other}"
            assert re.search(rf"\b{local} := Obj;", branches[kind]), f"{name}: {local}"


def test_no_shape_member_is_asked_of_the_base_interface():
    members = "|".join(list(OWNERS) + ["FileName", "Corner"])
    for name in ("GetSchPropertyGated", "SetSchProperty", "SchGetShape", "SchSetShape"):
        found = re.findall(rf"\bObj\.({members})\b", _units()[name])
        assert not found, f"{name}: Obj.{found}"


def test_the_writer_asks_the_gate_before_any_member():
    body = _raw("SchSetShape")
    guard = body.index("If SchMemberGate(Obj, PropName) <> 1 Then Exit;")
    first = min(body.index(local + ".") for local in LOCALS)
    assert guard < first


def test_the_reader_names_every_member_it_reads():
    # No catch-all branch: a member a shape does not carry reads blank and
    # unreadable, never as another member of that shape.
    body = _raw("SchGetShape")
    assert "Else Result :=" not in body
    for kind, text in _branches(body).items():
        assert text.count("Else Have := False;") == 1, kind
        named = set(re.findall(r"PropName = '(\w+)'", text))
        assert named == {m for m, types in OWNERS.items() if kind in types}, kind
    assert "If Not Have Then NotePropertyDiag('unreadable', PropName);" in body


def test_a_value_is_checked_before_the_shape_is_touched():
    body = _raw("SchSetShape")
    first_write = min(m.start() for m in re.finditer(r"\w+Obj\.\w+ := ", body))
    for check in ("If Not IsFloatStr(Value) Then Exit;",
                  "If (Owner <= 2) And ((F <= 0) Or (F > 100000)) Then Exit;",
                  "If (Owner = 3) And ((F < -360) Or (F > 360)) Then Exit;",
                  "Else If LowerCase(Trim(Value)) <> 'false' Then Exit;",
                  "N := SchLineWidthParam(Value);",
                  "N := LineShapeOrdinal(Value);"):
        assert body.index(check) < first_write, check


def test_every_write_is_read_back():
    body = _raw("SchSetShape")
    last_write = max(m.start() for m in re.finditer(r"\w+Obj\.\w+ := ", body))
    read = body.index("Got := SchGetShape(Obj, PropName);")
    assert read > last_write
    tail = body[read:]
    assert "Abs(StrToFloatDef(Got, -1) - F) < 0.001" in tail
    assert "SchAngleSame(StrToFloatDef(Got, 0), F)" in tail
    assert "Result := Got = BoolToJsonStr(B)" in tail
    assert "Result := LineShapeOrdinal(Got) = N" in tail
    assert "Result := Got = IntToStr(N)" in tail


# ------------------------------------------------------ getter and setter

def test_the_getter_and_setter_send_shape_members_to_the_group_first():
    get = _raw("GetSchPropertyGated")
    assert "Else If SchShapeOwner(PropName) > 0 Then Result := SchGetShape(Obj, PropName)" in get
    assert get.index("SchShapeOwner(PropName)") < get.index("'Location.X'")
    # The old arc-only reader is gone: the group answers for an arc.
    assert "ArcObj" not in get
    put = _raw("SetSchProperty")
    # The table answers before the Try, as for every gated member.
    assert put.index("If SchMemberGate(Obj, PropName) = 0 Then") < put.index("SchShapeOwner(PropName)")
    at = put.index("Else If SchShapeOwner(PropName) > 0 Then")
    branch = put[at:put.index("Else If PropName = 'Location.X'", at)]
    assert "WroteOK := SchSetShape(Obj, PropName, Value)" in branch


def test_a_create_refuses_a_shape_member_on_the_wrong_type():
    body = _raw("UnsupportedSchProperty")
    assert "((SchShapeOwner(PropName) > 0) And (SchMemberGate(Obj, PropName) <> 1))" in body


def test_an_image_has_a_corner_and_a_file():
    for name in ("GetSchPropertyGated", "SetSchProperty"):
        body = _raw(name)
        corner = body[body.index("(PropName = 'Corner.X') Or (PropName = 'Corner.Y')"):]
        corner = corner[:corner.index("'CornerXRadius'")]
        assert "Else If Obj.ObjectId = eImage Then" in corner, name
        assert "ImgObj := Obj;" in corner and "ImgObj.Corner" in corner, name
        filename = body[body.index("(PropName = 'Filename') Or (PropName = 'FileName')"):]
        filename = filename[:filename.index("'Comment'")]
        assert "If (Obj.ObjectId = eImage) And (PropName = 'FileName') Then" in filename, name
        assert "ImgObj.FileName" in filename and "Filename'" in filename, name


def test_a_corner_on_a_type_without_one_is_not_reported_written():
    body = _raw("SetSchProperty")
    corner = body[body.index("(PropName = 'Corner.X') Or (PropName = 'Corner.Y')"):]
    corner = corner[:corner.index("'CornerXRadius'")]
    assert re.search(r"End\s+Else\s+Matched := False;\s+End", corner)


def test_shape_numbers_compare_as_numbers_in_a_filter():
    body = _raw("IsSchNumberProperty")
    for prop in ("RADIUS", "SECONDARYRADIUS", "STARTANGLE", "ENDANGLE", "LINESHAPESIZE"):
        assert f"(U = '{prop}')" in body, prop


# ------------------------------------------------------------- the bezier

def test_a_bezier_takes_one_start_and_three_points_a_curve():
    body = _raw("SetSchVertices")
    bez = body[body.index("If Obj.ObjectId = eBezier Then"):]
    bez, other = bez.split("Else", 1)[0], bez.split("Else", 1)[1]
    assert "If ((Xs.Count - 1) Mod 3) <> 0 Then Exit;" in bez
    assert bez.index("Obj.ClearAllVertices;") < bez.index("Obj.InsertVertex(I);")
    assert bez.index("Obj.InsertVertex(I);") < bez.index("Obj.SetState_Vertex(I,")
    assert "VerticesCount" not in bez
    # A polyline or polygon keeps the route it had.
    assert "Obj.VerticesCount := Xs.Count;" in other
    assert "Obj.Vertex[I] :=" in other


def test_a_long_number_is_refused_before_strtointdef():
    # Altium's StrToIntDef raises past 32 bits instead of giving the default
    # (Free Pascal's does not, so the compiled test cannot see it).
    body = _raw("LineShapeOrdinal")
    assert body.index("If Length(N) > 2 Then Exit;") < body.index("StrToIntDef(N, -1)")


def test_a_polyline_outline_takes_two_points_and_a_polygon_three():
    minimum = _raw("SchMinVertices")
    assert "Result := 3;" in minimum
    assert "If Obj.ObjectId = ePolyline Then Result := 2;" in minimum
    body = _raw("SetSchVertices")
    assert "If Xs.Count < SchMinVertices(Obj) Then Exit;" in body
    assert "Xs.Count < 3" not in body


# --------------------------------------------- the library and the scope

def test_a_component_create_goes_through_the_library_like_the_symbol_writers():
    for name in ("Gen_CreateObject", "Gen_BatchCreate"):
        body = _raw(name)
        assert "AddSchObject" not in body, name
        assert "AddLibSymbolPrim(SchLib, Component, NewObj);" in body, name
        own = re.search(r"If Pos\('OwnerPartId=', PropsStr\) = 0 Then\s+"
                        r"SetOwnerPart\(NewObj, Component\);", body)
        assert own, name
        assert own.end() < body.index("AddLibSymbolPrim(SchLib, Component, NewObj);"), name
        assert "MarkLibDirty(SchLib);" in body, name


def test_a_batch_create_refuses_a_scope_other_than_the_document_in_front():
    body = _raw("Gen_BatchCreate")
    refuse = body.index("If LowerCase(Trim(Scope)) <> 'active_doc' Then")
    assert refuse < body.index("SchObjectFactory(ObjTypeInt")
    assert "ItemReason := 'SCOPE_NOT_SUPPORTED';" in body[refuse:refuse + 200]


def test_a_create_says_which_property_it_did_not_write():
    assert "RenderPropertyDiagJson(0)" in _raw("Gen_CreateObject")
    body = _raw("Gen_BatchCreate")
    assert "Diag := RenderPropertyDiagJson(0);" in body
    assert "property_problems" in body
    assert "'\"unknown_count\":0,'" in body and "'\"failed_count\":0,'" in body


# ------------------------------------------------------- run under Free Pascal

def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


#: (routine call, wanted result as Pascal text)
LINE_SHAPES = [
    ("LineShapeOrdinal('None')", "0"), ("LineShapeOrdinal('Arrow')", "1"),
    ("LineShapeOrdinal('Solid Arrow')", "2"), ("LineShapeOrdinal('solid_arrow')", "2"),
    ("LineShapeOrdinal('SOLIDARROW')", "2"), ("LineShapeOrdinal('Tail')", "3"),
    ("LineShapeOrdinal('Solid-Tail')", "4"), ("LineShapeOrdinal(' circle ')", "5"),
    ("LineShapeOrdinal('Square')", "6"), ("LineShapeOrdinal('3')", "3"),
    ("LineShapeOrdinal('7')", "-1"), ("LineShapeOrdinal('-1')", "-1"),
    ("LineShapeOrdinal('')", "-1"), ("LineShapeOrdinal('arrows')", "-1"),
    ("LineShapeOrdinal('99999999999999999999')", "-1"),
    ("LineShapeOrdinal('-99999999999')", "-1"),
]
NAMES = ["None", "Arrow", "Solid Arrow", "Tail", "Solid Tail", "Circle", "Square"]
ANGLES = [((0, 360), True), ((360, 0), True), ((359.9995, 0), True),
          ((-90, 270), True), ((90, 90.01), False), ((45, 225), False),
          ((179.75, 179.75), True), ((0.0005, 360), True), ((1, 359), False)]


def test_line_shapes_angles_and_owners_run_under_free_pascal(tmp_path):
    fpc = _fpc()
    checks, n = [], 0

    def check(cond: str):
        nonlocal n
        n += 1
        checks.append(f"  if not ({cond}) then Halt({n});")
        return n

    for call, want in LINE_SHAPES:
        check(f"{call} = {want}")
    for i, name in enumerate(NAMES):
        check(f"LineShapeName({i}) = '{name}'")
        check(f"LineShapeOrdinal(LineShapeName({i})) = {i}")
    for (a, b), same in ANGLES:
        check(f"{'' if same else 'not '}SchAngleSame({a}, {b})")
    for member in OWNERS:
        check(f"SchShapeOwner('{member}') > 0")
    for member in ("Location.X", "Color", "FileName", "Corner.X", "Vertices", "radius"):
        check(f"SchShapeOwner('{member}') = 0")
    labels = [c for c in checks]

    program = "\n".join([
        "program shapes;", "{$mode delphi}", "uses SysUtils;",
        "const",
        "  eLineShapeNone = 0; eLineShapeArrow = 1; eLineShapeSolidArrow = 2;",
        "  eLineShapeTail = 3; eLineShapeSolidTail = 4; eLineShapeCircle = 5;",
        "  eLineShapeSquare = 6;",
        # DelphiScript passes -1 for the replace flags; Free Pascal wants a set.
        "function StringReplace(const S, A, B: String; Flags: Integer): String;",
        "begin Result := SysUtils.StringReplace(S, A, B, [rfReplaceAll]); end;",
        _routine("Utils.pas", "IsIntStr"),
        _routine("Generic.pas", "SchShapeOwner"),
        _routine("Generic.pas", "LineShapeOrdinal"),
        _routine("Generic.pas", "LineShapeName"),
        _routine("Generic.pas", "SchAngleSame"),
        "begin",
        *checks,
        "end.",
    ])
    path = tmp_path / "shapes.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("shapes.exe" if os.name == "nt" else "shapes")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        pytest.fail(f"failed check {ran.returncode}: {labels[ran.returncode - 1]}")
