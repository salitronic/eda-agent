# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Proj_Close closes the project it is given, and nothing else.

MEASURED 2026-09-13 on AD 26.10.1.6. proj_close named a scratch project
that had no loaded document, while Blinker555_v4's board was focused.
WorkspaceManager:CloseObject, given the scratch project's full path,
closed Blinker555_v4. The handler saw its target still open and retried,
and the retry closed the scratch project. The reply said success with
``attempts: 2``, and nothing mentioned the project that went.

CloseObject acts on the FOCUSED project. Every earlier close that worked
had a sheet of the named project focused, which is why it looked right.
The reference scripts never close a project by name; they focus it, check
DM_FocusedProject, and close the focused one.

It also explains a report from a managed project: a save prompt listing
49 documents when closing a 10-document project, then a second call that
listed 4 and closed. The first close was aimed at the other project.

Structural, because no Altium runs in the suite. Each check is scoped to
the part of the handler it is about.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


@pytest.fixture(scope="module")
def project():
    return load(_ALTIUM / "Project.pas")


@pytest.fixture(scope="module")
def close(project):
    return project["Proj_Close"]


def _loop(close: str) -> str:
    marker = "While (Not Closed) And (Attempts < 2) Do"
    assert marker in close, "the close loop is gone"
    return close.split(marker, 1)[1]


def test_focus_is_established_before_anything_is_changed(close):
    """A close that has to be refused must leave the project as it was.
    Checked after the save or discard, a refusal would already have
    written the project, or cleared its modified flags."""
    before_any_change = close.split("If SaveFirst Then", 1)[0]
    assert "FocusProjectForClose(Project)" in before_any_change, (
        "the focus check runs after the save or the discard")
    refusal = before_any_change.split("FocusProjectForClose(Project)", 1)[1]
    assert '"closed":false' in refusal
    assert re.search(r"\bExit;", refusal), (
        "a refused close falls through to the save or discard anyway")


def test_every_attempt_is_focused_first(close):
    """Focus can move between attempts, so once up front is not enough."""
    loop = _loop(close)
    assert loop.index("FocusProjectForClose(Project)") < loop.index(
        "'WorkspaceManager:CloseObject'")


def test_the_open_projects_are_compared_around_every_attempt(close):
    loop = _loop(close)
    at = loop.index("'WorkspaceManager:CloseObject'")
    assert "OpenProjectPaths(Workspace)" in loop[:at], (
        "no snapshot of the open projects before the close")
    assert "PathsGoneOtherThan(" in loop[at:], (
        "nothing checks which projects went after the close")


def test_nothing_is_retried_after_another_project_closed(close):
    """The retry is what turned one wrong close into two."""
    loop = _loop(close)
    stop = loop.index("If ClosedInstead <> '' Then Break;")
    assert stop < loop.index("If SaveFirst Then Break;")
    assert stop < loop.index("DiscardProjectMembers(Project)")


def test_success_requires_that_nothing_else_closed(close):
    assert "If Closed And (ClosedInstead = '') Then" in close, (
        "a close that also took another project can still report success")
    lines = [ln for ln in close.splitlines() if '"closed_instead":' in ln]
    # Located, so the checks below cannot pass over lines never found.
    assert len(lines) >= 3, f"closed_instead is not in every reply: {lines}"
    assert any("PipePathsToJsonArray(ClosedInstead)" in ln for ln in lines), (
        "the failure reply does not name the projects that closed")


def test_focusing_only_shows_documents_already_loaded(project):
    body = project["FocusProjectForClose"]
    assert "Client.GetDocumentByPath(Path)" in body
    assert "Client.ShowDocument(ServerDoc)" in body
    # AFTER the show. The same check also runs once up front, so plain
    # containment passes with the re-check deleted and focus assumed.
    assert body.index("Client.ShowDocument(ServerDoc)") < body.rindex(
        "FocusedProjectPathIs(Target)"), (
        "focus is assumed rather than checked after showing a document")
    for opener in ("OpenDocument", "OpenObject"):
        assert opener not in body, (
            f"{opener} loads a member as a free document and focuses nothing")


def test_the_gone_list_skips_the_target_and_ignores_case(project):
    body = project["PathsGoneOtherThan"]
    assert "UpperCase(TargetPath)" in body
    assert "UpperCase(AfterList)" in body


def test_helpers_come_before_the_handler():
    """No forward declarations in DelphiScript."""
    raw = (_ALTIUM / "Project.pas").read_text(encoding="utf-8")
    at = raw.index("Function Proj_Close(")
    for name in ("FocusedProjectPathIs", "OpenProjectPaths",
                 "FocusProjectForClose", "PathsGoneOtherThan"):
        assert raw.index(f"Function {name}(") < at, name
    main = (_ALTIUM / "Main.pas").read_text(encoding="utf-8")
    assert "Function DocFullPath(" in main, "DocFullPath lives in Main.pas"


def test_the_tool_says_so():
    from eda_agent.tools import project as project_tools

    src = inspect.getsource(project_tools)
    doc = src.split("async def proj_close(", 1)[1].split('"""', 2)[1]
    assert "FOCUSED" in doc
    assert "closed_instead" in doc
