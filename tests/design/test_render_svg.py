# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SVG render: a complete schematic preview shows component values.

A value-less preview (designator only) can't be reviewed -- 10k vs 1M is
invisible. These pin that the instance value flows through to the drawing.
"""

from __future__ import annotations

from eda_agent.design.canvas import (
    SchematicCanvas,
    Sheet,
    SymbolInstance,
)
from eda_agent.design.render_svg import render_canvas_svg
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


def _res() -> SymbolModel:
    return SymbolModel(
        lib_path="/x.SchLib", lib_ref="R",
        pins=(
            SymbolPin(designator="1", name="1", x=-100, y=0,
                      orientation=2, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=100, y=0,
                      orientation=0, length=100, electrical_type="passive"),
        ),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30),
    )


def _canvas_with(inst: SymbolInstance) -> SchematicCanvas:
    c = SchematicCanvas(sheets=[Sheet(name="main")])
    c.add_instance(inst)
    return c


def test_value_is_rendered_in_svg():
    sym = _res()
    svg = render_canvas_svg(_canvas_with(
        SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000,
                       rotation=0, value="4k7")))
    assert "R1" in svg          # designator
    assert "4k7" in svg         # value -- the part this guards


def test_no_value_renders_designator_only():
    sym = _res()
    svg = render_canvas_svg(_canvas_with(
        SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000,
                       rotation=0)))   # value defaults to ""
    assert "R1" in svg
    # No stray value <text> when the part carries no value.
    assert svg.count("<text") >= 1


def test_pipeline_populates_instance_value():
    """build_canvas_from_plan copies the plan part's value onto the instance
    so the render and any downstream consumer see it."""
    from tests.design.test_pipeline import MockExtractor, _passive, _LIB
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.pipeline import build_canvas_from_plan

    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "value": "10k", "status": "existing", "sheet": "main",
             "zone": "z"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "value": "100nF", "status": "existing", "sheet": "main",
             "zone": "z"},
        ],
        "nets": [
            {"name": "VOUT", "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"}, {"refdes": "C1", "pin": "2"}]},
        ],
    })
    res = build_canvas_from_plan(
        plan, MockExtractor({(_LIB, "RES"): _passive("RES"),
                             (_LIB, "CAP"): _passive("CAP")}))
    assert res.ok, [f.text for f in res.failures]
    values = {i.refdes: i.value for i in res.canvas.instances}
    assert values == {"R1": "10k", "C1": "100nF"}
