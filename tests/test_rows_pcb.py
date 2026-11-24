# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""rows kinds of a board: doc=pcb, kind=track and arc (RowsPcb.pas).

The use case: every track of ONE net, changed only where it has a given
width, so a BGA breakout keeps its neck-downs. The read narrows inside
Altium by one route chosen from the scope (the net own group, the
components, a spatial search, or the board walk narrowed to a layer and to
free primitives), and every scope condition is still checked on every
object a route hands out. The write finds every id in the same scope, then
compares every guard, and only then writes, in one edit, with the spatial
index kept in step.

The pure routines are lifted out of RowsPcb.pas (with the core they call)
and run under Free Pascal; the walks need Altium, so their shape is checked
in the source.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNIT = "RowsPcb.pas"


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


def _routines(unit: str) -> list[str]:
    return re.findall(r"(?m)^(?:Function|Procedure) (\w+)", _code(unit))


# --- the unit's shape ---------------------------------------------------------

def test_every_routine_takes_a_parameter_and_comes_before_its_callers():
    code = _code(UNIT)
    names = _routines(UNIT)
    for n in names:
        assert re.search(rf"(?m)^(?:Function|Procedure) {n}\(", code), \
            f"{n} has no parameter (it would show in Run Script)"
    starts = {n: re.search(rf"(?m)^(?:Function|Procedure) {n}\b", code).start()
              for n in names}
    for n in names:
        body = _routine(UNIT, n)
        for callee in names:
            if callee != n and re.search(rf"\b{callee}\(", body):
                assert starts[callee] < starts[n], f"{n} calls {callee} defined below it"


def test_the_unit_adds_no_object_and_never_wanders_to_another_board():
    code = _strip(_code(UNIT))
    for call in ("AddPCBObject", "RemovePCBObject", "DestroyPCBObject",
                 "PCBObjectFactory", "GetPCBBoardAnywhere", "GetCurrentPCBBoard"):
        assert call not in code, f"{UNIT} calls {call}"
    # the board comes from the shared focused-board helper only
    assert code.count("RowsFocusedBoard(") == 2


def test_no_getter_case_reads_as_a_bridge_command():
    # test_bridge_handlers_reachable reads any 'name': Result := Fn( as a command
    assert not re.search(r"'[a-z_0-9]+':\s*Result\s*:=", _code(UNIT))


def test_the_unit_is_stamped():
    assert "Function UnitStamp_RowsPcb(Dummy : Integer) : String;" in _code(UNIT)


# --- the read -----------------------------------------------------------------

def test_the_read_compiles_everything_before_touching_altium():
    body = _routine(UNIT, "RowsPcbRead")
    assert body.index("RowsCompileWhere") < body.index("RowsFocusedBoard")
    assert "Types := RowsFieldTypes(Spec, Fields);" in body
    assert "CF := RowsColumn(Conds, 0);" in body
    assert "RowsPart(Conds" not in body
    assert "'NO_PCB'" in body
    assert "RowsReadReply('pcb', Kind," in body


def test_every_route_narrows_inside_altium():
    walk = _routine(UNIT, "RowsPcbWalk")
    # net: the net own group, filtered to the kind
    net = walk[walk.index("If Route = 'net' Then"):walk.index("Else If Route = 'component'")]
    assert "Group := Net.GroupIterator_Create;" in net
    assert "Group.AddFilter_ObjectSet(MkSet(ObjType));" in net
    assert "If (PouredWant <> 'exclude') And (Not Stop) Then" in net   # pours only when asked
    # component: the component own group
    comp = walk[walk.index("Else If Route = 'component'"):walk.index("Else If Route = 'spatial'")]
    assert "Group := Comp.GroupIterator_Create;" in comp
    assert "FilterWildMatch(Value, PatWant)" in comp
    # spatial: the box
    spat = walk[walk.index("Else If Route = 'spatial'"):]
    assert "Spatial.AddFilter_Area(BL, BB, BR, BT);" in spat
    # board: one layer (V7 set past Mechanical16), free primitives when the scope allows
    assert "If GetLayerString(LayerId) = 'Unknown' Then" in walk
    assert "Iterator.AddFilter_LayerSet(MkSet(LayerId));" in walk
    assert ("If (OwnerWant = 'free') And (PouredWant = 'exclude') Then\n"
            in walk.replace("\r\n", "\n"))
    assert "Iterator.AddFilter_Method(eProcessFree)" in walk
    # every route visits through the one scope check
    assert walk.count("Stop := RowsPcbVisit(") == 5
    visit = _routine(UNIT, "RowsPcbVisit")
    assert visit.index("RowsPcbInScope(") < visit.index("K := Ids.IndexOf(Addr);")
    assert visit.index("RowsPcbInScope(") < visit.index("Inc(Matched);")
    assert "More := True;" in visit


