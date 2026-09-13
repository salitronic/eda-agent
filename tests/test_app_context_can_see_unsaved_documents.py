# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_context's "unsaved" list filters on a key the bridge now sends.

It filtered on ``modified``. App_GetOpenDocuments emitted file_name,
file_path, document_kind and loaded, and nothing else, so the filter
matched no document ever: the list was always empty, the "N documents
have unsaved changes" warning was unreachable, and a session with
pending edits opened by reporting itself clean. app_get_active_document
promised the same key in its docstring and its handler never wrote it
either.

Both directions are guarded here, because either one alone passes while
the pair disagrees: the Pascal has to emit the key, and the Python has
to still be reading that name.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from eda_agent.tools import application as app_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_APPLICATION = _ALTIUM / "Application.pas"
_MAIN = _ALTIUM / "Main.pas"


def _tools():
    registry = ToolRegistry()
    app_mod.register_application_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


class _Bridge:
    """Answers the calls app_context makes, with canned data."""

    def __init__(self, docs):
        self._docs = docs

    def is_altium_running(self):
        return True

    def ping_with_version(self):
        return {"pong": True, "script_version": "test", "altium_version": "26.0"}

    async def send_command_async(self, command, *args, **kwargs):
        if command == "application.get_active_document":
            return {"file_path": "C:\\p\\a.SchDoc", "document_kind": "SCH"}
        if command == "application.get_open_documents":
            return self._docs
        return {}


@pytest.fixture()
def context(monkeypatch):
    """Run app_context over a canned document list."""

    def run(docs):
        monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge(docs))
        monkeypatch.setattr(app_mod, "_bundled_script_version", lambda: "test")
        return asyncio.run(_tools()["app_context"]())

    return run


def test_a_dirty_document_reaches_the_caller(context):
    out = context([
        {"file_path": "C:\\p\\a.SchDoc", "loaded": True, "modified": True},
        {"file_path": "C:\\p\\b.SchDoc", "loaded": True, "modified": False},
    ])
    assert out["unsaved"] == ["C:\\p\\a.SchDoc"]
    assert "unsaved changes" in (out["next_step"] or "")


def test_a_clean_workspace_says_nothing(context):
    """The opposite direction, so the check above cannot pass by way of
    something that reports every document."""
    out = context([
        {"file_path": "C:\\p\\a.SchDoc", "loaded": True, "modified": False},
    ])
    assert out["unsaved"] == []
    assert "unsaved changes" not in (out["next_step"] or "")


def test_a_handler_that_omits_the_key_is_what_this_guards(context):
    """The shipped defect, reproduced: with the key absent the workspace
    reads clean whatever its real state. Nothing asserts a behaviour
    here beyond pinning what the Pascal check below is for."""
    out = context([
        {"file_path": "C:\\p\\a.SchDoc", "loaded": True},
    ])
    assert out["unsaved"] == []


def test_the_handler_emits_the_key_the_filter_reads():
    """The half that was missing. The name is taken from the Python, so
    a rename on either side fails here rather than going quiet."""
    src = inspect.getsource(app_mod)
    context_src = src.split("async def app_context", 1)[1].split(
        "    async def ", 1)[0]
    assert 'd.get("modified")' in context_src, (
        "app_context no longer filters open documents on 'modified'; "
        "this guard is checking for the wrong key")

    body = load(_APPLICATION, minimum=12)["App_GetOpenDocuments"]
    assert '"modified":' in body, (
        "App_GetOpenDocuments does not emit 'modified', so app_context's "
        "unsaved list can never be anything but empty")


def test_the_active_document_emits_what_its_docstring_promises():
    body = load(_APPLICATION, minimum=12)["App_GetActiveDocument"]
    # Three exits: the focused document, then the SCH and PCB server
    # fallbacks. A caller reading the field should not have to know
    # which one answered.
    assert body.count('"modified":') == 3, (
        "app_get_active_document documents a 'modified' field; every "
        "branch that builds the reply has to write it")


def test_dirtiness_is_read_through_one_helper():
    """MarkDocDirtyByPath sets the flag, DocIsModified reads it. One
    definition, so the two cannot drift into disagreeing about what
    counts as unsaved."""
    main = load(_MAIN, minimum=50)
    assert "DocIsModified" in main, "DocIsModified is gone from Main.pas"
    helper = main["DocIsModified"]
    assert "Client.GetDocumentByPath" in helper
    assert "Modified" in helper

    body = load(_APPLICATION, minimum=12)["App_GetOpenDocuments"]
    assert "DocIsModified" in body, (
        "App_GetOpenDocuments is reading the modified flag its own way "
        "instead of through the shared helper")


def test_the_helper_is_defined_before_it_is_called():
    """DelphiScript has no forward declarations, and a call before the
    definition is an uncatchable modal that stops the polling loop. The
    build order is read out of build.py rather than imported: importing
    it runs the lint and rewrites the bundle."""
    text = (_ALTIUM / "build.py").read_text(encoding="utf-8")
    files = re.findall(r"'([A-Za-z_]+\.pas)'", text)
    assert "Main.pas" in files and "Application.pas" in files, (
        "build.py's FILES list no longer parses; the order below would "
        "be checked against nothing")
    assert files.index("Main.pas") < files.index("Application.pas")
