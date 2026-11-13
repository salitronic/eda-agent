# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Exact copper geometry: every shape is a core grown by a radius.

A round pad or a via is a point grown by its radius. An oval pad and a track
are a segment grown by half their width. A rounded-rectangle pad is its inner
rectangle grown by the corner radius. A rectangle, an octagon and a poured
region are polygons grown by nothing.

That one representation (a Minkowski sum of a core and a disk) makes the
clearance between ANY two shapes exact and cheap: the distance between their
cores, minus both radii. The old router boxed every pad, which is why a
0.5 mm pitch part could pass its own check and still violate on the board.

Units are mils throughout. Angles are degrees, counter-clockwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass(frozen=True)
class Shape:
    """A core (point, segment or polygon) grown by ``r``.

    ``pts`` holds one point, two points, or a polygon's vertices (the
    closing edge is implicit). ``holes`` are polygon holes, used by poured
    regions; a point inside a hole is outside the shape.
    """

    kind: str  # "point" | "segment" | "poly"
    pts: tuple[Point, ...]
    r: float = 0.0
    holes: tuple[tuple[Point, ...], ...] = field(default=())

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return (min(xs) - self.r, min(ys) - self.r,
                max(xs) + self.r, max(ys) + self.r)


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

def _rot(x: float, y: float, deg: float) -> Point:
    if not deg:
        return (x, y)
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (x * c - y * s, x * s + y * c)


def circle(cx: float, cy: float, diameter: float) -> Shape:
    return Shape("point", ((cx, cy),), diameter / 2.0)


def capsule(x1: float, y1: float, x2: float, y2: float, width: float) -> Shape:
    """A track, or anything drawn with a round pen of ``width``."""
    if x1 == x2 and y1 == y2:
        return Shape("point", ((x1, y1),), width / 2.0)
    return Shape("segment", ((x1, y1), (x2, y2)), width / 2.0)


def stroke_outline(x1: float, y1: float, x2: float, y2: float, width: float) -> list[Point]:
    """A round-pen stroke as a polygon, its ends half octagons round the
    end points: it holds every point of the real stroke."""
    r = width / 2.0 / math.cos(math.pi / 8)
    a = math.atan2(y2 - y1, x2 - x1) if (x1, y1) != (x2, y2) else 0.0
    pts = []
    for cx, cy, start in ((x2, y2, a - math.pi / 2), (x1, y1, a + math.pi / 2)):
        for i in range(5):
            t = start + i * math.pi / 4
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def outline(s: Shape) -> list[Point]:
    """A shape's outline as one ring, for drawing (a polygon's rounding left
    out)."""
    if s.kind == "poly":
        return list(s.pts)
    a = s.pts[0]
    b = s.pts[-1]
    return stroke_outline(a[0], a[1], b[0], b[1], 2 * s.r)


def polygon(pts, holes=()) -> Shape:
    pts = tuple((float(x), float(y)) for x, y in pts)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return Shape("poly", pts, 0.0,
                 tuple(tuple((float(x), float(y)) for x, y in h) for h in holes))


def pad_shape(cx: float, cy: float, w: float, h: float, shape: str,
              rotation: float = 0.0, corner_pct: float = 0.0,
              offset: Point = (0.0, 0.0)) -> Shape:
    """One pad's copper on one layer, in world coordinates.

    ``shape`` is Altium's word for it: round (a circle when w == h, an
    oval otherwise), rect, roundrect or octagon. ``corner_pct`` is the
    rounded rectangle's corner radius as a percentage of half the SHORTER
    side, which is how Altium stores it. ``offset`` is the copper's offset
    from the hole, in the pad's own frame.
    """
    ox, oy = _rot(offset[0], offset[1], rotation)
    cx, cy = cx + ox, cy + oy
    shape = (shape or "round").lower()
    if shape in ("round", "rounded", "oval", "obround", "circle"):
        if abs(w - h) < 1e-9:
            return circle(cx, cy, w)
        half_len = abs(w - h) / 2.0
        if w > h:
            dx, dy = _rot(half_len, 0.0, rotation)
        else:
            dx, dy = _rot(0.0, half_len, rotation)
        return Shape("segment", ((cx - dx, cy - dy), (cx + dx, cy + dy)),
                     min(w, h) / 2.0)
    if shape in ("roundrect", "roundedrect", "rounded_rectangle",
                 "roundedrectangular"):
        cr = max(0.0, min(100.0, corner_pct)) / 100.0 * min(w, h) / 2.0
        return _rect(cx, cy, w - 2 * cr, h - 2 * cr, rotation, cr)
    if shape in ("octagon", "octagonal"):
        # Altium chamfers each corner by a quarter of the shorter side.
        c = min(w, h) / 4.0
        hw, hh = w / 2.0, h / 2.0
        local = [(-hw + c, -hh), (hw - c, -hh), (hw, -hh + c), (hw, hh - c),
                 (hw - c, hh), (-hw + c, hh), (-hw, hh - c), (-hw, -hh + c)]
        pts = [_rot(x, y, rotation) for x, y in local]
        return Shape("poly", tuple((cx + x, cy + y) for x, y in pts), 0.0)
    return _rect(cx, cy, w, h, rotation, 0.0)


