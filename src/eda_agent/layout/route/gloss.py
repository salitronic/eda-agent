# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Gloss: lay finished tracks again as the shortest clean 45/90 runs.

Works on copper as it stands, whatever laid it: this router, an
autorouter or a hand. Per net and layer, the free tracks (not part of a
footprint, not keep-outs) are cut into runs between fixed points: a
point where the run meets anything but the next track of the run (a pad,
a via, an arc, a third track, a width change). Each run is replaced by
the shortest octilinear path between its ends that

* turns no more than 90 degrees at any joint, and meets the copper at
  its ends at no sharper angle than the run did (nor under 90 degrees
  where the run did not either);
* leaves and enters a surface pad from the pad's centre, along one of
  the pad's axes or at 45 degrees to them;
* still touches every piece of its net's copper the run touched, so no
  join is lost, a track teed onto its middle included;
* clears every other net's copper by the board's clearance rules, net
  ties allowed, and the board edge and keep-outs.

A run is changed only when the new one is shorter, or as short with
fewer bends. A run that breaks the house rules (a piece off the 45 degree
grid, a piece shorter than its width, an acute corner, a pad left off its
centre or off the 45 degree grid of its axes, an acute angle where it meets
its net's copper) is
changed, once the runs are laid in their fewest segments, for any clean
path no more than a little longer (``DIRTY_STRETCH``, ``DIRTY_EXTRA``):
shortest is no reason to keep it. What stays is named in the report
(``faults_kept``).

Every run is then laid in the fewest segments (``simplify``): tracks in
line become one, and a stretch of short jogs or off-angle pieces is
replaced by a straight, or a straight and a 45, wherever that clears and
is no longer. A bend inside a pad, off its centre, and a joint that only
the round end of the next track but one reaches are part of the run, not
joins that split it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ... import job_control
from .. import geom
from ..drc import BIG_POLY, Item, conductive_items, net_ties, _tied
from ..model import LayoutBoard, Pad, Track
from ..rules import RuleSet
from .exact import CopperIndex
from .params import RouteRules

#: Two track ends closer than this, in mils, are one point.
SNAP = 0.01
#: Copper closer than this touches.
TOUCH = 1e-3
#: How much shorter a run must get to be changed, in mils.
GAIN = 0.5
#: Slack on the 90 degree turn limit, in degrees.
TURN_SLACK = 0.5
#: The most points of a run one replacement may span.
SPAN = 8
#: A run that breaks the house rules gives way to a clean one up to this
#: much longer: a factor and a few mils more.
DIRTY_STRETCH = 1.3
DIRTY_EXTRA = 20.0
#: Runs kept with their faults named in the report; the rest are counted.
FAULTS_LISTED = 30


#: Why a run was kept, by the furthest check a shorter path got past.
KEPT_BECAUSE = ("already_shortest", "pad_entry", "end_angle", "clearance",
                "would_lose_a_join", "would_cross_a_pad")
#: Why a run that breaks the house rules was kept: no clean path from its
#: start to its end is near as short. The rest are KEPT_BECAUSE's.
NO_CLEAN_PATH = "no_clean_path_near_as_short"


def house_faults(pts, a_pad, b_pad, layer: str, width: float,
                 angle_a: float, angle_b: float) -> list[str]:
    """What in a run breaks the house rules: ``off_grid`` (a piece off the
    45 degree grid), ``short_piece`` (shorter than the track is wide, in a
    run of more than one), ``acute_corner`` (a turn of more than 90
    degrees), ``pad_entry`` (a pad left off its centre, or off its axes
    and the 45 degree lines between them), ``acute_join`` (under 90 degrees
    to the copper an end meets)."""
    out = []
    segs = list(zip(pts, pts[1:]))
    if any(not _octilinear(a, b) for a, b in segs):
        out.append("off_grid")
    if len(segs) > 1 and any(math.hypot(b[0] - a[0], b[1] - a[1]) < width - SNAP
                             for a, b in segs):
        out.append("short_piece")
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u, v = _dir(a, b), _dir(b, c)
        if u and v and _turn(u, v) > 90.0 + TURN_SLACK:
            out.append("acute_corner")
            break
    for pad, end, nxt in ((a_pad, pts[0], pts[1]), (b_pad, pts[-1], pts[-2])):
        if pad is None:
            continue
        centred = math.hypot(end[0] - pad.x, end[1] - pad.y) <= SNAP
        if not centred or not _leaves_pad(pad, layer, end, nxt, width):
            out.append("pad_entry")
            break
    if ((a_pad is None and angle_a < 90.0 - TURN_SLACK)
            or (b_pad is None and angle_b < 90.0 - TURN_SLACK)):
        out.append("acute_join")
    return out


@dataclass
class GlossReport:
    runs: int = 0
    relaid: int = 0
    length_before: float = 0.0
    length_after: float = 0.0
    bends_before: int = 0
    bends_after: int = 0
    nets: list[str] = field(default_factory=list)
    kept: dict = field(default_factory=dict)
    blocked_by: dict = field(default_factory=dict)
    #: Runs that break the house rules and stay as they are: net, layer,
    #: the faults, why, and where the run starts.
    faults_kept: list = field(default_factory=list)
    #: Runs laid again for their faults, not their length.
    relaid_clean: int = 0
    #: Runs laid again in fewer segments, and their track counts.
    simplified: int = 0
    segments_before: int = 0
    segments_after: int = 0
    #: Runs of tiny steps (layout/staircase.py) no clear path could replace.
    staircases_left: list = field(default_factory=list)
    staircase_count: int = 0

    def as_dict(self) -> dict:
        return {"runs": self.runs, "relaid": self.relaid, "kept": dict(self.kept),
                "simplified": self.simplified, "segments_before": self.segments_before,
                "segments_after": self.segments_after,
                "staircase_count": self.staircase_count,
                "staircases_left": list(self.staircases_left),
                "blocked_by": dict(self.blocked_by),
                "length_before": round(self.length_before, 3),
                "length_after": round(self.length_after, 3),
                "bends_before": self.bends_before, "bends_after": self.bends_after,
                "nets": sorted(set(self.nets)),
                "relaid_clean": self.relaid_clean,
                "faults_kept": len(self.faults_kept),
                "faults_kept_listed": self.faults_kept[:FAULTS_LISTED]}


def _key(x: float, y: float) -> tuple[int, int]:
    return (round(x / SNAP), round(y / SNAP))


def _dir(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > SNAP else None


def _turn(u, v) -> float:
    """Degrees the path turns going along u then v (0 = straight on)."""
    c = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.degrees(math.acos(c))


def _length(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _bends(pts) -> int:
    n = 0
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u, v = _dir(a, b), _dir(b, c)
        if u and v and _turn(u, v) > TURN_SLACK:
            n += 1
    return n


def _octilinear(a, b) -> bool:
    d = _dir(a, b)
    if d is None:
        return True
    ang = math.degrees(math.atan2(d[1], d[0])) % 45.0
    return min(ang, 45.0 - ang) < 0.05


def _sign(v: float) -> float:
    return 1.0 if v > 0 else -1.0 if v < 0 else 0.0


def _forms(p, q):
    """Octilinear paths from p to q: straight, a straight and a 45 in
    either order, one split round the other, and the two right-angle
    paths a pad axis may need (on the diagonal, also a stub along each
    axis either side of the 45). Every step moves toward q on both axes, so
    no joint turns more than 90 degrees."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    ax, ay = abs(dx), abs(dy)
    sx, sy = _sign(dx), _sign(dy)
    if ax < SNAP or ay < SNAP:
        return [[p, q]]
    if abs(ax - ay) < SNAP:
        # On the diagonal: straight, or leaving along an axis (for a pad)
        # by a right angle, or by a stub, the 45 and a stub on the other axis.
        out = [[p, q], [p, (q[0], p[1]), q], [p, (p[0], q[1]), q]]
        for f in (0.25, 0.5):
            st = ax * f
            out.append([p, (p[0] + sx * st, p[1]), (q[0], q[1] - sy * st), q])
            out.append([p, (p[0], p[1] + sy * st), (q[0] - sx * st, q[1]), q])
        return out
    a = min(ax, ay)
    diag = (sx * a, sy * a)
    rest = (dx - diag[0], dy - diag[1])
    out = [
        [p, (p[0] + rest[0], p[1] + rest[1]), q],
        [p, (p[0] + diag[0], p[1] + diag[1]), q],
        [p, (p[0] + rest[0] / 2, p[1] + rest[1] / 2),
         (p[0] + rest[0] / 2 + diag[0], p[1] + rest[1] / 2 + diag[1]), q],
        [p, (p[0] + diag[0] / 2, p[1] + diag[1] / 2),
         (p[0] + diag[0] / 2 + rest[0], p[1] + diag[1] / 2 + rest[1]), q],
        [p, (q[0], p[1]), q],
        [p, (p[0], q[1]), q],
    ]
    return out


def _pad_angle_ok(pad: Pad, a, b) -> bool:
    """Whether the segment a->b runs along one of the pad's axes or at 45
    degrees to them."""
    d = _dir(a, b)
    if d is None:
        return True
    ang = (math.degrees(math.atan2(d[1], d[0])) - pad.rotation) % 45.0
    return min(ang, 45.0 - ang) < 0.05


def _leaves_pad(pad: Pad, layer: str, centre, turn, width: float) -> bool:
    """Whether a run leaving the pad's centre for ``turn`` goes along one
    of the pad's axes or at 45 degrees to them: the straight or 45 degree
    piece of a dogleg onto the centre."""
    return _pad_angle_ok(pad, centre, turn)


class _Copper:
    """Every conductive item, filed per layer, that tracks can be taken out
    of and put into as runs are laid again."""

    def __init__(self, board: LayoutBoard, rules: RuleSet, spacing=None, barriers=()):
        self.board = board
        self.rules = rules
        # (net, net) -> the routing brief's spacing between them, and its
        # isolation barriers (Brief.barrier_sides), kept as clearances.
        self.spacing: dict = dict(spacing or {})
        self.barriers = list(barriers)
        self.layers = {name: i for i, name in enumerate(board.copper_layers())}
        self.index = CopperIndex(len(self.layers), bucket=100.0)
        self.items: list[Item | None] = []
        self.of_track: dict[int, list[int]] = {}
        self.ties = net_ties(board)
        self._gap: dict = {}
        self._poly: dict[int, geom.PolyIndex] = {}
        for it in conductive_items(board):
            if it.kind == "track":
                self.add_track(board.tracks[it.idx], it)
            else:
                self._add(it)
        # Keep-outs: nothing may overlap them.
        self.keepouts: dict[str, list[geom.Shape]] = {}
        for t in board.tracks:
            if t.keepout:
                self.keepouts.setdefault(t.layer, []).append(t.shape())
        for r in board.regions:
            if r.kind == "keepout" or r.keepout:
                self.keepouts.setdefault(r.layer, []).append(r.shape())

    def _add(self, it: Item) -> int:
        k = len(self.items)
        self.items.append(it)
        li = self.layers.get(it.layer)
        if li is not None:
            self.index.add(li, k, it.shape, tag=("i", k))
        return k

    def add_track(self, t: Track, it: Item | None = None) -> None:
        it = it or Item("track", -1, t.net, t.layer, t.shape(), t.comp)
        self.of_track.setdefault(id(t), []).append(self._add(it))

    def drop_track(self, t: Track) -> None:
        for k in self.of_track.pop(id(t), []):
            self.index.remove_tag(("i", k))
            self.items[k] = None

    def near(self, layer: str, shape: geom.Shape, reach: float):
        li = self.layers.get(layer)
        if li is None:
            return
        for k, _s, _tag in self.index.near(li, shape.bbox, reach):
            it = self.items[k]
            if it is not None:
                yield k, it

    def gap(self, s: geom.Shape, k: int, cutoff: float) -> float:
        """Edge-to-edge gap from ``s`` to item ``k``, exact up to
        ``cutoff``; a big polygon is answered through its index."""
        o = self.items[k].shape
        if o.kind == "poly" and len(o.pts) + sum(len(h) for h in o.holes) > BIG_POLY:
            if k not in self._poly:
                self._poly[k] = geom.PolyIndex(o)
            return self._poly[k].core_distance(s, cutoff + s.r + o.r) - s.r - o.r
        return geom.clearance(s, o)

    def required(self, cand: Item, other: Item) -> float:
        key = (self.rules.facts(cand, self.board), self.rules.facts(other, self.board))
        if key not in self._gap:
            self._gap[key] = self.rules.clearance(cand, other, self.board)
        need = max(self._gap[key], self.spacing.get((cand.net, other.net), 0.0))
        for b in self.barriers:
            if {RouteRules._side(cand.net, b), RouteRules._side(other.net, b)} == {"a", "b"}:
                need = max(need, b[2])
        return need


def _edge_gap(rules: RuleSet) -> float:
    edge = rules.of_type("board_outline")
    return edge[0].values.get("gap", 0.0) if edge else 0.0


class _Gloss:
    def __init__(self, board: LayoutBoard, rules: RuleSet, spacing=None, barriers=(),
                 areas=(), fences=None):
        self.board = board
        self.rules = rules
        self.copper = _Copper(board, rules, spacing, barriers)
        # The routing brief's areas (brief.AreaRule): no track of a net an
        # area bars may be laid into it.
        self.areas = [(a, [geom.polygon(ring) for ring in a.polygons]) for a in areas]
        # A net's own region and keep-outs from its brief entry: net ->
        # (region rings or None, keep-out rings).
        self.fences = dict(fences or {})
        self.edge_gap = _edge_gap(rules)
        self.outline = board.outline_shape() if len(board.outline) >= 3 else None
        self.reach = max(rules.max_clearance(), self.edge_gap, 1.0,
                         *self.copper.spacing.values(),
                         *(b[2] for b in self.copper.barriers)) + 1.0
        self.report = GlossReport()
        self.blocker = ""

    # -- runs ---------------------------------------------------------------

    def _free(self, net: str, layer: str) -> list[Track]:
        return [t for t in self.board.tracks
                if t.net == net and t.layer == layer and not t.comp and not t.keepout
                and math.hypot(t.x2 - t.x1, t.y2 - t.y1) > SNAP]

    @staticmethod
    def _next_but_one(it: Item, here, ends) -> bool:
        """Whether a track reaches ``here`` only with the round end it has
        at a joint close by, and heads away from here: pieces shorter than
        half a width lie between, so its join is at that joint, not here.
        (A run laid again must still touch it: see _joins.)"""
        if len(it.shape.pts) != 2:
            return False
        a, b = it.shape.pts
        for end in (a, b):
            gap = math.hypot(end[0] - here[0], end[1] - here[1])
            if gap <= SNAP or gap > it.shape.r + TOUCH:
                continue
            if _key(*end) in ends:
                other = b if end is a else a
                # Running on past ``here`` it would lie over the run.
                along = ((here[0] - end[0]) * (other[0] - end[0])
                         + (here[1] - end[1]) * (other[1] - end[1]))
                if along <= TOUCH:
                    return True
        return False

    def _runs(self, tracks: list[Track]):
        """Runs as (points, tracks), each from a fixed point to a fixed
        point through ends where exactly two tracks of one width meet and
        nothing else does."""
        ends: dict = {}
        for t in tracks:
            ends.setdefault(_key(t.x1, t.y1), []).append(t)
            ends.setdefault(_key(t.x2, t.y2), []).append(t)

        def other_end(t, k):
            return (t.x2, t.y2) if _key(t.x1, t.y1) == k else (t.x1, t.y1)

        def fixed(k) -> bool:
            at = ends.get(k, [])
            if len(at) != 2 or abs(at[0].width - at[1].width) > SNAP:
                return True
            # Anything else of the net at this point, a track teed onto it
            # or passing through it included, makes it a join.
            own = {k_ for t in at for k_ in self.copper.of_track.get(id(t), [])}
            here = (k[0] * SNAP, k[1] * SNAP)
            probe = geom.circle(here[0], here[1], 0.0)
            for k_, it in self.copper.near(at[0].layer, probe, TOUCH):
                if it.net == at[0].net and k_ not in own \
                        and self.copper.gap(probe, k_, TOUCH) <= TOUCH:
                    if it.kind == "track" and self._next_but_one(it, here, ends):
                        continue
                    # A bend inside a pad, off its centre, is the run's
                    # own; the pad stays one of its joins. At the centre
                    # the run is joined to the pad there.
                    if it.kind == "pad":
                        pad = self.board.pads[it.idx]
                        if math.hypot(pad.x - here[0], pad.y - here[1]) > 2 * SNAP:
                            continue
                    return True
            return False

        used: set[int] = set()
        runs = []
        starts = [k for k in ends if fixed(k)]
        for k0 in starts:
            for t0 in ends[k0]:
                if id(t0) in used:
                    continue
                pts = [(k0[0] * SNAP, k0[1] * SNAP)]
                exact = [(t0.x1, t0.y1) if _key(t0.x1, t0.y1) == k0 else (t0.x2, t0.y2)]
                seq = []
                t, k = t0, k0
                while True:
                    used.add(id(t))
                    seq.append(t)
                    nxt = other_end(t, k)
                    exact.append(nxt)
                    k = _key(*nxt)
                    if fixed(k):
                        break
                    t = next(u for u in ends[k] if u is not t)
                    if id(t) in used:
                        break
                runs.append((exact, seq))
        return runs

    # -- the pads a run ends in ----------------------------------------------

    def _end_pad(self, net: str, layer: str, pt) -> Pad | None:
        """The one surface pad of the net the point lies in, if exactly one."""
        probe = geom.circle(pt[0], pt[1], 0.0)
        found = []
        for k, it in self.copper.near(layer, probe, TOUCH):
            if it.kind == "pad" and it.net == net and self.copper.gap(probe, k, TOUCH) <= TOUCH:
                found.append(self.board.pads[it.idx])
        found = [p for p in found if p.is_smd]
        return found[0] if len(found) == 1 else None

    def _pads_at(self, net: str, layer: str, pt) -> set[int]:
        """The net's pads (item ids) the point lies in."""
        probe = geom.circle(pt[0], pt[1], 0.0)
        return {k for k, it in self.copper.near(layer, probe, TOUCH)
                if it.kind == "pad" and it.net == net
                and self.copper.gap(probe, k, TOUCH) <= TOUCH}

    # -- checks ---------------------------------------------------------------

    def _joins(self, net: str, layer: str, shapes, mine: set[int]) -> set[int]:
        """Items of the net, other than the run's own tracks, that the run's
        copper touches."""
        out = set()
        for s in shapes:
            for k, it in self.copper.near(layer, s, TOUCH):
                if it.net == net and k not in mine and self.copper.gap(s, k, TOUCH) <= TOUCH:
                    out.add(k)
        return out

    def _clears(self, net: str, layer: str, s: geom.Shape, comp: str = "") -> bool:
        cand = Item("track", -1, net, layer, s, comp)
        for k, it in self.copper.near(layer, s, self.reach):
            if it.net == net or (not it.net and not net):
                continue
            if it.kind == "plane":
                continue
            if self.copper.ties and _tied(cand, it, self.copper.ties, self.board):
                continue
            need = self.copper.required(cand, it)
            if need > 0 and self.copper.gap(s, k, need) < need - 1e-3:
                self.blocker = f"{it.kind} of {it.net or 'no net'}"
                return False
        for ko in self.copper.keepouts.get(layer, []):
            if geom.clearance(s, ko) < 0.0:
                self.blocker = "keep-out"
                return False
        for area, shapes in self.areas:
            if area.bars(net, layer) and any(geom.clearance(s, a) < 0.0 for a in shapes):
                self.blocker = f"area {area.name or area.kind}"
                return False
        fence = self.fences.get(net)
        if fence and not _within_fence(s, *fence):
            self.blocker = "the net's region or keep-out"
            return False
        if self.outline is not None and \
                geom.shape_to_outline_clearance(s, self.outline) < self.edge_gap - 1e-3:
            self.blocker = "board edge"
            return False
        return True

    def _end_angles(self, net: str, layer: str, end, first, own: set[int]) -> float:
        """The sharpest angle in degrees (180 = straight on, 0 = folded back)
        between the segment end->first and any other track of the net
        leaving ``end``."""
        u = _dir(end, first)
        if u is None:
            return 180.0
        worst = 180.0
        probe = geom.circle(end[0], end[1], 0.0)
        for k, it in self.copper.near(layer, probe, TOUCH):
            if it.kind != "track" or it.net != net or k in own:
                continue
            a, b = it.shape.pts
            for p, q in ((a, b), (b, a)):
                v = _dir(p, q)
                if v and math.hypot(p[0] - end[0], p[1] - end[1]) <= SNAP:
                    worst = min(worst, _turn(u, v))
        return worst

    # -- one run --------------------------------------------------------------

    def relay(self, net: str, layer: str, pts, seq: list[Track], clean: bool = False) -> bool:
        """Lay the run again: shorter, or as short with fewer bends; with
        ``clean``, a run that breaks the house rules as any clean path near
        as short (and one that breaks none is left)."""
        width = seq[0].width
        if any(abs(t.width - width) > SNAP for t in seq):
            return False
        mine = {k for t in seq for k in self.copper.of_track.get(id(t), [])}
        old_shapes = [t.shape() for t in seq]
        joins = self._joins(net, layer, old_shapes, mine)
        # A new run touches no pad of its net but the ones at its ends, so
        # a run that passes over a pad on the way (a join it must keep) is
        # left as laid.
        end_pads = self._pads_at(net, layer, pts[0]) | self._pads_at(net, layer, pts[-1])
        a_pad = self._end_pad(net, layer, pts[0])
        b_pad = self._end_pad(net, layer, pts[-1])
        p = (a_pad.x, a_pad.y) if a_pad else pts[0]
        q = (b_pad.x, b_pad.y) if b_pad else pts[-1]
        old_len = _length(pts)
        old_bends = _bends(pts)
        old_a = self._end_angles(net, layer, pts[0], pts[1], mine)
        old_b = self._end_angles(net, layer, pts[-1], pts[-2], mine)
        faults = house_faults(pts, a_pad, b_pad, layer, width, old_a, old_b) if clean else []
        if clean and not faults:
            return False
        reach = old_len * DIRTY_STRETCH + DIRTY_EXTRA
        best = None
        # The furthest check any path got past: what kept the run as it is,
        # and, kept for clearance, the last thing a path ran into.
        furthest = -1
        self.blocker = ""
        for form in _forms(p, q):
            form = [form[0]] + [x for x, y in zip(form[1:], form) if math.hypot(x[0] - y[0], x[1] - y[1]) > SNAP]
            if len(form) < 2:
                continue
            new_len = _length(form)
            new_bends = _bends(form)
            if faults:
                # Clean is the gain: any path near as short, with no piece
                # shorter than the track is wide.
                if new_len > reach or (len(form) > 2 and any(
                        math.hypot(b[0] - a[0], b[1] - a[1]) < width - SNAP
                        for a, b in zip(form, form[1:]))):
                    continue
            elif not (new_len < old_len - GAIN
                      or (new_len <= old_len + SNAP and new_bends < old_bends)):
                continue
            furthest = max(furthest, 0)
            if a_pad and not _leaves_pad(a_pad, layer, form[0], form[1], width):
                continue
            if b_pad and not _leaves_pad(b_pad, layer, form[-1], form[-2], width):
                continue
            furthest = max(furthest, 1)
            # The copper at each end: no sharper than the run met it, and
            # nowhere under 90 degrees where the run was not either.
            # A run replaced for its faults meets its copper at 90 or more.
            floor_a = 90.0 if faults else min(old_a, 90.0)
            floor_b = 90.0 if faults else min(old_b, 90.0)
            if not a_pad:
                na = self._end_angles(net, layer, form[0], form[1], mine)
                if na < floor_a - TURN_SLACK:
                    continue
            if not b_pad:
                nb = self._end_angles(net, layer, form[-1], form[-2], mine)
                if nb < floor_b - TURN_SLACK:
                    continue
            furthest = max(furthest, 2)
            shapes = [geom.capsule(a[0], a[1], b[0], b[1], width) for a, b in zip(form, form[1:])]
            if not all(self._clears(net, layer, s) for s in shapes):
                continue
            furthest = max(furthest, 3)
            touched = self._joins(net, layer, shapes, mine)
            if not joins <= touched:
                continue
            furthest = max(furthest, 4)
            if any(self.copper.items[k].kind == "pad" and k not in end_pads for k in touched):
                continue
            score = (new_len, new_bends)
            if best is None or score < best[0]:
                best = (score, form)
        if not clean:
            self.report.runs += 1
        if best is None:
            why = NO_CLEAN_PATH if faults and furthest < 0 else KEPT_BECAUSE[furthest + 1]
            if not clean:
                self.report.kept[why] = self.report.kept.get(why, 0) + 1
            if faults:
                # Named, so a tidy that leaves them says which and why.
                self.report.faults_kept.append(
                    {"net": net, "layer": layer, "faults": faults, "kept_because": why,
                     "at": [round(pts[0][0], 1), round(pts[0][1], 1)]})
            if why == "clearance" and self.blocker:
                self.report.blocked_by[self.blocker] = \
                    self.report.blocked_by.get(self.blocker, 0) + 1
            return False
        form = best[1]
        # By identity: two tracks can be equal, and only these go.
        gone = {id(t) for t in seq}
        self.board.tracks = [t for t in self.board.tracks if id(t) not in gone]
        for t in seq:
            self.copper.drop_track(t)
        for a, b in zip(form, form[1:]):
            t = Track(layer, a[0], a[1], b[0], b[1], width, net)
            self.board.tracks.append(t)
            self.copper.add_track(t)
            if self.only is not None:
                self.only.add(id(t))
        self.report.relaid += 1
        if clean:
            self.report.relaid_clean += 1
        self.report.length_before += old_len
        self.report.length_after += _length(form)
        self.report.bends_before += old_bends
        self.report.bends_after += _bends(form)
        self.report.nets.append(net)
        return True

    # -- fewest segments ------------------------------------------------------

    @staticmethod
    def _merged(pts) -> list:
        """The path without the points it passes straight through."""
        out = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) <= SNAP:
                continue
            if len(out) >= 2:
                u, v = _dir(out[-2], out[-1]), _dir(out[-1], p)
                if u and v and _turn(u, v) <= TURN_SLACK:
                    out[-1] = p
                    continue
            out.append(p)
        return out

    @staticmethod
    def _sharp(pts) -> int:
        """Joints that turn more than 90 degrees."""
        n = 0
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            u, v = _dir(a, b), _dir(b, c)
            if u and v and _turn(u, v) > 90.0 + TURN_SLACK:
                n += 1
        return n

    @staticmethod
    def _odd(pts) -> int:
        """Segments that are neither on an axis nor at 45 degrees."""
        return sum(1 for a, b in zip(pts, pts[1:]) if not _octilinear(a, b))

    def simplify(self, net: str, layer: str, pts, seq: list[Track]) -> bool:
        """Lay a run again in the fewest segments: straight-through joints
        become one track, and any stretch of it that a straight, or a
        straight and a 45, can replace is replaced, where that clears. The
        run keeps its ends, its joins and its length; short jogs and
        off-angle pieces go."""
        width = seq[0].width
        if any(abs(t.width - width) > SNAP for t in seq):
            return False
        mine = {k for t in seq for k in self.copper.of_track.get(id(t), [])}
        joins = self._joins(net, layer, [t.shape() for t in seq], mine)
        end_pads = self._pads_at(net, layer, pts[0]) | self._pads_at(net, layer, pts[-1])
        allowed_pads = end_pads | {k for k in joins if self.copper.items[k].kind == "pad"}
        a_pad = self._end_pad(net, layer, pts[0])
        b_pad = self._end_pad(net, layer, pts[-1])
        old_a = self._end_angles(net, layer, pts[0], pts[1], mine)
        old_b = self._end_angles(net, layer, pts[-1], pts[-2], mine)
        a_ok = bool(a_pad) and _leaves_pad(a_pad, layer, pts[0], pts[1], width)
        b_ok = bool(b_pad) and _leaves_pad(b_pad, layer, pts[-1], pts[-2], width)

        def holds(cand, laid) -> bool:
            if self._sharp(cand) > self._sharp(laid) or self._odd(cand) > self._odd(laid):
                return False
            if a_ok and not _leaves_pad(a_pad, layer, cand[0], cand[1], width):
                return False
            if b_ok and not _leaves_pad(b_pad, layer, cand[-1], cand[-2], width):
                return False
            if not a_pad and self._end_angles(net, layer, cand[0], cand[1], mine) \
                    < min(old_a, 90.0) - TURN_SLACK:
                return False
            if not b_pad and self._end_angles(net, layer, cand[-1], cand[-2], mine) \
                    < min(old_b, 90.0) - TURN_SLACK:
                return False
            had = {(_key(*a), _key(*b)) for a, b in zip(laid, laid[1:])}
            shapes = []
            for a, b in zip(cand, cand[1:]):
                s = geom.capsule(a[0], a[1], b[0], b[1], width)
                shapes.append(s)
                if (_key(*a), _key(*b)) not in had and not self._clears(net, layer, s):
                    return False
            touched = self._joins(net, layer, shapes, mine)
            if not joins <= touched:
                return False
            return not any(self.copper.items[k].kind == "pad" and k not in allowed_pads
                           for k in touched)

        cur = self._merged(pts)
        again = True
        while again and len(cur) > 2:
            again = False
            n = len(cur)
            for i in range(n - 2):
                for j in range(min(n - 1, i + SPAN), i + 1, -1):
                    for form in _forms(cur[i], cur[j])[:4]:
                        cand = self._merged(cur[:i] + list(form) + cur[j + 1:])
                        if len(cand) >= len(cur) and self._odd(cand) >= self._odd(cur):
                            continue
                        if len(cand) > len(cur) or _length(cand) > _length(cur) + SNAP:
                            continue
                        if holds(cand, cur):
                            cur, again = cand, True
                            break
                    if again:
                        break
                if again:
                    break
        before = len(seq)
        if len(cur) - 1 >= before and self._odd(cur) >= self._odd(pts):
            return False
        gone = {id(t) for t in seq}
        self.board.tracks = [t for t in self.board.tracks if id(t) not in gone]
        for t in seq:
            self.copper.drop_track(t)
        for a, b in zip(cur, cur[1:]):
            t = Track(layer, a[0], a[1], b[0], b[1], width, net)
            self.board.tracks.append(t)
            self.copper.add_track(t)
            if self.only is not None:
                self.only.add(id(t))
        self.report.simplified += 1
        self.report.segments_before += before
        self.report.segments_after += len(cur) - 1
        self.report.nets.append(net)
        return True

    def run(self, nets=None, layers=None, passes: int = 3, tracks=None) -> GlossReport:
        wanted = set(nets) if nets else None
        # Only runs made wholly of these tracks (and of tracks laid again
        # here) may change.
        self.only = {id(t) for t in tracks} if tracks is not None else None
        names = sorted({t.net for t in self.board.tracks if t.net and not t.comp})
        lays = [l for l in self.board.signal_layers() if not layers or l in layers]
        for _ in range(passes):
            changed = False
            for net in names:
                if wanted is not None and net not in wanted:
                    continue
                job_control.checkpoint()
                for layer in lays:
                    # Runs share no track, so laying one again leaves the
                    # others' tracks in place.
                    for pts, seq in self._runs(self._free(net, layer)):
                        if len(seq) < 2 and _octilinear(pts[0], pts[-1]):
                            continue
                        if self.only is not None and any(id(t) not in self.only for t in seq):
                            continue
                        changed |= self.relay(net, layer, pts, seq)
            if not changed:
                break
        for net in names:
            if wanted is not None and net not in wanted:
                continue
            for layer in lays:
                for pts, seq in self._runs(self._free(net, layer)):
                    if len(seq) < 2:
                        continue
                    if self.only is not None and any(id(t) not in self.only for t in seq):
                        continue
                    self.simplify(net, layer, pts, seq)
        # Last, what still breaks the house rules gives way to a clean
        # path near as short.
        for net in names:
            if wanted is not None and net not in wanted:
                continue
            job_control.checkpoint()
            for layer in lays:
                for pts, seq in self._runs(self._free(net, layer)):
                    if self.only is not None and any(id(t) not in self.only for t in seq):
                        continue
                    self.relay(net, layer, pts, seq, clean=True)
        from ..staircase import find as find_staircases
        left = find_staircases(
            t for t in self.board.tracks if not t.comp and not t.keepout
            and (wanted is None or t.net in wanted) and (not layers or t.layer in layers)
            and (self.only is None or id(t) in self.only))
        self.report.staircases_left = left["staircases"]
        self.report.staircase_count = left["staircase_count"]
        return self.report


FENCE_STEP = 5.0


def _within_fence(s: geom.Shape, region, keep_out) -> bool:
    """Whether a track's centre line stays inside ``region`` (when given)
    and out of ``keep_out``, tested every FENCE_STEP mils along it, the
    ends included: as the brief judges a route (brief.broken), and finer."""
    from .brief import _inside
    a, b = s.pts[0], s.pts[-1]
    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / FENCE_STEP))
    for i in range(n + 1):
        x, y = a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n
        if (region and not _inside(region, x, y)) or (keep_out and _inside(keep_out, x, y)):
            return False
    return True


def gloss(board: LayoutBoard, rules: RuleSet | None = None, nets=None, layers=None,
          passes: int = 3, tracks=None, spacing=None, barriers=(),
          areas=(), fences=None) -> GlossReport:
    """Lay the board's free tracks again as the shortest clean 45/90 runs,
    in place. See the module docstring for what a run may become.
    ``tracks`` limits it to runs made only of those Track objects (a
    router's own output); every other track stays as it is. ``spacing``
    and ``barriers`` the routing brief's (Brief.spacing_pairs,
    Brief.barrier_sides), kept like clearances, and ``areas`` its areas
    (Brief.areas), which no track of a net they bar is laid into.
    ``fences``: net -> (region rings or None, keep-out rings), from the
    brief's entries: a net's track stays inside its region and out of its
    keep-outs."""
    return _Gloss(board, rules or RuleSet.from_board(board), spacing, barriers, areas,
                  fences).run(
        nets, layers, passes, tracks)
