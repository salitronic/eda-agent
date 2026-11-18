# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The board rasterised for a maze router.

Each routing layer becomes a grid of cell centres. For every cell the
grid keeps the distance to the nearest fixed copper and whose it is, and
the distance to the nearest fixed copper of ANY OTHER owner. With both,
"how far is the nearest copper that is foreign to net N" is one lookup
for every N: the first distance when the nearest owner is not N, else the
second. A track centre may sit on a cell when that foreign distance is at
least the clearance to that owner plus half the track's width.

Keep-outs and the board edge have fields of their own. Mixed in with
copper they took the nearest place from copper a few hundredths of a mil
further away that needed far more clearance, and a via went in 1.6 mil
from a pad that wanted 5.

Distances are measured from cell centres to the exact shapes (``geom``),
so the only approximation is where a centre line runs between cells;
the exact DRC after routing is the judge of that.

Owners are net ids from 1, and three that belong to no net:
``NETLESS`` (copper on no net, foreign to every net); ``EDGE`` (the
board outline and cutouts) and ``KEEPOUT`` are the two separate fields.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import geom
from ..model import LayoutBoard

NETLESS = 0
EDGE = -1
KEEPOUT = -2
NONE = -9

FAR = np.float32(1e6)

#: Altium's keep-out layer applies to every copper layer.
KEEPOUT_LAYER = "KeepOutLayer"


@dataclass(frozen=True)
class GridSpec:
    ox: float        # centre of cell (0, 0), mils
    oy: float
    pitch: float
    nx: int
    ny: int

    def x(self, i):
        return self.ox + np.asarray(i) * self.pitch

    def y(self, j):
        return self.oy + np.asarray(j) * self.pitch

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return (int(round((x - self.ox) / self.pitch)),
                int(round((y - self.oy) / self.pitch)))

    def window(self, x0, y0, x1, y1) -> tuple[int, int, int, int]:
        """Cell index range [i0, i1) x [j0, j1) covering a box, clipped."""
        i0 = max(0, int(math.floor((x0 - self.ox) / self.pitch)))
        j0 = max(0, int(math.floor((y0 - self.oy) / self.pitch)))
        i1 = min(self.nx, int(math.ceil((x1 - self.ox) / self.pitch)) + 1)
        j1 = min(self.ny, int(math.ceil((y1 - self.oy) / self.pitch)) + 1)
        return i0, j0, i1, j1