# --- the write ----------------------------------------------------------------

def test_the_writer_guards_every_row_before_it_writes():
    body = _routine(UNIT, "RowsPcbWrite")
    # pcb has no container guard
    assert "RowsTakeRows(Spec, Params, ''," in body
    # the find walk narrows by the same scope as the read
    assert body.index("RowsPcbScope(") < body.index("RowsPcbWalk(Board, Fields, Kind, 'find',")
    # every refusal before the edit opens; the edit closes in a Finally
    assert body.rindex("ROWS_REFUSED") < body.index("PcbBeginEdit(0)")
    assert body.index("PcbBeginEdit(0)") < body.index("PcbEndEdit(0)")
    assert "Finally\n            PcbEndEdit(0);" in body.replace("\r\n", "\n")
    # a failed guard refuses after the guard loop and before the edit
    guards = body.index("Else If Not RowsPcbFieldSame(Spec, Name, Have, Value) Then")
    assert "ROWS_REFUSED" in body[guards:body.index("PcbBeginEdit(0)")]
    # guards compared with the angle-aware compare
    assert "Else If Not RowsPcbFieldSame(Spec, Name, Have, Value) Then" in body


def test_the_writer_brackets_and_reads_back():
    body = _routine(UNIT, "RowsPcbWrite")
    begin = body.index("PCBM_BeginModify")
    end = body.index("PCBM_EndModify")
    assert begin < body.index("RowsPcbSet(Obj, Kind, Key, Value, Units, WriteLayer)") < end
    # tracks and arcs re-index themselves; the six-search check was most
    # of a write's time and never fired on them (measured live)
    assert "BoardIndexState(" not in body and "EnsureIndexed(" not in body
    assert "' reads ' + Have" in body
    assert "MarkDocDirtyByPath(Board.FileName);" in body
    # writes are made on the collected list, never inside a walk
    assert body.count("RowsPcbWalk(") == 1
    assert "Obj := Found.Items[J];" in body


def test_the_setter_writes_only_writable_fields():
    from_spec = {}
    for kind, fn in (("track", "RowsPcbTrackSpec"), ("arc", "RowsPcbArcSpec")):
        spec = re.findall(r"'([^']*)'", _routine(UNIT, fn))
        entries = "".join(spec).split("|")
        from_spec[kind] = {e.split(":")[0] for e in entries if e.endswith(":w")}
    setter = _code(UNIT)[_code(UNIT).index("Function RowsPcbSet("):]
    setter = setter[:setter.index("\nEnd;")]
    arc_part = setter[setter.index("Arc := Obj;"):setter.index("Track := Obj;")]
    track_part = setter[setter.index("Track := Obj;"):]
    assert set(re.findall(r"'(\w+)':", arc_part)) == from_spec["arc"]
    assert set(re.findall(r"'(\w+)':", track_part)) == from_spec["track"]
    assert "net" not in from_spec["track"] | from_spec["arc"]


# --- the pure routines under Free Pascal ---------------------------------------

