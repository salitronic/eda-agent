# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""rows.read_rows / rows.write_rows: narrow reads, guarded writes by id.

A tool that edits many objects either made one bridge call per object (a
10 ms+ crossing each, plus a walk) or read the whole design to pick a few.
The rows commands let Altium narrow the walk to one object kind, a scope and
a where, return only the fields asked for, and take back one row per object
keyed by the id the read gave, each guarded by any of its fields. The
contract is docs/ROWS_CONTRACT.md.

The shared core's pure routines are lifted out of RowsCore.pas (with the
real helpers they call) and run under Free Pascal. The walks need Altium;
their shape is checked in the source: the writer finds and guards every row
before it writes, writes inside one edit, and never adds or removes an
object.
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

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(unit: str) -> str:
    return (ALTIUM / unit).read_text(encoding="utf-8", errors="replace")


def _routine(unit: str, name: str) -> str:
    code = _code(unit)
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


# --- routing --------------------------------------------------------------

def test_the_rows_category_is_routed():
    assert ("'rows':        Result := HandleRowsCommand(Action, Params, RequestId);"
            in _code("StatusForm.pas"))
    handler = _routine("Rows.pas", "HandleRowsCommand")
    assert "'pcblib/track': Result := RowsPcbLibTrackRead(Params, RequestId);" in handler
    assert "'pcblib/track': Result := RowsPcbLibTrackWrite(Params, RequestId);" in handler
    assert "If Action = 'read_rows' Then" in handler
    assert "Else If Action = 'write_rows' Then" in handler


def test_the_units_are_built_and_stamped_in_order():
    build = _code("build.py")
    order = [build.index(f"'{u}'") for u in
             ("Generic.pas", "RowsCore.pas", "RowsPcbLib.pas", "Rows.pas", "StatusForm.pas")]
    assert order == sorted(order)
    stamps = _routine("StatusForm.pas", "UnitStampsResponse")
    for unit in ("RowsCore", "RowsPcbLib", "Rows"):
        assert f"UnitStamp_{unit}(0)" in stamps


# --- the writer's shape -----------------------------------------------------

def test_no_rows_unit_adds_or_removes_objects():
    for unit in ("RowsCore.pas", "RowsPcbLib.pas", "Rows.pas"):
        code = _code(unit)
        for call in ("AddPCBObject", "RemovePCBObject", "DestroyPCBObject",
                     "PCBObjectFactory", "RemoveSchObject", "AddSchObject"):
            assert call not in code, f"{unit} calls {call}"


def test_the_writer_guards_every_row_before_it_writes():
    body = _routine("RowsPcbLib.pas", "RowsPcbLibTrackWrite")
    # rows parsed with the container guard required
    assert "RowsTakeRows(Spec, Params, 'footprint'," in body
    # pass 1 compares guards; the edit opens only in pass 2, after the refusals
    assert body.index("ROWS_REFUSED") < body.index("PcbBeginEdit(0)")
    assert "If Pass = 2 Then PcbEndEdit(0);" in body
    assert "Have, '=', Value) Then" in body
    # each object once, and every written field read back
    repeat = body.index("Else If Done.IndexOf(Addr) >= 0 Then")
    assert body.index("Inc(Repeats)", repeat) - repeat < 120
    assert "' reads ' + Have + ' after writing '" in body


def test_the_read_narrows_inside_altium():
    body = _routine("RowsPcbLib.pas", "RowsPcbLibTrackRead")
    assert "GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject));" in body
    assert "FilterWildMatch(FpName, FpPattern)" in body
    assert "If LayerStr <> '' Then Keep := Track.Layer = LayerId;" in body
    # conditions compiled once, before the walk
    assert body.index("RowsCompileWhere") < body.index("LibraryIterator_Create")
    assert "More := True;" in body
    # field types looked up once per read, not per cell
    assert "Types := RowsFieldTypes(Spec, Fields);" in body
    assert "RowsCellCached(Types.Strings[I]," in body
    # a text cell repeating down its column is escaped once (main's escape
    # loops per character): the cache is made once per read
    assert "CellCache := RowsCellCache(Fields.Count);" in body
    # conditions split once per read, not once per object
    assert "RowsPart(Conds" not in body
    assert "CF := RowsColumn(Conds, 0);" in body


# --- the core under Free Pascal ----------------------------------------------

