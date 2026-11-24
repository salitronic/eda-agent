# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""PcbLib primitive registration must not broadcast PCBM_BoardRegisteration on
Footprint.I_ObjectAddress (issue #289).

On Altium Designer 26.1.1.7 and 26.10.1.5, calling
``PCBServer.SendMessageToRobots(Footprint.I_ObjectAddress, c_Broadcast,
PCBM_BoardRegisteration, Prim.I_ObjectAddress)`` raises a null-read access
violation modal inside ``ScriptingSystem.DLL``. Adding the primitive to both
the footprint and backing board plus broadcasting ``PCBM_BoardRegisteration``
on ``PcbLib.Board.I_ObjectAddress`` / ``Board.I_ObjectAddress`` persists
cleanly across save/close/reopen without crashing.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import strip_comments

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "altium" / "Library.pas"


def _code() -> str:
    return strip_comments(_LIB.read_text(encoding="utf-8", errors="replace"))


def test_no_footprint_board_registration_robot_call_remains():
    code = _code()
    hits = re.findall(
        r"SendMessageToRobots\s*\(\s*Footprint\.I_ObjectAddress\s*,"
        r"\s*c_Broadcast\s*,\s*PCBM_BoardRegisteration",
        code,
        re.IGNORECASE,
    )
    assert not hits, (
        f"Found {len(hits)} Footprint.I_ObjectAddress PCBM_BoardRegisteration "
        "broadcast(s) in Library.pas; these fault ScriptingSystem.DLL on "
        "AD 26.1.1 / 26.10.1 (issue #289)"
    )


def test_board_level_registration_is_preserved_in_library():
    code = _code()
    board_regs = re.findall(
        r"SendMessageToRobots\s*\(\s*(?:PcbLib\.)?Board\.I_ObjectAddress\s*,"
        r"\s*c_Broadcast\s*,\s*PCBM_BoardRegisteration",
        code,
        re.IGNORECASE,
    )
    assert len(board_regs) >= 12, (
        f"Expected at least 12 Board-level PCBM_BoardRegisteration calls in "
        f"Library.pas, found {len(board_regs)}"
    )
