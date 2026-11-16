# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_export_image with document_path prints one named sheet or board
through a temporary OutJob that never enters the project's folder or file."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

from eda_agent.export import document_print as dp
from eda_agent.tools import project
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


class _Bridge:
    """Altium for one export: the project lists the document, and a run of
    the OutJob writes the PDF the OutJob names."""

    def __init__(self, projects, pages=1, write=True, front="C:\\x\\Front.SchDoc"):
        self.sent, self.projects = [], projects
        self.pages, self.write, self.front = pages, write, front
        self.outjob_text = ""

    async def send_command_async(self, command, params=None, **kwargs):
        params = params or {}
        self.sent.append((command, dict(params)))
        if command == "project.get_open_projects":
            return {"projects": [{"project_path": p} for p in self.projects]}
        if command == "project.get_documents":
            return [{"file_path": d} for d in self.projects[params["project_path"]]]
        if command == "application.get_active_document":
            return {"file_path": self.front}
        if command == "project.add_document":
            self.outjob_text = Path(params["document_path"]).read_text(encoding="utf-8")
            return {"success": True, "project_saved": False}
        if command == "project.run_outjob":
            if self.write:
                from PIL import Image
                target = re.search(r"^OutputFilePath1=(.*)$", self.outjob_text, re.M).group(1)
                sheets = [Image.new("RGB", (200, 100), "white") for _ in range(self.pages)]
                sheets[0].save(target.strip(), "PDF", save_all=True, append_images=sheets[1:])
            return {"process_issued": True}
        if command == "project.remove_document":
            return {"removed": True}
        return {"success": True}

    def commands(self):
        return [c for c, _ in self.sent]


def _tool(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    from eda_agent import config
    monkeypatch.setattr(config.get_config(), "workspace_dir", tmp_path / "ws", raising=False)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    project.register_project_tools(_Server())
    return tools["proj_export_image"]


def _design(tmp_path):
    folder = tmp_path / "design"
    folder.mkdir()
    sheet, board, prj = folder / "A.SchDoc", folder / "B.PcbDoc", folder / "P.PrjPcb"
    for f in (sheet, board, prj):
        f.write_text("x")
    return sheet, board, prj


def test_a_sheet_is_printed_and_the_project_folder_is_left_as_it_was(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(sheet), str(board)]})
    export = _tool(monkeypatch, bridge, tmp_path)
    before = sorted(os.listdir(prj.parent))
    out = tmp_path / "a.pdf"
    reply = asyncio.run(export(str(out), document_path=str(sheet)))
    assert reply["success"] and reply["pages"] == 1 and reply["kind"] == "sch"
    assert out.read_bytes().startswith(b"%PDF-")
    assert sorted(os.listdir(prj.parent)) == before
    assert reply["outjob_removed"] and reply["restored_front"]
    assert not os.listdir(tmp_path / "ws" / "export_staging")


def test_the_outjob_is_added_and_removed_without_saving_the_project(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(sheet)]})
    export = _tool(monkeypatch, bridge, tmp_path)
    asyncio.run(export(str(tmp_path / "a.pdf"), document_path=str(sheet)))
    sent = dict(bridge.sent)
    assert sent["project.add_document"]["save_project"] == "false"
    assert sent["project.remove_document"]["save_project"] == "false"
    order = bridge.commands()
    assert (order.index("project.add_document") < order.index("project.run_outjob")
            < order.index("application.close_document")
            < order.index("project.remove_document")
            < order.index("application.set_active_document"))
    # The OutJob lives in the workspace and names the document relative to
    # the project: a full path stops Altium on "Missing file".
    outjob = sent["project.add_document"]["document_path"]
    assert str(tmp_path / "ws") in outjob
    assert "OutputDocumentPath1=A.SchDoc\n" in bridge.outjob_text


def test_a_board_page_is_drawn_as_a_picture(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(board)]}, pages=2)
    export = _tool(monkeypatch, bridge, tmp_path)
    out = tmp_path / "b.png"
    reply = asyncio.run(export(str(out), format="png", document_path=str(board),
                               dpi=72, page=2))
    assert reply["success"] and reply["pages"] == 2 and reply["page"] == 2
    assert reply["width_px"] == 200 and out.read_bytes().startswith(b"\x89PNG")
    assert "OutputType1=PCB Print" in bridge.outjob_text


