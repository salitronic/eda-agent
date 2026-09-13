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
