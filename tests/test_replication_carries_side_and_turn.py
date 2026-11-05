# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_replicate_layout moves each part by the channel's whole transform.

Retake 07b copied an LED cell (LED on top, its 100 nF cap on the bottom
under it) to the next LED of the ring with move_components=True: the
copy's cap landed on TOP and off its place. The part's side was never
set, and its offset from the anchor went across unturned while the
copied tracks were turned about the anchor.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _move_block() -> str:
    body = load(_PCB)["PCB_ReplicateLayout"]
    start = body.index("If MoveComps Then")
    return body, body[start:body.index("End;", body.index("PCBM_EndModify", start))]


def test_the_side_is_set_before_the_part_is_placed():
    _, block = _move_block()
    side = block.index("CmpDst.Layer := CmpSrc.Layer")
    assert side < block.index("CmpDst.Rotation :="), "flip after rotating undoes it"
    assert side < block.index("MoveByXY("), "flip after placing moves the part"


def test_the_offset_is_turned_like_the_copied_copper():
    body, block = _move_block()
    assert "CmpDst.x :=" not in block, "assigning x leaves the pads behind"
    expected = body[body.index("OffX := CmpSrc.x"):body.index("If MoveComps Then")]
    assert re.search(r"OffX \* Cos\(DRot", expected)
    assert re.search(r"OffY \* Sin\(DRot", expected)
    assert "RotateAroundXY(SrcAnchorX, SrcAnchorY, DRot)" in body
