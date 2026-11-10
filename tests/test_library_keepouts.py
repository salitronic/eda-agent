# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Footprint tracks, arcs, fills and regions can be keepouts.

The lib_add_footprint_* writers placed plain primitives only, and the
keepout flags (IsKeepout and the five Keepout Restrictions) were reachable
only through the generic layer, which acts on a board. So a footprint whose
drawing asks for copper to be kept out of one signal layer (a "no surface
traces" area between rows of pads) could only get a shape on the Keep-Out
layer, which keeps everything out of every layer. Tracks, arcs, fills and
regions now take keepout and keepout_for, written on the primitive and read
back, and lib_get_footprint_primitives reports them.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.library import (KEEPOUT_RESTRICTIONS, _fills_payload,
                                     _keepout_restrictions, _regions_payload,
                                     _tracks_payload)
from tests.test_footprint_from_package_tools import _pascal_routine
from tests.test_handlers_verify_their_outcome import _decommented

_SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1]]
_FLAGS = {"copper": "IsKeepoutForCopper", "track": "IsKeepoutForTrack",
          "via": "IsKeepoutForVia", "smd_pad": "IsKeepoutForSMDPad",
          "th_pad": "IsKeepoutForTHPad"}
_WRITERS = {"Lib_AddFootprintRegions": "Region", "Lib_AddFootprintTracks": "Track",
            "Lib_AddFootprintFills": "Fill"}
_BAD = [
    ({"keepout_for": ["copper"]}, "needs keepout true"),
    ({"keepout": False, "keepout_for": ["copper"]}, "needs keepout true"),
    ({"keepout": "yes"}, "true or false"),
    ({"keepout": 1}, "true or false"),
    ({"keepout": True, "keepout_for": ["polygon"]}, "one or more of"),
    ({"keepout": True, "keepout_for": []}, "one or more of"),
    ({"keepout": True, "keepout_for": "copper"}, "must be a list"),
]


def _region_op(region):
    payload, problems = _regions_payload([region])
    assert not problems, problems
    return dict(f.split("=", 1) for f in payload.split(";"))


def _payloads(extra):
    """The same keepout asked of a region, a fill and a track."""
    region, rp = _regions_payload([dict(extra, layer="TopLayer", points=_SQUARE)])
    fill, fp = _fills_payload([dict(extra, layer="TopLayer", x1=0, y1=0, x2=1, y2=1)])
    tp: list[str] = []
    track, skipped = _tracks_payload([dict(extra, layer="TopLayer", x1=0, y1=0,
                                           x2=1, y2=0)], "mil", tp)
    return [(region, rp), (fill, fp), (track, tp)], skipped


def test_a_plain_item_sends_no_keepout_fields():
    for payload, problems in _payloads({})[0]:
        assert not problems and payload and "keepout" not in payload


def test_a_copper_only_keepout_goes_out_with_that_restriction_alone():
    for payload, problems in _payloads({"keepout": True,
                                        "keepout_for": ["copper"]})[0]:
        assert not problems
        assert payload.endswith(";keepout=1;keepout_for=copper"), payload


def test_a_keepout_with_no_restrictions_named_keeps_everything_out():
    op = _region_op({"layer": "TopLayer", "points": _SQUARE, "keepout": True})
    assert op["keepout_for"] == ",".join(KEEPOUT_RESTRICTIONS)


def test_restrictions_go_out_in_altiums_order_once_each():
    assert _keepout_restrictions({"keepout": True, "keepout_for": [
        "TH_Pad", "copper", "via", "copper"]}) == ["copper", "via", "th_pad"]


@pytest.mark.parametrize("extra, reason", _BAD)
def test_a_keepout_that_cannot_be_meant_is_refused_not_sent_plain(extra, reason):
    sent, skipped = _payloads(extra)
    for payload, problems in sent:
        assert payload == ""
        assert len(problems) == 1 and reason in problems[0]
    assert skipped == 1


