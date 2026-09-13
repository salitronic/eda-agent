# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Proj_Close's half of close-without-saving: scoped, checked, retried once.

Proj_Close ran WorkspaceManager:CloseObject with modified documents still
modified. Altium raised its save prompt, RunProcess is synchronous, and
the prompt blocked the handler, which blocked the polling loop, which is
the only thing that could have answered it. Every later call waited
until a human clicked. On a 96-document project it wedged every time.

The handler clears THIS project's modified flags before closing and
retries a close without saving once. MEASURED live: gated on
IServerDocument.Modified, which read False while the editor showed the
sheet modified, the clear did nothing and the close prompted. Ungated, on
the next build, the same close raised no prompt and discarded the edit.
If a prompt appears anyway, proj_close answers it, guarded in
tests/test_proj_close_answers_its_own_save_prompt.py. This file guards
the handler half: ungated, scoped to the project, never workspace-wide.

Structural, because no Altium runs in the suite. Every check is scoped to
the part of the handler it is about, since a name also appearing in a
comment or an error message would otherwise satisfy it.
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


def _before_the_close(body: str) -> str:
    marker = "'WorkspaceManager:CloseObject'"
    assert marker in body, "Proj_Close no longer issues CloseObject"
    return body.split(marker, 1)[0]


def test_without_saving_the_edits_are_discarded_before_the_close(close):
    pre = _before_the_close(close)
    arm = re.search(
        r"If SaveFirst Then\s+SaveProjectMembers\(Project\)\s+Else\s+Begin"
        r"(.*?)\bEnd;", pre, re.S)
    assert arm, "save=false has no arm of its own ahead of the close"
    assert "DiscardProjectMembers(Project)" in arm.group(1), (
        "save=false reaches CloseObject with the documents still "
        "modified, which raises a prompt the loop cannot answer")


def test_a_discard_that_did_not_take_is_refused_before_the_close(close):
    """Checked, not trusted: the modified flag does not always propagate,
    and one document left dirty is enough to raise the prompt."""
    pre = _before_the_close(close)
    assert pre.index("DiscardProjectMembers") < pre.index("DirtyProjectMembers")
    refusal = pre.split("DirtyProjectMembers(Project)", 1)[1]
    assert '"closed":false' in refusal
    assert '"still_modified":' in refusal
    assert re.search(r"\bExit;", refusal), (
        "a refusal that falls through still issues the close")


def test_the_retry_is_only_for_a_close_without_saving(close):
    loop = close.split("While (Not Closed) And (Attempts < 2) Do", 1)
    assert len(loop) == 2, "the single retry is gone"
    body = loop[1]
    assert body.index("'WorkspaceManager:CloseObject'") < body.index(
        "If SaveFirst Then Break;"), (
        "a close with save=true must not be retried: the likely cause is "
        "a prompt somebody cancelled on purpose")


def test_the_close_is_still_confirmed_and_still_saves_when_asked(close):
    """The two properties this handler already had must survive."""
    assert "FindProjectByPath(Workspace, ProjectPath) = Nil" in close
    assert "SaveProjectMembers(Project)" in close
    assert "WorkspaceManager:SaveAll" not in close


def test_the_discard_is_scoped_to_the_project(project):
    """Never the workspace. A workspace-wide save once reached a client
    project with 17 documents that nobody had named, and a workspace-wide
    DISCARD would throw that work away instead of writing it."""
    for name in ("DiscardProjectMembers", "DirtyProjectMembers"):
        body = project[name]
        assert "Project.DM_LogicalDocuments" in body
        for wide in ("DM_Projects", "DM_ProjectCount", "GetWorkspace",
                     "DM_FreeDocumentsProject"):
            assert wide not in body, f"{name} reaches the workspace via {wide}"


def test_the_flag_is_cleared_without_trusting_the_read(project):
    """MEASURED: IServerDocument.Modified read False while the editor tab
    showed the sheet as modified, so a clear gated on that read cleared
    nothing at all."""
    clear = project["ClearDocModifiedByPath"]
    assert "Client.GetDocumentByPath(Path)" in clear
    assert "SetModified(False)" in clear
    assert "DocIsModified" not in clear, (
        "the clear is gated on a Modified read measured not to reflect the "
        "editor's own state")


def test_the_dirty_check_reads_the_flag_rather_than_assuming(project):
    assert "DocIsModified(Path)" in project["DirtyProjectMembers"]


def test_helpers_are_defined_before_the_handler_that_calls_them():
    """No forward declarations in DelphiScript, and a call before the
    definition is an uncatchable modal that stops the loop."""
    raw = (_ALTIUM / "Project.pas").read_text(encoding="utf-8")
    at = raw.index("Function Proj_Close(")
    for name in ("ClearDocModifiedByPath", "DiscardProjectMembers",
                 "DirtyProjectMembers", "PipePathsToJsonArray"):
        assert raw.index(f"Function {name}(") < at, name

    build = (_ALTIUM / "build.py").read_text(encoding="utf-8")
    order = re.findall(r"'([A-Za-z_]+\.pas)'", build)
    assert order.index("Main.pas") < order.index("Project.pas"), (
        "DocIsModified lives in Main.pas")
    assert order.index("Utils.pas") < order.index("Project.pas"), (
        "EscapeJsonString lives in Utils.pas")


def test_the_tool_tells_a_caller_how_to_answer_a_prompt_anyway():
    """The reporter found app_press_dialog_button's "works while blocked"
    property only by reading source."""
    from eda_agent.tools import project as project_tools

    src = inspect.getsource(project_tools)
    doc = src.split("async def proj_close(", 1)[1].split('"""', 2)[1]
    assert "app_press_dialog_button" in doc
    assert "save=False" in doc
