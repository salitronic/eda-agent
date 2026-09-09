# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Multi-layer Manhattan A* router over a :class:`RoutingProblem` grid.

Net order: class priority first (power / ground, then high-current /
switch, differential / clock, analog, control, plain signal -- the
classes :mod:`eda_agent.design.net_classes` assigns), then short
nets before long inside each class, then name. Each routed net is
rasterized back into the obstacle map before the next starts.

Multi-pin nets grow a tree by sequential closest-pair: route the closest
terminal pair, then repeatedly route the terminal nearest the tree into
ANY cell of the tree (steiner-lite -- taps can land mid-segment, which
is connective copper in Altium).

Output tracks are ``{x1, y1, x2, y2, width, layer, net_name}`` and vias
``{x, y, net, size, hole_size}`` -- integer mils, key-for-key the
parameters of the ``pcb_place_tracks`` / ``pcb_place_via`` MCP tools.

Determinism: no randomness; every iteration order is sorted and heap
ties break on insertion order, so identical input gives identical
output regardless of input list order.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from typing import Any

from eda_agent.route.model import (
    DEFAULT_GRID_PITCH_MILS,
    RouteRules,
    RoutingProblem,
    Terminal,
    dist_point_seg,
    dist_seg_rect,
    dist_seg_seg,
)

# Lower routes earlier. Unknown classes route with plain signals.
_CLASS_PRIORITY = {
    "power": 0,
    "ground": 0,
    "high_current": 1,
    "switch": 1,
    "differential": 2,
    "clock": 2,
    "analog": 3,
    "control": 4,
    "signal": 5,
}

# FOUR DIRECTIONS IN THE SEARCH, 45s AFTERWARDS. Adding the diagonals to
# the A* move set was tried and measured: it reroutes earlier nets
# through cells the orthogonal routes left free, walls off a later net,
# and cost the blinker benchmark a net it had always routed (100% to
# 83.3%). Nets are routed in order and each becomes an obstacle for the
# next, so a locally prettier path is not a better board. The corners are
# chamfered after the fact instead, against the same obstacle map, which
# cannot change what routes at all.
_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
_DIR_NONE = 4  # start of path / just emerged from a via: next move is free

#: How deep a corner may be cut, in grid cells. The chamfer never eats
#: more than half of the shorter leg, so a short jog keeps its shape.
_MAX_CHAMFER_CELLS = 4
_EPS = 1e-6


@dataclass
class RouterOptions:
    """A* cost knobs. Step cost is 1.0 per grid cell; the penalties are
    in the same unit (multiples of one grid pitch)."""

    bend_penalty: float = 1.0
    via_cost: float = 10.0
    #: A via inside a surface-mount pad wicks solder off the joint and
    #: has to be filled and capped, which is a different and dearer
    #: process. Off by default; the cases that genuinely need it are BGA
    #: fanout with no room to escape and a thermal pad being stitched to
    #: a plane.
    allow_via_in_pad: bool = False
    # Per-connection expansion budget so a walled-in net fails fast
    # instead of flooding a big grid forever.
    max_expansions: int = 200_000


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def route_geometry(geometry: dict[str, Any],
                   rules: dict[str, Any] | RouteRules | None = None,
                   net_classes: dict[str, str] | None = None,
                   grid_pitch_mils: int = DEFAULT_GRID_PITCH_MILS,
                   options: RouterOptions | None = None) -> dict[str, Any]:
    """Route every netted pad group in the board geometry dict (mils).

    Tool-shaped result: ``{"ok": False, "reason": ...}`` on malformed
    input, else ``{"ok": True, "summary": ..., "order": ..., "nets":
    ..., "tracks": ..., "vias": ..., "validation": ...}``. Per-net
    routing failure is honest data (status ``failed``), not a tool
    error.
    """
    try:
        problem = RoutingProblem.from_geometry(
            geometry, rules, net_classes=net_classes,
            grid_pitch_mils=grid_pitch_mils)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "reason": str(exc)}
    return route_problem(problem, options)


