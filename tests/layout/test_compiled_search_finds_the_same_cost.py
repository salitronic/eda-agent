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


TURNS = search.Turns()


def _turning_cost(graph, win, path, src):
    """The cheapest cost of a node path in a turning graph: the path's
    states are worked out step by step from a FRESH source."""
    nL = win.n_layer

    def states(n):
        return [n * 9 + h for h in range(9)] if n < nL else [9 * nL + n - nL]

    first = path[0]
    best = {(first * 9 + search.FRESH) if first < nL else 9 * nL + first - nL: 0.0}
    for b in path[1:]:
        nxt = {}
        for s, c in best.items():
            row = graph.indices[graph.indptr[s]:graph.indptr[s + 1]]
            w = graph.data[graph.indptr[s]:graph.indptr[s + 1]]
            for t in states(b):
                hit = np.nonzero(row == t)[0]
                if len(hit):
                    nc = c + float(w[hit[0]])
                    if nc < nxt.get(t, math.inf):
                        nxt[t] = nc
        best = nxt
        assert best, f"step to {b} is not an arc"
    return min(best.values())


@needs_wheel
@pytest.mark.parametrize("seed", range(60))
def test_with_turns_the_same_cost_as_scipy(seed, monkeypatch):
    win, cell, via, move, pitch, pot, src, tgt = _case(seed)
    graph = search.build_graph_turning(win, cell, via, move, pitch, TURNS, pot)
    limits = (40.0 * pitch, 1e11)
    got = find_path(win, cell, via, move, pitch, pot, src, tgt, limits, TURNS)
    monkeypatch.setenv(search.KERNEL_ENV, "python")
    want = find_path(win, cell, via, move, pitch, pot, src, tgt, limits, TURNS)
    assert (got is None) == (want is None), (got, want)
    if want is None:
        return
    assert got[0] in src and got[-1] in tgt
    a, b = _turning_cost(graph, win, got, src), _turning_cost(graph, win, want, src)
    assert math.isclose(a, b, rel_tol=0, abs_tol=1e-9 * max(1.0, b)), (a, b)


def test_with_turns_a_shallow_run_bends_once(monkeypatch):
    # 30 across and 6 down, open: without turns a staircase costs the same
    # as one 45 degree bend; with them only the one bend is cheapest.
    for kernel in ("compiled", "python"):
        if kernel == "python":
            monkeypatch.setenv(search.KERNEL_ENV, "python")
        elif lib is None:
            continue
        win = Window(0, 0, 31, 7, 1)
        cell = np.ones((1, win.M))
        args = (win, cell, np.full(win.M, np.inf), np.ones((1, 8)), 1.0, None,
                np.array([0], dtype=np.int64), np.array([6 * 31 + 30], dtype=np.int64),
                (1e11,), TURNS)
        path = find_path(*args)
        steps = [(path[i + 1] % 31 - path[i] % 31, path[i + 1] // 31 - path[i] // 31)
                 for i in range(len(path) - 1)]
        bends = sum(1 for p, q in zip(steps, steps[1:]) if p != q)
        assert bends == 1, (kernel, steps)


def test_with_turns_no_diagonal_cuts_between_two_walls(monkeypatch):
    monkeypatch.setenv(search.KERNEL_ENV, "python")
    win = Window(0, 0, 3, 3, 1)
    cell = np.ones((1, 9))
    cell[0, 1] = cell[0, 3] = np.inf
    args = (win, cell, np.full(9, np.inf), np.ones((1, 8)), 1.0, None,
            np.array([0], dtype=np.int64), np.array([4], dtype=np.int64), (1e11,))
    assert find_path(*args) == [0, 4]
    assert find_path(*args, TURNS) is None



def _best_cost(graph, starts, ends):
    d = dijkstra(graph, indices=starts, min_only=True)
    return float(d[ends].min())


def _path_cost_between(graph, win, path, starts, ends):
    """The cheapest cost of a node path in a turning graph from one of the
    ``starts`` states to one of the ``ends`` states."""
    nL = win.n_layer

    def states(n):
        return [n * 9 + h for h in range(9)] if n < nL else [9 * nL + n - nL]

    starts, ends = set(int(s) for s in starts), set(int(e) for e in ends)
    best = {s: 0.0 for s in states(path[0]) if s in starts}
    for b in path[1:]:
        nxt = {}
        for s, c in best.items():
            row = graph.indices[graph.indptr[s]:graph.indptr[s + 1]]
            w = graph.data[graph.indptr[s]:graph.indptr[s + 1]]
            for t in states(b):
                hit = np.nonzero(row == t)[0]
                if len(hit) and c + float(w[hit[0]]) < nxt.get(t, math.inf):
                    nxt[t] = c + float(w[hit[0]])
        best = nxt
        assert best, f"step to {b} is not an arc"
    return min(c for s, c in best.items() if s in ends)


@needs_wheel
@pytest.mark.parametrize("seed", range(40))
def test_with_headings_and_arrivals_the_same_cost_as_scipy(seed, monkeypatch):
    win, cell, via, move, pitch, pot, src, tgt = _case(seed)
    rng = np.random.default_rng(1000 + seed)
    heading = rng.integers(0, 9, len(src))
    arrive = rng.integers(0, 256, len(tgt))
    graph = search.build_graph_turning(win, cell, via, move, pitch, TURNS, pot)
    starts, ends = search.turning_states(win, src, tgt, heading, arrive)
    limits = (40.0 * pitch, 1e11)
    got = find_path(win, cell, via, move, pitch, pot, src, tgt, limits, TURNS, heading, arrive)
    monkeypatch.setenv(search.KERNEL_ENV, "python")
    want = find_path(win, cell, via, move, pitch, pot, src, tgt, limits, TURNS, heading, arrive)
    assert (got is None) == (want is None), (got, want)
    if want is None:
        return
    best = _best_cost(graph, starts, ends)
    for path in (got, want):
        c = _path_cost_between(graph, win, path, starts, ends)
        assert math.isclose(c, best, rel_tol=1e-12, abs_tol=1e-9), (c, best)
