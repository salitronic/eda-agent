# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The bridge keeps its own record of unsaved documents, by full path (#426).

Altium's Modified flag misses edits made through the API, and on a PcbLib
it reads False while the editor's own tab shows the library edited
(measured on AD 25.8.1 after set_footprint_description and
set_pad_properties). The bridge's file-name list (EditedDocs) cannot tell
two open files of one name apart, two projects each holding a PCB.PcbDoc,
and nothing reported it. And a script restart lost it.

So: one entry per file, keyed by its full path; marked by the dispatcher
for every command that edits (the handler's own note, else the document
the command acted on); unsaved while the file on disk is no newer than at
the first edit, so any save clears it; journaled in the workspace and
replayed at start, where a file no longer open is reported lost.

The record's routines are compiled with Free Pascal, as Altium runs them,
against real files; EdIsOpen (which asks Altium) is a stand-in.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import safety
from eda_agent.tools import metadata

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(unit: str) -> str:
    return (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")


def _routine(unit: str, name: str) -> str:
    code = _code(unit)
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _pas(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Which commands mark a document
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "application.save_document", "application.save_all", "project.save",
    "application.close_document", "project.close", "generic.select_objects",
    "generic.deselect_all", "project.compile", "project.add_document",
    "pcb.measure_clearance", "pcb.pours_at", "generic.zoom_to_xy",
    "application.acknowledge_lost", "application.get_edited_documents",
])
def test_saves_closes_and_reads_leave_documents(command):
    assert safety.command_leaves_documents(command)


@pytest.mark.parametrize("command", [
    "pcb.place_tracks", "pcb.copy_tracks_radial", "pcb.teardrops",
    "generic.add_sheet", "generic.copy_objects", "library.split_pin_functions",
    "project.set_parameter", "application.run_process",
    "pcb.load_stack_manager_model",
])
def test_edits_do_not_leave_documents(command):
    assert not safety.command_leaves_documents(command)


def test_an_action_naming_a_read_leaves_documents():
    assert safety.command_leaves_documents("pcb.embedded_data", {"action": "get"})
    assert not safety.command_leaves_documents("pcb.embedded_data", {"action": "write"})


@pytest.mark.parametrize("command,params", [
    ("generic.run_process", {"process": "WorkspaceManager:OpenObject",
                             "params": "ObjectKind=Document|FileName=C:\\a.PcbDoc"}),
    ("generic.run_process", {"process": "WorkspaceManager:SaveObject"}),
    ("generic.run_process", {"process": "Sch:Zoom", "params": "Action=All"}),
    ("generic.run_process", {"process": "PCB:SelectNext"}),
    ("generic.run_process", {"process": "PCB:GotoLibraryComponent"}),
    ("application.run_process", {"process_name": "WorkspaceManager:CloseObject"}),
])
def test_processes_that_open_save_or_move_the_view_leave_documents(command, params):
    # Seen live: OpenObject marked the document that was in front.
    assert safety.command_leaves_documents(command, params)


@pytest.mark.parametrize("command,params", [
    ("generic.run_process", {"process": "PCB:Clear"}),
    ("generic.run_process", {"process": "PCB:CustomPadShape", "params": "Action=Convert"}),
    ("generic.run_process", {"process": "Sch:ManageUnions"}),
    ("generic.run_process", {}),
    ("pcb.place_tracks", {"process": "WorkspaceManager:OpenObject"}),
])
def test_other_processes_still_mark_the_front_document(command, params):
    assert not safety.command_leaves_documents(command, params)


