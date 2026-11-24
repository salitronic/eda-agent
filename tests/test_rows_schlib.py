# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""rows kind schlib / pin: every pin of a symbol in one read, one write back.

lib_get_pin_list reads a symbol's pins in one call but gives no id, and
writing a different value to each pin costs one symbol walk per pin. The
rows kind schlib / pin (RowsSchLib.pas) reads the pins of the symbol on
screen, or of every symbol matching a pattern, over every part and display
mode, and takes back one row per pin keyed by its id and guarded by any
field, all written in one edit or none.

The pure routines (field table, value checks, the values handed to
SetSchProperty, the write order, the compares) are lifted out with the core
helpers they call and run under Free Pascal. The walks need Altium; their
shape is checked in the source: symbols are found by walking the library,
never by a by-name lookup (that one can save and reopen the library), every
row is guarded before the edit opens, and no pin is added or removed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNIT = "RowsSchLib.pas"


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

def test_the_table_follows_the_contract():
    spec = re.search(r"Result := (.*?);\s*End;", _routine(UNIT, "RowsSchLibPinSpec"), re.S)[1]
    table = "".join(re.findall(r"'([^']*)'", spec))
    names = [e.split(":")[0] for e in table.split("|")]
    assert names[:4] == ["id", "symbol", "part", "mode"]
    assert table.startswith("id:s:r|symbol:s:r|part:i:r|mode:i:r|")
    assert ":n:" not in table                      # no decimal on the wire
    assert "|x:l:w|y:l:w|length:l:w|orientation:i:w|" in table
    assert table == table.lower()
    assert "designator:s:w" in table and "electrical:s:w" in table


def test_symbols_are_never_looked_up_by_name():
    code = _strip(_code(UNIT))
    for call in ("LookupLibComponent", "RefreshSchLibFromDisk", "GetTargetLibComponent",
                 "ScanLibForComponent", "RememberedLibComponent", "LastCreatedLibComponent"):
        assert call not in code, f"{UNIT} calls {call}"
    walk = _routine(UNIT, "RowsSchLibSymbols")
    assert "Iter.AddFilter_ObjectSet(MkSet(eSchComponent));" in walk
    assert "SchLib.CurrentSchComponent" in walk         # the default = on screen
    assert "FilterWildMatch(N, Pattern)" in walk
    # no pin iterator inside the library iterator
    assert ".SchIterator_Create" not in walk.replace("SchLibIterator_Create", "")
    at = _routine(UNIT, "RowsSchLibSymbolAt")
    assert "If N = Want Then" in at                     # by walk position


def test_a_pin_id_is_its_place_in_the_walk():
    """A schematic object's I_ObjectAddress cannot be turned into text on AD
    25.8: IntToStr of it, even through an Integer local, stopped the script
    live three times ("Could not convert variant of type (Dispatch) into
    type (Boolean)"). The address is only COMPARED (the symbol on screen),
    as main does; a pin's id is symbol position : pin position."""
    code = _strip(_code(UNIT))
    uses = [line.strip() for line in code.splitlines() if "I_ObjectAddress" in line]
    assert uses == ["IsCur := (Cur.I_ObjectAddress = Comp.I_ObjectAddress);"], uses
    pin_id = _routine(UNIT, "RowsSchLibPinId")
    assert "Result := SymPos + ':' + IntToStr(PinPos);" in pin_id
    for name in ("RowsSchLibPinRead", "RowsSchLibPinWrite"):
        body = _routine(UNIT, name)
        assert "Inc(PinPos);" in body and "RowsSchLibPinId(Addrs.Strings[C], PinPos)" in body, name
    walk = _routine(UNIT, "RowsSchLibSymbols")
    assert "Inc(Pos);" in walk and "A := IntToStr(Pos);" in walk
    # the position counts EVERY symbol, before any match is decided
    assert walk.index("Inc(Pos);") < walk.index("Take :=")


def test_functions_are_written_through_the_name_inline():
    """The name is set to the function list, Altium splits it (the
    call Lib_SplitPinFunctions makes, inlined, not called), and the name is
    put back, since the split spoils it. A trailing "/" because a name with
    no "/" leaves the list alone (bug 50, measured on AD 25.8.1)."""
    body = _routine(UNIT, "RowsSchLibPinSet")
    assert "Lib_SplitPinFunctions(" not in _strip(_code(UNIT))
    old = body.index("OldName := Pin.Name;")
    to_list = body.index("Pin.Name := List + '/';")
    split = body.index("Pin.SetState_FunctionsFromName;")
    fin = body.index("Finally")
    back = body.index("Pin.Name := OldName;")
    assert old < to_list < split < fin < back


def test_no_pin_is_added_or_removed():
    code = _strip(_code(UNIT))
    for call in ("AddSchObject", "RemoveSchObject", "SchObjectFactory",
                 "RegisterSchObjectInContainer", "AddPCBObject", "RemovePCBObject",
                 "delete_objects"):
        assert call not in code, f"{UNIT} calls {call}"


