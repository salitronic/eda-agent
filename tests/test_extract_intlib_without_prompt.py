# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""lib_extract_intlib extracts without Altium's "Open Integrated Library" prompt.

Opening an .IntLib as a document raises that modal (Install / Extract /
Import / Cancel), which blocks the polling loop until a person answers it.
IntegratedLibraryManager.ExtractSourcesToPath extracts without asking, and
writes the sources under the names they have INSIDE the .IntLib, which need
not be the .IntLib's own name, so the script lists the folder for what
really appeared.
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


def test_the_script_lists_the_folder_for_what_appeared():
    body = _body()
    assert "ExtractedLibsJson(ExtractDir, '*.SchLib', BaseName, Chosen)" in body
    assert "ExtractedLibsJson(ExtractDir, '*.PcbLib', BaseName, Chosen)" in body
    assert "',\"sch_libs\":' + SchLibsJson" in body
    assert "',\"pcb_libs\":' + PcbLibsJson" in body


def test_the_source_named_after_the_intlib_is_preferred():
    lister = load(_LIB)["ExtractedLibsJson"]
    assert "FindFiles(Dir, Mask, 63, False, Found)" in lister
    named = lister.index("= UpperCase(BaseName) Then")
    first = lister.index("Chosen := Files[0];")
    assert named < first


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self, reply):
        self.reply = reply
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return dict(self.reply)


def _extract(monkeypatch, reply):
    bridge = _Bridge(reply)
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    lib_tools.register_library_tools(mcp)
    out = asyncio.run(mcp.tools["lib_extract_intlib"](intlib_path=r"C:\l\SEAM.IntLib"))
    return out, bridge.sent


def test_the_reply_is_passed_on(monkeypatch):
    reply = {"extract_dir": r"C:\l\SEAM" + "\\", "extracted": True, "problem": "",
             "sch_lib_path": r"C:\l\SEAM\SEAM.SchLib", "sch_lib_found": True,
             "pcb_lib_path": r"C:\l\SEAM\V_SEAM.PcbLib", "pcb_lib_found": True,
             "sch_libs": [r"C:\l\SEAM\SEAM.SchLib"],
             "pcb_libs": [r"C:\l\SEAM\V_SEAM.PcbLib"]}
    out, sent = _extract(monkeypatch, reply)
    assert sent[0][0] == "library.extract_intlib"
    assert out["pcb_lib_path"].endswith("V_SEAM.PcbLib")
    assert out.get("ok") is not False


def test_nothing_extracted_is_a_failure(monkeypatch):
    out, _ = _extract(monkeypatch, {
        "extract_dir": r"C:\l\SEAM" + "\\", "extracted": False,
        "problem": "the library manager raised an error",
        "sch_lib_found": False, "pcb_lib_found": False,
        "sch_libs": [], "pcb_libs": []})
    assert out["ok"] is False
    assert "nothing was extracted" in out["reason"]
    assert "raised an error" in out["reason"]
