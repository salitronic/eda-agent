# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The trainer's hold-out must be by DESIGN, normalised with training stats.

A model fitted on human-versus-engine pairs sees several pairs per sheet,
all sharing that sheet's human canvas. Holding out pairs at random leaks
every sheet into training and the reported accuracy describes sheets the
model has seen. The split is by plan_hash, and the held-out rows are
normalised with the TRAINING means and stds, never their own.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
TRAINER = REPO / "scripts" / "train" / "train_quality_model.py"

_spec = importlib.util.spec_from_file_location("train_quality_model", TRAINER)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["train_quality_model"] = _mod
_spec.loader.exec_module(_mod)


def _rows(n_designs: int, per_design: int):
    rows = []
    for d in range(n_designs):
        for i in range(per_design):
            rows.append({
                "plan_hash": f"design{d}",
                "features_a": {"wire_crossings": float(i), "long_wires": 1.0},
                "features_b": {"wire_crossings": float(i + 1), "long_wires": 0.0},
                "winner": "a" if i % 2 else "b",
            })
    return rows


def test_split_keeps_every_pair_of_a_design_on_one_side():
    rows = _rows(10, 6)
    train, held = _mod._split_by_design(rows, 0.3, seed=7)
    assert len(train) + len(held) == len(rows)
    train_designs = {r["plan_hash"] for r in train}
    held_designs = {r["plan_hash"] for r in held}
    assert train_designs.isdisjoint(held_designs)
    assert len(held_designs) == 3


def test_split_is_deterministic_for_a_seed():
    rows = _rows(10, 3)
    a = _mod._split_by_design(rows, 0.2, seed=3)
    b = _mod._split_by_design(rows, 0.2, seed=3)
    assert [r["plan_hash"] for r in a[1]] == [r["plan_hash"] for r in b[1]]


def test_zero_fraction_holds_nothing_out():
    rows = _rows(4, 2)
    train, held = _mod._split_by_design(rows, 0.0, seed=1)
    assert train == rows and held == []


def test_held_out_rows_use_the_training_normalisation():
    """The held-out set must not be z-scored against itself."""
    rows = _rows(5, 4)
    train, held = _mod._split_by_design(rows, 0.2, seed=1)
    _pairs, means, stds = _mod._normalise_features(train)
    applied = _mod._apply_normalisation(held, means, stds)
    F = len(_mod._FEATURE_NAMES)
    i = _mod._FEATURE_NAMES.index("wire_crossings")
    for (a, b, winner), row in zip(applied, held):
        expect = (float(row["features_a"]["wire_crossings"]) - means[i]) / stds[i]
        assert abs(a[i] - expect) < 1e-9
        assert len(a) == len(b) == F
        assert winner == row["winner"]


def test_the_feature_list_includes_the_human_law_features():
    for name in ("alignment_penalty", "shunt_on_side", "long_wires",
                 "row_bands_per_part"):
        assert name in _mod._FEATURE_NAMES