_LIFTED = [
    ("Utils.pas", "IsFloatStr"), ("Utils.pas", "StrToFloatDef"),
    ("Utils.pas", "BoolToJsonStr"), ("Utils.pas", "FloatToJsonStr"),
    ("Main.pas", "NextBatchOp"), ("Main.pas", "GetBatchField"),
    ("Utils.pas", "FilterOpPos"), ("Utils.pas", "FilterOpText"),
    ("Utils.pas", "FilterWildMatch"), ("Utils.pas", "FilterOpHolds"),
    ("RowsCore.pas", "RowsIntStr"), ("RowsCore.pas", "RowsIntOf"),
    ("RowsCore.pas", "RowsAngleDeg"),
    ("RowsCore.pas", "RowsPointListOk"), ("RowsCore.pas", "RowsTypeWord"), ("RowsCore.pas", "RowsLen"),
    ("RowsCore.pas", "RowsCoord"), ("RowsCore.pas", "RowsFieldAt"),
    ("RowsCore.pas", "RowsFieldType"), ("RowsCore.pas", "RowsFieldWritable"),
    ("RowsCore.pas", "RowsSpecNames"), ("RowsCore.pas", "RowsSpecNameList"),
    ("RowsCore.pas", "RowsParseFields"), ("RowsCore.pas", "RowsFieldTypes"),
    ("RowsCore.pas", "RowsTypeProblem"), ("RowsCore.pas", "RowsValueProblem"),
    ("RowsCore.pas", "RowsCompileWhere"), ("RowsCore.pas", "RowsPart"),
    ("RowsCore.pas", "RowsColumn"), ("RowsCore.pas", "RowsCol"),
    ("RowsCore.pas", "RowsCondHolds"), ("RowsCore.pas", "RowsCell"), ("RowsCore.pas", "RowsCellCache"),
    ("RowsCore.pas", "RowsCellCached"),
    ("RowsCore.pas", "RowsSplit"), ("RowsCore.pas", "RowsPairs"), ("RowsCore.pas", "RowsRowField"),
    ("RowsCore.pas", "RowsRowGuard"),
    ("RowsCore.pas", "RowsFlatAdd"), ("RowsCore.pas", "RowsFlatValue"),
    ("RowsCore.pas", "RowsFlatWrites"), ("RowsCore.pas", "RowsFlatKept"),
    ("RowsCore.pas", "RowsParseFlat"), ("RowsCore.pas", "RowsParseRows"),
]

SPEC = "id:s:r|footprint:s:r|layer:s:w|x1:l:w|width:l:w|keepout:b:r"

