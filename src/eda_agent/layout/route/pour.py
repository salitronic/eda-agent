# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Copper pours: the board's own polygon outlines, poured after routing.

A two-layer board joins its ground mostly through pours: the designer draws
a polygon on each side and Altium pours it round whatever else is routed.
Routed as tracks instead, such a net takes room the other nets need; with
the poured nets set aside, the rest failed markedly fewer connections.

So a net with a pour outline on a routing layer is routed like any other,
and then each outline is poured on the grid: a cell is copper when every
point of its square keeps the clearance from foreign copper, keep-outs and
the board edge; necks a cell or two wide go, and so does copper touching
nothing of its own net, as Altium removes dead copper. The net's routes
the pour makes redundant are taken up, longest first, each only while its
pads stay joined without it, and the room goes to the nets still failing.
Poured again, whatever the pour then leaves apart is joined by routed
tracks and stitching vias with every other route a wall.

Set aside while the rest were routed and joined afterwards, the poured
nets got only the room left over; joined while negotiating, they chased
pour pieces another net's move had just cut off. Both did worse than
routing them as tracks. Taking up only what the pour provably joins can
only give room back.

The pour here stands in for Altium's own, for connectivity and scoring;
applied to a board, the designer's polygons are repoured in Altium.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_opening, distance_transform_edt, label
from scipy.spatial import cKDTree

from ..geom import ring_area
from ..model import Region
from .grid import RouteGrid, fill_polygon, shape_distance
from .params import RouteRules

#: Half a cell's diagonal, as a fraction of the pitch: every point of a
#: cell's square lies within this of its centre.
HALF_DIAGONAL = 0.7072

#: 4-connected: squares meeting only at a corner are not joined.
FOUR = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)


@dataclass
class Pour:
    li: int                        # routing layer index
    net: str
    nid: int
    outline: list
    area: float
    mask: np.ndarray | None = None


def find_pours(router) -> list[Pour]:
    """The pour outlines on routing layers whose net the router routes
    as a tree (a plane net joins its plane by vias instead)."""
    g = router.grid
    tree_nets = {j.name for j in router.jobs if j.plane_mask is None}
    out = []
    for r in router.board.regions:
        if r.kind != "pour_boundary" or not r.net or r.net not in tree_nets:
            continue
        li = g.layer_index.get(r.layer)
        if li is None or len(r.outline) < 3:
            continue
        out.append(Pour(li, r.net, g.net_id[r.net], list(r.outline), abs(ring_area(r.outline))))
    return out


def pour(router) -> list[Pour]:
    """Pour every outline round what is routed now; returns the pours
    with their masks set, smallest first (a smaller pour inside a larger
    one of another net keeps its copper, and the larger pours round it)."""
    out = router.apply(pours=False)
    g = RouteGrid(out, router.pitch, router.grid.reach, layers=router.grid.layers,
                  grow=router.object_grow, field_of=router.field_of,
                  n_fields=router.n_fields).build()
    spec = g.spec
    p = router.pitch
    margin = HALF_DIAGONAL * p
    done: dict[int, list[Pour]] = {}
    rr = router.rr
    for pr in sorted(router.pours, key=lambda x: x.area):
        inside = fill_polygon(spec, [pr.outline])
        row = rr.clearance_row(pr.nid, polygon=True)
        slack = g.slack(pr.nid, pr.li, (slice(None), slice(None)),
                        lambda who: RouteRules.lookup(row, who), rr.edge_clearance)
        ok = inside & (slack >= margin)
        for other in done.get(pr.li, []):
            if other.nid == pr.nid:
                continue
            # Square to square: centres at least the clearance and two
            # half-diagonals apart.
            d = distance_transform_edt(~other.mask) * p
            ok &= d >= rr.pair_clearance(pr.nid, other.nid, polygon=True) + 2 * margin
        ok = binary_opening(ok, structure=np.ones((3, 3), dtype=bool))
        # Copper touching nothing of its own net is dead: a square whose
        # centre lies within half a cell of the net's copper overlaps it.
        # Judged on the copper as drawn, not as the grid grows it for a
        # clearance rule.
        own = _own_cells(out, g, pr, 0.5 * p)
        lab, _ = label(ok, structure=FOUR)
        alive = np.unique(lab[own & ok])
        alive = alive[alive > 0]
        pr.mask = np.isin(lab, alive) if len(alive) else np.zeros_like(ok)
        done.setdefault(pr.li, []).append(pr)
    return router.pours


