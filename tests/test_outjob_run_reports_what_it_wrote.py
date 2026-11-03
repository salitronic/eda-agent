# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An OutJob run is a success when it wrote a file, not when it ran.

proj_run_outjob returned ``success: true`` with an ``output_dir`` built
from the container's configured path, for a BOM container bound to a
managed release that wrote nothing. The directory did not exist. The
false success surfaced much later as "the CSV is missing".

The handler now reports only that the process was issued, and the three
Python tools that run containers decide success from the output
directory, through one shared check so they cannot disagree.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import asyncio
import pytest

from eda_agent.tools import project as project_tools

from tests.pascal_source import load

_PROJECT_PAS = (Path(__file__).resolve().parents[1] / "scripts" / "altium"
                / "Project.pas")


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    """Answers the OutJob commands, optionally writing a file mid-run."""

    def __init__(self, payload, writes=None):
        self.payload = payload
        self.writes = writes or []

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "project.get_outjob_containers":
            return {"containers": [{"name": "BOM"}]}
        for path in self.writes:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("Designator,Qty\n", encoding="utf-8")
        return dict(self.payload)


@pytest.fixture()
def tools(monkeypatch):
    def build(bridge):
        monkeypatch.setattr(project_tools, "get_bridge", lambda: bridge)
        mcp = _Mcp()
        project_tools.register_project_tools(mcp)
        return mcp.tools
    return build


def _payload(output_dir, **extra):
    return {"process_issued": True, "container_name": "BOM",
            "container_type": "GeneratedFiles",
            "relative_path": "Project Outputs for Board",
            "output_dir": str(output_dir), **extra}


def _run(tools, bridge, **kwargs):
    return asyncio.run(tools(bridge)["proj_run_outjob"](
        container_name="BOM", **kwargs))


def test_the_reported_case_a_directory_that_was_never_created(tmp_path, tools):
    missing = tmp_path / "Project Outputs for Board"
    out = _run(tools, _Bridge(_payload(missing)))
    assert out["success"] is False
    assert out["output_dir_exists"] is False
    assert out["files_written"] == 0
    assert str(missing) in out["reason"]


def test_a_stale_file_is_not_this_run_s_output(tmp_path, tools):
    folder = tmp_path / "out"
    folder.mkdir()
    old = folder / "BOM.csv"
    old.write_text("old", encoding="utf-8")
    an_hour_ago = time.time() - 3600
    os.utime(old, (an_hour_ago, an_hour_ago))

    out = _run(tools, _Bridge(_payload(folder)))
    assert out["success"] is False
    assert out["output_dir_exists"] is True
    assert "no file under" in out["reason"]


def test_a_file_written_by_the_run_is_success(tmp_path, tools):
    folder = tmp_path / "out"
    written = folder / "BOM.csv"
    out = _run(tools, _Bridge(_payload(folder), writes=[written]))
    assert out["success"] is True
    assert out["files_written"] == 1
    assert out["written"] == [str(written)]
    assert "reason" not in out


def test_a_container_with_no_output_path_is_not_a_success(tools):
    out = _run(tools, _Bridge(_payload("")))
    assert out["success"] is False
    assert "no output path" in out["reason"]


def test_an_old_deployed_script_saying_success_is_not_believed(tmp_path, tools):
    """A session still running the previous script sends success:true.
    The Python side decides, so that must not leak through."""
    missing = tmp_path / "nowhere"
    out = _run(tools, _Bridge(_payload(missing, success=True)))
    assert out["success"] is False


def test_an_error_reply_passes_through_untouched(tools):
    reply = {"error": "CONTAINER_NOT_FOUND"}
    out = _run(tools, _Bridge(reply))
    assert out == reply


