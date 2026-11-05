# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Routing in planned stages, critical things first.

A layout engineer does not let a search find whatever connects the dots.
The routing is planned in an order, and the copper of each stage is fixed
for the ones after it:

1. differential pairs, coupled at their gap, skew evened out with bumps
   (``diffpair``);
2. the vias of every pad a plane or pour joins: dog-bones, an exposed
   pad's via array (``fanout.plane_fanout``);
3. an access lane straight out of every fine-pitch IC pin, reserved for
   that pin's own net (``reserve_pin_lanes``);
4. buses, three or more nets between the same two parts, planned as one
   group of nested lanes (``lanes``);
5. everything else, negotiated, high-current nets first, then the rails,
   then the rest (the router's own order, ``Router._order``);
6. what still cannot be reached is reported pad by pad with what blocks
   it (``unreached_pads``), not looped over.

Every stage lays its copper through ``Router.lay`` (board and grid alike)
and checks it exactly against everything already there; whatever a stage
cannot do cleanly it leaves to the negotiated router.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field, fields

import numpy as np

from .. import geom
from .exact import CopperIndex, sharp_bends
from .fanout import _extent, outward

EPS = 1e-6


@dataclass
class StageOptions:
    """Which stages run, and their sizes, in mils.

    The defaults are the set measured best over 160 finished boards with
    the person's placement: 98.93% mean against 98.85% for the router
    before the stages, two boards fewer at 100%, all DRC-clean, the same
    vias, about 29% more time, and bends sharper than 45 degrees down from
    105 to 9. Plane fanout cost 8 boards at 100% and a via ban on IC pins
    cost 5, so both are off unless asked for. The switches B3 added
    (2026-10-07) are on where the benchmark measured them no worse and
    off, waiting, where it did not: a repair that keeps its clean reroute
    cost two boards a connection each and took longer on them; with the
    repair that negotiates among the blockers as well, one board more was
    fully routed for 27% more board time; and stopping a stalled
    negotiation (stall_stop) left 11 boards worse and 4 better. The exact
    room for a via in a pad (exact_pad_via) is off too (see below).
    """

    diff_pairs: bool = True
    plane_fanout: bool = False
    pin_lanes: bool = True
    bus_lanes: bool = True
    # Route high-current nets, then rails, then the rest, in every round.
    class_order: bool = True
    # Stop negotiating when it stops gaining even with no plane to give
    # back, and report what is left.
    stall_stop: bool = False
    # Where a via may go into a pad: "bga" (a BGA's balls and an exposed
    # pad only), "ic" (also an IC's pins, never a passive's) or "any".
    # "bga" by default: a via in a pin wicks the solder unless it is
    # filled and capped, and a demo board under "ic" came out with 90 of
    # them, a USB connector's pins among them. The fallback (route_adaptive)
    # still widens it to "ic" when that alone leaves connections unmade,
    # and says so in stage_report.fallback.
    pad_vias: str = "bga"
    # Intra-pair skew left after bumps.
    pair_skew: float = 5.0
    # Pair width and gap when the board has no differential pair rule.
    pair_width: float | None = None
    pair_gap: float | None = None
    # How far out of a fine-pitch pin its lane reaches, and the pitch at
    # or under which a pin is fine (0.8 mm and 0.65 mm).
    lane_depth: float = 31.5
    fine_pitch: float = 25.6
    # What crossing another net's lane costs, per cell, as a multiple of
    # an open cell; inf makes every lane a wall.
    lane_cost: float = 8.0
    # Nets between the same two parts that make a bus.
    bus_min: int = 3
    # When the stages leave connections unmade, route once more without
    # the ones that only constrain, and keep the better (route_adaptive).
    fallback: bool = True
    # Cut every corner the router lays to bends of 45 degrees or less.
    bends: bool = True
    # Lay each run between its bends as one straight piece, or a straight
    # and a 45 degree piece, not the grid's staircase of cell steps.
    straight: bool = True
    # Stop the global routing's negotiation once its overflow stops
    # falling (GlobalRouter.STALL_*), not after a fixed number of rounds.
    # MEASURED on the 160 (2026-10-07): the same completion and boards
    # fully routed, 0.4% less copper, 14% less routing time.
    global_stall: bool = True
    # Keep a repair's reroute that crosses no other net and joins more,
    # instead of putting the net back for want of anything to rip up.
    # Off: measured worse (see above).
    repair_clean: bool = False
    # When ripping up what blocks a failing net and routing each again in
    # turn does not join more, negotiate them among themselves. Off:
    # measured slower (see above).
    repair_negotiate: bool = False
    # Judge the room for a via at a pad's centre from the exact shapes
    # round it, not from the nearest cell less the distance to it. Off:
    # MEASURED on the 160, two boards more fully routed (inside the noise
    # band) and 0.6% more vias; with it on, RT_SUPER_C1 lost three
    # connections.
    exact_pad_via: bool = False
    # An attempt that stalls with a plane to give back goes straight to the
    # next attempt, its routes unsettled (Router.run). MEASURED: the same
    # completion on all 160; on the public boards bunny_brain_D 99.57% in
    # 920 s became 100% in 378 s.
    quick_release: bool = True
    # Reach a surface pad only along one of its axes, from cells outside it
    # far enough for the track to clear the pad before it turns: straight
    # out of the centre, never slantwise across the pad's copper or its
    # corner. An IC's elongated pin is left only through its short ends.
    axis_entry: bool = False
    # Route a net at its rule's width even where one of its pads is
    # narrower, necking down to that pad's width only on the stub into it.
    # Off: a net is routed at what its narrowest pad takes, all the way.
    neck_pads: bool = False
    # Route only what the board's own copper leaves apart: a net its
    # tracks already join whole is not routed, and one partly joined is
    # routed by joining its pieces, each reachable anywhere along its
    # copper. Off: every net's pads are joined afresh.
    join_existing: bool = False
    # Copper layers routed on by no net, by name (an inner layer kept clear
    # for a pour or a sensitive signal). A layer a net's surface pad is on
    # is refused: its pads could not be reached.
    exclude_layers: tuple = ()
    # A through via of a net already on the board is a free layer change
    # for the net's routes, where no second via is laid.
    reuse_vias: bool = False

    def any(self) -> bool:
        return self.diff_pairs or self.plane_fanout or self.pin_lanes or self.bus_lanes

    def legacy(self) -> bool:
        """Nothing the stages brought is asked for: the router exactly as
        it was before them, corners, fanout vias and warm start included."""
        return not (self.any() or self.class_order or self.stall_stop or self.bends
                    or self.straight or self.global_stall or self.repair_clean
                    or self.repair_negotiate or self.quick_release or self.exact_pad_via
                    or self.axis_entry or self.neck_pads or self.join_existing
                    or bool(self.exclude_layers) or self.reuse_vias
                    or self.pad_vias != "any")

    @classmethod
    def coerce(cls, value) -> "StageOptions":
        """None for the defaults, False for the router exactly as it was
        before the stages (``legacy``), a dict of fields, or options."""
        if isinstance(value, cls):
            return value
        if value is None or value is True:
            return cls()
        if value is False:
            return cls(diff_pairs=False, plane_fanout=False, pin_lanes=False, bus_lanes=False,
                       class_order=False, pad_vias="any", fallback=False, bends=False,
                       straight=False, global_stall=False, exact_pad_via=False,
                       quick_release=False)
        known = {f.name for f in fields(cls)}
        bad = set(value) - known
        if bad:
            raise ValueError(f"unknown stage options: {', '.join(sorted(bad))}")
        return cls(**dict(value))


@dataclass
class StageReport:
    via_in_pad: list = field(default_factory=list)
    pairs: list = field(default_factory=list)
    buses: list = field(default_factory=list)
    unreached: list = field(default_factory=list)
    fanout: dict = field(default_factory=dict)
    pin_lanes: int = 0
    sharp_bends: int = 0
    seconds: dict = field(default_factory=dict)
    # Set when the board was routed again without the constraining stages
    # because that joined more (router._fallback): what was dropped, why.
    fallback: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class StageContext:
    """What the stages share: the router, an exact index of the copper
    laid so far, and what each pad and net is."""

    def __init__(self, router):
        from ..bench import copy_board

        r = self.r = router
        # The stages lay copper on the router's own copy of the board; the
        # copy is made first, so the pads looked up here are its pads.
        if not router._own_board:
            router.board = copy_board(router.board)
            router._own_board = True
        b = router.board
        self.index = CopperIndex.from_grid(router.grid)
        self.laid_any = False
        self.objects: dict = {}           # tag -> (vias, tracks, grid items)
        # Tags of copper a stage may take up and lay again elsewhere (a
        # bus may move dog-bones out of its way).
        self.soft_tags: frozenset | tuple = ()
        self.planes = router._plane_masks()
        cu = b.copper_layers()
        self.top, self.bottom = cu[0], cu[-1]
        routing = set(router.grid.layers)
        self.pours: dict[str, list] = {}
        for reg in b.regions:
            if reg.kind == "pour_boundary" and reg.net and reg.layer in routing \
                    and len(reg.outline) >= 3:
                self.pours.setdefault(reg.net, []).append((reg.layer, geom.polygon(reg.outline)))
        self.comp_pads: dict[str, list[int]] = {}
        for i, p in enumerate(b.pads):
            if p.comp:
                self.comp_pads.setdefault(p.comp, []).append(i)
        self._centre: dict[str, tuple] = {}
        self.pads_of = b.pads_by_net()
        self.web = max(router.c, 4.0)
        self.outline = b.outline_shape() if len(b.outline) >= 3 else None
        self.own_vias: dict[int, list] = {}
        for v in b.vias:
            self.own_vias.setdefault(router.grid.owner(v.net), []).append((v.x, v.y, v.diameter / 2))
        self.fine: dict[int, float] = self._fine_pins()

    # -- facts ---------------------------------------------------------------

    def routed(self, net: str) -> bool:
        r = self.r
        return bool(net) and (r.only is None or net in r.only) and net not in r.laid_nets

    def joined_elsewhere(self, net: str) -> bool:
        return net in self.planes or net in self.pours

    def joins(self, net: str, x: float, y: float, layer: str) -> bool:
        """Whether a via of ``net`` at (x, y) meets its plane, or a pour of
        its net on another layer than ``layer``."""
        m = self.planes.get(net)
        if m is not None:
            ci, cj = self.r.grid.spec.cell(x, y)
            if 0 <= cj < m.shape[0] and 0 <= ci < m.shape[1] and m[cj, ci]:
                return True
        for lay, shape in self.pours.get(net, ()):
            if lay != layer and geom.point_in_poly(x, y, shape):
                return True
        return False

    def in_own_pour(self, net: str, pad) -> bool:
        """Whether a pour of the pad's own net on its own layer covers it."""
        layers = set(pad.layers())
        return any(lay in layers and geom.point_in_poly(pad.x, pad.y, shape)
                   for lay, shape in self.pours.get(net, ()))

    def comp_centre(self, comp: str):
        if comp not in self._centre:
            ps = [self.r.board.pads[i] for i in self.comp_pads.get(comp, [])]
            self._centre[comp] = ((sum(p.x for p in ps) / len(ps), sum(p.y for p in ps) / len(ps))
                                  if ps else (0.0, 0.0))
        return self._centre[comp]

    def is_passive(self, i: int) -> bool:
        p = self.r.board.pads[i]
        return bool(p.comp) and len(self.comp_pads.get(p.comp, [])) == 2

    def width(self, net: str) -> float:
        return self.r.rr.width(net, self.pads_of.get(net, []))

    def vias(self, net: str):
        return self.r.rr.vias(net)

    def lane_depth(self, i: int) -> float:
        return self.r.options.lane_depth if (i in self.fine and self.r.options.pin_lanes) else 0.0

    def _fine_pins(self) -> dict[int, float]:
        """Surface pins of an IC (six pads or more) that are longer than
        wide and sit at the fine pitch or closer to the next pin: pin ->
        that pitch."""
        b = self.r.board
        limit = self.r.options.fine_pitch
        out = {}
        for comp, idx in self.comp_pads.items():
            pads = [i for i in idx if b.pads[i].is_smd and b.pads[i].copper]
            if len(pads) < 6:
                continue
            xy = np.array([(b.pads[i].x, b.pads[i].y) for i in pads])
            for k, i in enumerate(pads):
                c = b.pads[i].copper[0]
                if max(c.w, c.h) < 1.3 * min(c.w, c.h):
                    continue
                d = np.hypot(xy[:, 0] - xy[k, 0], xy[:, 1] - xy[k, 1])
                d[k] = np.inf
                if d.min() <= limit + EPS:
                    out[i] = float(d.min())
        return out

    # -- checks --------------------------------------------------------------

    def clearance(self, nid: int):
        rr = self.r.rr
        return lambda owner: rr.pair_clearance(nid, owner)

    def reach(self, nid: int) -> float:
        return float(self.r.rr.clearance_row(nid).max())

    def clears(self, nid: int, shape, li: int, mine=None, ignore_tags=()) -> bool:
        return self.index.clears(shape, li, mine if mine is not None else {nid},
                                 self.clearance(nid), edge=self.r.rr.edge_clearance,
                                 reach=self.reach(nid), ignore_tags=ignore_tags)

    def inside_board(self, x: float, y: float) -> bool:
        """The point lies on the board. The edge clearance of what stands
        there is the index's (it holds the outline's edges); this catches
        copper wholly outside, which clears every edge."""
        if self.outline is None:
            return True
        return geom.point_in_poly(x, y, self.outline)

    def via_fits(self, via, nid: int, pad: int, in_pad: bool = False) -> bool:
        """A via clears everything foreign on every routing layer it spans,
        keeps off other vias of its own net, lies on the board, and is in
        no pad of its net (unless ``in_pad``, an exposed pad's array)."""
        r = self.r
        rv = via.diameter / 2
        if not self.inside_board(via.x, via.y):
            return False
        span = set(r.board.layers_between(via.low_layer, via.high_layer))
        for li, layer in enumerate(r.grid.layers):
            if layer in span and not self.clears(nid, via.shape_on(layer), li):
                return False
        for x, y, ro in self.own_vias.get(nid, ()):
            if math.hypot(x - via.x, y - via.y) < rv + ro + r.c - EPS:
                return False
        # Clear of its net's other surface pads by a web; an exposed pad's
        # array is in that pad, and in no other (a pin of the same net
        # beside it included).
        disc = geom.circle(via.x, via.y, via.diameter)
        own = r.board.pads[pad]
        for p in self.pads_of.get(own.net, ()):
            if not p.is_smd or (in_pad and p is own):
                continue
            for c in p.copper:
                s = p.shape_on(c.layer)
                if geom.bboxes_near(disc, s, self.web) and \
                        geom.clearance(disc, s) < self.web - EPS:
                    return False
        # Its solder mask opening leaves the board's web to every other.
        if not r.options.legacy() and r.via_leaves_web(via, via.x, via.y,
                                                       pad if in_pad else None):
            return False
        return True

    # -- laying copper -------------------------------------------------------

    def lay(self, vias, tracks, tag) -> None:
        added = self.r.lay(vias, tracks)
        for li, owner, s in added:
            self.index.add(li, owner, s, tag)
        for v in vias:
            self.own_vias.setdefault(self.r.grid.owner(v.net), []).append((v.x, v.y, v.diameter / 2))
        old = self.objects.get(tag, ([], [], []))
        self.objects[tag] = (old[0] + list(vias), old[1] + list(tracks), old[2] + added)
        self.laid_any = True

    def reserve(self, shape, owner: int, li: int, tag) -> None:
        """Keep other nets off ``shape`` as if it were ``owner``'s copper,
        without laying any: a pin's access lane."""
        self.r.grid.add_shape(shape, owner, li)
        self.r._fixed = None
        self.index.add(li, owner, shape, tag)
        old = self.objects.get(tag, ([], [], []))
        self.objects[tag] = (old[0], old[1], old[2] + [(li, owner, shape)])
        self.laid_any = True

    def unlay(self, tag) -> tuple[list, list]:
        """Take up what was laid with ``tag``: off the board, out of the
        grid and the index. Returns its (vias, tracks)."""
        vias, tracks, added = self.objects.pop(tag, ([], [], []))
        if not added:
            return [], []
        self.index.remove_tag(tag)
        r = self.r
        gone_v = {id(v) for v in vias}
        gone_t = {id(t) for t in tracks}
        r.board.vias = [v for v in r.board.vias if id(v) not in gone_v]
        r.board.tracks = [t for t in r.board.tracks if id(t) not in gone_t]
        r.grid.remove_shapes(added)
        r._fixed = None
        for v in vias:
            lst = self.own_vias.get(r.grid.owner(v.net), [])
            self.own_vias[r.grid.owner(v.net)] = [o for o in lst if o != (v.x, v.y, v.diameter / 2)]
        return vias, tracks


# ---------------------------------------------------------------------------
# The stages
# ---------------------------------------------------------------------------

def run_stages(router) -> bool:
    """Run the stages the router's options ask for, in order. Returns
    whether any copper (or reservation) was laid."""
    from . import diffpair, lanes
    from .fanout import plane_fanout

    opts = router.options
    rep = router.stage_report
    ctx = StageContext(router)
    for name, on, fn in (("diff_pairs", opts.diff_pairs, diffpair.route_pairs),
                         ("plane_fanout", opts.plane_fanout, plane_fanout),
                         ("pin_lanes", opts.pin_lanes, reserve_pin_lanes),
                         ("bus_lanes", opts.bus_lanes, lanes.route_buses)):
        if not on:
            continue
        t0 = time.perf_counter()
        fn(ctx)
        rep.seconds[name] = round(time.perf_counter() - t0, 3)
    router.stage_context = ctx
    return ctx.laid_any


def reserve_pin_lanes(ctx: StageContext) -> None:
    """Keep the first ``lane_depth`` straight out of every fine-pitch IC
    pin for that pin's own net.

    Out of a 0.5 mm QFP a pin's track has a mil or two either side of
    its centreline; let another net cross in front of the pins and the
    pins behind it have no way out. A person keeps that strip clear.
    Each lane is the net's width wide, cut short where it would come
    within clearance of copper already there (a decoupling part's pad),
    and left out where the pitch has no room for a track between two
    lanes anyway.
    """
    r = ctx.r
    b = r.board
    by_comp: dict[str, list[int]] = {}
    for i in ctx.fine:
        by_comp.setdefault(b.pads[i].comp, []).append(i)
    count = 0
    for comp, pins in by_comp.items():
        centre = ctx.comp_centre(comp)
        for i in pins:
            pad = b.pads[i]
            net = pad.net
            if (not net or not ctx.routed(net) or i in r.laid_via or i in r.laid_joined
                    or (len(ctx.pads_of.get(net, [])) < 2 and net not in ctx.planes)):
                continue
            layer = pad.copper[0].layer
            li = r.grid.layer_index.get(layer)
            if li is None:
                continue
            nid = r.grid.net_id[net]
            w = min(ctx.width(net), min(pad.copper[0].w, pad.copper[0].h))
            if ctx.fine[i] < w + r.c - EPS:
                continue        # no room for the neighbour's track beside the lane
            d = outward(pad, centre)
            sh = pad.shape_on(layer)
            edge = _extent(sh, (pad.x, pad.y), d)
            sx, sy = pad.x + d[0] * edge, pad.y + d[1] * edge
            depth = r.options.lane_depth
            while depth > r.pitch:
                lane = geom.capsule(sx, sy, sx + d[0] * depth, sy + d[1] * depth, w)
                if ctx.clears(nid, lane, li):
                    if math.isinf(r.options.lane_cost):
                        ctx.reserve(lane, nid, li, ("lane", i))
                    else:
                        _mark_lane(r, lane, nid, li)
                    count += 1
                    break
                depth -= r.pitch / 2
    r.stage_report.pin_lanes = count


def _mark_lane(r, lane, nid: int, li: int) -> None:
    """Mark the cells where another net's track would come within
    clearance of the lane as the lane's net's: the router prices them
    ``lane_cost`` times over for every other net (Router._window_costs).
    A wall would be stronger, and on a dense board it cost connections
    the negotiation could otherwise make: priced, a lane is crossed only
    where nothing else will do."""
    from .grid import shape_distance

    if r.lane_owner is None:
        r.lane_owner = np.zeros((r.L, r.ny, r.nx), dtype=np.int32)
    spec = r.grid.spec
    reach = lane.r + r.c + r.w_def / 2 + r._bow_at(r.w_def / 2 + r.c)
    x0, y0, x1, y1 = lane.bbox
    i0, j0, i1, j1 = spec.window(x0 - reach, y0 - reach, x1 + reach, y1 + reach)
    if i0 >= i1 or j0 >= j1:
        return
    X = spec.x(np.arange(i0, i1))[None, :]
    Y = spec.y(np.arange(j0, j1))[:, None]
    near = shape_distance(lane, X, Y) < r.c + r.w_def / 2 + r._bow_at(r.w_def / 2 + r.c)
    win = r.lane_owner[li, j0:j1, i0:i1]
    win[near & (win == 0)] = nid


# ---------------------------------------------------------------------------
# The report at the end
# ---------------------------------------------------------------------------

def finish_report(router, out, drc=None) -> dict:
    """The stage report completed from the routed board ``out``: every via
    in a pad (for the fab notes), every bend sharper than 45 degrees, and
    every pad left unreached with what blocks it."""
    rep = router.stage_report
    rep.via_in_pad = vias_in_pads(router, out)
    rep.sharp_bends = len(sharp_bends(out.tracks, out.pads, out.vias))
    rep.unreached = unreached_pads(router, out)
    return rep.as_dict()


def vias_in_pads(router, out) -> list[dict]:
    """Every via of the routed board that lands in a surface pad of its
    own net: a BGA ball or an exposed pad. Each needs filling and capping
    at fabrication."""
    from .fanout import find_exposed_pads

    eps = set(find_exposed_pads(out))
    pads: dict[str, list] = {}
    for i, p in enumerate(out.pads):
        if p.is_smd and p.net:
            pads.setdefault(p.net, []).append(i)
    rank = {"exposed pad": 0, "bga": 1, "ic pin": 2, "passive": 3}
    res = []
    for v in out.vias:
        if v.comp:
            continue
        disc = geom.circle(v.x, v.y, v.diameter)
        hits = []
        for i in pads.get(v.net, ()):
            p = out.pads[i]
            for c in p.copper:
                s = p.shape_on(c.layer)
                if geom.bboxes_near(disc, s, 0.0) and geom.clearance(disc, s) < -EPS:
                    kind = ("exposed pad" if i in eps else "bga" if i in router.via_pads
                            else "passive" if router._is_passive(p) else "ic pin")
                    hits.append((rank[kind], kind, p.key))
                    break
        if hits:
            _, kind, key = min(hits)
            res.append({"x": round(v.x, 4), "y": round(v.y, 4), "net": v.net, "pad": key,
                        "kind": kind, "size": v.diameter, "hole": v.hole,
                        "note": "via in pad: fill and cap"})
    return res


def unreached_pads(router, out, near: float | None = None) -> list[dict]:
    """Every pad the routed board leaves apart from the largest group of
    its net, with its place and the nets whose copper lies nearest it on
    its layers (what is in its way).

    A negotiation that stalls stops; what it could not join is told pad
    by pad, so a person (or the next step) knows where to look.
    """
    from ..drc import Item, conductive_items, connectivity

    items = conductive_items(out)
    groups = connectivity(out, items)
    reach = near if near is not None else max(50.0, 4.0 * (router.w_def + router.c))
    boxes = np.array([it.shape.bbox for it in items]).reshape(-1, 4)
    # A plane net's pads that reach its plane: a group holding one of them
    # is the plane's, whatever its size. The rest of the net's groups are
    # what is unreached, not merely the smaller ones.
    on_plane: dict[str, set] = {}
    by_net: dict[str, list[int]] = {}
    for i, p in enumerate(out.pads):
        if p.net:
            by_net.setdefault(p.net, []).append(i)
    for net in router._plane_masks():
        on_plane[net] = set(by_net.get(net, []))
    for job in router.jobs:
        if job.plane_mask is None:
            continue
        ends = {tuple(p[0]) for p in job.paths} | {tuple(p[-1]) for p in job.paths}
        missed = {t.pad for t in job.terminals
                  if not any(tuple(c) in ends for c in t.cells.tolist())}
        on_plane[job.name] = set(by_net.get(job.name, [])) - missed
    res = []
    for net, parts in groups.items():
        if len(parts) < 2:
            continue
        parts = sorted(parts, key=len, reverse=True)
        if on_plane.get(net):
            parts.sort(key=lambda g: not (g & on_plane[net]))
        for part in parts[1:]:
            for i in sorted(part):
                p = out.pads[i]
                layers = set(p.layers())
                sel = np.nonzero((boxes[:, 0] <= p.x + reach) & (boxes[:, 2] >= p.x - reach)
                                 & (boxes[:, 1] <= p.y + reach) & (boxes[:, 3] >= p.y - reach))[0]
                dist: dict[str, float] = {}
                here = geom.circle(p.x, p.y, 0.0)
                for k in sel:
                    it: Item = items[k]
                    if not it.net or it.net == net or it.layer not in layers:
                        continue
                    g = geom.clearance(here, it.shape)
                    if g <= reach:
                        dist[it.net] = min(dist.get(it.net, math.inf), max(g, 0.0))
                blockers = sorted(dist, key=dist.get)[:6]
                res.append({"net": net, "pad": p.key, "x": round(p.x, 3), "y": round(p.y, 3),
                            "layers": sorted(layers), "blocked_by": blockers})
    return res
