# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Track clean-up worked out off the board: slivers to delete and straight
chains to merge into one track each.

``plan`` takes the free tracks and what may stand at a joint, and returns
what to delete and what to stretch. One pass over an index of track ends,
so the cost grows with the number of tracks, not its square.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

#: Two ends closer than this are one point (mils).
SNAP = 0.01
_CELL = 100.0


@dataclass
class Piece:
    """One free track. ``net`` None: not known, so it is never merged."""

    layer: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    net: Optional[str] = ""

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (self.x1, self.y1), (self.x2, self.y2)


@dataclass
class Plan:
    slivers: list[Piece] = field(default_factory=list)
    #: (the track kept, its new ends [x1, y1, x2, y2], the tracks it absorbs)
    merges: list[tuple[Piece, list[float], list[Piece]]] = field(default_factory=list)
    #: Straight joints left alone, by why.
    kept_joints: dict[str, int] = field(default_factory=dict)

    @property
    def merged(self) -> int:
        return sum(len(gone) for _, _, gone in self.merges)


def _q(v: float) -> int:
    return round(v / SNAP)


def _seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 <= 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


class _Grid:
    """Tracks by the cells their boxes touch, for the few near a point."""

    def __init__(self) -> None:
        self.cells: dict[tuple[str, int, int], list[Piece]] = {}

    def add(self, t: Piece) -> None:
        x0, x1 = sorted((t.x1, t.x2))
        y0, y1 = sorted((t.y1, t.y2))
        pad = t.width / 2 + SNAP
        for cx in range(int((x0 - pad) // _CELL), int((x1 + pad) // _CELL) + 1):
            for cy in range(int((y0 - pad) // _CELL), int((y1 + pad) // _CELL) + 1):
                self.cells.setdefault((t.layer, cx, cy), []).append(t)

    def near(self, layer: str, p: tuple[float, float]) -> list[Piece]:
        return self.cells.get((layer, int(p[0] // _CELL), int(p[1] // _CELL)), [])


def plan(tracks: Iterable[Piece], mode: str = "slivers", min_length: float = 1.0,
         blocked: Optional[Callable[[str, tuple[float, float]], str]] = None,
         others: Iterable[Piece] = ()) -> Plan:
    """What ``mode`` (slivers, merge or both) does to ``tracks``.

    A sliver is a track shorter than ``min_length``. A merge joins tracks
    of one layer, width and net that run on in a straight line, at joints
    where exactly those two ends meet and nothing else stands: ``blocked``
    names what does at a point of a layer (a pad, a via, an arc), and no
    other track, of ``tracks`` or ``others`` (footprint tracks), may touch
    the joint.
    """
    out = Plan()
    live = list(tracks)
    if mode in ("slivers", "both"):
        out.slivers = [t for t in live if t.length < min_length]
        gone = {id(t) for t in out.slivers}
        live = [t for t in live if id(t) not in gone]
    if mode not in ("merge", "both"):
        return out

    ends: dict[tuple[str, int, int], list[tuple[int, int]]] = {}
    grid = _Grid()
    for i, t in enumerate(live):
        grid.add(t)
        for e, p in enumerate(t.ends()):
            ends.setdefault((t.layer, _q(p[0]), _q(p[1])), []).append((i, e))
    for t in others:
        grid.add(t)

    def why_not(key, here) -> str:
        if len(here) != 2 or here[0][0] == here[1][0]:
            return "junction" if len(here) > 2 else ""
        (i, ei), (j, ej) = here
        a, b = live[i], live[j]
        if a.length <= SNAP or b.length <= SNAP:
            return ""
        if a.net is None or b.net is None:
            return "net_unknown"
        if a.net != b.net or abs(a.width - b.width) > SNAP:
            return ""
        s = a.ends()[ei]
        fa, fb = a.ends()[1 - ei], b.ends()[1 - ej]
        ax, ay, bx, by = fa[0] - s[0], fa[1] - s[1], fb[0] - s[0], fb[1] - s[1]
        if (abs(ax * by - ay * bx) > 1e-4 * a.length * b.length
                or ax * bx + ay * by >= 0):
            return ""
        if blocked is not None:
            what = blocked(a.layer, s)
            if what:
                return what
        for t in grid.near(a.layer, s):
            if t is a or t is b:
                continue
            if _seg_dist(s, *t.ends()) <= t.width / 2 + SNAP:
                return "third_track"
        return "ok"

    parent = list(range(len(live)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    joined: set[tuple[int, int]] = set()
    for key, here in ends.items():
        why = why_not(key, here)
        if why == "ok":
            (i, ei), (j, ej) = here
            parent[find(i)] = find(j)
            joined.update(((i, ei), (j, ej)))
        elif why:
            out.kept_joints[why] = out.kept_joints.get(why, 0) + 1

    groups: dict[int, list[int]] = {}
    for i in range(len(live)):
        groups.setdefault(find(i), []).append(i)
    for members in groups.values():
        if len(members) < 2:
            continue
        free = [live[i].ends()[e] for i in members for e in (0, 1) if (i, e) not in joined]
        if len(free) != 2:
            continue
        keep = live[members[0]]
        out.merges.append((keep, [free[0][0], free[0][1], free[1][0], free[1][1]],
                           [live[i] for i in members[1:]]))
    return out


def blockers(board: Any) -> Callable[[str, tuple[float, float]], str]:
    """What stands at a point of a layer on ``board``: "pad", "via", "arc"
    or "" for nothing. A pad counts over the circle round its copper."""
    cells: dict[tuple[int, int], list[tuple[str, Any]]] = {}

    def put(kind: str, obj: Any, x: float, y: float, r: float) -> None:
        for cx in range(int((x - r) // _CELL), int((x + r) // _CELL) + 1):
            for cy in range(int((y - r) // _CELL), int((y + r) // _CELL) + 1):
                cells.setdefault((cx, cy), []).append((kind, obj))

    for p in board.pads:
        r = max([math.hypot(c.w, c.h) / 2 + math.hypot(c.offset_x, c.offset_y)
                 for c in p.copper] or [0.0])
        put("pad", p, p.x, p.y, r + SNAP)
    for v in board.vias:
        put("via", v, v.x, v.y, v.diameter / 2 + SNAP)
    for a in board.arcs:
        put("arc", a, a.cx, a.cy, a.radius + a.width / 2 + SNAP)

    def blocked(layer: str, s: tuple[float, float]) -> str:
        for kind, o in cells.get((int(s[0] // _CELL), int(s[1] // _CELL)), ()):
            if kind == "pad":
                for c in o.copper:
                    if c.layer == layer and (
                            math.hypot(s[0] - o.x - c.offset_x, s[1] - o.y - c.offset_y)
                            <= math.hypot(c.w, c.h) / 2 + SNAP):
                        return "pad"
            elif kind == "via":
                if math.hypot(s[0] - o.x, s[1] - o.y) <= o.diameter / 2 + SNAP:
                    return "via"
            elif o.layer == layer and abs(
                    math.hypot(s[0] - o.cx, s[1] - o.cy) - o.radius) <= o.width / 2 + SNAP:
                return "arc"
        return ""

    return blocked
