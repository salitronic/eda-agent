# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""L2 toward a PRIOR, and every badness weight held non-positive.

Pulled toward zero, a feature the pairs cannot discriminate on (crossings:
human canvases and engine candidates mostly have none) ends at zero and
best-of stops penalising it. Measured on 38 held-out sheets: crossings went
from 0.45x to 2.57x of the human's while alignment improved. Pulled toward
the hand-tuned weights, the corpus can only move a weight it has evidence
about.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "train_quality_model", REPO / "scripts" / "train" / "train_quality_model.py")
_t = importlib.util.module_from_spec(_spec)
sys.modules["train_quality_model"] = _t
_spec.loader.exec_module(_t)

F = len(_t._FEATURE_NAMES)


def _uninformative_pairs(n=40):
    """Identical feature vectors on both sides: nothing to learn from."""
    return [([0.0] * F, [0.0] * F, "a" if i % 2 else "b") for i in range(n)]


def test_with_nothing_to_learn_the_weights_stay_at_the_prior():
    prior = [-float(i + 1) for i in range(F)]
    w, _ = _t._train(_uninformative_pairs(), epochs=300, lr=0.05, l2=1.0,
                     prior=prior)
    for got, want in zip(w, prior):
        assert abs(got - want) < 1e-6


def test_the_default_prior_is_zero():
    w, _ = _t._train(_uninformative_pairs(), epochs=100, lr=0.05, l2=1.0)
    assert all(abs(x) < 1e-9 for x in w)


def test_all_nonpositive_projects_every_weight():
    """Pairs that would push a badness weight positive cannot."""
    i = _t._FEATURE_NAMES.index("wire_crossings")
    pairs = []
    for k in range(60):
        a = [0.0] * F
        b = [0.0] * F
        a[i] = 3.0          # the winner has MORE crossings
        pairs.append((a, b, "a"))
    w, _ = _t._train(pairs, epochs=200, lr=0.05, l2=0.01, all_nonpositive=True)
    assert w[i] <= 0.0
    w_free, _ = _t._train(pairs, epochs=200, lr=0.05, l2=0.01)
    assert w_free[i] > 0.0, "without the constraint the same pairs push it up"
