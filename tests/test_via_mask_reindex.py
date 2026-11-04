# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A via whose mask is written is put back in the board's spatial index (#208).

A via's box includes its solder-mask expansion, written through the pad
cache. Neither the object's modify bracket nor the robot messages re-index
it, so a spatial search by the new bounds found nothing. Measured on
AD 26.11: removing the via from the board and adding it back re-indexes it,
keeps its net, and one Undo restores the old mask.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, kind: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^{kind} {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_helper_takes_the_object_off_the_board_and_back():
    body = _routine("Utils.pas", "Procedure", "ReindexBoardObject")
    remove = body.index("Board.RemovePCBObject(Obj);")
    assert remove < body.index("Board.AddPCBObject(Obj);")


def test_a_generic_mask_write_on_a_via_reindexes_it_after_the_bracket():
    body = _routine("PCBGeneric.pas", "Function", "ProcessPCBBoardObjects")
    modify = body[body.index("If Mode = 'modify' Then"):]
    end_bracket = modify.index("FoundObj.EndModify;")
    call = modify.index("ReindexBoardObject(Board, FoundObj);")
    assert end_bracket < call < modify.index("Inc(TotalMatched);")
    guard = modify[end_bracket:call]
    assert "FoundObj.ObjectId = eViaObject" in guard
    assert "Pos('SolderMask', SetStr) > 0" in guard


def test_the_via_mask_tools_reindex_after_the_write():
    for name in ("PCB_SetViaSoldermaskRelief", "PCB_SetViaTenting"):
        body = _routine("PCB.pas", "Function", name)
        write = body.index("Via.SetState_Cache := Cache;")
        end_modify = body.index("PCBM_EndModify", write)
        call = body.index("ReindexBoardObject(Board, Via);")
        assert write < end_modify < call, name
