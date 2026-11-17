# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Slivers and straight chains, planned off the board in one pass."""
from __future__ import annotations

import time

from eda_agent.layout.cleanup import Piece, plan


def _chain(n, net="A", layer="top", y=0.0, step=10.0, width=6.0):
    return [Piece(layer, i * step, y, (i + 1) * step, y, width, net) for i in range(n)]


def test_a_straight_chain_becomes_one_track():
    todo = plan(_chain(5), "merge")
    assert todo.merged == 4 and len(todo.merges) == 1
    keep, new, gone = todo.merges[0]
    assert sorted((new[0], new[2])) == [0.0, 50.0] and new[1] == new[3] == 0.0
    assert len(gone) == 4 and keep not in gone


def test_a_bend_a_width_step_and_another_net_stay_joints():
    bend = [Piece("top", 0, 0, 10, 0, 6, "A"), Piece("top", 10, 0, 20, 10, 6, "A")]
    step = [Piece("top", 0, 5, 10, 5, 6, "A"), Piece("top", 10, 5, 20, 5, 8, "A")]
    nets = [Piece("top", 0, 9, 10, 9, 6, "A"), Piece("top", 10, 9, 20, 9, 6, "B")]
    back = [Piece("top", 0, 20, 10, 20, 6, "A"), Piece("top", 10, 20, 4, 20, 6, "A")]
    assert plan(bend + step + nets + back, "merge").merged == 0


def test_a_joint_with_something_at_it_is_left():
    tee = _chain(2) + [Piece("top", 10, 0, 10, 30, 6, "A")]
    todo = plan(tee, "merge")
    assert todo.merged == 0 and todo.kept_joints == {"junction": 1}
    crossing = _chain(2) + [Piece("top", 10, -20, 10, 20, 6, "A")]
    todo = plan(crossing, "merge")
    assert todo.merged == 0 and todo.kept_joints == {"third_track": 1}
    todo = plan(_chain(2), "merge", blocked=lambda layer, s: "via" if s == (10.0, 0.0) else "")
    assert todo.merged == 0 and todo.kept_joints == {"via": 1}
    todo = plan(_chain(2), "merge", others=[Piece("top", 10, 0, 10, 5, 4, "A")])
    assert todo.merged == 0


def test_a_track_whose_net_is_not_known_is_not_merged():
    todo = plan(_chain(2, net=None), "merge")
    assert todo.merged == 0 and todo.kept_joints == {"net_unknown": 1}


def test_layers_are_kept_apart():
    todo = plan(_chain(2, layer="top") + _chain(2, layer="bottom"), "merge")
    assert len(todo.merges) == 2 and todo.merged == 2


def test_slivers_go_first_and_the_rest_merges_in_both():
    tracks = _chain(3) + [Piece("top", 100, 100, 100.4, 100, 6, "A")]
    only = plan(tracks, "slivers", 1.0)
    assert len(only.slivers) == 1 and not only.merges
    both = plan(tracks, "both", 1.0)
    assert len(both.slivers) == 1 and both.merged == 2


def test_the_cost_does_not_grow_with_the_square():
    tracks = []
    for row in range(60):
        tracks += _chain(100, net=f"N{row}", y=row * 20.0)
    started = time.perf_counter()
    todo = plan(tracks, "both", 1.0)
    assert time.perf_counter() - started < 5.0
    assert len(todo.merges) == 60 and todo.merged == 60 * 99