_LIFTED = [
    ("Utils.pas", "IsFloatStr"), ("Utils.pas", "StrToFloatDef"),
    ("Utils.pas", "BoolToJsonStr"), ("Utils.pas", "FloatToJsonStr"),
    ("Main.pas", "NextBatchOp"), ("Main.pas", "GetBatchField"),
    ("Utils.pas", "FilterOpPos"), ("Utils.pas", "FilterOpText"),
    ("Utils.pas", "FilterWildMatch"), ("Utils.pas", "FilterOpHolds"),
    ("RowsCore.pas", "RowsIntStr"), ("RowsCore.pas", "RowsIntOf"),
    ("RowsCore.pas", "RowsAngleDeg"),
    ("RowsCore.pas", "RowsPointListOk"), ("RowsCore.pas", "RowsTypeWord"), ("RowsCore.pas", "RowsLen"),
    ("RowsCore.pas", "RowsCoord"), ("RowsCore.pas", "RowsAngle"),
    ("RowsCore.pas", "RowsFieldAt"),
    ("RowsCore.pas", "RowsFieldType"), ("RowsCore.pas", "RowsFieldWritable"),
    ("RowsCore.pas", "RowsSpecNames"), ("RowsCore.pas", "RowsSpecNameList"),
    ("RowsCore.pas", "RowsTypeProblem"), ("RowsCore.pas", "RowsValueProblem"), ("RowsCore.pas", "RowsPart"),
    ("RowsCore.pas", "RowsCondHolds"),
    ("RowsCore.pas", "RowsSplit"), ("RowsCore.pas", "RowsPairs"), ("RowsCore.pas", "RowsRowField"),
    ("RowsCore.pas", "RowsRowGuard"),
    ("RowsCore.pas", "RowsFlatAdd"), ("RowsCore.pas", "RowsFlatValue"),
    ("RowsCore.pas", "RowsFlatWrites"), ("RowsCore.pas", "RowsFlatKept"),
    ("RowsCore.pas", "RowsParseFlat"), ("RowsCore.pas", "RowsParseRows"),
    (UNIT, "RowsPcbTrackSpec"), (UNIT, "RowsPcbArcSpec"), (UNIT, "RowsPcbSpec"),
    ("RowsCore.pas", "RowsArcSweep"), (UNIT, "RowsArcEnd"), (UNIT, "RowsAngleSame"),
    (UNIT, "RowsBoxesTouch"), (UNIT, "RowsParseBounds"), (UNIT, "RowsSplitNames"),
    (UNIT, "RowsPcbScopeProblem"), (UNIT, "RowsPcbRoute"), (UNIT, "RowsPcbOwnerKeeps"),
    (UNIT, "RowsPcbFieldSame"),
]