def route_problem(problem: RoutingProblem,
                  options: RouterOptions | None = None) -> dict[str, Any]:
    """Route a prebuilt :class:`RoutingProblem` (mils). Same payload as
    :func:`route_geometry`. Mutates the problem's obstacle map."""
    opt = options or RouterOptions()
    order = _net_order(problem)

    nets_out: dict[str, dict[str, Any]] = {}
    all_tracks: list[dict[str, Any]] = []
    all_vias: list[dict[str, Any]] = []
    routed = failed = skipped = 0

    for net in sorted(problem.terminals):
        if len(problem.terminals[net]) < 2:
            skipped += 1
            nets_out[net] = {
                "status": "skipped",
                "reason": "fewer than 2 terminals",
                "class": problem.class_of(net),
                "tracks": [], "vias": [],
            }
    for net in order:
        res = _route_net(problem, net, opt)
        nets_out[net] = res
        if res["status"] == "routed":
            routed += 1
            all_tracks.extend(res["tracks"])
            all_vias.extend(res["vias"])
            problem.add_route_obstacles(net, res["tracks"], res["vias"])
        else:
            failed += 1

    attempted = routed + failed
    summary = {
        "nets_total": len(nets_out),
        "routed": routed,
        "failed": failed,
        "skipped": skipped,
        "completion": (routed / attempted) if attempted else 1.0,
        "track_count": len(all_tracks),
        "via_count": len(all_vias),
        "total_length_mils": sum(
            abs(t["x2"] - t["x1"]) + abs(t["y2"] - t["y1"])
            for t in all_tracks),
    }
    solution = {
        "ok": True,
        "summary": summary,
        "order": order,
        "nets": {k: nets_out[k] for k in sorted(nets_out)},
        "tracks": all_tracks,
        "vias": all_vias,
    }
    solution["validation"] = validate_solution(problem, solution)
    return solution