def test_run_all_judges_each_container_by_what_it_wrote(tmp_path, tools):
    missing = tmp_path / "nowhere"
    out = asyncio.run(tools(_Bridge(_payload(missing, success=True)))[
        "proj_run_outjob_all"]())
    assert out["ok"] is False
    assert out["containers_with_output"] == 0
    assert out["results"][0]["ok"] is False
    assert out["results"][0]["reason"]

    folder = tmp_path / "out"
    written = folder / "BOM.csv"
    out = asyncio.run(tools(_Bridge(_payload(folder), writes=[written]))[
        "proj_run_outjob_all"]())
    assert out["ok"] is True
    assert out["results"][0]["ok"] is True
    assert out["results"][0]["files_written"] == 1


def test_a_fab_package_with_no_files_is_not_ok(tmp_path, tools):
    missing = tmp_path / "nowhere"
    out = asyncio.run(tools(_Bridge(_payload(missing, success=True)))[
        "proj_generate_fab_package"]())
    assert out["ok"] is False
    assert out["all_files"] == []

    folder = tmp_path / "out"
    written = folder / "Gerbers" / "board.GTL"
    out = asyncio.run(tools(_Bridge(_payload(folder), writes=[written]))[
        "proj_generate_fab_package"]())
    assert out["ok"] is True
    assert out["all_files"] == [str(written)]


def test_the_handler_no_longer_claims_an_outcome_it_cannot_see():
    body = load(_PROJECT_PAS)["Proj_RunOutJob"]
    assert '"success":true' not in body, (
        "Proj_RunOutJob asserts success again; it cannot know whether the "
        "container wrote anything")
    assert '"process_issued":true' in body


# 2026-10-21: a container whose OutJob was not the ACTIVE document wrote
# nothing and the reason blamed the output switches; and a run that wrote
# a full Gerber set then raised an error dialog came back as a bare
# failure. The OutJob is now made active first, and a dialog is judged
# against the output folder like any other run.

_OUTJOB = r"""[OutputGroup1]
Name=Fab
OutputMedium1=Fabrication Files
OutputMedium1_Type=GeneratedFiles
OutputMedium2=Docs
OutputMedium2_Type=Publish

[PublishSettings]
OutputBasePath1=Project Outputs\GERBER
OutputBasePath2=Project Outputs\PDF
"""


class _DialogBridge:
    """Writes the files, then the call is blocked by an error dialog."""

    def __init__(self, writes):
        self.writes = writes

    async def send_command_async(self, command, params=None, timeout=None):
        from eda_agent.bridge.exceptions import AltiumTimeoutError

        if command == "project.get_outjob_containers":
            return {"containers": [{"name": "Fabrication Files"},
                                   {"name": "Docs"}]}
        for path in self.writes:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("G04*", encoding="utf-8")
        raise AltiumTimeoutError(
            "Altium is showing a modal dialog 27s into this call",
            details={"dialogs": {
                "summary": "Altium is BLOCKED by a modal. error: 'Error'",
                "dialogs": [{"message": [
                    "Object reference not set to an instance of an object"]}]}})


def _outjob(tmp_path):
    p = tmp_path / "Board.OutJob"
    p.write_text(_OUTJOB, encoding="utf-8")
    return p


def test_an_inactive_outjob_is_named_as_the_cause(tmp_path, tools):
    folder = tmp_path / "out"
    folder.mkdir()
    out = _run(tools, _Bridge(_payload(folder, outjob_active=False)))
    assert out["success"] is False
    assert "not the active document" in out["reason"]
    assert "switched off" not in out["reason"]


def test_files_written_before_a_dialog_are_reported(tmp_path, tools):
    outjob = _outjob(tmp_path)
    gerber = tmp_path / "Project Outputs" / "GERBER" / "board.GTL"
    out = asyncio.run(tools(_DialogBridge([gerber]))["proj_run_outjob"](
        container_name="Fabrication Files", outjob_path=str(outjob)))
    assert out["success"] is True
    assert out["files_written"] == 1
    assert out["written"] == [str(gerber)]
    assert "Object reference" in " ".join(out["dialog_message"])
    assert "dialog" in out["reason"]


