# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The human canvas carries only wiring connected to a placed part.

The plan is built from the annotated, single-unit parts the reader can
resolve. The sheet may hold many more, on the same nets, and their wires and
glyphs were being counted against the human. Found on a public sheet with 11
plan parts and 146 power glyphs: a grid of 70 unannotated parts above the
circuit, each with a rail stub and two glyphs. A model fitted on pairs built
that way learned that fewer ports looks human.
"""
from __future__ import annotations

from eda_agent.design.human_benchmark import _keep_connected


def test_a_segment_touching_a_seed_is_kept_and_a_stray_one_is_not():
    seeds = {(1000, 1000)}
    wires = [
        (1000, 1000, 1500, 1000, "A"),      # starts on the pin
        (5000, 5000, 5500, 5000, "A"),      # same net, nowhere near
    ]
    kept, reached = _keep_connected(wires, seeds)
    assert kept == [wires[0]]
    assert (1500, 1000) in reached and (5000, 5000) not in reached


def test_reachability_follows_shared_endpoints_in_any_order():
    """The chain is listed far end first; one pass would miss it."""
    seeds = {(0, 0)}
    wires = [
        (2000, 0, 3000, 0, "A"),
        (1000, 0, 2000, 0, "A"),
        (0, 0, 1000, 0, "A"),
    ]
    kept, reached = _keep_connected(wires, seeds)
    assert len(kept) == 3
    assert (3000, 0) in reached


def test_a_t_junction_counts_as_a_connection():
    """An endpoint lying ON a kept segment, not at its end."""
    seeds = {(0, 0)}
    wires = [
        (0, 0, 2000, 0, "A"),
        (1000, 0, 1000, 800, "A"),          # drops off the middle
    ]
    kept, _ = _keep_connected(wires, seeds)
    assert len(kept) == 2


def test_nothing_is_kept_without_a_seed():
    kept, reached = _keep_connected([(0, 0, 100, 0, "A")], set())
    assert kept == [] and reached == set()


def test_the_orphan_grid_case():
    """Seventy stubs on the plan's rail, none touching a placed part."""
    seeds = {(4000, -9000)}
    orphans = [(x, -3000, x + 300, -3000, "+3V3") for x in range(0, 70 * 500, 500)]
    real = [(4000, -9000, 4000, -8000, "+3V3")]
    kept, _ = _keep_connected(orphans + real, seeds)
    assert kept == real