#: Pascal boolean expressions that must hold; numbered by position.
CHECKS = [
    # tables: id, the pcb context, net, then the object
    "Copy(RowsPcbTrackSpec(0), 1, 51) = 'id:s:r|owner:s:r|designator:s:r|pattern:s:r|net:s:r'",
    "Copy(RowsPcbArcSpec(0), 1, 51) = 'id:s:r|owner:s:r|designator:s:r|pattern:s:r|net:s:r'",
    "RowsFieldWritable(RowsPcbTrackSpec(0), 'width')",
    "RowsFieldWritable(RowsPcbTrackSpec(0), 'x2')",
    "not RowsFieldWritable(RowsPcbTrackSpec(0), 'net')",
    "not RowsFieldWritable(RowsPcbTrackSpec(0), 'length')",
    "RowsFieldWritable(RowsPcbArcSpec(0), 'end_angle')",
    "not RowsFieldWritable(RowsPcbArcSpec(0), 'x1')",      # an arc end is worked out
    "RowsFieldType(RowsPcbArcSpec(0), 'poured') = 'b'",
    "RowsPcbSpec('arc') = RowsPcbArcSpec(0)",
    "RowsPcbSpec('track') = RowsPcbTrackSpec(0)",
    # arcs
    "Abs(RowsArcSweep(0, 90) - 90) < 1e-9",
    "Abs(RowsArcSweep(270, 90) - 180) < 1e-9",
    "Abs(RowsArcSweep(90, 0) - 270) < 1e-9",
    "Abs(RowsArcSweep(0, 360) - 360) < 1e-9",
    "Abs(RowsArcSweep(45, 45) - 360) < 1e-9",
    "Abs(RowsArcEnd(100, 10, 90, True) - 110) < 1e-6",
    "Abs(RowsArcEnd(100, 10, 90, False) - 100) < 1e-6",
    "Abs(RowsArcEnd(5, 10, 180, False) + 5) < 1e-6",
    "RowsAngleSame('3600000', '0')",
    "RowsAngleSame('-900000', '2700000')",                    # the same point on the circle
    "RowsAngleSame('900000', '900000')",
    "not RowsAngleSame('900000', '900001')",                  # exact, no tolerance
    "not RowsAngleSame('', '0')",
    "not RowsAngleSame('90.5', '90.5')",                      # integers only
    "RowsPcbFieldSame(RowsPcbArcSpec(0), 'end_angle', '0', '3600000')",
    "not RowsPcbFieldSame(RowsPcbArcSpec(0), 'radius', '0', '3600000')",
    "RowsPcbFieldSame(RowsPcbTrackSpec(0), 'width', '59055', '59055')",
    "not RowsPcbFieldSame(RowsPcbTrackSpec(0), 'width', '59056', '59055')",
    "RowsFieldType(RowsPcbArcSpec(0), 'start_angle') = 'a'",
    "Pos(':n:', RowsPcbArcSpec(0) + RowsPcbTrackSpec(0)) = 0",
    "RowsPcbFieldSame(RowsPcbTrackSpec(0), 'net', 'gnd', 'GND')",
    # boxes
    "RowsBoxesTouch(0, 0, 10, 10, 10, 10, 20, 20)",
    "not RowsBoxesTouch(0, 0, 10, 10, 11, 0, 20, 10)",
    "not RowsBoxesTouch(0, 0, 10, 10, 0, 11, 10, 20)",
    "RowsBoxesTouch(0, 0, 100, 100, 40, 40, 60, 60)",
    "RowsBoxesTouch(10, 0, 20, 10, 0, 0, 10, 10)",            # touching edges count
    "RowsBoxesTouch(0, 10, 10, 20, 0, 0, 10, 10)",
    "BoundsOf('10000,20000,30000,40000', 'coord') = '10000,20000,30000,40000'",
    "BoundsOf('30000,40000,10000,20000', 'coord') = '10000,20000,30000,40000'",   # any order
    "BoundsOf('0,0,0.0254,1', 'coord') = 'PROBLEM'",           # whole numbers only
    "BoundsOf('1,2,3', 'mm') = 'PROBLEM'",
    "BoundsOf('1,2,3,4,5', 'mm') = 'PROBLEM'",
    "BoundsOf('1,a,3,4', 'mm') = 'PROBLEM'",
    # designators
    "NamesOf(' u1, U2 ,u1,,') = 'U1,U2'",
    "NamesOf('') = ''",
    # scope words
    "ScopeOf('', '') = 'all/exclude'",
    "ScopeOf(' FREE ', 'Include') = 'free/include'",
    "ScopeOf('component', 'only') = 'component/only'",
    "ScopeOf('mine', '') = 'PROBLEM'",
    "ScopeOf('all', 'some') = 'PROBLEM'",
    # routes: the narrowest place holding every match
    "RowsPcbRoute('GND', 2, 'BGA*', 'component', True) = 'net'",
    "RowsPcbRoute('', 1, '', 'all', True) = 'component'",
    "RowsPcbRoute('', 0, 'BGA*', 'all', False) = 'component'",
    "RowsPcbRoute('', 0, '', 'component', False) = 'component'",
    "RowsPcbRoute('', 0, '', 'free', True) = 'spatial'",
    "RowsPcbRoute(' ', 0, '', 'all', False) = 'board'",
    # ownership
    "RowsPcbOwnerKeeps('all', 'exclude', False, False)",
    "not RowsPcbOwnerKeeps('all', 'exclude', False, True)",      # pour copper left out
    "RowsPcbOwnerKeeps('all', 'include', False, True)",
    "not RowsPcbOwnerKeeps('all', 'only', False, False)",
    "RowsPcbOwnerKeeps('component', 'exclude', True, False)",
    "not RowsPcbOwnerKeeps('component', 'exclude', False, False)",
    "not RowsPcbOwnerKeeps('free', 'include', True, False)",
    "RowsPcbOwnerKeeps('free', 'include', False, True)",
    # values
    # rows: pcb has no container guard
    "RowsCode(RowsPcbTrackSpec(0), 'id=1`width=1000') = ''",
    "RowsCode(RowsPcbTrackSpec(0), 'id=1`?net=GND`?width=1000`width=800') = ''",
    "RowsCode(RowsPcbTrackSpec(0), 'id=1`net=VCC') = 'READ_ONLY_FIELD'",
    "RowsCode(RowsPcbTrackSpec(0), 'id=1`cx=1') = 'BAD_FIELD'",
    "RowsCode(RowsPcbArcSpec(0), 'id=1`cx=1`end_angle=360') = ''",
    "RowsCode(RowsPcbArcSpec(0), 'id=1`x1=1') = 'READ_ONLY_FIELD'",
    "RowsCode(RowsPcbArcSpec(0), 'id=1`?poured=maybe`width=1') = 'BAD_VALUE'",
]

