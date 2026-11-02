# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fanout: the vias a board's pads get before any signal is routed.

BGA. A person routing a BGA does not route ball by ball. The outer rings
leave on the BGA's own layer, as many rings as there is room for tracks
between the balls; every ball further in gets a via first, all in one
pattern, and the routing starts from those vias on the inner layers. Left
to find its own way, the router fought over the space between the balls
for every net at once, and most of the conflicts that never settled were
round the BGA.

A via goes between four balls (a dog-bone, with a short track to it) when
one fits there, else in the ball at its exact centre, the largest style
that clears everything either way. Dog-bones point away from the middle
of the part, so each ball has its own diagonal and no two share one.

Planes and pours (``plane_fanout``). Every surface pad of a net that a
plane or a pour on another layer joins gets its via before the signals
are routed, as a person drops a via beside each ground pad first: a
dog-bone, a short track out along the pad's long axis to a via just past
its end, in another direction where that is blocked. A via never goes
into the pad itself: in a passive's pad it wicks the solder from the
joint, and an IC's pin is no better. The exceptions are an IC's exposed
pad, which takes an array of vias for its heat, and a fine-pitch BGA's
balls where no dog-bone fits; each such via is listed for the fab notes
(filled and capped). An IC's pins of the exposed pad's own net are
joined to it by a short track inward, as the land pattern intends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..drc import pads_on_a_barrel
from ..model import LayoutBoard, Track, Via
from .grid import inner_depth

EPS = 1e-6


@dataclass
class BGA:
    comp: str
    layer: str
    pitch: float
    ball: float                    # ball pad diameter
    pads: list[int]                # indices into board.pads
    ij: dict[int, tuple[int, int]] = field(default_factory=dict)
    ring: dict[int, int] = field(default_factory=dict)
    center: tuple[float, float] = (0.0, 0.0)


def find_bgas(board: LayoutBoard, min_balls: int = 16) -> list[BGA]:
    """Parts whose surface pads are round and sit on a square lattice."""
    by_comp: dict[tuple[str, str], list[int]] = {}
    for i, p in enumerate(board.pads):
        if not p.comp or not p.is_smd or len(p.copper) != 1:
            continue
        c = p.copper[0]
        if c.shape not in ("round", "roundrect") or abs(c.w - c.h) > 0.01 * max(c.w, 1):
            continue
        by_comp.setdefault((p.comp, c.layer), []).append(i)
    out = []
    for (comp, layer), idx in by_comp.items():
        if len(idx) < min_balls:
            continue
        xs = np.array([board.pads[i].x for i in idx])
        ys = np.array([board.pads[i].y for i in idx])
        rot = board.pads[idx[0]].rotation
        if abs((rot % 90.0)) > 0.01 and abs((rot % 90.0) - 90.0) > 0.01:
            continue    # a lattice at an angle: not handled yet
        ux = np.unique(np.round(xs, 2))
        uy = np.unique(np.round(ys, 2))
        steps = np.concatenate([np.diff(ux), np.diff(uy)])
        steps = steps[steps > 0.5]
        if not len(steps):
            continue
        pitch = float(np.min(steps))
        gi = (xs - xs.min()) / pitch
        gj = (ys - ys.min()) / pitch
        if np.max(np.abs(gi - np.round(gi))) > 0.05 or np.max(np.abs(gj - np.round(gj))) > 0.05:
            continue
        gi, gj = np.round(gi).astype(int), np.round(gj).astype(int)
        nx, ny = gi.max() + 1, gj.max() + 1
        # The step between rounded positions is only as good as the
        # rounding; the span over the lattice is exact.
        pitch = float((xs.max() - xs.min()) / (nx - 1)) if nx > 1 else pitch
        if nx < 4 or ny < 4:
            continue
        ball = float(np.median([board.pads[i].copper[0].w for i in idx]))
        bga = BGA(comp, layer, pitch, ball, list(idx),
                  center=(float(xs.mean()), float(ys.mean())))
        for k, i in enumerate(idx):
            a, b = int(gi[k]), int(gj[k])
            bga.ij[i] = (a, b)
            bga.ring[i] = min(a, b, nx - 1 - a, ny - 1 - b)
        out.append(bga)
    return out


