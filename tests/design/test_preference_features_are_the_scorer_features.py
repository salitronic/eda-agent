# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The pair logger, the corpus miner and the scorer share one feature dict.

A model fitted on one feature definition and applied through another is
wrong silently: the weights still multiply something. ``quality.raw_features``
is that one definition, and this pins every consumer to it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from eda_agent.design.preferences import _features_from_score
from eda_agent.design.quality import LayoutScore, raw_features

REPO = Path(__file__).parent.parent.parent


def test_the_preference_logger_uses_raw_features():
    score = LayoutScore(wire_crossings=3, total_wire_length=1200,
                        alignment_penalty=0.25, shunt_on_side=2,
                        long_wires=4, row_bands_per_part=0.5)
    assert _features_from_score(score) == raw_features(score)


def test_the_trainer_fits_exactly_the_scorer_features():
    """A feature the scorer exposes but the trainer ignores is never learned;
    one the trainer expects but the scorer lacks reads as 0 forever."""
    spec = importlib.util.spec_from_file_location(
        "tqm", REPO / "scripts" / "train" / "train_quality_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod._FEATURE_NAMES) == set(raw_features(LayoutScore()))


def test_the_corpus_miner_imports_raw_features():
    src = (REPO / "scripts" / "train" / "mine_pairs_from_corpus.py").read_text(
        encoding="utf-8")
    assert "raw_features(score_canvas(" in src
