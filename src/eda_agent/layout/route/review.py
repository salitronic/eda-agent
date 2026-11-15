# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Routing in rounds, and the review the AI gives after each.

A board is routed a group of nets at a time, the groups in the order an
engineer lays them: differential pairs, switching nodes, high-current
paths, clocks, the other nets the brief rules, then the rest. Each round
routes only what the board's copper still leaves apart, so the rounds
before it, and every route the AI laid by hand, are fixed walls.

After each round the router stops and hands its work over: what it laid
per net, what it could not lay and the options it tried, a picture, and
findings: each brief rule measured on the copper (length, skew, return
plane, areas) and each judgement that is the AI's (detours, vias, pad
entries, nets left unrouted). Nothing is written until the AI accepts, and
pcb_autoroute_apply refuses while a finding is unanswered: the AI routes
again, or accepts it with its reason.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Optional

from ..model import LayoutBoard
from .brief import Brief
from .integrity import guard_misses, pdn_misses, termination_misses

#: The order of the rounds, by kind.
ROUND_ORDER = ("antenna", "differential", "switching", "high_current", "clock", "briefed",
               "rest")


def open_nets(board: LayoutBoard) -> set[str]:
    """Nets with two pads or more that the board's copper leaves apart."""
    from ..drc import connectivity

    return {n for n, groups in connectivity(board).items() if len(groups) > 1}


def rounds(board: LayoutBoard, brief: Brief, critical: dict[str, str],
           open_: Optional[set[str]] = None) -> list[dict[str, Any]]:
    """The rounds still to route, in order: {kind, nets}."""
    left = open_nets(board) if open_ is None else set(open_)
    out = []
    taken: set[str] = set()
    for kind in ROUND_ORDER:
        if kind == "briefed":
            nets = {n for n in brief.of if n in left}
        elif kind == "rest":
            nets = set(left)
        else:
            nets = {n for n, k in critical.items() if k == kind and n in left}
        nets -= taken
        if nets:
            out.append({"kind": kind, "nets": sorted(nets)})
            taken |= nets
        left -= nets
    return out


