# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Signal and power integrity rules of the brief, measured on the copper.

Each function takes the board with the round's copper on it and the brief,
and returns what breaks the rule, for review.findings to put to the AI:

* ``termination_misses``: a net's stubs off its trunk (the copper from the
  driver to the far end, the terminating part when it ends the net), or
  the copper from the driver to a series terminator.
* ``guard_misses``: track of a net with no copper of its guard net beside
  it, on both sides, within the guard gap.
* ``broadside``: track running over or under another net's on the copper
  layer next door, with no plane layer between.
* ``pdn_misses``: an IC's power pins with no decoupling capacitor near
  enough, and too few vias at a pin or at a capacitor's return pad.

Lengths along copper follow copper_path.NetCopper: track and arc ends, vias
joining their layers, a pour or region of the net joining what lands on
it, a pad joining the ends within half its longer side.
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Iterable, Optional

from .. import geom
from ..copper_path import NetCopper
from ..model import LayoutBoard, Pad

#: Pieces a track is cut into for the guard and broadside measures, mils.
STEP = 10.0


def _dijkstra(adj, sources: Iterable) -> tuple[dict, dict]:
    dist = {s: 0.0 for s in sources}
    prev: dict = {}
    heap = [(0.0, k, s) for k, s in enumerate(dist)]
    heapq.heapify(heap)
    seq = len(heap)
    while heap:
        d, _, n = heapq.heappop(heap)
        if d > dist.get(n, math.inf):
            continue
        for m, w in adj.get(n, ()):
            nd = d + w
            if nd < dist.get(m, math.inf):
                dist[m] = nd
                prev[m] = n
                heapq.heappush(heap, (nd, seq, m))
                seq += 1
    return dist, prev


def copper_distance(board: LayoutBoard, net: str, a: Pad, b: Pad) -> Optional[float]:
    """Mils along ``net``'s copper from pad ``a`` to pad ``b``, or None when
    the copper does not join them."""
    nc = NetCopper(board, net)
    src, dst = nc.attach_pad(a), nc.attach_pad(b)
    dist, _ = _dijkstra(nc.adj, [src])
    return dist.get(dst)


# --- termination --------------------------------------------------------------------

def termination_misses(routed: LayoutBoard, brief, nets: Iterable[str]) -> dict[str, dict]:
    """Per net of the round with a termination rule: the stubs over its
    ``max_stub`` (pad -> mils of copper off the trunk), or for a source
    termination the copper from the driver to the part when over it. A
    pad the copper does not reach yet is left to the unrouted findings."""
    pads = {p.key: p for p in routed.pads if p.comp}
    out: dict[str, dict] = {}
    for net in sorted(set(nets)):
        e = brief.of.get(net)
        if e is None or e.max_stub is None or not e.driver or e.driver not in pads:
            continue
        mine = [p for p in routed.pads if p.net == net and p.comp]
        nc = NetCopper(routed, net)
        node = {p.key: nc.attach_pad(p) for p in mine}
        dist, prev = _dijkstra(nc.adj, [node[e.driver]])
        reached = {k: dist[n] for k, n in node.items() if n in dist and k != e.driver}
        if not reached:
            continue
        at = e.terminate_at or ("end" if e.terminate else "")
        term = [p.key for p in mine if p.comp == e.terminate]
        if at == "source":
            got = [reached[k] for k in term if k in reached]
            if got and min(got) > e.max_stub + 1e-6:
                out[net] = {"what": "source", "length": round(min(got), 1),
                            "limit": e.max_stub, "part": e.terminate}
            continue
        far = next((k for k in term if k in reached), None) if at == "end" else None
        if far is None:
            far = max(reached, key=reached.get)
        trunk = [node[far]]
        while trunk[-1] != node[e.driver]:
            trunk.append(prev[trunk[-1]])
        off, _ = _dijkstra(nc.adj, trunk)
        stubs = {k: round(off[n], 1) for k, n in node.items()
                 if k in reached and k != far and n in off and off[n] > e.max_stub + 1e-6}
        if stubs:
            out[net] = {"what": "stub", "stubs": stubs, "limit": e.max_stub, "far": far,
                        "trunk": round(reached[far], 1)}
    return out


# --- guard ----------------------------------------------------------------------------

