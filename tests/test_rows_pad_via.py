# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""rows kinds for vias and pads: pcblib/via, pcblib/pad, pcb/via, pcb/pad.

"All vias on VCC with a 0.5 mm hole -> 0.8 mm", "everything on GND tented",
"soldermask on all pads of footprint X", "paste off on the pads of designator
Y": one narrowed read, one guarded write, whatever the number of objects.
The contract is docs/ROWS_CONTRACT.md; the unit is RowsPadVia.pas.

The unit's pure routines (field tables, row checks, the translation of a pad
row checks of a pad and a via taken together) are lifted with the core
routines they call and run under Free Pascal. The walks need Altium: their
shape is checked in the source -- every guard and every annular ring checked
before the edit opens, pads written through the proven set_pad_geometry
helpers, a board write re-indexed, only the focused board touched, nothing
added or removed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNIT = "RowsPadVia.pas"


def _code(unit: str) -> str:
    return (ALTIUM / unit).read_text(encoding="utf-8", errors="replace")


def _routine(unit: str, name: str) -> str:
    code = _code(unit)
    start = re.search(rf"(?m)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


# --- the unit's shape --------------------------------------------------------

def test_the_unit_never_adds_or_removes_objects():
    code = re.sub(r"\{[^}]*\}", " ", _code(UNIT))
    for call in ("AddPCBObject", "RemovePCBObject", "DestroyPCBObject",
                 "PCBObjectFactory", "ReindexBoardObject"):
        assert call not in code, f"{UNIT} calls {call} itself"


def test_every_routine_takes_a_parameter_and_the_unit_is_stamped():
    code = _code(UNIT)
    assert re.search(r"(?m)^(?:Function|Procedure) \w+\s*(?::|;)", code) is None
    assert "Function UnitStamp_RowsPadVia(Dummy : Integer) : String;" in code


def test_getters_assign_a_local_not_result():
    # tests/test_bridge_handlers_reachable.py reads 'x': Result := Fn( as a command.
    code = _code(UNIT)
    assert re.search(r"'[a-z_]+':\s*Result\s*:=", code) is None


def test_reads_narrow_inside_altium():
    lib = _routine(UNIT, "RowsPVLibRead")
    assert "GrpIter.AddFilter_ObjectSet(MkSet(ObjKind));" in lib
    assert "FilterWildMatch(FpName, FpPattern)" in lib
    assert lib.index("RowsCompileWhere") < lib.index("LibraryIterator_Create")
    assert "CF := RowsColumn(Conds, 0);" in lib
    assert "Types := RowsFieldTypes(Spec, Fields);" in lib
    walk = _routine(UNIT, "RowsPVCollect")
    # net -> its own group, designator / pattern -> those components, bounds -> spatial
    assert "Group := Net.GroupIterator_Create;" in walk
    assert "Group := Comp.GroupIterator_Create;" in walk
    assert "Spatial.AddFilter_Area(" in walk
    assert "Iter.AddFilter_Method(eProcessFree)" in walk
    assert walk.count("AddFilter_ObjectSet(MkSet(ObjKind));") == 4
    # an index miss falls back to the walk, never to an empty answer
    assert "If Matches.Count = 0 Then" in walk


def test_only_the_focused_board_is_touched():
    code = re.sub(r"\{[^}]*\}", " ", _code(UNIT))
    assert "GetPCBBoardAnywhere" not in code
    assert "GetCurrentPCBBoard" not in code
    assert code.count("RowsFocusedBoard(") == 2
    board = _routine("RowsCore.pas", "RowsFocusedBoard")
    found = board.index("GetPCBBoardAnywhere(0)")
    assert board.index("If Kind <> 'PCB' Then") < found
    # and the board found must be the focused document
    assert found < board.index("Have := Result.FileName;") < board.index("Result := Nil;", found)


@pytest.mark.parametrize("writer", ["RowsPVLibWrite", "RowsPVPcbWrite"])
def test_writers_refuse_before_the_edit_opens(writer):
    body = _routine(UNIT, writer)
    # the request checks are rows.py's (rows_rules): the writer checks only
    # what needs the document, before the edit opens
    assert "RowsPVRowsProblem" not in body and "ValueProblem" not in body
    assert body.index("ROWS_REFUSED") < body.index("PcbBeginEdit(0)")
    assert "PcbEndEdit(0)" in body
    if writer == "RowsPVLibWrite":
        # the template's two passes: guards in pass 1, the edit only in pass 2
        pass1 = body.index("If Pass = 1 Then")
        pass2 = body.index("Else If (Done.IndexOf(Addr) < 0)")
        assert pass1 < body.index("RowsPVFirstLook(") < pass2
        assert pass1 < body.index("RowsPVViaGeomProblem(") < pass2
        assert "If Pass = 2 Then PcbEndEdit(0);" in body
        assert "If (Pass = 1) And (Notes <> '') Then" in body
    else:
        assert body.index("RowsPVGuards(") < body.index("PcbBeginEdit(0)")
        assert body.index("RowsPVViaGeomProblem(") < body.index("PcbBeginEdit(0)")
        assert body.index("If Notes <> '' Then") < body.index("PcbBeginEdit(0)")
    # every written field read back
    assert "RowsPVCheck(Prim, IsVia, Keys, Vals, Types" in body
    # every row split once, before anything else reads it
    assert body.count("RowsTakeRows(") == 1
    for split in ("RowsPairs(", "RowsRowField(", "RowsRowGuard(", "RowsParseRows("):
        assert split not in body


def test_the_library_writer_needs_the_footprint_guard_and_the_board_one_does_not():
    assert "'footprint'," in _routine(UNIT, "RowsPVLibWrite").split("RowsTakeRows(")[1][:80]
    assert "RowsTakeRows(Spec, Params, '', Ids, Texts, RS, RE," \
        in _routine(UNIT, "RowsPVPcbWrite")


def test_the_board_writer_reindexes_a_via_after_a_mask_write():
    body = _routine(UNIT, "RowsPVPcbWrite")
    apply = body.index("Ok := RowsPVApply(")
    # only a via's mask can leave the index stale (#208); only those rows pay
    assert "If RowsFlatWrites(Keys, F) And ((Pos('mask', Keys.Strings[F]) > 0)" in body
    assert "Or (Pos('tenting', Keys.Strings[F]) > 0)) Then" in body
    assert body.index("If Indexed Then Before := BoardIndexState(Board, Prim);") < apply
    assert apply < body.index("Held := EnsureIndexed(Board, Prim, Before);")
    assert body.count("BoardIndexState(") == 1
    # board pads never move: MayMove False
    assert "False, Addr, Notes, True);" in body


def test_pads_are_written_as_integers_in_main_set_pad_geometry_order():
    """Altium's TCoord is an integer, so the pad writer
    assigns the wire's integers straight in -- main's ApplyPadEdit parsed a
    decimal out of a ';' row for every field (~170 us each in StrToFloat)."""
    apply = _routine(UNIT, "RowsPVApply")
    assert "RowsPVPadApply(Pad, Keys, Vals, S, E, Keep, XOrg, YOrg," in apply
    code = re.sub(r"\{[^}]*\}", " ", _code(UNIT))
    for gone in ("ApplyPadEdit(", "PadEditProblem(", "StrToFloat", "FloatToJsonStr",
                 "CoordFromUnits", "MilsToCoord", "GetBatchField("):
        assert gone not in code, gone
    body = _routine(UNIT, "RowsPVPadApply")
    order = [body.index(x) for x in (
        "Pad.Mode := ePadMode_Simple",
        "Pad.TopXSize := RowsIntOf(XSize)",
        "SetPadRoundRect(Pad, CornerPct)",
        "Pad.Rotation := RowsAngleDeg(Rot)",
        "Pad.X := XOrg + RowsIntOf(XS)",
        "Pad.Name := Name",
        "Pad.HoleSize := RowsIntOf(Hole)",
        "Pad.SetState_IsTopPasteEnabled(",
        "Pad.SolderMaskExpansionFromHoleEdge :=",
        "Cache.SolderMaskExpansion := RowsIntOf(MaskT)")]
    assert order == sorted(order)
    # the source before the values; the flag only on a manual paste
    assert body.index("If MaskMan = 'true' Then") < body.index("Cache.PasteMaskExpansion := RowsIntOf(Paste)")
    flag = body.index("Pad.SetState_IsTopPasteEnabled(")
    assert body.rindex("Cache.PasteMaskExpansionValid := eCacheManual;", 0, flag) < flag
    # WHICH fields follows Keep; what a write depends on follows the row as given
    assert "If AllMaskB = '' Then" in body
    assert "If AllCorner <> '' Then CornerPct := RowsIntOf(AllCorner);" in body
    # both taken from the row whether Keep writes them or not: read before
    # the Keep test in the one pass over the fields
    loop = body.index("For F := S To E - 1 Do")
    skip = body.index("If (Keep <> '') And (Copy(Keep, F - S + 1, 1) <> '1') Then Continue;")
    assert loop < body.index("Then AllCorner := V") < skip
    assert loop < body.index("Then AllMaskB := V") < skip
    # one pass, not a lookup per field
    assert "RowsFlatKept(" not in body
    assert "PCBM_BeginModify" in body and "PCBM_EndModify" in body


def test_the_via_writer_follows_the_proven_routes():
    body = _routine(UNIT, "RowsPVViaApply")
    order = [body.index(s) for s in (
        "Via.Mode := ePadMode_Simple",            # stack first
        "Via.HoleSize := HoleC",                  # drill and size
        "Via.SizeOnLayer(eMidLayers) :=",         # per-layer sizes
        "Via.SetState_IsTenting_Top(",            # tenting
        "Via.SolderMaskExpansionFromHoleEdge :=",  # edge, then the mask
        "Cache.SolderMaskExpansion := RowsCoord(MaskS, Units);")]
    assert order == sorted(order)
    # tenting only holds on a manual mask: made manual first
    tent = body.index("Via.SetState_IsTenting_Top(")
    assert body.rindex("Cache.SolderMaskExpansionValid := eCacheManual;", 0, tent) < tent
    # the mask goes through the pad cache, never the via
    assert "AsPad.SetState_Cache := Cache;" in body
    assert "Via.SetState_Cache" not in body
    assert "PCBM_BeginModify" in body and "PCBM_EndModify" in body


def test_a_middle_size_that_reads_zero_is_unverified_not_failed():
    body = _routine(UNIT, "RowsPVCheck")
    unverified = body.index("(Key = 'size_mid')")
    failed = body.index("Result := False;")
    assert unverified < failed
    assert "unverified" in body[unverified:failed]


def test_no_redraw_per_object_one_refresh_per_write():
    """The pad / via appliers leave the redraw to the writer: one view
    refresh at the end of a write, not one invalidate per object."""
    for applier in ("RowsPVPadApply", "RowsPVViaApply"):
        body = _routine(UNIT, applier)
        assert "GraphicallyInvalidate" not in body
        assert "PCBM_BeginModify" in body and "PCBM_EndModify" in body
    assert "PcbLib.Board.ViewManager_FullUpdate" in _routine(UNIT, "RowsPVLibWrite")
    assert "Board.ViewManager_FullUpdate" in _routine(UNIT, "RowsPVPcbWrite")


def test_the_read_back_reads_one_dispatch_deep():
    """A written field is read straight from the pad / via getter and
    compared inline (the generic RowsPVGet + RowsCondHolds route cost a few
    hundred microseconds a field); an unreadable value fails the row."""
    body = _routine(UNIT, "RowsPVCheck")
    assert "Have := RowsPVPadGet(Pad, Key, XOrg, YOrg, Units);" in body
    assert "Have := RowsPVViaGet(Via, Key, XOrg, YOrg, Units)" in body
    assert "RowsPVGet(" not in body and "RowsCondHolds(" not in body
    assert "Else If (Have = '') Or (Not Eq) Then" in body
    assert "If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;" in body


def test_testpoints_and_the_ipc4761_type_are_read_only():
    code = _code(UNIT)
    for field in ("testpoint_top", "testpoint_bottom", "assy_testpoint_top",
                  "assy_testpoint_bottom", "structure_type"):
        assert f"|{field}:b:w" not in code and f"|{field}:n:w" not in code
    # read by the members main already compiles (PCB.pas ViaStructureJson)
    assert "VS := Via.ViaStructure;" in _routine(UNIT, "RowsPVViaStructure")
    assert "SetState_ViaStructure" not in code and "IsTestpoint_Top :=" not in code


# --- the pure routines under Free Pascal --------------------------------------

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
    ("RowsCore.pas", "RowsTypeProblem"), ("RowsCore.pas", "RowsValueProblem"), ("RowsCore.pas", "RowsPart"),
    ("RowsCore.pas", "RowsSplit"), ("RowsCore.pas", "RowsPairs"), ("RowsCore.pas", "RowsRowField"),
    ("RowsCore.pas", "RowsRowGuard"),
    ("RowsCore.pas", "RowsFlatAdd"), ("RowsCore.pas", "RowsFlatValue"),
    ("RowsCore.pas", "RowsFlatWrites"), ("RowsCore.pas", "RowsFlatKept"),
    ("RowsCore.pas", "RowsParseFlat"), ("RowsCore.pas", "RowsParseRows"),
    (UNIT, "RowsPVViaFields"), (UNIT, "RowsPVPadFields"),
    (UNIT, "RowsPcbLibViaSpec"), (UNIT, "RowsPcbLibPadSpec"),
    (UNIT, "RowsPcbViaSpec"), (UNIT, "RowsPcbPadSpec"),
    (UNIT, "RowsPVOneOf"), (UNIT, "RowsPVInCsv"), (UNIT, "RowsPVParseBounds"),
    ]

