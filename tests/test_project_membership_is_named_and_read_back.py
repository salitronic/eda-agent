# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Project membership goes where it is asked and is read back from disk.

A scratch sheet created with add_to_project joined whichever project had
focus, a production one, and adding it to the intended project after did
not take it out of the first (issue #94). And a document removed from a
project was back after a reopen, because only the project in memory had
changed. Creation now takes the project by path and reports member_of;
removal saves the project and checks the file.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import application as app_tools
from eda_agent.tools import project as project_tools

from tests.pascal_source import load

_ALT = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def test_a_named_project_is_found_before_anything_is_created():
    body = load(_ALT / "Application.pas", minimum=20)["App_CreateDocument"]
    lookup = body.index("Project := OpenProjectAt(Workspace, ProjectPath)")
    refuse = body.index("'NO_PROJECT'")
    create = body.index("Client.OpenNewDocument(")
    assert lookup < refuse < create


def test_the_document_goes_to_that_project_only():
    body = load(_ALT / "Application.pas", minimum=20)["App_CreateDocument"]
    start = body.index("Added := False;")
    add = body[start:body.index("If Added Then ProjSaved", start)]
    assert "Project.DM_AddSourceDocument(FilePath);" in add
    assert "DM_FocusedProject" not in add


def test_membership_is_read_back():
    fns = load(_ALT / "Application.pas", minimum=20)
    assert '"member_of":[' in fns["App_CreateDocument"]
    assert "DM_LogicalDocuments(J)" in fns["ProjectsHoldingJson"]


def test_a_removal_saves_the_project_and_reads_the_file():
    body = load(_ALT / "Project.pas")["Proj_RemoveDocument"]
    remove = body.index("Project.DM_RemoveSourceDocument(DocumentPath);")
    save = body.index("ProjSaved := SaveProjectFile(Project);")
    check = body.index("Entry := ProjectFileEntryFor(ProjPath, DocumentPath);", save)
    # Measured live: Altium's writer leaves [DocumentN] sections past the
    # new count, so the file is read back and a leftover is reported.
    assert remove < save < check
    assert '"in_project_file":true' in body


def test_every_membership_change_writes_the_project_file():
    """An add or remove changes the project without marking it modified,
    so a save that waits for the flag never writes the .PrjPcb (measured:
    two sheets added, proj_save, the file listed neither)."""
    project = load(_ALT / "Project.pas")
    assert "SaveProjectFile(Project)" in project["Proj_AddDocument"]
    assert "SaveProjectFile(Project)" in project["Proj_Save"]
    assert "SaveProjectFile(Project)" in load(
        _ALT / "Application.pas", minimum=20)["App_CreateDocument"]
    generic = load(_ALT / "Generic.pas")
    assert "SaveProjectFile(Project)" in generic["Gen_AddSheet"]
    delete = generic["Gen_DeleteSheet"]
    assert "Proj_RemoveDocument(" in delete
    assert "WorkspaceManager:RemoveObjectFromProject" not in delete
    assert "WorkspaceManager:CloseObject" not in delete


def test_the_file_check_reads_documentpath_lines():
    body = load(_ALT / "Project.pas")["ProjectFileEntryFor"]
    assert "LoadTextFile(SL, ProjPath)" in body
    assert "'DOCUMENTPATH='" in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.params = None

    async def send_command_async(self, command, params=None, timeout=None):
        self.params = dict(params or {})
        return {"success": True}


def _run(module, register, tool, monkeypatch, *args, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    getattr(module, register)(mcp)
    asyncio.run(mcp.tools[tool](*args, **kw))
    return bridge.params


def test_the_create_tool_sends_the_project(monkeypatch, tmp_path):
    # A real folder: a missing one is refused before anything is sent.
    sent = _run(app_tools, "register_application_tools", "app_create_document",
                monkeypatch, "SCH", str(tmp_path / "x.SchDoc"),
                project_path=r"C:\t\Scratch.PrjPcb")
    assert sent["project_path"] == r"C:\t\Scratch.PrjPcb"


def test_the_remove_tool_can_leave_the_save_for_later(monkeypatch):
    sent = _run(project_tools, "register_project_tools", "proj_remove_document",
                monkeypatch, r"C:\t\x.SchDoc", save_project=False)
    assert sent["save_project"] == "false"
    sent = _run(project_tools, "register_project_tools", "proj_remove_document",
                monkeypatch, r"C:\t\x.SchDoc")
    assert "save_project" not in sent


def test_a_new_sheet_never_opens_a_file_left_on_disk():
    """proj_delete_sheet leaves the file; a sheet created again under the
    same name opened it, old drawing and all (retake 07b)."""
    from pathlib import Path
    from tests.pascal_source import load
    body = load(Path(__file__).resolve().parents[1] / "scripts" / "altium"
                / "Generic.pas")["Gen_AddSheet"]
    check = body.index("FileExists(NewDocPath) Then")
    assert check < body.index("Client.OpenNewDocument(")
    assert "'FILE_EXISTS'" in body[check:body.index("Client.OpenNewDocument(")]