def _pieces(t, step: float = STEP):
    """(x, y, unit normal) at the middle of each piece of track ``t``."""
    L = math.hypot(t.x2 - t.x1, t.y2 - t.y1)
    if L <= 1e-9:
        return []
    ux, uy = (t.x2 - t.x1) / L, (t.y2 - t.y1) / L
    n = max(1, int(math.ceil(L / step)))
    return [(t.x1 + (t.x2 - t.x1) * (k + 0.5) / n, t.y1 + (t.y2 - t.y1) * (k + 0.5) / n,
             (-uy, ux), L / n) for k in range(n)]


def _net_shapes(board: LayoutBoard, net: str, layer: str) -> list[geom.Shape]:
    """Copper of ``net`` on ``layer``: tracks, arcs, poured and solid
    regions, pads and vias."""
    out = [t.shape() for t in board.tracks if t.net == net and t.layer == layer and not t.keepout]
    out += [s for a in board.arcs if a.net == net and a.layer == layer for s in a.shapes()]
    out += [r.shape() for r in board.regions if r.net == net and r.layer == layer
            and r.kind in ("pour", "copper") and len(r.outline) >= 3]
    out += [s for p in board.pads if p.net == net for s in [p.shape_on(layer)] if s is not None]
    out += [v.shape_on(layer) for v in board.vias if v.net == net]
    return out


def guard_misses(routed: LayoutBoard, brief, nets: Iterable[str]) -> dict[str, dict]:
    """Per net of the round with a guard: the mils of its track without
    the guard net's copper within ``guard_gap`` of its edge on both sides,
    pieces at its own pads apart, when over ``max_unguarded``."""
    out: dict[str, dict] = {}
    for net in sorted(set(nets)):
        e = brief.of.get(net)
        if e is None or not e.guard or not e.guard_gap:
            continue
        cache: dict[str, list] = {}
        own: dict[str, list] = {}
        bare, first = 0.0, None
        for t in routed.tracks:
            if t.net != net or t.comp or t.keepout:
                continue
            if t.layer not in cache:
                cache[t.layer] = _net_shapes(routed, e.guard, t.layer)
                own[t.layer] = [s for p in routed.pads if p.net == net
                                for s in [p.shape_on(t.layer)] if s is not None]
            for x, y, (nx, ny), step in _pieces(t):
                spot = geom.circle(x, y, t.width + 2 * e.guard_gap)
                if any(geom.clearance(spot, s) <= 0 for s in own[t.layer]):
                    continue
                sides = 0
                for sgn in (1.0, -1.0):
                    a = t.width / 2
                    probe = geom.capsule(x + sgn * nx * a, y + sgn * ny * a,
                                         x + sgn * nx * (a + e.guard_gap),
                                         y + sgn * ny * (a + e.guard_gap), 0.5)
                    if any(geom.bboxes_near(probe, s, 0.0) and geom.clearance(probe, s) <= 0
                           for s in cache[t.layer]):
                        sides += 1
                if sides < 2:
                    bare += step
                    first = first or (round(x), round(y), t.layer)
        limit = e.max_unguarded or 0.0
        if bare > limit + 1e-6:
            out[net] = {"unguarded": round(bare, 1), "limit": limit, "guard": e.guard,
                        "guard_gap": e.guard_gap, "at": first[:2], "layer": first[2]}
    return out


# --- broadside --------------------------------------------------------------------------

def neighbour_layers(board: LayoutBoard, layer: str) -> list[str]:
    """The signal copper layers right above and below ``layer``: a plane
    layer between screens them, and is not a neighbour."""
    order = board.copper_layers()
    if layer not in order:
        return []
    kinds = {l.name: l.kind for l in board.layers}
    i = order.index(layer)
    return [order[k] for k in (i - 1, i + 1)
            if 0 <= k < len(order) and kinds.get(order[k], "signal") != "plane"]


def broadside(routed: LayoutBoard, net: str, others: set[str], gap: float) -> tuple[float, Any]:
    """Mils of ``net``'s track with a track of ``others`` within ``gap``
    across in plan view on a neighbouring layer, and where the first is."""
    near, first = 0.0, None
    cache: dict[str, list] = {}
    for t in routed.tracks:
        if t.net != net or t.comp or t.keepout:
            continue
        layers = neighbour_layers(routed, t.layer)
        if not layers:
            continue
        for l in layers:
            if l not in cache:
                cache[l] = [o.shape() for o in routed.tracks
                            if o.net in others and o.layer == l and not o.keepout]
        shapes = [s for l in layers for s in cache[l]]
        if not shapes:
            continue
        for x, y, (nx, ny), step in _pieces(t):
            ux, uy = ny, -nx
            piece = geom.capsule(x - ux * step / 2, y - uy * step / 2, x + ux * step / 2,
                                 y + uy * step / 2, t.width)
            if any(geom.bboxes_near(piece, s, gap) and geom.clearance(piece, s) <= gap + 1e-6
                   for s in shapes):
                near += step
                first = first or (round(x), round(y))
    return near, first