def validate_solution(problem: RoutingProblem,
                      solution: dict[str, Any]) -> dict[str, Any]:
    """Geometric post-check of a routing solution against the problem.

    Verifies, for every pair of DIFFERENT nets, that no same-layer
    track/track, track/pad, track/via or via/via spacing falls below
    half-width + half-width + clearance (mils) -- which also catches
    same-layer crossings (distance 0). Returns ``{"ok": bool,
    "violations": [...], "checked": n}``.
    """
    clearance = problem.rules.clearance_mils
    tracks = solution.get("tracks") or []
    vias = solution.get("vias") or []
    layer_idx = {name.lower(): i for i, name in enumerate(problem.layers)}
    violations: list[dict[str, Any]] = []
    checked = 0

    def _report(kind: str, net_a: str, net_b: Any,
                dist: float, need: float, where: tuple) -> None:
        violations.append({
            "kind": kind, "net_a": net_a,
            "net_b": net_b if net_b is not None else "",
            "distance_mils": round(dist, 3),
            "required_mils": round(need, 3),
            "at": [int(round(w)) for w in where],
        })

    # Track vs track (same layer, different nets).
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            if a["net_name"] == b["net_name"]:
                continue
            if str(a["layer"]).lower() != str(b["layer"]).lower():
                continue
            checked += 1
            need = a["width"] / 2.0 + b["width"] / 2.0 + clearance
            d = dist_seg_seg(a["x1"], a["y1"], a["x2"], a["y2"],
                             b["x1"], b["y1"], b["x2"], b["y2"])
            if d < need - _EPS:
                _report("track_track", a["net_name"], b["net_name"],
                        d, need, (a["x1"], a["y1"]))

    # Track vs static copper (pads / existing tracks / existing vias).
    for a in tracks:
        ali = layer_idx[str(a["layer"]).lower()]
        ahw = a["width"] / 2.0
        for g in problem.geoms:
            if g["net"] == a["net_name"]:
                continue
            if g["layer"] is not None and g["layer"] != ali:
                continue
            checked += 1
            if g["kind"] == "rect":
                need = ahw + clearance
                d = dist_seg_rect(a["x1"], a["y1"], a["x2"], a["y2"],
                                  g["cx"], g["cy"], g["hw"], g["hh"])
                where = (g["cx"], g["cy"])
            elif g["kind"] == "seg":
                need = ahw + g["hw"] + clearance
                d = dist_seg_seg(a["x1"], a["y1"], a["x2"], a["y2"],
                                 g["x1"], g["y1"], g["x2"], g["y2"])
                where = (g["x1"], g["y1"])
            else:  # circle
                need = ahw + g["r"] + clearance
                d = dist_point_seg(g["x"], g["y"],
                                   a["x1"], a["y1"], a["x2"], a["y2"])
                where = (g["x"], g["y"])
            if d < need - _EPS:
                _report(f"track_{g['kind']}", a["net_name"], g["net"],
                        d, need, where)

    # Vias span every layer: check against all tracks, pads, and vias.
    for i, v in enumerate(vias):
        vr = v["size"] / 2.0
        for a in tracks:
            if a["net_name"] == v["net"]:
                continue
            checked += 1
            need = vr + a["width"] / 2.0 + clearance
            d = dist_point_seg(v["x"], v["y"],
                               a["x1"], a["y1"], a["x2"], a["y2"])
            if d < need - _EPS:
                _report("via_track", v["net"], a["net_name"],
                        d, need, (v["x"], v["y"]))
        for g in problem.geoms:
            if g["net"] == v["net"]:
                continue
            checked += 1
            if g["kind"] == "rect":
                need = vr + clearance
                d = dist_point_rect_edge(v["x"], v["y"],
                                         g["cx"], g["cy"], g["hw"], g["hh"])
            elif g["kind"] == "seg":
                need = vr + g["hw"] + clearance
                d = dist_point_seg(v["x"], v["y"],
                                   g["x1"], g["y1"], g["x2"], g["y2"])
            else:
                need = vr + g["r"] + clearance
                d = math.hypot(v["x"] - g["x"], v["y"] - g["y"])
            if d < need - _EPS:
                _report(f"via_{g['kind']}", v["net"], g["net"],
                        d, need, (v["x"], v["y"]))
        for w in vias[i + 1:]:
            if w["net"] == v["net"]:
                continue
            checked += 1
            need = vr + w["size"] / 2.0 + clearance
            d = math.hypot(v["x"] - w["x"], v["y"] - w["y"])
            if d < need - _EPS:
                _report("via_via", v["net"], w["net"],
                        d, need, (v["x"], v["y"]))

    return {"ok": not violations, "violations": violations,
            "checked": checked}


def dist_point_rect_edge(px: float, py: float, cx: float, cy: float,
                         hw: float, hh: float) -> float:
    """Distance from a point to a rect's boundary, 0 inside (mils)."""
    dx = max(0.0, abs(px - cx) - hw)
    dy = max(0.0, abs(py - cy) - hh)
    return math.hypot(dx, dy)


# ---------------------------------------------------------------------------
# Net ordering
# ---------------------------------------------------------------------------


def _net_order(problem: RoutingProblem) -> list[str]:
    """Routable nets (>= 2 terminals) sorted: class priority, then HPWL
    short-to-long, then name."""
    keyed = []
    for net, terms in problem.terminals.items():
        if len(terms) < 2:
            continue
        xs = [t.x for t in terms]
        ys = [t.y for t in terms]
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
        prio = _CLASS_PRIORITY.get(problem.class_of(net), 5)
        keyed.append((prio, hpwl, net))
    keyed.sort()
    return [net for _, _, net in keyed]


# ---------------------------------------------------------------------------
# Per-net routing
# ---------------------------------------------------------------------------


