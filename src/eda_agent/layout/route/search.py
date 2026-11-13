# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Shortest paths on a region of the routing grid.

A search region is a set of cells (a mask inside a bounding box) on every
routing layer, plus one via node per cell. Each layer cell joins its
eight neighbours in the region; a via node joins the cell above it on
every layer, so any change of layer through one via costs one via. Costs
are per cell, entered: the step length times the cost of the cell
stepped onto.

Only the region's cells become graph nodes. scipy's Dijkstra pays for
every node it is given before it settles the first (about 12 ms for half
a million), so a long diagonal connection searched over its whole
bounding box, on six layers, paid for a million nodes it had no use for.
A band or corridor round the route holds a tenth of them.

The graph is built as a CSR matrix with numpy (no Python loop per node)
and searched from the new terminal towards the tree already routed,
stopping at a distance limit. Only arcs a search can take are in it: a
cell that may not be used, a move out of the region and a via that does
not fit have none.

scipy has no A*, but Dijkstra on reduced weights w - h(u) + h(v) IS A*
when h is a consistent estimate of the cost still to go. Here h is the
straight-line distance to the nearest target cell, from a Euclidean
distance transform: every move costs at least its length, so h never
overestimates and the reduced weights stay non-negative. scipy's
Dijkstra does not stop at a target, it settles everything under its
limit, so on reduced costs the limit is the detour allowed beyond a
straight line.