def test_the_generated_pascal_rule_reads_the_process(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    stubs = r"""
function ExtractJsonValue(Json : String; Key : String) : String;
var P : Integer;
begin
  Result := '';
  P := Pos('"' + Key + '":"', Json);
  if P = 0 then Exit;
  Result := Copy(Json, P + Length(Key) + 4, Length(Json));
  Result := Copy(Result, 1, Pos('"', Result) - 1);
end;
function ActionHasPrefix(Verb : String; Prefix : String) : Boolean;
begin
  Result := Copy(Verb, 1, Length(Prefix)) = Prefix;
end;
"""
    cases = [
        ("generic.run_process", '{"process":"WorkspaceManager:OpenObject"}', True),
        ("generic.run_process", '{"process":"Sch:Zoom","params":"Action=All"}', True),
        ("application.run_process", '{"process_name":"WorkspaceManager:SaveObject"}', True),
        ("generic.run_process", '{"process":"PCB:Clear"}', False),
        ("generic.run_process", '{}', False),
        ("pcb.place_tracks", '{"process":"WorkspaceManager:OpenObject"}', False),
        ("pcb.embedded_data", '{"action":"get"}', True),
    ]
    body = [f"  WriteLn(CommandLeavesDocuments({_pas(c)}, {_pas(p)}));" for c, p, _ in cases]
    program = "\n".join(["program rules;", "{$mode delphi}{$H+}", "uses SysUtils;", stubs,
                         safety.pascal_command_rules(), "begin", *body, "end."])
    src = tmp_path / "rules.pas"
    src.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-v0", f"-FE{tmp_path}", str(src)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(tmp_path / "rules.exe")], capture_output=True, text=True,
                         timeout=60)
    assert ran.stdout.split() == [str(want).upper() for _, _, want in cases]


def test_acknowledging_lost_entries_claims_no_document():
    assert safety.command_only_looks("application.acknowledge_lost")


def test_the_pascal_rules_carry_the_new_one():
    rules = safety.pascal_command_rules()
    assert "Function CommandLeavesDocuments(Command : String; Params : String) : Boolean;" in rules
    assert "ActionHasPrefix(Verb, 'save')" in rules
    assert "(Verb = 'add_document')" in rules and "(Op = 'get')" in rules
    status = _code("StatusForm.pas").replace("\r\n", "\n")
    start = status.index(safety.PASCAL_RULES_BEGIN)
    end = status.index(safety.PASCAL_RULES_END, start) + len(safety.PASCAL_RULES_END)
    assert status[start:end] == rules


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_a_handlers_note_goes_to_the_full_path_record():
    body = _routine("Main.pas", "NoteDocEdited")
    assert "EdNoteSerial := EdNoteSerial + 1;" in body
    assert "EdPending := EdPending + Chr(1) + FilePath + Chr(2) + IntToStr(Age) + Chr(3);" in body
    assert body.index("FileAge(FilePath)") < body.index("EdPending := EdPending +")


def test_the_dispatcher_marks_after_the_handler_and_before_the_reply():
    body = _routine("StatusForm.pas", "ProcessSingleRequest")
    before = body.index("EdSerialBefore := EdNoteSerial;")
    target = body.index("EdTarget := CommandTarget(Params, EdFront);")
    handler = body.index("ResponseContent := ProcessCommand(Command, Params, RequestId);")
    mark = body.index("EdAfterCommand(Command, Params, ResponseContent, ResultTag, RequestId,")
    reply = body.index("WriteResponseFile(RequestId, ResponseContent);")
    assert before < handler and target < handler < mark < reply
    assert body.index("ResultTag := 'EXCEPTION';") < mark


def test_the_record_is_rebuilt_from_its_journal_at_start():
    body = _routine("StatusForm.pas", "StartMCPPump")
    assert "EdUnwind(0)" in body
    assert body.index("LoadPreferences(0);") < body.index("EdUnwind(0)")


def test_a_command_that_edits_marks_its_document_even_unnoted():
    body = _routine("StatusForm.pas", "EdAfterCommand")
    assert "If EdNoteSerial <> SerialBefore Then Exit;" in body
    assert "If CommandOnlyLooks(Command) Or CommandLeavesDocuments(Command, Params) Then Exit;" in body
    assert "(Tag = 'EXCEPTION') Or (Pos('\"success\":true', Copy(Reply, 1, 300)) > 0)" in body
    assert "Marked := EdMarkResolved(Target, TargetAge, Command, Params, Front, Session);" in body
    assert "Marked := EdMarkResolved(Front, FrontAge, Command, Params, Front, Session);" in body


def test_saves_and_closes_clear_the_record():
    save = _routine("Application.pas", "App_SaveDocument")
    assert "If Saved Then EdForget(FilePath, 'S');" in save
    close = _routine("Application.pas", "App_CloseDocument")
    assert "If Closed Then EdForget(FilePath, 'C');" in close
    sweep = _routine("Main.pas", "SaveOneDocByDocRef")
    assert "EdForget(Path, 'S');" in sweep