def test_fields_go_through_the_proven_property_route():
    get = _routine(UNIT, "RowsSchLibPinGet")
    assert "V := GetSchProperty(Pin, Prop);" in get
    put = _routine(UNIT, "RowsSchLibPinSet")
    assert "SetSchProperty(Pin, Prop, RowsSchLibPinSetValue(Field, Value, Units)) = 1" in put
    # reachability trap: a case branch never assigns Result straight from a call
    assert not re.search(r"'\w+':\s*Result :=", _code(UNIT))


def test_the_read_narrows_inside_altium():
    body = _routine(UNIT, "RowsSchLibPinRead")
    assert "PinIter.AddFilter_ObjectSet(MkSet(ePin));" in body
    assert body.index("RowsCompileWhere") < body.index("FocusSchLib")
    assert "Types := RowsFieldTypes(Spec, Fields);" in body
    assert "CF := RowsColumn(Conds, 0);" in body
    assert "RowsPart(Conds" not in body
    assert "More := True;" in body
    assert "'NO_SYMBOL'" in body


def test_the_writer_guards_every_row_before_it_writes():
    body = _routine(UNIT, "RowsSchLibPinWrite")
    assert "RowsTakeRows(Spec, Params, 'symbol'," in body
    assert "ValueProblem" not in body                  # rows.py checks the values
    assert body.index("ROWS_REFUSED") < body.index("SchBeginEdit(SchLib, '');")
    assert "If Pass = 2 Then SchEndEdit(SchLib, 'Edit');" in body
    # each pin in its own robots bracket, written in rank order, read back exactly
    assert body.index("SchBeginModify(Pin);") < body.index("RowsSchLibPinSet(Pin")
    assert body.index("RowsSchLibPinSet(Pin") < body.index("SchEndModify(Pin);")
    assert "Writes := RowsSchLibPinOrder(FKeys, S, E);" in body
    # coordinates straight onto the pin as integers, never via a decimal
    put = _routine(UNIT, "RowsSchLibPinSet")
    assert "Pin.PinLength := RowsCoord(Value, Units)" in put
    assert "Pin.Location := Loc;" in put
    assert "Key, Have, Value, True) Then" in body       # read-back: exact
    assert "Name, Have, Value, False) Then" in body     # guard: as the contract
    repeat = body.index("Else If Done.IndexOf(Addr) >= 0 Then")
    assert body.index("Inc(Repeats)", repeat) - repeat < 120
    assert "MarkLibDirty(SchLib);" in body


def test_the_unit_is_stamped():
    assert "Function UnitStamp_RowsSchLib(Dummy : Integer) : String;" in _code(UNIT)


# --- the pure routines under Free Pascal -------------------------------------

_LIFTED = [
    ("Utils.pas", "IsFloatStr"), ("Utils.pas", "StrToFloatDef"), ("Utils.pas", "IsIntStr"),
    ("Utils.pas", "BoolToJsonStr"), ("Utils.pas", "FloatToJsonStr"),
    ("Main.pas", "NextBatchOp"), ("Main.pas", "GetBatchField"),
    ("Utils.pas", "FilterOpPos"), ("Utils.pas", "FilterOpText"),
    ("Utils.pas", "FilterWildMatch"), ("Utils.pas", "FilterOpHolds"),
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
    (UNIT, "RowsSchLibPinSpec"), (UNIT, "RowsSchLibPinProp"),
    (UNIT, "RowsSchLibFuncList"),
    (UNIT, "RowsPinElecCanon"), (UNIT, "RowsSchLibOrient"),
    (UNIT, "RowsSchLibPinSetValue"), (UNIT, "RowsSchLibPinRank"),
    (UNIT, "RowsSchLibPinOrder"), (UNIT, "RowsSchLibPinWrites"), (UNIT, "RowsSchLibPinSame"),
]

