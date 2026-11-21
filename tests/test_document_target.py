# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A command names its document by full path, and the bridge ensures it (#428).

A write used to land on whatever document was in front. A request may now
carry target_document and target_project, both absolute; the dispatcher
puts that document in front inside its project, or refuses the command
before the handler runs. set_active_document takes only a full path (the
old "ends with" match in the focused project is gone), set_active_project
opens a project, and get_project_files lists its documents by full path.

The path and kind checks are compiled with Free Pascal, as Altium runs
them; the rest is checked in the source and through the tools.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import safety
from eda_agent.bridge.exceptions import AltiumError
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
# The dispatcher
# ---------------------------------------------------------------------------

def test_ensure_runs_before_the_handler_and_its_refusal_replaces_it():
    body = _routine("StatusForm.pas", "ProcessSingleRequest")
    ensure = body.index("EnsureOut := EnsureTarget(Command, Params, RequestId);")
    # after the documents a command names are noted as loaded or not, so a
    # document Ensure opens is reported as opened by the bridge
    assert body.index("LoadedBefore := LoadedNow(NamedDocs);") < ensure
    # what a write acts on is read again once Ensure moved the front
    assert "OwnTarget := CommandTarget(Params, FocusBefore);" in body[ensure:]
    refusal = body.index("ResponseContent := EnsureRefusal;")
    assert refusal < body.index("ResponseContent := ProcessCommand(Command, Params, RequestId);")
    assert "If (ResponseContent = '') And (EnsureRefusal <> '') Then" in body
    # a refused command claims no document
    assert "(Not OwnRefused) And (EnsureRefusal = '')" in body
    assert "AppendEnvelopeField(ResponseContent, EnsureNote)" in body


def test_the_named_target_is_what_a_write_acts_on():
    target = _routine("StatusForm.pas", "CommandTarget")
    assert target.index("'target_document'") < target.index("'board_path'")
    assert "'target_document'" in _routine("StatusForm.pas", "CollectDocPaths")


def test_ensure_opens_the_project_first_and_checks_the_front():
    body = _routine("StatusForm.pas", "EnsureTarget")
    for code in ("'BAD_TARGET'", "'KIND_MISMATCH'", "'NOT_IN_PROJECT'", "'NOT_OPEN'",
                 "'TARGET_NOT_FOUND'", "'WRONG_DOCUMENT'", "'WRONG_PROJECT'"):
        assert code in body
    # the project is opened before a member is looked for or opened
    assert body.index("RunProcess('WorkspaceManager:OpenObject')") < body.index("EnsureMember(Project, Doc)")
    assert body.index("EnsureMember(Project, Doc)") < body.index("ServerDoc := EnsureOpen(Doc);")
    # the front is read from the view, and compared after the show
    assert body.count("Front := EdFrontPath(0);") >= 2
    # a file several projects list: the named project is focused first, and
    # the refusal comes only when the front or the focus is still wrong
    branch = body.index("If (Doc <> '') And Not FocusedProjectPathIs(Prj) Then")
    refocus = body.index("EnsureFocusProject(Workspace, Project, Prj);", branch)
    assert refocus < body.index("Altium could not be brought onto", branch)
    assert "If Not EnsureSame(Front, Doc) Then" in body
    # every refusal says nothing ran
    assert body.count("Nothing was run.") >= 10


def test_moving_the_focus_prefers_a_document_already_loaded():
    body = _routine("StatusForm.pas", "EnsureFocusProject")
    assert "For Pass := 0 To 1 Do" in body
    assert "If (ServerDoc = Nil) And (Pass = 1) Then" in body


def test_a_member_is_never_opened_without_its_project():
    body = _routine("StatusForm.pas", "EnsureTarget")
    no_project = body.index("'NOT_OPEN'")
    assert no_project < body.index("ServerDoc := EnsureOpen(Doc);")


# ---------------------------------------------------------------------------
# set_active_document, set_active_project, get_project_files
# ---------------------------------------------------------------------------

def test_set_active_document_takes_a_full_path_only():
    body = _routine("Application.pas", "App_SetActiveDocument")
    assert "EnsurePathProblem(Doc, False)" in body
    assert "DM_FocusedProject" not in body          # the old focused-project walk
    assert "Copy(LowerDocPath" not in body           # the old "ends with" match
    assert "EnsureMember(Project, Doc)" in body
    assert body.index("Client.ShowDocument(ServerDoc)") < body.index("If Not EnsureSame(Front, Doc) Then")


def test_the_new_commands_are_dispatched_and_classified():
    table = _routine("Application.pas", "HandleApplicationCommand")
    assert "'get_project_files':   Result := App_GetProjectFiles(Params, RequestId);" in table
    assert "'set_active_project':  Result := App_SetActiveProject(Params, RequestId);" in table
    assert safety.command_only_looks("application.set_active_project")
    assert safety.command_is_read_only("application.get_project_files")
    # neither marks a document as edited (#426)
    assert safety.command_leaves_documents("application.set_active_project")
    assert metadata.interaction_of("app_get_project_files") == metadata.READONLY


def test_project_files_carry_path_state_and_the_unsaved_record():
    body = _routine("Application.pas", "ProjectFilesJson")
    for field in ("'file_path'", "'kind'", "'in_project_folder'", "'exists'", "'loaded'",
                  "'front'", "'\"modified\":'"):
        assert field.strip("'") in body or field in body
    assert "EdDocFields(P, Modified)" in body


# ---------------------------------------------------------------------------
# The path and kind checks, compiled
# ---------------------------------------------------------------------------