#: Pascal boolean expressions that must hold; numbered by position.
CHECKS = [
    # the field table
    "RowsFieldType(SPEC, 'width') = 'l'",
    "RowsFieldType(SPEC, 'WIDTH') = 'l'",
    "RowsFieldType(SPEC, 'nope') = ''",
    "RowsFieldType(SPEC, 'x') = ''",                 # x is not x1
    "RowsFieldWritable(SPEC, 'width')",
    "not RowsFieldWritable(SPEC, 'footprint')",
    "not RowsFieldWritable(SPEC, 'nope')",
    # fields: id first and once; unknown refused
    "FieldsOf('width,id,width') = 'id,width'",
    "FieldsOf('') = 'id,footprint,layer,x1,width,keepout'",
    "FieldsProblem('width,colour') <> ''",
    "TypesOf('width,layer,keepout') = 's,l,s,b'",       # id first, types aligned
    # where
    "WhereOf('width=59055`layer~Top*') = 'width/=/59055,layer/~/Top*'",
    "WhereOf('width>=1000') = 'width/>=/1000'",
    "WhereTypeOf('width=59055`layer~Top*') = 'l,s'",      # type compiled in once
    "ColumnOf('width=59055`layer~Top*', 2) = '59055|Top*'",  # split once per read
    "WhereProblem('width=0.15') <> ''",                    # no decimals on the wire
    "ColumnOf('description=', 2) = ''",                    # an empty value survives
    "WhereProblem('colour=1') <> ''",
    "WhereProblem('width=abc') <> ''",
    "WhereProblem('keepout=maybe') <> ''",
    "WhereProblem('width') <> ''",
    "WhereProblem('') = ''",
    # compares
    "RowsCondHolds('s', 'TopOverlay', '=', 'topoverlay')",
    "RowsCondHolds('s', 'TopOverlay', '~', 'top*')",
    "not RowsCondHolds('s', 'TopOverlay', '!~', 'top*')",
    "RowsCondHolds('b', 'true', '=', 'TRUE')",
    "not RowsCondHolds('i', '', '=', '0')",          # unreadable matches nothing
    "not RowsCondHolds('i', '', '!=', '0')",
    "not RowsCondHolds('s', '', '!=', 'x')",
    # cells
    "RowsCell('l', '59055') = '59055'",
    "RowsCell('l', '') = 'null'",
    "RowsCell('b', 'TRUE') = 'true'",
    "RowsCell('s', 'a\"b') = '\"a\\\"b\"'",
    # lengths: the coordinate itself, whatever unit was named
    "RowsLen(59055, 'mm') = '59055'",
    "RowsLen(59054.6, 'coord') = '59055'",
    "RowsCoord('0.15', 'mm') = 0",
    # rows: fine, sorted, aligned
    "RowsOk('id=30`?footprint=A`width=1000~~id=4`?footprint=B`?width=2000`width=3000')",
    # given 4 then 30, kept sorted as text (30 < 4), each row still with its id
    "RowsSortedText('id=4`?footprint=B`width=3000~~id=30`?footprint=A`width=1000') "
    "= '30>id=30`?footprint=A`width=1000|4>id=4`?footprint=B`width=3000'",
    "RowsGuardOf('id=4`?footprint=B`width=3000', 'footprint') = 'B'",
    # ';' and '|' are plain characters in a value: only the backtick splits
    "RowsGuardOf('id=4`?footprint=A;B|C`width=3000', 'footprint') = 'A;B|C'",
    "RowsFieldOf('id=4`?footprint=B`name=GPIO1;RX=1', 'name') = 'GPIO1;RX=1'",
    "RowsFieldOf('id=4`?footprint=B', 'name') = ''",
    # scaled integers: canonical, 32-bit, so text equality is number equality
    "RowsIntStr('0') and RowsIntStr('-5') and RowsIntStr('2147483647')",
    "RowsIntStr('-2147483647') and RowsIntStr('216535')",
    "not RowsIntStr('2147483648') and not RowsIntStr('12345678901')",
    "not RowsIntStr('007') and not RowsIntStr('+5') and not RowsIntStr('-0')",
    "not RowsIntStr('') and not RowsIntStr('-') and not RowsIntStr('1.5')",
    "not RowsIntStr(' 5') and not RowsIntStr('5e3')",
    "not RowsIntStr('$1F') and not RowsIntStr('5 ') and not RowsIntStr('-')",
    "RowsIntStr('-2147483647') and not RowsIntStr('99999999999')",
    # a repeating text cell is escaped once; a new value replaces it
    "CachedPair('s', 'AB', 'AB') = '\"AB\"|\"AB\"'",
    "CachedPair('s', 'AB', 'CD') = '\"AB\"|\"CD\"'",
    "CachedPair('s', 'A\"B', 'A\"B') = '\"A\\\"B\"|\"A\\\"B\"'",
    "CachedPair('l', '5', '6') = '5|6'",
    "CachedPair('s', '', '') = '\"\"|\"\"'",
    "RowsCondHolds('l', '216535', '=', '216535')",
    "not RowsCondHolds('l', '216535', '=', '216536')",
    "RowsCondHolds('l', '9000', '<', '10000')",          # number order, not text order
    "not RowsCondHolds('l', '10000', '<=', '9000')",
    "RowsCondHolds('a', '-900000', '<', '0') and RowsCondHolds('i', '3', '!=', '4')",
    "not RowsCondHolds('l', '', '=', '0')",               # unreadable matches nothing
    "RowsCondHolds('l', '216535', '~', '2165*')",
    "RowsValueProblem('id:s:r|w:l:w', 'w', '0.15') <> ''",
    "RowsValueProblem('id:s:r|w:l:w', 'w', '-1500') = ''",
    "RowsLen(216535, 'coord') = '216535'",
    "RowsCoord('-1500', 'coord') = -1500",
    "RowsCoord('1.5', 'coord') = 0",
    # RowsSplit: Altium's own string calls, every piece kept in order
    "Joined(RowsSplit('a~~b', '~~'), '|') = 'a|b'",
    "Joined(RowsSplit('a~~b~~', '~~'), '|') = 'a|b|'",
    "Joined(RowsSplit('a~~~~b', '~~'), '|') = 'a||b'",
    "RowsSplit('', '~~').Count = 0",
    "Joined(RowsSplit('a`b'#10'c', '`'), '|') = 'a|b'#10'c'",
    "RowsFieldOf('id=1`text=L1'#13#10'L2`x=1', 'text') = 'L1'#13#10'L2'",
    "RowsOk('id=30`?footprint=A`width=1000~~~~id=4`?footprint=B`width=3000~~')",
    "WhereOf('layer=a;b|c`width>0') = 'layer/=/a;b|c,width/>/0'",
    # rows: every refusal
    "RowsCode('id=1`?footprint=A`width=1000~~id=1`?footprint=A`width=2000') = 'BAD_ROWS'",
    "RowsCode('`?footprint=A`width=1000') = 'BAD_ROWS'",
    "RowsCode('id=1`?footprint=A`colour=1') = 'BAD_FIELD'",
    "RowsCode('id=1`?footprint=A`?colour=1`width=1') = 'BAD_FIELD'",
    "RowsCode('id=1`?footprint=A`footprint=B') = 'READ_ONLY_FIELD'",
    "RowsCode('id=1`?footprint=A`width=wide') = 'BAD_VALUE'",
    "RowsCode('id=1`?footprint=A`?keepout=perhaps`width=1') = 'BAD_VALUE'",
    "RowsCode('id=1`?footprint=A`?width=1000') = 'BAD_ROWS'",     # writes nothing
    "RowsCode('id=1`width=1000') = 'BAD_ROWS'",                   # no container guard
    "RowsCode('') = 'MISSING_PARAMS'",
    "RowsCode('id=1`?footprint=A`?keepout=true`width=1000') = ''",
    "RowsCode(#1'rows_file \"x\" could not be read') = 'BAD_ROWS'",
]

