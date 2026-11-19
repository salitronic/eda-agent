# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A parameter delete leaves a sheet's system parameters in place, and says so.

MEASURED on AD 25.8.1: obj_delete with object_type parameter and filter
Name=Engineer on a sheet answered matched 1 and the sheet's SYSTEM
parameter Engineer was gone from the saved file. Altium refuses to remove
a system parameter itself, and recreates a missing one on the next load
with the value *, so the delete only lost its value (Title, Author,
DocumentNumber and Revision are system parameters too). The sheet pass
also counted a match before the remove, so an object the remove left in
place was found and counted again up to the guard.

The sheet pass now skips a system parameter Altium keeps (IsSystemParameter
with a ReadOnlyState other than 0; a sheet rule the user added reads 0 and
stays deletable), counts a remove only when the next walk finds one match
fewer, and the reply names what it kept. The routines are compiled with
Free Pascal against stand-in Altium objects.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import generic

from tests.pascal_source import load

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


# -- what the source does ------------------------------------------------------

def test_the_sheet_pass_goes_through_the_guarded_helper():
    body = _flat(load(SCRIPTS / "Generic.pas")["DeleteParametersAnyOwner"])
    assert ("If OwnerDesig = '' Then RemoveMatchingSheetParams(SchDoc, "
            "ReducedFilter, TotalMatched);") in body
    # No remove of its own any more: every sheet parameter goes through it.
    assert "SchDoc.RemoveSchObject(" not in body


def test_a_kept_parameter_is_never_picked_for_removal():
    body = _flat(load(SCRIPTS / "Generic.pas")["RemoveMatchingSheetParams"])
    fixed = body.index("If SheetParamIsFixed(Param) Then")
    picked = body.index("If FoundParam = Nil Then FoundParam := Param;")
    assert fixed < body.index("NoteDeleteKept(ParamName);") < picked
    assert body.index("Else Begin Inc(Removable);") < picked


def test_the_protection_is_read_through_a_parameter_view():
    raw = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8-sig")
    at = raw.index("Function SheetParamIsFixed(")
    assert "Par : ISch_Parameter;" in raw[at:at + 300]
    body = _flat(load(SCRIPTS / "Generic.pas")["SheetParamIsFixed"])
    assert "Try IsSystem := Par.IsSystemParameter;" in body
    assert "Try RoState := Par.ReadOnlyState;" in body


def test_the_reply_names_what_was_kept():
    body = _flat(load(SCRIPTS / "Utils.pas")["ModifyOutcomeJson"])
    kept = body.index("',\"kept_system_parameters\":' + DeleteKeptJson(0)")
    # Named beside a fault too, and the fault still wins the reason.
    assert kept < body.index("If _DeleteFaultStr <> '' Then")
    after = body.index("If _DeleteKeptStr <> '' Then Begin")
    assert body.index("_DeleteFaultStr <> ''") < after < body.index("_PropertyDiagStr <> ''")
    assert '"success":false' in body[after:]


def test_the_kept_names_are_cleared_per_request():
    assert "_DeleteKeptStr := '';" in load(SCRIPTS / "Utils.pas")["ResetPropertyDiag"]


def test_the_batch_names_the_kept_parameters():
    body = _flat(load(SCRIPTS / "Generic.pas")["Gen_BatchDelete"])
    assert "Kept := DeleteKeptText(0);" in body
    kept = body.index("Else If Kept <> '' Then")
    assert kept < body.index("Pos('\"success\":false', OpResult)")
    assert "'kept system parameters: '" in body
    # Read after the operation ran, from that operation's own buffer.
    assert body.index("ResetPropertyDiag(0);") < body.index("ProcessActiveDoc(") < kept


# -- what it does, compiled ----------------------------------------------------

