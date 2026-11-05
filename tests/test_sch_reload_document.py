# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sch_reload_document saves, closes, reopens and recompiles one sheet, and
stops at the first step that did not take."""
from __future__ import annotations

import asyncio

from eda_agent.bridge.exceptions import AltiumError
from eda_agent.tools import generic


class _Bridge:
    def __init__(self, answers):
        self.sent = []
        self.answers = answers

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append(command)
        answer = self.answers.get(command, {"success": True})
        if isinstance(answer, Exception):
            raise answer
        return answer


def _tool(monkeypatch, answers):
    bridge = _Bridge(answers)
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Server())
    return tools["sch_reload_document"], bridge


def test_a_sheet_is_saved_closed_reopened_and_recompiled_in_that_order(monkeypatch, tmp_path):
    sheet = tmp_path / "A.SchDoc"
    sheet.write_text("x")
    reload, bridge = _tool(monkeypatch, {
        "application.save_document": {"saved": True},
        "application.close_document": {"closed": True},
        "project.force_recompile": {"recompiled": True}})
    out = asyncio.run(reload(str(sheet)))
    assert out["ok"] and out["saved"] and out["reopened"]
    assert bridge.sent == ["application.save_document", "application.close_document",
                           "application.run_process", "application.set_active_document",
                           "project.force_recompile"]


def test_a_save_that_did_not_land_leaves_the_sheet_open(monkeypatch, tmp_path):
    sheet = tmp_path / "A.SchDoc"
    sheet.write_text("x")
    reload, bridge = _tool(monkeypatch, {
        "application.save_document": {"saved": False, "reason": "a command is active"}})
    out = asyncio.run(reload(str(sheet)))
    assert out["ok"] is False and out["stage"] == "save"
    assert out["reason"] == "a command is active"
    assert bridge.sent == ["application.save_document"]


def test_without_save_a_modified_sheet_is_refused_at_the_close(monkeypatch, tmp_path):
    sheet = tmp_path / "A.SchDoc"
    sheet.write_text("x")
    reload, bridge = _tool(monkeypatch, {
        "application.close_document": AltiumError("the document reads modified")})
    out = asyncio.run(reload(str(sheet), save=False))
    assert out["ok"] is False and out["stage"] == "close" and out["saved"] is False
    assert bridge.sent == ["application.close_document"]


def test_only_a_schematic_is_taken(monkeypatch):
    reload, bridge = _tool(monkeypatch, {})
    assert asyncio.run(reload(r"C:\x\B.PcbDoc"))["ok"] is False and bridge.sent == []
