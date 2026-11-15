# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A session is checked against the document it names, and can let go of one.

Two sessions sharing one Altium were refused because of each other's
FOCUSED document: the ownership guard read the target of a write
only from board_path / expect_file / file_path, so a command naming its
document as project_path or library_path was checked against whatever
the other session had left in front, and project.open, which changes no
document, counted as a write. And a claim could only be handed over
(app_claim_document), never given back: the other session waited out the
idle time.

The routines are compiled with Free Pascal, as Altium runs them; the
JSON reader is a flat stand-in (ExtractJsonValue needs half of Main.pas).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import safety
from eda_agent.tools import metadata

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _pas(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# A flat stand-in: "key":"value" pairs only, which is all the guard reads.
_JSON_STUB = r"""
function ExtractJsonValue(Json : String; Key : String) : String;
var P, E : Integer; Rest : String;
begin
  Result := '';
  P := Pos('"' + Key + '":"', Json);
  if P = 0 then Exit;
  Rest := Copy(Json, P + Length(Key) + 4, Length(Json));
  E := Pos('"', Rest);
  Result := Copy(Rest, 1, E - 1);
end;
"""

_TICK_STUB = r"""
var Tick : Cardinal = 1000;
function GetTickCount : Cardinal;
begin Result := Tick; end;
"""


def _run(tmp_path: Path, routines: list[tuple[str, str]], body: list[str]) -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    source = "\n\n".join(_routine(u, n) for u, n in routines)
    # DelphiScript takes -1 for "replace all"; Free Pascal wants the set.
    source = re.sub(r"(StringReplace\([^;]*?),\s*-1\)", r"\1, [rfReplaceAll])", source)
    program = "\n".join([
        "program owners;", "{$mode delphi}{$H+}", "uses SysUtils;",
        "var DocOwners : String;",
        _JSON_STUB, _TICK_STUB, source,
        "begin", "  DocOwners := '';", *body, "end.",
    ])
    path = tmp_path / "owners.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("owners.exe" if os.name == "nt" else "owners")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


# -- what a write is checked against -------------------------------------------

_TARGET = [("StatusForm.pas", "CommandTarget")]
FOCUS = r"C:\b\front.PcbDoc"


@pytest.mark.parametrize("params, want", [
    # the document the command names, never the one in front
    ('{"project_path":"C:\\p\\a.PrjPcb"}', r"C:\p\a.PrjPcb"),
    ('{"library_path":"C:\\l\\x.SchLib"}', r"C:\l\x.SchLib"),
    ('{"sheet_path":"C:\\p\\s.SchDoc"}', r"C:\p\s.SchDoc"),
    ('{"document_path":"C:\\p\\d.SchDoc"}', r"C:\p\d.SchDoc"),
    ('{"scope":"doc:C:\\p\\s.SchDoc"}', r"C:\p\s.SchDoc"),
    ('{"board_path":"C:\\b\\b.PcbDoc"}', r"C:\b\b.PcbDoc"),
    ('{"file_path":"C:\\f\\f.PcbLib"}', r"C:\f\f.PcbLib"),
    # a sheet named beside its project is the narrower target
    ('{"project_path":"C:\\p\\a.PrjPcb","sheet_path":"C:\\p\\s.SchDoc"}',
     r"C:\p\s.SchDoc"),
    # a scope that names no document, or nothing named: the front one
    ('{"scope":"active_doc"}', FOCUS),
    ('{"x":"1"}', FOCUS),
])
def test_a_write_is_checked_against_the_document_it_names(tmp_path, params, want):
    json = params.replace("\\\\", "\\")
    out = _run(tmp_path, _TARGET,
               [f"  WriteLn(CommandTarget({_pas(json)}, {_pas(FOCUS)}));"])
    assert out == [want]


def test_dispatch_takes_its_target_from_the_resolver():
    body = _routine("StatusForm.pas", "ProcessSingleRequest")
    assert "OwnTarget := CommandTarget(Params, FocusBefore);" in body
    assert "ExtractJsonValue(Params, 'board_path')" not in body


def test_opening_a_project_neither_claims_nor_is_refused():
    assert safety.command_only_looks("project.open")
    assert safety.command_only_looks("application.release_document")
    assert not safety.command_only_looks("project.set_parameter")
    looks = _routine("StatusForm.pas", "CommandOnlyLooks")
    assert "(Verb = 'open')" in looks and "(Verb = 'release_document')" in looks


# -- giving a document back ----------------------------------------------------

_OWNERS = [("Main.pas", "OwnerKey"), ("Main.pas", "OwnerRecord"),
           ("Main.pas", "OwnerField"), ("Main.pas", "OwnerDrop"),
           ("Main.pas", "OwnerSet"), ("Main.pas", "RequestTag"),
           ("Application.pas", "OwnerKeyHeldBy")]


def test_a_dropped_document_is_free_and_the_others_stay_held(tmp_path):
    out = _run(tmp_path, _OWNERS, [
        "  OwnerSet(OwnerKey('C:\\a.PcbDoc'), 'aaaa1111', 'pcb.place_tracks');",
        "  OwnerSet(OwnerKey('C:\\b.SchDoc'), 'bbbb2222', 'sch.place_wires');",
        "  OwnerSet(OwnerKey('C:\\c.PcbLib'), 'aaaa1111', 'library.add_pins');",
        "  WriteLn(OwnerKeyHeldBy('aaaa1111'));",
        "  OwnerDrop(OwnerKey('c:/a.pcbdoc'));",
        "  WriteLn(OwnerRecord(OwnerKey('C:\\a.PcbDoc')) = '');",
        "  WriteLn(OwnerField(OwnerRecord(OwnerKey('C:\\b.SchDoc')), 0));",
        "  WriteLn(OwnerKeyHeldBy('aaaa1111'));",
        "  OwnerDrop(OwnerKey('C:\\c.PcbLib'));",
        "  WriteLn('[' + OwnerKeyHeldBy('aaaa1111') + ']');",
        "  WriteLn(OwnerKeyHeldBy('bbbb2222'));",
        "  OwnerDrop(OwnerKey('C:\\nothing.SchDoc'));",
        "  WriteLn(OwnerField(OwnerRecord(OwnerKey('C:\\b.SchDoc')), 2));",
    ])
    assert out == [r"C:\A.PCBDOC", "TRUE", "bbbb2222", r"C:\C.PCBLIB",
                   "[]", r"C:\B.SCHDOC", "sch.place_wires"]


def test_a_set_replaces_the_record_instead_of_adding_one(tmp_path):
    out = _run(tmp_path, _OWNERS, [
        "  OwnerSet(OwnerKey('C:\\a.PcbDoc'), 'aaaa1111', 'one');",
        "  OwnerSet(OwnerKey('C:\\a.PcbDoc'), 'bbbb2222', 'two');",
        "  OwnerDrop(OwnerKey('C:\\a.PcbDoc'));",
        "  WriteLn('[' + DocOwners + ']');",
    ])
    assert out == ["[]"]


def test_release_gives_back_only_this_sessions_documents():
    body = _routine("Application.pas", "App_ReleaseDocument")
    assert "Tag := RequestTag(RequestId);" in body
    assert "If Owner <> Tag Then" in body and "'NOT_YOURS'" in body
    assert "Key := OwnerKeyHeldBy(Tag);" in body and "OwnerDrop(Key);" in body
    code = (SCRIPTS / "Application.pas").read_text(encoding="utf-8")
    assert "'release_document':    Result := App_ReleaseDocument(Params, RequestId);" in code


def test_the_refusal_says_how_it_ends():
    body = _routine("StatusForm.pas", "OwnerRefusal")
    assert "app_release_document" in body and "app_claim_document" in body


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None):
        self.sent.append((command, params))
        return {"released": []}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.mark.asyncio
async def test_the_tool_releases_one_document_or_all(monkeypatch):
    from eda_agent.tools import application
    bridge = _Bridge()
    monkeypatch.setattr(application, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    application.register_application_tools(mcp)
    release = mcp.tools["app_release_document"]
    await release(file_path=r"C:\a.PcbDoc")
    await release()
    assert bridge.sent == [
        ("application.release_document", {"file_path": r"C:\a.PcbDoc"}),
        ("application.release_document", {}),
    ]
    # Never refused itself, so a session can always let go.
    assert metadata.interaction_of("app_release_document") == metadata.READONLY