def test_document_replies_say_edited_and_unsaved():
    listed = _routine("Application.pas", "App_GetOpenDocuments")
    assert "EdRefresh(0);" in listed
    assert "EdDocFields(FullPath, ModifiedStr = 'true')" in listed
    active = _routine("Application.pas", "App_GetActiveDocument")
    assert "EdRefresh(0);" in active
    assert active.count("EdDocFields(FileName, DocIsModified(FileName))") == 4
    fields = _routine("Application.pas", "EdDocFields")
    for name in ("'edited'", "'unsaved'", "'last_edit'", "'last_command'"):
        assert name in fields


def test_the_new_commands_are_dispatched():
    table = _routine("Application.pas", "HandleApplicationCommand")
    assert "'get_edited_documents': Result := App_GetEditedDocuments(Params, RequestId);" in table
    assert "'acknowledge_lost':    Result := App_AcknowledgeLost(Params, RequestId);" in table


def test_a_bare_name_is_never_resolved_by_first_match():
    body = _routine("StatusForm.pas", "EdResolve")
    assert "ResolveLoadedDocPath" not in body
    assert "OpenProjectAt(Workspace, ProjPath)" in body
    assert "EdProjectsOf(Front)" in body
    assert "DM_FreeDocumentsProject" in body


# ---------------------------------------------------------------------------
# The record itself, compiled
# ---------------------------------------------------------------------------

_MAIN = ["EdKey", "EdPart", "EdFieldCount", "EdClean", "EdBody", "EdBodyOf",
         "EdWithout", "EdWith", "EdKeyAt", "EdAgeOf", "EdAgeNow", "EdStamp",
         "EdLooksFull", "EdJournalLine", "EdReplay", "EdJournalPath",
         "EdJournal", "EdMark", "EdKeyFor", "EdDropPending", "EdForget"]

# EdIsOpen asks Altium; here a list of open keys stands in for it.
_OPEN_STUB = r"""
var OpenList : String;
function EdIsOpen(Path : String) : Boolean;
begin
  Result := Pos('|' + EdKey(Path) + '|', OpenList) > 0;
end;
"""


def _run(tmp_path: Path, body: list[str], extra_main: tuple[str, ...] = (),
         status: tuple[str, ...] = (), app: tuple[str, ...] = (),
         stubs: str = "") -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    routines = [_routine("Main.pas", n) for n in _MAIN]
    after = [_routine("Main.pas", n) for n in ("EdRefresh",) + extra_main]
    after += [_routine("StatusForm.pas", n) for n in status]
    after += [stubs] + [_routine("Application.pas", n) for n in app]
    source = "\n\n".join(routines) + "\n" + _OPEN_STUB + "\n" + "\n\n".join(after)
    # DelphiScript takes -1 for "replace all"; Free Pascal wants the set.
    source = re.sub(r"(StringReplace\([^;]*?),\s*-1\)", r"\1, [rfReplaceAll])", source)
    program = "\n".join([
        "program edited;", "{$mode delphi}{$H+}", "uses SysUtils, Classes;",
        "var WorkspaceDir, EdRecord, EdPending : String;",
        "    EdNoteSerial, EdSeq, EdJournalFailed : Integer;",
        "    EdQuiet : Boolean;",
        source,
        "function JournalText : String;",
        "var SL : TStringList;",
        "begin SL := TStringList.Create; try if FileExists(EdJournalPath(0)) then",
        "  SL.LoadFromFile(EdJournalPath(0)); Result := SL.Text; finally SL.Free; end; end;",
        "begin",
        f"  WorkspaceDir := {_pas(str(tmp_path) + os.sep)};",
        "  EdRecord := ''; EdPending := ''; OpenList := '';",
        "  EdSeq := 0; EdJournalFailed := 0; EdQuiet := False;",
        *body, "end.",
    ])
    src = tmp_path / "edited.pas"
    src.write_text(program, encoding="utf-8")
    exe = tmp_path / "edited.exe"
    built = subprocess.run([fpc, "-v0", f"-FE{tmp_path}", str(src)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


def _file(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return p


def test_two_files_of_one_name_are_two_entries(tmp_path):
    a = _file(tmp_path, "A/PCB.PcbDoc")
    b = _file(tmp_path, "B/PCB.PcbDoc")
    out = _run(tmp_path, [
        f"  EdMark({_pas(str(a))}, 'C:\\A\\A.PrjPcb', FileAge({_pas(str(a))}), 'pcb.place_tracks', 's1', '');",
        f"  EdMark({_pas(str(b))}, 'C:\\B\\B.PrjPcb', FileAge({_pas(str(b))}), 'pcb.place_tracks', 's2', '');",
        "  WriteLn('A=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(a)) + ")), Chr(2), 1));",
        "  WriteLn('B=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(b)) + ")), Chr(2), 1));",
        "  WriteLn('BARE=[', EdKeyFor('PCB.PcbDoc'), ']');",
        "  WriteLn('LINES=', EdSeq);",
    ])
    assert "A=C:\\A\\A.PrjPcb" in out and "B=C:\\B\\B.PrjPcb" in out
    # A bare name matching two entries picks neither.
    assert "BARE=[]" in out
    assert "LINES=2" in out