@dataclass
class FanoutPlan:
    vias: list[Via] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    by_pad: dict[int, Via] = field(default_factory=dict)   # pad index -> its via


def plan_fanout(router, bgas: list[BGA]) -> FanoutPlan:
    """A via for each ball of a routed net that its layer cannot free.

    ``router`` supplies the grid's clearance fields, the rules and each
    net's via styles; it must be built on the board WITHOUT the fanout.
    """
    plan = FanoutPlan()
    board = router.board
    pads = board.pads
    by_net = {j.name: j for j in router.jobs}
    placed: list[tuple[float, float, float, str]] = []   # x, y, radius, net
    for bga in bgas:
        width = router.w_def
        c = router.c
        gap = bga.pitch - bga.ball
        between = max(0, int(math.floor((gap - c + EPS) / (width + c))))
        for i in bga.pads:
            p = pads[i]
            job = by_net.get(p.net)
            if job is None or bga.ring[i] <= between:
                continue
            via = _choose(router, job, bga, p, placed)
            if via is None:
                continue
            x, y, style, stub = via
            v = Via(x, y, style.diameter, style.hole, board.copper_layers()[0],
                    board.copper_layers()[-1], p.net)
            plan.vias.append(v)
            plan.by_pad[i] = v
            placed.append((x, y, style.diameter / 2, p.net))
            if stub:
                plan.tracks.append(Track(bga.layer, p.x, p.y, x, y, job.width, p.net))
    return plan


def _choose(router, job, bga: BGA, pad, placed):
    """Dog-bone if one fits, else in the pad; the largest style either way."""
    cx, cy = bga.center
    sx = 1.0 if pad.x >= cx else -1.0
    sy = 1.0 if pad.y >= cy else -1.0
    h = bga.pitch / 2.0
    spots = [(pad.x + sx * h, pad.y + sy * h, True), (pad.x, pad.y, False)]
    for x, y, stub in spots:
        room = _room(router, job, x, y)
        for style in job.vias:
            r = style.diameter / 2
            if room < r - EPS:
                continue
            if stub and room < r + 0.0:
                continue
            if any(math.hypot(x - px, y - py) < r + pr + router.c - EPS
                   for px, py, pr, net in placed if net != job.name):
                continue
            if router.via_leaves_web(style, x, y):
                continue
            if not stub or _stub_clear(router, job, pad, x, y):
                return x, y, style, stub
    return None


def _room(router, job, x: float, y: float) -> float:
    """Room round a point for this net's copper on every routing layer:
    the nearest cell's room less the distance to it."""
    spec = router.grid.spec
    i, j = spec.cell(x, y)
    if not (0 <= i < spec.nx and 0 <= j < spec.ny):
        return -1.0
    off = math.hypot(float(spec.x(i)) - x, float(spec.y(j)) - y)
    row = router.rr.clearance_row(job.id)
    at = (slice(j, j + 1), slice(i, i + 1))
    return min(float(router._slack(job.id, l, at, row)[0, 0]) for l in range(router.L)) - off


def _stub_clear(router, job, pad, x: float, y: float) -> bool:
    """The short track from the ball to a dog-bone via clears foreign
    copper along its length, sampled at the grid's pitch."""
    n = max(2, int(math.ceil(math.hypot(x - pad.x, y - pad.y) / (router.pitch / 2))))
    li = router.grid.layer_index.get(pad.copper[0].layer)
    if li is None:
        return False
    spec = router.grid.spec
    row = router.rr.clearance_row(job.id)
    for k in range(n + 1):
        px = pad.x + (x - pad.x) * k / n
        py = pad.y + (y - pad.y) * k / n
        i, j = spec.cell(px, py)
        off = math.hypot(float(spec.x(i)) - px, float(spec.y(j)) - py)
        at = (slice(j, j + 1), slice(i, i + 1))
        if float(router._slack(job.id, li, at, row)[0, 0]) - off < job.width / 2 - EPS:
            return False
    return True


