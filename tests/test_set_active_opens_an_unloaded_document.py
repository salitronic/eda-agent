# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_set_active_document opens a document no editor has loaded yet.

Measured in a demo run: after opening a project, focusing its board
failed with "Document not loaded" because opening a project does not
load its members.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.bridge.exceptions import AltiumError
from eda_agent.tools import application as app_tools


def _tool(monkeypatch, sent, loaded):
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append(command)
            if command == "application.run_process":
                loaded.append(True)
                return {"success": True}
            if not loaded:
                raise AltiumError("Document not loaded: x")
            return {"success": True}

    monkeypatch.setattr(app_tools, "get_bridge", lambda: _Bridge())
    app_tools.register_application_tools(_Mcp())
    return tools["app_set_active_document"]


def test_an_unloaded_file_is_opened_then_focused(monkeypatch, tmp_path):
    board = tmp_path / "B.PcbDoc"
    board.write_text("x")
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent, [])(str(board)))
    assert out["success"] is True and out["opened"] is True
    assert sent == ["application.set_active_document", "application.run_process",
                    "application.set_active_document"]


def test_a_loaded_file_is_only_focused(monkeypatch, tmp_path):
    sent = []
    out = asyncio.run(_tool(monkeypatch, sent, [True])(str(tmp_path / "B.PcbDoc")))
    assert "opened" not in out and sent == ["application.set_active_document"]


def test_a_missing_file_is_not_opened(monkeypatch, tmp_path):
    sent = []
    with pytest.raises(AltiumError):
        asyncio.run(_tool(monkeypatch, sent, [])(str(tmp_path / "none.PcbDoc")))
    assert "application.run_process" not in sent