def test_a_bare_name_with_one_entry_finds_it(tmp_path):
    a = _file(tmp_path, "A/Only.SchLib")
    out = _run(tmp_path, [
        f"  EdMark({_pas(str(a))}, '', FileAge({_pas(str(a))}), 'library.add_pins', 's', '');",
        "  WriteLn('BARE=', EdKeyFor('only.schlib'));",
    ])
    assert f"BARE={str(a).upper()}" in out


def test_only_the_first_edit_is_journaled_and_a_save_starts_again(tmp_path):
    a = _file(tmp_path, "Lib.PcbLib")
    out = _run(tmp_path, [
        f"  EdMark({_pas(str(a))}, '', 100, 'one', 's', '');",
        f"  EdMark({_pas(str(a))}, '', 100, 'two', 's', '');",
        "  WriteLn('AFTER2=', EdSeq, ' ', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(a)) + ")), Chr(2), 4));",
        # The file was saved (age 200) before the third edit.
        f"  EdMark({_pas(str(a))}, '', 200, 'three', 's', '');",
        "  WriteLn('AFTER3=', EdSeq, ' ', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(a)) + ")), Chr(2), 3));",
        "  Write(JournalText);",
    ])
    assert "AFTER2=1 two" in out
    assert "AFTER3=3 200" in out
    ops = [line.split("\t")[2] for line in out if "\t" in line]
    assert ops == ["E", "S", "E"]


def test_forget_journals_saved_and_closed_and_leaves_lost_alone(tmp_path):
    a = _file(tmp_path, "A.SchDoc")
    b = _file(tmp_path, "B.SchDoc")
    out = _run(tmp_path, [
        f"  EdMark({_pas(str(a))}, '', 1, 'x', 's', '');",
        f"  EdMark({_pas(str(b))}, '', 1, 'x', 's', '');",
        f"  EdForget({_pas(str(a))}, 'S');",
        f"  EdRecord := EdWith(EdRecord, EdKey({_pas(str(b))}), EdBody({_pas(str(b))}, '', 't', '1', 'x', 's', 'L', 'gone'));",
        f"  EdForget({_pas(str(b))}, 'C');",
        "  WriteLn('A=[', EdBodyOf(EdRecord, EdKey(" + _pas(str(a)) + ")), ']');",
        "  WriteLn('B=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(b)) + ")), Chr(2), 6));",
        "  Write(JournalText);",
    ])
    assert "A=[]" in out and "B=L" in out
    ops = [line.split("\t")[2] for line in out if "\t" in line]
    assert ops == ["E", "E", "S"]


