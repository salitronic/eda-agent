# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The corpus fit's constraints, each of which caught a real bad model.

- a feature constant on every engine candidate is frozen (shunt_on_side
  came out +1.99 per sd when every engine candidate had 0 of it);
- the representation features are frozen (port_count reached -14.5 per sd:
  humans draw one glyph and a bus, the engine a glyph per pin);
- illegal-geometry weights are projected non-positive (wires_through_bodies
  reached +0.60, a weight that rewards drawing through a body).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / "train" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_fit = _load("fit_corpus_model")
_tqm = sys.modules["train_quality_model"]


def _pair(eng: dict, hum: dict, human_is_a: bool, design: str):
    return {"plan_hash": design, "winner": "a" if human_is_a else "b",
            "features_a": hum if human_is_a else eng,
            "features_b": eng if human_is_a else hum}


def test_a_feature_constant_on_the_engine_side_is_frozen():
    rows = []
    for d in range(6):
        for i in range(4):
            eng = {"wire_crossings": float(i), "shunt_on_side": 0.0}
            hum = {"wire_crossings": 0.0, "shunt_on_side": float(i % 2)}
            rows.append(_pair(eng, hum, i % 2 == 0, f"d{d}"))
    frozen = _fit.engine_constant_features(rows)
    assert "shunt_on_side" in frozen
    assert "wire_crossings" not in frozen


def test_port_count_is_a_representation_feature_by_default():
    assert "port_count" in _fit._REPRESENTATION_FEATURES


def test_frozen_weights_stay_at_zero_and_illegal_ones_never_go_positive():
    """Fit a corpus that would push both the wrong way, then check."""
    rows = []
    for d in range(12):
        for i in range(6):
            # The human always has MORE through-body wires and MORE ports:
            # a free fit would reward both.
            eng = {"wires_through_bodies": 0.0, "port_count": 2.0 + i,
                   "total_wire_length": 1000.0 + 100 * i}
            hum = {"wires_through_bodies": 3.0, "port_count": 20.0 + i,
                   "total_wire_length": 500.0 + 100 * i}
            rows.append(_pair(eng, hum, (i + d) % 2 == 0, f"d{d}"))
    pairs, means, stds = _tqm._normalise_features(rows)
    names = _tqm._FEATURE_NAMES
    w, _ = _tqm._train(pairs, epochs=300, lr=0.05, l2=0.01,
                       frozen=frozenset({"port_count"}))
    assert w[names.index("port_count")] == 0.0
    assert w[names.index("wires_through_bodies")] <= 0.0
    assert w[names.index("total_wire_length")] < 0.0, (
        "shorter wire is the only legitimate signal here and must be learned")