#: Pascal boolean expressions that must hold; numbered by position.
CHECKS = [
    # the field tables: id, the doc's context fields, then the object's own
    "Pos('id:s:r|footprint:s:r|description:s:r|x:l:r|', LIBVIA) = 1",
    "Pos('id:s:r|footprint:s:r|description:s:r|name:s:w|', LIBPAD) = 1",
    "Pos('id:s:r|owner:s:r|designator:s:r|pattern:s:r|x:l:r|', PCBVIA) = 1",
    "Pos('id:s:r|owner:s:r|designator:s:r|pattern:s:r|name:s:r|', PCBPAD) = 1",
    "NoDup(LIBVIA) and NoDup(LIBPAD) and NoDup(PCBVIA) and NoDup(PCBPAD)",
    "RowsFieldType(LIBVIA, 'size') = 'l'",
    "RowsFieldType(LIBPAD, 'rotation') = 'a'",
    "RowsFieldType(LIBPAD, 'corner_radius_pct') = 'i'",
    "RowsFieldType(PCBVIA, 'structure_type') = 'i'",
    "Pos(':n:', LIBVIA + LIBPAD + PCBVIA + PCBPAD) = 0",       # no decimal left
    "RowsFieldType(LIBVIA, 'tenting_top') = 'b'",
    "RowsFieldType(PCBVIA, 'net') = 's'",
    "RowsFieldWritable(LIBVIA, 'hole') and RowsFieldWritable(PCBVIA, 'mask_top')",
    "RowsFieldWritable(PCBVIA, 'tenting_bottom') and RowsFieldWritable(LIBVIA, 'size_mid')",
    "not RowsFieldWritable(LIBVIA, 'testpoint_top')",
    "not RowsFieldWritable(PCBVIA, 'assy_testpoint_bottom')",
    "not RowsFieldWritable(PCBVIA, 'structure_type')",
    "not RowsFieldWritable(PCBVIA, 'net') and not RowsFieldWritable(PCBVIA, 'x')",
    "RowsFieldWritable(LIBPAD, 'name') and RowsFieldWritable(LIBPAD, 'x')",
    "not RowsFieldWritable(PCBPAD, 'name') and not RowsFieldWritable(PCBPAD, 'y')",
    "RowsFieldWritable(PCBPAD, 'paste') and RowsFieldWritable(PCBPAD, 'mask_top')",
    "not RowsFieldWritable(PCBPAD, 'layer') and not RowsFieldWritable(PCBPAD, 'designator')",
    # the flat row table: one split, then fields by index
    "FlatVal(' A = x ', 'a') = 'x'",
    "FlatVal('a=1`a=2`?a=3', 'a') = '2'",                     # the last one given
    "FlatVal('a=1`?a=3', '?a') = '3'",
    "FlatVal('a=1', 'b') = ''",
    "FlatVal('x=f(a=b)', 'x') = 'f(a=b)'",                    # only the first '=' splits
    "FlatVal('pins=A;B|C', 'pins') = 'A;B|C'",                 # ';' and '|' are plain text
    "FlatKept('a=1`b=2`a=3', '110', 'a') = '1'",
    "FlatKept('a=1`b=2`a=3', '', 'a') = '3'",
    "FlatKept('a=1`b=2', '01', 'a') = ''",
    "Ranges(LIBVIA, 'id=2`?footprint=A`size=8000~~id=1`?footprint=A`hole=3000') "
    "= '1:3-6,2:0-3'",                                         # sorted ids, row ranges
    "TypeAt(LIBVIA, 'id=2`?footprint=A`size=8000', 2) = 'l'",
    "TypeAt(LIBVIA, 'id=2`?footprint=A`size=8000', 1) = 's'",
    "TypeAt(LIBVIA, 'id=2`?footprint=A`size=8000', 0) = 's'",
    # a pad row taken together (set_pad_geometry's checks, on the table)
    # values a type check cannot see
    # a via row taken together
    # scope helpers
    "RowsPVInCsv('U1, u5,R3', 'U5')",
    "not RowsPVInCsv('U1,U5', 'U')",
    "not RowsPVInCsv('', 'U1') and not RowsPVInCsv('U1', '')",
    "RowsPVOneOf(' Free ', '|all|free|component|')",
    "not RowsPVOneOf('fre', '|all|free|component|')",
    "not RowsPVOneOf('', '|all|free|component|')",
    "BoundsOf('3,4,1,2') = '1,2,3,4'",
    "BoundsOf('-1, 25000, 3, -4') = '-1,-4,3,25000'",
    "BoundsOf('1,2.5,3,4') = 'BAD'",                          # whole numbers only
    "BoundsOf('1,2,3') = 'BAD'",
    "BoundsOf('1,2,3,4,5') = 'BAD'",
    "BoundsOf('1,a,3,4') = 'BAD'",
    # the rows a writer accepts, with these tables
    "RowsCode(LIBVIA, 'id=1`?footprint=A`?hole=5000`size=8000', 'footprint') = ''",
    "RowsCode(LIBVIA, 'id=1`?footprint=A`size=0.8', 'footprint') = 'BAD_VALUE'",
    "RowsCode(LIBVIA, 'id=1`?footprint=A`size=08000', 'footprint') = 'BAD_VALUE'",
    "RowsCode(LIBVIA, 'id=1`?footprint=A`size=+8000', 'footprint') = 'BAD_VALUE'",
    "RowsCode(LIBVIA, 'id=1`?footprint=A`testpoint_top=true', 'footprint') = 'READ_ONLY_FIELD'",
    "RowsCode(LIBVIA, 'id=1`?footprint=A`tenting_top=maybe', 'footprint') = 'BAD_VALUE'",
    "RowsCode(LIBVIA, 'id=1`size=8000', 'footprint') = 'BAD_ROWS'",
    "RowsCode(PCBVIA, 'id=1`?net=GND`tenting_top=true', '') = ''",
    "RowsCode(PCBPAD, 'id=1`name=7', '') = 'READ_ONLY_FIELD'",
    "RowsCode(LIBPAD, 'id=1`?footprint=A`?x_size=6000`paste_top_enabled=false', 'footprint') = ''",
]

