# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""rows kinds for schematic drawing: line, polyline and arc, in a SchLib
(schlib/...) and on a sheet (sch/...), in RowsSchGraph.pas.

Lines (most of them polylines) and arcs are the bulk of what symbols and
sheets are drawn with: restyling every outline of a library, or moving a
sheet's drawing, is one read and one write instead of a call per object.

The pure routines (field tables, the point list, the compares, the row
checks against the table) are lifted out with the core helpers they call and
run under Free Pascal. The walks need Altium; their shape is checked in the
source: a sheet is walked first level only (a placed part's drawing and the
sheet template are never touched), symbols are found by walking the library,
every member is asked of its own typed interface only, an id carries the
first point so a moved object is refused, every row is guarded before the
edit opens, and nothing is added or removed.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import rows as rows_tools
from eda_agent.tools.rows_rules import check_rows

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNIT = "RowsSchGraph.pas"
KINDS = [(d, k) for d in ("schlib", "sch") for k in ("line", "polyline", "arc")]


def _code(unit: str) -> str:
    return (ALTIUM / unit).read_text(encoding="utf-8", errors="replace")


def _strip(body: str) -> str:
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def _routine(unit: str, name: str) -> str:
    code = _code(unit)
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return _strip(code[start.start():start.start() + end.end()])


# --- the unit's shape --------------------------------------------------------

def test_every_kind_is_listed_and_routed():
    rows = _code("Rows.pas")
    for doc, kind in KINDS:
        assert f"RowsKindJson('{doc}', '{kind}'," in rows, (doc, kind)
        assert f"'{doc}/{kind}'" in rows, (doc, kind)
    assert "Result := RowsSGRead(Doc, Kind, Params, RequestId);" in rows
    assert "Result := RowsSGWrite(Doc, Kind, Params, RequestId);" in rows
    # a library row names its symbol; a sheet row needs no container guard
    assert "RowsKindJson('schlib', 'polyline', 'symbol', '\"symbol\"'" in rows
    assert "RowsKindJson('sch', 'polyline', '', '\"document_path\"'" in rows


def test_the_unit_is_built_and_stamped():
    for f in ("build.py", "lint.py", "Altium_API.PrjScr"):
        assert "RowsSchGraph.pas" in _code(f), f
    assert "UnitStamp_RowsSchGraph(0)" in _code("StatusForm.pas")
    build = _code("build.py")
    assert build.index("'RowsSchLib.pas'") < build.index("'RowsSchGraph.pas'") \
        < build.index("'Rows.pas'")


def test_a_sheet_is_walked_first_level_and_by_kind():
    """First level only: what belongs to a placed part or to the template
    is never listed nor written."""
    it = _routine(UNIT, "RowsSGIter")
    assert "If OnSheet Then Result.SetState_IterationDepth(eIterateFirstLevel);" in it
    assert "Result.AddFilter_ObjectSet(MkSet(ObjId));" in it
    for name in ("RowsSGRead", "RowsSGWrite"):
        body = _routine(UNIT, name)
        assert "RowsSGIter(Container, ObjId, OnSheet)" in body, name
        assert "SchIterator_Create" not in body, name
        assert "RowsSchLibSymbolAt(SchLib, Addrs.Strings[C])" in body, name
        assert "GetSchComponent" not in body and "LookupLibComponent" not in body


def test_a_library_is_never_taken_for_a_sheet():
    sheet = _routine(UNIT, "RowsSGSheet")
    assert "SchServer.GetSchDocumentByPath(Path)" in sheet
    assert "IsLib := (Doc.ObjectId = eSchLib);" in sheet
    assert "If Not IsLib Then Result := Doc;" in sheet


def test_members_are_asked_of_their_own_interface_only():
    """A member asked of another kind halts the script: every style and
    geometry member is read and written through the kind's own typed
    local, never the base object."""
    for name in ("RowsSGGet", "RowsSGSet"):
        body = _routine(UNIT, name)
        assert "Obj.LineWidth" not in body and "Obj.Color" not in body, name
        assert "Obj.LineStyle" not in body, name
        for kind, local in (("line", "Ln"), ("polyline", "Poly"), ("arc", "Arc")):
            assert f"Kind = '{kind}' Then" in body, (name, kind)
            assert f"{local} := Obj;" in body, (name, kind)
    get = _routine(UNIT, "RowsSGGet")
    assert "Result := IntToStr(Arc.LineWidth)" in get
    assert "Result := LineShapeName(Poly.StartLineShape)" in get