def _rect(cx, cy, w, h, rotation, r) -> Shape:
    hw, hh = max(w, 0.0) / 2.0, max(h, 0.0) / 2.0
    if hw == 0.0 and hh == 0.0:
        return Shape("point", ((cx, cy),), r)
    if hw == 0.0 or hh == 0.0:
        dx, dy = _rot(hw, hh, rotation)
        return Shape("segment", ((cx - dx, cy - dy), (cx + dx, cy + dy)), r)
    local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    pts = [_rot(x, y, rotation) for x, y in local]
    return Shape("poly", tuple((cx + x, cy + y) for x, y in pts), r)


def arc_points(cx: float, cy: float, radius: float, a1: float, a2: float,
               tol: float = 0.05) -> list[Point]:
    """Vertices along an arc from ``a1`` to ``a2`` (degrees, CCW).

    Chords are short enough that no point of the true arc is further than
    ``tol`` from them, so a clearance judged on the chords errs by at most
    ``tol``.
    """
    sweep = (a2 - a1) % 360.0
    if sweep == 0.0 and a1 != a2:
        sweep = 360.0
    if radius <= tol:
        n = 1
    else:
        step = 2.0 * math.degrees(math.acos(max(-1.0, 1.0 - tol / radius)))
        n = max(1, int(math.ceil(sweep / max(step, 1e-6))))
    return [(cx + radius * math.cos(math.radians(a1 + sweep * i / n)),
             cy + radius * math.sin(math.radians(a1 + sweep * i / n)))
            for i in range(n + 1)]


