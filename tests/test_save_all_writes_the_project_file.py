# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_save_all writes a project file that has something to write, and
counts it.

A sheet renamed in the Projects panel is renamed on disk and in the
project, and app_save_all left the .PrjPcb naming the old file while it
reported still_dirty 0 (#291).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Main.pas"


@pytest.fixture(scope="module")
def fns():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))


def test_every_member_with_a_file_must_be_listed(fns):
    # Altium keeps no project modified flag a script can read: the panel
    # showed the project modified while its document read Modified false.
    body = fns["ProjectFileNeedsSave"]
    assert "Needs := ProjDoc.Modified;" in body
    assert "Listed := ProjectListedNames(Project);" in body
    assert "Names := ProjectMemberNames(Project);" in body
    # A renamed sheet's new name, or an added document, is not listed.
    assert "If (One <> '') And (Pos('|' + One + '|', Listed) = 0) Then Needs := True;" in body
    # ActiveBOM.VirtualBOM is a member with no file.
    assert "If (Entry = '') Or (Not FileExists(Entry)) Then Continue;" in fns["ProjectMemberNames"]


def test_a_removal_alone_does_not_keep_a_project_dirty(fns):
    # Altium leaves removed documents' sections in the file after a save,
    # so a name the file holds and the project does not must not flag it.
    body = fns["ProjectFileNeedsSave"]
    assert "Pos('|' + One + '|', Names)" not in body


def test_the_project_file_is_never_opened_for_the_check(fns):
    for name in ("ProjectFileNeedsSave", "ProjectMemberNames"):
        assert "LoadFromFile" not in fns[name], name
        assert "LoadTextFile(" not in fns[name], name
    # The one file read is the bridge's own record in its workspace folder.
    listed = fns["ProjectListedNames"]
    assert "LoadFromFile" not in listed
    assert listed.count("LoadTextFile(") == 1
    assert "LoadTextFile(Store, WorkspaceDir + PROJECT_LISTED_FILE)" in listed


def test_what_the_file_names_is_recorded_per_file_time(fns):
    body = fns["ProjectListedNames"]
    assert "Age := FileAge(Path);" in body
    # A record whose file time no longer matches is dropped and made again.
    assert "If Copy(Rec, 1, Pos(Chr(2), Rec) - 1) = IntToStr(Age) Then" in body
    assert "Names := ProjectMemberNames(Project);" in body
    # Kept across a restart of the loop, in the bridge's own folder.
    assert "LoadTextFile(Store, WorkspaceDir + PROJECT_LISTED_FILE)" in body
    assert "Store.SaveToFile(WorkspaceDir + PROJECT_LISTED_FILE)" in body


def test_every_open_project_is_recorded_before_a_command():
    main = _PAS.read_text(encoding="utf-8")
    assert "ProjectListedNames(Workspace.DM_Projects(I));" in main
    form = (_PAS.parent / "StatusForm.pas").read_text(encoding="utf-8")
    body = functions(strip_comments(form))["ProcessOne"]
    assert body.index("NoteOpenProjects(0);") < body.index("Case Category Of")


def test_the_project_file_is_saved_when_it_needs_it_and_saving_is_on(fns):
    body = fns["SaveProjectMembers"]
    need = body.index("ProjectFileNeedsSave(Project)")
    held = body.index("If Not SaveHeldBack(Project.DM_ProjectFullPath) Then")
    assert need < held < body.index("ProjectServerDoc.DoFileSave('')")
    assert "ProjectServerDoc.SetModified(True)" in body


def test_still_dirty_counts_the_project_file(fns):
    body = fns["CountDirtyInProject"]
    assert "If ProjectFileNeedsSave(Project) Then Result := Result + 1;" in body


def test_the_project_file_is_measured_with_the_documents(fns):
    body = fns["WorkspaceDocPaths"]
    assert "Path := Project.DM_ProjectFullPath;" in body