_HARNESS = r"""
const SPEC = '%SPEC%';
""".rstrip()

#: EscapeJsonString's own helpers are Altium-side (StringReplace with -1 as
#: its flags, CodeUnitHex4); quotes and backslashes are all a cell needs here.
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

function CachedPair(T, A, B: String): String;
var C: TStringList;
begin
  C := RowsCellCache(1);
  Result := RowsCellCached(T, A, 0, C) + '|' + RowsCellCached(T, B, 0, C);
  C.Free;
end;

function Joined(L: TStringList; Sep: String): String;
var I: Integer;
begin
  Result := '';
  for I := 0 to L.Count - 1 do
  begin
    if I > 0 then Result := Result + Sep;
    Result := Result + L.Strings[I];
  end;
end;

function FieldsOf(W: String): String;
var P: String; L: TStringList;
begin
  L := RowsParseFields(SPEC, W, P);
  Result := Joined(L, ',');
  L.Free;
end;

function TypesOf(W: String): String;
var P: String; L, T: TStringList;
begin
  L := RowsParseFields(SPEC, W, P);
  T := RowsFieldTypes(SPEC, L);
  Result := Joined(T, ',');
  T.Free; L.Free;
end;

function FieldsProblem(W: String): String;
var L: TStringList;
begin
  L := RowsParseFields(SPEC, W, Result);
  L.Free;
end;

function WhereOf(W: String): String;
var P: String; L: TStringList; I: Integer;
begin
  L := RowsCompileWhere(SPEC, W, P);
  Result := '';
  for I := 0 to L.Count - 1 do
  begin
    if I > 0 then Result := Result + ',';
    Result := Result + RowsPart(L.Strings[I], 0) + '/' + RowsPart(L.Strings[I], 1)
      + '/' + RowsPart(L.Strings[I], 2);
  end;
  if P <> '' then Result := 'PROBLEM';
  L.Free;
end;

function WhereTypeOf(W: String): String;
var P: String; L: TStringList; I: Integer;
begin
  L := RowsCompileWhere(SPEC, W, P);
  Result := '';
  for I := 0 to L.Count - 1 do
  begin
    if I > 0 then Result := Result + ',';
    Result := Result + RowsPart(L.Strings[I], 3);
  end;
  L.Free;
end;

function ColumnOf(W: String; N: Integer): String;
var P: String; L, C: TStringList; I: Integer;
begin
  L := RowsCompileWhere(SPEC + '|description:s:r', W, P);
  C := RowsColumn(L, N);
  Result := '';
  for I := 0 to C.Count - 1 do
  begin
    if I > 0 then Result := Result + '|';
    Result := Result + RowsCol(C, I);
  end;
  if P <> '' then Result := 'PROBLEM ' + P;
  C.Free; L.Free;