The compiled search (the optional eda-agent-route wheel) takes the same
region and costs, builds no graph and stops at the first target it
settles. Its weights are computed with the same operations, so the path
it finds costs exactly what scipy's would; where two paths cost the same
it may take the other one. :func:`find_path` uses it when it is installed;
EDA_AGENT_ROUTE_KERNEL=python keeps scipy.
"""

from __future__ import annotations

import math
import os

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

BLOCKED = 1e12

#: The eight in-plane moves: (dx, dy, length in cells).
MOVES = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
         (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
         (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2)))


class Window:
    """A box of the grid, and the cells in it that the search may use."""

    def __init__(self, i0: int, j0: int, w: int, h: int, L: int,
                 mask: np.ndarray | None = None):
        self.i0, self.j0, self.w, self.h, self.L = i0, j0, w, h, L
        self.mask = np.ones((h, w), dtype=bool) if mask is None else mask
        self.cy, self.cx = np.nonzero(self.mask)
        self.M = len(self.cy)
        self.rank = np.full((h, w), -1, dtype=np.int64)
        self.rank[self.cy, self.cx] = np.arange(self.M)

    def __repr__(self):
        return f"Window(i0={self.i0}, j0={self.j0}, w={self.w}, h={self.h}, L={self.L}, cells={self.M})"

    @property
    def n_layer(self) -> int:
        return self.L * self.M

    @property
    def size(self) -> int:
        return self.n_layer + self.M

    @property
    def slices(self):
        return (slice(self.j0, self.j0 + self.h), slice(self.i0, self.i0 + self.w))

    def contains(self, y, x) -> np.ndarray:
        y, x = np.asarray(y), np.asarray(x)
        inbox = (y >= self.j0) & (y < self.j0 + self.h) & (x >= self.i0) & (x < self.i0 + self.w)
        out = np.zeros(inbox.shape, dtype=bool)
        out[inbox] = self.mask[y[inbox] - self.j0, x[inbox] - self.i0]
        return out

    def node(self, l, y, x):
        """Graph node of grid cell (l, y, x); the cell must be in the region."""
        r = self.rank[np.asarray(y) - self.j0, np.asarray(x) - self.i0]
        return np.asarray(l) * self.M + r

    def hub(self, y, x):
        return self.n_layer + self.rank[np.asarray(y) - self.j0, np.asarray(x) - self.i0]

    def decode(self, n: int) -> tuple[int, int, int]:
        """(layer, y, x) in grid indices; layer -1 for a via node."""
        if n >= self.n_layer:
            r = n - self.n_layer
            return -1, self.j0 + int(self.cy[r]), self.i0 + int(self.cx[r])
        l, r = divmod(n, self.M)
        return l, self.j0 + int(self.cy[r]), self.i0 + int(self.cx[r])


def build_graph(win: Window, cell_cost: np.ndarray, via_cost: np.ndarray,
                move_factor: np.ndarray, pitch: float,
                potential: np.ndarray | None = None) -> csr_matrix:
    """``cell_cost`` (L, M) and ``via_cost`` (M,) per region cell: inf
    where barred.

    ``move_factor`` (L, 8): the per-layer cost multiplier of each move,
    which is how a layer prefers one direction. ``potential`` (M,), in
    the same units as the costs, turns the search into A* (see above);
    it must never exceed the true cost to go.

    An arc whose weight reaches BLOCKED (a barred cell, a move out of the
    region, a via that does not fit) is left out of the graph: no search
    with a limit under BLOCKED can take it, so the paths are the same, and
    on a crowded board more than half the arcs were such.
    """
    L, M = win.L, win.M
    cy1, cx1 = win.cy + 1, win.cx + 1
    cc = np.minimum(cell_cost, BLOCKED)
    vc = np.minimum(via_cost, BLOCKED)
    nL = L * M
    idx = np.empty((L, M, 9), dtype=np.int32)
    wt = np.empty((L, M, 9), dtype=np.float64)
    selfid = np.arange(M, dtype=np.int32)
    layer_base = (np.arange(L, dtype=np.int32) * M)[:, None]
    # The region's ranks with a border of -1: a move off the box reads it.
    rank = np.full((win.h + 2, win.w + 2), -1, dtype=np.int32)
    rank[1:-1, 1:-1] = win.rank
    for k, (dx, dy, length) in enumerate(MOVES):
        r = rank[cy1 + dy, cx1 + dx]
        out = r < 0
        nb = np.where(out, selfid, r)
        idx[:, :, k] = layer_base + nb
        wk = cc[:, nb] * ((length * pitch) * move_factor[:, k][:, None])
        if potential is not None:
            wk += (potential[nb] - potential)[None, :]
            np.maximum(wk, 0.0, out=wk)
        wk[:, out] = BLOCKED
        wt[:, :, k] = wk
    blocked_cell = cc >= BLOCKED
    idx[:, :, 8] = nL + selfid[None, :]
    wt[:, :, 8] = vc[None, :] / 2.0 + blocked_cell * BLOCKED
    hidx = (layer_base + selfid[None, :]).T                 # (M, L)
    hwt = vc[:, None] / 2.0 + blocked_cell.T * BLOCKED
    keep = wt < BLOCKED
    hkeep = hwt < BLOCKED
    data = np.concatenate([wt[keep], hwt[hkeep]])
    indices = np.concatenate([idx[keep], hidx[hkeep]])
    indptr = np.zeros(win.size + 1, dtype=np.int32)
    np.cumsum(np.concatenate([keep.sum(axis=2).reshape(-1), hkeep.sum(axis=1)]),
              out=indptr[1:])
    return csr_matrix((data, indices, indptr), shape=(win.size, win.size), copy=False)


def shortest_path(graph: csr_matrix, sources: np.ndarray, targets: np.ndarray,
                  limit: float) -> list[int] | None:
    """Cheapest path from any source to any target, as graph node ids."""
    if len(sources) == 0 or len(targets) == 0:
        return None
    # Arcs of BLOCKED or more are not in the graph (build_graph): a limit
    # past it would find nothing more.
    limit = min(limit, BLOCKED / 2)
    dist, pred, _ = dijkstra(graph, directed=True, indices=np.unique(sources),
                             return_predecessors=True, min_only=True, limit=limit)
    td = dist[targets]
    k = int(np.argmin(td))
    if not np.isfinite(td[k]) or td[k] >= BLOCKED:
        return None
    n = int(targets[k])
    path = [n]
    while pred[n] >= 0:
        n = int(pred[n])
        path.append(n)
    path.reverse()
    return path


KERNEL_ENV = "EDA_AGENT_ROUTE_KERNEL"
#: The compiled search's interface this code was written against.
KERNEL_API = 1
KERNEL_HINT = "pip install eda-agent[route]"


def compiled():
    """The compiled search module, or None when it is not installed, is
    for another interface, or EDA_AGENT_ROUTE_KERNEL=python."""
    if os.environ.get(KERNEL_ENV, "").strip().lower() == "python":
        return None
    try:
        import eda_agent_route
    except ImportError:
        return None
    return eda_agent_route if getattr(eda_agent_route, "API", None) == KERNEL_API else None


def kernel() -> str:
    """Which search find_path runs: 'compiled' or 'scipy'."""
    return "compiled" if compiled() is not None else "scipy"


def find_path(win: Window, cell_cost: np.ndarray, via_cost: np.ndarray,
              move_factor: np.ndarray, pitch: float, potential: np.ndarray | None,
              sources: np.ndarray, targets: np.ndarray,
              limits: tuple[float, ...]) -> list[int] | None:
    """The cheapest path from any source to any target, as graph node ids,
    or None when none costs the last limit or less.

    scipy settles every node under its limit, so it is tried at each limit
    in turn, smallest first: a path found under a smaller limit is the
    cheapest of all. The compiled search stops at the first target it
    settles, so it runs once, at the last limit.
    """
    if len(sources) == 0 or len(targets) == 0:
        return None
    lib = compiled()
    if lib is None:
        graph = build_graph(win, cell_cost, via_cost, move_factor, pitch, potential)
        for limit in limits:
            path = shortest_path(graph, sources, targets, limit)
            if path is not None:
                return path
        return None
    f64 = lambda a: np.ascontiguousarray(a, dtype="<f8").tobytes()  # noqa: E731
    i32 = lambda a: np.ascontiguousarray(a, dtype="<i4").tobytes()  # noqa: E731
    i64 = lambda a: np.ascontiguousarray(a, dtype="<i8").tobytes()  # noqa: E731
    return lib.shortest_path(
        win.w, win.h, win.L, i32(win.cy), i32(win.cx), f64(cell_cost), f64(via_cost),
        f64(move_factor), float(pitch), i64(sources), i64(targets), float(limits[-1]),
        None if potential is None else f64(potential))
