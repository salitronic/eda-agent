# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Writes Altium accepts and then drops are reported, not counted as done.

MEASURED 2026-11-06 on AD 26.11: a zero-length track handed to
pcb_place_tracks was counted as placed and was not on the board, and a
parameter's GraphicallyLocked read back true and came back false after a
save and reopen, on a SchLib symbol and on a sheet alike.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import generic

PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _routine(name: str) -> str:
    code = PCB.read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code).start()
    return code[start:start + re.search(r"(?m)^End;", code[start:]).end()]


def test_a_zero_length_track_is_not_counted_as_placed():
    body = _routine("PCB_PlaceTracks")
    guard = body.index("(MilsToCoordF(TX1) = MilsToCoordF(TX2))")
    assert guard < body.index("PCBObjectFactory(eTrackObject")
    assert "Inc(ZeroLength);" in body[guard:body.index("PCBObjectFactory(eTrackObject")]
    assert "'\"zero_length\":'" in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    async def send_command_async(self, command, params=None, timeout=None):
        return {"matched": 1, "success": True}

    def send_command(self, command, params=None, timeout=None):
        return {"rules": []}


def _modify(monkeypatch, **kwargs):
    monkeypatch.setattr(generic, "get_bridge", lambda: _Bridge())
    mcp = _Mcp()
    generic.register_generic_tools(mcp)
    return asyncio.run(mcp.tools["obj_modify"](**kwargs))


def test_a_parameter_lock_write_says_it_is_not_saved(monkeypatch):
    out = _modify(monkeypatch, object_type="eParameter", filter="Name=Comment",
                  set="ShowName=true|GraphicallyLocked=true")
    assert "does not survive" in out["note"]


def test_other_parameter_writes_carry_no_note(monkeypatch):
    out = _modify(monkeypatch, object_type="eParameter", filter="Name=Comment",
                  set="ShowName=true")
    assert "note" not in out
