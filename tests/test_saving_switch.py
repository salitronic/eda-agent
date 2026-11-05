# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Saving switch on the bridge dashboard stops every save the bridge makes.

Off, the commands whose job is writing a file are refused, a command that
saves on the side runs but writes nothing and says so, and the Python side
refuses its own writes to design files and the interface routes to File >
Save, a Save button and Ctrl+S. The switch is kept in ``saving.json`` in the
workspace, so a restart keeps it; no file means saving is allowed.

The Pascal routines are copied out of the shipped units by name and run
under Free Pascal, so what is tested is what Altium runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import prjpcb, saving
from tests.pascal_source import functions, strip_comments

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
SOURCES = [p for p in sorted(SCRIPTS.glob("*.pas")) if p.name != "Altium_MCP.pas"]


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _code(unit: str) -> str:
    return (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")


# Stand-ins for the helpers the routines call. ExtractJsonValue reads a flat
# string value, which is all these requests carry.
_STUBS = """
const SAVING_FILE = 'saving.json';
var SavingOff : Boolean; SavesHeld : Integer; HeldFiles, WorkspaceDir : String;

function ExtractJsonValue(Json, Key : String) : String;
var P, Q : Integer;
begin
  Result := '';
  P := Pos('"' + Key + '":"', Json);
  if P = 0 then Exit;
  P := P + Length(Key) + 4;
  Q := P;
  while (Q <= Length(Json)) and (Json[Q] <> '"') do Inc(Q);
  Result := Copy(Json, P, Q - P);
end;

function EscapeJsonString(S : String) : String;
begin Result := S; end;

function BuildErrorResponse(RequestId, ErrorCode, ErrorMsg : String) : String;
begin
  Result := '{"id":"' + RequestId + '","success":false,"error":{"code":"'
    + ErrorCode + '","message":"' + ErrorMsg + '"}}';
end;

function ReadFileContent(FilePath : String) : String;
var L : TStringList;
begin
  L := TStringList.Create;
  try L.LoadFromFile(FilePath); Result := L.Text; finally L.Free; end;
end;

procedure WriteFileContent(FilePath : String; Content : String);
var L : TStringList;
begin
  L := TStringList.Create;
  try L.Text := Content; L.SaveToFile(FilePath); finally L.Free; end;
end;
"""


def _run(tmp_path: Path, body: list[str]) -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    main, form = _code("Main.pas"), _code("StatusForm.pas")
    routines = [_routine(main, n) for n in
                ("SaveHeldBack", "SavingOffError", "LoadSavingSwitch", "StoreSavingSwitch")]
    routines += [_routine(form, n) for n in ("SavingRefusal", "SavingNote")]
    program = "\n".join([
        "program saving;", "{$mode delphi}{$H+}", "uses SysUtils, Classes;",
        _STUBS, *routines,
        "begin",
        f"  WorkspaceDir := '{tmp_path}' + PathDelim;",
        *body,
        "end.",
    ])
    path = tmp_path / "saving.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("saving.exe" if os.name == "nt" else "saving")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


def _p(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


REFUSED = [
    ("application.save_all", "{}"),
    ("project.save_all", "{}"),
    ("application.save_document", '{"file_path":"C:\\\\a.SchDoc"}'),
    ("project.save", "{}"),
    ("application.create_document", "{}"),
    ("generic.add_sheet", "{}"),
    ("pcb.make_library", "{}"),
    ("pcb.make_pad_via_library", "{}"),
    ("pcb.save_copy_as", '{"target_path":"C:\\\\b.PcbDoc"}'),
    ("generic.simcfg_document", '{"action":"save"}'),
    ("project.close", "{}"),
    ("project.close", '{"save":"true"}'),
    ("application.execute_menu", '{"menu_path":"File|Save All"}'),
    ("application.run_process", '{"process_name":"WorkspaceManager:SaveAll"}'),
    ("generic.run_process", '{"process":"WorkspaceManager:SaveObject"}'),
]

ALLOWED = [
    ("pcb.save_copy_as", '{"check_only":"true"}'),
    ("generic.simcfg_document", '{"action":"reload"}'),
    ("project.close", '{"save":"false"}'),
    ("application.execute_menu", '{"menu_path":"View|Zoom Fit"}'),
    ("generic.run_process", '{"process":"PCB:Zoom"}'),
    ("generic.modify_objects", "{}"),
    ("application.ping", "{}"),
]


def test_off_refuses_what_writes_and_lets_the_rest_run(tmp_path):
    body = ["  SavingOff := True;"]
    for cmd, params in REFUSED + ALLOWED:
        body.append(f"  WriteLn(SavingRefusal({_p(cmd)}, {_p(params)}, 'r1'));")
    out = _run(tmp_path, body)
    assert len(out) == len(REFUSED) + len(ALLOWED)
    for (cmd, params), line in zip(REFUSED, out):
        assert '"code":"SAVING_OFF"' in line, (cmd, params, line)
        assert "nothing was written" in line
    for (cmd, params), line in zip(ALLOWED, out[len(REFUSED):]):
        assert line == "", (cmd, params, line)


def test_on_refuses_nothing(tmp_path):
    body = ["  SavingOff := False;"]
    body += [f"  WriteLn('[' + SavingRefusal({_p(c)}, {_p(p)}, 'r1') + ']');"
             for c, p in REFUSED]
    assert set(_run(tmp_path, body)) == {"[]"}


def test_a_save_held_back_is_named_once_in_the_reply(tmp_path):
    ok = '{"id":"r1","success":true,"data":{"moved":3}}'
    err = '{"id":"r1","success":false,"error":{"code":"X"}}'
    out = _run(tmp_path, [
        "  SavingOff := False; SavesHeld := 0; HeldFiles := '';",
        "  WriteLn(SaveHeldBack('C:\\p\\A.SchDoc'));",
        "  WriteLn(SavingNote(" + _p(ok) + "));",
        "  SavingOff := True;",
        "  WriteLn(SaveHeldBack('C:\\p\\A.SchDoc'));",
        "  WriteLn(SaveHeldBack('C:\\p\\A.SchDoc'));",
        "  WriteLn(SaveHeldBack('C:\\p\\Board.PcbDoc'));",
        "  WriteLn(SavesHeld, ' ', HeldFiles);",
        "  WriteLn(SavingNote(" + _p(ok) + "));",
        "  WriteLn(SavingNote(" + _p(err) + "));",
        "  WriteLn(SavingNote('{\"id\":\"r1\",\"success\":true,\"data\":{}}'));",
    ])
    assert out[0] == "FALSE" and out[1] == ok          # on: nothing held, reply as it was
    assert out[2:5] == ["TRUE", "TRUE", "TRUE"]
    assert out[5] == "3 A.SchDoc|Board.PcbDoc"
    reply = json.loads(out[6])
    assert reply["data"]["saving_off"] is True
    assert reply["data"]["not_saved"] == ["A.SchDoc", "Board.PcbDoc"]
    assert reply["data"]["moved"] == 3
    assert out[7] == err                               # a failure is left alone
    assert json.loads(out[8])["data"]["not_saved"] == ["A.SchDoc", "Board.PcbDoc"]


def test_the_switch_is_kept_across_a_restart_and_defaults_to_allowed(tmp_path):
    out = _run(tmp_path, [
        "  SavingOff := True; LoadSavingSwitch(0); WriteLn(SavingOff);",
        "  SavingOff := True; StoreSavingSwitch(0);",
        "  SavingOff := False; LoadSavingSwitch(0); WriteLn(SavingOff);",
        "  SavingOff := False; StoreSavingSwitch(0);",
        "  SavingOff := True; LoadSavingSwitch(0); WriteLn(SavingOff);",
    ])
    assert out == ["FALSE", "TRUE", "FALSE"]
    # The file the script writes is the one the Python side reads.
    assert saving.saving_off(tmp_path) is False


def _dispatch_names() -> dict[str, str]:
    """{handler function: 'category.action'} from every unit's dispatch table."""
    categories = {"Application.pas": "application", "Project.pas": "project",
                  "Library.pas": "library", "Generic.pas": "generic",
                  "PCB.pas": "pcb", "Audit.pas": "audit"}
    found: dict[str, str] = {}
    for unit, cat in categories.items():
        for action, fn in re.findall(r"'(\w+)':\s*Result := (\w+)\(", _code(unit)):
            found.setdefault(fn, f"{cat}.{action}")
    return found


def test_every_save_in_the_script_asks_the_switch_first():
    """A routine that writes a document either asks SaveHeldBack itself or is
    the handler of a command SavingRefusal turns away. A new save added
    without either fails here, by name."""
    refusal = functions(strip_comments(_code("StatusForm.pas")))["SavingRefusal"]
    refused = set(re.findall(r"Command = '([\w.]+)'", refusal))
    handlers = _dispatch_names()
    unguarded = []
    seen = 0
    for path in SOURCES:
        for name, body in functions(strip_comments(path.read_text(
                encoding="utf-8", errors="replace"))).items():
            if not re.search(r"\.DoFileSave\(|RunProcess\('WorkspaceManager:Save", body):
                continue
            seen += 1
            if "SaveHeldBack(" in body or handlers.get(name) in refused:
                continue
            unguarded.append(f"{path.name}:{name} ({handlers.get(name, 'no command')})")
    assert seen >= 12, f"found only {seen} saving routines; the scan has gone blind"
    assert not unguarded, "saves that ignore the Saving switch: " + ", ".join(unguarded)


def test_the_dashboard_button_is_wired_and_painted_at_start():
    dfm = (SCRIPTS / "StatusForm.dfm").read_text(encoding="ascii")
    form = functions(strip_comments(_code("StatusForm.pas")))
    for handler in ("btn_SavingClick", "btn_SavingEnter", "btn_SavingLeave"):
        assert f"= {handler}" in dfm
        assert handler in form
    assert "StoreSavingSwitch(0)" in form["btn_SavingClick"]
    assert "PaintSavingSwitch(0)" in form["ShowStatusForm"]
    assert "LoadSavingSwitch(0)" in form["StartMCPPump"]
    # Loaded before the form is shown, so the first paint is the stored state.
    pump = form["StartMCPPump"]
    assert pump.index("LoadSavingSwitch(0)") < pump.index("ShowStatusForm(0)")


# ---- the Python side -------------------------------------------------------

def test_python_reads_the_same_file(tmp_path):
    assert saving.saving_off(tmp_path) is False                     # no file
    (tmp_path / "saving.json").write_text('{"saving":"off"}')
    assert saving.saving_off(tmp_path) is True
    (tmp_path / "saving.json").write_text('{"saving":"on"}')
    assert saving.saving_off(tmp_path) is False
    (tmp_path / "saving.json").write_text("not json")
    assert saving.saving_off(tmp_path) is False


@pytest.mark.parametrize("path, saves", [
    ("File|Save", True), ("File|Save &All", True), ("File|Save As...", True),
    ("File|Save Copy As...", True), ("Design|Rules", False), ("View|Fit All", False),
])
def test_menu_paths_that_save(path, saves):
    assert saving.menu_saves(path) is saves


@pytest.mark.parametrize("caption, saves", [
    ("Save", True), ("&Save", True), ("Save All", True),
    ("Don't Save", False), ("Don\u2019t Save", False), ("Discard", False), ("OK", False),
])
def test_buttons_that_save(caption, saves):
    assert saving.button_saves(caption) is saves


@pytest.mark.parametrize("keys, saves", [
    ("ctrl+s", True), ("escape ctrl+shift+s", True), ("CTRL+S", True),
    ("s", False), ("ctrl+a", False), ("alt+s", False),
])
def test_keys_that_save(keys, saves):
    assert saving.keys_save(keys) is saves


def test_the_project_file_is_not_edited_while_saving_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(saving, "saving_off", lambda workspace=None: True)
    called = []
    monkeypatch.setattr(prjpcb, "prune_stale_documents",
                        lambda *a, **k: called.append(a) or {"pruned": 1})
    reply = {"removed": True, "project_path": str(tmp_path / "x.PrjPcb"), "members": []}
    out = prjpcb.finish_removal(reply, str(tmp_path))
    assert called == []
    assert out["stale_sections"]["pruned"] == 0
    assert "switched off" in out["stale_sections"]["reason"]


def test_every_python_write_path_asks_the_switch():
    src = Path(__file__).resolve().parents[1] / "src" / "eda_agent"
    ui = (src / "tools" / "uiauto.py").read_text(encoding="utf-8")
    for tool, check in (("app_click_menu", "menu_saves(menu_path)"),
                        ("app_run_ui_command", "menu_saves(menu_path)"),
                        ("app_press_dialog_button", "button_saves(button_caption)"),
                        ("app_send_keys", "keys_save(sequence)")):
        body = ui.split(f"async def {tool}(", 1)[1].split("\n    async def ", 1)[0]
        assert check in body and "saving_off()" in body, tool
    app = (src / "tools" / "application.py").read_text(encoding="utf-8")
    detach = app.split("async def app_detach(", 1)[1].split("@mcp.tool()", 1)[0]
    assert '"not_saved"' in detach          # a stop that held edits back says so
    restore = app.split("async def app_restore_checkpoint(", 1)[1].split("@mcp.tool()", 1)[0]
    assert restore.index("saving_off()") < restore.index("store.restore(")
    sim = (src / "tools" / "sim.py").read_text(encoding="utf-8")
    change = sim.split("async def _change_simcfg(", 1)[1].split("\nasync def ", 1)[0]
    assert change.index("saving_off()") < change.index("write_simcfg(")
