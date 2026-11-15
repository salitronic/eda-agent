# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Creepage and clearance across an isolation barrier, on the copper.

A barrier of the routing brief keeps one group of nets (a mains side, a
high-voltage class) a creepage and a clearance away from another group, or
from every other net. The distances and the clause they come from are the
AI's (IEC 62368-1, IEC 60664-1, IPC-2221B and the like); this measures them.

Creepage is the shortest way along the board's surface, so it is measured
on the outer copper layers, and a slot milled between the two sides (a
board cutout, or an unplated slot hole) is gone round: the path is the
shortest one round the slots that stays on the board. Clearance is the
straight way through air, measured on every layer; a slot does not lengthen
it.

The distance is taken between the copper outlines, sampled every
``SAMPLE`` mils, so it is good to about that much. A part's own pins on both
sides of the barrier (an optocoupler, a transformer) are left out: their
distance is the part's rating, which its datasheet gives.
"""

from __future__ import annotations

import heapq
import math
from typing import Iterable, Optional

from .. import geom
from ..model import LayoutBoard

#: Mils between the points an outline is sampled at.
SAMPLE = 5.0
#: Pairs of nearest points a slot lies between that are gone round.
DETOURS = 12


def side_nets(board: LayoutBoard, names: Iterable[str]) -> set[str]:
    """Nets named, a net class name standing for all of its nets."""
    out: set[str] = set()
    for n in names:
        out |= set(board.net_classes.get(n, ())) or {n}
    return out


def slots(board: LayoutBoard) -> list[list[tuple[float, float]]]:
    """The board's slots as rings: its cutouts, and every unplated hole
    with no copper (a milled slot drawn as a pad)."""
    out = [list(c) for c in board.cutouts if len(c) >= 3]
    for p in board.pads:
        if p.hole > 0 and not p.plated and not p.copper:
            if p.hole_type == "slot" and p.hole_width > p.hole:
                half = (p.hole_width - p.hole) / 2
                a = math.radians(p.rotation + p.hole_rotation)
                dx, dy = half * math.cos(a), half * math.sin(a)
                ring = geom.stroke_outline(p.x - dx, p.y - dy, p.x + dx, p.y + dy, p.hole)
            else:
                ring = geom.outline(geom.circle(p.x, p.y, p.hole))
            out.append(ring)
    return out


def _shapes(board: LayoutBoard, nets: set[str], layer: str, rest: bool = False):
    """(shape, the part it belongs to or "") of the copper on ``layer`` of
    ``nets``, or with ``rest`` of every net not in them (unconnected copper
    too)."""
    def mine(net: str) -> bool:
        return (net not in nets) if rest else (net in nets)

    out = []
    for t in board.tracks:
        if t.layer == layer and not t.keepout and mine(t.net):
            out.append((t.shape(), t.comp))
    for a in board.arcs:
        if a.layer == layer and not a.keepout and mine(a.net):
            out += [(s, a.comp) for s in a.shapes()]
    for v in board.vias:
        if mine(v.net) and layer in board.layers_between(v.low_layer, v.high_layer):
            out.append((geom.circle(v.x, v.y, v.layer_diameters.get(layer, v.diameter)), v.comp))
    for p in board.pads:
        if mine(p.net):
            s = p.shape_on(layer)
            if s is not None:
                out.append((s, p.comp))
    for r in board.regions:
        if r.layer == layer and mine(r.net) and r.kind in ("pour", "copper") \
                and not r.keepout and len(r.outline) >= 3:
            out.append((r.shape(), r.comp))
    return out


def _points(s: geom.Shape, near=None) -> list[tuple[float, float]]:
    """A shape's outline sampled every SAMPLE mils, only where it lies
    inside the box ``near`` (x0, y0, x1, y1) when given."""
    pts = []
    rings = [geom.outline(s)] + [list(h) for h in getattr(s, "holes", ()) or ()]
    for ring in rings:
        for i in range(len(ring)):
            (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % len(ring)]
            n = max(1, int(math.ceil(math.hypot(x2 - x1, y2 - y1) / SAMPLE)))
            for k in range(n):
                x, y = x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n
                if near is None or (near[0] <= x <= near[2] and near[1] <= y <= near[3]):
                    pts.append((x, y))
    return pts


def _crosses(a, b, ring) -> bool:
    """Whether the segment a-b passes through the inside of the ring."""
    n = len(ring)
    for i in range(n):
        if geom._segs_cross(a, b, ring[i], ring[(i + 1) % n]):
            return True
    # Along an edge or through a corner is round the slot, not through it:
    # only a midpoint clear inside the ring is.
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    on_edge = min(geom._pt_seg(*mid, *ring[i], *ring[(i + 1) % n]) for i in range(n)) < 1e-6
    return not on_edge and geom._inside_ring(mid[0], mid[1], ring)


def surface_distance(a, b, rings, outline=None) -> float:
    """The shortest way from a to b that keeps out of every ring (the
    slots) and, with ``outline``, on the board: straight when nothing is in
    the way, else round the slots' corners."""
    if not any(_crosses(a, b, r) for r in rings):
        return math.dist(a, b)
    nodes = [a, b] + [p for r in rings for p in r]

    def clear(p, q) -> bool:
        if any(_crosses(p, q, r) for r in rings):
            return False
        if outline and len(outline) >= 3:
            mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            if not geom._inside_ring(mid[0], mid[1], outline):
                return False
        return True

    best = {0: 0.0}
    heap = [(0.0, 0)]
    while heap:
        d, i = heapq.heappop(heap)
        if i == 1:
            return d
        if d > best.get(i, math.inf):
            continue
        for j in range(len(nodes)):
            if j == i:
                continue
            nd = d + math.dist(nodes[i], nodes[j])
            if nd < best.get(j, math.inf) and clear(nodes[i], nodes[j]):
                best[j] = nd
                heapq.heappush(heap, (nd, j))
    return math.inf


