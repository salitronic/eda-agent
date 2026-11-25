# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint primitive is announced to the library's board, never to the
footprint itself.

On AD 26.1 and 26.10 a PCBM_BoardRegisteration broadcast addressed to the
footprint stopped Altium with an access violation on a blank PcbLib. The
primitive is added to the footprint and to the library's board, and the board
is told of it; that saves, closes and reopens with the primitive kept.
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
        "broadcast(s) in Library.pas; these stop Altium on AD 26.1 and 26.10"
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
