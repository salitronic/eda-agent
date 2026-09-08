# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A mined offset is written as a prior only when it is one preference.

MEASURED on the corpus: decoup_cap|ic had an interquartile range of about
+/-2000 mils on both axes, because humans put decoupling caps on all four
sides of the IC. The median of that pile is a point on the IC's centre, and
``aggregate`` would have written it as confidently as any real preference.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
SCRIPT = REPO / "scripts" / "train" / "mine_priors_from_corpus.py"

_spec = importlib.util.spec_from_file_location("mine_priors_from_corpus", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["mine_priors_from_corpus"] = _mod
_spec.loader.exec_module(_mod)

refuse_wide_spreads = _mod.refuse_wide_spreads


def _row(pair: str, dx: int, dy: int):
    part, anchor = pair.split("|")
    return {"part_role": part, "anchor_role": anchor,
            "dx_mils": dx, "dy_mils": dy, "rot_delta_deg": 0}


def test_a_tight_pair_survives():
    rows = [_row("crystal_cap_r|crystal", 400 + d, 0) for d in (-100, 0, 0, 100, 0)]
    kept, refused = refuse_wide_spreads(rows, max_iqr=800)
    assert kept == rows and refused == set()


def test_a_four_sided_pile_is_refused_on_either_axis():
    """Caps at +/-2000 on x and y, in equal numbers: the median is (0, 0)."""
    rows = []
    for _ in range(5):
        rows += [_row("decoup_cap|ic", 2000, 0), _row("decoup_cap|ic", -2000, 0),
                 _row("decoup_cap|ic", 0, 2000), _row("decoup_cap|ic", 0, -2000)]
    kept, refused = refuse_wide_spreads(rows, max_iqr=800)
    assert refused == {"decoup_cap|ic"}
    assert kept == []


def test_refusal_is_per_pair_not_global():
    wide = [_row("decoup_cap|ic", s * 2000, 0) for s in (1, -1, 1, -1, 1, -1)]
    tight = [_row("crystal_cap_l|crystal", -400, 0) for _ in range(6)]
    kept, refused = refuse_wide_spreads(wide + tight, max_iqr=800)
    assert refused == {"decoup_cap|ic"}
    assert kept == tight


def test_too_few_rows_to_judge_are_left_alone():
    """Three rows have no quartiles; min_samples decides those downstream."""
    rows = [_row("x|y", 0, 0), _row("x|y", 5000, 0), _row("x|y", -5000, 0)]
    kept, refused = refuse_wide_spreads(rows, max_iqr=800)
    assert kept == rows and refused == set()