# ---------------------------------------------------------------------------
# Planes and pours
# ---------------------------------------------------------------------------

def _area(pad) -> float:
    c = pad.copper[0] if pad.copper else None
    return c.w * c.h if c else 0.0


def find_exposed_pads(board: LayoutBoard) -> list[int]:
    """An IC's exposed (thermal) pad: a surface pad of a part with at least
    four other surface pads, four times the area of the part's middle
    pad or more, lying inside the box of the others."""
    by_comp: dict[str, list[int]] = {}
    for i, p in enumerate(board.pads):
        if p.comp and p.is_smd and p.copper:
            by_comp.setdefault(p.comp, []).append(i)
    out = []
    for idx in by_comp.values():
        if len(idx) < 5:
            continue
        areas = {i: _area(board.pads[i]) for i in idx}
        mid = float(np.median(list(areas.values())))
        for i in idx:
            if mid <= 0 or areas[i] < 4.0 * mid:
                continue
            p = board.pads[i]
            xs = [board.pads[j].x for j in idx if j != i]
            ys = [board.pads[j].y for j in idx if j != i]
            if min(xs) < p.x < max(xs) and min(ys) < p.y < max(ys):
                out.append(i)
    return out


def _long_axis(pad) -> tuple[float, float] | None:
    """The unit direction of the pad's longer side; None for a pad as
    wide as it is long."""
    c = pad.copper[0]
    if abs(c.w - c.h) <= 0.01 * max(c.w, c.h, 1e-9):
        return None
    a = math.radians(pad.rotation + (0.0 if c.w > c.h else 90.0))
    # cos(90 degrees) is 6e-17, not 0: left so, a pad in line with its
    # part's centre faced one way or the other by that crumb's sign.
    return (round(math.cos(a), 12), round(math.sin(a), 12))


def snap8(dx: float, dy: float) -> tuple[float, float]:
    """The nearest of the eight directions to (dx, dy), as a unit vector."""
    a = math.atan2(dy, dx)
    a = round(a / (math.pi / 4)) * (math.pi / 4)
    return (math.cos(a), math.sin(a))