CHECKS = [
    # the table
    "RowsFieldType(SPEC, 'designator') = 's'",
    "RowsFieldType(SPEC, 'orientation') = 'i'",
    "RowsFieldType(SPEC, 'x') = 'l'",
    "RowsFieldType(SPEC, 'show_name') = 'b'",
    "not RowsFieldWritable(SPEC, 'part')",
    "not RowsFieldWritable(SPEC, 'symbol')",
    "RowsFieldWritable(SPEC, 'x')",
    "EveryWritableMapped",                       # each writable field has a property
    "RowsSchLibPinProp('id') = ''",
    "RowsSchLibPinProp('name_font_id') = 'Name_CustomFontID'",
    "RowsSchLibPinProp('functions') = 'DefinedFunctions'",
    "RowsSchLibPinProp('hidden') = 'IsHidden'",
    # electrical, by any of its names
    "RowsPinElecCanon('bidirectional') = 'io'",
    "RowsPinElecCanon('eElectricOutput') = 'output'",
    "RowsPinElecCanon('Open_Collector') = 'open_collector'",
    "RowsPinElecCanon('HiZ') = 'hiz'",
    "RowsPinElecCanon('passive') = 'passive'",
    "RowsPinElecCanon('analog') = ''",
    # values
    # functions in Altium's own "/" form (";" separates a row's fields)
    "RowsSchLibFuncList(' A / B//C ') = 'A/B/C'",
    "RowsSchLibFuncList('A;B') = 'A/B'",
    "RowsSchLibFuncList('A') = 'A'",
    "RowsSchLibPinSetValue('functions', 'GPIO1/ RX1 /', 'mil') = 'GPIO1/RX1'",
    "RowsSchLibPinSame('s', 'functions', 'A;B', 'A/B', True)",
    "not RowsSchLibPinSame('s', 'functions', 'A;B', 'B/A', True)",
    "not RowsSchLibPinSame('s', 'functions', '', 'A', True)",
    # what SetSchProperty is handed
    "RowsSchLibPinSetValue('orientation', '270', 'mm') = '3'",
    "RowsSchLibPinSetValue('orientation', '-90', 'mm') = '3'",
    "RowsSchLibPinSetValue('orientation', '0', 'mm') = '0'",
    "RowsSchLibPinSetValue('electrical', 'bidirectional', 'mm') = 'io'",
    "RowsSchLibPinSetValue('hidden', 'TRUE', 'mm') = 'true'",
    "RowsSchLibPinSetValue('name', ' VIN+ ', 'mm') = 'VIN+'",
    # the write order: orientation first, location last, the rest as given
    "WritesOf('id=1`?symbol=A`x=1`name=B`orientation=90`y=2`length=3') "
    "= 'orientation,name,length,x,y'",
    "WritesOf('id=1`?symbol=A`?x=1`name=B') = 'name'",
    # compares: guards as the contract, read-backs exact on text
    "RowsSchLibPinSame('s', 'designator', 'a1', 'A1', False)",
    "not RowsSchLibPinSame('s', 'designator', 'a1', 'A1', True)",
    "RowsSchLibPinSame('s', 'designator', 'A1', 'A1', True)",
    "RowsSchLibPinSame('s', 'electrical', 'io', 'bidirectional', True)",
    "not RowsSchLibPinSame('s', 'electrical', 'io', 'input', False)",
    "not RowsSchLibPinSame('s', 'electrical', '', 'analog', False)",
    "RowsSchLibPinSame('n', 'orientation', '270', '-90', True)",
    "not RowsSchLibPinSame('n', 'orientation', '90', '-90', True)",
    "not RowsSchLibPinSame('n', 'orientation', '', '0', True)",
    "RowsSchLibPinSame('l', 'x', '1000000', '1000000', True)",
    "not RowsSchLibPinSame('l', 'x', '1000001', '1000000', True)",
    "RowsSchLibPinSame('b', 'hidden', 'true', 'TRUE', True)",
    # rows against this table: the container guard is ?symbol
    "RowsCode('id=1`?symbol=U1`?designator=3`name=VIN') = ''",
    "RowsCode('id=1`?designator=3`name=VIN') = 'BAD_ROWS'",
    "RowsCode('id=1`?symbol=U1`part=2') = 'READ_ONLY_FIELD'",
    "RowsCode('id=1`?symbol=U1`orientation=up') = 'BAD_VALUE'",
    "RowsCode('id=1`?symbol=U1`show_name=maybe') = 'BAD_VALUE'",
    "RowsCode('id=1`?symbol=U1`colour=1') = 'BAD_FIELD'",
    "RowsCode('id=1`?symbol=U1`functions=A/B/C') = ''",
]

_SHIM = r"""
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
var SPEC: String;

function EveryWritableMapped: Boolean;
var N: TStringList; I: Integer;
begin
  Result := True;
  N := RowsSpecNames(SPEC);
  for I := 0 to N.Count - 1 do
    if RowsFieldWritable(SPEC, N.Strings[I]) and (RowsSchLibPinProp(N.Strings[I]) = '') then
      Result := False;
  N.Free;
end;

function WritesOf(R: String): String;
var L: TStringList; I: Integer;
begin
  L := RowsSchLibPinWrites(R);
  Result := '';
  for I := 0 to L.Count - 1 do
  begin
    if I > 0 then Result := Result + ',';
    Result := Result + RowsPart(L.Strings[I], 0);
  end;
  L.Free;
end;

function RowsCode(R: String): String;
var Ids, Texts: TStringList; P: String;
begin
  Ids := TStringList.Create; Texts := TStringList.Create;
  P := RowsParseRows(SPEC, R, 'symbol', Ids, Texts, Result);
  if (P = '') <> (Result = '') then Result := 'INCONSISTENT';
  Ids.Free; Texts.Free;
end;
"""


def test_the_pin_routines_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    lifted = [_routine(u, n) for u, n in _LIFTED]
    # the helpers must see the field table before the checks run
    checks = ["  SPEC := RowsSchLibPinSpec(0);"]
    checks += [f"  if not ({c}) then Halt({i});" for i, c in enumerate(CHECKS, 1)]
    program = "\n".join([
        "program rowsschlib;", "{$mode delphi}", "uses SysUtils, Classes;",
        _SHIM, *lifted, _HELPERS, "begin", *checks, "end.",
    ])
    path = tmp_path / "rowsschlib.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowsschlib.exe" if os.name == "nt" else "rowsschlib")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {CHECKS[n - 1] if n <= len(CHECKS) else '?'}")