def test_an_id_carries_the_first_point():
    sg_id = _routine(UNIT, "RowsSGId")
    assert "Result := At + '@' + RowsSGAnchor(Obj, Kind);" in sg_id
    anchor = _routine(UNIT, "RowsSGAnchor")
    assert "P := Poly.Vertex[1];" in anchor and "P := Obj.Location;" in anchor


def test_the_writer_guards_every_row_before_it_writes():
    body = _routine(UNIT, "RowsSGWrite")
    pass1 = body.index("If (K >= 0) And (Pass = 1) Then")
    edit = body.index("If OnSheet Then SchBeginEdit(Sheet, '') Else SchBeginEdit(SchLib, '');")
    refused = body.index("'ROWS_REFUSED'")
    assert refused < edit                       # a missing id stops before the edit
    assert body.index("RowsSGSame(FTypes.Strings[F], Name, Have,") > pass1
    assert "SchBeginModify(Obj);" in body and "SchEndModify(Obj);" in body
    assert body.index("SchEndModify(Obj);") < body.index("RowsSGSame(FTypes.Strings[F], Key, Have, Value,")
    assert "MarkDocDirtyByPath(SchDocPath(Sheet))" in body and "MarkLibDirty(SchLib)" in body
    for word in ("RemoveSchObject", "AddSchObject", "RegisterSchObjectInContainer",
                 "SchObjectFactory"):
        assert word not in body


def test_polyline_vertices_go_in_by_the_proven_route():
    body = _routine(UNIT, "RowsSGSet")
    assert "Poly.VerticesCount := Xs.Count;" in body
    assert body.index("Poly.VerticesCount := Xs.Count;") < body.index("Poly.Vertex[I] := Point(")
    assert "Result := Poly.VerticesCount = Xs.Count;" in body


# --- the pure routines under Free Pascal -------------------------------------

_LIFTED = [
    ("Utils.pas", "IsFloatStr"), ("Utils.pas", "StrToFloatDef"), ("Utils.pas", "IsIntStr"),
    ("Utils.pas", "BoolToJsonStr"), ("Utils.pas", "FloatToJsonStr"),
    ("Main.pas", "NextBatchOp"), ("Main.pas", "GetBatchField"),
    ("Utils.pas", "FilterOpPos"), ("Utils.pas", "FilterOpText"),
    ("Utils.pas", "FilterWildMatch"), ("Utils.pas", "FilterOpHolds"),
    ("Generic.pas", "LineShapeOrdinal"),
    ("RowsCore.pas", "RowsIntStr"), ("RowsCore.pas", "RowsIntOr"), ("RowsCore.pas", "RowsIntOf"),
    ("RowsCore.pas", "RowsAngleDeg"),
    ("RowsCore.pas", "RowsPointListOk"), ("RowsCore.pas", "RowsTypeWord"), ("RowsCore.pas", "RowsLen"),
    ("RowsCore.pas", "RowsCoord"), ("RowsCore.pas", "RowsFieldAt"),
    ("RowsCore.pas", "RowsFieldType"), ("RowsCore.pas", "RowsFieldWritable"),
    ("RowsCore.pas", "RowsSpecNames"), ("RowsCore.pas", "RowsSpecNameList"),
    ("RowsCore.pas", "RowsTypeProblem"), ("RowsCore.pas", "RowsValueProblem"), ("RowsCore.pas", "RowsPart"),
    ("RowsCore.pas", "RowsCondHolds"), ("RowsCore.pas", "RowsSplit"), ("RowsCore.pas", "RowsPairs"),
    ("RowsCore.pas", "RowsRowField"),
    ("RowsCore.pas", "RowsRowGuard"), ("RowsCore.pas", "RowsFlatAdd"), ("RowsCore.pas", "RowsFlatValue"),
    ("RowsCore.pas", "RowsFlatWrites"), ("RowsCore.pas", "RowsFlatKept"),
    ("RowsCore.pas", "RowsParseFlat"), ("RowsCore.pas", "RowsParseRows"),
    ("RowsCore.pas", "RowsCell"),
    (UNIT, "RowsSGSpec"), (UNIT, "RowsSGPoints"), (UNIT, "RowsSGSame"),
]