def test_refresh_clears_a_saved_file_and_reports_a_closed_one_lost(tmp_path):
    saved = _file(tmp_path, "Saved.PcbLib")
    closed = _file(tmp_path, "Closed.PcbLib")
    held = _file(tmp_path, "Held.PcbLib")
    out = _run(tmp_path, [
        f"  OpenList := '|' + EdKey({_pas(str(saved))}) + '|' + EdKey({_pas(str(held))}) + '|';",
        # Saved: its age at the edit is older than the file now.
        f"  EdMark({_pas(str(saved))}, '', FileAge({_pas(str(saved))}) - 10, 'x', 's', '');",
        f"  EdMark({_pas(str(closed))}, '', FileAge({_pas(str(closed))}), 'x', 's', '');",
        f"  EdMark({_pas(str(held))}, '', FileAge({_pas(str(held))}), 'x', 's', '');",
        "  EdRefresh(0);",
        "  WriteLn('SAVED=[', EdBodyOf(EdRecord, EdKey(" + _pas(str(saved)) + ")), ']');",
        "  WriteLn('CLOSED=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(closed)) + ")), Chr(2), 6),"
        " '/', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(closed)) + ")), Chr(2), 7));",
        "  WriteLn('HELD=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(held)) + ")), Chr(2), 6));",
        "  Write(JournalText);",
    ])
    assert "SAVED=[]" in out
    assert "CLOSED=L/closed without saving" in out
    assert "HELD=E" in out
    ops = [line.split("\t")[2] for line in out if "\t" in line]
    assert ops == ["E", "E", "E", "S", "L"]


def test_a_brand_new_file_stays_unsaved_until_it_exists(tmp_path):
    new = tmp_path / "New.SchDoc"
    out = _run(tmp_path, [
        f"  OpenList := '|' + EdKey({_pas(str(new))}) + '|';",
        f"  EdMark({_pas(str(new))}, '', EdAgeNow({_pas(str(new))}), 'x', 's', '');",
        "  EdRefresh(0);",
        "  WriteLn('BEFORE=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(new)) + ")), Chr(2), 6));",
    ])
    assert "BEFORE=E" in out


@pytest.mark.parametrize("text,value", [
    ("12", 12), ("-1", -1), ("1999999999", 1999999999), ("2000000000", -1),
    ("99999999999", -1), ("abc", -1), ("", -1), ("1x", -1), ("-", -1),
])
def test_a_file_age_is_read_without_overflow(tmp_path, text, value):
    out = _run(tmp_path, [f"  WriteLn('AGE=', EdAgeOf({_pas(text)}));"])
    assert f"AGE={value}" in out


def test_replay_keeps_the_last_event_and_stops_at_a_torn_line(tmp_path):
    t = "\t"
    lines = [
        t.join(["1", "2026-11-20 10:00:00", "E", r"C:\A\PCB.PcbDoc", r"C:\A\A.PrjPcb", "5", "pcb.x", "s1", ""]),
        t.join(["2", "2026-11-20 10:00:01", "E", r"C:\B\PCB.PcbDoc", r"C:\B\B.PrjPcb", "6", "pcb.y", "s2", ""]),
        t.join(["3", "2026-11-20 10:00:02", "S", r"C:\A\PCB.PcbDoc", "", "", "", "", ""]),
        t.join(["4", "2026-11-20 10:00:03", "L", r"C:\C\X.SchLib", "", "7", "lib.z", "s3", "closed without saving"]),
        t.join(["5", "2026-11-20 10:00:04", "E", r"C:\D\Torn"]),
        t.join(["6", "2026-11-20 10:00:05", "E", r"C:\E\After.PcbLib", "", "8", "x", "s", ""]),
    ]
    out = _run(tmp_path, [
        "  EdRecord := EdReplay(" + " + Chr(13) + Chr(10) + ".join(_pas(l).replace("\t", "' + Chr(9) + '") for l in lines) + ");",
        r"  WriteLn('A=[', EdBodyOf(EdRecord, EdKey('C:\A\PCB.PcbDoc')), ']');",
        r"  WriteLn('B=', EdPart(EdBodyOf(EdRecord, EdKey('C:\B\PCB.PcbDoc')), Chr(2), 1), '/', EdPart(EdBodyOf(EdRecord, EdKey('C:\B\PCB.PcbDoc')), Chr(2), 3));",
        r"  WriteLn('C=', EdPart(EdBodyOf(EdRecord, EdKey('C:\C\X.SchLib')), Chr(2), 6), '/', EdPart(EdBodyOf(EdRecord, EdKey('C:\C\X.SchLib')), Chr(2), 7));",
        r"  WriteLn('E=[', EdBodyOf(EdRecord, EdKey('C:\E\After.PcbLib')), ']');",
    ])
    assert "A=[]" in out
    assert "B=C:\\B\\B.PrjPcb/6" in out
    assert "C=L/closed without saving" in out
    # Nothing after the torn line is trusted.
    assert "E=[]" in out