def _rot(u, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return (u[0] * math.cos(a) - u[1] * math.sin(a), u[0] * math.sin(a) + u[1] * math.cos(a))


def outward(pad, centre) -> tuple[float, float]:
    """Away from the part: along the pad's long axis, on the side away from
    the part's centre; for a square or round pad, from the centre to the
    pad, to the nearest of the eight directions."""
    dx, dy = pad.x - centre[0], pad.y - centre[1]
    u = _long_axis(pad)
    if u is None:
        if math.hypot(dx, dy) < 1e-6:
            return (1.0, 0.0)
        return snap8(dx, dy)
    if u[0] * dx + u[1] * dy < -1e-9:
        u = (-u[0], -u[1])
    return u


def _extent(shape, c, d) -> float:
    """How far the shape reaches from ``c`` along the unit direction ``d``."""
    return max((px - c[0]) * d[0] + (py - c[1]) * d[1] for px, py in shape.pts) + shape.r


def plane_fanout(ctx) -> None:
    """A via for every surface pad of a net a plane or a pour on another
    layer joins, laid as fixed copper before the signals are routed:
    exposed pads first (an array of vias, and the IC's pins of that net
    joined to it), then a dog-bone from each other pad, IC pins before
    passives since a pin has less room round it."""
    r = ctx.r
    board = r.board
    rep = r.stage_report.fanout
    pads_of = board.pads_by_net()
    index = {id(p): i for i, p in enumerate(board.pads)}
    for e in find_exposed_pads(board):
        net = board.pads[e].net
        if not net or not ctx.routed(net):
            continue
        if ctx.joined_elsewhere(net) and _via_array(ctx, e):
            rep["exposed_pad_arrays"] = rep.get("exposed_pad_arrays", 0) + 1
        for q in ctx.comp_pads.get(board.pads[e].comp, []):
            if q != e and board.pads[q].net == net and q not in r.laid_joined \
                    and board.pads[q].is_smd and _stub_to(ctx, q, e):
                rep["exposed_pad_stubs"] = rep.get("exposed_pad_stubs", 0) + 1
    todo = []
    # Pads the board's own copper already takes to a via of their net.
    on_barrel = getattr(r, "pad_barrels", None)
    if on_barrel is None:
        on_barrel = pads_on_a_barrel(board)
    for net, pads in pads_of.items():
        if not ctx.routed(net) or not ctx.joined_elsewhere(net):
            continue
        for p in pads:
            i = index[id(p)]
            if (not p.is_smd or not p.copper or i in r.via_pads or i in r.laid_via
                    or i in r.laid_joined):
                continue
            if i in on_barrel:
                rep["already_joined"] = rep.get("already_joined", 0) + 1
                continue
            if net not in ctx.planes and ctx.in_own_pour(net, p):
                # A pad inside its net's own pour on its own layer is joined
                # by the pour; a via there only takes room on both sides.
                continue
            todo.append((ctx.is_passive(i), i))
    todo.sort()
    for _, i in todo:
        if _dogbone(ctx, i):
            rep["dogbones"] = rep.get("dogbones", 0) + 1
        else:
            rep["no_room"] = rep.get("no_room", 0) + 1


def _dogbone(ctx, i: int) -> bool:
    """A short track from the pad's centre out along its long axis to a via
    just past its end; across the axis, then at 45 degrees outward, where
    that is blocked. The via clears the pad by a web, so it is never in
    it; past an IC's fine-pitch pin it goes beyond the pin's access lane,
    out of the way of its neighbours'."""
    r = ctx.r
    board = r.board
    pad = board.pads[i]
    layer = pad.copper[0].layer
    li = r.grid.layer_index.get(layer)
    if li is None:
        return False
    net = pad.net
    nid = r.grid.net_id[net]
    sh = pad.shape_on(layer)
    c = (pad.x, pad.y)
    centre = ctx.comp_centre(pad.comp)
    span = math.hypot(pad.x - centre[0], pad.y - centre[1])
    if ctx.is_passive(i) and span > EPS:
        # A passive's pad is often longer across the part than along it;
        # out of the part means away from its other pad.
        out = ((pad.x - centre[0]) / span, (pad.y - centre[1]) / span)
    else:
        out = outward(pad, centre)
    perp = (-out[1], out[0])
    dirs = [out, perp, (-perp[0], -perp[1]), _rot(out, 45.0), _rot(out, -45.0)]
    width = min(ctx.width(net), min(pad.copper[0].w, pad.copper[0].h))
    lane = ctx.lane_depth(i)
    # Every place a via could go, cheapest first: a short stub, the
    # preferred style, the axis before the other directions, and a via
    # that still leaves a track's room to the copper round it. A via
    # pressed against its neighbours closes the channel a signal needed.
    row = r.rr.clearance_row(nid)
    channel = r.w_def + 2 * r.c
    cands = []
    for k, d in enumerate(dirs):
        edge = _extent(sh, c, d)
        for s, v in enumerate(ctx.vias(net)):
            rv = v.diameter / 2
            base = edge + max(ctx.web + rv, r.pad_web(v, i)) + (lane if k == 0 else 0.0)
            for step in range(3):
                dist = base + step * r.pitch
                x, y = c[0] + dist * d[0], c[1] + dist * d[1]
                room = _grid_room(r, nid, row, x, y)
                if room < rv - EPS:
                    continue
                cost = dist + (k > 0) * r.pitch + s * 2 * r.pitch \
                    + (room < rv + channel) * 4 * r.pitch
                cands.append((cost, k, s, step, x, y, v))
    cands.sort(key=lambda t: t[:4])
    for _, _, _, _, x, y, v in cands:
        if not ctx.joins(net, x, y, layer):
            continue
        via = Via(x, y, v.diameter, v.hole, ctx.top, ctx.bottom, net)
        if not ctx.via_fits(via, nid, i):
            continue
        stub = Track(layer, c[0], c[1], x, y, width, net)
        if not ctx.clears(nid, stub.shape(), li):
            continue
        ctx.lay([via], [stub], tag=("dogbone", i))
        r.laid_via[i] = via
        return True
    return False


def _grid_room(r, nid: int, row, x: float, y: float) -> float:
    """Room round a point for the net's copper on every routing layer, from
    the grid: the nearest cell's room less the distance to it."""
    spec = r.grid.spec
    i, j = spec.cell(x, y)
    if not (0 <= i < spec.nx and 0 <= j < spec.ny):
        return -1.0
    off = math.hypot(float(spec.x(i)) - x, float(spec.y(j)) - y)
    at = (slice(j, j + 1), slice(i, i + 1))
    return min(float(r._slack(nid, l, at, row)[0, 0]) for l in range(r.L)) - off


def _via_array(ctx, e: int) -> bool:
    """Vias inside an exposed pad, on a lattice at twice the via's size,
    each wholly inside the pad and clear of everything foreign on the
    other layers. Its heat goes down them, and its connection to the
    plane or pour with it."""
    r = ctx.r
    board = r.board
    pad = board.pads[e]
    layer = pad.copper[0].layer
    net = pad.net
    nid = r.grid.net_id[net]
    sh = pad.shape_on(layer)
    v = ctx.vias(net)[0]
    rv = v.diameter / 2
    pitch = max(2.0 * v.diameter, v.diameter + 2.0 * r.c)
    margin = 2.0                        # inside the pad's edge
    x0, y0, x1, y1 = sh.bbox
    nx = max(1, int((x1 - x0 - 2 * (rv + margin)) // pitch) + 1)
    ny = max(1, int((y1 - y0 - 2 * (rv + margin)) // pitch) + 1)
    placed = []
    for a in range(nx):
        for b in range(ny):
            x = pad.x + (a - (nx - 1) / 2) * pitch
            y = pad.y + (b - (ny - 1) / 2) * pitch
            if float(inner_depth(sh, np.array([[x]]), np.array([[y]]))[0, 0]) < rv + margin:
                continue
            via = Via(x, y, v.diameter, v.hole, ctx.top, ctx.bottom, net)
            if not ctx.via_fits(via, nid, e, in_pad=True):
                continue
            ctx.lay([via], [], tag=("array", e))
            placed.append(via)
    if not placed:
        return False
    joined = [p for p in placed if ctx.joins(net, p.x, p.y, layer)]
    r.laid_via[e] = joined[0] if joined else placed[0]
    return True


def _stub_to(ctx, q: int, e: int) -> bool:
    """A track from an IC pin's centre inward along its axis onto the
    exposed pad of its own net, if it clears the pins beside it."""
    r = ctx.r
    board = r.board
    pin, ep = board.pads[q], board.pads[e]
    if not pin.copper:
        return False
    layer = pin.copper[0].layer
    if layer not in ep.layers():
        return False
    li = r.grid.layer_index.get(layer)
    if li is None:
        return False
    u = _long_axis(pin)
    dx, dy = ep.x - pin.x, ep.y - pin.y
    if u is None:
        u = snap8(dx, dy)
    elif u[0] * dx + u[1] * dy < 0:
        u = (-u[0], -u[1])
    esh = ep.shape_on(layer)
    width = min(ctx.width(pin.net), min(pin.copper[0].w, pin.copper[0].h))
    reach = 2.0 * max(pin.copper[0].w, pin.copper[0].h) + r.c
    step = r.pitch / 4
    d = 0.0
    while d <= reach:
        x, y = pin.x + d * u[0], pin.y + d * u[1]
        if float(inner_depth(esh, np.array([[x]]), np.array([[y]]))[0, 0]) >= width / 2:
            t = Track(layer, pin.x, pin.y, x, y, width, pin.net)
            if not ctx.clears(r.grid.net_id[pin.net], t.shape(), li):
                return False
            ctx.lay([], [t], tag=("epstub", q))
            r.laid_joined.add(q)
            return True
        d += step
    return False
