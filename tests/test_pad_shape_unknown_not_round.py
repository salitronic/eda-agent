# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A pad shape the geometry read cannot name is Unknown, not Round.

Reported as Round, an unread or unnamed shape was drawn and checked as a
circle; Unknown makes every reader fall back to the pad's whole box.
"""
from __future__ import annotations

from pathlib import Path

from eda_agent.render.pcb_svg import PcbRenderOptions, _render_pad

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def test_the_geometry_read_starts_from_unknown():
    text = _GENERIC.read_text(encoding="utf-8-sig")
    body = text[text.index("Function Gen_GetPcbGeometry("):]
    at = body.index("Else If Obj.ObjectId = ePadObject Then")
    pad = body[at:at + 900]
    assert "ShapeStr := 'Unknown';" in pad
    assert "ShapeStr := 'Round';" not in pad.split("Try")[0]


def test_the_board_drawing_gives_an_unknown_pad_its_whole_box():
    svg = _render_pad({"x": 0, "y": 0, "x_size": 60, "y_size": 40, "shape": "Unknown"},
                      "#c00", PcbRenderOptions())
    assert '<rect x="-30.0" y="-20.0" width="60.0" height="40.0"' in svg
