# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An OutJob run says which output is held open instead of meeting
Altium's Error modal.

A take's second OutJob run stopped on an Altium "Error" dialog naming the
Fabrication.pdf the first run had written, still open in a viewer.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys

import pytest

from eda_agent.tools import project

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="file sharing modes are Windows'")


def _tools(bridge, monkeypatch):
    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    project.register_project_tools(_Mcp())
    return tools


def test_a_pdf_held_open_stops_the_run_before_altium_does(tmp_path, monkeypatch):
    out = tmp_path / "Outputs"
    out.mkdir()
    pdf = out / "Fabrication.pdf"
    pdf.write_bytes(b"%PDF")
    outjob = tmp_path / "Fab.OutJob"
    outjob.write_text("[OutputGroup1]\nOutputMedium1=PDF\n\n[PublishSettings]\n"
                      "OutputBasePath1=Outputs\n", encoding="utf-8")
    sent = []

    class _B:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append(command)
            return {}

    k = ctypes.windll.kernel32
    k.CreateFileW.restype = ctypes.c_void_p
    handle = k.CreateFileW(str(pdf), 0x80000000, 1, None, 3, 0x80, None)
    try:
        got = asyncio.run(_tools(_B(), monkeypatch)["proj_run_outjob"](
            "PDF", outjob_path=str(outjob)))
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))
    assert got["success"] is False and got["files_in_use"] == [str(pdf)]
    assert "project.run_outjob" not in sent
