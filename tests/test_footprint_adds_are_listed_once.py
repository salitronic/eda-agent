# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A primitive added to the footprint on screen is listed once afterwards.

Every footprint writer adds the new primitive to the footprint and to the
library board, which is what survives a save. Measured on AD 26.10: until
the next footprint switch the footprint's iterator then listed it twice
(12 entries, 11 objects). Making the same footprint current again after the
write left 11 entries, and pads, tracks, arcs, regions, text and a 3D body
added that way all came back after a save and a restart of Altium.
"""

from __future__ import annotations

import re
from pathlib import Path

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"

#: Writers that add to the board and settle the footprint themselves.
SETTLE_THEMSELVES = {
    "redraw_assembly": "puts the footprint that was on screen back at the end",
}


def _text() -> str:
    return LIBRARY.read_text(encoding="utf-8-sig")


def _body(text: str, name: str) -> str:
    start = re.search(rf"^(Function|Procedure) {name}\(", text, re.M).start()
    return text[start:text.index("\nEnd;\n", start)]


def _dispatch(text: str) -> dict[str, str]:
    body = _body(text, "HandleLibraryCommand")
    return dict(re.findall(r"'(\w+)':\s*Result := (Lib_\w+)\(", body))


def _settled(text: str) -> set[str]:
    return set(re.findall(r"Action = '(\w+)'", _body(text, "LibActionAddsToFootprint")))


#: Helpers that add one primitive to the library board for their caller.
_BOARD_ADDERS = ("AttachToCurrentFootprint(", "AddLibVia(", "AddExtrudedBody(")


def _adds_to_board(text: str, handler: str) -> bool:
    body = _body(text, handler)
    return "Board.AddPCBObject(" in body or any(h in body for h in _BOARD_ADDERS)


def test_the_board_adders_do_add_to_the_board():
    text = _text()
    for helper in _BOARD_ADDERS:
        name = helper.rstrip("(")
        assert "Board.AddPCBObject(" in _body(text, name), name


def test_every_writer_that_adds_to_the_board_is_settled():
    text = _text()
    missing = [action for action, handler in _dispatch(text).items()
               if _adds_to_board(text, handler)
               and action not in _settled(text)
               and action not in SETTLE_THEMSELVES]
    assert not missing, (
        "these add to the library board and leave the footprint on screen "
        f"listing the new primitive twice: {missing}")


def test_the_settled_actions_are_real_writers():
    text = _text()
    dispatch = _dispatch(text)
    for action in _settled(text):
        assert action in dispatch, action
        assert _adds_to_board(text, dispatch[action]), action


def test_the_dispatcher_settles_after_the_handler_runs():
    body = _body(_text(), "HandleLibraryCommand")
    settle = body.index("If LibActionAddsToFootprint(Action) Then SettleCurrentFootprint(0);")
    assert body.rindex("Else") < settle < body.index("RestoreFocusedDoc(SavedFocus);")


def test_settling_makes_the_same_footprint_current_again():
    body = _body(_text(), "SettleCurrentFootprint")
    assert "Cur := PcbLib.CurrentComponent" in body
    assert "PcbLib.SetState_CurrentComponent(Cur)" in body


def test_the_self_settling_writer_does_restore_the_footprint():
    body = _body(_text(), "Lib_RedrawAssembly")
    tail = body[body.index("PcbEndEdit(0)"):]
    assert "PcbLib.SetState_CurrentComponent(SavedCurrent)" in tail