# Stand-ins for the Altium objects the routines touch. A parameter is a
# class with the members read; reading Name or ReadOnlyState can be made to
# raise, as a member Altium will not answer does inside a Try.
_MOCKS = r"""
type
  TPar = class
    FName : String;
    IsSystemParameter : Boolean;
    FReadOnly : Integer;
    Sticky, RaiseOnRemove, RaiseOnReadOnly, RaiseOnName : Boolean;
    function GetName : String;
    function GetReadOnly : Integer;
    property Name : String read GetName;
    property ReadOnlyState : Integer read GetReadOnly;
  end;
  ISch_GraphicalObject = TPar;
  ISch_Parameter = TPar;
  TIter = class
    Items : TList;
    At : Integer;
    procedure SetState_IterationDepth(D : Integer);
    procedure AddFilter_ObjectSet(S : Integer);
    function FirstSchObject : TPar;
    function NextSchObject : TPar;
  end;
  ISch_Iterator = TIter;
  TDoc = class
    Params : TList;
    RemoveCalls : Integer;
    RemovedNames : String;
    constructor Create;
    function SchIterator_Create : TIter;
    procedure SchIterator_Destroy(It : TIter);
    procedure RemoveSchObject(P : TPar);
  end;
  ISch_Document = TDoc;

const
  eParameter = 41;
  eIterateFirstLevel = 1;

var
  _PropertyDiagStr, _DeleteFaultStr, _DeleteKeptStr : String;
  _SchWritesLanded : Integer;

function TPar.GetName : String;
begin
  if RaiseOnName then raise Exception.Create('no name');
  Result := FName;
end;

function TPar.GetReadOnly : Integer;
begin
  if RaiseOnReadOnly then raise Exception.Create('no read-only state');
  Result := FReadOnly;
end;

procedure TIter.SetState_IterationDepth(D : Integer); begin end;
procedure TIter.AddFilter_ObjectSet(S : Integer); begin end;

function TIter.FirstSchObject : TPar;
begin
  At := 0;
  if Items.Count = 0 then Result := nil else Result := TPar(Items[0]);
end;

function TIter.NextSchObject : TPar;
begin
  Inc(At);
  if At >= Items.Count then Result := nil else Result := TPar(Items[At]);
end;

constructor TDoc.Create;
begin
  Params := TList.Create;
  RemoveCalls := 0;
  RemovedNames := '';
end;

function TDoc.SchIterator_Create : TIter;
var I : Integer;
begin
  Result := TIter.Create;
  Result.Items := TList.Create;
  for I := 0 to Params.Count - 1 do Result.Items.Add(Params[I]);
end;

procedure TDoc.SchIterator_Destroy(It : TIter);
begin
  It.Items.Free;
  It.Free;
end;

procedure TDoc.RemoveSchObject(P : TPar);
begin
  Inc(RemoveCalls);
  if P.RaiseOnRemove then raise Exception.Create('remove failed');
  if P.Sticky then Exit;
  RemovedNames := RemovedNames + P.FName + ';';
  Params.Remove(P);
end;

function MkSet(A : Integer) : Integer; begin Result := A; end;

{ The filter as the delete passes it: empty matches every parameter. }
function MatchesFilter(P : TPar; F : String) : Boolean;
begin
  if F = '' then Result := True
  else if Copy(F, 1, 5) = 'Name=' then Result := SameText(P.FName, Copy(F, 6, MaxInt))
  else Result := False;
end;

function EscapeJsonString(S : String) : String;
begin
  Result := StringReplace(S, '"', '\"', [rfReplaceAll]);
end;

function RenderPropertyDiagJson(Dummy : Integer) : String;
begin
  Result := '{}';
end;

function AddPar(Doc : TDoc; N : String; Sys : Boolean; RO : Integer) : TPar;
begin
  Result := TPar.Create;
  Result.FName := N;
  Result.IsSystemParameter := Sys;
  Result.FReadOnly := RO;
  Doc.Params.Add(Result);
end;

{ A sheet as Altium holds one: system parameters (read-only names), a
  user parameter, the system Rule placeholder, a rule the user added and a
  user parameter locked by hand (read-only, not a system parameter). }
function NewSheet(Dummy : Integer) : TDoc;
begin
  Result := TDoc.Create;
  AddPar(Result, 'Title', True, 1);
  AddPar(Result, 'Engineer', True, 1);
  AddPar(Result, 'PartNumber', False, 0);
  AddPar(Result, 'Rule', True, 1);
  AddPar(Result, 'Rule', True, 0);
  AddPar(Result, 'Revision', True, 1);
  AddPar(Result, 'Locked', False, 3);
end;

procedure Report(Doc : TDoc; Total : Integer);
begin
  WriteLn('matched=', Total);
  WriteLn('removed=', Doc.RemovedNames);
  WriteLn('calls=', Doc.RemoveCalls);
  WriteLn('left=', Doc.Params.Count);
  WriteLn('kept=', DeleteKeptText(0));
  WriteLn('fault=', _DeleteFaultStr);
  WriteLn('reply=', ModifyOutcomeJson(0));
end;
"""