end;

function WhereProblem(W: String): String;
var L: TStringList;
begin
  L := RowsCompileWhere(SPEC, W, Result);
  L.Free;
end;

function RowsCode(R: String): String;
var Ids, Texts: TStringList; P: String;
begin
  Ids := TStringList.Create; Texts := TStringList.Create;
  P := RowsParseRows(SPEC, R, 'footprint', Ids, Texts, Result);
  if (P = '') <> (Result = '') then Result := 'INCONSISTENT';
  Ids.Free; Texts.Free;
end;

function RowsOk(R: String): Boolean;
begin
  Result := RowsCode(R) = '';
end;

function RowsSortedText(R: String): String;
var Ids, Texts: TStringList; C: String; I: Integer;
begin
  Ids := TStringList.Create; Texts := TStringList.Create;
  RowsParseRows(SPEC, R, 'footprint', Ids, Texts, C);
  Result := '';
  for I := 0 to Ids.Count - 1 do
  begin
    if I > 0 then Result := Result + '|';
    Result := Result + Ids.Strings[I] + '>' + Texts.Strings[I];
  end;
  Ids.Free; Texts.Free;
end;

function RowsGuardOf(R, N: String): String;
begin
  Result := RowsRowGuard(R, N);
end;

function RowsFieldOf(R, N: String): String;
begin
  Result := RowsRowField(R, N);
end;
"""


def test_the_core_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks = [f"  if not ({c}) then Halt({i});" for i, c in enumerate(CHECKS, 1)]
    program = "\n".join([
        "program rowscore;", "{$mode delphi}", "uses SysUtils, Classes;",
        _ESCAPE,
        *[_routine(u, n) for u, n in _LIFTED],
        _HARNESS.replace("%SPEC%", SPEC),
        _HELPERS,
        "begin", *checks, "end.",
    ])
    path = tmp_path / "rowscore.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowscore.exe" if os.name == "nt" else "rowscore")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {CHECKS[n - 1] if n <= len(CHECKS) else '?'}")


def test_no_rows_unit_hands_request_text_to_a_converter():
    """MEASURED (AD 25.8.1, 2026-11-11): Altium's StrToIntDef RAISES on a
    number past 32 bits ('99999999999' is not a valid integer value, an
    EConvertError modal that stopped the script). Request text becomes a
    number only through RowsIntOr / RowsIntOf / RowsCoord, which check it
    character by character first; the one StrToIntDef left reads the spans
    of main's own JSON key index."""
    altium = Path(__file__).resolve().parents[1] / "scripts" / "altium"
    for unit in sorted(altium.glob("Rows*.pas")):
        code = re.sub(r"\{[^}]*\}", " ", unit.read_text(encoding="utf-8", errors="replace"))
        uses = code.count("StrToIntDef(")
        allowed = 2 if unit.name == "RowsCore.pas" else 0
        assert uses == allowed, f"{unit.name}: {uses} StrToIntDef call(s)"
        assert "IsIntStr(" not in code, f"{unit.name}: main's IsIntStr has no 32-bit bound"
    json_value = _routine("RowsCore.pas", "RowsJsonValue")
    assert json_value.count("StrToIntDef(") == 2


# --- the Python side ---------------------------------------------------------

def test_rows_encode_as_the_wire_format():
    wire = rows_tools.encode_rows([
        {"id": "12", "?footprint": "SOT23", "?width": 0.15, "width": 0.12},
        {"id": "13", "?footprint": "SOT23", "?keepout": False, "layer": "TopOverlay"},
    ])
    assert wire == ("id=12`?footprint=SOT23`?width=0.15`width=0.12"
                    "~~id=13`?footprint=SOT23`?keepout=false`layer=TopOverlay")


@pytest.mark.parametrize("row", [
    {"?footprint": "A", "width": 1},                 # no id
    {"id": "1", "?footprint": "A`width=9"},          # a value that reshapes the row
    {"id": "1", "?footprint": "A~~id=2"},
    {"id": "1", "bad=name": 1},
])
def test_a_row_the_wire_cannot_carry_is_refused(row):
    with pytest.raises(ValueError):
        rows_tools.encode_rows([row])


