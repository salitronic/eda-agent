# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An OutJob copied into a project keeps everything but where it points.

Every user exports differently, so the copy changes only the documents
its outputs read, where its containers write, and its vault identity.
The fixture has the shape measured on Altium's shipped Fabrication.OutJob
and a project OutJob that names its board.
"""
from __future__ import annotations

import asyncio

from eda_agent import outjob
from eda_agent.tools import project

_SRC = (
    "[OutputJobFile]\r\nVersion=1.0\r\nItemGUID={ABC}\r\nRevisionId=7\r\n\r\n"
    "[OutputGroup1]\r\nOutputType1=Gerber\r\nOutputName1=Gerber\r\n"
    "OutputDocumentPath1=Old.PcbDoc\r\nOutputEnabled1=1\r\n"
    "OutputEnabled1_OutputMedium1=1\r\n"
    "Configuration1_Item1=GerberUnit=Metric|NumberOfDecimals=6\r\n"
    "OutputType2=Assembly Drawings\r\nOutputName2=Drawing\r\n"
    "OutputDocumentPath2=Old.PCBDwf\r\nOutputEnabled2=0\r\n"
    "OutputType3=BOM_PartType\r\nOutputName3=BOM\r\n"
    "OutputDocumentPath3=\r\nOutputEnabled3=1\r\n\r\n"
    "[PublishSettings]\r\nOutputFilePath2=Fab.pdf\r\nReleaseManaged2=1\r\n"
    "OutputBasePath2=Project Outputs for Old\r\n"
    "OutputFilePath3=C:\\Old\\Project Outputs for Old\\\r\nReleaseManaged3=1\r\n\r\n"
    "[GeneratedFilesSettings]\r\n"
    "RelativeOutputPath3=C:\\Old\\Project Outputs for Old\\\r\nOpenOutputs3=0\r\n"
).encode("utf-8")

_PRJ = r"C:\New\New.PrjPcb"
_DOCS = [r"C:\New\New.PcbDoc", r"C:\New\A.SchDoc", r"C:\New\B.SchDoc"]
_OUT = r"C:\New\Outputs"


def _cloned():
    data, report = outjob.clone(_SRC, _PRJ, _DOCS, _OUT)
    return data.decode("utf-8"), report


def test_outputs_read_the_new_board():
    text, report = _cloned()
    assert "OutputDocumentPath1=New.PcbDoc\r\n" in text
    assert report["retargeted"] == [{"output": 1, "was": "Old.PcbDoc",
                                     "now": "New.PcbDoc"}]


def test_a_document_the_project_lacks_is_reported_not_guessed():
    text, report = _cloned()
    assert "OutputDocumentPath2=Old.PCBDwf\r\n" in text
    assert [u["output"] for u in report["unmapped"]] == [2]
    assert "OutputDocumentPath3=\r\n" in text, "a whole-project output stays so"


def test_containers_write_to_the_folder_and_are_not_release_managed():
    text, report = _cloned()
    assert "ReleaseManaged1" not in text
    assert "ReleaseManaged2=0\r\n" in text and "ReleaseManaged3=0\r\n" in text
    # Inside the project, so written relative to it, as Altium writes
    # "Project Outputs for <project>".
    assert "OutputFilePath2=Outputs\\Fab.pdf\r\n" in text
    assert "OutputFilePath3=Outputs\\\r\n" in text
    assert "RelativeOutputPath3=Outputs\\\r\n" in text
    assert "OutputBasePath2=Outputs\r\n" in text
    assert report["containers"] == [2, 3]


def test_a_copied_project_writes_beside_itself():
    """07d worked on a copy of 07b's project, and its OutJob kept writing
    into 07b's folder: the paths were absolute."""
    text, _ = _cloned()
    assert "C:\\New" not in text.split("[PublishSettings]", 1)[1]


def test_a_folder_outside_the_project_stays_absolute():
    data, _ = outjob.clone(_SRC, _PRJ, _DOCS, r"D:\Fab\New")
    text = data.decode("utf-8")
    assert "OutputBasePath2=D:\\Fab\\New\r\n" in text
    data, _ = outjob.clone(_SRC, _PRJ, _DOCS, r"C:\Elsewhere\New")
    assert "OutputBasePath2=C:\\Elsewhere\\New\r\n" in data.decode("utf-8")


def test_the_users_settings_are_kept_byte_for_byte():
    text, _ = _cloned()
    for line in ("Configuration1_Item1=GerberUnit=Metric|NumberOfDecimals=6\r\n",
                 "OutputEnabled1_OutputMedium1=1\r\n", "OutputEnabled2=0\r\n",
                 "OpenOutputs3=0\r\n", "Version=1.0\r\n"):
        assert line in text, line
    assert "ItemGUID=\r\n" in text and "RevisionId=\r\n" in text


def test_outputs_are_listed():
    assert outjob.outputs(_SRC) == [
        {"output": 1, "type": "Gerber", "name": "Gerber", "enabled": True},
        {"output": 2, "type": "Assembly Drawings", "name": "Drawing",
         "enabled": False},
        {"output": 3, "type": "BOM_PartType", "name": "BOM", "enabled": True}]


def test_with_no_source_it_asks_and_writes_nothing(monkeypatch, tmp_path):
    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            assert command == "application.get_open_documents", command
            return [{"file_path": r"C:\Mine\Fab.OutJob"},
                    {"file_path": r"C:\Mine\Board.PcbDoc"}]

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(project, "get_bridge", lambda: _Bridge())
    project.register_project_tools(_Mcp())
    out = asyncio.run(tools["proj_create_outjob"](str(tmp_path / "P.PrjPcb")))
    assert out["needs_input"] is True
    assert out["candidates"] == [r"C:\Mine\Fab.OutJob"]
    assert list(tmp_path.iterdir()) == []