_UNITS = [
    ("Utils.pas", "ResetPropertyDiag"),
    ("Utils.pas", "NoteDeleteFault"),
    ("Utils.pas", "NoteDeleteKept"),
    ("Utils.pas", "DeleteKeptText"),
    ("Utils.pas", "DeleteKeptJson"),
    ("Utils.pas", "ModifyOutcomeJson"),
    ("Generic.pas", "SheetParamIsFixed"),
    ("Generic.pas", "RemoveMatchingSheetParams"),
]


def _run(tmp_path: Path, body: list[str]) -> dict[str, str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    real = "\n\n".join(_routine(u, n) for u, n in _UNITS)
    # DelphiScript takes -1 for "replace all"; Free Pascal wants the set.
    real = re.sub(r"(StringReplace\([^;]*?),\s*-1\)", r"\1, [rfReplaceAll])", real)
    # ModifyOutcomeJson comes after the stand-ins that call it in Report.
    utils, rest = real.split("Function SheetParamIsFixed", 1)
    program = "\n".join([
        "program sheetparams;", "{$mode delphi}{$H+}", "uses Classes, SysUtils;",
        _MOCKS.split("procedure Report(")[0],
        utils, "Function SheetParamIsFixed" + rest,
        "procedure Report(" + _MOCKS.split("procedure Report(")[1],
        "var Doc : TDoc; Total : Integer; P : TPar;",
        "begin", "  ResetPropertyDiag(0);", "  Total := 0;", *body, "end.",
    ])
    path = tmp_path / "sheetparams.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("sheetparams.exe" if os.name == "nt" else "sheetparams")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return dict(line.split("=", 1) for line in ran.stdout.splitlines())


def _delete(tmp_path: Path, filt: str, *setup: str) -> dict[str, str]:
    return _run(tmp_path, [
        "  Doc := NewSheet(0);", *setup,
        f"  RemoveMatchingSheetParams(Doc, '{filt}', Total);",
        "  Report(Doc, Total);",
    ])


def test_a_system_parameter_is_kept_and_not_counted(tmp_path):
    out = _delete(tmp_path, "Name=Engineer")
    assert out["matched"] == "0"
    assert out["calls"] == "0" and out["left"] == "7"
    assert out["kept"] == "Engineer"
    assert out["fault"] == ""
    assert '"kept_system_parameters":["Engineer"]' in out["reply"]
    assert '"success":false' in out["reply"] and '"partial"' not in out["reply"]
    assert "obj_modify" in out["reply"]


def test_a_user_rule_goes_and_the_rule_placeholder_stays(tmp_path):
    out = _delete(tmp_path, "Name=Rule")
    assert out["matched"] == "1"
    assert out["removed"] == "Rule;" and out["calls"] == "1"
    assert out["left"] == "6" and out["kept"] == "Rule"


def test_a_user_parameter_goes_and_the_reply_is_a_success(tmp_path):
    out = _delete(tmp_path, "Name=PartNumber")
    assert out["matched"] == "1" and out["removed"] == "PartNumber;"
    assert out["kept"] == "" and out["fault"] == ""
    assert '"success":true' in out["reply"]
    assert "kept_system_parameters" not in out["reply"]


def test_a_user_parameter_locked_by_hand_still_goes(tmp_path):
    # Read-only, but not a system parameter: Altium does not recreate it.
    out = _delete(tmp_path, "Name=Locked")
    assert out["matched"] == "1" and out["removed"] == "Locked;"
    assert out["kept"] == ""


def test_every_match_is_walked_past_a_kept_one(tmp_path):
    # Empty filter: everything removable goes, every system name is kept once.
    out = _delete(tmp_path, "")
    assert out["matched"] == "3"
    assert out["removed"] == "PartNumber;Rule;Locked;" and out["calls"] == "3"
    assert out["left"] == "4"
    assert out["kept"] == "Title, Engineer, Rule, Revision"
    assert '"kept_system_parameters":["Title","Engineer","Rule","Revision"]' in out["reply"]


def test_a_remove_that_took_nothing_is_a_fault_not_a_count(tmp_path):
    out = _delete(tmp_path, "Name=PartNumber",
                  "  TPar(Doc.Params[2]).Sticky := True;")
    assert out["matched"] == "0"
    assert out["calls"] == "1", "the same parameter was removed again"
    assert out["fault"] == 'the parameter "PartNumber" stayed on the sheet after it was removed'
    assert '"partial":true' in out["reply"]


def test_a_remove_that_raises_keeps_the_count_of_those_before(tmp_path):
    out = _delete(tmp_path, "",
                  "  TPar(Doc.Params[4]).RaiseOnRemove := True;")
    assert out["matched"] == "1" and out["removed"] == "PartNumber;"
    assert out["fault"] == 'removing the parameter "Rule" raised an error'
    # The kept names ride beside the fault.
    assert '"kept_system_parameters":' in out["reply"] and '"partial":true' in out["reply"]


def test_an_unreadable_read_only_state_keeps_a_system_parameter(tmp_path):
    out = _delete(tmp_path, "Name=Rule",
                  "  TPar(Doc.Params[4]).RaiseOnReadOnly := True;")
    assert out["matched"] == "0" and out["calls"] == "0"
    assert out["kept"] == "Rule"


def test_a_kept_parameter_whose_name_cannot_be_read_is_still_named(tmp_path):
    out = _delete(tmp_path, "",
                  "  TPar(Doc.Params[0]).RaiseOnName := True;")
    assert out["kept"].startswith("(unnamed), ")


def test_a_name_kept_on_several_sheets_is_named_once(tmp_path):
    out = _run(tmp_path, [
        "  Doc := NewSheet(0);",
        "  RemoveMatchingSheetParams(Doc, 'Name=Title', Total);",
        "  Doc := NewSheet(0);",
        "  RemoveMatchingSheetParams(Doc, 'Name=Title', Total);",
        "  Report(Doc, Total);",
    ])
    assert out["kept"] == "Title"
    assert '"kept_system_parameters":["Title"]' in out["reply"]


# -- the tools say so ----------------------------------------------------------

class _Bridge:
    def __init__(self, reply):
        self.sent = []
        self.reply = reply

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return self.reply


def _tools(monkeypatch, reply):
    bridge = _Bridge(reply)
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return captured, bridge


def test_obj_delete_hands_back_the_kept_names(monkeypatch):
    reply = {"matched": 0, "saved": False, "modified": False,
             "kept_system_parameters": ["Engineer"], "success": False,
             "reason": "the filter matched sheet system parameters"}
    tools, bridge = _tools(monkeypatch, reply)
    got = asyncio.run(tools["obj_delete"](object_type="eParameter",
                                          filter="Name=Engineer"))
    assert bridge.sent[0][0] == "generic.delete_objects"
    assert got["kept_system_parameters"] == ["Engineer"]
    assert got["success"] is False


def test_the_delete_tools_document_the_kept_parameters(monkeypatch):
    tools, _ = _tools(monkeypatch, {})
    assert "kept_system_parameters" in tools["obj_delete"].__doc__
    assert "kept system parameters" in tools["obj_batch_delete"].__doc__
