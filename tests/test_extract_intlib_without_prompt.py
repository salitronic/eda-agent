# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""lib_extract_intlib extracts without Altium's "Open Integrated Library" prompt.

The handler used to open the .IntLib as a document (WorkspaceManager:
OpenObject) and then run IntegratedLibrary:ExtractSources. Opening an
.IntLib raises the "Open Integrated Library" modal (Install / Extract /
Import / Cancel), which blocks the polling loop until a person answers
it (issue #321). IntegratedLibraryManager.ExtractSourcesToPath extracts
without asking; measured on AD 25.8.1 it writes the sources under the
names they have INSIDE the .IntLib, which need not be the .IntLib's own
name, so the tool lists the folder for what really appeared.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import library as lib_tools

from tests.pascal_source import load

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body():
    return load(_LIB)["Lib_ExtractIntLib"]


def test_the_intlib_is_never_opened_as_a_document():
    body = _body()
    assert "WorkspaceManager:OpenObject" not in body
    assert "IntegratedLibrary:ExtractSources" not in body


def test_the_library_manager_extracts_into_the_sibling_folder():
    body = _body()
    assert "ExtractDir := ParentDir + BaseName + '\\';" in body
    make = body.index("ForceDirectories(ExtractDir)")
    extract = body.index(
        "IntegratedLibraryManager.ExtractSourcesToPath(IntLibPath, ExtractDir);")
    assert make < extract


def test_a_manager_error_is_reported_not_hidden():
    body = _body()
    assert "Problem := 'the library manager raised an error';" in body
    assert "',\"extracted\":' + BoolToJsonStr(Problem = '')" in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    """Answers like the handler, after writing the given files."""

    def __init__(self, folder, names):
        self.folder = folder
        self.names = names
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        self.folder.mkdir(exist_ok=True)
        for n in self.names:
            (self.folder / n).write_bytes(b"x")
        base = self.folder.name
        return {"intlib_path": params["intlib_path"],
                "extract_dir": str(self.folder) + "\\",
                "extracted": True, "problem": "",
                "sch_lib_path": str(self.folder / (base + ".SchLib")),
                "sch_lib_found": (base + ".SchLib") in self.names,
                "pcb_lib_path": str(self.folder / (base + ".PcbLib")),
                "pcb_lib_found": (base + ".PcbLib") in self.names}


def _extract(monkeypatch, tmp_path, names):
    intlib = tmp_path / "SEAM.IntLib"
    intlib.write_bytes(b"x")
    bridge = _Bridge(tmp_path / "SEAM", names)
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    lib_tools.register_library_tools(mcp)
    out = asyncio.run(mcp.tools["lib_extract_intlib"](intlib_path=str(intlib)))
    return out, bridge.sent


def test_sources_named_inside_the_intlib_are_found(monkeypatch, tmp_path):
    out, sent = _extract(monkeypatch, tmp_path,
                         ["SEAM.SchLib", "SAMTEC_SEAM.PcbLib"])
    assert sent[0][0] == "library.extract_intlib"
    assert Path(out["sch_lib_path"]).name == "SEAM.SchLib"
    assert Path(out["pcb_lib_path"]).name == "SAMTEC_SEAM.PcbLib"
    assert out["sch_lib_found"] is True and out["pcb_lib_found"] is True
    assert out.get("ok") is not False


def test_the_source_named_after_the_intlib_comes_first(monkeypatch, tmp_path):
    out, _ = _extract(monkeypatch, tmp_path,
                      ["A_OTHER.PcbLib", "SEAM.PcbLib", "SEAM.SchLib"])
    assert Path(out["pcb_lib_path"]).name == "SEAM.PcbLib"
    assert [Path(p).name for p in out["pcb_libs"]] == [
        "A_OTHER.PcbLib", "SEAM.PcbLib"]


def test_nothing_extracted_is_a_failure(monkeypatch, tmp_path):
    out, _ = _extract(monkeypatch, tmp_path, [])
    assert out["ok"] is False
    assert "nothing was extracted" in out["reason"]
    assert out["sch_lib_found"] is False and out["pcb_lib_found"] is False