def _tree_length(pads) -> float:
    """Length of the shortest tree joining the pads' centres (Prim)."""
    pts = [(p.x, p.y) for p in pads]
    if len(pts) < 2:
        return 0.0
    best = {i: math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]) for i in range(1, len(pts))}
    total = 0.0
    while best:
        i = min(best, key=best.get)
        total += best.pop(i)
        for j in best:
            best[j] = min(best[j], math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
    return total


def net_metrics(tracks: list[dict], vias: list[dict], pads_of: dict) -> dict[str, dict]:
    """Per net: copper length, vias, layers, and the length over the
    straight distance between its farthest pads (the detour)."""
    out: dict[str, dict] = {}
    for t in tracks:
        m = out.setdefault(t["net_name"], {"length": 0.0, "vias": 0, "layers": set(), "tracks": 0})
        m["length"] += math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"])
        m["layers"].add(t["layer"])
        m["tracks"] += 1
    for v in vias:
        out.setdefault(v["net"], {"length": 0.0, "vias": 0, "layers": set(), "tracks": 0})["vias"] += 1
    for net, m in out.items():
        # Against the shortest tree joining the net's pads: a net of three
        # pads laid round the board once passed unasked at 780 mil.
        span = _tree_length(pads_of.get(net, []))
        m["detour"] = round(m["length"] / span, 2) if span > 1 else None
        m["length"] = round(m["length"], 1)
        m["layers"] = sorted(m["layers"])
    return out


#: A route this much longer than the shortest tree of its pads is put to the AI.
DETOUR_ASK = 1.6
#: Vias on a net with no via budget that are put to the AI.
VIAS_ASK = 4


def pad_entries(tracks: list[dict], pads_of: dict) -> dict[str, list[str]]:
    """Per net, its surface pads the round's tracks do not enter straight
    and centrally: a track that crosses the pad without ending on its
    centre, or that ends there at an angle to the pad's axes. DRC passes
    both; a good layout has neither."""
    from .. import geom

    out: dict[str, list[str]] = {}
    for net in sorted({t["net_name"] for t in tracks}):
        mine = [t for t in tracks if t["net_name"] == net]
        for p in pads_of.get(net, []):
            if p.hole:
                continue
            for c in p.copper:
                s = p.shape_on(c.layer)
                touching = [t for t in mine if t["layer"] == c.layer and geom.clearance(
                    geom.capsule(t["x1"], t["y1"], t["x2"], t["y2"], t["width"]), s) <= 0]
                if not touching:
                    continue
                ends = [t for t in touching
                        if math.hypot(t["x1"] - p.x, t["y1"] - p.y) < 1.0
                        or math.hypot(t["x2"] - p.x, t["y2"] - p.y) < 1.0]
                if not ends:
                    out.setdefault(net, []).append(f"crosses {p.key} off its centre")
                    continue
                for t in ends:
                    a = math.degrees(math.atan2(t["y2"] - t["y1"], t["x2"] - t["x1"]))
                    off = (a - p.rotation) % 90.0
                    if 1.0 < off < 89.0:
                        out.setdefault(net, []).append(f"enters {p.key} at {round(min(off, 90 - off))} "
                                                       "degrees to its axis")
                        break
    return out


# --- findings: what must be answered before a round is applied ------------------------

#: Spacing of the points a track is checked at for its return plane, mils.
REFERENCE_STEP = 10.0


def _finding(fid: str, check: str, text: str, **extra) -> dict[str, Any]:
    return {"id": fid, "check": check, "text": text, **extra}


def _simulate(what: str, **args) -> dict[str, Any]:
    """The call that simulates a finding on the real copper; its result
    file is the evidence an answer gives."""
    return {"tool": "pcb_route_simulate", "what": what, **args}


def copper_length(board: LayoutBoard, net: str) -> float:
    """A net's routed copper on the board: tracks and arcs, footprints'
    own copper apart."""
    total = sum(math.hypot(t.x2 - t.x1, t.y2 - t.y1) for t in board.tracks
                if t.net == net and not t.comp and not t.keepout)
    total += sum(abs(math.radians((a.a2 - a.a1) % 360.0)) * a.radius for a in board.arcs
                 if a.net == net and not a.comp)
    return total


def _reference_cover(board: LayoutBoard, net: str, layer: str):
    """A test (x, y) -> whether copper of ``net`` (a plane, a pour or its
    outline, a region) lies on a copper layer next to ``layer``."""
    from .. import geom

    order = board.copper_layers()
    if layer not in order:
        return lambda x, y: False
    i = order.index(layer)
    near = [order[k] for k in (i - 1, i + 1) if 0 <= k < len(order)]
    if any(l.name in near and l.kind == "plane" and l.plane_net == net for l in board.layers):
        return lambda x, y: True
    shapes = [geom.polygon(r.outline, r.holes) for r in board.regions
              if r.layer in near and r.net == net and len(r.outline) >= 3
              and r.kind in ("pour", "copper", "pour_boundary")]
    return lambda x, y: any(geom.point_in_poly(x, y, s) for s in shapes)


def reference_gaps(board: LayoutBoard, tracks: list[dict], brief: Brief) -> dict[str, dict]:
    """Per net with a ``reference`` in the brief: the length of its new
    tracks with no copper of that net on a layer next to them, and where
    the first such stretch is."""
    out: dict[str, dict] = {}
    covers: dict[tuple, Any] = {}
    for t in tracks:
        e = brief.of.get(t["net_name"])
        if e is None or not e.reference:
            continue
        key = (e.reference, t["layer"])
        if key not in covers:
            covers[key] = _reference_cover(board, *key)
        cover = covers[key]
        n = max(1, int(math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"]) / REFERENCE_STEP))
        step = math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"]) / n
        for k in range(n):
            f = (k + 0.5) / n
            x, y = t["x1"] + (t["x2"] - t["x1"]) * f, t["y1"] + (t["y2"] - t["y1"]) * f
            if not cover(x, y):
                g = out.setdefault(t["net_name"], {"uncovered": 0.0, "at": (round(x), round(y)),
                                                   "layer": t["layer"], "reference": e.reference})
                g["uncovered"] += step
    return out


def impedance_misses(board: LayoutBoard, tracks: list[dict], brief: Brief) -> dict[str, dict]:
    """Per net with an impedance target: its new pieces off the target by
    more than the entry's tolerance, the worst of them, and their length.
    Measured at each piece's width on its own layer of the board's stack."""
    from . import impedance as zi

    geo = zi.geometry(board)
    out: dict[str, dict] = {}
    for t in tracks:
        e = brief.of.get(t["net_name"])
        if e is None or e.target_ohms is None:
            continue
        target, gap = e.target_ohms, (e.gap if e.diff_impedance else None)
        g = geo.get(t["layer"])
        z = zi.z0(g, t["width"], gap) if g else None
        off = None if z is None else abs(z - target) / target * 100.0
        if off is not None and off <= e.tolerance_pct:
            continue
        m = out.setdefault(t["net_name"], {"target": target, "worst": None, "length": 0.0,
                                           "layer": t["layer"], "width": t["width"],
                                           "diff": bool(gap)})
        m["length"] += math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"])
        if z is None or m["worst"] is None or (
                m["worst"] != "none" and abs(z - target) > abs(m["worst"] - target)):
            m.update(worst="none" if z is None else round(z, 1), layer=t["layer"],
                     width=t["width"])
    return out


#: Pieces a track is cut into to measure how much of it runs beside another
#: net, mils.
PARALLEL_STEP = 10.0


def apart_misses(routed: LayoutBoard, brief: Brief, nets: Iterable[str]) -> dict[tuple, dict]:
    """(net, other) -> how the net's tracks on the routed board break its
    entry's ``spacing`` from ``other`` (the smallest gap and where, against
    the other's tracks, vias, pads and pours on the same layer) or its
    ``max_parallel`` (the length of track within ``parallel_gap`` of the
    other's tracks and pours on the same layer), or its ``max_broadside``
    (the length within ``parallel_gap`` in plan of the other's tracks on a
    neighbouring layer). A net class in apart_from stands for its nets.
    Only entries the round touches: one of their nets, or a net they keep
    away from, in ``nets``."""
    from .. import geom
    from .creepage import side_nets
    from .integrity import broadside

    nets = set(nets)
    order = routed.copper_layers()

    def spans(v, layer):
        if layer not in order or v.low_layer not in order or v.high_layer not in order:
            return True
        a, b = sorted((order.index(v.low_layer), order.index(v.high_layer)))
        return a <= order.index(layer) <= b

    def shapes(other, layer, runs_only):
        out = [t.shape() for t in routed.tracks
               if t.net == other and t.layer == layer and not t.keepout]
        out += [r.shape() for r in routed.regions if r.net == other and r.layer == layer
                and r.kind in ("pour", "copper") and len(r.outline) >= 3]
        if not runs_only:
            out += [geom.circle(v.x, v.y, v.layer_diameters.get(layer, v.diameter))
                    for v in routed.vias if v.net == other and spans(v, layer)]
            out += [s for p in routed.pads if p.net == other
                    for s in [p.shape_on(layer)] if s is not None]
        return out

    found: dict[tuple, dict] = {}
    cache: dict[tuple, list] = {}
    for e in brief.entries:
        away = sorted(side_nets(routed, e.apart_from) - set(e.nets))
        if not away or not (nets & (set(e.nets) | set(away))):
            continue
        for net in e.nets:
            mine = [t for t in routed.tracks if t.net == net and not t.comp and not t.keepout]
            for other in away:
                worst, near = None, 0.0
                for t in mine:
                    if e.spacing is not None:
                        key = (other, t.layer, False)
                        if key not in cache:
                            cache[key] = shapes(other, t.layer, False)
                        s = t.shape()
                        for o in cache[key]:
                            if not geom.bboxes_near(s, o, e.spacing):
                                continue
                            g = geom.clearance(s, o)
                            if g < e.spacing - 1e-6 and (worst is None or g < worst[0]):
                                worst = (g, t.layer, (round((t.x1 + t.x2) / 2),
                                                      round((t.y1 + t.y2) / 2)))
                    if e.max_parallel is not None:
                        key = (other, t.layer, True)
                        if key not in cache:
                            cache[key] = shapes(other, t.layer, True)
                        L = math.hypot(t.x2 - t.x1, t.y2 - t.y1)
                        n = max(1, int(math.ceil(L / PARALLEL_STEP)))
                        for k in range(n):
                            f0, f1 = k / n, (k + 1) / n
                            piece = geom.capsule(t.x1 + (t.x2 - t.x1) * f0, t.y1 + (t.y2 - t.y1) * f0,
                                                 t.x1 + (t.x2 - t.x1) * f1, t.y1 + (t.y2 - t.y1) * f1,
                                                 t.width)
                            if any(geom.bboxes_near(piece, o, e.parallel_gap)
                                   and geom.clearance(piece, o) <= e.parallel_gap + 1e-6
                                   for o in cache[key]):
                                near += L / n
                miss: dict[str, Any] = {}
                if worst is not None:
                    miss.update(gap=round(worst[0], 2), layer=worst[1], at=worst[2],
                                spacing=e.spacing)
                if e.max_parallel is not None and near > e.max_parallel + 1e-6:
                    miss.update(parallel=round(near, 1), max_parallel=e.max_parallel,
                                parallel_gap=e.parallel_gap)
                if e.max_broadside is not None and e.parallel_gap:
                    over, at = broadside(routed, net, {other}, e.parallel_gap)
                    if over > e.max_broadside + 1e-6:
                        miss.update(broadside=round(over, 1), max_broadside=e.max_broadside,
                                    parallel_gap=e.parallel_gap, broadside_at=at)
                if miss:
                    found[(net, other)] = miss
    return found


def barrier_misses(board: LayoutBoard, routed: LayoutBoard, brief: Brief,
                   nets: Iterable[str]) -> list[tuple]:
    """(barrier, layer, 'creepage' or 'clearance', {gap, need, at}) for each
    barrier the round touches (one of its nets on a side, or a barrier from
    every other net), measured on the routed board with the pours as read."""
    from dataclasses import replace

    from .creepage import measure, side_nets

    if not brief.barriers:
        return []
    nets = set(nets)
    have = {id(r) for r in routed.regions}
    merged = replace(routed, regions=list(routed.regions) + [
        r for r in board.regions if r.kind == "pour" and id(r) not in have])
    out = []
    for b in brief.barriers:
        a = side_nets(routed, b.nets)
        other = side_nets(routed, b.apart_from) if b.apart_from else None
        if other is not None and not nets & (a | other):
            continue
        for layer, m in sorted(measure(merged, a, other, b.creepage, b.clearance).items()):
            for what in ("creepage", "clearance"):
                if what in m:
                    out.append((b, layer, what, m[what]))
    return out


def assembly_findings(routed: LayoutBoard, vias: list[dict], brief: Brief,
                      nets: Iterable[str]) -> list[dict]:
    """The brief's assembly and orient rules on the round: its new vias
    near a surface pad or under a body, its parts closer than part_gap,
    and its parts facing the wrong way or with a pin on the wrong net."""
    from types import SimpleNamespace

    from . import assembly as A
    from .fanout import find_bgas, find_exposed_pads

    nets = set(nets)
    refs = {p.comp for p in routed.pads if p.comp and p.net in nets}
    out: list[dict] = []
    rule = brief.assembly
    if rule is not None:
        free = {i for b in find_bgas(routed) for i in b.pads} | set(find_exposed_pads(routed))
        mine = [SimpleNamespace(x=v["x"], y=v["y"], diameter=v.get("size", 0.0), net=v["net"])
                for v in vias if v.get("net") in nets]
        worst: dict[tuple, tuple] = {}
        for v, bar, gap in A.via_misses(mine, rule, routed, free):
            key = (v.net, bar[2], bar[3])
            if key not in worst or gap < worst[key][1]:
                worst[key] = (v, gap, bar[1])
        for (net, what, owner), (v, gap, keep) in sorted(worst.items()):
            said = (f"a via {max(gap, 0.0):.1f} mil from {owner}'s pad, under its via_to_pad "
                    f"{keep:g}" if what == "pad" else f"a via under {owner}'s body")
            out.append(_finding(f"assembly:via:{net}:{owner}", "assembly",
                                f"{net}: {said}, at ({v.x:.0f}, {v.y:.0f}) ({rule.source}). "
                                "Route it again, or tent or fill the via and say so.",
                                net=net, part=owner, what=what, measured=round(max(gap, 0.0), 2),
                                limit=keep if what == "pad" else None))
        for a, b, gap in A.gap_misses(routed, rule, refs):
            out.append(_finding(f"assembly:gap:{a}:{b}", "assembly",
                                f"{a} and {b}: {gap:.1f} mil body to body, under the part_gap "
                                f"{rule.part_gap:g} ({rule.source}). Move one, or confirm the "
                                "assembler takes it.", parts=[a, b], what="gap",
                                measured=round(gap, 2), limit=rule.part_gap))
    touched = [r for r in brief.orient if refs & set(r.parts)]
    for r, part, what, text in A.orient_misses(routed, touched):
        out.append(_finding(f"orient:{r.name}:{part}:{what}", "orient",
                            f"orient {r.name}: {text} ({r.source}). Turn the part, or fix "
                            "the schematic or footprint if the pin is wrong.",
                            rule=r.name, part=part, what=what))
    return out


def loop_measures(board: LayoutBoard, routed: LayoutBoard, brief: Brief,
                  nets: Iterable[str]) -> list[dict]:
    """Each loop of the brief the round touches (one of its pads' nets in
    ``nets``), measured on the routed board: its area (square mils) or the
    length of its path, the inductance that area gives, and how many of its
    net hops follow routed copper. A hop on a plane or pour net (as read or
    as routed) is a return straight under the path; a hop on one part
    crosses its body."""
    from ..copper_path import plane_nets
    from ..geom import ring_area
    from ..loop_audit import loop_inductance_nh, routed_points
    from ...design.loops import Loop

    nets = set(nets)
    pads = {(p.comp, str(p.name)): p for p in routed.pads if p.comp}
    by_key = {p.key: k for k, p in pads.items()}
    planes = plane_nets(board) | plane_nets(routed)
    copper: dict = {}
    out = []
    for lp in brief.loops:
        keys = [by_key[m] for m in lp.members if m in by_key]
        if len(keys) != len(lp.members):
            continue
        loop_nets = sorted({pads[k].net for k in keys if pads[k].net})
        if not nets & set(loop_nets):
            continue
        path = keys + [keys[0]] if lp.is_area else keys
        loop = Loop(lp.kind, path, loop_nets, "brief", "copper")
        pts, done, hops = routed_points(routed, loop, pads, planes, copper)
        m: dict[str, Any] = {"name": lp.name, "kind": lp.kind, "routed_hops": f"{done}/{hops}",
                             "complete": done == hops}
        if lp.is_area:
            area = ring_area(pts) if len(pts) >= 3 else 0.0
            m.update(area=round(area, 1), inductance_nh_est=round(loop_inductance_nh(area), 2),
                     limit=lp.max_area)
        else:
            m.update(length=round(sum(math.dist(pts[i], pts[i + 1])
                                      for i in range(len(pts) - 1)), 1), limit=lp.max_length)
        if lp.max_length is not None and lp.is_area:
            m["length"] = round(sum(math.dist(pts[i], pts[i + 1])
                                    for i in range(len(pts) - 1)), 1)
        m["over"] = [what for what, got, lim in (("area", m.get("area"), lp.max_area),
                                                 ("length", m.get("length"), lp.max_length))
                     if got is not None and lim is not None and got > lim + 1e-6]
        out.append(m)
    return out


def area_intrusions(board: LayoutBoard, brief: Brief,
                    poured: Optional[LayoutBoard] = None) -> list[dict]:
    """Copper inside an area of the brief that bars its net: the tracks and
    vias of ``board``, and the poured copper and regions of ``poured`` (the
    board as read, its pours as Altium last poured them: a pour with a
    cutout round the area keeps out of it)."""
    from .. import geom
    from .brief import _inside

    poured = poured if poured is not None else board
    out = []
    for a in brief.areas:
        name = a.name or a.kind
        hits: dict[tuple, str] = {}
        for t in board.tracks:
            if t.comp or t.keepout or not t.net or not a.bars(t.net, t.layer):
                continue
            mid = ((t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2)
            if any(_inside(a.polygons, x, y) for x, y in ((t.x1, t.y1), mid, (t.x2, t.y2))):
                hits.setdefault((t.net, t.layer), "track")
        for v in board.vias:
            if v.net and not v.comp and v.net not in a.allow and _inside(a.polygons, v.x, v.y):
                hits.setdefault((v.net, "via"), "via")
        rings = [geom.polygon(r) for r in a.polygons]
        for r in poured.regions:
            if r.kind not in ("pour", "copper") or not r.net or r.comp \
                    or not a.bars(r.net, r.layer) or len(r.outline) < 3:
                continue
            pour = geom.polygon(r.outline, r.holes)
            if any(_inside(a.polygons, x, y) for x, y in r.outline) or any(
                    geom.point_in_poly(x, y, pour) for ring in a.polygons for x, y in ring) \
                    or any(geom.clearance(s, pour) <= 0 for s in rings):
                hits[(r.net, r.layer)] = "pour" if r.kind != "copper" else "region"
        for (net, layer), what in sorted(hits.items()):
            out.append({"area": name, "kind": a.kind, "net": net, "layer": layer, "what": what})
    return out


def findings(board: LayoutBoard, routed: LayoutBoard, tracks: list[dict], vias: list[dict],
             brief: Brief, nets: Iterable[str], metrics: dict, held: dict, unrouted: dict,
             advice: dict, planes: Iterable[str] = (), entries: Optional[dict] = None,
             judge: bool = True) -> list[dict]:
    """Everything the AI must answer before the round is applied, each with
    an ``id`` the answer names. The brief's rules measured on the routed
    board (length, skew, return plane, areas), then, with ``judge``, the
    judgements that are the AI's (detours, vias, pad entries).

    ``board`` is the board as read, its pours as last poured; ``routed``
    the board with the round's copper on it."""
    nets = set(nets)
    out: list[dict] = []
    for net, h in sorted(held.items()):
        out.append(_finding(f"held:{net}", "held", f"{net}: routed only by breaking the brief "
                            f"({'; '.join(h['broken'])}), so not laid. Change that rule or the "
                            "board, then route the round again.", net=net))
    for net in sorted(unrouted):
        opts = (advice.get(net) or {}).get("options") or []
        tail = (f"Best option tried: {opts[0]['option']} ({opts[0]['result']}). Choose one, "
                "or say how it should go." if opts else
                "No option tried routes it: give a corridor, a layer, or move a part.")
        out.append(_finding(f"unrouted:{net}", "unrouted", f"{net}: not routed. {tail}", net=net))
    # The brief's measurable rules, on the whole net as the board now has it.
    lengths = {}
    for net in sorted(nets | {n for e in brief.entries if e.match for n in e.nets}):
        if net in brief.of:
            lengths[net] = copper_length(routed, net)
    for net in sorted(nets):
        e = brief.of.get(net)
        if e is None or net in unrouted or net in held:
            continue
        L = lengths.get(net, 0.0)
        if e.max_length is not None and L > e.max_length + 1e-6:
            out.append(_finding(f"length:{net}", "length",
                                f"{net}: {L:.0f} mil long, over its max_length {e.max_length:g} "
                                f"({e.source or 'no source'}).", net=net, measured=round(L, 1),
                                limit=e.max_length))
        if e.min_length is not None and L < e.min_length - 1e-6:
            out.append(_finding(f"length:{net}", "length",
                                f"{net}: {L:.0f} mil long, under its min_length {e.min_length:g}.",
                                net=net, measured=round(L, 1), limit=e.min_length))
    groups: dict[str, list] = {}
    for e in brief.entries:
        if e.match:
            groups.setdefault(e.match, []).append(e)
    for g, es in sorted(groups.items()):
        members = [n for e in es for n in e.nets]
        if not nets & set(members):
            continue
        skew = min((e.skew for e in es if e.skew is not None), default=None)
        open_ = [n for n in members if n in unrouted or lengths.get(n, 0.0) <= 0]
        if skew is None or open_:
            continue
        got = {n: lengths[n] for n in members}
        spread = max(got.values()) - min(got.values())
        if spread > skew + 1e-6:
            longest = max(got, key=got.get)
            shortest = min(got, key=got.get)
            out.append(_finding(f"skew:{g}", "skew",
                                f"match group {g}: {spread:.0f} mil between {longest} "
                                f"({got[longest]:.0f}) and {shortest} ({got[shortest]:.0f}), over "
                                f"its skew {skew:g}. Tune the short ones (pcb_tune_length) or "
                                "route again.", group=g, measured=round(spread, 1), limit=skew))
    for net, g in sorted(reference_gaps(board, tracks, brief).items()):
        out.append(_finding(f"reference:{net}", "reference",
                            f"{net}: {g['uncovered']:.0f} mil of track on {g['layer']} with no "
                            f"{g['reference']} on a layer next to it (first near {g['at']}). Its "
                            "return current has no plane there: move the route, or pour "
                            f"{g['reference']} under it.", net=net, measured=round(g["uncovered"], 1)))
    for net, m in sorted(impedance_misses(board, tracks, brief).items()):
        got = ("no impedance the stack gives (no dielectric there, or out of the closed "
               "form's range)" if m["worst"] == "none" else f"{m['worst']} ohm")
        out.append(_finding(f"impedance:{net}", "impedance",
                            f"{net}: {m['length']:.0f} mil off its {m['target']:g} ohm"
                            f"{' differential' if m['diff'] else ''} target, worst {got} at "
                            f"{m['width']:g} mil on {m['layer']}. Route it on a layer its width "
                            "suits, or confirm the stack with a field solver and give the "
                            "result as evidence.", net=net,
                            simulate=_simulate("impedance", net=net),
                            measured=None if m["worst"] == "none" else m["worst"],
                            limit=m["target"]))
    for (net, other), m in sorted(apart_misses(routed, brief, nets).items()):
        said = []
        if "gap" in m:
            said.append(f"{m['gap']:g} mil from {other} on {m['layer']} near {m['at']}, under "
                        f"its spacing {m['spacing']:g}")
        if "parallel" in m:
            said.append(f"{m['parallel']:.0f} mil of track within {m['parallel_gap']:g} mil of "
                        f"{other}, over its max_parallel {m['max_parallel']:g}")
        if "broadside" in m:
            said.append(f"{m['broadside']:.0f} mil over or under {other} on the next layer "
                        f"(within {m['parallel_gap']:g} mil across, first near "
                        f"{m['broadside_at']}), over its max_broadside {m['max_broadside']:g}")
        out.append(_finding(f"apart:{net}:{other}", "apart",
                            f"{net}: {'; '.join(said)}. Route it further away or on another "
                            "layer, or give a crosstalk simulation's result as evidence.",
                            net=net, other=other,
                            simulate=_simulate("crosstalk", net=net, other=other),
                            **{k: v for k, v in m.items() if k not in ("at", "broadside_at")}))
    for net, m in sorted(termination_misses(routed, brief, nets).items()):
        e = brief.of[net]
        if m["what"] == "source":
            said = (f"{m['length']:.0f} mil of copper from the driver {e.driver} to its series "
                    f"terminator {m['part']}, over its max_stub {m['limit']:g}. Move "
                    f"{m['part']} to the driver")
            worst = m["length"]
        else:
            worst = max(m["stubs"].values())
            said = (f"stubs off its trunk ({e.driver} to {m['far']}, {m['trunk']:.0f} mil) "
                    f"over its max_stub {m['limit']:g}: "
                    + ", ".join(f"{k} {v:g} mil" for k, v in sorted(m["stubs"].items()))
                    + ". Route it as a daisy chain through those pads, or move the "
                    "termination to the far end")
        out.append(_finding(f"termination:{net}", "termination",
                            f"{net}: {said} ({e.source or 'no source'}).", net=net,
                            measured=worst, limit=m["limit"]))
    for net, m in sorted(guard_misses(routed, brief, nets).items()):
        out.append(_finding(f"guard:{net}", "guard",
                            f"{net}: {m['unguarded']:.0f} mil of track on {m['layer']} without "
                            f"{m['guard']} within {m['guard_gap']:g} mil on both sides (first "
                            f"near {m['at']}), over its max_unguarded {m['limit']:g}. Lay "
                            f"{m['guard']} beside it (a guard track or a pour), or route it "
                            "where the guard runs.", net=net, measured=m["unguarded"],
                            limit=m["limit"]))
    for m in pdn_misses(routed, brief, nets):
        r = next(x for x in brief.pdn if x.name == m["rule"])
        if m["what"] == "no_decap":
            said = f"power pin {m['pin']} ({m['net']}) has no decoupling capacitor"
            fix = "add one, or name it in the rule's decaps"
        elif m["what"] == "distance":
            how = ("along the copper" if m["along_copper"]
                   else "straight (the copper does not join them yet)")
            said = (f"{m['cap']} is {m['measured']:.0f} mil from power pin {m['pin']} {how}, "
                    f"over its max_distance {m['limit']:g}")
            fix = f"move {m['cap']} to the pin, or route its rail pad straight to it"
        elif m["what"] == "vias":
            said = (f"power pin {m['pin']} ({m['net']}) has {m['measured']} via(s) within "
                    f"{m['reach']:g} mil, under its min_vias {m['limit']}")
            fix = "add vias to the plane beside the pin"
        else:
            said = (f"{m['cap']}'s return pad {m['pin']} ({m['net']}) has {m['measured']} "
                    f"via(s) within {m['reach']:g} mil, under its min_vias {m['limit']}")
            fix = "add return vias beside the pad"
        out.append(_finding(f"pdn:{r.name}:{m['pin']}:{m['what']}", "pdn",
                            f"pdn {r.name}: {said} ({r.source}). {fix[0].upper() + fix[1:]}, "
                            "or give a PDN simulation's result as evidence.", rule=r.name,
                            pin=m["pin"], what=m["what"],
                            simulate=_simulate("pdn", name=r.name),
                            **{k: m[k] for k in ("measured", "limit", "cap") if k in m}))
    for b, layer, what, m in barrier_misses(board, routed, brief, nets):
        kind = "along the surface" if what == "creepage" else "through air"
        out.append(_finding(f"barrier:{b.name}:{layer}:{what}", "barrier",
                            f"barrier {b.name}: {m['gap']:g} mil {kind} on {layer} near "
                            f"{m['at']}, under its {what} {m['need']:g} ({b.source}). Move the "
                            "copper apart, or cut a slot between the sides (creepage only).",
                            barrier=b.name, layer=layer, what=what, measured=m["gap"],
                            limit=m["need"]))
    for m in loop_measures(board, routed, brief, nets):
        if not m["over"] or not m["complete"]:
            continue
        lp = next(x for x in brief.loops if x.name == m["name"])
        said = []
        if "area" in m["over"]:
            said.append(f"encloses {m['area']:.0f} sq mil (about {m['inductance_nh_est']:g} nH), "
                        f"over its max_area {lp.max_area:g}")
        if "length" in m["over"]:
            said.append(f"runs {m['length']:.0f} mil, over its max_length {lp.max_length:g}")
        out.append(_finding(f"loop:{m['name']}", "loop",
                            f"loop {m['name']} ({m['kind']}): {'; '.join(said)} "
                            f"({lp.source}). Close it up: the parts nearer, the return on the "
                            "layer under the forward path, or give a simulation's result as "
                            "evidence.", loop=m["name"],
                            simulate=_simulate("loop", name=m["name"]),
                            measured=m.get("area") if "area" in m["over"] else m.get("length"),
                            limit=lp.max_area if "area" in m["over"] else lp.max_length))
    for hit in area_intrusions(routed, brief, poured=board):
        fix = ("add a polygon cutout there in Altium, so the repour leaves it"
               if hit["what"] == "pour" else "take that copper out of the area")
        out.append(_finding(f"area:{hit['area']}:{hit['net']}:{hit['layer']}", "area",
                            f"area {hit['area']} ({hit['kind']}): {hit['net']} {hit['what']} on "
                            f"{hit['layer']} inside it; {fix}.", **hit))
    out += assembly_findings(routed, vias, brief, nets)
    if judge:
        planes = set(planes)
        for net, m in sorted(metrics.items()):
            e = brief.of.get(net)
            if m.get("detour") and m["detour"] >= DETOUR_ASK and (e is None or e.max_length is None):
                out.append(_finding(f"detour:{net}", "detour",
                                    f"{net}: {m['detour']}x the shortest tree of its pads. "
                                    "Acceptable, or give it a region or max_length?", net=net,
                                    measured=m["detour"]))
            if (e is None or e.max_vias is None) and m["vias"] >= VIAS_ASK and net not in planes:
                out.append(_finding(f"vias:{net}", "vias", f"{net}: {m['vias']} vias. "
                                    "Acceptable, or set max_vias / layers?", net=net,
                                    measured=m["vias"]))
        for net, (n, at) in sorted(off_angle(tracks).items()):
            out.append(_finding(f"angle:{net}", "angle",
                                f"{net}: {n} piece{'s' if n > 1 else ''} off the 45 degree "
                                f"grid (first near {at}). Acceptable, or redraw it by hand?",
                                net=net, measured=n))
        for net, what in sorted((entries or {}).items()):
            out.append(_finding(f"pad_entry:{net}", "pad_entry",
                                f"{net}: {'; '.join(what)}. Acceptable, or redraw the entry by "
                                "hand (straight along the pad's axis, ending on its centre)?",
                                net=net))
    return out


#: Degrees off a multiple of 45 a piece of track may be.
ANGLE_TOL = 0.5


def off_angle(tracks: list[dict]) -> dict[str, tuple[int, tuple]]:
    """Per net, how many of its new pieces run off a multiple of 45
    degrees, and where the first is."""
    out: dict[str, tuple[int, tuple]] = {}
    for t in tracks:
        if math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"]) <= 1e-6:
            continue
        r = math.degrees(math.atan2(t["y2"] - t["y1"], t["x2"] - t["x1"])) % 45.0
        if min(r, 45.0 - r) > ANGLE_TOL:
            n, at = out.get(t["net_name"], (0, (round(t["x1"]), round(t["y1"]))))
            out[t["net_name"]] = (n + 1, at)
    return out


def unanswered(found: list[dict], accept: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """The findings ``accept`` leaves open, and the decisions it makes.

    Each answer gives a ``reason`` and names a finding by ``id``, or every
    finding of a ``check`` (pad_entry, detour...); ``evidence`` (a
    simulation's result file, a calculation) is kept with it. An answer
    with no reason answers nothing."""
    by_id: dict[str, dict] = {}
    by_check: dict[str, dict] = {}
    for a in accept or []:
        reason = str((a or {}).get("reason") or "").strip()
        if not reason:
            continue
        ans = {"reason": reason, **({"evidence": str(a["evidence"])} if a.get("evidence") else {})}
        if a.get("id"):
            by_id[str(a["id"])] = ans
        elif a.get("check"):
            by_check[str(a["check"])] = ans
    left, decided = [], []
    for f in found:
        ans = by_id.get(f["id"]) or by_check.get(f["check"])
        if ans:
            decided.append({"id": f["id"], "check": f["check"], "text": f["text"], **ans})
        else:
            left.append(f)
    return left, decided


def picture(before: LayoutBoard, tracks: list[dict], vias: list[dict], nets: Iterable[str],
            brief: Brief, unreached: list[dict], path: Path) -> Optional[str]:
    """A PNG of the round: the board as it was in grey, the round's copper
    by layer, its vias, the brief's areas, and every pad left unreached.
    None when matplotlib is not installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    nets = set(nets)
    # The view: the round's copper, its pads and what it left unreached.
    view_x = ([x for t in tracks for x in (t["x1"], t["x2"])]
              + [p.x for p in before.pads if p.net in nets])
    view_y = ([y for t in tracks for y in (t["y1"], t["y2"])]
              + [p.y for p in before.pads if p.net in nets])
    aspect = ((max(view_x) - min(view_x) + 1) / (max(view_y) - min(view_y) + 1)
              if view_x else 1.3)
    w = min(14.0, max(6.0, 7.0 * aspect ** 0.5))
    fig, ax = plt.subplots(figsize=(w, min(10.0, max(4.0, w / max(aspect, 0.3)))), dpi=110)
    if len(before.outline) >= 3:
        xs, ys = zip(*(list(before.outline) + [before.outline[0]]))
        ax.plot(xs, ys, color="#888", lw=1)
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Circle, Polygon as Patch
    from .. import geom

    # Everything at its true size: whether a track enters a pad straight
    # and central, and how close it runs to its neighbours, is judged here.
    def stroke(x1, y1, x2, y2, w):
        return Patch(geom.stroke_outline(x1, y1, x2, y2, w), closed=True)

    # Copper on the layers the round used darker than the rest, which it
    # may cross.
    used = {t["layer"] for t in tracks} or {c.layer for p in before.pads if p.net in nets
                                            for c in p.copper}
    for same in (False, True):
        ax.add_collection(PatchCollection(
            [stroke(t.x1, t.y1, t.x2, t.y2, t.width) for t in before.tracks
             if not t.keepout and (t.layer in used) == same],
            facecolor="#b4b4b4" if same else "#e8e8e8", lw=0))
    for mine in (False, True):
        pads = [Patch(geom.outline(s), closed=True) for p in before.pads
                if (p.net in nets) == mine and p.copper
                for s in [p.shape_on(p.copper[0].layer)] if s is not None]
        ax.add_collection(PatchCollection(pads, facecolor="#555" if mine else "#b8b8b8",
                                          lw=0, alpha=0.85))
    palette = ["#c0392b", "#2471a3", "#1e8449", "#b9770e", "#7d3c98", "#117a65"]
    layer_colour = {l: palette[i % len(palette)] for i, l in enumerate(before.copper_layers())}
    for layer, colour in layer_colour.items():
        mine = [stroke(t["x1"], t["y1"], t["x2"], t["y2"], t["width"])
                for t in tracks if t["layer"] == layer]
        if mine:
            ax.add_collection(PatchCollection(mine, facecolor=colour, lw=0, alpha=0.9))
    for v in vias:
        ax.add_patch(Circle((v["x"], v["y"]), v["size"] / 2, facecolor="white",
                            edgecolor="black", lw=0.8))
    for e in {id(brief.of[n]): brief.of[n] for n in nets if n in brief.of}.values():
        for ring, style in [(r, "--") for r in e.region or []] + [(r, ":") for r in e.keep_out]:
            xs, ys = zip(*(list(ring) + [ring[0]]))
            ax.plot(xs, ys, style, color="#7d3c98", lw=1)
    for u in unreached:
        if u["net"] in nets:
            ax.plot(u["x"], u["y"], "x", color="red", ms=10, mew=2)
            ax.annotate(u["net"], (u["x"], u["y"]), fontsize=7, color="red",
                        xytext=(4, 4), textcoords="offset points")
    handles = [plt.Line2D([], [], color=c, lw=2, label=l) for l, c in layer_colour.items()
               if any(t["layer"] == l for t in tracks)]
    if handles:
        ax.legend(handles=handles, loc="upper right", fontsize=7)
    if view_x:
        pad = max(60.0, 0.1 * max(max(view_x) - min(view_x), max(view_y) - min(view_y)))
        ax.set_xlim(min(view_x) - pad, max(view_x) + pad)
        ax.set_ylim(min(view_y) - pad, max(view_y) + pad)
    else:
        ax.autoscale_view()
    ax.set_aspect("equal")
    ax.set_title(f"Round: {len(nets)} nets, {len(tracks)} tracks, {len(vias)} vias (mil)", fontsize=9)
    ax.tick_params(labelsize=7)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)


# --- what to do about a net the round could not route ---------------------------------

#: The most nets a round tries options for, and trials per net.
ADVISE_NETS = 4
ADVISE_TRIALS = 6
#: The brief's rules a trial may drop, in the order they are named.
RELAXABLE = ("width", "max_vias", "keep_out", "region", "layers", "spacing")


def moved(board: LayoutBoard, ref: str, dx: float, dy: float) -> LayoutBoard:
    """A copy of ``board`` with part ``ref`` and everything of its footprint
    moved by (dx, dy)."""
    from ..bench import copy_board

    b = copy_board(board)
    for p in b.pads:
        if p.comp == ref:
            p.x += dx
            p.y += dy
    for t in b.tracks:
        if t.comp == ref:
            t.x1, t.x2, t.y1, t.y2 = t.x1 + dx, t.x2 + dx, t.y1 + dy, t.y2 + dy
    for a in b.arcs:
        if a.comp == ref:
            a.cx += dx
            a.cy += dy
    for v in b.vias:
        if v.comp == ref:
            v.x += dx
            v.y += dy
    for r in b.regions:
        if r.comp == ref:
            r.outline = [(x + dx, y + dy) for x, y in r.outline]
            r.holes = [[(x + dx, y + dy) for x, y in h] for h in r.holes]
    for c in b.components:
        if c.ref == ref:
            c.x += dx
            c.y += dy
            c.courtyard = [(x + dx, y + dy) for x, y in c.courtyard]
            for body in c.bodies:
                if body.get("outline"):
                    body["outline"] = [(x + dx, y + dy) for x, y in body["outline"]]
    return b


def _box(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _fits(board: LayoutBoard, ref: str) -> bool:
    """Whether part ``ref`` keeps out of the other parts on its side and
    inside the board outline (by boxes)."""
    me = next((c for c in board.components if c.ref == ref), None)
    if me is None or len(me.courtyard) < 3:
        return False
    x0, y0, x1, y1 = _box(me.courtyard)
    if len(board.outline) >= 3:
        bx0, by0, bx1, by1 = _box(board.outline)
        if x0 < bx0 or y0 < by0 or x1 > bx1 or y1 > by1:
            return False
    for c in board.components:
        if c.ref == ref or c.side != me.side or len(c.courtyard) < 3:
            continue
        a0, b0, a1, b1 = _box(c.courtyard)
        if x0 < a1 and a0 < x1 and y0 < b1 and b0 < y1:
            return False
    return True


def _trial(board: LayoutBoard, nets, brief: Brief, stages) -> tuple[bool, dict]:
    """Route ``nets`` alone on ``board`` (its copper fixed) and say whether
    every one of them is joined within the brief, with the copper it took."""
    from ..drc import connectivity
    from .brief import broken
    from .router import route_adaptive

    st = dict(stages) if isinstance(stages, dict) else {}
    st["join_existing"] = True
    r = route_adaptive(board, nets=frozenset(nets), brief=brief, stages=st)
    out = r.apply()
    groups = connectivity(out)
    new_t = out.tracks[len(board.tracks):]
    new_v = out.vias[len(board.vias):]
    bad = broken(brief, new_t, new_v)
    ok = all(len(groups.get(n, [])) <= 1 for n in nets) and not any(n in bad for n in nets)
    length = sum(math.hypot(t.x2 - t.x1, t.y2 - t.y1) for t in new_t if t.net in nets)
    return ok, {"length": round(length, 1), "vias": sum(v.net in nets for v in new_v),
                "layers": sorted({t.layer for t in new_t if t.net in nets})}


def _without_rule(brief: Brief, net: str, rule: str) -> Brief:
    """The brief with one rule of ``net``'s entry dropped, for ``net`` alone."""
    from dataclasses import replace

    empty = {"layers": None, "region": None, "keep_out": [], "max_vias": None,
             "width": None, "spacing": None}[rule]
    entries = []
    for e in brief.entries:
        if net in e.nets:
            rest = tuple(n for n in e.nets if n != net)
            if rest:
                entries.append(replace(e, nets=rest))
            entries.append(replace(e, nets=(net,), **{rule: empty}))
        else:
            entries.append(e)
    return brief.keeping(entries)


def _across(board: LayoutBoard, net: str, where: list[dict]) -> list[str]:
    """Nets whose copper (tracks, arcs, vias) crosses the straight line from
    each unreached pad of ``net`` to the nearest pad of its net it is apart
    from: what lies in the way, which a look round the pad alone misses."""
    from .. import geom
    from ..drc import conductive_items, connectivity

    items = conductive_items(board)
    groups = connectivity(board, items)
    pads = board.pads
    key_of = {p.key: i for i, p in enumerate(pads)}
    out: list[str] = []
    for u in where:
        i = key_of.get(u["pad"])
        if i is None:
            continue
        mine = next((g for g in groups.get(net, []) if i in g), {i})
        others = [j for g in groups.get(net, []) if g is not mine for j in g]
        if not others:
            continue
        j = min(others, key=lambda k: math.hypot(pads[k].x - pads[i].x, pads[k].y - pads[i].y))
        line = geom.capsule(pads[i].x, pads[i].y, pads[j].x, pads[j].y, 1.0)
        hits: dict[str, float] = {}
        for it in items:
            if it.kind == "pad" or not it.net or it.net == net:
                continue
            if geom.clearance(line, it.shape) <= 0:
                d = math.hypot(it.shape.bbox[0] - pads[i].x, it.shape.bbox[1] - pads[i].y)
                hits[it.net] = min(hits.get(it.net, math.inf), d)
        out += [n for n in sorted(hits, key=hits.get) if n not in out]
    return out[:4]


def advise(board: LayoutBoard, routed: LayoutBoard, unrouted: Iterable[str], brief: Brief,
           unreached: list[dict], stages=None, round_nets: Iterable[str] = (),
           seconds: float = 90.0) -> dict[str, dict]:
    """Options for each net the round left unrouted, each tried by routing it
    so: a part beside it moved clear, a net laid before this round ripped up,
    or one rule of the brief dropped. Every option says what its trial gave;
    none is applied. Options that route the net come first, then the least
    change.

    ``board`` is the board the round started from, ``routed`` the board
    with the round's copper on it.
    """
    import time

    from ..bench import copy_board

    t_end = time.monotonic() + seconds
    round_nets = set(round_nets)
    comps = {c.ref: c for c in board.components}
    laid = {t.net for t in board.tracks if t.net and not t.comp}
    out: dict[str, dict] = {}
    for net in sorted(unrouted)[:ADVISE_NETS]:
        where = [u for u in unreached if u["net"] == net]
        blockers: list[str] = []
        for u in where:
            for b in u.get("blocked_by", []):
                if b not in blockers and b != net:
                    blockers.append(b)
        for b in _across(routed, net, where):
            if b not in blockers:
                blockers.append(b)
        options: list[dict] = []
        tried = 0
        # Trials are planned first and run one of each kind before a second
        # of any: on a large board one trial takes a minute, and two rip-ups
        # once used the whole budget before the brief's own rules were tried.
        planned: dict[str, list] = {"move_part": [], "rip_up": [], "relax_brief": []}

        def attempt(kind, option, apply, b, nets, br):
            planned[kind].append((option, apply, b, nets, br))

        def run(kind, option, apply, b, nets, br):
            nonlocal tried
            if tried >= ADVISE_TRIALS or time.monotonic() > t_end:
                return None
            tried += 1
            try:
                ok, got = _trial(b() if callable(b) else b, nets, br, stages)
            except Exception as exc:  # noqa: BLE001 - a failed trial is an answer
                ok, got = False, {"error": str(exc)[:120]}
            res = (f"routes it: {got['length']} mil, {got['vias']} vias on "
                   f"{', '.join(got['layers']) or 'no new tracks'}" if ok else "does not route it")
            options.append({"kind": kind, "option": option, "routes": ok, "result": res,
                            "apply": apply, **({"trial": got} if ok else {})})
            return ok

        # A part beside an unreached pad, moved clear: not one of the net's
        # own, not locked, and with no routed copper of its own to break.
        near = []
        for u in where:
            for c in board.components:
                if c.locked or len(c.courtyard) < 3:
                    continue
                own = {p.net for p in board.pads if p.comp == c.ref and p.net}
                if net in own or own & laid:
                    continue
                x0, y0, x1, y1 = _box(c.courtyard)
                d = math.hypot(max(x0 - u["x"], 0, u["x"] - x1), max(y0 - u["y"], 0, u["y"] - y1))
                near.append((d, c.ref, u))
        seen: set[str] = set()
        for d, ref, u in sorted(near, key=lambda t: t[0]):
            if ref in seen or len(seen) >= 2:
                continue
            seen.add(ref)
            x0, y0, x1, y1 = _box(comps[ref].courtyard)
            step = max(x1 - x0, y1 - y0) * 0.5 + 10.0
            away = ((x0 + x1) / 2 - u["x"], (y0 + y1) / 2 - u["y"])
            norm = math.hypot(*away) or 1.0
            dx, dy = round(away[0] / norm * step, 1), round(away[1] / norm * step, 1)
            b = moved(routed, ref, dx, dy)
            if not _fits(b, ref):
                continue
            own = sorted({p.net for p in b.pads if p.comp == ref and p.net})
            attempt("move_part", f"move {ref} by ({dx:g}, {dy:g}) mil",
                    {"tool": "pcb_move_components",
                     "moves": [{"designator": ref, "x": round(comps[ref].x + dx, 1),
                                "y": round(comps[ref].y + dy, 1)}]},
                    b, [net] + own, brief)
        # A net laid before this round that is in the way, ripped up and
        # routed again with it.
        def ripped(other):
            def build():
                b = copy_board(routed)
                b.tracks = [t for t in b.tracks if t.net != other or t.comp or t.keepout]
                b.arcs = [a for a in b.arcs if a.net != other or a.comp]
                b.vias = [v for v in b.vias if v.net != other or v.comp]
                return b
            return build

        for other in blockers[:2]:
            if other in round_nets:
                continue
            attempt("rip_up", f"rip up {other} (laid before this round) and route both",
                    {"tool": "pcb_unroute", "nets": [other]}, ripped(other), [net, other], brief)

        def relax_brief():
            # The AI's own rules: offered, never applied, and listed last.
            # All of them dropped first says whether the brief is what stops
            # the net; only then is each tried alone, to name the one.
            e = brief.of.get(net)
            if e is None:
                return
            ruled = [r for r in RELAXABLE if getattr(e, r) not in (None, [], ())]
            if not ruled:
                return
            free = brief
            for r in ruled:
                free = _without_rule(free, net, r)
            whole = (f"drop all of the brief's rules for {net} ({', '.join(ruled)}): "
                     "whether it can be routed at all") if len(ruled) > 1 else \
                f"drop the brief's {ruled[0]} for {net}"
            ok = run("relax_brief", whole,
                     {"tool": "pcb_route_brief", "drop": [{"nets": [net], "rules": ruled}]},
                     routed, [net], free)
            if ok and len(ruled) > 1:
                for r in ruled:
                    run("relax_brief", f"drop the brief's {r} for {net}",
                        {"tool": "pcb_route_brief", "drop": [{"nets": [net], "rules": [r]}]},
                        routed, [net], _without_rule(brief, net, r))

        # One of each kind before a second of any: a move, a rip-up, then
        # whether the brief itself is what stops it; then the other moves
        # and rip-ups.
        firsts = [(k, planned[k].pop(0)) for k in ("move_part", "rip_up") if planned[k]]
        for kind, args in firsts:
            run(kind, *args)
        relax_brief()
        for i in range(max(len(planned["move_part"]), len(planned["rip_up"]))):
            for kind in ("move_part", "rip_up"):
                if i < len(planned[kind]):
                    run(kind, *planned[kind][i])
        rank = {"move_part": 0, "rip_up": 1, "relax_brief": 2}
        options.sort(key=lambda o: (not o["routes"], rank[o["kind"]]))
        out[net] = {"blocked_by": blockers, "pads": [u["pad"] for u in where],
                    "options": options, "tried": tried}
    return out


__all__ = ["ROUND_ORDER", "open_nets", "rounds", "net_metrics", "questions", "picture",
           "moved", "advise"]