def test_a_journal_line_replays_to_the_same_entry(tmp_path):
    out = _run(tmp_path, [
        "  EdRecord := '';",
        r"  EdRecord := EdReplay(EdJournalLine(1, 't', 'E', EdBody('C:\P\Lib.PcbLib', 'C:\P\P.PrjPcb', 't', '42', 'library.set_pad_properties', 'abc', 'E', 'n')));",
        r"  WriteLn('BODY=', StringReplace(EdBodyOf(EdRecord, EdKey('C:\P\Lib.PcbLib')), Chr(2), '|', [rfReplaceAll]));",
    ])
    assert r"BODY=C:\P\Lib.PcbLib|C:\P\P.PrjPcb|t|42|library.set_pad_properties|abc|E|n" in out


# The JSON helpers live in Utils.pas; plain stand-ins are enough here.
_JSON_STUBS = r"""
function JsonBool(Name : String; Value : Boolean) : String;
begin
  if Value then Result := '"' + Name + '":true' else Result := '"' + Name + '":false';
end;
function JsonStr(Name, Value : String) : String;
begin
  Result := '"' + Name + '":"' + Value + '"';
end;
function EdOpenFullPath(Path : String) : String;
begin
  Result := Path;
end;
"""


@pytest.mark.parametrize("edited,modified,unsaved", [
    (False, False, "false"), (True, False, "true"), (False, True, "true"),
    (True, True, "true"),
])
def test_unsaved_is_edited_or_altiums_flag(tmp_path, edited, modified, unsaved):
    doc = _file(tmp_path, "Lib.PcbLib")
    mark = (f"  EdMark({_pas(str(doc))}, '', FileAge({_pas(str(doc))}), 'library.x', 's', '');"
            if edited else "")
    out = _run(tmp_path, [
        mark,
        f"  WriteLn('F=', EdDocFields({_pas(str(doc))}, {str(modified)}));",
    ], app=("EdDocFields",), stubs=_JSON_STUBS)
    line = next(x for x in out if x.startswith("F="))
    assert f'"edited":{str(edited).lower()}' in line
    assert f'"unsaved":{unsaved}' in line
    assert ('"last_command":"library.x"' in line) == edited


def _journal(tmp_path: Path, lines: list[list[str]]) -> None:
    (tmp_path / "edited_journal.txt").write_text(
        "".join("\t".join(cols) + "\r\n" for cols in lines), encoding="utf-8")


