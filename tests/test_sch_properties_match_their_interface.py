# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A schematic property must only be read off a type that has it.

Reported as issue #22 by zacky0904 on AD25: obj_query with
``object_type="ePort"`` and ``Orientation`` among the properties opened an
"Undeclared identifier: Orientation" dialog and stalled the polling loop
instead of returning a tool error.

Text and Orientation are not on the base ISch_GraphicalObject. ISch_Port
names itself with Name, and carries its direction in Style. The scripting
reference corroborates that twice: PlaceAPort.pas builds a Port from Name,
Style, IOType, Alignment and Width and never touches Text or Orientation,
and ReplaceSchObjects.pas reads a cross-sheet connector's Orientation
precisely in order to MAP it onto Port.Style.

WHY IT CANNOT BE CAUGHT. An undeclared identifier is surfaced by the
script engine as a modal before any surrounding Try/Except runs. The same
engine behaviour defeated Try/Except around StrToFloat and around the
response-file create. Guarding after the fact is not available here, so
the access has to be gated before it happens.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

GENERIC = Path(__file__).parent.parent / "scripts" / "altium" / "Generic.pas"
MAIN = Path(__file__).parent.parent / "scripts" / "altium" / "Main.pas"


def _source() -> str:
    return GENERIC.read_text(encoding="utf-8", errors="replace")


def _decommented(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
    return re.sub(r"//.*", " ", text)


@pytest.mark.parametrize("prop", ["Text", "Orientation", "IsHidden"])
def test_the_access_is_gated_by_type(prop):
    """Every read and write of these goes through a type check."""
    code = _decommented(_source())
    guard = {"Text": "SchObjectHasText", "Orientation": "SchObjectHasOrientation",
             "IsHidden": "SchObjectHasIsHidden"}[prop]

    # \bObj\. and not just Obj\. : PowerObj.Orientation is a TYPED local
    # where the access is already correct, and matching it flagged code
    # that was never in question. Same substring trap as the undeclared
    # local lint rule.
    accesses = [m.start()
                for m in re.finditer(r"\bObj\." + prop + r"\b", code)]
    assert accesses, f"no Obj.{prop} access found; has the getter moved?"

    for at in accesses:
        window = code[max(0, at - 400):at]
        assert guard in window or "ObjectId" in window, (
            f"Obj.{prop} at offset {at} is reached without a type check. "
            f"On a type that lacks it this raises an undeclared identifier, "
            f"which is a modal that stalls the polling loop and which "
            f"Try/Except cannot contain")


@pytest.mark.parametrize("guard", ["SchObjectHasText", "SchObjectHasOrientation"])
def test_a_port_is_excluded(guard):
    """The reported type. Both properties are absent on ISch_Port."""
    code = _source()
    start = code.index(f"Function {guard}")
    body = code[start:code.index("\nEnd;", start)]
    assert "ePort" in body, (
        f"{guard} does not exclude ePort, which is the type the report "
        f"reproduced against")


def test_the_connection_point_is_pin_only():
    """ConnectionX/ConnectionY read Orientation too.

    Added while fixing a different bug and carrying the same fault: a
    connection point is a pin idea, and the Try around the read cannot
    save a type that has no Orientation.
    """
    code = _decommented(_source())
    for prop in ("ConnectionX", "ConnectionY"):
        at = code.index(f"PropName = '{prop}'")
        window = code[at:at + 400]
        assert "ObjectId <> ePin" in window, (
            f"{prop} does not restrict itself to pins before reading "
            f"Orientation")


def test_the_net_highlight_path_uses_name_for_a_port():
    """It iterated ePort and read Obj.Text on everything but a sheet entry."""
    code = _decommented(_source())
    at = code.index("ObjNet :=")
    window = code[max(0, at - 600):at + 400]
    assert "SchObjectHasText" in window, (
        "the net-highlight path still decides between Name and Text by "
        "checking only for a sheet entry, so a Port takes the Text branch")


# ---------------------------------------------------------------------------
# Fault 1: the constant AD25 refuses.
# ---------------------------------------------------------------------------

def test_the_sentinel_is_not_the_32_bit_boundary():
    """AD25 rejects 2147483647 outright with "Invalid constant"."""
    code = _decommented(MAIN.read_text(encoding="utf-8", errors="replace"))
    assert "2147483647" not in code, (
        "the 32-bit boundary literal is back. AD25 refuses it while "
        "compiling and the loop never starts")

    m = re.search(r"MAX_INT\s*=\s*(\d+)\s*;", code)
    assert m, "MAX_INT is gone; DelphiScript does not predefine MaxInt"
    value = int(m.group(1))
    assert value < 2147483647
    # It is a "larger than anything real" sentinel for internal units,
    # where 1 unit is 1/10000 mil. It has to clear a realistic board.
    assert value >= 1000000000, (
        f"MAX_INT is {value}, which is under 100 inches in internal units "
        f"and could be reached by a real coordinate")


def test_no_hardcoded_boundary_literals_elsewhere():
    """Two sentinels bypassed the constant and carried the literal."""
    offenders = []
    for path in (GENERIC.parent).glob("*.pas"):
        if path.name == "Altium_MCP.pas":
            continue
        code = _decommented(path.read_text(encoding="utf-8", errors="replace"))
        for i, line in enumerate(code.splitlines(), 1):
            if "2147483647" in line:
                offenders.append(f"{path.name}:{i}")
    assert not offenders, (
        f"32-bit boundary literals outside the constant: {offenders}")

# AD21 property failures: inspect the real dispatcher, not a copied table.
def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    assert end, f"missing end of {name}"
    body = code[start.start():start.start() + end.end()]
    # Keep quoted JSON/string literals intact while stripping Pascal comments.
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ",
                  body, flags=re.S)