def _own_cells(board, g, pr: Pour, within: float) -> np.ndarray:
    """Cells whose centre lies within ``within`` of the pour net's own
    copper on the pour's layer, as drawn."""
    layer = g.layers[pr.li]
    spec = g.spec
    own = np.zeros((spec.ny, spec.nx), dtype=bool)
    shapes = [p.shape_on(layer) for p in board.pads if p.net == pr.net and layer in p.layers()]
    shapes += [t.shape() for t in board.tracks if t.net == pr.net and t.layer == layer and not t.keepout]
    shapes += [v.shape_on(layer) for v in board.vias
               if v.net == pr.net and layer in board.layers_between(v.low_layer, v.high_layer)]
    for s in shapes:
        x0, y0, x1, y1 = s.bbox
        i0, j0, i1, j1 = spec.window(x0 - within, y0 - within, x1 + within, y1 + within)
        if i0 >= i1 or j0 >= j1:
            continue
        X = spec.x(np.arange(i0, i1))[None, :]
        Y = spec.y(np.arange(j0, j1))[:, None]
        own[j0:j1, i0:i1] |= shape_distance(s, X, Y) <= within
    return own


def islands(router, net: str) -> list[tuple[int, np.ndarray]]:
    """The separate pieces of a net's pours: (layer, (k, 2) cells)."""
    out = []
    for pr in router.pours:
        if pr.net != net or pr.mask is None or not pr.mask.any():
            continue
        lab, n = label(pr.mask, structure=FOUR)
        for k in range(1, n + 1):
            ys, xs = np.nonzero(lab == k)
            out.append((pr.li, np.stack([ys, xs], axis=1)))
    return out


