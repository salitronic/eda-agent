# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A field called file_path carries a path, not a file name.

MEASURED 2026-09-13 on AD 26.10.1.6: app_get_active_document reported
"CloseSheet.SchDoc" as the file_path of a project member, and
proj_list_documents did the same, because both passed DM_FileName, which is
the file NAME. The active document's ``modified`` was then looked up by that
name, which can never resolve, so it read False for a sheet the editor
showed as modified. And proj_close cannot tell which documents belong to a
project from names alone.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def test_the_active_document_reports_a_path():
    body = load(_ALTIUM / "Application.pas", minimum=12)["App_GetActiveDocument"]
    focused = body.split("DM_FocusedDocument", 1)[1].split("If Data = ''", 1)[0]
    assert "FileName := DocFullPath(Doc);" in focused
    assert "FileName := Doc.DM_FileName;" not in focused


def test_project_documents_report_paths():
    body = load(_ALTIUM / "Project.pas")["Proj_GetDocuments"]
    lines = [ln for ln in body.splitlines() if '"file_path":' in ln]
    # Located first, so the two checks below cannot pass by matching
    # nothing. The first version of this test spelled the needle without
    # the leading comma the source has, and its "not in" half passed on a
    # line it had never found.
    assert len(lines) == 1, f"expected one file_path line, found {lines}"
    assert "EscapeJsonString(DocFullPath(Doc))" in lines[0]
    assert "DM_FileName" not in lines[0]


def test_the_helper_prefers_the_full_path_and_resolves_a_bare_one():
    main = load(_ALTIUM / "Main.pas", minimum=50)
    helper = main["DocFullPath"]
    assert helper.index("DM_FullPath") < helper.index("DM_FileName"), (
        "the file name must be the last resort, not the first choice")
    assert "ResolveLoadedDocPath(P)" in helper
    raw = (_ALTIUM / "Main.pas").read_text(encoding="utf-8")
    assert raw.index("Function ResolveLoadedDocPath(") < raw.index(
        "Function DocFullPath("), "no forward declarations in DelphiScript"
