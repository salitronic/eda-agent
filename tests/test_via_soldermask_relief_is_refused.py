# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The via soldermask write is refused, and refused without sending.

Setting SolderMaskExpansion on an IPCB_Via raises an access violation
inside ScriptingSystem.DLL on AD 26.10.1.6. Measured twice on a scratch
board holding three vias and nothing else, once through
``Via.BeginModify`` and once through ``SendMessageToRobots``, identical
both ways. The engine shows a modal before any ``Except`` runs, so the
polling loop stops and the session needs a manual restart, and because
the fault lands between PreProcess and PostProcess it leaves an open
transaction behind it.

Refusing is not the preferred answer anywhere in this bridge. It is
right here only because the operation cannot complete: every caller who
tried it lost their session and changed nothing on the board.

TWO LAYERS, AND THE PYTHON ONE IS THE LOAD-BEARING ONE. Reaching the
handler's refusal means sending the command, and a session running a
deployed script older than 2026.09.10.3 still has the write in it, so
sending is itself the harm. The tool must issue no bridge command at
all.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.metadata import tool_metadata
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


class _LoudBridge:
    """Any bridge call at all is the failure this file is about."""

    def send_command(self, *args, **kwargs):
        raise AssertionError(f"a command was sent: {args!r}")

    async def send_command_async(self, *args, **kwargs):
        raise AssertionError(f"a command was sent: {args!r}")


@pytest.fixture()
def tool(monkeypatch):
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: _LoudBridge())
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    tools = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    return tools["pcb_set_via_soldermask_relief"]


def test_it_refuses_without_touching_the_bridge(tool):
    out = asyncio.run(tool(expansion_mils=4))
    assert out["success"] is False
    assert out["error"] == "NOT_SCRIPTABLE"


def test_the_refusal_names_the_route_that_works(tool):
    """A refusal with no alternative is where a caller starts guessing,
    and the guesses here cost a session each."""
    out = asyncio.run(tool(expansion_mils=4))
    instead = out["instead"]
    assert "Solder Mask Expansion" in instead
    assert "IsVia" in instead
    # The rule authoring tool cannot make this kind, so pointing at it
    # without saying so would send the caller in a circle.
    assert "pcb_create_design_rule cannot" in instead


def test_it_says_what_it_was_asked_to_do(tool):
    """So a transcript shows the intent that was refused, not just the
    refusal."""
    out = asyncio.run(tool(expansion_mils=7, net="GND"))
    assert out["requested"] == {"expansion_mils": 7, "net": "GND"}


def test_it_is_not_advertised_as_a_mutation():
    """The pcb_set_ prefix defaults to "silent", meaning it changes the
    board. A caller filtering for safe operations would read that as the
    opposite of the truth."""
    assert tool_metadata("pcb_set_via_soldermask_relief")["interaction"] == (
        "readonly")


def test_the_handler_refuses_as_well():
    """Second layer, for a caller that reaches the bridge another way
    (tool_invoke, or a raw command)."""
    body = load(_PCB_PAS, minimum=100)["PCB_SetViaSoldermaskRelief"]
    assert "NOT_SCRIPTABLE" in body
    assert "SolderMaskExpansion" not in body, (
        "the handler is attempting the write again")