def test_rows_as_dicts():
    reply = {"data": {"fields": ["id", "width"], "rows": [["1", 0.15], ["2", 0.1]]}}
    assert rows_tools.rows_as_dicts(reply) == [
        {"id": "1", "width": 0.15}, {"id": "2", "width": 0.1}]


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _Bridge:
    """Records each call; a table_file is read at send time (the client
    deletes it after the reply), its text kept as params["table_text"]."""

    def __init__(self, workspace=None):
        self.sent = []
        if workspace is not None:
            self.config = type("Config", (), {"workspace_dir": workspace})()

    #: What rows.list_kinds answers: the field types the client scales by.
    KINDS = {"kinds": [
        {"doc": "pcblib", "kind": "track", "container_guard": "footprint", "fields": [
            {"name": "id", "type": "s"}, {"name": "footprint", "type": "s"},
            {"name": "width", "type": "l", "writable": True},
            {"name": "layer", "type": "s", "writable": True}]},
        {"doc": "pcblib", "kind": "pad", "container_guard": "footprint", "fields": [
            {"name": "id", "type": "s"}, {"name": "footprint", "type": "s"},
            {"name": "name", "type": "s", "writable": True},
            {"name": "x_size", "type": "l", "writable": True},
            {"name": "rotation", "type": "a", "writable": True}]},
        {"doc": "pcb", "kind": "track", "container_guard": "", "fields": [
            {"name": "id", "type": "s"}, {"name": "width", "type": "l", "writable": True}]}]}

    async def send_command_async(self, command, params, *a, **k):
        if command == "rows.list_kinds":
            return {"success": True, "data": self.KINDS}
        params = dict(params)
        if "table_file" in params:
            raw = (self.config.workspace_dir / params["table_file"]).read_bytes()
            assert raw.startswith(b"\xef\xbb\xbf")      # UTF-8 with a BOM, as requests
            params["table_text"] = raw[3:].decode("utf-8")
        self.sent.append((command, params))
        if isinstance(params.get("profile"), str):
            return {"success": True, "data": {"rows": 1, "profile": {"total": 5, "parse": 1}}}
        return {"success": True}


def _tools(monkeypatch, workspace=None):
    mcp, bridge = _Mcp(), _Bridge(workspace)
    monkeypatch.setattr(rows_tools, "get_bridge", lambda: bridge)
    rows_tools.register_rows_tools(mcp)
    return mcp.tools, bridge


def test_rows_read_sends_the_scope_and_the_narrowing(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["rows_read"](doc="pcblib", kind="track",
                                   scope={"footprint": "BGA*"}, layer="Top Overlay",
                                   where="width=0.15", fields="width", units="mil"))
    command, params = bridge.sent[0]
    assert command == "rows.read_rows"
    assert params["footprint"] == "BGA*"
    assert params["layer"] == "Top Overlay"
    assert params["where"] == "width=1500"           # 0.15 mil in 1/10000 mil
    assert "units" not in params                     # the wire is always integers


def test_rows_write_sends_encoded_rows(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["rows_write"](doc="pcblib", kind="track", rows=[
        {"id": "7", "?footprint": "X", "width": 0.2}]))
    command, params = bridge.sent[0]
    assert command == "rows.write_rows"
    assert params["rows"] == "id=7`?footprint=X`width=78740"   # 0.2 mm, the default
    assert "units" not in params


def test_rows_write_sends_the_rows_beside_the_request(monkeypatch, tmp_path):
    """With a workspace the rows go in a file as the flat table (the
    bridge loads it without parsing), never through main's JSON handling;
    the file is a bare name in the workspace, deleted after."""
    tools, bridge = _tools(monkeypatch, tmp_path)
    asyncio.run(tools["rows_write"](doc="pcblib", kind="pad", rows=[
        {"id": "7", "?footprint": "X", "name": "A;B|C"},
        {"id": "8", "?footprint": "X", "x_size": 0.5}]))
    command, params = bridge.sent[0]
    assert "rows" not in params
    assert params["table_file"].startswith("rows_") and "/" not in params["table_file"]
    assert params["table_text"] == rows_tools.table_text([
        {"id": "7", "?footprint": "X", "name": "A;B|C"},
        {"id": "8", "?footprint": "X", "x_size": "196850"}])
    assert list(tmp_path.iterdir()) == []      # deleted after the reply


