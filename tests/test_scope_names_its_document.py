# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A scope names the document a query, modify or delete acts on.

A sheet's path given bare as ``scope`` was answered from whichever sheet
was in front, with every object carrying the other sheet's ``_doc``.
obj_modify and obj_delete take the same argument, so the same slip edits
the wrong sheet. A bare path now means that document (or project), and a
value that is no scope is refused before anything is sent.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.scope import ScopeError, to_compact, to_wire
from eda_agent.tools import generic
from tests.pascal_source import functions, strip_comments

_SHEET = r"C:\Designs\Hub\PD Controller.SchDoc"


@pytest.mark.parametrize("given,expected", [
    ("", "active_doc"),
    ("active_doc", "active_doc"),
    ("project", "project"),
    ("doc:" + _SHEET, "doc:" + _SHEET),
    (_SHEET, "doc:" + _SHEET),
    ("C:/Designs/Hub/USB Hub.SchDoc", "doc:C:/Designs/Hub/USB Hub.SchDoc"),
    (r"C:\Designs\Hub\Hub.PrjPcb", r"project:C:\Designs\Hub\Hub.PrjPcb"),
    ("lib_component:R_0402@2", "lib_component:R_0402@2"),
])
def test_a_scope_reads_as_the_document_it_names(given, expected):
    assert to_compact(given) == expected


@pytest.mark.parametrize("given", ["bogus", "PD Controller", "current",
                                   "PD Controller.SchDoc"])
def test_a_value_that_is_no_scope_is_refused(given):
    with pytest.raises(ScopeError):
        to_compact(given)
    with pytest.raises(ScopeError):
        to_wire(given)


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, params))
        return {"success": True, "objects": [], "count": 0, "matched": 0}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
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
    return tools, bridge


@pytest.mark.parametrize("tool,kw", [
    ("obj_query", {"object_type": "eNetLabel", "properties": "Text"}),
    ("obj_count", {"object_type": "eNetLabel"}),
    ("obj_modify", {"object_type": "eNetLabel", "filter": "Text=A",
                    "set": "Text=B"}),
    ("obj_delete", {"object_type": "eNetLabel", "filter": "Text=A"}),
])
def test_a_bare_path_reaches_the_bridge_as_that_document(monkeypatch, tool, kw):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools[tool](scope=_SHEET, **kw))
    scopes = [p["scope"] for _, p in bridge.sent if "scope" in p]
    assert scopes == [{"type": "doc", "file_path": _SHEET}]


@pytest.mark.parametrize("tool,kw", [
    ("obj_query", {"object_type": "eNetLabel", "properties": "Text"}),
    ("obj_count", {"object_type": "eNetLabel"}),
    ("obj_modify", {"object_type": "eNetLabel", "filter": "Text=A",
                    "set": "Text=B"}),
    ("obj_delete", {"object_type": "eNetLabel", "filter": "Text=A"}),
])
def test_an_unknown_scope_sends_nothing(monkeypatch, tool, kw):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools[tool](scope="PD Controller", **kw))
    assert out["error"] == "INVALID_SCOPE" and not bridge.sent


@pytest.mark.parametrize("tool,kw", [
    ("obj_batch_modify", {"operations": [
        {"scope": _SHEET, "object_type": "eNetLabel", "filter": "Text=A",
         "set": "Text=B"}]}),
    ("obj_batch_delete", {"operations": [
        {"scope": _SHEET, "object_type": "eNetLabel", "filter": "Text=A"}]}),
])
def test_batches_name_the_document_too(monkeypatch, tool, kw):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools[tool](**kw))
    assert "scope=doc:" + _SHEET + ";" in bridge.sent[-1][1]["operations"]
    tools, bridge = _tools(monkeypatch)
    kw["operations"][0]["scope"] = "PD Controller"
    out = asyncio.run(tools[tool](**kw))
    assert out["error"] == "INVALID_SCOPE" and not bridge.sent


_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


@pytest.mark.parametrize("handler", [
    "Gen_QueryObjects", "Gen_ModifyObjects", "Gen_DeleteObjects",
    "Gen_GetObjectCount", "Gen_BatchModify", "Gen_BatchDelete"])
def test_the_bridge_refuses_a_scope_it_does_not_know(handler):
    """The Python check is the first line; the bridge refuses on its own
    so no caller reaches the active document by a misspelt scope."""
    body = functions(strip_comments(_PAS.read_text(encoding="utf-8")))[handler]
    parsed = body.index("ParseScope(Scope, ScopeType, ScopePath);")
    assert body.index("UnknownScopeError(RequestId, Scope, ScopeType)") > parsed


def test_a_create_refuses_a_scope_it_would_ignore(monkeypatch):
    """A create goes on the document in front; a scope naming another
    sheet was dropped without a word."""
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["obj_batch_create"](operations=[
        {"scope": _SHEET, "object_type": "eNetLabel",
         "properties": "Text=A"}]))
    assert out["error"] == "SCOPE_NOT_SUPPORTED" and not bridge.sent
