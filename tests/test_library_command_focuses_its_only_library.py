# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A library command refused because the other kind of library is in front
focuses the one library of its kind and runs again.

The demo agent focused libraries by hand before each call; a library
focused early shows its last part until the next exists. With the tools
bringing their own library forward, the ones that did not (a silkscreen
arc, a pad property) were refused with NO_PCBLIB.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.bridge.altium_bridge import AltiumBridge
from eda_agent.bridge.exceptions import raise_for_code


def _bridge(focused, open_docs):
    b = object.__new__(AltiumBridge)
    b.calls = []
    state = {"focused": focused}

    async def direct(command, params=None, timeout=None):
        b.calls.append(command)
        if command == "application.get_open_documents":
            return [{"file_path": p} for p in open_docs]
        if command == "application.set_active_document":
            state["focused"] = params["file_path"]
            return {"success": True}
        if command.startswith("library."):
            if not state["focused"].lower().endswith(".pcblib"):
                raise_for_code("NO_PCBLIB", "No PCB library is active")
            return {"success": True}
        if command.startswith("pcb."):
            raise_for_code("NO_PCBLIB", "No PCB library is active")
        raise AssertionError(command)

    b.send_command_direct = direct
    return b, state


def test_the_only_pcblib_is_focused_and_the_command_runs_again():
    b, state = _bridge(r"C:\l\P.SchLib", [r"C:\l\P.SchLib", r"C:\l\P.PcbLib"])
    assert asyncio.run(b.send_command_async("library.add_footprint_arc", {})) == {"success": True}
    assert state["focused"] == r"C:\l\P.PcbLib"
    assert b.calls == ["library.add_footprint_arc", "application.get_open_documents",
                       "application.set_active_document", "library.add_footprint_arc"]


def test_two_open_pcblibs_leave_the_refusal_standing():
    b, _ = _bridge(r"C:\l\P.SchLib", [r"C:\a\A.PcbLib", r"C:\b\B.PcbLib"])
    with pytest.raises(Exception, match="No PCB library"):
        asyncio.run(b.send_command_async("library.add_footprint_arc", {}))


def test_other_commands_are_not_retried():
    b, _ = _bridge(r"C:\l\P.SchLib", [r"C:\l\P.PcbLib"])
    with pytest.raises(Exception, match="No PCB library"):
        asyncio.run(b.send_command_async("pcb.something", {}))
    assert b.calls == ["pcb.something"]