def groups(router, job) -> list[dict]:
    """The net's pads and pour pieces, joined where a pad lies on a piece
    (a through-hole pad on pieces of two layers joins them): each group's
    terminals and its cells, (k, 3) layer, y, x."""
    parts = islands(router, job.name)
    n_t = len(job.terminals)
    parent = list(range(n_t + len(parts)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    where: dict[tuple, int] = {}
    for k, (li, cells) in enumerate(parts):
        for y, x in cells.tolist():
            where[(li, y, x)] = n_t + k
    for i, t in enumerate(job.terminals):
        for c in t.cells.tolist():
            k = where.get(tuple(c))
            if k is not None:
                parent[find(i)] = find(k)
    # A pad the board's own copper already takes down a via: the via joins
    # it to the piece it stands in. Without this a pad with its stub and
    # via laid was routed to the pour again with a second via.
    barrels = getattr(router, "pad_barrels", None) or {}
    spec = router.grid.spec
    for i, t in enumerate(job.terminals):
        for x, y in barrels.get(t.pad, ()):
            ci, cj = spec.cell(x, y)
            for li in range(router.L):
                k = where.get((li, cj, ci))
                if k is not None:
                    parent[find(i)] = find(k)
    out: dict[int, dict] = {}
    for i, t in enumerate(job.terminals):
        g = out.setdefault(find(i), {"terminals": [], "cells": []})
        g["terminals"].append(i)
        g["cells"].append(t.cells)
    for k, (li, cells) in enumerate(parts):
        r = find(n_t + k)
        if r in out:
            out[r]["cells"].append(np.column_stack([np.full(len(cells), li), cells]))
    return [{"terminals": g["terminals"], "cells": np.concatenate(g["cells"]).astype(np.int64)}
            for g in out.values()]


def stitch(router, job, pres: float = 1.0, hard: bool = True) -> int:
    """Join the groups of a poured net with routed tracks and vias,
    nearest first from the largest, at present-cost ``pres`` (or with
    every other route a wall, ``hard``); returns the connections left
    unmade, which is also the job's ``failed``."""
    gs = job.groups if job.groups is not None else groups(router, job)
    router._unstamp(job)
    job.paths = []
    job.via_at = {}
    job.failed = 0
    if len(gs) < 2:
        router._stamp(job)
        return 0
    spec = router.grid.spec
    gs.sort(key=lambda g: -len(g["terminals"]))
    tree_cells = gs[0]["cells"]
    tree = {tuple(c) for c in tree_cells.tolist()}
    hubs: set = set()
    left = gs[1:]
    failed = 0
    while left:
        kd = cKDTree(np.column_stack([spec.x(tree_cells[:, 2]), spec.y(tree_cells[:, 1])]))
        best = None
        for k, g in enumerate(left):
            xy = np.column_stack([spec.x(g["cells"][:, 2]), spec.y(g["cells"][:, 1])])
            d, i = kd.query(xy)
            j = int(np.argmin(d))
            if best is None or d[j] < best[0]:
                best = (float(d[j]), k, tuple(xy[j]), tuple(kd.data[i[j]]))
        _, k, a, b = best
        g = left.pop(k)
        path = router._connect(job, g["cells"], tree, hubs, [a, b], pres, hard=hard)
        if path is None:
            failed += 1
            continue
        router._add_path(job, path)
        for l, y, x in path:
            if l < 0:
                hubs.add((y, x))
            else:
                tree.add((l, y, x))
        tree.update(tuple(c) for c in g["cells"].tolist())
        tree_cells = np.array([c for c in tree], dtype=np.int64)
    router._stamp(job)
    job.failed = failed
    return failed


def prune(router, job) -> int:
    """Take up the net's routes its pours make redundant, longest first:
    each goes when the net's pads stay joined without it, through its
    pour pieces and its remaining routes. Returns how many went.

    The pieces are those of a pour made with the routes in place; a route
    taken up leaves its cells to the pour, which only grows."""
    if not job.paths:
        return 0
    gs = groups(router, job)
    owner: dict[tuple, int] = {}
    for k, g in enumerate(gs):
        for c in g["cells"].tolist():
            owner[tuple(c)] = k
    need = _components(router, gs, owner, job.paths)
    kept = list(job.paths)
    taken = 0
    for path in sorted(job.paths, key=len, reverse=True):
        trial = [p for p in kept if p is not path]
        if _components(router, gs, owner, trial) <= need:
            kept = trial
            taken += 1
    if taken:
        router._unstamp(job)
        job.paths = kept
        used = {(y, x) for p in kept for l, y, x in p if l < 0}
        job.via_at = {k: v for k, v in job.via_at.items() if k in used}
        router._stamp(job)
    return taken


def _components(router, gs, owner, paths) -> int:
    """How many pieces the groups make, joined by the routes: a route
    joins every group it touches and every route it shares a cell with
    (a route of a tree ends on another)."""
    n = len(gs)
    parent = list(range(n + len(paths)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    seen: dict[tuple, int] = {}
    for i, path in enumerate(paths):
        me = n + i
        for l, y, x in path:
            key = (l, y, x)
            if key in seen:
                union(me, seen[key])
            else:
                seen[key] = me
            for li in (range(router.L) if l < 0 else (l,)):
                k = owner.get((li, y, x))
                if k is not None:
                    union(me, k)
                # A via joins whatever lies at its place on any layer.
                j = seen.get((li, y, x)) if l < 0 else seen.get((-1, y, x))
                if j is not None:
                    union(me, j)
    return len({find(k) for k in range(n)})


def unjoined(router, job) -> int:
    """Connections of a poured net still unmade: its groups, joined by its
    routes, less one."""
    gs = groups(router, job)
    if len(gs) < 2:
        return 0
    owner: dict[tuple, int] = {}
    for k, g in enumerate(gs):
        for c in g["cells"].tolist():
            owner[tuple(c)] = k
    return _components(router, gs, owner, job.paths) - 1


def regions(router) -> list[Region]:
    """Each pour's copper as rectangles of whole cells: runs along a row,
    merged down the rows while they stay the same."""
    spec = router.grid.spec
    p = router.pitch
    out = []
    for pr in router.pours:
        if pr.mask is None or not pr.mask.any():
            continue
        layer = router.grid.layers[pr.li]
        open_runs: dict[tuple[int, int], int] = {}
        rects = []
        for j in range(pr.mask.shape[0] + 1):
            runs = set()
            if j < pr.mask.shape[0]:
                row = np.concatenate([[False], pr.mask[j], [False]])
                edges = np.flatnonzero(row[1:] != row[:-1])
                runs = {(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])}
            for key in [k for k in open_runs if k not in runs]:
                rects.append((open_runs.pop(key), j, *key))
            for key in runs:
                open_runs.setdefault(key, j)
        for j0, j1, i0, i1 in rects:
            x0, x1 = float(spec.x(i0)) - p / 2, float(spec.x(i1 - 1)) + p / 2
            y0, y1 = float(spec.y(j0)) - p / 2, float(spec.y(j1 - 1)) + p / 2
            out.append(Region(layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [], pr.net,
                              "pour", source=f"pour:{pr.net}"))
    return out
