# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""proj_export_image with document_path prints one named document.

Issue #361. Plain WorkspaceManager:Print opens the print preview on AD
25, so the named export runs a temporary one-output OutJob (a Publish
container) that must be a document of the target's project: it is added,
run, removed and deleted, and the document in front is put back, also
when the run writes nothing.
"""
from __future__ import annotations

import asyncio
import configparser
import os

import pytest

from eda_agent.export import document_print as dp
from eda_agent.tools import project as project_tools

pdfium = pytest.importorskip("pypdfium2")


def _ini(text):
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read_string(text)
    return ini


def _project(tmp_path, docs=("Main.SchDoc", "Board.PcbDoc")):
    prj = tmp_path / "Thing.PrjPcb"
    body = ["[Design]", "Version=1.0", ""]
    for n, d in enumerate(docs, 1):
        (tmp_path / d).write_bytes(b"doc")
        body += [f"[Document{n}]", f"DocumentPath={d}", ""]
    prj.write_bytes("\r\n".join(body).encode("utf-8"))
    return prj


def _tiny_pdf(path, pages=1):
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 100)
    doc.save(str(path))
    doc.close()


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    """Answers like main: the run writes a PDF where the OutJob on disk
    says, unless ``write`` is off; the remove can be told to fail."""

    def __init__(self, front, write=True, pages=1, remove_ok=True):
        self.front = front
        self.write = write
        self.pages = pages
        self.remove_ok = remove_ok
        self.calls = []
        self.outjob_text = ""
        self.in_project = set()
        self.reloading = 0

    async def send_command_async(self, command, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((command, params))
        if command == "application.get_active_document":
            return {"file_path": self.front}
        if command == "application.set_active_document":
            self.front = params["file_path"]
            return {"success": True}
        if command == "project.add_document":
            self.in_project.add(params["document_path"])
            return {"success": True, "project_saved": True}
        if command == "project.run_outjob":
            oj = params["outjob_path"]
            assert oj in self.in_project, "run before the OutJob was added"
            with open(oj, encoding="utf-8") as fh:
                self.outjob_text = fh.read()
            self.front = oj
            if self.write:
                ps = _ini(self.outjob_text)["PublishSettings"]
                _tiny_pdf(os.path.join(ps["OutputBasePath1"],
                                       ps["OutputFileName1"]), self.pages)
            return {"process_issued": True, "output_dir": "",
                    "outjob_active": True}
        if command == "application.close_document":
            return {"success": True}
        if command == "project.get_documents":
            if self.reloading > 0:
                self.reloading -= 1
                raise RuntimeError("PROJECT_NOT_FOUND - Project not found")
            return {"documents": sorted(self.in_project)}
        if command == "project.remove_document":
            if not self.remove_ok:
                return {"removed": False, "in_project_file": True}
            self.in_project.discard(params["document_path"])
            return {"removed": True, "project_saved": True,
                    "in_project_file": False, "success": True}
        raise AssertionError(f"unexpected command {command}")


def _run(monkeypatch, bridge, **kw):
    monkeypatch.setattr(project_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    project_tools.register_project_tools(mcp)
    return asyncio.run(mcp.tools["proj_export_image"](**kw))


# --- the OutJob text -------------------------------------------------------

def test_sheet_outjob_has_one_scoped_output_on_a_silent_pdf_container():
    ini = _ini(dp.build_outjob("Sub\\Main.SchDoc", "sch", "C:\\out",
                               "x.pdf"))
    grp = ini["OutputGroup1"]
    assert grp["OutputMedium1"] == "PDF"
    assert grp["OutputMedium1_Type"] == "Publish"
    assert "OutputMedium2" not in grp and "OutputType2" not in grp
    assert grp["OutputType1"] == "Schematic Print"
    assert grp["OutputDocumentPath1"] == "Sub\\Main.SchDoc"
    assert grp["OutputEnabled1_OutputMedium1"] == "1"
    ps = ini["PublishSettings"]
    assert ps["OutputBasePath1"] == "C:\\out\\"
    assert ps["OutputFileName1"] == "x.pdf"
    assert ps["OpenOutput1"] == "0" and ps["PromptOverwrite1"] == "0"


def test_board_outjob_prints_both_sides_with_its_own_page_settings():
    ini = _ini(dp.build_outjob("Board.PcbDoc", "pcb", "C:\\out", "x.pdf"))
    grp = ini["OutputGroup1"]
    assert grp["OutputType1"] == "PCB Print"
    items = [v for k, v in grp.items() if k.startswith("Configuration1_Item")]
    outs = [i for i in items if "Record=PcbPrintOut" in i]
    assert len(outs) == 2
    assert "Mirror=False" in outs[0] and "Mirror=True" in outs[1]
    assert grp["PageOptions1"].startswith("Record=PageOptions")


def test_outjob_refuses_another_kind_or_a_line_break():
    with pytest.raises(ValueError):
        dp.build_outjob("a.PcbLib", "lib", "C:\\out", "x.pdf")
    with pytest.raises(ValueError):
        dp.build_outjob("a.SchDoc\r\nOutputType2=Gerber", "sch", "C:\\o", "x")


def test_project_is_found_only_when_it_lists_the_document(tmp_path):
    prj = _project(tmp_path)
    assert dp.find_projects(str(tmp_path / "Main.SchDoc")) == [str(prj)]
    (tmp_path / "Loose.SchDoc").write_bytes(b"x")
    assert dp.find_projects(str(tmp_path / "Loose.SchDoc")) == []


# --- the tool --------------------------------------------------------------

def test_a_sheet_exports_and_everything_is_put_back(monkeypatch, tmp_path):
    prj = _project(tmp_path)
    before = prj.read_bytes()
    front = str(tmp_path / "Board.PcbDoc")
    bridge = _Bridge(front)
    out = tmp_path / "sheet.png"
    reply = _run(monkeypatch, bridge, output_path=str(out), format="png",
                 document_path=str(tmp_path / "Main.SchDoc"), dpi=72)
    assert reply["success"] is True, reply
    assert out.is_file() and reply["pages"] == 1 and reply["width_px"] == 200
    order = [c for c, _ in bridge.calls]
    assert order.index("project.add_document") < order.index(
        "project.run_outjob") < order.index("project.remove_document")
    assert order[-1] == "application.get_active_document"
    assert "OutputDocumentPath1=Main.SchDoc" in bridge.outjob_text
    assert reply["restored"] is True and bridge.front == front
    assert reply["outjob_removed"] is True
    assert not list(tmp_path.glob(dp.TEMP_PREFIX + "*"))
    assert reply["project_file_rewritten"] is False
    assert prj.read_bytes() == before


def test_a_board_pdf_keeps_both_pages(monkeypatch, tmp_path):
    _project(tmp_path)
    out = tmp_path / "board.pdf"
    reply = _run(monkeypatch, _Bridge("", pages=2), output_path=str(out),
                 document_path=str(tmp_path / "Board.PcbDoc"))
    assert reply["success"] is True and reply["kind"] == "pcb"
    assert reply["pages"] == 2 and out.read_bytes().startswith(b"%PDF-")


def test_a_run_that_writes_nothing_still_cleans_up(monkeypatch, tmp_path):
    _project(tmp_path)
    front = str(tmp_path / "Board.PcbDoc")
    bridge = _Bridge(front, write=False)
    reply = _run(monkeypatch, bridge, output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / "Main.SchDoc"))
    assert reply["success"] is False
    assert reply["error"] == "EXPORT_FILE_MISSING"
    assert reply["outjob_removed"] is True and bridge.front == front
    assert not list(tmp_path.glob(dp.TEMP_PREFIX + "*"))


def test_an_outjob_the_project_still_lists_is_kept(monkeypatch, tmp_path):
    _project(tmp_path)
    reply = _run(monkeypatch, _Bridge("", remove_ok=False),
                 output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / "Main.SchDoc"))
    assert reply["outjob_removed"] is False
    assert os.path.isfile(reply["outjob_left"])


@pytest.mark.parametrize("doc,error", [
    ("Main.SchLib", "DOCUMENT_KIND_UNSUPPORTED"),
    ("Missing.SchDoc", "DOCUMENT_NOT_FOUND"),
    ("Loose.SchDoc", "PROJECT_NOT_FOUND"),
])
def test_refusals_reach_no_altium_call(monkeypatch, tmp_path, doc, error):
    _project(tmp_path)
    (tmp_path / "Loose.SchDoc").write_bytes(b"x")
    (tmp_path / "Main.SchLib").write_bytes(b"x")
    bridge = _Bridge("")
    reply = _run(monkeypatch, bridge, output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / doc))
    assert reply["error"] == error and bridge.calls == []


def test_two_projects_listing_the_document_must_be_named(monkeypatch,
                                                         tmp_path):
    _project(tmp_path)
    (tmp_path / "Other.PrjPcb").write_bytes(
        (tmp_path / "Thing.PrjPcb").read_bytes())
    reply = _run(monkeypatch, _Bridge(""), output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / "Main.SchDoc"))
    assert reply["error"] == "PROJECT_AMBIGUOUS"
    assert len(reply["candidates"]) == 2


def test_a_rewritten_project_file_is_reported(monkeypatch, tmp_path):
    prj = _project(tmp_path)

    class _Rewrites(_Bridge):
        async def send_command_async(self, command, params=None, timeout=None):
            if command == "project.add_document":
                prj.write_bytes(prj.read_bytes() + b"[Extra]\r\n")
            return await super().send_command_async(command, params, timeout)

    reply = _run(monkeypatch, _Rewrites(""), output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / "Main.SchDoc"))
    assert reply["success"] is True and reply["project_file_rewritten"] is True


def test_the_page_asked_for_is_rendered(monkeypatch, tmp_path):
    _project(tmp_path)
    out = tmp_path / "bottom.png"
    reply = _run(monkeypatch, _Bridge("", pages=2), output_path=str(out),
                 format="png", page=2, dpi=72,
                 document_path=str(tmp_path / "Board.PcbDoc"))
    assert reply["success"] is True and reply["page"] == 2
    reply = _run(monkeypatch, _Bridge("", pages=2), output_path=str(out),
                 format="png", page=3, dpi=72,
                 document_path=str(tmp_path / "Board.PcbDoc"))
    assert reply["success"] is False and reply["error"] == "RENDER_FAILED"


def test_the_tool_waits_for_the_reloaded_project(monkeypatch, tmp_path):
    """Once finish_removal has edited the project file, Altium reloads the
    project; the reply comes only after project calls answer again."""
    import eda_agent.prjpcb as prjpcb

    _project(tmp_path)

    class _Reloads(_Bridge):
        async def send_command_async(self, command, params=None, timeout=None):
            if command == "project.get_documents":
                self.polls += 1
                if self.polls in (2, 3):
                    self.calls.append((command, dict(params or {})))
                    raise RuntimeError("PROJECT_NOT_FOUND")
            return await super().send_command_async(command, params, timeout)

    bridge = _Reloads("")
    bridge.polls = 0
    monkeypatch.setattr(prjpcb, "finish_removal", lambda rem, _dir: {
        **rem, "stale_sections": {"pruned": 1}})
    monkeypatch.setattr("time.sleep", lambda s: None)
    reply = _run(monkeypatch, bridge, output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / "Main.SchDoc"))
    assert reply["success"] is True
    assert reply["project_reloaded"] is True
    assert reply["project_settled"] is True
    assert bridge.polls == 4


def test_no_wait_when_the_project_file_was_not_edited(monkeypatch, tmp_path):
    _project(tmp_path)
    bridge = _Bridge("")
    reply = _run(monkeypatch, bridge, output_path=str(tmp_path / "s.pdf"),
                 document_path=str(tmp_path / "Main.SchDoc"))
    assert "project_settled" not in reply
    assert "project.get_documents" not in [c for c, _ in bridge.calls]