def test_a_page_the_pdf_lacks_is_refused(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(sheet)]})
    export = _tool(monkeypatch, bridge, tmp_path)
    reply = asyncio.run(export(str(tmp_path / "a.png"), format="png",
                               document_path=str(sheet), page=3))
    assert reply["success"] is False and reply["error"] == "RENDER_FAILED"
    assert "1..1" in reply["details"]


def test_a_run_that_writes_nothing_says_so_and_still_cleans_up(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(sheet)]}, write=False)
    export = _tool(monkeypatch, bridge, tmp_path)
    reply = asyncio.run(export(str(tmp_path / "a.pdf"), document_path=str(sheet)))
    assert reply["success"] is False and reply["error"] == "EXPORT_FILE_MISSING"
    assert "project.remove_document" in bridge.commands()
    assert reply["outjob_removed"]


def test_the_project_is_asked_of_altium_and_must_be_one(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    other = tmp_path / "design" / "Q.PrjPcb"
    export = _tool(monkeypatch, _Bridge({}), tmp_path)
    reply = asyncio.run(export(str(tmp_path / "a.pdf"), document_path=str(sheet)))
    assert reply["error"] == "PROJECT_NOT_FOUND"

    both = _Bridge({str(prj): [str(sheet)], str(other): [str(sheet)]})
    export = _tool(monkeypatch, both, tmp_path)
    reply = asyncio.run(export(str(tmp_path / "a.pdf"), document_path=str(sheet)))
    assert reply["error"] == "PROJECT_AMBIGUOUS" and len(reply["candidates"]) == 2
    assert "project.add_document" not in both.commands()
    reply = asyncio.run(export(str(tmp_path / "a.pdf"), document_path=str(sheet),
                               project_path=str(other)))
    assert reply["success"] and reply["project_path"] == str(other)


@pytest.mark.parametrize("kwargs, error", [
    ({"format": "gif"}, "IMAGE_FORMAT_UNSUPPORTED"),
    ({"document_path": "C:\\x\\lib.PcbLib"}, "DOCUMENT_KIND_UNSUPPORTED"),
    ({"document_path": "C:\\nowhere\\A.SchDoc"}, "DOCUMENT_NOT_FOUND"),
    ({"dpi": 5}, "BAD_DPI"),
    ({"page": 0}, "BAD_PAGE"),
])
def test_a_bad_request_is_refused_before_altium_is_asked(monkeypatch, tmp_path, kwargs, error):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(sheet)]})
    export = _tool(monkeypatch, bridge, tmp_path)
    args = {"document_path": str(sheet), **kwargs}
    reply = asyncio.run(export(str(tmp_path / "a.pdf"), **args))
    assert reply["error"] == error and bridge.sent == []


def test_an_output_folder_that_is_not_there_is_refused(monkeypatch, tmp_path):
    sheet, board, prj = _design(tmp_path)
    bridge = _Bridge({str(prj): [str(sheet)]})
    export = _tool(monkeypatch, bridge, tmp_path)
    reply = asyncio.run(export(str(tmp_path / "none" / "a.pdf"), document_path=str(sheet)))
    assert reply["error"] == "OUTPUT_DIR_MISSING" and bridge.sent == []


def test_the_outjob_text_holds_one_scoped_output_and_no_line_break_gets_in():
    text = dp.build_outjob("sub\\A.SchDoc", "sch", "C:\\out", "a.pdf")
    assert text.count("OutputType1=") == 1 and "OutputType2" not in text
    assert "OutputFilePath1=C:\\out\\a.pdf\r\n" in text
    assert "OpenOutput1=0" in text and "PromptOverwrite1=0" in text
    with pytest.raises(ValueError):
        dp.build_outjob("A.SchDoc\r\nOutputType2=x", "sch", "C:\\out", "a.pdf")
    assert dp.relative_to_project("C:\\p\\sub\\A.SchDoc", "C:\\p\\P.PrjPcb") == "sub\\A.SchDoc"
    assert dp.relative_to_project("D:\\A.SchDoc", "C:\\p\\P.PrjPcb") == "D:\\A.SchDoc"


def test_add_document_can_leave_the_project_unsaved():
    body = load(_ALTIUM / "Project.pas")["Proj_AddDocument"]
    skip = body.index("'save_project')) = 'false'")
    assert skip < body.index("SaveProjectFile(Project)")


def test_a_run_is_checked_against_its_outjob_not_the_document_in_front():
    body = load(_ALTIUM / "StatusForm.pas")["CommandTarget"]
    assert (body.index("'document_path'") < body.index("'outjob_path'")
            < body.index("'project_path'"))
