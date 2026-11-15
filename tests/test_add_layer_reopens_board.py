# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_add_layer saves and reopens the board after the insert, because a
via placed since the board was opened counts on the new layer only once the
board is loaded again."""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb
from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


class _Bridge:
    def __init__(self, answers):
        self.sent = []
        self.answers = answers

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append(command)
        return self.answers.get(command, {"success": True})


def _tool(monkeypatch, answers):
    bridge = _Bridge(answers)
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Server())
    return tools["pcb_add_layer"], bridge


def _added(path):
    return {"success": True, "layer": "Mid-Layer 1", "file_path": str(path),
            "vias_refreshed": 4, "note": "save and reopen"}


def test_the_board_is_saved_closed_and_reopened_after_the_insert(monkeypatch, tmp_path):
    board = tmp_path / "B.PcbDoc"
    board.write_text("x")
    add, bridge = _tool(monkeypatch, {
        "pcb.add_layer": _added(board),
        "application.save_document": {"saved": True},
        "application.close_document": {"closed": True}})
    out = asyncio.run(add("MidLayer1"))
    assert bridge.sent == ["pcb.add_layer", "application.save_document",
                           "application.close_document", "application.run_process",
                           "application.set_active_document"]
    assert out["reopened"] and out["saved"] and out["vias_refreshed"] == 4
    assert "note" not in out


def test_reopen_false_adds_the_layer_and_keeps_the_note(monkeypatch, tmp_path):
    board = tmp_path / "B.PcbDoc"
    board.write_text("x")
    add, bridge = _tool(monkeypatch, {"pcb.add_layer": _added(board)})
    out = asyncio.run(add("MidLayer1", reopen=False))
    assert bridge.sent == ["pcb.add_layer"]
    assert out["note"] and "reopened" not in out


def test_a_board_never_saved_is_left_open_with_the_note(monkeypatch, tmp_path):
    add, bridge = _tool(monkeypatch, {"pcb.add_layer": _added(tmp_path / "PCB1.PcbDoc")})
    out = asyncio.run(add("MidLayer1"))
    assert bridge.sent == ["pcb.add_layer"]
    assert out["success"] and out["reopened"] is False and out["note"]
    assert out["reopen_stage"] == "save"


def test_a_save_that_did_not_land_leaves_the_board_open(monkeypatch, tmp_path):
    board = tmp_path / "B.PcbDoc"
    board.write_text("x")
    add, bridge = _tool(monkeypatch, {
        "pcb.add_layer": _added(board),
        "application.save_document": {"saved": False, "reason": "read-only"}})
    out = asyncio.run(add("MidLayer1"))
    assert bridge.sent == ["pcb.add_layer", "application.save_document"]
    assert out["success"] and out["reopened"] is False and out["saved"] is False
    assert out["reopen_reason"] == "read-only" and out["note"]


def test_a_layer_that_was_not_added_is_not_followed_by_a_save(monkeypatch):
    add, bridge = _tool(monkeypatch, {"pcb.add_layer": {"success": False}})
    asyncio.run(add("MidLayer1"))
    assert bridge.sent == ["pcb.add_layer"]


def test_the_handler_names_the_board_file():
    assert '"file_path":"\' + EscapeJsonString(Board.FileName)' in load(_PCB)["PCB_AddLayer"]
