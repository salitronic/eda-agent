# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Placement: global, then legal, then refined, on the exact board.

**Global.** Wirelength as a quadratic: every net pulls its pins together
(a clique for small nets, weighted so a net's pull does not grow with its
size), fixed parts and their pins anchor the solution, and the system is
solved with sparse linear algebra, x and y apart. Left alone that piles
every part in the middle, so each round the parts are spread by
recursive bisection of the board by area (the "look-ahead" of SimPL) and
tied to their spread spots by anchors that grow stiffer every round.

**Legal.** Largest first, each part takes the free spot nearest its
global position, trying its four turns, on a raster of what is already
occupied on its side of the board: the outline, fixed parts, and parts
placed before it. What a part keeps out is its courtyard, or, when the
footprint gave only its pads' extent, its pads themselves: an edge
connector's pads can span the whole board and fence nothing between
them.

**Refined.** Parts try small moves and turns; a change stays when it
shortens the wire and the part still fits.

Parts keep the side they were on. Nets with very many pins (ground and
power, which reach every part by a plane or a pour) are left out of the
wirelength, since pulling every part towards every other is what they
would do.

That is the "analytic" strategy. The "blocks" strategy (``floorplan``)
plans the board by sub-circuit instead and uses this class for its parts,
outlines and rasters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

from .. import geom
from ..model import BOTTOM, LayoutBoard
from ..route.grid import GridSpec, fill_polygon, shape_distance
from .transform import _Frame, set_pose

#: Nets with more pins than this are left out of the wirelength.
BIG_NET = 24
#: The share of the area parts fill when spread compactly (the
#: ``spread_density`` option). Spread over the whole board, a small circuit
#: on a large board is scattered across it, where a person packs it by its
#: connectors with far less wire. Compact spreading fixes that but routed
#: slightly worse over the benchmark set, so the default spreads over the
#: whole board.
SPREAD_DENSITY = 0.4
#: A part with at least this many pads is an IC, for decoupling.
IC_PADS = 8
#: How hard a decoupling part is pulled to its IC pin, against the pull of
#: an ordinary two-pin net, when the pull is asked for. Off by default:
#: over the benchmark set, placed then routed, every weight tried routed
#: worse than no pull, and it put parts beside an IC far more often than
#: the people who drew those boards did. The parts it picks are not all
#: decoupling.
DECAP_PULL = 4.0
TURNS = (0.0, 90.0, 180.0, 270.0)
#: How a board is placed: "analytic" (the wirelength placement in this
#: module) or "blocks" (floorplan first, ``floorplan``). The class places
#: analytically unless told otherwise; the placement job and
#: ``pcb_autoplace`` use ``DEFAULT_STRATEGY`` when given none.
#:
#: Blocks by default: over 153 boards, scrambled, placed and routed by the
#: engine, blocks with its fallback (``floorplan.FALLBACK_SHARE``) routed
#: 95.9% mean against 94.5%, 83 boards complete against 74, and it reads
#: as sub-circuits rather than a scatter. It left 8 more boards with body
#: overlaps; those show in the result and in the flagged moves.
STRATEGIES = ("analytic", "blocks")
DEFAULT_STRATEGY = "blocks"


@dataclass
class Part:
    ref: str
    side: str
    fixed: bool
    x: float
    y: float
    rot: float
    # Pad offsets in the part's frame (turn 0, its own side's mirror).
    pins: dict[int, tuple[float, float]] = field(default_factory=dict)
    # What the part keeps out, in its frame: polygons, each a "body"
    # (touching another body is fine) or "copper" (kept the clearance from
    # other copper).
    keep: list[list[tuple[float, float]]] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    area: float = 0.0
    idx: int = -1
    # Through-hole: its leads pierce the board, so it holds room on both
    # sides and must find room on both.
    through: bool = False


def _footprint(board: LayoutBoard, comp, frame: _Frame, pad_ids) -> list[tuple[list, str]]:
    """The part's courtyard (or body), its pads one by one, and the copper
    its footprint draws: (polygon in the part's frame, "body" or "copper").

    Always the pads as well: a 3D body need not cover its footprint's
    pads, and a connector placed by its body alone had its pads 27 mil
    into the next part's on the first board placed. And the footprint's
    own tracks, arcs and regions: a switch's footprint draws copper past
    its pads, and a part was placed on top of it."""
    out = []
    if comp.courtyard_source in ("courtyard", "body") and len(comp.courtyard) >= 3:
        out.append(([frame.to_local(x, y) for x, y in comp.courtyard], "body"))
    for i in pad_ids:
        p = board.pads[i]
        for layer in p.layers():
            s = p.shape_on(layer)
            if s is None:
                continue
            x0, y0, x1, y1 = s.bbox
            out.append(([frame.to_local(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))],
                        "copper"))
            break
    if not comp.ref:
        return out      # "" is every free track's part too, not this one's
    # Copper only: a footprint's silkscreen and mechanical drawings, taken
    # as copper, fenced whole boards off and left parts with nowhere to go.
    copper = set(board.copper_layers())
    shapes = [t.shape() for t in board.tracks if t.comp == comp.ref and t.layer in copper]
    shapes += [s for a in board.arcs if a.comp == comp.ref and a.layer in copper for s in a.shapes()]
    for s in shapes:
        ring = _capsule_ring(s)
        if ring:
            out.append(([frame.to_local(x, y) for x, y in ring], "copper"))
    for r in board.regions:
        if (r.comp == comp.ref and len(r.outline) >= 3 and r.kind != "pour_boundary"
                and r.layer in copper):
            out.append(([frame.to_local(x, y) for x, y in r.outline], "copper"))
    return out


