# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A pin name in the SVG preview is drawn inside its part's body.

The renderer stepped each name OUT along its pin, so every name sat on its
own pin line on top of the pin number. Every IC in a preview looked
cluttered in a way the same sheet placed in Altium is not.
"""

from __future__ import annotations

import re

from eda_agent.design.canvas import SchematicCanvas, Sheet, SymbolInstance
from eda_agent.design.render_svg import (
    RenderOptions,
    _mils_to_svg,
    render_canvas_svg,
)
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin

# A body from x 0 to 600 with one pin leaving each side. Pin x/y is the
# body end of the pin, so IN's wire end is at x -200 and OUT's at x 800.
_IC = SymbolModel(
    lib_path="test.SchLib",
    lib_ref="IC",
    pins=(
        SymbolPin(designator="1", name="IN", x=0, y=0, orientation=2,
                  length=200, electrical_type="input"),
        SymbolPin(designator="2", name="OUT", x=600, y=0, orientation=0,
                  length=200, electrical_type="output"),
    ),
    body_bbox=SymbolBBox(x_min=0, y_min=-100, x_max=600, y_max=100),
)


def _pin_name_texts(svg: str) -> dict[str, tuple[float, str]]:
    """Pin-name text elements (font-size 9) as name -> (x, text-anchor)."""
    found = {}
    for m in re.finditer(
            r'<text x="([\d.]+)" y="[\d.]+" font-size="9"[^>]*'
            r'text-anchor="(\w+)"[^>]*>([^<]+)</text>', svg):
        found[m.group(3)] = (float(m.group(1)), m.group(2))
    return found


def test_pin_names_are_drawn_inside_the_body_growing_inward():
    canvas = SchematicCanvas()
    sheet = Sheet(name="main")
    canvas.add_sheet(sheet)
    canvas.add_instance(SymbolInstance(
        refdes="U1", symbol=_IC, x=2000, y=2000, rotation=0))

    names = _pin_name_texts(render_canvas_svg(canvas))
    assert set(names) == {"IN", "OUT"}, f"pin names not found: {names}"

    options = RenderOptions()
    bb = canvas.instances[0].world_bbox()
    left, _ = _mils_to_svg(bb.x_min, bb.y_min, sheet, options)
    right, _ = _mils_to_svg(bb.x_max, bb.y_min, sheet, options)
    for name, (x, _anchor) in names.items():
        assert left < x < right, (
            f"{name} drawn at x={x}, outside the body ({left} to {right})")
    assert names["IN"][1] == "start", (
        "a left pin's name should grow to the right, into the body")
    assert names["OUT"][1] == "end", (
        "a right pin's name should grow to the left, into the body")