def test_unwind_replays_checks_and_compacts(tmp_path):
    held = _file(tmp_path, "Held.PcbLib")
    saved = _file(tmp_path, "Saved.SchDoc")
    gone = _file(tmp_path, "Gone.PcbDoc")
    _journal(tmp_path, [
        ["1", "t1", "E", str(held), "", "@HELD@", "a", "s", ""],
        ["2", "t2", "E", str(saved), "", "1", "b", "s", ""],
        ["3", "t3", "E", str(gone), "", "@GONE@", "c", "s", ""],
        ["4", "t4", "L", r"C:\Old\Lost.SchLib", "", "1", "d", "s", "closed without saving"],
        ["5", "t5", "E", r"C:\Old\Done.SchLib", "", "1", "e", "s", ""],
        ["6", "t6", "C", r"C:\Old\Done.SchLib", "", "", "", "", ""],
    ])
    out = _run(tmp_path, [
        # The ages of the files as they are now go in for HELD and GONE.
        "  with TStringList.Create do try",
        "    LoadFromFile(EdJournalPath(0));",
        f"    Text := StringReplace(Text, '@HELD@', IntToStr(FileAge({_pas(str(held))})), [rfReplaceAll]);",
        f"    Text := StringReplace(Text, '@GONE@', IntToStr(FileAge({_pas(str(gone))})), [rfReplaceAll]);",
        "    SaveToFile(EdJournalPath(0));",
        "  finally Free; end;",
        f"  OpenList := '|' + EdKey({_pas(str(held))}) + '|' + EdKey({_pas(str(saved))}) + '|';",
        "  EdUnwind(0);",
        "  WriteLn('HELD=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(held)) + ")), Chr(2), 6));",
        "  WriteLn('SAVED=[', EdBodyOf(EdRecord, EdKey(" + _pas(str(saved)) + ")), ']');",
        "  WriteLn('GONE=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(gone)) + ")), Chr(2), 6), '/',"
        " EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(gone)) + ")), Chr(2), 7));",
        r"  WriteLn('LOST=', EdPart(EdBodyOf(EdRecord, EdKey('C:\Old\Lost.SchLib')), Chr(2), 6));",
        r"  WriteLn('DONE=[', EdBodyOf(EdRecord, EdKey('C:\Old\Done.SchLib')), ']');",
        "  WriteLn('TMP=', FileExists(EdJournalPath(0) + '.tmp'));",
        "  Write(JournalText);",
    ], extra_main=("LoadTextFile",), status=("EdUnwind",))
    assert "HELD=E" in out
    assert "SAVED=[]" in out
    assert "GONE=L/not open when the bridge started again" in out
    assert "LOST=L" in out and "DONE=[]" in out
    assert "TMP=FALSE" in out
    journal = [line.split("\t") for line in out if "\t" in line]
    # Compacted: one line per surviving entry, numbered from 1.
    assert [cols[0] for cols in journal] == [str(i) for i in range(1, len(journal) + 1)]
    assert sorted(cols[2] for cols in journal) == ["E", "L", "L"]


def test_unwind_reads_the_temporary_file_when_the_journal_is_gone(tmp_path):
    held = _file(tmp_path, "Held.PcbLib")
    (tmp_path / "edited_journal.txt.tmp").write_text(
        "\t".join(["1", "t", "E", str(held), "", "@AGE@", "a", "s", ""]) + "\r\n",
        encoding="utf-8")
    out = _run(tmp_path, [
        "  with TStringList.Create do try",
        "    LoadFromFile(EdJournalPath(0) + '.tmp');",
        f"    Text := StringReplace(Text, '@AGE@', IntToStr(FileAge({_pas(str(held))})), [rfReplaceAll]);",
        "    SaveToFile(EdJournalPath(0) + '.tmp');",
        "  finally Free; end;",
        f"  OpenList := '|' + EdKey({_pas(str(held))}) + '|';",
        "  EdUnwind(0);",
        "  WriteLn('HELD=', EdPart(EdBodyOf(EdRecord, EdKey(" + _pas(str(held)) + ")), Chr(2), 6));",
        "  WriteLn('JOURNAL=', FileExists(EdJournalPath(0)));",
    ], extra_main=("LoadTextFile",), status=("EdUnwind",))
    assert "HELD=E" in out and "JOURNAL=TRUE" in out


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **_):
        self.sent.append((command, params))
        return {"documents": [], "lost": []}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap


def test_the_tool_reads_and_acknowledges(monkeypatch):
    from eda_agent.tools import application
    mcp = _Mcp()
    application.register_application_tools(mcp)
    bridge = _Bridge()
    monkeypatch.setattr(application, "get_bridge", lambda: bridge)
    tool = mcp.tools["app_edited_documents"]
    asyncio.run(tool())
    asyncio.run(tool(project_path=r"C:\B\B.PrjPcb"))
    asyncio.run(tool(file_path=r"C:\B\PCB.PcbDoc", acknowledge_lost=True))
    asyncio.run(tool(acknowledge_lost=True))
    assert bridge.sent == [
        ("application.get_edited_documents", {}),
        ("application.get_edited_documents", {"project_path": r"C:\B\B.PrjPcb"}),
        ("application.acknowledge_lost", {"file_path": r"C:\B\PCB.PcbDoc"}),
        ("application.acknowledge_lost", {}),
    ]
    assert metadata.interaction_of("app_edited_documents") == metadata.READONLY