def test_a_dialog_with_nothing_written_is_a_failure(tmp_path, tools):
    outjob = _outjob(tmp_path)
    out = asyncio.run(tools(_DialogBridge([]))["proj_run_outjob"](
        container_name="Fabrication Files", outjob_path=str(outjob)))
    assert out["success"] is False
    assert "Nothing new" in out["reason"]


def test_the_output_folder_is_read_per_medium(tmp_path, tools):
    """OutputBasePathJ belongs to the J-th medium, as the handler reads it."""
    outjob = _outjob(tmp_path)
    pdf = tmp_path / "Project Outputs" / "PDF" / "board.pdf"
    out = asyncio.run(tools(_DialogBridge([pdf]))["proj_run_outjob"](
        container_name="Docs", outjob_path=str(outjob)))
    assert out["written"] == [str(pdf)]


def test_run_all_stops_at_a_dialog(tmp_path, tools):
    outjob = _outjob(tmp_path)
    gerber = tmp_path / "Project Outputs" / "GERBER" / "board.GTL"
    out = asyncio.run(tools(_DialogBridge([gerber]))["proj_run_outjob_all"](
        outjob_path=str(outjob)))
    assert [r["container"] for r in out["results"]] == ["Fabrication Files"]
    assert out["not_run"] == ["Docs"]
    assert out["results"][0]["ok"] is True
    assert "dialog" in out["results"][0]["reason"]

    out = asyncio.run(tools(_DialogBridge([gerber]))[
        "proj_generate_fab_package"](outjob_path=str(outjob)))
    assert out["ok"] is False
    assert out["not_run"] == ["Docs"]
    assert out["all_files"] == [str(gerber)]


def test_the_handler_makes_the_outjob_active_before_running():
    body = load(_PROJECT_PAS)["Proj_RunOutJob"]
    show = body.index("Client.ShowDocument(OutJobDoc)")
    run = body.index("RunProcess('WorkspaceManager:GenerateReport')")
    assert show < run
    assert body.index("Client.ShowDocument(OutJobDoc)") < body.index(
        "RunProcess('WorkspaceManager:Print')")
    assert '"outjob_active":' in body


def test_a_run_that_raised_after_writing_reports_both(tmp_path, tools):
    """The NC Drill step raised after its files were out; the set is
    reported, with the error, rather than the script stopping."""
    folder = tmp_path / "out"
    written = folder / "board.DRL"
    out = _run(tools, _Bridge(_payload(folder, run_raised=True),
                              writes=[written]))
    assert out["success"] is True
    assert out["written"] == [str(written)]
    assert "raised an error" in out["reason"]


def test_a_run_that_raised_and_wrote_nothing_fails(tmp_path, tools):
    out = _run(tools, _Bridge(_payload(tmp_path / "out", run_raised=True)))
    assert out["success"] is False
    assert "Nothing new" in out["reason"]


def test_the_handler_catches_an_error_from_the_run():
    body = load(_PROJECT_PAS)["Proj_RunOutJob"]
    try_at = body.index("RunRaised := False;")
    for process in ("RunProcess('WorkspaceManager:GenerateReport')",
                    "RunProcess('WorkspaceManager:Print')"):
        assert try_at < body.index(process) < body.index("RunRaised := True;")
    assert '"run_raised":' in body


def test_the_handler_closes_outjobs_an_earlier_run_opened():
    """Only OutJobs the bridge opened are closed, never the one about to
    run, and never one with unsaved changes."""
    body = load(_PROJECT_PAS)["Proj_RunOutJob"]
    assert body.index("CloseRunOutJobs(OutJobPath)") < body.index(
        "RunProcess('WorkspaceManager:OpenObject')")
    assert "RunOutJobsOpened := RunOutJobsOpened + '|' + OutJobPath" in body
    closer = load(_PROJECT_PAS)["CloseRunOutJobs"]
    assert "UpperCase(Path) = UpperCase(Keep)" in closer
    assert "Doc.Modified" in closer and "DocWasEdited(Path)" in closer
    assert closer.index("If Dirty Then Continue;") < closer.index(
        "Client.CloseDocument(Doc)")
