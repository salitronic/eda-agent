# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A text frame's (and a note's) Corner is read and written by the generic layer.

SEEN on AD 25.8.1: a generic query of a sheet's text frames asking for
Corner.X / Corner.Y answered '' for every frame, so a frame's size could
not be read, and a modify of Corner.X on one changed nothing. The getter
and setter narrowed only to ISch_Rectangle and ISch_Line. Corner exists
on ISch_TextFrame (Gen_PlaceTextFrame sets it), and a note is a text
frame; both now narrow to a typed ISch_TextFrame local, never through
Obj.Corner on the base interface, which does not compile.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"
_FRAME_TEST = "Else If (Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote) Then"


def _units():
    return load(_GENERIC, minimum=20)


def _corner_branch(body: str) -> str:
    at = body.index("If (PropName = 'Corner.X') Or (PropName = 'Corner.Y') Then")
    end = body.index("PropName = 'CornerXRadius'", at)
    return body[at:end]


def _frame_part(branch: str) -> str:
    at = branch.index(_FRAME_TEST)
    end = branch.index("End", at + len(_FRAME_TEST) + 10)
    return branch[at:end]


def test_the_getter_reads_a_text_frames_corner_through_a_typed_local():
    part = _frame_part(_corner_branch(_units()["GetSchProperty"]))
    assert "FrameObj := Obj;" in part
    assert "Crn := FrameObj.Corner;" in part
    assert "Have := True;" in part


def test_the_setter_writes_both_axes_of_a_text_frames_corner():
    part = _frame_part(_corner_branch(_units()["SetSchProperty"]))
    assert "FrameObj := Obj;" in part
    assert "Crn := FrameObj.Corner;" in part
    assert "Crn.X := MilsToCoordF(StrToFloatDef(Value, 0))" in part
    assert "Crn.Y := MilsToCoordF(StrToFloatDef(Value, 0));" in part
    assert part.index("Crn := FrameObj.Corner;") < part.index("FrameObj.Corner := Crn;")


def test_the_frame_local_is_a_text_frame_in_both():
    for name in ("GetSchProperty", "SetSchProperty"):
        assert "FrameObj : ISch_TextFrame;" in _units()[name], name


def test_corner_is_still_never_asked_of_the_base_interface():
    for name in ("GetSchProperty", "SetSchProperty"):
        branch = _corner_branch(_units()[name])
        assert "Obj.Corner" not in branch.replace("FrameObj.Corner", ""), name


def test_other_types_stay_outside_the_corner_branch():
    for name in ("GetSchProperty", "SetSchProperty"):
        branch = _corner_branch(_units()[name])
        for t in ("eRectangle", "eRoundRectangle", "eLine", "eTextFrame", "eNote"):
            assert "Obj.ObjectId = %s" % t in branch, (name, t)
        for t in ("eLabel", "ePin", "eWire", "eSheetSymbol", "eHarnessConnector",
                  "ePolygon", "eEllipse", "eArc", "eImage"):
            assert "Obj.ObjectId = %s" % t not in branch, (name, t)