def _run(tmp_path: Path, body: list[str]) -> list[str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    source = "\n\n".join(_routine("Application.pas", n) for n in
                         ("EnsurePathProblem", "EnsureKindProblem", "EnsureDocKind"))
    program = "\n".join(["program target;", "{$mode delphi}{$H+}", "uses SysUtils;",
                         source, "begin", *body, "end."])
    src = tmp_path / "target.pas"
    src.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-v0", f"-FE{tmp_path}", str(src)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(tmp_path / "target.exe")], capture_output=True, text=True,
                         timeout=60)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.splitlines()


PATHS = [
    (r"C:\Work\A\PCB.PcbDoc", False, ""),
    (r"\\server\share\B\PCB.PcbDoc", False, ""),
    (r"C:\Work\A\A.PrjPcb", True, ""),
    (r"C:\Work\Lib.LibPkg", True, ""),
    ("PCB.PcbDoc", False, "not an absolute path"),
    (r"A\PCB.PcbDoc", False, "not an absolute path (a drive letter"),
    (r"1:\A\PCB.PcbDoc", False, "not an absolute path (a drive letter"),
    ("C:/Work/A/PCB.PcbDoc", False, "forward slashes"),
    (r"C:\Work\A\..\B\PCB.PcbDoc", False, "a . or .. part"),
    (r"C:\Work\.\PCB.PcbDoc", False, "a . or .. part"),
    (r"C:\PROGRA~1\A\PCB.PcbDoc", False, "a short 8.3 spelling"),
    (r"C:\Work\A\PCB", False, "no file extension"),
    (r"C:\Work\A\PCB.PcbDoc", True, "not a project file"),
]


def test_only_an_absolute_long_path_passes(tmp_path):
    body = [f"  WriteLn(EnsurePathProblem({_pas(p)}, {str(prj)}));" for p, prj, _ in PATHS]
    out = _run(tmp_path, body)
    for (path, _, want), got in zip(PATHS, out):
        assert got.startswith(want) if want else got == "", (path, got)


KINDS = [
    ("pcb.place_pad", r"C:\A\PCB.PcbDoc", ""),
    ("pcb.layer_view", r"C:\A\Lib.PcbLib", ""),
    ("pcb.place_pad", r"C:\A\Sheet.SchDoc", "a pcb. command"),
    ("library.add_pins", r"C:\A\Sym.SchLib", ""),
    ("library.add_pins", r"C:\A\PCB.PcbDoc", "a library. command"),
    ("generic.query_objects", r"C:\A\Sheet.SchDoc", ""),
    ("project.compile", r"C:\A\Sheet.SchDoc", ""),
]


def test_a_command_at_the_wrong_kind_of_document_is_refused(tmp_path):
    body = [f"  WriteLn(EnsureKindProblem({_pas(c)}, {_pas(d)}));" for c, d, _ in KINDS]
    body += [f"  WriteLn(EnsureDocKind({_pas(p)}));" for p in
             (r"C:\A\x.PcbDoc", r"C:\A\x.SCHDOC", r"C:\A\x.PcbLib", r"C:\A\x.SchLib",
              r"C:\A\x.OutJob")]
    out = _run(tmp_path, body)
    for (cmd, doc, want), got in zip(KINDS, out):
        assert got.startswith(want) if want else got == "", (cmd, doc, got)
    assert out[len(KINDS):] == ["PCB", "SCH", "PCBLIB", "SCHLIB", ""]


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

class _Bridge:
    def __init__(self, refuse=None):
        self.sent = []
        self.refuse = refuse

    async def send_command_async(self, command, params=None, **_):
        self.sent.append((command, params))
        if self.refuse and command == "application.set_active_document":
            raise AltiumError(self.refuse)
        return {"project_path": (params or {}).get("project_path", ""), "files": []}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap


def _tools(monkeypatch, bridge):
    from eda_agent.tools import application
    mcp = _Mcp()
    application.register_application_tools(mcp)
    monkeypatch.setattr(application, "get_bridge", lambda: bridge)
    return mcp.tools


def test_the_tools_send_full_paths_and_the_project(monkeypatch):
    bridge = _Bridge()
    tools = _tools(monkeypatch, bridge)
    asyncio.run(tools["app_set_active_project"](project_path=r"C:\B\B.PrjPcb"))
    asyncio.run(tools["app_get_project_files"](project_path=r"C:\B\B.PrjPcb"))
    asyncio.run(tools["app_set_active_document"](file_path=r"C:\B\PCB.PcbDoc",
                                                 project_path=r"C:\B\B.PrjPcb"))
    asyncio.run(tools["app_set_active_document"](file_path=r"C:\B\PCB.PcbDoc"))
    assert bridge.sent == [
        ("application.set_active_project", {"project_path": r"C:\B\B.PrjPcb"}),
        ("application.get_project_files", {"project_path": r"C:\B\B.PrjPcb"}),
        ("application.set_active_document", {"file_path": r"C:\B\PCB.PcbDoc",
                                             "project_path": r"C:\B\B.PrjPcb"}),
        ("application.set_active_document", {"file_path": r"C:\B\PCB.PcbDoc"}),
    ]


def test_with_a_project_the_tool_opens_nothing_itself(monkeypatch, tmp_path):
    # The bridge opens a member inside its project; a refusal there is the
    # answer, never an open outside the project.
    doc = tmp_path / "PCB.PcbDoc"
    doc.write_text("x", encoding="utf-8")
    bridge = _Bridge(refuse="NOT_LOADED: Document not loaded")
    tools = _tools(monkeypatch, bridge)
    with pytest.raises(AltiumError):
        asyncio.run(tools["app_set_active_document"](file_path=str(doc),
                                                     project_path=str(tmp_path / "B.PrjPcb")))
    assert [c for c, _ in bridge.sent] == ["application.set_active_document"]