def _centre(s: geom.Shape) -> tuple[int, int]:
    x0, y0, x1, y1 = s.bbox
    return (round((x0 + x1) / 2), round((y0 + y1) / 2))


def _box(s: geom.Shape, grow: float):
    x0, y0, x1, y1 = s.bbox
    return (x0 - grow, y0 - grow, x1 + grow, y1 + grow)


def measure(board: LayoutBoard, side_a: set[str], side_b: Optional[set[str]],
            creepage: Optional[float], clearance: Optional[float]) -> dict[str, dict]:
    """Layer -> the worst place each distance falls short: ``creepage``
    (outer layers, along the surface) and ``clearance`` (every layer,
    straight), each {gap, need, at}. ``side_b`` None is every other net."""
    order = board.copper_layers()
    outer = {order[0], order[-1]} if order else set()
    rings = slots(board)
    outline = board.outline if len(board.outline) >= 3 else None
    out: dict[str, dict] = {}
    for layer in order:
        need_c = creepage if layer in outer else None
        need = max(v for v in (need_c, clearance, 0.0) if v is not None)
        if not need:
            continue
        a_side = _shapes(board, side_a, layer)
        b_side = _shapes(board, side_a, layer, rest=True) if side_b is None \
            else _shapes(board, side_b, layer)
        straight: list[tuple[float, tuple, tuple]] = []
        closest = None
        for sa, ca in a_side:
            for sb, cb in b_side:
                if ca and ca == cb:
                    continue
                if not geom.bboxes_near(sa, sb, need):
                    continue
                g = geom.clearance(sa, sb)
                if g >= need - 1e-6:
                    continue
                if clearance is not None and g < clearance - 1e-6:
                    w = out.setdefault(layer, {}).get("clearance")
                    if w is None or g < w["gap"]:
                        out[layer]["clearance"] = {"gap": round(max(g, 0.0), 2),
                                                   "need": clearance, "at": _centre(sa)}
                if need_c is not None and g < need_c - 1e-6:
                    if closest is None or g < closest[0]:
                        closest = (g, _centre(sa))
                    pa = _points(sa, _box(sb, need_c))
                    pb = _points(sb, _box(sa, need_c))
                    for p in pa:
                        for q in pb:
                            d = math.dist(p, q)
                            if d < need_c:
                                straight.append((d, p, q))
        if straight:
            straight.sort()
            worst = None
            for d, p, q in straight:
                if not any(_crosses(p, q, r) for r in rings):
                    worst = (d, p, q)
                    break
            if worst is None:
                for d, p, q in straight[:DETOURS]:
                    s = surface_distance(p, q, rings, outline)
                    if s < need_c - 1e-6 and (worst is None or s < worst[0]):
                        worst = (s, p, q)
            if worst is not None:
                out.setdefault(layer, {})["creepage"] = {
                    "gap": round(worst[0], 2), "need": need_c,
                    "at": (round((worst[1][0] + worst[2][0]) / 2),
                           round((worst[1][1] + worst[2][1]) / 2))}
        elif closest is not None:
            # Closer than the outlines' samples show: straight, no slot seen.
            out.setdefault(layer, {})["creepage"] = {"gap": round(max(closest[0], 0.0), 2),
                                                     "need": need_c, "at": closest[1]}
    return {l: m for l, m in out.items() if m}


__all__ = ["SAMPLE", "measure", "side_nets", "slots", "surface_distance"]
