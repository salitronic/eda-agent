# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_add_sheet adds to a project it can name, and says so only when
the project lists the sheet.

With a SchLib in front, the focused project is the one free documents
live in. It has no file, so the sheet path came out as a bare name, the
sheet stayed unsaved outside every project, and the reply still said
``added_to_project: true`` because the add call raised nothing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic
from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body() -> str:
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))["Gen_AddSheet"]


def test_a_project_without_a_file_is_refused_before_anything_is_made():
    body = _body()
    guard = body.index("If Not LooksAbsolutePath(Project.DM_ProjectFullPath) Then")
    assert guard < body.index("Client.OpenNewDocument(")
    assert "'NO_PROJECT'" in body[guard:body.index("Client.OpenNewDocument(")]


def test_added_is_read_back_from_the_project():
    body = _body()
    add = body.index("Project.DM_AddSourceDocument(NewDocPath);")
    readback = body.index("Added := ProjectListsDocument(Project, NewDocPath);")
    assert add < readback
    assert "Added := True" not in body
    assert "\"success\":' + BoolToJsonStr(Saved And Added)" in body


def test_the_reply_names_the_project():
    assert '"project_path":"' in _body()


def test_a_named_project_is_used_instead_of_the_focused_one():
    body = _body()
    assert "FindProjectByPath(Workspace, ProjectPath)" in body
    assert "'PROJECT_NOT_OPEN'" in body


def test_the_tool_sends_the_project_only_when_given(monkeypatch):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(generic, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(Mcp())
    asyncio.run(tools["proj_add_sheet"](name="A"))
    asyncio.run(tools["proj_add_sheet"](name="B", project_path=r"C:\p\P.PrjPcb"))
    assert sent == [("generic.add_sheet", {"name": "A"}),
                    ("generic.add_sheet",
                     {"name": "B", "project_path": r"C:\p\P.PrjPcb"})]