def test_a_profile_is_asked_for_and_completed_here(monkeypatch, tmp_path):
    tools, bridge = _tools(monkeypatch, tmp_path)
    out = asyncio.run(tools["rows_write"](doc="pcblib", kind="pad", profile=True, rows=[
        {"id": "7", "?footprint": "X", "x_size": 0.5}]))
    assert bridge.sent[0][1]["profile"] == "1"
    prof = out["data"]["profile"]
    assert prof["total"] == 5 and prof["parse"] == 1
    assert {"client", "round_trip", "outside"} <= set(prof)
    asyncio.run(tools["rows_write"](doc="pcblib", kind="pad", rows=[
        {"id": "7", "?footprint": "X", "x_size": 0.5}]))
    assert "profile" not in bridge.sent[1][1]


def test_a_reply_without_a_data_wrapper_is_converted(monkeypatch):
    """The real bridge client returns the reply's data itself, not
    {"success": ..., "data": ...}: lengths still come back in the caller's
    unit, and a profile still gets the client's side."""
    tools, bridge = _tools(monkeypatch)

    async def bare(command, params, *a, **k):
        if command == "rows.list_kinds":
            return _Bridge.KINDS
        return {"fields": ["id", "x_size"], "rows": [["7", 196850]],
                "results": [{"id": "7", "x_size": 196850}],
                "profile": {"total": 5}}
    bridge.send_command_async = bare
    out = asyncio.run(tools["rows_read"](doc="pcblib", kind="pad", units="mm",
                                         fields="x_size", profile=True))
    assert out["rows"] == [["7", 0.5]]
    assert "round_trip" in out["profile"]
    out = asyncio.run(tools["rows_write"](doc="pcblib", kind="pad", units="mm", rows=[
        {"id": "7", "?footprint": "X", "x_size": 0.5}]))
    assert out["results"][0]["x_size"] == 0.5


def test_undo_false_is_sent_for_library_pads_and_vias_only(monkeypatch):
    """undo=false drops Altium's per-object undo record (pcblib pad / via):
    sent as "false", never sent when undo stays on, refused before
    anything is sent for any other kind."""
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["rows_write"](doc="pcblib", kind="pad", undo=False, rows=[
        {"id": "7", "?footprint": "X", "x_size": 0.5}]))
    assert bridge.sent[0][1]["undo"] == "false"
    asyncio.run(tools["rows_write"](doc="pcblib", kind="pad", rows=[
        {"id": "7", "?footprint": "X", "x_size": 0.5}]))
    assert "undo" not in bridge.sent[1][1]
    for doc, kind in (("pcblib", "track"), ("pcb", "track")):
        out = asyncio.run(tools["rows_write"](doc=doc, kind=kind, undo=False, rows=[
            {"id": "7", "?footprint": "X", "width": 0.2}]))
        assert out["success"] is False and "undo=false" in out["error"]
    assert len(bridge.sent) == 2


def test_rows_write_carries_a_board_scope(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["rows_write"](doc="pcb", kind="track", scope={"net": "VCC"},
                                    units="mil", rows=[{"id": "7", "width": 10}]))
    command, params = bridge.sent[0]
    assert params["net"] == "VCC"
    assert params["rows"] == "id=7`width=100000"            # 10 mil


def test_numbers_scale_to_the_wire_and_back():
    """Lengths 1/10000 mil, angles 1/10000 degree; integers stay integers. A
    coordinate read in mm and written back is the same coordinate."""
    assert rows_tools.to_wire("w", "l", 0.15, "mm") == "59055"
    assert rows_tools.to_wire("w", "l", 5.9055, "mil") == "59055"
    assert rows_tools.to_wire("w", "l", 59055, "coord") == "59055"
    assert rows_tools.to_wire("r", "a", 90, "mm") == "900000"
    assert rows_tools.to_wire("n", "i", 3.0, "mm") == "3"
    assert rows_tools.to_wire("b", "b", True, "mm") == "true"
    for bad in (("i", 2.5, "mm"), ("l", 1.5, "coord"), ("l", "wide", "mm"),
                ("l", 1e6, "mm"), ("l", True, "mm")):
        with pytest.raises(ValueError):
            rows_tools.to_wire("x", *bad)
    assert rows_tools.from_wire("l", 59055, "mm") == 0.15
    assert rows_tools.from_wire("l", 59055, "mil") == 5.9055
    assert rows_tools.from_wire("a", 900000, "mm") == 90
    assert rows_tools.from_wire("l", 59055, "coord") == 59055
    assert rows_tools.from_wire("i", 3, "mm") == 3
    for n in (1, 7, 59055, 59057, -123457, 2147483647, -2147483647):
        assert rows_tools.to_wire("x", "l", rows_tools.from_wire("l", n, "mm"), "mm") == str(n)
    assert rows_tools.unit_for("schlib", "") == "mil"
    assert rows_tools.unit_for("pcb", "") == "mm"
    assert rows_tools.unit_for("pcb", "inch") is None


