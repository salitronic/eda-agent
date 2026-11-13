# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Nets routed as one group of parallel lanes: buses, and pairs.

A bus, three or more nets running between the same two parts, is routed
by a person as a whole: one lane per net at a fixed pitch (width plus
clearance), nested so that no two cross, every bend 45 degrees. Routed
net by net, each takes whatever line the search finds first, and the
next has to go round it.

The group is planned the way a pair is (``diffpair``): one centreline is
searched at the width of the whole group, (n - 1) pitch plus a track, on
a single layer; each net's lane is that centreline offset by its place
in the group, so the lanes nest by construction and keep their pitch
through every turn. Out of each part the pins fan in to the lanes with
45 degree jogs, staggered where two parallel jogs would come too close.
Every lane is then checked exactly, against the copper already there and
against the others; a group that does not check out is left to the
negotiated router whole.

Copper an earlier stage laid only for convenience (a dog-bone's via) may
stand in a lane's way. Rather than detour round it, the search may cross
it at a price; what the lanes cross is taken up, the group laid, and the
dog-bones planned again round the group, and the report says which.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .. import geom
from ..model import Track
from .exact import BEND_TOL, turn
from .fanout import snap8
from .grid import EDGE, KEEPOUT, shape_distance
from .search import Window, find_path

EPS = 1e-6

#: Extra cost, per cell, of crossing copper the group may take up.
SOFT_COST = 4.0

#: tan(22.5 degrees): how far a lane's corner moves along the line when
#: its centreline turns by 45 degrees, per mil of offset.
TAN_HALF = math.tan(math.radians(22.5))


@dataclass
class GroupPlan:
    nets: list[str]                 # lane order, right to left seen from end A
    width: float
    pitch: float
    layer: str
    li: int
    lines: list[list[tuple]]        # per net, pad A centre to pad B centre
    centre: list[tuple] = field(default_factory=list)


def _rot90(u):
    return (-u[1], u[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-12 else (0.0, 0.0)


def end_frame(pads, toward):
    """The direction a row of pads leaves in: across the row, toward the
    point ``toward``, to the nearest of the eight directions. None when the
    pads do not lie in a row across it."""
    xy = np.array([(p.x, p.y) for p in pads])
    m = xy.mean(axis=0)
    if len(pads) >= 2:
        u, s, vt = np.linalg.svd(xy - m)
        row = (float(vt[0][0]), float(vt[0][1]))
        f = _rot90(row)
        if _dot(f, (toward[0] - m[0], toward[1] - m[1])) < 0:
            f = (-f[0], -f[1])
    else:
        f = (toward[0] - m[0], toward[1] - m[1])
    f = snap8(*f)
    spread = max(abs(_dot((x - m[0], y - m[1]), f)) for x, y in xy)
    size = min(min(c.w, c.h) for p in pads for c in p.copper[:1]) if pads[0].copper else 0.0
    if spread > max(size / 2, 2.0):
        return None, (float(m[0]), float(m[1]))
    return f, (float(m[0]), float(m[1]))


def _miter(n1, n2):
    k = 1.0 + _dot(n1, n2)
    return ((n1[0] + n2[0]) / k, (n1[1] + n2[1]) / k)


class _Room:
    """The room round each cell of a window for the group's centreline,
    exactly from the copper near it: hard (copper that stays) and with
    the soft copper too (copper the group may take up)."""

    def __init__(self, ctx, li, box, members, soft, need, reach):
        r = ctx.r
        spec = r.grid.spec
        i0, j0, i1, j1 = box
        self.i0, self.j0 = i0, j0
        X = spec.x(np.arange(i0, i1))[None, :]
        Y = spec.y(np.arange(j0, j1))[:, None]
        far = need + reach + 4 * r.pitch
        hard = np.full((j1 - j0, i1 - i0), far, dtype=np.float64)
        soft_room = hard.copy()
        win = (slice(j0, j1), slice(i0, i1))
        hard = np.minimum(hard, r.grid.dk[li][win])
        hard = np.minimum(hard, r.grid.de[win] - r.rr.edge_clearance)
        hard[r.grid.de[win] <= 0.0] = -np.inf
        rr = r.rr
        cl: dict = {}
        x0, y0 = float(spec.x(i0)), float(spec.y(j0))
        x1, y1 = float(spec.x(i1 - 1)), float(spec.y(j1 - 1))
        for owner, s, tag in ctx.index.near(li, (x0, y0, x1, y1), far):
            if owner in members or owner in (KEEPOUT, EDGE):
                continue
            if owner not in cl:
                cl[owner] = max(rr.pair_clearance(m, owner) for m in members)
            bx0, by0, bx1, by1 = s.bbox
            a0, b0, a1, b1 = spec.window(bx0 - far, by0 - far, bx1 + far, by1 + far)
            a0, b0, a1, b1 = max(a0, i0), max(b0, j0), min(a1, i1), min(b1, j1)
            if a0 >= a1 or b0 >= b1:
                continue
            D = shape_distance(s, X[:, a0 - i0:a1 - i0], Y[b0 - j0:b1 - j0]) - cl[owner]
            sub = (slice(b0 - j0, b1 - j0), slice(a0 - i0, a1 - i0))
            target = soft_room if (tag is not None and tag in soft) else hard
            np.minimum(target[sub], D, out=target[sub])
        self.hard = hard
        self.all = np.minimum(hard, soft_room)
        self.need = need

    def cost(self, y, x):
        """Per cell (grid indices): 1 where the group fits, 1 + SOFT_COST
        where it fits once soft copper is taken up, inf elsewhere (and off
        the window)."""
        yy, xx = np.asarray(y) - self.j0, np.asarray(x) - self.i0
        h_, w_ = self.hard.shape
        inside = (yy >= 0) & (yy < h_) & (xx >= 0) & (xx < w_)
        yy, xx = np.where(inside, yy, 0), np.where(inside, xx, 0)
        h = self.hard[yy, xx] >= self.need
        a = self.all[yy, xx] >= self.need
        return np.where(inside & a, 1.0, np.where(inside & h, 1.0 + SOFT_COST, np.inf))


def _octi(a, b, diag_first):
    """The cells of the 45-degree path from cell a to cell b (y, x): one
    diagonal run and one straight run, in either order."""
    dy, dx = b[0] - a[0], b[1] - a[1]
    sy, sx = (dy > 0) - (dy < 0), (dx > 0) - (dx < 0)
    nd = min(abs(dy), abs(dx))
    ns = max(abs(dy), abs(dx)) - nd
    st = (sy, 0) if abs(dy) > abs(dx) else (0, sx)
    steps = [(sy, sx)] * nd + [st] * ns if diag_first else [st] * ns + [(sy, sx)] * nd
    out = [a]
    for s in steps:
        out.append((out[-1][0] + s[0], out[-1][1] + s[1]))
    return out


def straighten(cells, ok, first=None, last=None):
    """Greedy string pulling: from each cell, as far along the route as a
    two-run 45-degree path stays on allowed cells. A grid route that
    steps E, NE, E, NE becomes one diagonal and one straight run, which
    offsets cleanly into parallel lanes. ``first`` and ``last`` (cell
    steps, (dy, dx)) are the directions the route must leave and arrive
    within 45 degrees of: the ways in and out of its ends."""
    n = len(cells)

    def within(a, b):
        return a is None or (a[0] * b[0] + a[1] * b[1]) > 0 and \
            2 * (a[0] * b[0] + a[1] * b[1]) ** 2 >= (a[0] ** 2 + a[1] ** 2) * (b[0] ** 2 + b[1] ** 2)

    def path(i, j):
        for diag_first in (True, False):
            p = _octi(cells[i], cells[j], diag_first)
            if len(p) < 2:
                return None
            if i == 0 and not within(first, (p[1][0] - p[0][0], p[1][1] - p[0][1])):
                continue
            if j == n - 1 and not within(last, (p[-1][0] - p[-2][0], p[-1][1] - p[-2][1])):
                continue
            if all(ok(c) for c in p):
                return p
        return None

    out = [cells[0]]
    i = 0
    while i < n - 1:
        best = (i + 1, [cells[i], cells[i + 1]])
        step = 1
        lo = i + 1
        while True:
            j = min(n - 1, i + step)
            found = path(i, j)
            if found is None:
                break
            best = (j, found)
            lo = j
            if j == n - 1:
                break
            step *= 2
        hi = min(n - 1, i + step)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            found = path(i, mid)
            if found is None:
                hi = mid
            else:
                best = (mid, found)
                lo = mid
        out.extend(best[1][1:])
        i = best[0]
    return out


def corners(cells):
    """The corner cells of a cell route: its ends and wherever it turns."""
    if len(cells) < 3:
        return list(cells)
    out = [cells[0]]
    for a, b, c in zip(cells, cells[1:], cells[2:]):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    out.append(cells[-1])
    return out


def _step(a, b):
    """The unit cell step from a to b along one of the eight directions,
    and how many steps."""
    dy, dx = b[0] - a[0], b[1] - a[1]
    n = max(abs(dy), abs(dx))
    return ((dy // n, dx // n) if n else (0, 0)), n


def chamfer(cells, ok, min_run: float, pitch: float):
    """Make every turn of a cell route 45 degrees, and every run between two
    turns the same way long enough for the outer lanes.

    A 90-degree corner is cut by a run at 45 degrees to both sides; so is a
    corner made of two 45-degree turns with too short a run between them,
    cut afresh from where its two outer runs would meet. Offset by o, a
    lane's run between two turns the same way is shorter than the
    centreline's by 2 o tan(22.5 degrees): a centreline run shorter than
    that (``min_run``, mils) turns the inner lanes back on themselves.
    Returns None when a corner has no room to be cut that wide."""
    pts = corners(cells)

    def length(s, n):
        return n * pitch * (math.sqrt(2.0) if s[0] and s[1] else 1.0)

    def cells_of(ps):
        out = [ps[0]]
        for a, b in zip(ps, ps[1:]):
            out.extend(_octi(a, b, True)[1:])
        return out

    for _ in range(4 * len(pts) + 8):
        pts = corners(cells_of(pts))
        if len(pts) < 3:
            break
        runs = [_step(a, b) for a, b in zip(pts, pts[1:])]
        fix = None
        for i in range(len(runs) - 1):
            (u, nu), (v, nv) = runs[i], runs[i + 1]
            dot = u[0] * v[0] + u[1] * v[1]
            if dot < 0:
                return None                         # a turn of 135 degrees or more
            if dot == 0:
                fix = (i, pts[i + 1], u, v, i + 1, i + 1)   # a right angle at pts[i + 1]
                break
            if 0 < i + 1 < len(runs) - 1:
                w, _ = runs[i + 2] if i + 2 < len(runs) else ((0, 0), 0)
                cross1 = u[0] * v[1] - u[1] * v[0]
                cross2 = v[0] * w[1] - v[1] * w[0]
                if cross1 and cross1 == cross2 and length(v, nv) < min_run - 1e-9 \
                        and (u[0] * w[0] + u[1] * w[1]) == 0:
                    # Two turns the same way round a run too short: cut the
                    # corner where runs i and i + 2 would meet.
                    x = _meet(pts[i], u, pts[i + 3], w)
                    if x is None:
                        return None
                    fix = (i, x, u, w, i + 1, i + 2)
                    break
        if fix is None:
            break
        i, x, u, v, first, last = fix
        (su, back), (sv, ahead) = _step(pts[i], x), _step(x, pts[last + 1])
        if (back and su != u) or (ahead and sv != v):
            return None                 # the two runs would meet behind their ends
        diag = (u[0] + v[0], u[1] + v[1])
        unit = math.sqrt(2.0) if diag[0] and diag[1] else 2.0
        k_min = int(math.ceil(min_run / (unit * pitch) - 1e-9))
        # A run left at an end of the route turns only once, and needs half
        # of ``min_run``; a run between two corners keeps a cell at least.
        keep_a = int(math.ceil(min_run / 2 / length(u, 1))) if i == 0 else 1
        keep_b = int(math.ceil(min_run / 2 / length(v, 1))) if last + 1 == len(pts) - 1 else 1
        done = False
        for k in range(min(back - keep_a, ahead - keep_b), max(k_min, 1) - 1, -1):
            p = (x[0] - k * u[0], x[1] - k * u[1])
            q = (x[0] + k * v[0], x[1] + k * v[1])
            run = _octi(p, q, True) + _octi(pts[i], p, True) + _octi(q, pts[last + 1], True)
            if all(ok(cc) for cc in run):
                pts = pts[:i + 1] + [c for c in (p, q) if c != pts[i] and c != pts[last + 1]] \
                    + pts[last + 1:]
                done = True
                break
        if not done:
            return None
    return cells_of(pts)


def _meet(a, u, b, w):
    """Where the line through cell a along step u meets the line through
    cell b along step w, if it is a cell."""
    # a + s u = b + t w  (s, t integers)
    det = u[0] * (-w[1]) - u[1] * (-w[0])
    if det == 0:
        return None
    ry, rx = b[0] - a[0], b[1] - a[1]
    s_num = ry * (-w[1]) - rx * (-w[0])
    if s_num % det:
        return None
    s = s_num // det
    return (a[0] + s * u[0], a[1] + s * u[1])


def plan_group(ctx, ends, width: float, pitch: float, li: int, soft=frozenset()):
    """Plan ``ends`` [(net, pad A index, pad B index)] as parallel lanes on
    routing layer ``li``. Returns a GroupPlan, or the reason it could not
    be planned (a string)."""
    r = ctx.r
    b = r.board
    spec = r.grid.spec
    n = len(ends)
    nets = [e[0] for e in ends]
    nids = {r.grid.net_id[nt] for nt in nets}
    pa = [b.pads[e[1]] for e in ends]
    pb = [b.pads[e[2]] for e in ends]
    ma = (sum(p.x for p in pa) / n, sum(p.y for p in pa) / n)
    mb = (sum(p.x for p in pb) / n, sum(p.y for p in pb) / n)
    fa, ma = end_frame(pa, mb)
    fb, mb = end_frame(pb, ma)
    if fa is None or fb is None:
        return "the pins at an end are not in a row"
    na = _rot90(fa)                         # left of travel, leaving A
    nb = _rot90((-fb[0], -fb[1]))           # left of travel, arriving at B
    order_a = sorted(range(n), key=lambda k: _dot((pa[k].x - ma[0], pa[k].y - ma[1]), na))
    order_b = sorted(range(n), key=lambda k: _dot((pb[k].x - mb[0], pb[k].y - mb[1]), nb))
    if order_a != order_b:
        return "the lanes would cross between the two ends"
    offs = {k: pitch * (j - (n - 1) / 2) for j, k in enumerate(order_a)}
    half_w = (n - 1) * pitch / 2 + width / 2
    cmax = max(float(r.rr.clearance_row(i).max()) for i in nids)
    bow = r._bow_at(half_w + r.c)
    need = half_w + bow + TAN_HALF * (n - 1) * pitch / 2 * 0.25 + EPS
    span = math.hypot(mb[0] - ma[0], mb[1] - ma[1])

    row_gap = cmax + width / 2 + 0.5

    def start(m, f, pads, lefts):
        # The forward distance the fan-in needs, then the cell there.
        nl = _rot90(f)
        pos = [_dot((p.x - m[0], p.y - m[1]), nl) for p in pads]
        lat = [lefts[k] - pos[k] for k in range(n)]
        lead = leads(pos, lat, {k: lefts[k] for k in range(n)}, pitch,
                     clear_of_row(pads, f, row_gap))
        far = 0.0
        for k, p in enumerate(pads):
            fwd = _dot((p.x - m[0], p.y - m[1]), f)
            far = max(far, -fwd + lead[k] + abs(lat[k]) + TAN_HALF * abs(lefts[k]))
        far += 2 * r.pitch + width
        return spec.cell(m[0] + f[0] * far, m[1] + f[1] * far)

    lat_a = [offs[k] for k in range(n)]
    lat_b = [-offs[k] for k in range(n)]    # B's outward frame is mirrored
    ca = start(ma, fa, pa, lat_a)
    cb = start(mb, fb, pb, lat_b)
    pt = lambda c: (float(spec.x(c[0])), float(spec.y(c[1])))    # noqa: E731
    pa_, pb_ = pt(ca), pt(cb)
    if _dot((pb_[0] - pa_[0], pb_[1] - pa_[1]), fa) <= 0 or \
            _dot((pa_[0] - pb_[0], pa_[1] - pb_[1]), fb) <= 0:
        return "the two ends are too close for the lanes to fan in"
    reach = cmax + width + pitch
    min_run = 2.0 * TAN_HALF * max(abs(o) for o in offs.values()) + 0.5
    m = int(math.ceil(min_run / r.pitch)) + 1
    path = None
    for half in (max(30.0, 0.15 * span), max(90.0, 0.5 * span), max(250.0, 1.5 * span)):
        half += half_w
        path, room = _search(ctx, li, pa_, pb_, ca, cb, fa, fb, half, nids, soft, need, reach, m)
        if path is not None:
            break
    if path is None:
        return "no room for the lanes on this layer"
    ok = lambda c: np.isfinite(room.cost(np.array([c[0]]), np.array([c[1]]))[0])   # noqa: E731
    step = lambda f: (int(round(f[1])), int(round(f[0])))                          # noqa: E731
    # The straight leads at either end stay as they are; the route between
    # them is pulled straight.
    na_, nb_ = room.leads
    if len(set(path)) != len(path):
        # The leads from the two ends ran into each other.
        return "the two ends are too close for the lanes to fan in"
    middle = straighten(path[na_ - 1:len(path) - nb_ + 1], ok, step(fa), step((-fb[0], -fb[1])))
    cells = chamfer(path[:na_ - 1] + middle + path[len(path) - nb_ + 1:], ok, min_run, r.pitch)
    if cells is None:
        return "a corner has no room for the lanes to turn together"
    cpts = [pt((x, y)) for y, x in corners(cells)]
    return _offset_lanes(ctx, cpts, fa, fb, pa, pb, offs, width, pitch, nets, li, row_gap)


def _search(ctx, li, pa_, pb_, ca, cb, fa, fb, half, nids, soft, need, reach, m=0):
    r = ctx.r
    spec = r.grid.spec
    (x0, y0), (x1, y1) = pa_, pb_
    i0, j0, i1, j1 = spec.window(min(x0, x1) - half, min(y0, y1) - half,
                                 max(x0, x1) + half, max(y0, y1) + half)
    if i0 >= i1 or j0 >= j1:
        return None, None
    from .grid import _seg_dist
    X = spec.x(np.arange(i0, i1))[None, :]
    Y = spec.y(np.arange(j0, j1))[:, None]
    mask = _seg_dist(X, Y, x0, y0, x1, y1) <= half
    room = _Room(ctx, li, (i0, j0, i1, j1), nids, soft, need, reach)
    win = Window(i0, j0, i1 - i0, j1 - j0, 1, mask)
    gy, gx = win.j0 + win.cy, win.i0 + win.cx
    cost = room.cost(gy, gx)[None, :]
    if not win.contains(ca[1], ca[0]) or not win.contains(cb[1], cb[0]):
        return None, room
    if not np.isfinite(room.cost(np.array([ca[1]]), np.array([ca[0]]))[0]) or \
            not np.isfinite(room.cost(np.array([cb[1]]), np.array([cb[0]]))[0]):
        return None, room

    def free(q):
        return win.contains(q[0], q[1]) and np.isfinite(cost[0, win.rank[q[0] - j0, q[1] - i0]])

    def ahead(c, f):
        out = []
        for d in (f, _rotd(f, 45), _rotd(f, -45)):
            q = (c[0] + int(round(d[1])), c[1] + int(round(d[0])))
            if free(q):
                out.append(q)
        return out

    def lead(c, f):
        # Straight on from an end for ``m`` cells (fewer where blocked):
        # the first and last runs are long enough to turn from.
        out = [(c[1], c[0])]
        for _ in range(m):
            q = (out[-1][0] + int(round(f[1])), out[-1][1] + int(round(f[0])))
            if not free(q):
                break
            out.append(q)
        return out

    ext_a, ext_b = lead(ca, fa), lead(cb, fb)
    src = ahead(ext_a[-1], fa)
    dst = ahead(ext_b[-1], fb)
    if not src or not dst:
        return None, room
    sources = np.array([win.node(0, y, x) for y, x in src], dtype=np.int64)
    targets = np.array([win.node(0, y, x) for y, x in dst], dtype=np.int64)
    h = r._potential(win, targets)
    span = math.hypot(x1 - x0, y1 - y0)
    p = find_path(win, cost, np.full(win.M, np.inf), r.move_factor[li:li + 1], r.pitch, h,
                  sources, targets, tuple(a * span + b for a, b in r.DETOURS))
    if p is not None:
        room.leads = (len(ext_a), len(ext_b))
        cells = ext_a + [win.decode(k)[1:] for k in p] + ext_b[::-1]
        return cells, room
    return None, room


def _rotd(u, deg):
    a = math.radians(deg)
    return (u[0] * math.cos(a) - u[1] * math.sin(a), u[0] * math.sin(a) + u[1] * math.cos(a))


def _offset_lanes(ctx, cpts, fa, fb, pa, pb, offs, width, pitch, nets, li, row_gap):
    """Each net's lane: its pin, a 45-degree jog to its place in the group,
    the centreline offset by that place (mitred at every bend), and the
    same at the far end. Checked exactly before it is returned."""
    r = ctx.r
    n = len(nets)
    # Directions along the centreline, with the way in and out added.
    segs = [_unit(q[0] - p[0], q[1] - p[1]) for p, q in zip(cpts, cpts[1:])]
    if not segs:
        return "the lanes have no length"
    dirs = [fa] + segs + [(-fb[0], -fb[1])]
    for u, v in zip(dirs, dirs[1:]):
        if turn((0, 0), u, (u[0] + v[0], u[1] + v[1])) > 45.0 + BEND_TOL:
            return "the centreline turns too sharply"
    normals = [_rot90(u) for u in dirs]
    miters = [_miter(normals[i], normals[i + 1]) for i in range(len(cpts))]
    lines = []
    for k in range(n):
        o = offs[k]
        mid = [(p[0] + o * m[0], p[1] + o * m[1]) for p, m in zip(cpts, miters)]
        # Every offset segment must run the same way as the centreline's.
        for (p, q), u in zip(zip(mid, mid[1:]), segs):
            if _dot((q[0] - p[0], q[1] - p[1]), u) <= EPS:
                return "a centreline run is too short for the outer lanes"
        lines.append(mid)
    fan_a = _fan(pa, fa, [ln[0] for ln in lines], offs, pitch, width, row_gap)
    fan_b = _fan(pb, fb, [ln[-1] for ln in lines], {k: -offs[k] for k in offs}, pitch,
                 width, row_gap)
    if isinstance(fan_a, str):
        return fan_a
    if isinstance(fan_b, str):
        return fan_b
    full = []
    for k in range(n):
        pts = fan_a[k] + lines[k] + fan_b[k][::-1]
        clean = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
                clean.append(p)
        pts = [clean[0]] + [q for p, q, s in zip(clean, clean[1:], clean[2:])
                            if turn(p, q, s) > 1e-6] + [clean[-1]] if len(clean) > 2 else clean
        full.append(pts)
    plan = GroupPlan(nets, width, pitch, r.grid.layers[li], li, full, cpts)
    why = check_lanes(ctx, plan)
    return why or plan


def _fan(pads, f, firsts, offs, pitch, width, row_gap):
    """At one end, each pin's way to the first point of its lane: out along
    ``f`` past the row of pins, a 45-degree jog across to the lane's line,
    then straight on to the lane. Two jogs side by side that would come
    closer than the pitch are staggered (see ``leads``)."""
    nl = _rot90(f)
    n = len(pads)
    lat = [_dot((firsts[k][0] - pads[k].x, firsts[k][1] - pads[k].y), nl) for k in range(n)]
    fwd = [_dot((firsts[k][0] - pads[k].x, firsts[k][1] - pads[k].y), f) for k in range(n)]
    pos = [_dot((p.x, p.y), nl) for p in pads]
    lead = leads(pos, lat, offs, pitch, clear_of_row(pads, f, row_gap))
    out = []
    for k in range(n):
        p = (pads[k].x, pads[k].y)
        a = lead[k]
        jog = abs(lat[k])
        if a + jog > fwd[k] - min(width, 2.0) + 1e-6:
            return "the fan-in at an end has no room"
        pts = [p]
        if a > EPS:
            pts.append((p[0] + a * f[0], p[1] + a * f[1]))
        if jog > EPS:
            pts.append((p[0] + (a + jog) * f[0] + lat[k] * nl[0],
                        p[1] + (a + jog) * f[1] + lat[k] * nl[1]))
        out.append(pts)
    return out


def clear_of_row(pads, f, gap: float) -> list[float]:
    """For each pad, how far straight out along ``f`` its track goes before
    it may turn: past the front of every pad in the row by ``gap``. A jog
    that starts sooner runs beside the next pin, inside its clearance."""
    fronts = []
    for p in pads:
        s = p.shape_on(p.copper[0].layer) if p.copper else None
        reach = 0.0 if s is None else max(_dot((x - p.x, y - p.y), f) for x, y in s.pts) + s.r
        fronts.append(_dot((p.x, p.y), f) + reach)
    front = max(fronts)
    return [max(0.0, front - _dot((p.x, p.y), f) + gap) for p in pads]


def leads(pos, lat, offs, pitch, base=None) -> list[float]:
    """How far each pin goes straight out before its jog: past the row of
    pins (``base``), and further where two jogs side by side would come
    closer than the pitch.

    Two parallel 45-degree jogs that start level, s apart across the row,
    are s / sqrt(2) apart: under the pitch when the pins are close. Where
    the lanes close in on the middle the outer jog waits; where they open
    out, the inner one. ``pos`` is each pin's place across the row,
    ``lat`` how far its jog moves it, ``offs`` its lane's place."""
    n = len(pos)
    out = list(base) if base is not None else [0.0] * n
    order = sorted(range(n), key=lambda k: offs[k])
    for _ in range(n):
        changed = False
        for j, k in zip(order, order[1:]):
            if lat[j] * lat[k] <= 0:
                continue        # moving apart, or one not moving
            s = abs(pos[k] - pos[j])
            need = max(0.0, pitch * math.sqrt(2) - s)
            if need <= 0:
                continue
            # The one on the side the jogs head away from waits: closing in
            # (lat against its side) the outer one, opening out the inner.
            outer, inner = (k, j) if abs(offs[k]) >= abs(offs[j]) else (j, k)
            closing = lat[outer] * offs[outer] < 0
            wait, first = (outer, inner) if closing else (inner, outer)
            want = out[first] + need + 0.5
            if out[wait] < want - 1e-9:
                out[wait] = want
                changed = True
        if not changed:
            break
    return out


def check_lanes(ctx, plan: GroupPlan, ignore_tags=()) -> str | None:
    """Every lane clears the copper already there (other than copper the
    group may take up), and every other lane; every bend off a pad is 45
    degrees or less. None when it does, else why not."""
    r = ctx.r
    li = plan.li
    shapes = []
    for net, pts in zip(plan.nets, plan.lines):
        nid = r.grid.net_id[net]
        segs = [geom.capsule(a[0], a[1], b[0], b[1], plan.width) for a, b in zip(pts, pts[1:])]
        for s in segs:
            if not ctx.clears(nid, s, li, ignore_tags=ignore_tags or ctx.soft_tags):
                return "a lane comes too close to copper already there"
        for p, q, s in zip(pts, pts[1:], pts[2:]):
            if turn(p, q, s) > 45.0 + BEND_TOL and not _on_pad(ctx, net, q, plan.layer):
                return "a lane turns too sharply"
        shapes.append((nid, segs))
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            ni, si = shapes[i]
            nj, sj = shapes[j]
            need = r.rr.pair_clearance(ni, nj)
            for a in si:
                for b in sj:
                    if geom.bboxes_near(a, b, need) and geom.clearance(a, b) < need - 1e-3:
                        return "two lanes come too close"
    return None


def _on_pad(ctx, net, q, layer) -> bool:
    point = geom.circle(q[0], q[1], 0.0)
    for p in ctx.pads_of.get(net, ()):
        s = p.shape_on(layer)
        if s is not None and geom.clearance(point, s) <= 1e-6:
            return True
    return False


def lay_group(ctx, plan: GroupPlan, tag) -> list[Track]:
    tracks = []
    for net, pts in zip(plan.nets, plan.lines):
        for a, b in zip(pts, pts[1:]):
            tracks.append(Track(plan.layer, a[0], a[1], b[0], b[1], plan.width, net))
    ctx.lay([], tracks, tag=tag)
    return tracks


def lane_length(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


# ---------------------------------------------------------------------------
# Buses
# ---------------------------------------------------------------------------

def find_buses(ctx) -> list[dict]:
    """Groups of ``bus_min`` or more two-pad nets between the same two
    parts, whose pins sit side by side on one side of each part with no
    other connected pin between them. A run of the pins broken by another
    net's pin splits into runs; each run long enough is a bus."""
    r = ctx.r
    b = r.board
    index = {id(p): i for i, p in enumerate(b.pads)}
    pairs: dict[tuple, list] = {}
    for net, pads in ctx.pads_of.items():
        if len(pads) != 2 or not ctx.routed(net) or net in ctx.planes or net in ctx.pours:
            continue
        p, q = pads
        if not p.comp or not q.comp or p.comp == q.comp:
            continue
        if (p.comp, q.comp) > (q.comp, p.comp):
            p, q = q, p
        i, j = index[id(p)], index[id(q)]
        if i in r.laid_via or j in r.laid_via or i in r.laid_joined or j in r.laid_joined:
            continue
        pairs.setdefault((p.comp, q.comp), []).append((net, i, j))
    out = []
    for (ca, cb), nets in pairs.items():
        if len(nets) < r.options.bus_min:
            continue
        for run in _runs(ctx, ca, nets, 1, ctx.comp_centre(cb)):
            for sub in _runs(ctx, cb, run, 2, ctx.comp_centre(ca)):
                if len(sub) >= r.options.bus_min:
                    out.append({"parts": (ca, cb), "ends": sub})
    out.sort(key=lambda g: -len(g["ends"]))
    return out


def _runs(ctx, comp, ends, side, toward):
    """Split ``ends`` into runs whose pins on ``comp`` (end ``side`` of each
    entry) lie next to each other along the part's edge, with no pin of
    another routed net between. A pin longer than wide faces out along its
    axis; a round or square one (a header's) faces the far part."""
    from .fanout import _long_axis, outward

    r = ctx.r
    b = r.board
    mine = {e[side]: e for e in ends}
    pins = [i for i in ctx.comp_pads.get(comp, []) if b.pads[i].copper]
    if len(mine) < 2:
        return [ends]
    c = ctx.comp_centre(comp)
    face = snap8(toward[0] - c[0], toward[1] - c[1])
    dirs = {i: (outward(b.pads[i], c) if _long_axis(b.pads[i]) is not None else face)
            for i in pins}
    groups: dict[tuple, list] = {}
    for i in mine:
        groups.setdefault(tuple(round(v, 3) for v in dirs[i]), []).append(i)
    out = []
    for d, members in groups.items():
        if len(members) < 2:
            continue
        nl = _rot90(d)
        side_pins = [i for i in pins if tuple(round(v, 3) for v in dirs[i]) == d]
        side_pins.sort(key=lambda i: _dot((b.pads[i].x, b.pads[i].y), nl))
        run = []
        for i in side_pins:
            if i in mine:
                run.append(mine[i])
                continue
            busy = b.pads[i].net and len(ctx.pads_of.get(b.pads[i].net, [])) >= 2
            if busy:
                if len(run) >= 2:
                    out.append(run)
                run = []
        if len(run) >= 2:
            out.append(run)
    return out


def route_buses(ctx) -> None:
    """Plan and lay each bus as nested lanes; a bus that cannot be planned
    is left to the negotiated router. Dog-bones in a bus's way are taken
    up and planned again round it."""
    from .fanout import _dogbone

    r = ctx.r
    b = r.board
    rep = r.stage_report
    for k, bus in enumerate(find_buses(ctx)):
        ends = bus["ends"]
        nets = [e[0] for e in ends]
        if any(nt in r.laid_nets for nt in nets):
            continue
        width = max(ctx.width(nt) for nt in nets)
        nids = [r.grid.net_id[nt] for nt in nets]
        gap = max(r.rr.pair_clearance(a, c) for a in nids for c in nids if a != c)
        pitch = width + gap + 0.01
        entry = {"parts": list(bus["parts"]), "nets": nets, "lanes": 0, "ripped_up": [],
                 "layer": None, "reason": ""}
        layers = _common_layers(r, [b.pads[e[1]] for e in ends] + [b.pads[e[2]] for e in ends])
        if not layers:
            entry["reason"] = "the two parts share no routing layer"
            rep.buses.append(entry)
            continue
        soft = {t for t in ctx.objects if t[0] == "dogbone"}
        ctx.soft_tags = soft
        plan = None
        why = "no layer"
        for li in layers:
            plan = plan_group(ctx, ends, width, pitch, li, soft)
            if not isinstance(plan, str):
                break
            why = plan
            plan = None
        ctx.soft_tags = ()
        if plan is None:
            entry["reason"] = why
            rep.buses.append(entry)
            continue
        ripped = _crossed_tags(ctx, plan, soft) if soft else []
        undo = []
        for tag in ripped:
            undo.append((tag, ctx.unlay(tag)))
            r.laid_via.pop(tag[1], None)
        why = check_lanes(ctx, plan, ignore_tags=())
        if why:
            for tag, (vias, tracks) in undo:
                ctx.lay(vias, tracks, tag=tag)
                if vias:
                    r.laid_via[tag[1]] = vias[0]
            entry["reason"] = why
            rep.buses.append(entry)
            continue
        lay_group(ctx, plan, tag=("bus", k))
        r.laid_nets.update(nets)
        moved = []
        for tag, _ in undo:
            again = _dogbone(ctx, tag[1])
            moved.append({"pad": b.pads[tag[1]].key, "replanned": bool(again)})
        entry.update(lanes=len(nets), layer=plan.layer, ripped_up=moved,
                     lengths=[round(lane_length(p), 1) for p in plan.lines])
        rep.buses.append(entry)


def _common_layers(r, pads) -> list[int]:
    common = None
    for p in pads:
        ls = {r.grid.layer_index[c.layer] for c in p.copper if c.layer in r.grid.layer_index}
        common = ls if common is None else common & ls
    return sorted(common or [])


def _crossed_tags(ctx, plan: GroupPlan, soft) -> list:
    """The soft copper (dog-bones) the planned lanes come too close to."""
    r = ctx.r
    out = []
    for net, pts in zip(plan.nets, plan.lines):
        nid = r.grid.net_id[net]
        for a, b in zip(pts, pts[1:]):
            s = geom.capsule(a[0], a[1], b[0], b[1], plan.width)
            for owner, tag in ctx.index.blockers(s, plan.li, {nid}, ctx.clearance(nid),
                                                 edge=r.rr.edge_clearance, reach=ctx.reach(nid)):
                if tag in soft and tag not in out:
                    out.append(tag)
    return out