def _assert_property_contract(code: str, prop: str, guard: str, rejected: str):
    capability = _routine(code, guard)
    assert re.search(rf"Obj\.ObjectId\s*(?:=|<>)\s*{rejected}\b", capability)
    assert "Result := False" in capability
    for routine, diagnostic in (("GetSchProperty", "unreadable"),
                                ("SetSchProperty", "unknown")):
        body = _routine(code, routine)
        start = re.search(rf"Else If PropName = '{prop}'\s+Then", body)
        assert start, f"missing {routine} {prop} dispatch"
        tail = body[start.end():]
        branch = re.split(r"\bElse If PropName\b", tail, maxsplit=1)[0]
        assert f"If {guard}(Obj) Then" in branch
        assert branch.index(guard) < branch.index(f"Obj.{prop}")
        if routine == "GetSchProperty":
            assert f"NotePropertyDiag('{diagnostic}', PropName)" in branch
            assert "Result := ''" in body[:start.start()]
        else:
            assert "Matched := False" in branch
            assert "If Not Matched Then Result := 0" in body
            assert "If Result = 0 Then NotePropertyDiag('unknown', PropName)" in body


@pytest.mark.parametrize("prop,guard,rejected", [
    ("Text", "SchObjectHasText", "eParameterSet"),
    ("IsHidden", "SchObjectHasIsHidden", "eNetLabel"),
])
def test_ad21_unsupported_property_contract(prop, guard, rejected):
    _assert_property_contract(_source(), prop, guard, rejected)


@pytest.mark.parametrize("prop,guard,rejected", [
    ("Text", "SchObjectHasText", "eParameterSet"),
    ("IsHidden", "SchObjectHasIsHidden", "eNetLabel"),
])
def test_property_regression_detects_removed_guards(prop, guard, rejected):
    """Mutate only an in-memory copy; each original crash must be detected."""
    source = _source()
    _assert_property_contract(source, prop, guard, rejected)
    for routine in ("GetSchProperty", "SetSchProperty"):
        original = _routine(source, routine)
        broken = original.replace(f"If {guard}(Obj) Then", "If True Then")
        assert broken != original
        # _routine strips comments, so substitute by original routine offsets.
        start = source.index(f"Function {routine}(")
        end = source.index("\nEnd;", start) + len("\nEnd;")
        mutant = source[:start] + broken + source[end:]
        with pytest.raises(AssertionError):
            _assert_property_contract(mutant, prop, guard, rejected)
    mutant = re.sub(rf"(Obj\.ObjectId\s*(?:=|<>)\s*){rejected}\b", r"\1eDummy", source)
    with pytest.raises(AssertionError):
        _assert_property_contract(mutant, prop, guard, rejected)


