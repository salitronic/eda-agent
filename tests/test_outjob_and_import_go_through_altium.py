# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An OutJob is read through Altium's OutJob API, and an imported document
is written by Altium, never by the script opening or copying the file.

Measured on AD 26.11.1.5: an OutJob's server document held as an
IWSM_OutputJobDocument lists each container's name, type, output path and
outputs, matching what the OutJob enables; a Reports and a PDF container
then ran into the folders it reported. An import saved a copy Altium wrote,
byte for byte the size of the source, and the source was left as it was.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Project.pas"


def _fns():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))


def test_no_outjob_is_read_as_a_file():
    code = strip_comments(_PAS.read_text(encoding="utf-8"))
    assert "TIniFile" not in code
    for name in ("Proj_GetOutJobContainers", "Proj_RunOutJob"):
        body = _fns()[name]
        assert "OJ.GetState_OutputMediumCount" in body, name
        assert "LoadTextFile(" not in body and "ReadString(" not in body, name


def test_the_container_list_comes_from_the_api():
    body = _fns()["Proj_GetOutJobContainers"]
    for call in ("M.GetState_Name", "M.GetState_TypeString", "M.GetState_OutputPath",
                 "OJ.GetState_MediumOutputer(M, J)", "O.DM_GeneratorName", "O.DM_ViewName"):
        assert call in body, call


def test_a_pdf_container_publishes_and_the_rest_generate():
    body = _fns()["Proj_RunOutJob"]
    pdf = body.index("If UpperCase(ContainerType) = 'PDF' Then")
    assert body.index("'PublishToPDF'", pdf) < body.index("'WorkspaceManager:GenerateReport'", pdf)


def test_an_import_is_written_by_altium_and_never_over_a_file():
    body = _fns()["Proj_ImportDocument"]
    assert "CopyFile(" not in body
    refuse = body.index("'FILE_EXISTS'")
    point = body.index("ServerDoc.SetFileName(DestPath);")
    save = body.index("ServerDoc.DoFileSave('');", point)
    back = body.index("ServerDoc.SetFileName(SourcePath);", save)
    add = body.index("Project.DM_AddSourceDocument(DestPath);", back)
    assert refuse < point < save < back < add


def test_a_board_backup_is_saved_by_altium_before_the_copy():
    pas = functions(strip_comments(
        (_PAS.parent / "PCB.pas").read_text(encoding="utf-8")))
    helper = pas["SaveBoardFileAs"]
    assert "CopyFile(" not in helper
    point = helper.index("Doc.SetFileName(BackupPath);")
    assert helper.index("Doc.DoFileSave('');", point) < helper.index("Doc.SetFileName(Path);", point)
    body = pas["PCB_SaveCopyAs"]
    assert body.index("SaveBoardFileAs(TargetPath, BackupPath)") < body.index("ServerDoc.SetFileName(TargetPath);")