CHECKS = [
    # the tables
    "RowsFieldType(RowsSGSpec('schlib', 'polyline'), 'vertices') = 'v'",
    "RowsFieldType(RowsSGSpec('sch', 'line'), 'x2') = 'l'",
    "RowsFieldType(RowsSGSpec('sch', 'arc'), 'start_angle') = 'a'",
    "RowsFieldType(RowsSGSpec('schlib', 'arc'), 'width') = 'i'",
    "not RowsFieldWritable(RowsSGSpec('schlib', 'line'), 'symbol')",
    "not RowsFieldWritable(RowsSGSpec('sch', 'polyline'), 'points')",
    "RowsFieldWritable(RowsSGSpec('sch', 'polyline'), 'end_shape')",
    "RowsFieldType(RowsSGSpec('sch', 'line'), 'symbol') = ''",
    "RowsFieldType(RowsSGSpec('sch', 'arc'), 'style') = ''",      # arcs have no style
    # the point list
    "RowsPointListOk('0,0;100000,-200000')",
    "RowsPointListOk(' 1,2 ; 3,4 ; 5,6 ')",
    "not RowsPointListOk('0,0')",                 # one point is no line
    "not RowsPointListOk('0,0;1.5,2')",           # integers only
    "not RowsPointListOk('0,0;1')",
    "not RowsPointListOk('')",
    "Points('10,20;30,-40') = '10/20;30/-40'",
    "Points('10,20') = 'NO'",
    "RowsTypeProblem('v', 'vertices', '0,0') <> ''",
    "RowsTypeProblem('v', 'vertices', '0,0;1,1') = ''",
    "RowsCell('v', '0,0;1,1') = '\"0,0;1,1\"'",
    # compares
    "RowsSGSame('s', 'end_shape', 'Solid Arrow', 'solid_arrow', True)",
    "RowsSGSame('s', 'end_shape', 'None', '0', True)",
    "not RowsSGSame('s', 'end_shape', 'Arrow', 'Tail', False)",
    "not RowsSGSame('s', 'end_shape', 'Arrow', 'pointy', False)",
    "RowsSGSame('a', 'end_angle', '0', '3600000', True)",
    "not RowsSGSame('a', 'end_angle', '900000', '0', True)",
    "RowsSGSame('v', 'vertices', '0,0;1,1', '0,0; 1,1', True)",
    "not RowsSGSame('v', 'vertices', '0,0;1,2', '0,0;1,1', True)",
    "not RowsSGSame('v', 'vertices', '', '0,0;1,1', True)",
    "RowsSGSame('i', 'width', '2', '2', True)",
    # rows against the tables: a library row needs ?symbol, a sheet row not
    "RowsCode('schlib', 'line', 'id=1:1@0,0`?symbol=R`width=2') = ''",
    "RowsCode('schlib', 'line', 'id=1:1@0,0`width=2') = 'BAD_ROWS'",
    "RowsCode('sch', 'line', 'id=1@0,0`x2=100000') = ''",
    "RowsCode('sch', 'polyline', 'id=1@0,0`vertices=0,0') = 'BAD_VALUE'",
    "RowsCode('sch', 'polyline', 'id=1@0,0`points=3') = 'READ_ONLY_FIELD'",
    "RowsCode('sch', 'arc', 'id=1@0,0`style=1') = 'BAD_FIELD'",
]

_SHIM = r"""
const
  eLineShapeNone = 0; eLineShapeArrow = 1; eLineShapeSolidArrow = 2;
  eLineShapeTail = 3; eLineShapeSolidTail = 4; eLineShapeCircle = 5;
  eLineShapeSquare = 6;

function EscapeJsonString(S: String): String;
begin
  Result := StringReplace(StringReplace(S, '\', '\\', [rfReplaceAll]),
    '"', '\"', [rfReplaceAll]);
end;

{ Altium's StringReplace takes its flags as an integer (-1 = all). }
function StringReplace(S, OldP, NewP: String; Flags: Integer): String; overload;
begin
  Result := SysUtils.StringReplace(S, OldP, NewP, [rfReplaceAll, rfIgnoreCase]);
end;
""".rstrip()

_HELPERS = r"""
function Points(T: String): String;
var Xs, Ys: TStringList; I: Integer;
begin
  Xs := TStringList.Create; Ys := TStringList.Create;
  if not RowsSGPoints(T, Xs, Ys) then Result := 'NO'
  else
  begin
    Result := '';
    for I := 0 to Xs.Count - 1 do
    begin
      if I > 0 then Result := Result + ';';
      Result := Result + Xs.Strings[I] + '/' + Ys.Strings[I];
    end;
  end;
  Xs.Free; Ys.Free;
end;

function RowsCode(D, K, R: String): String;
var Ids, Texts: TStringList; P, G: String;
begin
  Ids := TStringList.Create; Texts := TStringList.Create;
  if D = 'schlib' then G := 'symbol' else G := '';
  P := RowsParseRows(RowsSGSpec(D, K), R, G, Ids, Texts, Result);
  if (P = '') <> (Result = '') then Result := 'INCONSISTENT';
  Ids.Free; Texts.Free;
end;
"""


