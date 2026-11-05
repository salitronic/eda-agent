# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A written object is found by its new geometry before the write reports (#208).

A via's box includes its solder-mask expansion, written through the pad
cache. Neither the object's modify bracket nor the robot messages re-index
it, so a spatial search by the new bounds found nothing. Measured on
AD 26.11: removing the via from the board and adding it back re-indexes it,
keeps its net, and one Undo restores the old mask.

obj_modify no longer guesses which properties need that: every changed
object is looked up in the index by its own box after the write, and
re-indexed where the index lags.
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


def test_the_index_is_asked_at_the_centre_and_two_opposite_corners():
    body = _routine("Utils.pas", "Function", "BoardIndexState")
    assert "(BR.Left + BR.Right) Div 2" in body
    assert "BoardIndexFinds(Board, Obj, BR.Left, BR.Bottom)" in body
    assert "BoardIndexFinds(Board, Obj, BR.Right, BR.Top)" in body
    finds = _routine("Utils.pas", "Function", "BoardIndexFinds")
    # The same object, not merely one of its type there.
    assert "I_ObjectAddress" in finds and "AddFilter_Area" in finds


def test_a_write_is_judged_against_the_index_before_it():
    body = _routine("Utils.pas", "Function", "EnsureIndexed")
    assert "Before : Integer" in body
    kept = body.index("If (After And Before) = Before Then Exit;")
    owned = body.index("Obj.InComponent Or Obj.InPolygon Or Obj.InDimension")
    group = body.index("eComponentObject")
    reindex = body.index("ReindexBoardObject(Board, Obj);")
    assert kept < owned < reindex and group < reindex
    # Checked again after the re-index, so a fix that did not take is said.
    assert "Result := 3" in body[reindex:]


def test_every_modified_object_is_checked_after_its_bracket():
    body = _routine("PCBGeneric.pas", "Function", "ProcessPCBBoardObjects")
    modify = body[body.index("If Mode = 'modify' Then"):]
    before = modify.index("IndexBefore := BoardIndexState(Board, FoundObj);")
    begin = modify.index("FoundObj.BeginModify;")
    end_bracket = modify.index("FoundObj.EndModify;")
    call = modify.index("EnsureIndexed(Board, FoundObj, IndexBefore)")
    assert before < begin < end_bracket < call < modify.index("Inc(TotalMatched);")
    # Not gated on a property name: every write is checked.
    assert "Pos(" not in modify[end_bracket:call]
    assert '"reindexed":' in modify and '"stale_index":' in modify


def test_the_modify_reply_carries_the_index_outcome():
    body = _routine("PCBGeneric.pas", "Function", "ProcessActivePCBDoc")
    assert "IntToStr(TotalMatched) + IndexJson" in body


def test_the_via_mask_tools_reindex_after_the_write():
    for name in ("PCB_SetViaSoldermaskRelief", "PCB_SetViaTenting"):
        body = _routine("PCB.pas", "Function", name)
        write = body.index("Via.SetState_Cache := Cache;")
        end_modify = body.index("PCBM_EndModify", write)
        call = body.index("ReindexBoardObject(Board, Via);")
        assert write < end_modify < call, name
