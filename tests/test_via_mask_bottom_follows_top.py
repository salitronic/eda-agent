# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A lone via mask expansion goes to both sides (follow-up to issue #61).

SolderMaskExpansion is the TOP value once a pad or via has separate mask
expansions, and the bottom value is kept while they are linked.
set_pad_properties already writes bottom = top when only mask_expansion
is given. lib_set_via_properties and the board's generic SolderMaskExpansion
did not: on a via whose sides were separate they wrote the top and left
the old bottom behind. Both now let the bottom follow unless the caller
gave it a value of its own.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _lib_via():
    return _flat(load(_ALTIUM / "Library.pas")["Lib_SetViaProperties"])


def _apply_set():
    return _flat(load(_ALTIUM / "PCBGeneric.pas", minimum=20)["ApplySetPropertiesPCB"])


def _mask_block(body: str) -> str:
    """The `If MaskStr <> '' Then Begin ... End;` block that writes the top."""
    start = body.index("If MaskStr <> '' Then Begin PadCache.SolderMaskExpansionValid := eCacheManual;")
    return body[start:body.index("End;", start)]


def test_the_library_via_bottom_takes_the_top_when_no_bottom_is_given():
    block = _mask_block(_lib_via())
    assert ("If MaskBotStr = '' Then "
            "PadCache.SolderMaskBottomExpansion := PadCache.SolderMaskExpansion;") in block
    # The copy follows the top write, so it copies the new value.
    assert block.index("PadCache.SolderMaskExpansion := CoordFromUnits(") < \
        block.index("PadCache.SolderMaskBottomExpansion := PadCache.SolderMaskExpansion;")


def test_an_explicit_library_bottom_still_wins_and_lands_before_the_write():
    body = _lib_via()
    copy = body.index("PadCache.SolderMaskBottomExpansion := PadCache.SolderMaskExpansion;")
    explicit = body.index("If MaskBotStr <> '' Then PadCache.SolderMaskBottomExpansion := "
                          "CoordFromUnits(StrToFloatDef(MaskBotStr, 0), UnitsStr);")
    write = body.index("FoundPad.SetState_Cache := PadCache;", copy)
    assert copy < explicit < write


def test_the_followed_library_bottom_is_read_back():
    body = _lib_via()
    write = body.index("FoundPad.SetState_Cache := PadCache;",
                       body.index("PadCache.SolderMaskBottomExpansion := PadCache.SolderMaskExpansion;"))
    check = body.index(
        "If (MaskStr <> '') And (MaskBotStr = '') Then "
        "If Abs(PadCache.SolderMaskBottomExpansion "
        "- CoordFromUnits(StrToFloatDef(MaskStr, 0), UnitsStr)) > Tol Then Ok := False;")
    assert check > write


def test_the_board_bottom_follows_a_lone_generic_expansion():
    body = _apply_set()
    applied = body.index("SetPCBProperty(Obj, PropName, PropValue);")
    follow = body.index(
        "If (PropName = 'SolderMaskExpansion') And IsFloatStr(PropValue) "
        "And (Pos('SolderMaskBottomExpansion=', SetStr) = 0) Then Begin "
        "Try PCBSetMask(Obj, 'SolderMaskBottomExpansion', PropValue); Except End; End;")
    # Inside the per-assignment loop, after the top value was written.
    assert applied < follow < body.rindex("End;")
    assert body.count("PCBSetMask(Obj, 'SolderMaskBottomExpansion', PropValue)") == 1