def test_the_pure_routines_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    lifted = [_routine(u, n) for u, n in _LIFTED]
    checks = [f"  if not ({c}) then Halt({i});" for i, c in enumerate(CHECKS, 1)]
    program = "\n".join([
        "program rowsschgraph;", "{$mode delphi}", "uses SysUtils, Classes;",
        _SHIM, *lifted, _HELPERS, "begin", *checks, "end.",
    ])
    path = tmp_path / "rowsschgraph.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowsschgraph.exe" if os.name == "nt" else "rowsschgraph")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {CHECKS[n - 1] if n <= len(CHECKS) else '?'}")


# --- the client side -----------------------------------------------------------

def test_a_point_list_converts_both_ways():
    wire = rows_tools.to_wire("vertices", "v", [[0, 0], [1.27, -2.54]], "mm")
    assert wire == "0,0;500000,-1000000"
    assert rows_tools.to_wire("vertices", "v", "0,0;50,-100", "mil") == "0,0;500000,-1000000"
    back = rows_tools.from_wire("v", "0,0;500000,-1000000", "mil")
    assert back == [[0, 0], [50.0, -100.0]]
    assert rows_tools.from_wire("v", "0,0;500000,-1000000", "coord") == [[0, 0], [500000, -1000000]]
    with pytest.raises(ValueError):
        rows_tools.to_wire("vertices", "v", [[0, 0]], "mil")
    with pytest.raises(ValueError):
        rows_tools.to_wire("vertices", "v", "0,0;1", "mil")


_ENTRY = {"container_guard": "", "fields": [
    {"name": "id", "type": "s"},
    {"name": "vertices", "type": "v", "writable": True},
    {"name": "width", "type": "i", "writable": True},
    {"name": "start_shape", "type": "s", "writable": True},
    {"name": "radius", "type": "l", "writable": True},
    {"name": "end_angle", "type": "a", "writable": True}]}


@pytest.mark.parametrize("row,code", [
    ({"id": "1@0,0", "width": "2", "start_shape": "Solid Arrow"}, ""),
    ({"id": "1@0,0", "width": "4"}, "BAD_VALUE"),
    ({"id": "1@0,0", "start_shape": "pointy"}, "BAD_VALUE"),
    ({"id": "1@0,0", "vertices": "0,0"}, "BAD_VALUE"),
    ({"id": "1@0,0", "vertices": "0,0;10,10"}, ""),
    ({"id": "1@0,0", "radius": "0"}, "BAD_VALUE"),
    ({"id": "1@0,0", "end_angle": "3600001"}, "BAD_VALUE"),
])
def test_sheet_rows_are_checked_before_sending(row, code):
    got, _ = check_rows("sch", "polyline", [row], _ENTRY)
    assert got == code


def test_document_path_is_sent_for_a_sheet(monkeypatch):
    sent = []

    class _B:
        async def send_command_async(self, command, params, *a, **k):
            if command == "rows.list_kinds":
                return {"kinds": [{"doc": "sch", "kind": "polyline", **_ENTRY}]}
            sent.append(params)
            return {"fields": ["id", "vertices"], "rows": [["1@0,0", "0,0;100000,0"]]}

    class _M:
        tools = {}

        def tool(self, *a, **k):
            def deco(fn):
                _M.tools[fn.__name__] = fn
                return fn
            return deco

    bridge = _B()           # one instance: the kind cache is keyed by it
    monkeypatch.setattr(rows_tools, "get_bridge", lambda: bridge)
    monkeypatch.setattr(rows_tools, "_KINDS", {})
    rows_tools.register_rows_tools(_M())
    out = asyncio.run(_M.tools["rows_read"](doc="sch", kind="polyline",
                                            document_path=r"C:\x\a.SchDoc"))
    assert sent[0]["document_path"] == r"C:\x\a.SchDoc"
    assert out["rows"] == [["1@0,0", [[0, 0], [10.0, 0]]]]       # mil by default
    asyncio.run(_M.tools["rows_write"](doc="sch", kind="polyline", rows=[
        {"id": "1@0,0", "vertices": [[0, 0], [20, 0]]}], document_path=r"C:\x\a.SchDoc"))
    assert sent[1]["document_path"] == r"C:\x\a.SchDoc"
