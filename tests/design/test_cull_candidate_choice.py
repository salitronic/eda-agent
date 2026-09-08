# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The cross-net cull picks an offender that strands nobody, if one exists.

Culling a net drops its routed copper and re-admits each per-pin stub only
if the stub introduces no new cross-net meeting. A pin that gets no stub
back is left on a floating label, and the sheet is declined outright.

WHICH net is culled decides whether that happens, and the worst offender is
only the best first guess. Measured over 587 public sheets: weighing the
worst four candidates instead of committing to the worst took stranded-pin
declines from 19 to 5 and the decline rate from 7.8% to 6.1%.
"""
from __future__ import annotations

from eda_agent.design.pipeline import _CULL_CANDIDATES, _pick_cull_candidate


def _attempt(stranded: int):
    """A try_cull result: (segments, label_points, stranded, is_rail)."""
    return ([], [], stranded, False)


def test_the_worst_offender_wins_when_it_strands_nobody():
    """The cheap path must stay cheap: one trial, no wandering."""
    tried = []

    def try_cull(name):
        tried.append(name)
        return _attempt(0)

    name, attempt = _pick_cull_candidate({"A": 5, "B": 3, "C": 1}, try_cull)
    assert name == "A" and attempt[2] == 0
    assert tried == ["A"], "a clean worst offender must not cost more trials"


def test_a_stranding_worst_offender_gives_way_to_a_clean_one():
    strand = {"A": 2, "B": 1, "C": 0}

    def try_cull(name):
        return _attempt(strand[name])

    name, attempt = _pick_cull_candidate({"A": 9, "B": 5, "C": 2}, try_cull)
    assert name == "C", "C strands nobody and must be preferred"
    assert attempt[2] == 0


def test_when_every_candidate_strands_the_worst_is_still_culled():
    """The loop must make progress; refusing to cull would not terminate."""
    def try_cull(name):
        return _attempt(3)

    name, attempt = _pick_cull_candidate({"A": 9, "B": 5, "C": 2}, try_cull)
    assert name == "A", "the worst offender is the fallback, as before"
    assert attempt[2] == 3


def test_no_more_than_the_budget_of_candidates_is_tried():
    """Each trial re-runs the meeting count per stub length, and the caller
    repeats this until the sheet is clean."""
    tried = []

    def try_cull(name):
        tried.append(name)
        return _attempt(1)

    offenders = {chr(ord("A") + i): 20 - i for i in range(12)}
    _pick_cull_candidate(offenders, try_cull)
    assert len(tried) == _CULL_CANDIDATES


def test_ties_are_broken_by_name_so_the_choice_is_reproducible():
    """Equal meeting counts must not leave the order to dict iteration."""
    seen = []

    def try_cull(name):
        seen.append(name)
        return _attempt(1)

    _pick_cull_candidate({"Z": 4, "A": 4, "M": 4}, try_cull)
    assert seen == sorted(seen)