def _route_net(problem: RoutingProblem, net: str,
               opt: RouterOptions) -> dict[str, Any]:
    terms = sorted(problem.terminals[net],
                   key=lambda t: (t.x, t.y, t.layers))
    width = problem.width_for_net(net)
    cls = problem.class_of(net)

    def _fail(k: int, reason: str) -> dict[str, Any]:
        # Partial copper of a failed net is dropped: it would block
        # later nets without delivering connectivity.
        return {
            "status": "failed",
            "reason": f"connection {k + 1}/{len(terms) - 1}: {reason}",
            "class": cls, "width": width, "tracks": [], "vias": [],
        }

    # Seed pair: globally closest two terminals (Manhattan on centers).
    best = None
    for i in range(len(terms)):
        for j in range(i + 1, len(terms)):
            d = (abs(terms[i].x - terms[j].x)
                 + abs(terms[i].y - terms[j].y))
            cand = (d, i, j)
            if best is None or cand < best:
                best = cand
    _, i0, j0 = best  # type: ignore[misc]

    tree_cells: set[tuple[int, int, int]] = set()
    tree_xy: dict[tuple[int, int], set[int]] = {}
    paths: list[list[tuple[int, int, int]]] = []

    def _starts(t: Terminal) -> list[tuple[int, int, int]]:
        return [(li, t.cell[0], t.cell[1]) for li in t.layers]

    def _absorb(path: list[tuple[int, int, int]]) -> None:
        paths.append(path)
        for (li, ix, iy) in path:
            tree_cells.add((li, ix, iy))
            tree_xy.setdefault((ix, iy), set()).add(li)

    path = _astar(problem, net, _starts(terms[i0]),
                  set(_starts(terms[j0])), opt)
    if path is None:
        return _fail(0, "no path between closest pair")
    _absorb(path)

    connected = {i0, j0}
    k = 1
    while len(connected) < len(terms):
        # Closest unconnected terminal to the tree (cell Manhattan).
        pick = None
        for idx in range(len(terms)):
            if idx in connected:
                continue
            cx, cy = terms[idx].cell
            d = min(abs(cx - ix) + abs(cy - iy) for (ix, iy) in tree_xy)
            cand = (d, idx)
            if pick is None or cand < pick:
                pick = cand
        idx = pick[1]  # type: ignore[index]
        path = _astar(problem, net, _starts(terms[idx]), tree_cells, opt)
        if path is None:
            return _fail(k, f"terminal at ({terms[idx].x},{terms[idx].y}) "
                            "unreachable")
        _absorb(path)
        connected.add(idx)
        k += 1

    tracks, vias = _emit(problem, net, width, paths, terms, tree_xy)
    return {
        "status": "routed",
        "class": cls,
        "width": width,
        "tracks": tracks,
        "vias": vias,
        "length_mils": sum(
            abs(t["x2"] - t["x1"]) + abs(t["y2"] - t["y1"])
            for t in tracks),
    }


# ---------------------------------------------------------------------------
# A* search
# ---------------------------------------------------------------------------


def _astar(problem: RoutingProblem, net: str,
           starts: list[tuple[int, int, int]],
           targets: set[tuple[int, int, int]],
           opt: RouterOptions) -> list[tuple[int, int, int]] | None:
    """Shortest Manhattan path from any start to any target cell.

    States are ``(layer, ix, iy, dir)`` so the bend penalty sees the
    arrival direction. Returns the path as ``(layer, ix, iy)`` cells
    (consecutive same-cell entries mark a via) or None.
    """
    if not targets:
        return None
    txs = [ix for (_l, ix, _y) in targets]
    tys = [iy for (_l, _x, iy) in targets]
    bx1, bx2 = min(txs), max(txs)
    by1, by2 = min(tys), max(tys)

    def _h(ix: int, iy: int) -> float:
        # Manhattan distance to the target bounding box: admissible
        # (every target is inside the box; bends/vias only add cost).
        return (max(0, bx1 - ix, ix - bx2)
                + max(0, by1 - iy, iy - by2))

    counter = itertools.count()
    open_heap: list[tuple[float, float, int,
                          tuple[int, int, int, int]]] = []
    best: dict[tuple[int, int, int, int], float] = {}
    parent: dict[tuple[int, int, int, int],
                 tuple[int, int, int, int] | None] = {}

    for (li, ix, iy) in sorted(set(starts)):
        if not problem.passable(li, ix, iy, net):
            continue
        st = (li, ix, iy, _DIR_NONE)
        best[st] = 0.0
        parent[st] = None
        heapq.heappush(open_heap, (_h(ix, iy), 0.0, next(counter), st))

    n_layers = len(problem.layers)
    expansions = 0
    while open_heap and expansions < opt.max_expansions:
        f, g, _, st = heapq.heappop(open_heap)
        if g > best.get(st, math.inf) + _EPS:
            continue
        li, ix, iy, d = st
        if (li, ix, iy) in targets:
            return _reconstruct(parent, st)
        expansions += 1

        for di, (dx, dy) in enumerate(_DIRS):
            jx, jy = ix + dx, iy + dy
            if not problem.passable(li, jx, jy, net):
                continue
            ng = g + 1.0
            if d != _DIR_NONE and d != di:
                ng += opt.bend_penalty
            nst = (li, jx, jy, di)
            if ng < best.get(nst, math.inf) - _EPS:
                best[nst] = ng
                parent[nst] = st
                heapq.heappush(
                    open_heap, (ng + _h(jx, jy), ng, next(counter), nst))

        if n_layers > 1 and problem.via_ok(
                ix, iy, net, allow_in_pad=opt.allow_via_in_pad):
            for l2 in range(n_layers):
                if l2 == li:
                    continue
                ng = g + opt.via_cost
                nst = (l2, ix, iy, _DIR_NONE)
                if ng < best.get(nst, math.inf) - _EPS:
                    best[nst] = ng
                    parent[nst] = st
                    heapq.heappush(
                        open_heap, (ng + _h(ix, iy), ng, next(counter), nst))
    return None