_HELPERS = r"""
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

_TAIL = r"""

function LIBVIA: String; begin Result := RowsPcbLibViaSpec(0); end;
function LIBPAD: String; begin Result := RowsPcbLibPadSpec(0); end;
function PCBVIA: String; begin Result := RowsPcbViaSpec(0); end;
function PCBPAD: String; begin Result := RowsPcbPadSpec(0); end;

function NoDup(Spec: String): Boolean;
var L: TStringList; I: Integer;
begin
  L := RowsSpecNames(Spec);
  Result := L.Count > 10;
  for I := 0 to L.Count - 1 do
    if L.IndexOf(L.Strings[I]) <> I then Result := False;
  L.Free;
end;

function BoundsOf(S: String): String;
var X1, Y1, X2, Y2: Integer;
begin
  if RowsPVParseBounds(S, X1, Y1, X2, Y2) then
    Result := IntToStr(X1) + ',' + IntToStr(Y1) + ','
      + IntToStr(X2) + ',' + IntToStr(Y2)
  else
    Result := 'BAD';
end;

function FlatVal(R, Key: String): String;
var K, V, T: TStringList;
begin
  K := TStringList.Create; V := TStringList.Create; T := TStringList.Create;
  RowsFlatAdd(R, K, V, T);
  Result := RowsFlatValue(K, V, 0, K.Count, Key);
  K.Free; V.Free; T.Free;