def arc_track(cx, cy, radius, a1, a2, width, tol=0.05) -> list[Shape]:
    pts = arc_points(cx, cy, radius, a1, a2, tol)
    return [capsule(*pts[i], *pts[i + 1], width) for i in range(len(pts) - 1)]


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def _pt_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    if d2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / d2
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orient(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _segs_cross(a, b, c, d) -> bool:
    d1 = _orient(*c, *d, *a)
    d2 = _orient(*c, *d, *b)
    d3 = _orient(*a, *b, *c)
    d4 = _orient(*a, *b, *d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return True
    return False


def _seg_seg(a, b, c, d) -> float:
    if _segs_cross(a, b, c, d):
        return 0.0
    return min(_pt_seg(*a, *c, *d), _pt_seg(*b, *c, *d),
               _pt_seg(*c, *a, *b), _pt_seg(*d, *a, *b))


def _inside_ring(px, py, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > py) != (yj > py):
            x_at = xi + (py - yi) * (xj - xi) / (yj - yi)
            if px < x_at:
                inside = not inside
        j = i
    return inside


def point_in_poly(px: float, py: float, s: Shape) -> bool:
    """Inside the polygon's outer ring and outside every hole."""
    if not _inside_ring(px, py, s.pts):
        return False
    return not any(_inside_ring(px, py, h) for h in s.holes)


def _edges(s: Shape):
    rings = [s.pts] + list(s.holes) if s.kind == "poly" else [s.pts]
    for ring in rings:
        n = len(ring)
        if s.kind != "poly":
            if n == 2:
                yield ring[0], ring[1]
            continue
        for i in range(n):
            yield ring[i], ring[(i + 1) % n]


def core_distance(a: Shape, b: Shape) -> float:
    """Distance between the two cores, ignoring both radii. 0 if they meet."""
    if a.kind == "point" and b.kind == "point":
        return math.hypot(a.pts[0][0] - b.pts[0][0], a.pts[0][1] - b.pts[0][1])
    if a.kind == "poly" or b.kind == "poly":
        p, q = (a, b) if a.kind == "poly" else (b, a)
        # A core with any point inside the polygon overlaps it.
        if point_in_poly(q.pts[0][0], q.pts[0][1], p):
            return 0.0
        if q.kind == "poly" and point_in_poly(p.pts[0][0], p.pts[0][1], q):
            return 0.0
        best = math.inf
        if q.kind == "point":
            px, py = q.pts[0]
            for e0, e1 in _edges(p):
                best = min(best, _pt_seg(px, py, *e0, *e1))
            return best
        for e0, e1 in _edges(p):
            for f0, f1 in _edges(q):
                best = min(best, _seg_seg(e0, e1, f0, f1))
                if best == 0.0:
                    return 0.0
        return best
    if a.kind == "point":
        return _pt_seg(*a.pts[0], *b.pts[0], *b.pts[1])
    if b.kind == "point":
        return _pt_seg(*b.pts[0], *a.pts[0], *a.pts[1])
    return _seg_seg(a.pts[0], a.pts[1], b.pts[0], b.pts[1])


def clearance(a: Shape, b: Shape) -> float:
    """Edge-to-edge gap. Negative when the copper overlaps."""
    return core_distance(a, b) - a.r - b.r


# ---------------------------------------------------------------------------
# Large polygons
# ---------------------------------------------------------------------------

class PolyIndex:
    """A big polygon (a pour, a plane) indexed for fast queries.

    A poured region on a real board runs to thousands of vertices, with a
    hole round every foreign pad. Walking every edge for every nearby track
    took minutes on a large board, almost all of it in edge-to-edge
    tests. Here the edges sit in a uniform grid, so a distance query looks
    only at edges near the query and stops at a cutoff; and edges are also
    bucketed by row, so an inside test counts crossings only among edges
    that span the query's row.
    """

    def __init__(self, shape: Shape, cell: float = 25.0):
        self.shape = shape
        self.cell = cell
        self.edges: list[tuple[Point, Point]] = list(_edges(shape))
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.rows: dict[int, list[int]] = {}
        for i, (p, q) in enumerate(self.edges):
            for key in self._cells_along(p, q):
                self.grid.setdefault(key, []).append(i)
            y0, y1 = sorted((p[1], q[1]))
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                self.rows.setdefault(gy, []).append(i)

    def _cells_along(self, p: Point, q: Point) -> set[tuple[int, int]]:
        """Every cell the segment passes through, and a few beside it.

        Cut into pieces no longer than a cell, each piece's box spans at
        most 2x2 cells, so a long diagonal edge costs cells in proportion
        to its length, not to the area of its box.
        """
        c = self.cell
        n = max(1, int(math.ceil(math.hypot(q[0] - p[0], q[1] - p[1]) / c)))
        keys: set[tuple[int, int]] = set()
        for k in range(n):
            ax = p[0] + (q[0] - p[0]) * k / n
            ay = p[1] + (q[1] - p[1]) * k / n
            bx = p[0] + (q[0] - p[0]) * (k + 1) / n
            by = p[1] + (q[1] - p[1]) * (k + 1) / n
            for gx in range(int(math.floor(min(ax, bx) / c)), int(math.floor(max(ax, bx) / c)) + 1):
                for gy in range(int(math.floor(min(ay, by) / c)), int(math.floor(max(ay, by) / c)) + 1):
                    keys.add((gx, gy))
        return keys

    def contains(self, px: float, py: float) -> bool:
        inside = False
        for i in self.rows.get(int(math.floor(py / self.cell)), ()):
            (xi, yi), (xj, yj) = self.edges[i]
            if (yi > py) != (yj > py):
                if px < xi + (py - yi) * (xj - xi) / (yj - yi):
                    inside = not inside
        return inside

    def _near_edges(self, x0, y0, x1, y1, cutoff):
        c = self.cell
        gx0, gy0 = int(math.floor((x0 - cutoff) / c)), int(math.floor((y0 - cutoff) / c))
        gx1, gy1 = int(math.floor((x1 + cutoff) / c)), int(math.floor((y1 + cutoff) / c))
        seen: set[int] = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for i in self.grid.get((gx, gy), ()):
                    if i not in seen:
                        seen.add(i)
                        yield self.edges[i]

    def _near_segment(self, p: Point, q: Point, cutoff: float):
        """Edges near a segment, looked up piece by piece along it."""
        n = max(1, int(math.ceil(math.hypot(q[0] - p[0], q[1] - p[1]) / (4 * self.cell))))
        seen: set[tuple[Point, Point]] = set()
        for k in range(n):
            ax = p[0] + (q[0] - p[0]) * k / n
            ay = p[1] + (q[1] - p[1]) * k / n
            bx = p[0] + (q[0] - p[0]) * (k + 1) / n
            by = p[1] + (q[1] - p[1]) * (k + 1) / n
            for e in self._near_edges(min(ax, bx), min(ay, by),
                                      max(ax, bx), max(ay, by), cutoff):
                if n == 1:
                    yield e
                elif e not in seen:
                    seen.add(e)
                    yield e

    def core_distance(self, other: Shape, cutoff: float) -> float:
        """Distance from ``other``'s core to this polygon's core.

        Exact when the answer is at most ``cutoff``; otherwise some value
        above it (the caller only needs to know it is far).
        """
        if self.contains(*other.pts[0]):
            return 0.0
        if other.kind == "poly" and point_in_poly(*self.shape.pts[0], other):
            return 0.0
        best = cutoff + 1.0
        if other.kind == "point":
            px, py = other.pts[0]
            for e0, e1 in self._near_edges(px, py, px, py, cutoff):
                best = min(best, _pt_seg(px, py, *e0, *e1))
            return best
        # One grid lookup per edge of the other shape, so two big pours
        # that share a bounding box do not meet as a full cross product.
        for f0, f1 in _edges(other):
            for e0, e1 in self._near_segment(f0, f1, cutoff):
                d = _seg_seg(e0, e1, f0, f1)
                if d < best:
                    if d == 0.0:
                        return 0.0
                    best = d
        return best


def bboxes_near(a: Shape, b: Shape, gap: float) -> bool:
    ax0, ay0, ax1, ay1 = a.bbox
    bx0, by0, bx1, by1 = b.bbox
    return not (ax1 + gap < bx0 or bx1 + gap < ax0
                or ay1 + gap < by0 or by1 + gap < ay0)


def shape_to_outline_clearance(s: Shape, outline: Shape) -> float:
    """Gap from a copper shape to the nearest board-edge segment.

    Negative when the copper crosses or leaves the board. A shape whose
    core lies wholly outside the outline gets a negative distance too.
    """
    edge_dist = math.inf
    for e0, e1 in _edges(outline):
        edge_dist = min(edge_dist, core_distance(s, Shape("segment", (e0, e1))))
    inside = point_in_poly(s.pts[0][0], s.pts[0][1], outline)
    gap = edge_dist - s.r
    return gap if inside else -(edge_dist + s.r)


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

def ring_area(pts) -> float:
    """Unsigned shoelace area of a ring."""
    n = len(pts)
    return abs(0.5 * sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                         for i in range(n)))


def convex_hull(pts) -> list[Point]:
    """Counter-clockwise hull (monotone chain)."""
    p = sorted(set((float(x), float(y)) for x, y in pts))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(p):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def overlap_area(a, b) -> float:
    """Area two outlines share, each taken as its convex hull.

    Sutherland-Hodgman clipping of one hull by the other. Courtyards are
    nearly always rectangles; for a concave one the hull overstates the
    overlap, which is the safe side for a placement check.
    """
    subject = convex_hull(a)
    clip = convex_hull(b)
    if len(subject) < 3 or len(clip) < 3:
        return 0.0
    out = subject
    n = len(clip)
    for i in range(n):
        if not out:
            break
        cx0, cy0 = clip[i]
        cx1, cy1 = clip[(i + 1) % n]

        def inside(q):
            return (cx1 - cx0) * (q[1] - cy0) - (cy1 - cy0) * (q[0] - cx0) >= 0

        def cut(p, q):
            dx, dy = q[0] - p[0], q[1] - p[1]
            ex, ey = cx1 - cx0, cy1 - cy0
            den = dx * ey - dy * ex
            if den == 0:
                return q
            t = ((cx0 - p[0]) * ey - (cy0 - p[1]) * ex) / den
            return (p[0] + t * dx, p[1] + t * dy)

        src, out = out, []
        for k in range(len(src)):
            cur, prev = src[k], src[k - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
    return ring_area(out) if len(out) >= 3 else 0.0