def test_a_where_scales_only_numbers_of_number_fields():
    types = {"width": "l", "rotation": "a", "layer": "s"}
    assert rows_tools.encode_where(["width>=0.15", "layer=Top", "rotation=90"],
                                   types, "mm") == "width>=59055`layer=Top`rotation=900000"
    assert rows_tools.encode_where("width~59*", types, "mm") == "width~59*"


def test_a_read_reply_comes_back_in_the_callers_unit(monkeypatch):
    tools, bridge = _tools(monkeypatch)

    async def reply(command, params, *a, **k):
        if command == "rows.list_kinds":
            return {"success": True, "data": _Bridge.KINDS}
        return {"success": True, "data": {"units": "coord", "fields": ["id", "x_size", "rotation"],
                                          "rows": [["1", 196850, 900000]]}}
    bridge.send_command_async = reply
    out = asyncio.run(tools["rows_read"](doc="pcblib", kind="pad"))
    assert out["data"]["rows"] == [["1", 0.5, 90]]
    assert out["data"]["units"] == "mm"


def test_rows_write_refuses_before_sending(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["rows_write"](doc="pcblib", kind="track",
                                          rows=[{"width": 0.2}]))
    assert out["success"] is False
    out = asyncio.run(tools["rows_write"](doc="board", kind="track",
                                          rows=[{"id": "1", "width": 0.2}]))
    assert out["success"] is False
    assert bridge.sent == []


# One parse error anywhere stops Altium compiling the whole script project,
# and lint.py's comment blanker is fooled by a '{' inside a string. These two
# read the rows units the way the compiler does: strings first, then comments.
ROWS_UNITS = sorted((Path(__file__).resolve().parents[1] / "scripts" / "altium")
                    .glob("Rows*.pas"))


def _code_only(path: Path) -> tuple[str, list[int]]:
    """The unit with comments and string contents blanked, and the numbers
    of the lines on which a string literal is still open (a Pascal string
    cannot run past its line)."""
    out = []
    open_lines = []
    in_str = in_brace = in_line = False
    line = 1
    for ch in path.read_text(encoding="utf-8").replace("\r\n", "\n"):
        if ch == "\n":
            if in_str:
                open_lines.append(line)
                in_str = False
            in_line = False
            line += 1
            out.append(ch)
            continue
        if in_line or (in_brace and ch != "}"):
            continue
        if in_brace:
            in_brace = False
            out.append(" ")
            continue
        if in_str:
            if ch == "'":
                in_str = False
                out.append("'")
            continue
        if ch == "'":
            in_str = True
            out.append("'")
        elif ch == "{":
            in_brace = True
        elif ch == "/" and out and out[-1] == "/":
            out.pop()
            in_line = True
        else:
            out.append(ch)
    return "".join(out), open_lines


def test_every_rows_string_closes_on_its_line():
    # RowsPcbLibMore's write reply once lost its quote marks; Altium refused
    # the project with "Unterminated string".
    for path in ROWS_UNITS:
        _, open_lines = _code_only(path)
        assert open_lines == [], f"{path.name}: unterminated string on line(s) {open_lines}"


# Words DelphiScript reads as keywords even where Delphi allows them as
# names: a parameter called Stored stopped the whole project compiling
# ("Expression expected but Stored found").
DIRECTIVE_WORDS = ("stored", "default", "nodefault", "index", "implements",
                   "readonly", "writeonly", "dispid", "message", "absolute",
                   "external", "forward", "override", "virtual", "resident")


def test_no_rows_unit_names_a_variable_after_a_directive():
    for path in ROWS_UNITS:
        code, _ = _code_only(path)
        for word in DIRECTIVE_WORDS:
            hit = re.search(rf"\b{word}\b", code, re.IGNORECASE)
            assert hit is None, f"{path.name} uses {word!r} as a name"