end;

function FlatKept(R, Keep, Key: String): String;
var K, V, T: TStringList;
begin
  K := TStringList.Create; V := TStringList.Create; T := TStringList.Create;
  RowsFlatAdd(R, K, V, T);
  Result := RowsFlatKept(K, V, 0, K.Count, Keep, Key);
  K.Free; V.Free; T.Free;
end;

function ParseInto(Spec, R: String; Ids, Texts, RS, RE, K, V, T: TStringList): String;
var C: String;
begin
  Result := RowsParseFlat(Spec, R, 'footprint', Ids, Texts, RS, RE, K, V, T, C);
end;

function Ranges(Spec, R: String): String;
var Ids, Texts, RS, RE, K, V, T: TStringList; I: Integer;
begin
  Ids := TStringList.Create; Texts := TStringList.Create; RS := TStringList.Create;
  RE := TStringList.Create; K := TStringList.Create; V := TStringList.Create;
  T := TStringList.Create;
  Result := ParseInto(Spec, R, Ids, Texts, RS, RE, K, V, T);
  if Result = '' then
    for I := 0 to Ids.Count - 1 do
    begin
      if Result <> '' then Result := Result + ',';
      Result := Result + Ids[I] + ':' + RS[I] + '-' + RE[I];
    end;
  Ids.Free; Texts.Free; RS.Free; RE.Free; K.Free; V.Free; T.Free;
