# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A document is found by what it is, never by a file name another project shares (#436).

A free document (open, in no project: a sheet or library copied from another
project and opened before it is added to its own) can report only its bare
file name (measured on AD 26.9; AD 25.8, 26.8 and 26.12 report the full path). The
path resolver then returned the first open project member of that name, so
the bare name of the copy resolved to the original in the OTHER project, and
its free-documents branch could never answer (it wanted an absolute path
such a document does not report). The library writers that
fill in a missing library_path from the focused document opened, wrote and
saved that other file, and the library check accepted any library whose file
name matched.

The routines are compiled with Free Pascal, as Altium runs them, against
stand-in workspace, project, document and schematic server classes.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _pas(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# The Altium interfaces as plain classes: only the members the routines read.
_STUBS = r"""
type
  IServerDocument = class
    FileName : String;
  end;
  IDocument = class
    FullPath, FileName_ : String;
    Server : IServerDocument;
    function DM_FullPath : String;
    function DM_FileName : String;
    function DM_ServerDocument : IServerDocument;
  end;
  IProject = class
    Docs : array of IDocument;
    function DM_LogicalDocumentCount : Integer;
    function DM_LogicalDocuments(J : Integer) : IDocument;
  end;
  IWorkspace = class
    Projects : array of IProject;
    Free : IProject;
    function DM_ProjectCount : Integer;
    function DM_Projects(I : Integer) : IProject;
    function DM_FreeDocumentsProject : IProject;
  end;
  ISch_Document = class
    Name : String;
    function DocumentName : String;
  end;
  ISch_Lib = ISch_Document;
  TSchServer = class
    Libs : array of ISch_Document;
    Paths : array of String;
    function GetSchDocumentByPath(P : String) : ISch_Document;
  end;

var
  TheWorkspace : IWorkspace;
  SchServer : TSchServer;
  Lib : ISch_Document;
  Loose : IDocument;

function IDocument.DM_FullPath : String; begin Result := FullPath; end;
function IDocument.DM_FileName : String; begin Result := FileName_; end;
function IDocument.DM_ServerDocument : IServerDocument; begin Result := Server; end;
function IProject.DM_LogicalDocumentCount : Integer; begin Result := Length(Docs); end;
function IProject.DM_LogicalDocuments(J : Integer) : IDocument; begin Result := Docs[J]; end;
function IWorkspace.DM_ProjectCount : Integer; begin Result := Length(Projects); end;
function IWorkspace.DM_Projects(I : Integer) : IProject; begin Result := Projects[I]; end;
function IWorkspace.DM_FreeDocumentsProject : IProject; begin Result := Free; end;
function ISch_Document.DocumentName : String; begin Result := Name; end;
function TSchServer.GetSchDocumentByPath(P : String) : ISch_Document;
var I : Integer;
begin
  Result := Nil;
  for I := 0 to High(Paths) do
    if UpperCase(Paths[I]) = UpperCase(P) then Result := Libs[I];
end;

function GetWorkspace : IWorkspace; begin Result := TheWorkspace; end;

{ A project member reports its full path; a free document its bare name, }
{ with the file it was loaded from behind it.                            }
function Member(Path : String) : IDocument;
begin
  Result := IDocument.Create;
  Result.FullPath := Path;
  Result.FileName_ := ExtractFileName(Path);
  Result.Server := IServerDocument.Create;
  Result.Server.FileName := Path;
end;

function FreeDoc(Path : String) : IDocument;
begin
  Result := Member(Path);
  Result.FullPath := ExtractFileName(Path);
end;

function Project(D1, D2 : IDocument) : IProject;
begin
  Result := IProject.Create;
  if D1 <> nil then begin SetLength(Result.Docs, 1); Result.Docs[0] := D1; end;
  if D2 <> nil then begin SetLength(Result.Docs, 2); Result.Docs[1] := D2; end;
end;

procedure Workspace(P1, P2, FreeProj : IProject);
begin
  TheWorkspace := IWorkspace.Create;
  if P1 <> nil then begin SetLength(TheWorkspace.Projects, 1); TheWorkspace.Projects[0] := P1; end;
  if P2 <> nil then begin SetLength(TheWorkspace.Projects, 2); TheWorkspace.Projects[1] := P2; end;
  if FreeProj = nil then FreeProj := IProject.Create;
  TheWorkspace.Free := FreeProj;
end;

function SchLib(Name : String) : ISch_Document;
begin
  Result := ISch_Document.Create;
  Result.Name := Name;
end;

procedure OpenAt(Lib : ISch_Document; Path : String);
var N : Integer;
begin
  N := Length(SchServer.Paths);
  SetLength(SchServer.Paths, N + 1); SetLength(SchServer.Libs, N + 1);
  SchServer.Paths[N] := Path; SchServer.Libs[N] := Lib;
end;
"""

_RESOLVER = [("Main.pas", "LooksAbsolutePath"), ("Main.pas", "DistinctPathsAdd"),
             ("Main.pas", "OnlyDistinctPath"), ("Main.pas", "DocServerPath"),
             ("Main.pas", "DocPathsNamedIn"), ("Main.pas", "ResolveLoadedDocPath"),
             ("Main.pas", "DocFullPath")]
_LIBCHECK = [("Main.pas", "LooksAbsolutePath"), ("Main.pas", "CanonicalPathValue"),
             ("Library.pas", "SchLibIsAtPath")]

A = r"C:\proj_a\Power.SchDoc"
B = r"C:\proj_b\Power.SchDoc"


def _run(tmp_path: Path, routines: list[tuple[str, str]], body: list[str]) -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    source = "\n\n".join(_routine(u, n) for u, n in routines)
    # DelphiScript takes -1 for "replace all"; Free Pascal wants the set.
    source = re.sub(r"(StringReplace\([^;]*?),\s*-1\)", r"\1, [rfReplaceAll])", source)
    program = "\n".join([
        "program lookup;", "{$mode delphi}{$H+}", "uses SysUtils;",
        _STUBS, source,
        "begin", "  SchServer := TSchServer.Create;", *body, "end.",
    ])
    path = tmp_path / "lookup.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    exe = tmp_path / ("lookup.exe" if os.name == "nt" else "lookup")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


# -- the path of a document ----------------------------------------------------

def test_a_free_copy_is_its_own_file_not_the_other_projects(tmp_path):
    """Project A holds Power.SchDoc; its copy from B's folder is open as a free
    document. The copy is the copy, wherever the name also occurs."""
    out = _run(tmp_path, _RESOLVER, [
        f"  Workspace(Project(Member({_pas(A)}), nil), nil, Project(FreeDoc({_pas(B)}), nil));",
        f"  WriteLn(DocFullPath(TheWorkspace.Free.Docs[0]));",
        f"  WriteLn(DocFullPath(TheWorkspace.Projects[0].Docs[0]));",
    ])
    assert out == [B, A]


def test_a_bare_name_two_files_carry_resolves_to_nothing(tmp_path):
    out = _run(tmp_path, _RESOLVER, [
        f"  Workspace(Project(Member({_pas(A)}), nil), nil, Project(FreeDoc({_pas(B)}), nil));",
        "  WriteLn('[' + ResolveLoadedDocPath('Power.SchDoc') + ']');",
        # two projects, one name each: the first one is no answer either
        f"  Workspace(Project(Member({_pas(A)}), nil), Project(Member({_pas(B)}), nil), nil);",
        "  WriteLn('[' + ResolveLoadedDocPath('Power.SchDoc') + ']');",
    ])
    assert out == ["[]", "[]"]


def test_a_bare_name_one_file_carries_resolves_to_it(tmp_path):
    shared = r"C:\common\Shared.SchDoc"
    out = _run(tmp_path, _RESOLVER, [
        # only a free document: the branch that could never answer now does
        f"  Workspace(nil, nil, Project(FreeDoc({_pas(B)}), nil));",
        "  WriteLn(ResolveLoadedDocPath('power.schdoc'));",
        # only a member
        f"  Workspace(Project(Member({_pas(A)}), nil), nil, nil);",
        "  WriteLn(ResolveLoadedDocPath('Power.SchDoc'));",
        # one sheet two projects list is one file
        f"  Workspace(Project(Member({_pas(shared)}), nil), Project(Member({_pas(shared)}), nil), nil);",
        "  WriteLn(ResolveLoadedDocPath('Shared.SchDoc'));",
        # a full path is taken as given
        "  WriteLn(ResolveLoadedDocPath('C:\\x\\Power.SchDoc'));",
    ])
    assert out == [B, A, shared, r"C:\x\Power.SchDoc"]


def test_a_member_keeps_its_full_path_without_asking_its_server_document(tmp_path):
    out = _run(tmp_path, _RESOLVER, [
        f"  Workspace(Project(Member({_pas(A)}), nil), nil, nil);",
        "  TheWorkspace.Projects[0].Docs[0].Server.FileName := 'C:\\elsewhere\\Power.SchDoc';",
        "  WriteLn(DocFullPath(TheWorkspace.Projects[0].Docs[0]));",
    ])
    assert out == [A]


def test_a_bare_name_with_no_file_of_its_own_stays_bare(tmp_path):
    """A document that reports its bare name and cannot give its own file is
    not looked up by that name: the one other file carrying it is another
    document. The bare name goes back, and every caller refuses it."""
    out = _run(tmp_path, _RESOLVER, [
        f"  Workspace(Project(Member({_pas(A)}), nil), nil, Project(FreeDoc({_pas(B)}), nil));",
        "  TheWorkspace.Free.Docs[0].Server := nil;",
        "  WriteLn('[' + DocFullPath(TheWorkspace.Free.Docs[0]) + ']');",
    ])
    assert out == ["[Power.SchDoc]"]


def test_a_document_with_no_full_path_is_found_by_a_unique_file_name(tmp_path):
    """No DM_FullPath at all, only a DM_FileName: the one open file of that
    name answers, two files of that name answer nothing."""
    out = _run(tmp_path, _RESOLVER, [
        f"  Loose := Member({_pas(A)});",
        "  Loose.FullPath := '';",
        "  Loose.Server := nil;",
        f"  Workspace(Project(Member({_pas(A)}), nil), nil, nil);",
        "  WriteLn(DocFullPath(Loose));",
        f"  Workspace(Project(Member({_pas(A)}), nil), Project(Member({_pas(B)}), nil), nil);",
        "  WriteLn('[' + DocFullPath(Loose) + ']');",
    ])
    assert out == [A, "[Power.SchDoc]"]


# -- is this the library asked for ---------------------------------------------

LIB_A = r"C:\proj_a\Parts.SchLib"
LIB_B = r"C:\proj_b\Parts.SchLib"


def test_a_library_of_the_same_name_elsewhere_is_not_the_one_asked_for(tmp_path):
    out = _run(tmp_path, _LIBCHECK, [
        f"  WriteLn(SchLibIsAtPath(SchLib({_pas(LIB_A)}), {_pas(LIB_A)}));",
        f"  WriteLn(SchLibIsAtPath(SchLib({_pas(LIB_A)}), 'c:/proj_a/parts.schlib'));",
        f"  WriteLn(SchLibIsAtPath(SchLib({_pas(LIB_A)}), {_pas(LIB_B)}));",
    ])
    assert out == ["TRUE", "TRUE", "FALSE"]


def test_a_free_library_is_checked_by_the_document_at_the_path(tmp_path):
    out = _run(tmp_path, _LIBCHECK, [
        "  { the free library itself: the one open at the path asked for }",
        "  Lib := SchLib('Parts.SchLib');",
        f"  OpenAt(Lib, {_pas(LIB_B)});",
        f"  WriteLn(SchLibIsAtPath(Lib, {_pas(LIB_B)}));",
        "  { another library of that name is open there: not this one }",
        f"  OpenAt(SchLib({_pas(LIB_A)}), {_pas(LIB_A)});",
        f"  WriteLn(SchLibIsAtPath(Lib, {_pas(LIB_A)}));",
        "  { nothing open at the path }",
        "  WriteLn(SchLibIsAtPath(Lib, 'C:\\none\\Parts.SchLib'));",
        "  { the bare name it reports, as the focused document gives it }",
        "  WriteLn(SchLibIsAtPath(Lib, 'Parts.SchLib'));",
    ])
    assert out == ["TRUE", "FALSE", "FALSE", "TRUE"]


def test_the_style_audit_refreshes_the_focused_library_by_its_own_file():
    body = _routine("Library.pas", "Lib_AuditStyles")
    assert "FocusedPath := CanonicalPathValue('path', DocFullPath(Doc))" in body
    assert "CanonicalPathValue('path', Doc.DM_FullPath)" not in body
