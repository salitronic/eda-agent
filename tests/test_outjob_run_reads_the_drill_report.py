# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A run whose drill report lists holes at the same point is not a success.

MEASURED 2026-10-30: after 279 via holes were resized, the NC drill file
held 5 holes while the Gerbers, DRC and object counts all looked right.
Only the drill report said "Two holes at same point : 279". Every run is
judged by reading the reports it wrote, so that board cannot go out as a
success.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import project as project_tools


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    """Runs the container by writing the given files into the output folder."""

    def __init__(self, out_dir, files):
        self.out_dir = out_dir
        self.files = files

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "project.get_outjob_containers":
            return {"containers": [{"name": "Fabrication"}]}
        for rel, text in self.files.items():
            path = Path(self.out_dir) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="latin-1")
        return {"process_issued": True, "container_name": "Fabrication",
                "container_type": "GeneratedFiles",
                "output_dir": str(self.out_dir)}


@pytest.fixture()
def run(monkeypatch, tmp_path):
    def go(files):
        bridge = _Bridge(tmp_path / "Outputs", files)
        monkeypatch.setattr(project_tools, "get_bridge", lambda: bridge)
        mcp = _Mcp()
        project_tools.register_project_tools(mcp)
        return asyncio.run(mcp.tools["proj_run_outjob"](container_name="Fabrication"))
    return go


_CLEAN = ("ASCII RoundHoles File : Board-RoundHoles.TXT\n"
          "Totals                                         364\n")
_DUPLICATES = _CLEAN + "Two holes at same point : 279\n"


def test_the_measured_report_fails_the_run(run):
    out = run({"NC Drill/Board.DRR": _DUPLICATES,
               "NC Drill/Board-RoundHoles.TXT": "M48\n"})
    assert out["success"] is False
    assert out["drill_duplicates"][0]["count"] == 279
    assert "share a point" in out["reason"] and "reopen" in out["reason"]


def test_a_clean_report_leaves_the_run_a_success(run):
    out = run({"NC Drill/Board.DRR": _CLEAN,
               "NC Drill/Board-RoundHoles.TXT": "M48\n"})
    assert out["success"] is True
    assert "drill_duplicates" not in out


def test_a_run_without_a_drill_report_is_judged_as_before(run):
    out = run({"Gerber/Board.GTL": "G04*\n"})
    assert out["success"] is True
