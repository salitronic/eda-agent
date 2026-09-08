# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The ruler is the instrument every A/B is judged on, so it is pinned.

It is deliberately NOT the scorer. The scorer is the objective the engine
optimises and changes whenever the engine's priorities do; a session that
rewrote it also compared scores across the two versions and reported an
improvement that was an artifact of the new weights.
"""
from __future__ import annotations

from eda_agent.design.canvas import SchematicCanvas, SymbolInstance, WireSegment
from eda_agent.design.ruler import RULER_VERSION, compare, measure
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin

_LIB = "/fake/lib.SchLib"


def _two_pin(lib_ref: str, vertical: bool) -> SymbolModel:
    if vertical:
        pins = (SymbolPin(designator="1", name="1", x=0, y=100, orientation=1,
                          length=100, electrical_type="passive"),
                SymbolPin(designator="2", name="2", x=0, y=-100, orientation=3,
                          length=100, electrical_type="passive"))
    else:
        pins = (SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                          length=100, electrical_type="passive"),
                SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                          length=100, electrical_type="passive"))
    return SymbolModel(lib_path=_LIB, lib_ref=lib_ref, pins=pins,
                       body_bbox=SymbolBBox(x_min=-50, y_min=-50,
                                            x_max=50, y_max=50))


def _canvas() -> SchematicCanvas:
    cv = SchematicCanvas()
    cv.add_instance(SymbolInstance(refdes="C1", symbol=_two_pin("C", True),
                                   x=1000, y=1000, rotation=0))
    cv.add_instance(SymbolInstance(refdes="C2", symbol=_two_pin("C", True),
                                   x=1000, y=1400, rotation=0))
    cv.add_instance(SymbolInstance(refdes="R1", symbol=_two_pin("R", False),
                                   x=2500, y=2200, rotation=0))
    cv.add_wires([
        WireSegment(x1=0, y1=1000, x2=3000, y2=1000, sheet="main", net="A"),
        WireSegment(x1=1500, y1=0, x2=1500, y2=2000, sheet="main", net="B"),
        WireSegment(x1=2500, y1=2200, x2=2700, y2=2200, sheet="main", net="C"),
    ])
    return cv


def test_the_ruler_reports_its_version():
    out = measure(_canvas())
    assert out["ruler_version"] == RULER_VERSION == 1


def test_known_values_on_a_tiny_canvas():
    """Hand-checkable numbers, so a change to any measure is visible."""
    out = measure(_canvas())
    # C1 and C2 share x=1000; R1 shares nothing.
    assert out["pct_axis_aligned"] == 200.0 / 3
    # C1, C2 upright; R1 on its side.
    assert out["pct_two_pin_upright"] == 200.0 / 3
    assert out["pct_upright_C"] == 100.0
    assert out["pct_upright_R"] == 0.0
    # net A (horizontal, y=1000) crosses net B (vertical, x=1500) once.
    assert out["crossings"] == 1
    assert out["wire_length"] == 3000 + 2000 + 200
    assert out["wire_segments"] == 3
    # two bands: y=1000/1400 (within 400? no, bands split at >200 gaps)
    assert out["parts_per_row"] == 1.0
    assert out["parts_per_col"] == 3 / 2


def test_the_same_canvas_measures_the_same_twice():
    assert measure(_canvas()) == measure(_canvas())


def test_compare_gives_ratios_and_flags_a_zero_human_baseline():
    eng = {"ruler_version": 1, "crossings": 2, "labels": 4, "wire_length": 100}
    hum = {"ruler_version": 1, "crossings": 0, "labels": 2, "wire_length": 50}
    out = compare(eng, hum)
    assert out["labels"] == 2.0 and out["wire_length"] == 2.0
    assert out["crossings"] == float("inf")
    assert "ruler_version" not in out