def shape_distance(s: geom.Shape, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Distance from each point to the shape's edge; 0 inside."""
    if s.kind == "point":
        (cx, cy), = s.pts
        d = np.hypot(X - cx, Y - cy)
    elif s.kind == "segment":
        d = _seg_dist(X, Y, *s.pts[0], *s.pts[1])
    else:
        d = np.full(np.broadcast(X, Y).shape, np.inf)
        inside = np.zeros(d.shape, dtype=bool)
        for ring in [s.pts] + list(s.holes):
            n = len(ring)
            for k in range(n):
                ax, ay = ring[k]
                bx, by = ring[(k + 1) % n]
                d = np.minimum(d, _seg_dist(X, Y, ax, ay, bx, by))
                crosses = (ay > Y) != (by > Y)
                with np.errstate(divide="ignore", invalid="ignore"):
                    xc = ax + (Y - ay) * (bx - ax) / (by - ay)
                inside ^= crosses & (X < xc)
        d = np.where(inside, 0.0, d)
    return np.maximum(d - s.r, 0.0)


def inner_depth(s: geom.Shape, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """How far inside the shape each point is: the distance to its edge,
    positive inside, negative outside."""
    if s.kind == "point":
        (cx, cy), = s.pts
        return s.r - np.hypot(X - cx, Y - cy)
    if s.kind == "segment":
        return s.r - _seg_dist(X, Y, *s.pts[0], *s.pts[1])
    d = np.full(np.broadcast(X, Y).shape, np.inf)
    inside = np.zeros(d.shape, dtype=bool)
    for ring in [s.pts] + list(s.holes):
        n = len(ring)
        for k in range(n):
            ax, ay = ring[k]
            bx, by = ring[(k + 1) % n]
            d = np.minimum(d, _seg_dist(X, Y, ax, ay, bx, by))
            crosses = (ay > Y) != (by > Y)
            with np.errstate(divide="ignore", invalid="ignore"):
                xc = ax + (Y - ay) * (bx - ax) / (by - ay)
            inside ^= crosses & (X < xc)
    return np.where(inside, s.r + d, s.r - d)


def _seg_dist(X, Y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0.0:
        return np.hypot(X - ax, Y - ay)
    t = np.clip(((X - ax) * dx + (Y - ay) * dy) / L2, 0.0, 1.0)
    return np.hypot(X - (ax + t * dx), Y - (ay + t * dy))


def fill_polygon(spec: GridSpec, rings) -> np.ndarray:
    """Cells whose centres lie inside the rings (even-odd), by scanline."""
    inside = np.zeros((spec.ny, spec.nx), dtype=bool)
    ys = spec.y(np.arange(spec.ny))
    xs = spec.x(np.arange(spec.nx))
    edges = []
    for ring in rings:
        n = len(ring)
        for k in range(n):
            edges.append((*ring[k], *ring[(k + 1) % n]))
    if not edges:
        return inside
    e = np.array(edges, dtype=float)
    ax, ay, bx, by = e[:, 0], e[:, 1], e[:, 2], e[:, 3]
    for j, y in enumerate(ys):
        m = (ay > y) != (by > y)
        if not m.any():
            continue
        xc = ax[m] + (y - ay[m]) * (bx[m] - ax[m]) / (by[m] - ay[m])
        xc.sort()
        # Even-odd: a cell is inside when an odd number of crossings lie
        # to its right.
        count_right = len(xc) - np.searchsorted(xc, xs, side="right")
        inside[j] = (count_right % 2) == 1
    return inside


class RouteGrid:
    """Distance fields per routing layer, over a uniform grid."""

    def __init__(self, board: LayoutBoard, pitch: float, reach: float,
                 layers: list[str] | None = None, grow=None, field_of=None, n_fields: int = 1,
                 pad_owner=None):
        self.board = board
        # Netless pads a clearance rule names, by id(pad): their owner and
        # the name it goes by (RouteRules PAD_OWNER).
        self.pad_owner, self.pad_owner_net = pad_owner or ({}, {})
        # grow(kind, net, comp, layer, smd, polygon): how far a fixed
        # object's shape is grown, for a clearance rule its net alone
        # does not answer (see RouteRules.grow).
        self.grow = grow
        self.layers = layers or board.signal_layers()
        self.layer_index = {n: i for i, n in enumerate(self.layers)}
        self.reach = reach
        xs = [p[0] for p in board.outline] or [0.0]
        ys = [p[1] for p in board.outline] or [0.0]
        ox = math.floor(min(xs) / pitch) * pitch
        oy = math.floor(min(ys) / pitch) * pitch
        nx = int(math.ceil((max(xs) - ox) / pitch)) + 1
        ny = int(math.ceil((max(ys) - oy) / pitch)) + 1
        self.spec = GridSpec(ox, oy, pitch, nx, ny)
        L = len(self.layers)
        # The nearest two owners and their distances, per field: owners
        # that need the same clearance from everything share one. Kept
        # for the nearest two of ALL owners, a net that must keep 3 mm
        # was hidden behind two nearer ones that need 0.15 mm, and a pour
        # came 108 mil from it. ``field_of(owner)`` names the field.
        self.field_of = field_of or (lambda owner: 0)
        F = max(1, n_fields)
        self.fd1 = np.full((F, L, ny, nx), FAR, dtype=np.float32)
        self.fd2 = np.full((F, L, ny, nx), FAR, dtype=np.float32)
        self.fid1 = np.full((F, L, ny, nx), NONE, dtype=np.int32)
        self.fid2 = np.full((F, L, ny, nx), NONE, dtype=np.int32)
        self.d1, self.d2, self.id1, self.id2 = self.fd1[0], self.fd2[0], self.fid1[0], self.fid2[0]
        self.dk = np.full((L, ny, nx), FAR, dtype=np.float32)   # keep-outs
        self.de = np.full((ny, nx), FAR, dtype=np.float32)      # board edge
        # The shapes the fields were built from, kept for exact checks.
        self.shapes: list[tuple[int, int, geom.Shape]] = []
        self.edge_shapes: list[geom.Shape] = []
        nets = board.nets()
        self.net_id = {n: i + 1 for i, n in enumerate(nets)}
        for pid, owned in self.pad_owner_net.items():
            self.net_id[owned] = self.pad_owner[pid]
        self.net_name = {i: n for n, i in self.net_id.items()}

    # -- building --------------------------------------------------------

    def owner(self, net: str) -> int:
        return self.net_id.get(net, NETLESS) if net else NETLESS

    def add_shape(self, s: geom.Shape, owner: int, layer: int, reach: float | None = None):
        if owner == EDGE:
            self.edge_shapes.append(s)
        else:
            self.shapes.append((layer, owner, s))
        reach = self.reach if reach is None else reach
        x0, y0, x1, y1 = s.bbox
        i0, j0, i1, j1 = self.spec.window(x0 - reach, y0 - reach, x1 + reach, y1 + reach)
        if i0 >= i1 or j0 >= j1:
            return
        X = self.spec.x(np.arange(i0, i1))[None, :]
        Y = self.spec.y(np.arange(j0, j1))[:, None]
        D = shape_distance(s, X, Y).astype(np.float32)
        win = (slice(j0, j1), slice(i0, i1))
        if owner == KEEPOUT:
            np.minimum(self.dk[layer][win], D, out=self.dk[layer][win])
        elif owner == EDGE:
            np.minimum(self.de[win], D, out=self.de[win])
        else:
            self._merge(layer, win, D, owner)

    def remove_shapes(self, items) -> None:
        """Take shapes out again, (layer, owner, shape) as added: the
        fields round them are rebuilt from the shapes that remain."""
        gone = {id(s) for _, _, s in items}
        if not gone:
            return
        self.shapes = [it for it in self.shapes if id(it[2]) not in gone]
        by_layer: dict[int, list] = {}
        for li, _, s in items:
            by_layer.setdefault(li, []).append(s)
        reach = self.reach
        for li, shapes in by_layer.items():
            x0 = min(s.bbox[0] for s in shapes) - reach
            y0 = min(s.bbox[1] for s in shapes) - reach
            x1 = max(s.bbox[2] for s in shapes) + reach
            y1 = max(s.bbox[3] for s in shapes) + reach
            i0, j0, i1, j1 = self.spec.window(x0, y0, x1, y1)
            if i0 >= i1 or j0 >= j1:
                continue
            win = (slice(j0, j1), slice(i0, i1))
            self.fd1[(slice(None), li) + win] = FAR
            self.fd2[(slice(None), li) + win] = FAR
            self.fid1[(slice(None), li) + win] = NONE
            self.fid2[(slice(None), li) + win] = NONE
            for layer, owner, s in self.shapes:
                if layer != li or owner == KEEPOUT:
                    continue
                bx0, by0, bx1, by1 = s.bbox
                a0, b0, a1, b1 = self.spec.window(bx0 - reach, by0 - reach, bx1 + reach, by1 + reach)
                a0, b0, a1, b1 = max(a0, i0), max(b0, j0), min(a1, i1), min(b1, j1)
                if a0 >= a1 or b0 >= b1:
                    continue
                X = self.spec.x(np.arange(a0, a1))[None, :]
                Y = self.spec.y(np.arange(b0, b1))[:, None]
                D = shape_distance(s, X, Y).astype(np.float32)
                self._merge(li, (slice(b0, b1), slice(a0, a1)), D, owner)

    def _merge(self, layer, win, D, owner):
        f = self.field_of(owner)
        d1 = self.fd1[f, layer][win]
        d2 = self.fd2[f, layer][win]
        i1 = self.fid1[f, layer][win]
        i2 = self.fid2[f, layer][win]
        closer = D < d1
        shift = closer & (i1 != owner)
        d2[shift] = d1[shift]
        i2[shift] = i1[shift]
        d1[closer] = D[closer]
        i1[closer] = owner
        second = ~closer & (i1 != owner) & (D < d2)
        d2[second] = D[second]
        i2[second] = owner

    def build(self) -> "RouteGrid":
        b = self.board
        L = len(self.layers)
        for p in b.pads:
            o = self.pad_owner.get(id(p)) or self.owner(p.net)
            net = self.pad_owner_net.get(id(p), p.net)
            for c in p.copper:
                li = self.layer_index.get(c.layer)
                if li is not None:
                    s = self._grown(p.shape_on(c.layer), "pad", net, p.comp, c.layer, p.is_smd)
                    self.add_shape(s, o, li)
            if p.hole > 0 and not p.plated:
                # An unplated hole is foreign to every net on every layer.
                hole = geom.circle(p.x, p.y, p.hole)
                for li in range(L):
                    self.add_shape(hole, NETLESS, li)
        for t in b.tracks:
            s = t.shape() if t.keepout else self._grown(t.shape(), "track", t.net, t.comp, t.layer)
            self._add_drawn(s, t.net, t.layer, t.keepout)
        for a in b.arcs:
            for s in a.shapes():
                if not a.keepout:
                    s = self._grown(s, "arc", a.net, a.comp, a.layer)
                self._add_drawn(s, a.net, a.layer, a.keepout)
        for v in b.vias:
            for layer in b.layers_between(v.low_layer, v.high_layer):
                li = self.layer_index.get(layer)
                if li is not None:
                    s = self._grown(v.shape_on(layer), "via", v.net, v.comp, layer)
                    self.add_shape(s, self.owner(v.net), li)
        for r in b.regions:
            if r.kind in ("pour_boundary", "cutout", "plane"):
                continue
            keep = r.kind == "keepout" or r.keepout
            s = r.shape() if keep else self._grown(r.shape(), "region", r.net, r.comp, r.layer,
                                                    polygon=r.kind == "pour")
            self._add_drawn(s, r.net, r.layer, keep)
        self._add_edge()
        return self

    def _grown(self, s, kind, net, comp, layer, smd=False, polygon=False):
        g = self.grow(kind, net, comp, layer, smd, polygon) if self.grow else 0.0
        if g <= 0.0:
            return s
        return geom.Shape(s.kind, s.pts, s.r + g, s.holes)

    def _add_drawn(self, s, net, layer, keepout):
        if keepout:
            targets = range(len(self.layers)) if layer == KEEPOUT_LAYER else \
                [self.layer_index[layer]] if layer in self.layer_index else []
            for li in targets:
                self.add_shape(s, KEEPOUT, li)
            return
        li = self.layer_index.get(layer)
        if li is not None:
            self.add_shape(s, self.owner(net), li)

    def _add_edge(self):
        b = self.board
        if len(b.outline) < 3:
            return
        rings = [b.outline] + list(b.cutouts)
        outside = ~fill_polygon(self.spec, rings)
        for ring in rings:
            n = len(ring)
            for k in range(n):
                seg = geom.Shape("segment", (tuple(ring[k]), tuple(ring[(k + 1) % n])))
                self.add_shape(seg, EDGE, 0)
        self.de[outside] = 0.0

    # -- queries -----------------------------------------------------------

    def slack(self, owner: int, layer: int, win, clearance, edge_clearance: float) -> np.ndarray:
        """Room left round a point of ``owner``'s copper: the distance to
        foreign copper less the clearance it needs, least over the
        nearest two owners of each field, the keep-outs and the board edge.

        ``clearance(owners)`` gives the clearance to each owner id.
        """
        out = np.minimum(self.dk[layer][win], self.de[win] - edge_clearance)
        for f in range(self.fd1.shape[0]):
            i1, i2 = self.fid1[f, layer][win], self.fid2[f, layer][win]
            d1, d2 = self.fd1[f, layer][win], self.fd2[f, layer][win]
            mine = (i1 == owner) & (owner != NETLESS)
            s2 = d2 - clearance(i2)
            first = np.where(mine, s2, d1 - clearance(i1))
            # Both nearest owners foreign: the further may need more room.
            second = np.where(~mine & ((i2 != owner) | (owner == NETLESS)), s2, np.inf)
            out = np.minimum(out, np.minimum(first, second))
        return out