def test_the_package_footprint_call_still_drops_a_bad_track_quietly():
    payload, skipped = _tracks_payload([{"x1": 0, "y1": 0, "x2": 1, "y2": 0,
                                         "keepout": "yes"}])
    assert payload == "" and skipped == 1


def test_the_shared_helpers_write_and_read_back_every_flag():
    apply = _decommented(_pascal_routine("KeepoutApply"))
    assert "Prim.IsKeepout := True;" in apply
    for word, flag in _FLAGS.items():
        assert f"Prim.{flag} := KeepoutListHas(KeepFor, '{word}');" in apply, flag
        assert f"(Prim.{flag} = KeepoutListHas(KeepFor, '{word}'))" in apply, flag
    assert "Result := Prim.IsKeepout" in apply
    request = _decommented(_pascal_routine("KeepoutRequest"))
    # On the Keep-Out layer the object is a keepout unless keepout=0: made
    # without the flag, a footprint region read back with no restrictions
    # (keeping nothing out) until the library was saved and reopened.
    assert "If (Asked = '') And (Layer = eKeepOutLayer) Then Asked := '1';" in request
    assert "If KeepFor = '' Then KeepFor := 'copper,track,via,smd_pad,th_pad';" in request
    assert "If KeepoutListProblem(KeepFor) <> '' Then" in request
    assert "Else If KeepFor <> '' Then" in request
    assert "Wanted := True;" in request
    assert "(Asked <> '1') And (Asked <> '0')" in request


@pytest.mark.parametrize("routine, var", sorted(_WRITERS.items()))
def test_each_batch_writer_checks_first_and_counts_what_reads_back(routine, var):
    body = _decommented(_pascal_routine(routine))
    check = body.index("KeepoutRequest(GetBatchField(Op, 'keepout'), KeepFor, Layer,")
    assert body.index(f"{var} := PCBServer.PCBObjectFactory(") > check
    refusal = body[check:check + 260]
    assert "Inc(BadKeepouts);" in refusal and "Continue;" in refusal
    applied = f"If KeepoutApply({var}, KeepFor) Then Inc(Keepouts)"
    assert applied in body
    # Written before the object is registered.
    assert body.index(applied) < body.index(f"Footprint.AddPCBObject({var});")
    assert "Else Inc(KeepoutFailed);" in body
    for key in ('"keepouts":', '"keepout_failures":', '"bad_keepouts":'):
        assert key in body, key


def test_the_arc_writer_refuses_a_bad_keepout_and_reports_the_read_back():
    body = _decommented(_pascal_routine("Lib_AddFootprintArc"))
    ask = body.index("KeepProblem := KeepoutRequest(ExtractJsonValue(Params, 'keepout')")
    assert body.index("'BAD_KEEPOUT'") > ask
    assert body.index("'BAD_KEEPOUT'") < body.index("Arc := PCBServer.PCBObjectFactory(")
    assert "If KeepWanted Then KeepOk := KeepoutApply(Arc, KeepFor);" in body
    assert "',\"keepout_verified\":' + BoolToJsonStr(KeepOk)" in body
    assert "KeepoutFieldsJson(Arc, Layer)" in body


def test_the_restriction_words_agree_between_python_and_pascal():
    problem = _decommented(_pascal_routine("KeepoutListProblem"))
    fields = _decommented(_pascal_routine("KeepoutFieldsJson"))
    for word, flag in _FLAGS.items():
        assert word in KEEPOUT_RESTRICTIONS
        assert f"(W <> '{word}')" in problem, word
        assert f"If Prim.{flag} Then S := S + ',\"{word}\"';" in fields, flag
    assert "If IsKeep Or (Layer = eKeepOutLayer) Then" in fields


@pytest.mark.parametrize("var", ["Track", "Arc", "FillObj", "Region"])
def test_the_reader_reports_the_keepout_of_every_kind(var):
    body = _decommented(_pascal_routine("Lib_GetFootprintPrimitives"))
    assert f"KeepoutFieldsJson({var}, LayerId)" in body