_ESCAPE = r"""
{ Altium's StringReplace takes its flags as an integer (-1 = all). }
function StringReplace(S, OldP, NewP: String; Flags: Integer): String; overload;
begin
  Result := SysUtils.StringReplace(S, OldP, NewP, [rfReplaceAll]);
end;

function EscapeJsonString(S: String): String;
begin
  Result := StringReplace(StringReplace(S, '\', '\\', [rfReplaceAll]),
    '"', '\"', [rfReplaceAll]);
end;
""".rstrip()

_HELPERS = r"""

function BoundsOf(S, U: String): String;
var L, B, R, T: Integer; P: String;
begin
  P := RowsParseBounds(S, U, L, B, R, T);
  if P <> '' then Result := 'PROBLEM'
  else Result := IntToStr(L) + ',' + IntToStr(B) + ',' + IntToStr(R) + ',' + IntToStr(T);
end;

function NamesOf(S: String): String;
var L: TStringList; I: Integer;
begin
  L := RowsSplitNames(S);
  Result := '';
  for I := 0 to L.Count - 1 do
  begin
    if I > 0 then Result := Result + ',';
    Result := Result + L.Strings[I];
  end;
  L.Free;
end;

function ScopeOf(O, P: String): String;
begin
  if RowsPcbScopeProblem(O, P) <> '' then Result := 'PROBLEM'
  else Result := O + '/' + P;
end;

function RowsCode(Spec, R: String): String;
var Ids, Texts: TStringList; P: String;
begin
  Ids := TStringList.Create; Texts := TStringList.Create;
  P := RowsParseRows(Spec, R, '', Ids, Texts, Result);
  if (P = '') <> (Result = '') then Result := 'INCONSISTENT';
  Ids.Free; Texts.Free;
end;
"""


def test_the_pure_routines_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks = [f"  if not ({c}) then Halt({i});" for i, c in enumerate(CHECKS, 1)]
    program = "\n".join([
        "program rowspcb;", "{$mode delphi}", "uses SysUtils, Classes;",
        _ESCAPE,
        *[_routine(u, n) for u, n in _LIFTED],
        _HELPERS,
        "begin", *checks, "end.",
    ])
    path = tmp_path / "rowspcb.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowspcb.exe" if os.name == "nt" else "rowspcb")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {CHECKS[n - 1] if n <= len(CHECKS) else '?'}")