def _reconstruct(parent: dict, last: tuple[int, int, int, int]
                 ) -> list[tuple[int, int, int]]:
    cells: list[tuple[int, int, int]] = []
    st: tuple[int, int, int, int] | None = last
    while st is not None:
        cells.append((st[0], st[1], st[2]))
        st = parent[st]
    cells.reverse()
    # Direction-only state changes repeat the same cell; collapse them.
    out: list[tuple[int, int, int]] = []
    for c in cells:
        if not out or out[-1] != c:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Emission (grid path -> track / via dicts)
# ---------------------------------------------------------------------------


def _chamfer(problem: RoutingProblem, net: str, li: int,
             cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Cut every right-angle corner in a same-layer run into two 45s.

    A right angle in signal copper is the first thing a reviewer picks
    up and the standard house rule on most boards. The search runs on a
    4-direction grid and cannot produce anything else, so the corners
    are cut here, after the fact, where doing it cannot change which
    nets route. Putting the diagonals in the search itself was tried and
    measured: it reroutes earlier nets and strands later ones.

    VERIFIED AGAINST THE SAME OBSTACLE MAP the search used. The diagonal
    crosses cells that neither leg occupies, so every one is checked. A
    corner whose chamfer would clip a pad keeps its right angle, which
    is correct and visible rather than clever and shorted.

    Cuts as deep as it can, never taking more than half of either leg,
    so a short jog keeps its shape and a long run gets a long chamfer.
    """
    if len(cells) < 3:
        return cells

    def _unit(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
        return ((b[0] > a[0]) - (b[0] < a[0]),
                (b[1] > a[1]) - (b[1] < a[1]))

    out: list[tuple[int, int]] = [cells[0]]
    i = 1
    while i < len(cells) - 1:
        corner = cells[i]
        din = _unit(cells[i - 1], corner)
        dout = _unit(corner, cells[i + 1])
        # A right angle only: straight runs and reversals are left alone.
        if din == (0, 0) or dout == (0, 0) or (
                din[0] * dout[0] + din[1] * dout[1]) != 0:
            out.append(corner)
            i += 1
            continue

        # How far the legs run, in cells, either side of the corner.
        back = 0
        while back < len(out) and _unit(out[-1 - back], corner) == din:
            back += 1
        fwd = 0
        while (i + fwd + 1 < len(cells)
               and _unit(cells[i + fwd], cells[i + fwd + 1]) == dout):
            fwd += 1

        depth = min(_MAX_CHAMFER_CELLS, back // 2, fwd // 2)
        applied = 0
        for d in range(depth, 0, -1):
            a = (corner[0] - din[0] * d, corner[1] - din[1] * d)
            step = (din[0] + dout[0], din[1] + dout[1])
            if all(problem.passable(li, a[0] + step[0] * k,
                                    a[1] + step[1] * k, net)
                   for k in range(1, d + 1)):
                applied = d
                break

        if not applied:
            out.append(corner)
            i += 1
            continue

        # Drop the cells between the cut point and the corner; they were
        # already emitted on the way in.
        for _ in range(applied - 1):
            out.pop()
        a = out[-1]
        step = (din[0] + dout[0], din[1] + dout[1])
        for k in range(1, applied + 1):
            out.append((a[0] + step[0] * k, a[1] + step[1] * k))
        # Resume past the outgoing cells the chamfer replaced.
        i += applied

    if out[-1] != cells[-1]:
        out.append(cells[-1])
    return out


def _emit(problem: RoutingProblem, net: str, width: int,
          paths: list[list[tuple[int, int, int]]],
          terms: list[Terminal],
          tree_xy: dict[tuple[int, int], set[int]],
          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tracks: list[dict[str, Any]] = []
    vias: list[dict[str, Any]] = []
    via_at: set[tuple[int, int]] = set()

    def _track(x1: int, y1: int, x2: int, y2: int, layer: str) -> None:
        if (x1, y1) == (x2, y2):
            return
        tracks.append({
            "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
            "width": int(width), "layer": layer, "net_name": net,
        })

    def _flush_cells(cells: list[tuple[int, int]], layer_idx: int) -> None:
        """Chamfer the run's corners, then emit it as track segments.

        Done on the CELLS rather than on the emitted segments: the
        chamfer has to check the obstacle map, and that is indexed by
        cell.
        """
        if len(cells) < 2:
            return
        points: list[tuple[int, int]] = []
        for ix, iy in _chamfer(problem, net, layer_idx, cells):
            pt = problem.cell_center(ix, iy)
            if not points or points[-1] != pt:
                points.append(pt)
        _flush_run(points, problem.layers[layer_idx], _track)

    for path in paths:
        run_cells: list[tuple[int, int]] = []
        run_layer = path[0][0]
        for (li, ix, iy) in path:
            if li != run_layer:
                _flush_cells(run_cells, run_layer)
                pt = problem.cell_center(ix, iy)
                if pt not in via_at:
                    via_at.add(pt)
                    vias.append({
                        "x": pt[0], "y": pt[1], "net": net,
                        "size": int(problem.rules.via_size_mils),
                        "hole_size": int(problem.rules.via_drill_mils),
                    })
                run_cells = [(ix, iy)]
                run_layer = li
            else:
                if not run_cells or run_cells[-1] != (ix, iy):
                    run_cells.append((ix, iy))
        _flush_cells(run_cells, run_layer)

    # Stub from each exact pad center to its snapped grid point, on a
    # layer where the pad copper and the routed tree coincide.
    for t in terms:
        gx, gy = problem.cell_center(*t.cell)
        if (gx, gy) == (t.x, t.y):
            continue
        usable = sorted(set(t.layers) & tree_xy.get(t.cell, set()))
        li = usable[0] if usable else t.layers[0]
        layer = problem.layers[li]
        # Two-segment Manhattan dogleg when the offset is diagonal.
        if gx != t.x and gy != t.y:
            _track(t.x, t.y, gx, t.y, layer)
            _track(gx, t.y, gx, gy, layer)
        else:
            _track(t.x, t.y, gx, gy, layer)
    return tracks, vias


def _flush_run(run: list[tuple[int, int]], layer: str, track_fn) -> None:
    """Collapse a same-layer polyline of grid points into collinear
    track segments."""
    if len(run) < 2:
        return
    sx, sy = run[0]
    px, py = run[0]
    dirv: tuple[int, int] | None = None
    for (x, y) in run[1:]:
        d = ((x > px) - (x < px), (y > py) - (y < py))
        if dirv is None:
            dirv = d
        elif d != dirv:
            track_fn(sx, sy, px, py, layer)
            sx, sy = px, py
            dirv = d
        px, py = x, y
    track_fn(sx, sy, px, py, layer)


__all__ = [
    "RouterOptions",
    "route_geometry",
    "route_problem",
    "validate_solution",
]
