# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The three human-law features the scorer exposes for a fitted model.

Measured over 176 public sheets, engine against human: 2-pin rail passives
on their side (100% vs 2%), wire runs over 1000 mils (10% of segments vs
none), parts per horizontal band (1.57 vs 2.5). None of the six original
Bradley-Terry features can express any of them, so a model fitted on the
six could not learn what separates the two sides.

They carry no weight in the hand-tuned score. What is pinned here is that
they are computed correctly, that ``raw_features`` carries all of them, and
that a model file written before they existed still scores exactly as it
did.
"""
from __future__ import annotations

import json

import pytest

from eda_agent.design.canvas import SchematicCanvas, SymbolInstance, WireSegment
from eda_agent.design.plan import DesignPlan
from eda_agent.design.quality import (
    LayoutScore,
    _apply_learned_model,
    _count_shunt_on_side,
    _row_bands_per_part,
    raw_features,
    reset_model_cache,
    score_canvas,
)
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


def _plan() -> DesignPlan:
    """C1 decoupling (VCC/GND), R1 series signal, J1 a 2-pin power header."""
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": k, "lib_path": _LIB}
                  for r, k in (("U1", "IC"), ("C1", "C"), ("R1", "R"),
                               ("J1", "HDR"))],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"},
                      {"refdes": "J1", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"},
                      {"refdes": "J1", "pin": "2"}]},
            {"name": "A", "pins": [{"refdes": "U1", "pin": "3"},
                                   {"refdes": "R1", "pin": "1"}]},
            {"name": "B", "pins": [{"refdes": "U1", "pin": "4"},
                                   {"refdes": "R1", "pin": "2"}]},
        ],
    })


def _inst(refdes, model, x, y, rotation=0):
    return SymbolInstance(refdes=refdes, symbol=model, x=x, y=y,
                          rotation=rotation)


# ---------------------------------------------------------------------------
# shunt_on_side
# ---------------------------------------------------------------------------

def test_a_decoupling_cap_on_its_side_counts_once():
    """Judged in the WORLD frame: a pins-left-right symbol at rotation 0."""
    plan = _plan()
    insts = [_inst("C1", _two_pin("C", vertical=False), 1000, 1000)]
    assert _count_shunt_on_side(insts, plan) == 1


def test_an_upright_decoupling_cap_does_not_count():
    plan = _plan()
    insts = [_inst("C1", _two_pin("C", vertical=True), 1000, 1000)]
    assert _count_shunt_on_side(insts, plan) == 0


def test_the_rotation_value_is_not_what_is_judged():
    """A pins-left-right symbol rotated 270 IS upright; a pins-up-down one
    rotated 270 is on its side. Rotation alone says nothing."""
    plan = _plan()
    upright = [_inst("C1", _two_pin("C", vertical=False), 1000, 1000, 270)]
    on_side = [_inst("C1", _two_pin("C", vertical=True), 1000, 1000, 270)]
    assert _count_shunt_on_side(upright, plan) == 0
    assert _count_shunt_on_side(on_side, plan) == 1


def test_a_series_signal_resistor_is_not_a_shunt_part():
    plan = _plan()
    insts = [_inst("R1", _two_pin("R", vertical=False), 1000, 1000)]
    assert _count_shunt_on_side(insts, plan) == 0


def test_a_two_pin_connector_is_not_a_shunt_part():
    """Connectors are the sheet's I/O edge; 0% of human ones stand upright."""
    plan = _plan()
    insts = [_inst("J1", _two_pin("HDR", vertical=False), 1000, 1000)]
    assert _count_shunt_on_side(insts, plan) == 0


def test_no_plan_means_no_shunt_count():
    insts = [_inst("C1", _two_pin("C", vertical=False), 1000, 1000)]
    assert _count_shunt_on_side(insts, None) == 0


# ---------------------------------------------------------------------------
# row bands
# ---------------------------------------------------------------------------

def test_row_bands_per_part():
    m = _two_pin("R", vertical=False)
    one_row = [_inst(f"R{i}", m, 1000 * i, 2000) for i in range(4)]
    assert _row_bands_per_part(one_row) == 0.25
    four_rows = [_inst(f"R{i}", m, 1000, 2000 + 500 * i) for i in range(4)]
    assert _row_bands_per_part(four_rows) == 1.0
    assert _row_bands_per_part([]) == 0.0


# ---------------------------------------------------------------------------
# raw_features and the learned model
# ---------------------------------------------------------------------------

def test_raw_features_carries_every_learned_feature():
    from eda_agent.design.quality import _LEARNED_FEATURES

    feats = raw_features(LayoutScore())
    assert set(feats) == {name for name, _ in _LEARNED_FEATURES}


def test_score_canvas_reports_the_new_features(monkeypatch, tmp_path):
    monkeypatch.setenv("EDA_AGENT_QUALITY_MODEL", str(tmp_path / "none.json"))
    reset_model_cache()
    plan = _plan()
    cv = SchematicCanvas()
    cv.add_instance(_inst("C1", _two_pin("C", vertical=False), 1000, 1000))
    cv.add_instance(_inst("R1", _two_pin("R", vertical=False), 1000, 3000))
    cv.add_wires([WireSegment(x1=0, y1=0, x2=2500, y2=0, sheet="main",
                              net="A")])
    sc = score_canvas(cv, plan)
    assert sc.shunt_on_side == 1
    assert sc.long_wires == 1
    assert sc.row_bands_per_part == 1.0
    assert raw_features(sc)["shunt_on_side"] == 1.0


def test_a_six_feature_model_scores_exactly_as_before():
    """A model file written before the human-law features existed."""
    model = {"weights_raw": {"wire_crossings": -2.0, "total_wire_length": -0.01},
             "intercept_raw": 1.0}
    feats = {"wire_crossings": 3, "total_wire_length": 500,
             "shunt_on_side": 7, "long_wires": 9, "alignment_penalty": 0.5}
    breakdown, total = _apply_learned_model(model, feats)
    # -(1.0 - 6.0 - 5.0) = 10.0, and the human-law features contribute
    # nothing because the model carries no weight for them.
    assert total == pytest.approx(10.0)
    assert set(breakdown) == {"crossings", "through_body", "overlaps",
                              "aspect", "length", "ports", "intercept"}


def test_a_model_with_human_law_weights_applies_them():
    model = {"weights_raw": {"shunt_on_side": -3.0, "alignment_penalty": -10.0},
             "intercept_raw": 0.0}
    feats = {"shunt_on_side": 2, "alignment_penalty": 0.5}
    breakdown, total = _apply_learned_model(model, feats)
    assert total == pytest.approx(11.0)
    assert breakdown["shunt_on_side"] == pytest.approx(6.0)
    assert breakdown["alignment"] == pytest.approx(5.0)


def test_the_pipeline_applies_a_human_law_model_from_disk(monkeypatch, tmp_path):
    """End to end: a model file naming a new feature changes score_canvas."""
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"weights_raw": {"shunt_on_side": -100.0},
                                "intercept_raw": 0.0}), encoding="utf-8")
    monkeypatch.setenv("EDA_AGENT_QUALITY_MODEL", str(path))
    reset_model_cache()
    plan = _plan()
    on_side = SchematicCanvas()
    on_side.add_instance(_inst("C1", _two_pin("C", vertical=False), 1000, 1000))
    upright = SchematicCanvas()
    upright.add_instance(_inst("C1", _two_pin("C", vertical=True), 1000, 1000))
    try:
        assert score_canvas(on_side, plan).total > score_canvas(upright, plan).total
    finally:
        reset_model_cache()
