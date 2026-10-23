# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""zoom_to_xy on a board must zoom to the point, not to a location marker.

The board branch used to run PCB:Jump with Object=JumpToLocation10. That
object jumps to location MARKER 10; X and Y are not its parameters and were
ignored. With no marker 10 set Altium raised a modal "Location Marker 10
has not been set", which stopped the polling loop until someone pressed OK.

It now fits the view to a square of +-radius mils round x,y through
IPCB_Board.GraphicalView_ZoomOnRect, radius optional with a default.
"""

from __future__ import annotations

from pathlib import Path

GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body() -> str:
    src = GENERIC.read_text(encoding="latin-1")
    start = src.index("Function Gen_ZoomToXY(")
    end = src.index("\nFunction ", start + 1)
    body = src[start:end]
    assert "Board := GetPCBBoardAnywhere" in body, "the board branch was not found"
    return body


def _board_branch() -> str:
    body = _body()
    start = body.index("If Board <> Nil Then")
    return body[start:body.index("Else If SchDoc <> Nil Then", start)]


def test_the_board_branch_does_not_jump_to_a_location_marker():
    branch = _board_branch()
    assert "JumpToLocation" not in branch
    assert "PCB:Jump" not in branch


def test_the_board_branch_zooms_on_a_rectangle_round_the_point():
    branch = _board_branch()
    assert "GraphicalView_ZoomOnRect(Cx - Rad, Cy - Rad, Cx + Rad, Cy + Rad)" in branch
    assert "Cx := MilsToCoordF(StrToFloatDef(XStr, 0))" in branch
    assert "Cy := MilsToCoordF(StrToFloatDef(YStr, 0))" in branch


def test_the_radius_is_optional_and_never_zero():
    body = _body()
    assert "RadStr := ExtractJsonValue(Params, 'radius')" in body
    branch = _board_branch()
    assert "StrToFloatDef(RadStr, 200)" in branch
    assert "If Rad <= 0 Then Rad := MilsToCoordF(200)" in branch
