# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The compiled grid search finds paths that cost exactly what scipy's do.

Both take the same region and costs: random regions with walls, barred
vias, layers that prefer a direction and an A* potential. Where two paths
cost the same either may be taken, so what must agree is whether there is
a path and what it costs, and every path must be one the graph holds.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.sparse.csgraph import dijkstra

from eda_agent.layout.route import search
from eda_agent.layout.route.search import BLOCKED, Window, build_graph, find_path

lib = search.compiled()
needs_wheel = pytest.mark.skipif(lib is None, reason=f"compiled search not installed ({search.KERNEL_HINT})")


def _case(seed: int):
    rng = np.random.default_rng(seed)
    w, h, L = int(rng.integers(4, 40)), int(rng.integers(4, 40)), int(rng.integers(1, 4))
    mask = rng.random((h, w)) > 0.15
    win = Window(0, 0, w, h, L, mask)
    cell = rng.uniform(1.0, 3.0, (L, win.M))
    cell[rng.random((L, win.M)) < 0.2] = np.inf
    via = rng.uniform(5.0, 30.0, win.M)
    via[rng.random(win.M) < 0.3] = np.inf
    move = rng.uniform(1.0, 2.0, (L, 8))
    pitch = float(rng.choice([2.5, 5.0, 10.0]))
    sources = rng.choice(win.size, size=min(3, win.M), replace=False).astype(np.int64)
    targets = rng.choice(win.size, size=min(5, win.M), replace=False).astype(np.int64)
    potential = None
    if seed % 2:
        # The straight-line distance to the nearest target: a lower bound.
        ty, tx = win.cy[targets % win.M], win.cx[targets % win.M]
        d = np.hypot(win.cy[:, None] - ty[None, :], win.cx[:, None] - tx[None, :]).min(axis=1)
        potential = d * pitch * 0.9
    return win, cell, via, move, pitch, potential, sources, targets


def _cost(graph, path):
    return sum(graph[a, b] for a, b in zip(path, path[1:]))


@needs_wheel
@pytest.mark.parametrize("seed", range(120))
def test_the_same_cost_as_scipy(seed, monkeypatch):
    win, cell, via, move, pitch, pot, src, tgt = _case(seed)
    graph = build_graph(win, cell, via, move, pitch, pot)
    limits = (40.0 * pitch, 1e11)
    got = find_path(win, cell, via, move, pitch, pot, src, tgt, limits)
    monkeypatch.setenv(search.KERNEL_ENV, "python")
    want = find_path(win, cell, via, move, pitch, pot, src, tgt, limits)
    assert (got is None) == (want is None), (got, want)
    if want is None:
        return
    assert got[0] in src and got[-1] in tgt
    assert all(b in graph.indices[graph.indptr[a]:graph.indptr[a + 1]] for a, b in zip(got, got[1:]))
    assert math.isclose(_cost(graph, got), _cost(graph, want), rel_tol=0, abs_tol=1e-9 * max(1.0, _cost(graph, want)))
    best = dijkstra(graph, indices=np.unique(src), min_only=True)[tgt].min()
    assert math.isclose(_cost(graph, got), best, rel_tol=1e-12, abs_tol=1e-9)


@needs_wheel
def test_a_limit_too_small_finds_nothing():
    win = Window(0, 0, 6, 1, 1)
    cell = np.ones((1, 6))
    args = (win, cell, np.full(6, np.inf), np.ones((1, 8)), 1.0, None,
            np.array([0], dtype=np.int64), np.array([5], dtype=np.int64))
    assert find_path(*args, (1e11,)) == [0, 1, 2, 3, 4, 5]
    assert find_path(*args, (4.5,)) is None


@needs_wheel
def test_a_bad_array_is_refused_not_read_past():
    with pytest.raises(ValueError, match="cell costs"):
        lib.shortest_path(3, 1, 1, np.zeros(3, "<i4").tobytes(), np.arange(3, dtype="<i4").tobytes(),
                          np.ones(2).tobytes(), np.ones(3).tobytes(), np.ones(8).tobytes(), 1.0,
                          np.array([0], "<i8").tobytes(), np.array([2], "<i8").tobytes(), BLOCKED)


def test_python_is_kept_on_request(monkeypatch):
    monkeypatch.setenv(search.KERNEL_ENV, "python")
    assert search.kernel() == "scipy" and search.compiled() is None
