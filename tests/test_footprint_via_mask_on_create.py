# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""library.add_footprint_vias creates each via with its solder mask.

A via farm under a pad, or a ring round a mounting hole, wants every via
tented or opened the same way. Without mask fields on the creator, each
via was made plain and then edited with one set_via_properties call (two,
counting the unlink some Altium versions need): 44 vias took ~26 s.
These guards hold the optional fields on the batch op, the order they are
written in (the order set_via_properties measured), their read-back, and
the refusal of an op whose mask field is not what it may hold.
"""

from __future__ import annotations

import asyncio

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src
from tests.test_library_and_board_reads_writes_2026_10_23 import _tools


def _fn(func: str) -> str:
    return _decommented(_body(_src("Library.pas"), func))


def test_the_tool_sends_the_mask_fields_on_each_via():
    tools, sent, mp = _tools("library")
    try:
        asyncio.run(tools["lib_add_footprint_vias"](
            [{"x": 0, "y": 0, "tenting_top": True, "tenting_bottom": False,
              "mask_from_hole_edge": True, "mask_separate": True,
              "mask_expansion": 0.05, "mask_bottom_expansion": -0.1}],
            units="mm", size=0.5, hole_size=0.25))
    finally:
        mp.undo()
    op = sent[-1][1]["vias"]
    for part in ("tenting_top=true", "tenting_bottom=false",
                 "mask_from_hole_edge=true", "mask_separate=true",
                 "mask_expansion=0.05", "mask_bottom_expansion=-0.1"):
        assert part in op, part


def test_a_via_without_mask_fields_is_sent_as_before():
    tools, sent, mp = _tools("library")
    try:
        asyncio.run(tools["lib_add_footprint_vias"](
            [{"x": 1, "y": 2}], units="mm", size=0.6, hole_size=0.3))
    finally:
        mp.undo()
    assert sent[-1][1]["vias"] == "x=1;y=2;size=0.6;hole_size=0.3"


def test_a_bad_mask_field_is_refused_before_sending():
    tools, sent, mp = _tools("library")
    try:
        got = asyncio.run(tools["lib_add_footprint_vias"](
            [{"x": 0, "y": 0, "tenting_top": "yes"},
             {"x": 1, "y": 0, "mask_expansion": "wide"},
             {"x": 2, "y": 0}], units="mm", size=0.5, hole_size=0.25))
    finally:
        mp.undo()
    assert len(got["refused"]) == 2
    assert sent[-1][1]["vias"] == "x=2;y=0;size=0.5;hole_size=0.25"


def test_the_handler_refuses_an_op_with_a_bad_mask_field_before_making_it():
    body = _fn("Lib_AddFootprintVias")
    check = body.index("If ViaMaskOpBad(Op) Then NumbersOk := False;")
    assert check < body.index("Via := AddLibVia(")
    bad = _fn("ViaMaskOpBad")
    for key in ("'tenting_top'", "'tenting_bottom'", "'mask_from_hole_edge'",
                "'mask_separate'"):
        assert key in bad, key
    assert bad.count("(F <> 'true') And (F <> 'false')") == 4
    assert bad.count("Not IsFloatStr(F)") == 2


def test_the_mask_is_written_only_when_asked_and_counted():
    body = _fn("Lib_AddFootprintVias")
    made = body.index("Via := AddLibVia(")
    given = body.index("If ViaMaskFieldsGiven(Op) Then", made)
    assert body.index("ApplyViaMaskOp(Via, Op, UnitsStr)", given) > given
    assert "If Fails = 0 Then Inc(Masked)" in body
    assert "PropFailed := PropFailed + Fails" in body
    assert '\',"masked":\'' in body and '\',"property_failures":\'' in body


def test_the_mask_is_written_in_the_measured_order_and_read_back():
    body = _fn("ApplyViaMaskOp")
    edge = body.index("Via.SolderMaskExpansionFromHoleEdge :=")
    cache = body.index("ViaPad.SetState_Cache := Cache;")
    top = body.index("Via.SetState_IsTenting_Top(")
    bot = body.index("Via.SetState_IsTenting_Bottom(")
    # hole edge before the value whose meaning it changes; tenting last,
    # on a mask the cache has already made manual (#84)
    assert edge < cache < top < bot
    assert body.index("Cache.SolderMaskExpansionValid := eCacheManual;") < cache
    # the bottom follows the top unless it has a value of its own
    assert ("If MaskBot = '' Then\n                    "
            "Cache.SolderMaskBottomExpansion := Cache.SolderMaskExpansion;"
            in body.replace("\r\n", "\n"))
    # every field read back
    assert body.index("Cache := ViaPad.GetState_Cache;", cache) > cache
    for read in ("Via.SolderMaskExpansionFromHoleEdge <> (Edge = 'true')",
                 "Cache.UseSeparateExpansions <> (Sep = 'true')",
                 "Via.GetState_IsTenting_Top <> (TentTop = 'true')",
                 "Via.GetState_IsTenting_Bottom <> (TentBot = 'true')"):
        assert read in body, read
    assert body.index("PCBM_BeginModify") < edge
    assert body.index("PCBM_EndModify") > bot
