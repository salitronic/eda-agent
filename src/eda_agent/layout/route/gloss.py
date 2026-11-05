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
* leaves and enters a surface pad straight along one of the pad's axes,
  from the pad's centre;
* still touches every piece of its net's copper the run touched, so no
  join is lost, a track teed onto its middle included;
* clears every other net's copper by the board's clearance rules, net
  ties allowed, and the board edge and keep-outs.

A run is changed only when the new one is shorter, or as short with
fewer bends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geom
from ..drc import BIG_POLY, Item, conductive_items, net_ties, _tied
from ..model import LayoutBoard, Pad, Track
from ..rules import RuleSet
from .exact import CopperIndex

#: Two track ends closer than this, in mils, are one point.
SNAP = 0.01
#: Copper closer than this touches.
TOUCH = 1e-3
#: How much shorter a run must get to be changed, in mils.
GAIN = 0.5
#: Slack on the 90 degree turn limit, in degrees.
TURN_SLACK = 0.5


#: Why a run was kept, by the furthest check a shorter path got past.
KEPT_BECAUSE = ("already_shortest", "pad_entry", "end_angle", "clearance",
                "would_lose_a_join", "would_cross_a_pad")


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

    def as_dict(self) -> dict:
        return {"runs": self.runs, "relaid": self.relaid, "kept": dict(self.kept),
                "blocked_by": dict(self.blocked_by),
                "length_before": round(self.length_before, 3),
                "length_after": round(self.length_after, 3),
                "bends_before": self.bends_before, "bends_after": self.bends_after,
                "nets": sorted(set(self.nets))}


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
    paths a pad axis may need. Every step moves toward q on both axes, so
    no joint turns more than 90 degrees."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    ax, ay = abs(dx), abs(dy)
    sx, sy = _sign(dx), _sign(dy)
    if ax < SNAP or ay < SNAP or abs(ax - ay) < SNAP:
        return [[p, q]]
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


def _pad_axis_ok(pad: Pad, a, b) -> bool:
    """Whether the segment a->b runs along one of the pad's axes."""
    d = _dir(a, b)
    if d is None:
        return True
    ang = (math.degrees(math.atan2(d[1], d[0])) - pad.rotation) % 90.0
    return min(ang, 90.0 - ang) < 0.05


def _leaves_pad(pad: Pad, layer: str, centre, turn, width: float) -> bool:
    """Whether a run leaving the pad's centre for ``turn`` goes along one
    of the pad's axes and turns only once the track is clear of the pad's
    copper: a turn inside it leaves the pad on the slant all the same."""
    if not _pad_axis_ok(pad, centre, turn):
        return False
    s = pad.shape_on(layer)
    return s is None or geom.clearance(geom.circle(turn[0], turn[1], width), s) >= -1e-3


class _Copper:
    """Every conductive item, filed per layer, that tracks can be taken out
    of and put into as runs are laid again."""

    def __init__(self, board: LayoutBoard, rules: RuleSet):
        self.board = board
        self.rules = rules
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
        return self._gap[key]


def _edge_gap(rules: RuleSet) -> float:
    edge = rules.of_type("board_outline")
    return edge[0].values.get("gap", 0.0) if edge else 0.0


class _Gloss:
    def __init__(self, board: LayoutBoard, rules: RuleSet):
        self.board = board
        self.rules = rules
        self.copper = _Copper(board, rules)
        self.edge_gap = _edge_gap(rules)
        self.outline = board.outline_shape() if len(board.outline) >= 3 else None
        self.reach = max(rules.max_clearance(), self.edge_gap, 1.0) + 1.0
        self.report = GlossReport()
        self.blocker = ""

    # -- runs ---------------------------------------------------------------

    def _free(self, net: str, layer: str) -> list[Track]:
        return [t for t in self.board.tracks
                if t.net == net and t.layer == layer and not t.comp and not t.keepout
                and math.hypot(t.x2 - t.x1, t.y2 - t.y1) > SNAP]

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
            probe = geom.circle(k[0] * SNAP, k[1] * SNAP, 0.0)
            for k_, it in self.copper.near(at[0].layer, probe, TOUCH):
                if it.net == at[0].net and k_ not in own \
                        and self.copper.gap(probe, k_, TOUCH) <= TOUCH:
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

    def relay(self, net: str, layer: str, pts, seq: list[Track]) -> bool:
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
            if not (new_len < old_len - GAIN or (new_len <= old_len + SNAP and new_bends < old_bends)):
                continue
            furthest = max(furthest, 0)
            if a_pad and not _leaves_pad(a_pad, layer, form[0], form[1], width):
                continue
            if b_pad and not _leaves_pad(b_pad, layer, form[-1], form[-2], width):
                continue
            furthest = max(furthest, 1)
            # The copper at each end: no sharper than the run met it, and
            # nowhere under 90 degrees where the run was not either.
            if not a_pad:
                na = self._end_angles(net, layer, form[0], form[1], mine)
                if na < min(old_a, 90.0) - TURN_SLACK:
                    continue
            if not b_pad:
                nb = self._end_angles(net, layer, form[-1], form[-2], mine)
                if nb < min(old_b, 90.0) - TURN_SLACK:
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
        self.report.runs += 1
        if best is None:
            why = KEPT_BECAUSE[furthest + 1]
            self.report.kept[why] = self.report.kept.get(why, 0) + 1
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
        self.report.length_before += old_len
        self.report.length_after += _length(form)
        self.report.bends_before += old_bends
        self.report.bends_after += _bends(form)
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
        return self.report


def gloss(board: LayoutBoard, rules: RuleSet | None = None, nets=None, layers=None,
          passes: int = 3, tracks=None) -> GlossReport:
    """Lay the board's free tracks again as the shortest clean 45/90 runs,
    in place. See the module docstring for what a run may become.
    ``tracks`` limits it to runs made only of those Track objects (a
    router's own output); every other track stays as it is."""
    return _Gloss(board, rules or RuleSet.from_board(board)).run(nets, layers, passes, tracks)
