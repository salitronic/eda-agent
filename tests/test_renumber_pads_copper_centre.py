# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb.renumber_pads orders pads by their copper centre, not their origin.

A custom pad's copper need not be centred on its origin. Live on AD 25.8.1
a custom pad whose origin sat above the other pads and whose copper ran
below all of them was numbered 1 by lr_tb; by its copper it is last. The
sort now takes the centre of the pad's bounding rectangle (the mask in it
is the same on every side), as library.get_pad_geometry does for
copper_x_mm / copper_y_mm, and the origin only when the rectangle is empty.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_RenumberPads"]


def test_the_sort_keys_are_the_copper_centre():
    body = _body()
    first = body.index("Pad := GrpIter.FirstPCBObject;")
    rect = body.index("PadBR := Pad.BoundingRectangle;", first)
    cx = body.index("CuX := (PadBR.Left + PadBR.Right) Div 2;", rect)
    cy = body.index("CuY := (PadBR.Bottom + PadBR.Top) Div 2;", rect)
    add_x = body.index("Xs.Add(IntToStr(CuX));", max(cx, cy))
    add_y = body.index("Ys.Add(IntToStr(CuY));", add_x)
    assert first < rect < add_x < add_y
    assert "Xs.Add(IntToStr(Pad.X));" not in body
    assert "Ys.Add(IntToStr(Pad.Y));" not in body


def test_an_empty_rectangle_keeps_the_origin():
    body = _body()
    first = body.index("Pad := GrpIter.FirstPCBObject;")
    ox = body.index("CuX := Pad.X;", first)
    oy = body.index("CuY := Pad.Y;", ox)
    guard = body.index(
        "If (PadBR.Right > PadBR.Left) And (PadBR.Top > PadBR.Bottom) Then", oy)
    assert ox < oy < guard < body.index("CuX := (PadBR.Left")