@pytest.mark.parametrize("name", ["Gen_CreateObject", "Gen_BatchCreate"])
def test_create_preflights_before_writing_or_registering(name):
    body = _routine(_source(), name)
    preflight = body.index("UnsupportedSchProperty(NewObj, PropsStr)")
    apply = body.index("ApplySetProperties(NewObj, PropsStr)")
    assert preflight < apply
    rejection = body[preflight:apply]
    assert "SchServer.DestroySchObject(NewObj)" in rejection
    assert "UNSUPPORTED_PROPERTY" in rejection
    assert "ResetPropertyDiag(0)" in body[:preflight]
    for registration in ("Component.AddSchObject(NewObj)",
                         "RegisterSchObjectInContainer(NewObj)"):
        assert apply < body.index(registration)
    if name == "Gen_CreateObject":
        assert "BuildErrorResponse" in rejection and "Exit;" in rejection
    else:
        # Failure must fall through to item reporting and the next iteration.
        assert "Inc(Failed)" in rejection
        assert "Exit;" not in rejection and "Break;" not in rejection
        assert "Else" in rejection
        loop = body.index("While True Do")
        assert loop < body.index("ResetPropertyDiag(0)", loop) < preflight
        assert '"property"' in body and '"object_type"' in body
        assert "Inc(Created)" in body[apply:]


def test_extracted_pascal_capabilities_and_creation_preflight(tmp_path):
    """Execute production guards/parser with FPC; this is not an Altium test."""
    import shutil
    import subprocess

    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    source = _source()
    routines = "\n".join(_routine(source, name) for name in
                         ("SchObjectHasText", "SchObjectHasIsHidden",
                          "UnsupportedSchProperty"))
    # Match all identifiers used by the real guards, so existing denylist
    # exclusions remain part of the executable test.
    types = sorted(set(re.findall(r"\be[A-Z]\w*", routines)) |
                   {"eNetLabel", "eParameterSet", "eParameter", "ePort", "eSheetEntry"})
    constants = "\n".join(f"  {name} = {i};" for i, name in enumerate(types))
    checks = [
        ("eParameterSet", "Text=bad", "Text"),
        ("eParameterSet", "Location.X=10|Text=bad|Location.Y=20", "Text"),
        ("eParameterSet", "Location.X=10|Location.Y=20", ""),
        ("eNetLabel", "Text=GOOD|IsHidden=true", "IsHidden"),
        ("eNetLabel", "IsHidden=false|Text=GOOD", "IsHidden"),
        ("eNetLabel", "Text=GOOD|Location.X=10|Location.Y=20", ""),
        ("eParameter", "Text=GOOD|IsHidden=true", ""),
        ("ePort", "Text=bad", "Text"),
        ("eSheetEntry", "Text=bad", "Text"),
        ("eNetLabel", "IsHidden|Text=GOOD", ""),
        ("eNetLabel", "Text=contains=equals", ""),
        ("eNetLabel", "", ""),
    ]
    calls = "\n".join(
        f"  Obj.ObjectId := {kind}; if UnsupportedSchProperty(Obj, '{props}') <> '{expected}' then Halt({i});"
        for i, (kind, props, expected) in enumerate(checks, 1))
    program = ("program property_preflight;\n{$mode delphi}\nuses SysUtils;\nconst\n" + constants +
               "\ntype ISch_GraphicalObject = class\n  ObjectId: Integer;\nend;\n" + routines +
               "\nvar Obj: ISch_GraphicalObject;\nbegin\n  Obj := ISch_GraphicalObject.Create;\n" +
               calls + "\n  Obj.Free;\nend.\n")
    path = tmp_path / "property_preflight.pas"
    path.write_text(program, encoding="utf-8")
    compiled = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executable = tmp_path / ("property_preflight.exe" if os.name == "nt" else "property_preflight")
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, f"production preflight failed case {result.returncode}: {result.stderr}"