# --- pdn ----------------------------------------------------------------------------------

def _prefix(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha()).upper()


def decaps_of(board: LayoutBoard, rule) -> dict[str, list[Pad]]:
    """Capacitor -> its two pads, for those that serve the rule's rails:
    those it names, else every two-pad capacitor with a pad on a rail."""
    by: dict[str, list[Pad]] = {}
    for p in board.pads:
        if p.comp:
            by.setdefault(p.comp, []).append(p)
    rails = set(rule.rails)
    names = rule.decaps or [c for c, ps in by.items() if _prefix(c) == "C" and len(ps) == 2]
    return {c: by[c] for c in names if c in by and any(p.net in rails for p in by[c])}


def _vias_near(board: LayoutBoard, net: str, x: float, y: float, reach: float,
               skip: Optional[Pad] = None) -> int:
    n = sum(1 for v in board.vias if v.net == net and math.hypot(v.x - x, v.y - y) <= reach)
    # A plated hole of the net counts as a via to its planes.
    n += sum(1 for p in board.pads if p.net == net and p.hole > 0 and p.plated and p is not skip
             and math.hypot(p.x - x, p.y - y) <= reach)
    if skip is not None and skip.hole > 0 and skip.plated:
        n += 1
    return n


def pdn_misses(routed: LayoutBoard, brief, nets: Iterable[str]) -> list[dict]:
    """For each pdn rule the round touches (a rail, or a capacitor's return
    net, among ``nets``): per power pin, its nearest capacitor along the
    copper (straight when the copper does not join them) over
    ``max_distance``, no capacitor at all, or under ``min_vias`` within
    ``via_reach``; and each capacitor's return pad under ``min_vias``."""
    nets = set(nets)
    out: list[dict] = []
    for r in brief.pdn:
        caps = decaps_of(routed, r)
        returns = {p.net for ps in caps.values() for p in ps if p.net not in r.rails and p.net}
        if not nets & (set(r.rails) | returns):
            continue
        pins = [p for p in routed.pads if p.comp == r.part and p.net in r.rails]
        used: set[str] = set()
        for pin in sorted(pins, key=lambda p: p.key):
            best = None
            for c, ps in caps.items():
                rail = next((p for p in ps if p.net == pin.net), None)
                if rail is None:
                    continue
                along = copper_distance(routed, pin.net, pin, rail)
                d = along if along is not None else math.hypot(rail.x - pin.x, rail.y - pin.y)
                if best is None or d < best[0]:
                    best = (d, c, along is not None)
            if best is None:
                out.append({"rule": r.name, "pin": pin.key, "what": "no_decap",
                            "net": pin.net})
                continue
            used.add(best[1])
            if r.max_distance is not None and best[0] > r.max_distance + 1e-6:
                out.append({"rule": r.name, "pin": pin.key, "what": "distance",
                            "cap": best[1], "measured": round(best[0], 1),
                            "along_copper": best[2], "limit": r.max_distance,
                            "net": pin.net})
            if r.min_vias is not None and r.via_reach:
                k = _vias_near(routed, pin.net, pin.x, pin.y, r.via_reach, skip=pin)
                if k < r.min_vias:
                    out.append({"rule": r.name, "pin": pin.key, "what": "vias", "net": pin.net,
                                "measured": k, "limit": r.min_vias, "reach": r.via_reach})
        if r.min_vias is not None and r.via_reach:
            for c in sorted(used):
                for p in caps[c]:
                    if p.net in r.rails or not p.net:
                        continue
                    k = _vias_near(routed, p.net, p.x, p.y, r.via_reach, skip=p)
                    if k < r.min_vias:
                        out.append({"rule": r.name, "pin": p.key, "what": "return_vias",
                                    "cap": c, "net": p.net, "measured": k,
                                    "limit": r.min_vias, "reach": r.via_reach})
    return out


__all__ = ["termination_misses", "guard_misses", "broadside", "neighbour_layers",
           "pdn_misses", "decaps_of", "copper_distance"]
