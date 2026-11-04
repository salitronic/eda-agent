# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A text's Rotation and any primitive's Locked can be written.

Both were read by the generic layer but had no writer: a designator or
comment text could not be turned, and a lock read on every primitive could
not be set, so obj_modify reported both names as unknown. The members are
the ones the bridge already writes elsewhere: IPCB_Text.Rotation in
PCB_PlaceText and the layout copy, Moveable in PCB_LockNetRouting.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _setter() -> str:
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)["SetPCBProperty"]


def _branch(body: str, name: str) -> str:
    """The text of one property branch, up to the next property branch."""
    start = body.index(f"PropName = '{name}'")
    nxt = re.search(r"Else If \(?PropName = '", body[start + 1:])
    return body[start:start + 1 + nxt.start()] if nxt else body[start:]


def test_a_text_rotation_is_written_on_the_text():
    branch = _branch(_setter(), "Rotation")
    assert re.search(r"Oid\s*=\s*eTextObject\s+Then\s+Begin\s+Txt\s*:=\s*Obj;"
                     r"\s*Txt\.Rotation\s*:=\s*StrToFloatDef\(Value", branch), branch
    # The component and pad writers are kept, and other types still refuse.
    assert "Comp.Rotation :=" in branch and "Pad.Rotation :=" in branch
    assert "Matched := False" in branch


def test_locked_is_written_as_the_inverse_of_moveable():
    body = _setter()
    assert re.search(r"PropName\s*=\s*'Locked'\s+Then\s+Obj\.Moveable\s*:=\s*"
                     r"Not\s+StrToBool\(Value\)", body), "no Locked writer"


def test_locked_is_read_the_same_way_it_is_written():
    reader = load(_ALTIUM / "PCBGeneric.pas", minimum=20)["GetPCBProperty"]
    assert re.search(r"PropName\s*=\s*'Locked'.*?Not\s+Obj\.Moveable", reader, re.S)