end;

function TypeAt(Spec, R: String; F: Integer): String;
var Ids, Texts, RS, RE, K, V, T: TStringList;
begin
  Ids := TStringList.Create; Texts := TStringList.Create; RS := TStringList.Create;
  RE := TStringList.Create; K := TStringList.Create; V := TStringList.Create;
  T := TStringList.Create;
  Result := ParseInto(Spec, R, Ids, Texts, RS, RE, K, V, T);
  if Result = '' then Result := T[F];
  Ids.Free; Texts.Free; RS.Free; RE.Free; K.Free; V.Free; T.Free;
end;

function RowsCode(Spec, R, Required: String): String;
var Ids, Texts: TStringList; P: String;
begin
  Ids := TStringList.Create; Texts := TStringList.Create;
  P := RowsParseRows(Spec, R, Required, Ids, Texts, Result);
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
        "program rowspadvia;", "{$mode delphi}", "uses SysUtils, Classes;",
        _HELPERS,
        *[_routine(u, n) for u, n in _LIFTED],
        _TAIL,
        "begin", *checks, "end.",
    ])
    path = tmp_path / "rowspadvia.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowspadvia.exe" if os.name == "nt" else "rowspadvia")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {CHECKS[n - 1] if n <= len(CHECKS) else '?'}")


