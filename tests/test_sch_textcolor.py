# SPDX-License-Identifier: Apache-2.0
"""AD21 TextColor crash: exercise the real guards, not a permissive mock API."""
from __future__ import annotations

import re
import os
import shutil
import subprocess

import pytest

from tests.test_sch_properties_match_their_interface import _routine, _source


def _assert_textcolor_access(code):
    capability = _routine(code, "SchObjectHasTextColor")
    assert "Result := False" in capability
    assert "If Obj = Nil Then Exit" in capability
    assert set(re.findall(r"Obj\.ObjectId = (e\w+)", capability)) == {
        "ePort", "eSheetEntry", "eHarnessEntry"}
    # A typed local must be declared and assigned AFTER the capability check.
    for routine in ("GetSchProperty", "SetSchProperty"):
        body = _routine(code, routine)
        branch = body.split("Else If PropName = 'TextColor' Then", 1)[1]
        branch = re.split(r"\bElse If PropName\b", branch, maxsplit=1)[0]
        guard = branch.index("If SchObjectHasTextColor(Obj) Then")
        assert not re.search(r"\bObj\.TextColor\b", branch)
        for local, interface in (("PortObj", "ISch_Port"),
                                 ("EntryObj", "ISch_SheetEntry"),
                                 ("HarnessEntryObj", "ISch_HarnessEntry")):
            assert f"{local} : {interface}" in body
            assert guard < branch.index(f"{local} := Obj") < branch.index(f"{local}.TextColor")
        if routine == "GetSchProperty":
            assert "NotePropertyDiag('unreadable', PropName)" in branch
        else:
            assert "Matched := False" in branch


def test_textcolor_uses_only_documented_typed_interfaces():
    _assert_textcolor_access(_source())


@pytest.mark.parametrize("mutation", ["read_guard", "write_guard", "allow_power", "raw_read"])
def test_textcolor_guard_detects_the_original_defect(mutation):
    code = _source()
    if mutation == "allow_power":
        code = code.replace("(Obj.ObjectId = eHarnessEntry);",
                            "(Obj.ObjectId = eHarnessEntry) Or (Obj.ObjectId = ePowerObject);", 1)
    elif mutation == "raw_read":
        code = code.replace("IntToStr(PortObj.TextColor)", "IntToStr(Obj.TextColor)", 1)
    else:
        routine = "GetSchProperty" if mutation == "read_guard" else "SetSchProperty"
        start = code.index(f"Function {routine}(")
        code = code[:start] + code[start:].replace(
            "If SchObjectHasTextColor(Obj) Then", "If True Then", 1)
    with pytest.raises((AssertionError, ValueError)):
        _assert_textcolor_access(code)


def _assert_preflight(code):
    preflight = _routine(code, "UnsupportedSchProperty")
    assert "(PropName = 'TextColor') And (Not SchObjectHasTextColor(Obj))" in preflight
    apply = _routine(code, "ApplySetProperties")
    check = apply.index("UnsupportedSchProperty(Obj, SetStr)")
    assert check < apply.index("Loc := Obj.Location")
    assert check < apply.index("SetSchProperty(Obj, PropName, PropValue)")
    rejection = apply[check:apply.index("HasX := False")]
    assert "NotePropertyDiag('unknown', UnsupportedProp)" in rejection
    assert "Exit;" in rejection
    filt = _routine(code, "MatchesFilter")
    check = filt.index("UnsupportedSchProperty(Obj, Condition)")
    read = filt.index("Actual := GetSchProperty")
    assert check < read
    rejection = filt[check:read]
    assert "Result := False" in rejection and "Exit;" in rejection


def test_mixed_sets_and_empty_filters_are_preflighted():
    _assert_preflight(_source())


@pytest.mark.parametrize("call", ["UnsupportedSchProperty(Obj, SetStr)",
                                   "UnsupportedSchProperty(Obj, Condition)"])
def test_preflight_regression_detects_removed_checks(call):
    with pytest.raises((AssertionError, ValueError)):
        _assert_preflight(_source().replace(call, "''"))


def test_project_queries_include_property_diagnostics():
    body = _routine(_source(), "IterateProjectDocs")
    query = body.split("If Mode = 'query' Then", 1)[1].split("Else", 1)[0]
    assert "RenderPropertyDiagJson(0)" in query


def test_production_textcolor_filters_do_not_read_missing_members(tmp_path):
    """Compile production guards/filter with a getter that poisons bad reads."""
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    names = ("SchObjectHasText", "SchObjectHasIsHidden", "SchObjectHasTextColor",
             "UnsupportedSchProperty")
    source = _source()
    guards = "\n".join(_routine(source, name) for name in names)
    types = sorted(set(re.findall(r"\be[A-Z]\w*", guards)) | {"ePowerObject", "eNetLabel"})
    constants = "\n".join(f"{kind}={i};" for i, kind in enumerate(types))
    program = """program textcolor_filters;
{$mode delphi}
uses SysUtils;
const @@CONSTANTS@@
type ISch_GraphicalObject = class ObjectId: Integer; end;
var Reads, Diagnostics: Integer;
@@GUARDS@@
procedure NotePropertyDiag(Kind, Prop: String);
begin Inc(Diagnostics); end;
function GetSchProperty(Obj: ISch_GraphicalObject; Prop: String): String;
begin
  if (Prop='TextColor') and not SchObjectHasTextColor(Obj) then Halt(90);
  Inc(Reads);
  if Prop='TextColor' then Result:='128' else Result:='GND';
end;
@@FILTER@@
var Obj: ISch_GraphicalObject;
begin
  if SchObjectHasTextColor(nil) then Halt(1);
  Obj:=ISch_GraphicalObject.Create;
  Obj.ObjectId:=ePowerObject;
  if MatchesFilter(Obj, 'TextColor=') then Halt(2);
  if MatchesFilter(Obj, 'TextColor=128') then Halt(3);
  if (Reads<>0) or (Diagnostics<>2) then Halt(4);
  if UnsupportedSchProperty(Obj, 'Color=123|Location.X=100|TextColor=128')<>'TextColor' then Halt(5);
  if not MatchesFilter(Obj, 'Text=GND') then Halt(6);
  Obj.ObjectId:=ePort;
  if not MatchesFilter(Obj, 'TextColor=128') then Halt(7);
  Obj.ObjectId:=eSheetEntry;
  if not MatchesFilter(Obj, 'TextColor=128') then Halt(8);
  Obj.ObjectId:=eHarnessEntry;
  if not MatchesFilter(Obj, 'TextColor=128') then Halt(9);
  if Reads<>4 then Halt(10);
  Obj.Free;
end.
""".replace("@@CONSTANTS@@", constants).replace("@@GUARDS@@", guards).replace(
        "@@FILTER@@", _routine(source, "MatchesFilter"))
    path = tmp_path / "textcolor_filters.pas"
    path.write_text(program, encoding="utf-8")
    compiled = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executable = path.with_suffix(".exe" if os.name == "nt" else "")
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
