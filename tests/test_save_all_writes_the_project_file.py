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
    assert "Result := ProjDoc.Modified;" in body
    assert "If Not LoadTextFile(Lines, Path) Then Exit;" in body
    assert "InDocument := UpperCase(Copy(Line, 1, 9)) = '[DOCUMENT';" in body
    # A renamed sheet's new name, or an added document, is not listed.
    assert "If Listed.IndexOf(UpperCase(ExtractFileName(Entry))) < 0 Then" in body
    # ActiveBOM.VirtualBOM is a member with no file.
    assert "If (Entry = '') Or (Not FileExists(Entry)) Then Continue;" in body


def test_stale_entries_do_not_keep_a_project_dirty(fns):
    # Altium leaves removed documents' sections in the file after a save,
    # so an entry naming a missing file would flag the project forever.
    assert "Not FileExists(Entry)) And" not in fns["ProjectFileNeedsSave"]


def test_the_file_is_read_without_load_from_file(fns):
    assert "LoadFromFile" not in fns["ProjectFileNeedsSave"]


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