def test_the_library_writer_writes_only_what_would_change():
    """Measured on 1000 pads (2026-11-10): a pad row that changed nothing
    cost the same ~28 ms as one that did. The footprint writer reads each
    field first, writes only those that differ, leaves a row that is already
    right alone, and still reads every row back in full. Since 2026-11-23
    the reads are pass 1's: pass 2 walks only footprints holding a row that
    changes, stops once each is written, and is skipped when none does."""
    body = _routine(UNIT, "RowsPVLibWrite")
    pass1 = body.index("If Pass = 1 Then")
    narrow = body.index("KeepL.Strings[K] := RowsPVFirstLook(Prim, IsVia,")
    apply = body.index("Ok := RowsPVApply(Prim, IsVia, Keys, Vals, S, E,")
    check = body.index("If Not RowsPVCheck(Prim, IsVia, Keys, Vals, Types,")
    assert pass1 < narrow < apply < check
    # the change check's cells are an unchanged row's read-back
    assert "RecL.Strings[K] := Rec;" in body
    assert "+ RecL.Strings[K] + '}');" in body and "Inc(Unchanged);" in body
    look = _routine(UNIT, "RowsPVFirstLook")
    assert "Rec := Rec + ',\"' + Name + '\":' + RowsCell(T, Have);" in look
    # nothing to change: no edit, no second walk
    assert body.index("If NChanged = 0 Then Break;") < body.index("PcbBeginEdit(0);")
    # pass 2: only footprints with a changed row, only changed rows, then stop
    assert "(Pass = 2) And (FpChanged.IndexOf(FpName) >= 0)" in body
    assert "And (Pos('1', KeepL.Strings[K]) > 0) Then" in body
    assert body.count("((Pass = 1) Or (Done.Count < NChanged))") == 2
    # a changed row not reached again is a failure, not silence
    assert "not found again to write;" in body
    # only the kept fields are written, every field is read back
    assert "KeepL.Strings[K], XOrg, YOrg, Units, True, Addr," in body
    assert "RowsWriteReplySkip(" in body
    # text written exactly (a case-only rename is a change), guards ignore
    # case; an unreadable value fails a guard and is always written
    assert "If T = 's' Then Keep := Have <> Value" in look
    assert "If (T = 's') Or (T = 'b') Then Eq := UpperCase(Have) = UpperCase(Value)" in look
    assert "If (Have = '') Or (Not Eq) Then" in look
    assert look.index("Keep := True;") < look.index("If Have <> '' Then")
    # guards and the change check in ONE pass, one dispatch deep
    assert look.count("For F := S To E - 1 Do") == 1
    assert "Have := RowsPVPadGet(Pad, Name, XOrg, YOrg, Units);" in look
    assert "RowsCondHolds(" not in look
    # the same refusal text the board writer (RowsPVGuards) gives
    note = "'row ' + Addr + ': ' + Name + ' is ' + Have"
    assert note in look and note in _routine(UNIT, "RowsPVGuards")