def test_extracted_pascal_single_and_mixed_batch_creation(tmp_path):
    """Execute actual creation routines against observable Altium API stubs.

    Transport JSON decoding and API objects are mocked; the preflight,
    destruction, registration, counters and batch JSON assembly are production
    Pascal. No claim about the real Altium scripting engine is made here.
    """
    import json
    import shutil
    import subprocess

    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    source = _source()
    main = MAIN.read_text(encoding="utf-8")
    guards = "\n".join(_routine(source, name) for name in
                       ("SchObjectHasText", "SchObjectHasIsHidden", "UnsupportedSchProperty"))
    creators = "\n".join(_routine(source, name) for name in
                         ("Gen_CreateObject", "Gen_BatchCreate"))
    parsers = "\n".join(_routine(main, name) for name in ("NextBatchOp", "GetBatchField"))
    stubs = r'''
program creation_regression;
{$mode delphi}
uses SysUtils;
const ePort=1; eSheetEntry=2; eParameterSet=3; eNetLabel=4;
      eParameter=5; eSchLib=6; eCreate_Default=0;
type
  TSchObject = class
    ObjectId: Integer;
    DocumentName: String;
    CurrentSchComponent: TSchObject;
    Registered: Boolean;
    procedure AddSchObject(Obj: TSchObject);
    procedure RegisterSchObjectInContainer(Obj: TSchObject);
    procedure GraphicallyInvalidate;
  end;
  ISch_GraphicalObject = TSchObject;
  ISch_Document = TSchObject;
  ISch_Lib = TSchObject;
  ISch_Component = TSchObject;
  TProcessControl = class
    procedure PreProcess(Doc: TSchObject; Context: String);
    procedure PostProcess(Doc: TSchObject; Context: String);
  end;
  TSchServer = class
    ProcessControl: TProcessControl;
    Doc: TSchObject;
    function SchObjectFactory(Kind, Mode: Integer): TSchObject;
    procedure DestroySchObject(Obj: TSchObject);
    function GetCurrentSchDocument: TSchObject;
  end;
var SchServer: TSchServer;
    RegisteredCount, DestroyedCount, AppliedCount, ResetCount: Integer;
    DiagDirty: Boolean;
procedure TSchObject.AddSchObject(Obj: TSchObject);
begin
  if Obj.Registered then Halt(51);
  Obj.Registered := True;
  Inc(RegisteredCount);
end;
procedure TSchObject.RegisterSchObjectInContainer(Obj: TSchObject);
begin AddSchObject(Obj); end;
procedure TSchObject.GraphicallyInvalidate;
begin end;
procedure TProcessControl.PreProcess(Doc: TSchObject; Context: String);
begin end;
procedure TProcessControl.PostProcess(Doc: TSchObject; Context: String);
begin end;
function TSchServer.SchObjectFactory(Kind, Mode: Integer): TSchObject;
begin
  if DiagDirty then Halt(52);
  Result := TSchObject.Create;
  Result.ObjectId := Kind;
end;
procedure TSchServer.DestroySchObject(Obj: TSchObject);
begin
  if Obj.Registered then Halt(53);
  Inc(DestroyedCount);
  DiagDirty := True;
  Obj.Free;
end;
function TSchServer.GetCurrentSchDocument: TSchObject;
begin Result := Doc; end;
procedure ResetPropertyDiag(Dummy: Integer);
begin DiagDirty := False; Inc(ResetCount); end;
procedure SchRegisterObject(Container, Obj: TSchObject);
begin if not Obj.Registered then Halt(54); end;
procedure MarkDocDirtyByPath(Path: String);
begin end;
function ObjectTypeFromString(S: String): Integer;
begin
  Result := -1;
  if S='eParameterSet' then Result:=eParameterSet;
  if S='eNetLabel' then Result:=eNetLabel;
  if S='eParameter' then Result:=eParameter;
end;
function UnknownObjectTypeMessage(S: String): String;
begin Result := S; end;
function EscapeJsonString(S: String): String;
begin Result := S; end;
function BuildErrorResponse(Id, Code, Message: String): String;
begin Result := '{"code":"'+Code+'","message":"'+Message+'"}'; end;
function BuildSuccessResponse(Id, Payload: String): String;
begin Result := Payload; end;
'''
    transport = r'''
function ExtractJsonValue(Params, Key: String): String;
begin
  if Key='operations' then Result:=Params
  else Result:=GetBatchField(Params, Key);
end;
'''
    application = r'''
procedure ApplySetProperties(Obj: TSchObject; Props: String);
begin
  if UnsupportedSchProperty(Obj, Props)<>'' then Halt(55);
  if DiagDirty then Halt(56);
  Inc(AppliedCount);
  DiagDirty := True;
end;
'''
    execution = r'''
procedure PrintCounts;
begin
  WriteLn(RegisteredCount, ',', DestroyedCount, ',', AppliedCount, ',', ResetCount);
end;
begin
  SchServer := TSchServer.Create;
  SchServer.ProcessControl := TProcessControl.Create;
  SchServer.Doc := TSchObject.Create;
  DiagDirty := True;
  WriteLn(Gen_CreateObject('object_type=eNetLabel;properties=IsHidden=true', '1'));
  PrintCounts;
  WriteLn(Gen_CreateObject('object_type=eParameterSet;properties=Text=bad', '2'));
  PrintCounts;
  WriteLn(Gen_CreateObject('object_type=eNetLabel;properties=Text=GOOD', '3'));
  PrintCounts;
  RegisteredCount:=0; DestroyedCount:=0; AppliedCount:=0; ResetCount:=0;
  DiagDirty := True;
  WriteLn(Gen_BatchCreate(
    'object_type=eNetLabel;properties=IsHidden=true~~' +
    'object_type=eNetLabel;properties=Text=GOOD~~' +
    'object_type=eParameterSet;properties=Text=bad~~' +
    'object_type=eParameterSet;properties=Location.X=10|Location.Y=20', '4'));
  PrintCounts;
  WriteLn(Gen_CreateObject('object_type=eParameter;properties=IsHidden=true', '5'));
  PrintCounts;
end.
'''
    path = tmp_path / "creation_regression.pas"
    path.write_text(stubs + parsers + transport + guards + application + creators + execution,
                    encoding="utf-8")
    compiled = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executable = path.with_suffix(".exe" if os.name == "nt" else "")
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, f"mock API invariant failed ({result.returncode}): {result.stdout} {result.stderr}"
    lines = result.stdout.splitlines()
    assert json.loads(lines[0]) == {"code": "UNSUPPORTED_PROPERTY", "message": "Property IsHidden is not supported on eNetLabel"}
    assert lines[1] == "0,1,0,1"
    assert json.loads(lines[2]) == {"code": "UNSUPPORTED_PROPERTY", "message": "Property Text is not supported on eParameterSet"}
    assert lines[3] == "0,2,0,2"
    assert json.loads(lines[4]) == {"created": True, "object_type": "eNetLabel"}
    assert lines[5] == "1,2,1,3"
    assert json.loads(lines[6]) == {
        "created": 2, "failed": 2, "total": 4,
        "failures": [
            {"index": 0, "object_type": "eNetLabel", "reason": "UNSUPPORTED_PROPERTY", "property": "IsHidden"},
            {"index": 2, "object_type": "eParameterSet", "reason": "UNSUPPORTED_PROPERTY", "property": "Text"},
        ],
    }
    assert lines[7] == "2,2,2,5"
    assert json.loads(lines[8]) == {"created": True, "object_type": "eParameter"}
    assert lines[9] == "3,2,3,6"