def _capsule_ring(s) -> list[tuple[float, float]]:
    """A rectangle round a track: its length and width, caps included."""
    if s.kind == "point":
        (x, y), = s.pts
        r = s.r
        return [(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r)]
    if s.kind != "segment":
        return []
    (ax, ay), (bx, by) = s.pts
    ll = math.hypot(bx - ax, by - ay)
    if ll == 0:
        return []
    ux, uy = (bx - ax) / ll * s.r, (by - ay) / ll * s.r
    nx, ny = -uy, ux
    return [(ax - ux + nx, ay - uy + ny), (bx + ux + nx, by + uy + ny),
            (bx + ux - nx, by + uy - ny), (ax - ux - nx, ay - uy - ny)]


def _turn(pts, deg, mirror):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    out = []
    for x, y in pts:
        if mirror:
            x = -x
        out.append((x * c - y * s, x * s + y * c))
    return out


class Placer:
    def __init__(self, board: LayoutBoard, movable: set[str] | None = None,
                 spacing: float = 4.0, cell: float = 2.5, seed: int = 0,
                 decap_pull: float = 0.0, spread_density: float = 0.0,
                 strategy: str = "analytic", grid: float | None = None,
                 fallback: float | None = None):
        from ..bench import is_fixed

        if strategy not in STRATEGIES:
            raise ValueError(f"unknown placement strategy {strategy!r}; one of {', '.join(STRATEGIES)}")
        self.strategy = strategy
        # The blocks strategy's lattice, mils (None: its default, 0.25 mm),
        # and the share of flagged parts past which it hands the board to
        # the analytic strategy (None: its default; 0: never).
        self.grid = grid
        self.fallback = fallback
        self._args = dict(movable=movable, spacing=spacing, cell=cell, seed=seed,
                          decap_pull=decap_pull, spread_density=spread_density)
        self.board = board
        from ..rules import RuleSet as _RS
        # Parts sit at least a clearance apart: pads keep out their own
        # shapes, and two pads must be the clearance rule apart.
        spacing = max(spacing, _RS.from_board(board).default_clearance())
        self.spacing = spacing
        from ..rules import RuleSet
        edge = RuleSet.from_board(board).of_type("board_outline")
        self.edge_margin = edge[0].values.get("gap", 10.0) if edge else 10.0
        self.cell = cell
        self.rng = np.random.default_rng(seed)
        pads_of: dict[str, list[int]] = {}
        for i, p in enumerate(board.pads):
            if p.comp:
                pads_of.setdefault(p.comp, []).append(i)
        self.parts: list[Part] = []
        for c in board.components:
            frame = _Frame(c.x, c.y, c.rotation, c.side)
            fixed = (c.ref not in movable) if movable is not None else is_fixed(c)
            # A part with no designator cannot be moved by name, and its
            # pads read as belonging to no part: it stays where it is.
            fixed = fixed or not c.ref
            part = Part(c.ref, c.side, fixed, c.x, c.y, c.rotation)
            for i in pads_of.get(c.ref, []):
                part.pins[i] = frame.to_local(board.pads[i].x, board.pads[i].y)
            items = _footprint(board, c, frame, pads_of.get(c.ref, []))
            part.keep = [pl for pl, _ in items]
            part.kinds = [k for _, k in items]
            part.area = sum(geom.ring_area(k) for k in part.keep)
            part.through = any(not board.pads[i].is_smd for i in pads_of.get(c.ref, []))
            if not part.keep:
                part.fixed = True   # nothing to keep out, nothing to place
            part.idx = len(self.parts)
            self.parts.append(part)
        self.by_ref = {p.ref: p for p in self.parts}
        # Nets as lists of (part index, pad index); pads of no part are fixed.
        self.nets = []
        for net, pads in board.pads_by_net().items():
            if len(pads) < 2 or len(pads) > BIG_NET:
                continue
            self.nets.append([(self.by_ref[p.comp].idx if p.comp and p.comp in self.by_ref else -1,
                               self._pad_index(p)) for p in pads])
        xs = [q[0] for q in board.outline]
        ys = [q[1] for q in board.outline]
        self.box = (min(xs), min(ys), max(xs), max(ys))
        self.decap_pull = decap_pull
        self.spread_density = spread_density
        self.decaps = self._decoupling() if decap_pull > 0 else []
        # (part, pad, part, pad, weight): extra two-pin pulls, the decaps'.
        self.springs: list[tuple[int, int, int, int, float]] = []

    def _decoupling(self) -> list[tuple[int, int, list[tuple[int, int]]]]:
        """Parts that decouple an IC: two pads across two nets that each
        reach at least three pads, one of which reaches an IC. Their nets
        are a supply and its return, often both past BIG_NET and so left
        out of the wirelength: nothing pulled such a part anywhere, and it
        went wherever spreading put it. Each is (part, its pad on the
        supply, the IC pins on the supply). The supply is the smaller of
        the two nets that reaches an IC; the return is usually the largest
        net on the board. Known by what it connects, not by its name."""
        by_net = self.board.pads_by_net()
        out = []
        for part in self.parts:
            if part.fixed or len(part.pins) != 2:
                continue
            pads = [(i, self.board.pads[i]) for i in part.pins]
            nets = [p.net for _, p in pads]
            if not all(nets) or nets[0] == nets[1]:
                continue
            if min(len(by_net.get(n, [])) for n in nets) < 3:
                continue
            for i, pad in sorted(pads, key=lambda ip: len(by_net.get(ip[1].net, []))):
                ic = [(self.by_ref[q.comp].idx, self._pad_index(q)) for q in by_net[pad.net]
                      if q.comp and q.comp in self.by_ref and q.comp != part.ref
                      and len(self.by_ref[q.comp].pins) >= IC_PADS]
                if ic:
                    out.append((part.idx, i, ic))
                    break
        return out

    def _tie_decaps(self) -> None:
        """Tie each decoupling part to one IC pin on its supply: the
        nearest, with a pin already taken counting as further away, so a
        bank of them spreads over an IC's supply pins."""
        self.springs = []
        used: dict[tuple[int, int], int] = {}
        for pi, pad, pins in sorted(self.decaps, key=lambda d: self.parts[d[0]].ref):
            part = self.parts[pi]
            best = None
            for qi, qpad in pins:
                x, y = self.pin_xy(self.parts[qi], qpad)
                cost = math.hypot(x - part.x, y - part.y) + 150.0 * used.get((qi, qpad), 0)
                if best is None or cost < best[0]:
                    best = (cost, qi, qpad)
            _, qi, qpad = best
            used[(qi, qpad)] = used.get((qi, qpad), 0) + 1
            self.springs.append((pi, pad, qi, qpad, self.decap_pull))

    def _pad_index(self, pad) -> int:
        if not hasattr(self, "_pid"):
            self._pid = {id(p): i for i, p in enumerate(self.board.pads)}
        return self._pid[id(pad)]

    # -- pins ------------------------------------------------------------------

    def pin_xy(self, part: Part, pad: int, x=None, y=None, rot=None) -> tuple[float, float]:
        lx, ly = part.pins[pad]
        f = _Frame(part.x if x is None else x, part.y if y is None else y,
                   part.rot if rot is None else rot, part.side)
        return f.to_world(lx, ly)

    def hpwl(self) -> float:
        total = 0.0
        for net in self.nets:
            xs, ys = [], []
            for pi, pad in net:
                if pi < 0:
                    p = self.board.pads[pad]
                    xs.append(p.x)
                    ys.append(p.y)
                else:
                    x, y = self.pin_xy(self.parts[pi], pad)
                    xs.append(x)
                    ys.append(y)
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    # -- global ---------------------------------------------------------------

    def _solve(self, anchors: np.ndarray | None, alpha: float):
        """Quadratic wirelength with pins at part centres plus their
        offsets at the current turn, anchors pulling each movable part
        to a target with weight ``alpha``."""
        mov = [p for p in self.parts if not p.fixed]
        col = {p.idx: k for k, p in enumerate(mov)}
        n = len(mov)
        if not n:
            return
        for axis in (0, 1):
            rows, cols, vals = [], [], []
            rhs = np.zeros(n)
            diag = np.zeros(n)

            def pin(pi, pad):
                part = self.parts[pi] if pi >= 0 else None
                if part is None:
                    p = self.board.pads[pad]
                    return None, (p.x, p.y)[axis]
                off = self.pin_xy(part, pad, 0.0, 0.0)[axis]
                if part.fixed:
                    return None, (part.x, part.y)[axis] + off
                return col[part.idx], off

            def pull(a, b, w):
                (ia, oa), (ib, ob) = a, b
                if ia is None and ib is None:
                    return
                if ia is not None and ib is not None:
                    if ia == ib:
                        return
                    diag[ia] += w
                    diag[ib] += w
                    rows.extend([ia, ib])
                    cols.extend([ib, ia])
                    vals.extend([-w, -w])
                    rhs[ia] += w * (ob - oa)
                    rhs[ib] += w * (oa - ob)
                else:
                    i, oi, fixed_pos = (ia, oa, ob) if ia is not None else (ib, ob, oa)
                    diag[i] += w
                    rhs[i] += w * (fixed_pos - oi)

            for net in self.nets:
                k = len(net)
                w = 2.0 / (k * max(k - 1, 1)) if k > 2 else 1.0
                ends = [pin(pi, pad) for pi, pad in net]
                for a in range(k):
                    for b in range(a + 1, k):
                        pull(ends[a], ends[b], w)
            for pa, qa, pb, qb, w in self.springs:
                pull(pin(pa, qa), pin(pb, qb), w)
            # A weak pull to the board's middle keeps parts with no net
            # (or only big nets) from floating off; anchors do the rest.
            centre = ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)[axis]
            diag += 1e-3
            rhs += 1e-3 * centre
            if anchors is not None:
                diag += alpha
                rhs += alpha * anchors[:, axis]
            A = coo_matrix((vals + list(diag), (rows + list(range(n)), cols + list(range(n)))),
                           shape=(n, n)).tocsr()
            sol = spsolve(A, rhs)
            for k, p in enumerate(mov):
                if axis == 0:
                    p.x = float(sol[k])
                else:
                    p.y = float(sol[k])

    def _spread(self) -> np.ndarray:
        """Targets that spread the movable parts by area: recursive
        bisection, each half taking a share of the parts in proportion to
        its size (per side of the board), over the board, or with
        ``spread_density`` over a box they fill that share of (see
        ``_spread_box``)."""
        mov = [p for p in self.parts if not p.fixed]
        targets = np.zeros((len(mov), 2))
        index = {p.idx: k for k, p in enumerate(mov)}
        for side in ("top", "bottom"):
            group = [p for p in mov if p.side == side]
            self._bisect(group, self._spread_box(group), targets, index)
        return targets

    def _spread_box(self, group) -> tuple[float, float, float, float]:
        """Where a side's parts are spread: the box round where the
        wirelength put them, grown about their middle until they would
        fill ``spread_density`` of it, and kept on the board. The whole
        board when ``spread_density`` is 0."""
        bx0, by0, bx1, by1 = self.box
        if not group or self.spread_density <= 0:
            return self.box
        need = sum(max(p.area, 1.0) for p in group) / self.spread_density
        xs = [p.x for p in group]
        ys = [p.y for p in group]
        x0, x1 = max(bx0, min(xs)), min(bx1, max(xs))
        y0, y1 = max(by0, min(ys)), min(by1, max(ys))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        if w * h < need:
            # Grow the smaller side first, towards a square, then both.
            side = math.sqrt(need)
            w, h = max(w, min(side, need / h)), max(h, min(side, need / w))
            k = math.sqrt(need / (w * h)) if w * h < need else 1.0
            w, h = w * k, h * k
        w, h = min(w, bx1 - bx0), min(h, by1 - by0)
        x0 = min(max(cx - w / 2, bx0), bx1 - w)
        y0 = min(max(cy - h / 2, by0), by1 - h)
        return (x0, y0, x0 + w, y0 + h)

    def _bisect(self, group, box, targets, index):
        x0, y0, x1, y1 = box
        if not group:
            return
        if len(group) == 1 or (x1 - x0) < 1 or (y1 - y0) < 1:
            for p in group:
                targets[index[p.idx]] = (min(max(p.x, x0), x1), min(max(p.y, y0), y1))
            return
        vertical = (x1 - x0) >= (y1 - y0)
        key = (lambda p: p.x) if vertical else (lambda p: p.y)
        group = sorted(group, key=key)
        total = sum(max(p.area, 1.0) for p in group)
        half, acc, cut = total / 2, 0.0, 0
        for k, p in enumerate(group):
            acc += max(p.area, 1.0)
            if acc >= half:
                cut = k + 1
                break
        cut = min(max(cut, 1), len(group) - 1)
        share = sum(max(p.area, 1.0) for p in group[:cut]) / total
        if vertical:
            xm = x0 + (x1 - x0) * share
            self._bisect(group[:cut], (x0, y0, xm, y1), targets, index)
            self._bisect(group[cut:], (xm, y0, x1, y1), targets, index)
        else:
            ym = y0 + (y1 - y0) * share
            self._bisect(group[:cut], (x0, y0, x1, ym), targets, index)
            self._bisect(group[cut:], (x0, ym, x1, y1), targets, index)

    def global_place(self, rounds: int = 12) -> None:
        self._solve(None, 0.0)
        if self.decaps:
            self._tie_decaps()
            self._solve(None, 0.0)
        alpha = 0.02
        for _ in range(rounds):
            targets = self._spread()
            self._solve(targets, alpha)
            alpha *= 1.6
        targets = self._spread()
        for k, p in enumerate([p for p in self.parts if not p.fixed]):
            p.x, p.y = float(targets[k][0]), float(targets[k][1])
        # A decoupling part starts its search for a spot at its IC pin:
        # started from where spreading put it, half of a bank of four
        # ended 250 mil from the IC.
        for pi, _, qi, qpad, _ in self.springs:
            self.parts[pi].x, self.parts[pi].y = self.pin_xy(self.parts[qi], qpad)

    # -- legal -------------------------------------------------------------------

    def _raster(self, polys, x, y, rot, mirror, grow):
        """Cells a set of part-frame polygons covers at a pose, each grown
        by its own amount (``grow`` a number, or one per polygon), as (row
        slice start, col start, mask)."""
        grows = list(grow) if isinstance(grow, (list, tuple)) else [grow] * len(polys)
        g = max(grows) if grows else 0.0
        pts = [_turn(pl, rot, mirror) for pl in polys]
        allx = [q[0] + x for pl in pts for q in pl]
        ally = [q[1] + y for pl in pts for q in pl]
        c = self.cell
        i0 = int(math.floor((min(allx) - g - self.box[0]) / c))
        j0 = int(math.floor((min(ally) - g - self.box[1]) / c))
        i1 = int(math.ceil((max(allx) + g - self.box[0]) / c)) + 1
        j1 = int(math.ceil((max(ally) + g - self.box[1]) / c)) + 1
        mask = np.zeros((j1 - j0, i1 - i0), dtype=bool)
        # Each polygon on its own box: judged over the part's whole box, a
        # footprint of 1328 copper pieces took four minutes to stamp.
        for pl, gr in zip(pts, grows):
            s = geom.polygon([(qx + x, qy + y) for qx, qy in pl])
            bx0, by0, bx1, by1 = s.bbox
            a0 = max(0, int(math.floor((bx0 - gr - self.box[0]) / c)) - i0)
            b0 = max(0, int(math.floor((by0 - gr - self.box[1]) / c)) - j0)
            a1 = min(i1 - i0, int(math.ceil((bx1 + gr - self.box[0]) / c)) + 1 - i0)
            b1 = min(j1 - j0, int(math.ceil((by1 + gr - self.box[1]) / c)) + 1 - j0)
            if a0 >= a1 or b0 >= b1:
                continue
            X = self.box[0] + (np.arange(i0 + a0, i0 + a1) + 0.5) * c
            Y = self.box[1] + (np.arange(j0 + b0, j0 + b1) + 0.5) * c
            d = shape_distance(geom.Shape(s.kind, s.pts, gr), X[None, :], Y[:, None])
            mask[b0:b1, a0:a1] |= d <= 0.0
        return j0, i0, mask

    def _raster_shape(self, s: geom.Shape, grow: float):
        """Cells a board-frame shape covers, grown by ``grow``, as (row
        start, col start, mask)."""
        c = self.cell
        x0, y0, x1, y1 = s.bbox
        i0 = int(math.floor((x0 - grow - self.box[0]) / c))
        j0 = int(math.floor((y0 - grow - self.box[1]) / c))
        i1 = int(math.ceil((x1 + grow - self.box[0]) / c)) + 1
        j1 = int(math.ceil((y1 + grow - self.box[1]) / c)) + 1
        X = self.box[0] + (np.arange(i0, i1) + 0.5) * c
        Y = self.box[1] + (np.arange(j0, j1) + 0.5) * c
        d = shape_distance(geom.Shape(s.kind, s.pts, s.r + grow, s.holes), X[None, :], Y[:, None])
        return j0, i0, d <= 0.0

    def _grows(self, p: Part) -> list[float]:
        """How far each of a part's polygons is grown on the raster. A cell
        counts by its centre, so everything grows by half a cell's
        diagonal; copper by half the clearance as well, so two parts'
        copper stays a clearance apart, where bodies may touch. Kept a
        clearance apart too, a row of small passives that a person fits
        body to body has no room left for its last part."""
        c = self.cell
        return [0.71 * c + (self.spacing / 2 if k == "copper" else 0.0) for k in p.kinds]

    def _cell_xy(self, J: int, I: int) -> tuple[float, float]:
        return self.box[0] + (I + 0.5) * self.cell, self.box[1] + (J + 0.5) * self.cell

    def _masks(self, p: Part):
        """The part's raster at each turn, placed on cell (0, 0): offsets
        and mask. On cell (J, I) it is the same mask moved by (J, I)."""
        if not hasattr(p, "_masks"):
            x, y = self._cell_xy(0, 0)
            p._masks = {rot: self._raster(p.keep, x, y, rot, p.side == BOTTOM, self._grows(p))
                        for rot in TURNS}
        return p._masks

    def _masks_cu(self, p: Part):
        """As ``_masks``, of the part's copper alone (None with no copper)."""
        if not hasattr(p, "_masks_cu"):
            polys = [pl for pl, k in zip(p.keep, p.kinds) if k == "copper"]
            grows = [g for g, k in zip(self._grows(p), p.kinds) if k == "copper"]
            x, y = self._cell_xy(0, 0)
            p._masks_cu = {rot: (self._raster(polys, x, y, rot, p.side == BOTTOM, grows)
                                 if polys else None) for rot in TURNS}
        return p._masks_cu

    def _cell_of(self, x: float, y: float) -> tuple[int, int]:
        return (int(math.floor((y - self.box[1]) / self.cell)),
                int(math.floor((x - self.box[0]) / self.cell)))

    def _fits(self, occ, j0, i0, mask) -> bool:
        H, W = occ.shape
        h, w = mask.shape
        if j0 < 0 or i0 < 0 or j0 + h > H or i0 + w > W:
            return False
        return not (occ[j0:j0 + h, i0:i0 + w] & mask).any()

    def _occupancy(self):
        """What is closed to parts before any movable one is placed, per
        side: everything a part keeps out, and copper alone. Off the board,
        within the board-outline rule of its edge, cutouts, pads of no
        part, keepouts, the board's own tracks and vias, and the fixed
        parts."""
        c = self.cell
        W = int(math.ceil((self.box[2] - self.box[0]) / c)) + 1
        H = int(math.ceil((self.box[3] - self.box[1]) / c)) + 1
        spec = GridSpec(self.box[0] + c / 2, self.box[1] + c / 2, c, W, H)
        inside = fill_polygon(spec, [self.board.outline] + list(self.board.cutouts))
        # Keep parts off the edge by the board-outline rule (at least half
        # a cell, since a cell counts as inside by its centre alone).
        from scipy.ndimage import distance_transform_edt
        margin = max(self.edge_margin, c / 2)
        # Padded with outside all round: on a rectangular board the edge IS
        # the raster's border, and a transform that cannot see past it
        # found no edge to keep away from.
        dist = distance_transform_edt(np.pad(inside, 1))[1:-1, 1:-1]
        inside &= dist * c > margin
        occ = {s: ~inside for s in ("top", "bottom")}
        occ_cu = {s: ~inside for s in ("top", "bottom")}
        # Pads that belong to no part (a mounting hole, a test pad placed
        # on its own) stay where they are and keep their room.
        grow = self.spacing / 2 + 0.71 * c
        for pad in self.board.pads:
            if pad.comp:
                continue
            for layer in pad.layers():
                sh = pad.shape_on(layer)
                if sh is None:
                    continue
                x0, y0, x1, y1 = sh.bbox
                ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                j0, i0, m = self._raster([ring], 0.0, 0.0, 0.0, False, grow)
                sides = ("top", "bottom") if not pad.is_smd else \
                    ("bottom",) if layer == "BottomLayer" else ("top",)
                for side in sides:
                    self._stamp(occ[side], j0, i0, m)
                    self._stamp(occ_cu[side], j0, i0, m)
                break
        # Keepouts, and copper that stays where it is when parts move: a
        # part's pads must keep the clearance from it, like from a part's.
        from .floorplan import free_copper, keepouts
        for found, gr in ((keepouts(self.board), 0.71 * c),
                          (free_copper(self.board), grow)):
            for shape, sides in found:
                j0, i0, m = self._raster_shape(shape, gr)
                for side in sides:
                    self._stamp(occ[side], j0, i0, m)
                    self._stamp(occ_cu[side], j0, i0, m)
        for p in self.parts:
            if p.fixed and p.keep:
                # A cell is marked by its centre: grown by half its
                # diagonal, the raster covers the whole outline.
                grows = self._grows(p)
                j0, i0, m = self._raster(p.keep, p.x, p.y, p.rot, p.side == BOTTOM, grows)
                cu = [(pl, g) for pl, g, k in zip(p.keep, grows, p.kinds) if k == "copper"]
                for side in self._sides(p):
                    self._stamp(occ[side], j0, i0, m)
                if cu:
                    j0, i0, m = self._raster([pl for pl, _ in cu], p.x, p.y, p.rot,
                                             p.side == BOTTOM, [g for _, g in cu])
                    for side in self._sides(p):
                        self._stamp(occ_cu[side], j0, i0, m)
        return occ, occ_cu

    def legalize(self, search: float = 800.0) -> list[str]:
        """Every movable part to a free spot near where global placement
        put it; returns the parts that fit nowhere.

        Two rasters are kept per side: what every part keeps out, and its
        copper alone. A part that fits nowhere whole is placed by its
        copper, its body allowed over other bodies: people do this on
        dense boards (a 3D body drawn larger than the part, a part under
        another's overhang), and left where spreading put it, such a part
        lay on other parts' copper and shorted it. Those parts are listed
        in ``body_overlaps``."""
        self.occ, self.occ_cu = self._occupancy()
        failed = []
        self.body_overlaps = []
        order = sorted((p for p in self.parts if not p.fixed), key=lambda p: -p.area)
        # An IC's decoupling parts go right after it (or first, when the IC
        # is fixed), before the rest crowd its pins: placed smallest-last,
        # they found the room beside the IC taken and ended well away from
        # the pins they serve.
        mine: dict[int, list[Part]] = {}
        for pi, _, qi, _, _ in self.springs:
            mine.setdefault(qi, []).append(self.parts[pi])
        early = [q for ps in (mine[qi] for qi in mine if self.parts[qi].fixed) for q in ps]
        tied_parts = {id(q) for ps in mine.values() for q in ps}
        rest = []
        for p in order:
            if id(p) in tied_parts:
                continue
            rest.append(p)
            rest.extend(mine.get(p.idx, []))
        order = early + rest
        full = math.hypot(self.box[2] - self.box[0], self.box[3] - self.box[1])
        tied = {pi: (qi, qpad) for pi, _, qi, qpad, _ in self.springs}
        for p in order:
            if p.idx in tied:
                # From its IC pin where the IC now stands: legalized first,
                # being larger, an IC can land far from where spreading had
                # it, and its decoupling parts searched round the old spot.
                qi, qpad = tied[p.idx]
                p.x, p.y = self.pin_xy(self.parts[qi], qpad)
            pose = (self._legal_one(p, search) or self._legal_one(p, full)
                    or self._legal_exact(p, self.occ, self._masks(p)))
            if pose is not None:
                self._place(p, *pose)
                continue
            pose = self._legal_exact(p, self.occ_cu, self._masks_cu(p))
            if pose is not None:
                self._place(p, *pose)
                p.overlapping = True
                self.body_overlaps.append(p.ref)
                continue
            failed.append(p.ref)
        return failed

    def _place(self, p: Part, J: int, I: int, rot: float) -> None:
        """Put the part on cell (J, I) at a turn, into both rasters."""
        p.x, p.y = self._cell_xy(J, I)
        p.rot = rot
        p.cell = (J, I)
        oj, oi, m = self._masks(p)[rot]
        cu = self._masks_cu(p)[rot]
        for side in self._sides(p):
            self._stamp(self.occ[side], J + oj, I + oi, m)
            if cu is not None:
                self._stamp(self.occ_cu[side], J + cu[0], I + cu[1], cu[2])

    def _lift(self, p: Part) -> None:
        """Take a part placed whole back out of both rasters."""
        J, I = p.cell
        oj, oi, m = self._masks(p)[p.rot]
        cu = self._masks_cu(p)[p.rot]
        for side in self._sides(p):
            self._unstamp(self.occ[side], J + oj, I + oi, m)
            if cu is not None:
                self._unstamp(self.occ_cu[side], J + cu[0], I + cu[1], cu[2])

    def _legal_one(self, p: Part, search: float):
        """The best pose within ``search`` of the part's spot, stepping in
        a quarter of its size, or None."""
        c = self.cell
        best = None
        gj, gi = self._cell_of(p.x, p.y)
        gx, gy = p.x, p.y
        masks = self._masks(p)
        # Search in steps a fraction of the part's size: a big part does
        # not need to try every cell.
        size = math.sqrt(max(p.area, c * c))
        step = max(1, int(size / (4 * c)))
        found_at = None
        for r in range(0, int(search / (c * step)) + 1):
            for di, dj in self._ring(r):
                J, I = gj + dj * step, gi + di * step
                x, y = self._cell_xy(J, I)
                for rot, (oj, oi, m) in masks.items():
                    if not self._fits_all(self.occ, p, J + oj, I + oi, m):
                        continue
                    cost = self._local_wire(p, x, y, rot) + 0.5 * math.hypot(x - gx, y - gy)
                    if best is None or cost < best[0]:
                        best = (cost, J, I, rot)
            if best is not None:
                found_at = found_at if found_at is not None else r
                if r >= found_at + 2:
                    break
        return None if best is None else best[1:]

    #: Of the legal spots an exact search finds, this many nearest the
    #: part's target are priced.
    EXACT_CANDIDATES = 200

    def _legal_exact(self, p: Part, occ, masks):
        """Every spot the part fits, found at once for each turn: the
        overlap of its outline with what is occupied, at every offset, is
        a correlation. Used when the stepped search finds nothing: its
        steps are a quarter of the part's size, and on a small crowded
        board they stepped over the one spot a transistor fitted, where
        the person had put it. The best pose, or None."""
        from scipy.signal import fftconvolve

        busy = occ[p.side] if not p.through else (occ["top"] | occ["bottom"])
        busy = busy.astype(np.float32)
        gx, gy = p.x, p.y
        best = None
        for rot, mk in masks.items():
            if mk is None:
                continue
            oj, oi, m = mk
            h, w = m.shape
            if h > busy.shape[0] or w > busy.shape[1]:
                continue
            hit = fftconvolve(busy, m[::-1, ::-1].astype(np.float32), mode="valid")
            aa, bb = np.nonzero(hit < 0.5)
            if not len(aa):
                continue
            J, I = aa - oj, bb - oi
            x = self.box[0] + (I + 0.5) * self.cell
            y = self.box[1] + (J + 0.5) * self.cell
            d = np.hypot(x - gx, y - gy)
            for k in np.argsort(d)[:self.EXACT_CANDIDATES]:
                cost = self._local_wire(p, float(x[k]), float(y[k]), rot) + 0.5 * float(d[k])
                if best is None or cost < best[0]:
                    best = (cost, int(J[k]), int(I[k]), rot)
        return None if best is None else best[1:]

    @staticmethod
    def _sides(p: Part):
        return ("top", "bottom") if p.through else (p.side,)

    def _fits_all(self, occ, p: Part, j0, i0, m) -> bool:
        return all(self._fits(occ[s], j0, i0, m) for s in self._sides(p))

    @staticmethod
    def _ring(r: int):
        if r == 0:
            return [(0, 0)]
        out = []
        for d in range(-r, r + 1):
            out += [(d, -r), (d, r)]
        for d in range(-r + 1, r):
            out += [(-r, d), (r, d)]
        return out

    @staticmethod
    def _stamp(occ, j0, i0, m):
        H, W = occ.shape
        h, w = m.shape
        ja, ia = max(j0, 0), max(i0, 0)
        jb, ib = min(j0 + h, H), min(i0 + w, W)
        if ja < jb and ia < ib:
            occ[ja:jb, ia:ib] |= m[ja - j0:jb - j0, ia - i0:ib - i0]

    def _nets_of(self, p: Part):
        if not hasattr(self, "_net_index"):
            self._net_index = {}
            for k, net in enumerate(self.nets):
                for pi, _ in net:
                    if pi >= 0:
                        self._net_index.setdefault(pi, set()).add(k)
        return self._net_index.get(p.idx, set())

    def _local_wire(self, p: Part, x, y, rot) -> float:
        """Wirelength of the nets touching ``p`` if it stood at this pose,
        and of its decoupling pulls, weighted."""
        total = 0.0
        for pa, qa, pb, qb, w in self.springs:
            if p.idx not in (pa, pb):
                continue
            ax, ay = (self.pin_xy(p, qa, x, y, rot) if pa == p.idx else self.pin_xy(self.parts[pa], qa))
            bx, by = (self.pin_xy(p, qb, x, y, rot) if pb == p.idx else self.pin_xy(self.parts[pb], qb))
            total += w * (abs(ax - bx) + abs(ay - by))
        for k in self._nets_of(p):
            xs, ys = [], []
            for pi, pad in self.nets[k]:
                if pi == p.idx:
                    qx, qy = self.pin_xy(p, pad, x, y, rot)
                elif pi < 0:
                    b = self.board.pads[pad]
                    qx, qy = b.x, b.y
                else:
                    qx, qy = self.pin_xy(self.parts[pi], pad)
                xs.append(qx)
                ys.append(qy)
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    # -- refine ----------------------------------------------------------------

    def refine(self, passes: int = 2, reach: float = 60.0) -> None:
        """Small moves and turns that shorten a part's wire and still fit.
        A part placed with its body over another's stays: lifted, it would
        clear cells the other part still covers."""
        c = self.cell
        k = max(1, int(round(reach / 2 / c)))
        for _ in range(passes):
            for p in sorted((p for p in self.parts if not p.fixed), key=lambda p: p.area):
                if getattr(p, "overlapping", False) or getattr(p, "cell", None) is None:
                    continue
                masks = self._masks(p)
                self._lift(p)
                J, I = p.cell
                best = (self._local_wire(p, p.x, p.y, p.rot), J, I, p.rot)
                for dj in (-2, -1, 0, 1, 2):
                    for di in (-2, -1, 0, 1, 2):
                        JJ, II = J + dj * k, I + di * k
                        x, y = self._cell_xy(JJ, II)
                        for rot, (qj, qi, mm) in masks.items():
                            cost = self._local_wire(p, x, y, rot)
                            if cost >= best[0] - 1e-6:
                                continue
                            if self._fits_all(self.occ, p, JJ + qj, II + qi, mm):
                                best = (cost, JJ, II, rot)
                self._place(p, *best[1:])

    @staticmethod
    def _unstamp(occ, j0, i0, m):
        H, W = occ.shape
        h, w = m.shape
        ja, ia = max(j0, 0), max(i0, 0)
        jb, ib = min(j0 + h, H), min(i0 + w, W)
        if ja < jb and ia < ib:
            occ[ja:jb, ia:ib] &= ~m[ja - j0:jb - j0, ia - i0:ib - i0]

    # -- result ----------------------------------------------------------------

    def apply(self) -> LayoutBoard:
        from ..bench import copy_board
        out = copy_board(self.board)
        for p in self.parts:
            if not p.fixed:
                set_pose(out, p.ref, x=p.x, y=p.y, rotation=p.rot)
        return out

    def run(self) -> dict:
        import time
        if self.strategy == "blocks":
            from .floorplan import FALLBACK_SHARE, GRID, place_blocks
            rep = place_blocks(self, grid=self.grid or GRID)
            share = FALLBACK_SHARE if self.fallback is None else self.fallback
            movable = sum(1 for p in self.parts if not p.fixed)
            if share and movable and len(rep["flagged_moves"]) > share * movable:
                return self._fall_back(rep, len(rep["flagged_moves"]) / movable)
            return rep
        t0 = time.perf_counter()
        self.global_place()
        failed = self.legalize()
        self.refine()
        return {"failed": failed, "body_overlaps": list(self.body_overlaps),
                "seconds": round(time.perf_counter() - t0, 1), "hpwl": round(self.hpwl(), 1)}

    def _fall_back(self, rep: dict, share: float) -> dict:
        """The board placed analytically instead, when the floorplan left
        too many parts far from their blocks (``FALLBACK_SHARE``): its
        blocks no longer held together, and wirelength placement routed
        those boards better. Said so in the result."""
        other = Placer(self.board, strategy="analytic", **self._args)
        out = other.run()
        for p in other.parts:
            if not p.fixed:
                mine = self.by_ref[p.ref]
                mine.x, mine.y, mine.rot = p.x, p.y, p.rot
        out["strategy"] = "analytic"
        out["fallback"] = {"from": "blocks", "flagged_share": round(share, 3),
                           "flagged_moves": len(rep["flagged_moves"]), "blocks": len(rep["blocks"])}
        out["seconds"] = round(rep["seconds"] + out["seconds"], 1)
        return out