def test_undo_false_drops_only_the_per_object_undo_record():
    """undo=false (library writer only) leaves out the per-object
    BeginModify / EndModify messages and nothing else: the edit bracket,
    the redraw and the read-back stay; the board writer always sends them;
    the dispatcher refuses undo=false for any other kind and a value that
    is neither true nor false."""
    for name in ("RowsPVPadApply", "RowsPVViaApply"):
        body = _routine(UNIT, name)
        assert body.count("PCBServer.SendMessageToRobots(") == 2
        flat = re.sub(r"\s+", " ", body)
        assert flat.count("If Robots Then Try PCBServer.SendMessageToRobots(") == 2
    lib = _routine(UNIT, "RowsPVLibWrite")
    assert "Robots := Not RowsUndoOff(Params);" in lib
    assert "IgnoreChanges" not in lib.replace("BeginIgnoreChanges gained", "")
    assert "PcbBeginEdit(0);" in lib and "PcbEndEdit(0);" in lib
    pcb = _routine(UNIT, "RowsPVPcbWrite")
    assert "RowsUndoOff" not in pcb
    assert re.search(r"Addr, Notes, True\);", pcb)
    off = _routine("RowsCore.pas", "RowsUndoOff")
    assert "ExtractJsonValue(Params, 'undo'))) = 'false'" in off
    rows = _code("Rows.pas")
    assert "(Undo = 'false') And (Key <> 'pcblib/pad') And (Key <> 'pcblib/via')" in rows
    assert "(Undo <> '') And (Undo <> 'true') And (Undo <> 'false')" in rows
    assert rows.index("(Undo = 'false') And") < rows.index("'pcblib/track': Result := RowsPcbLibTrackWrite")
