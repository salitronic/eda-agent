# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Floorplan-first placement: the blocks first, then the parts in them.

A cost-driven placer shortens wire and leaves a scatter; a person judges
a layout first by whether it is organised. So the board is planned the
way a person plans it:

1. **The fixed skeleton.** The outline and its cutouts, keepouts, pads of
   no part, and the fixed parts where they stand. A block headed by a
   fixed part (a connector, usually) is built round it there, its columns
   on the sides that face open board.
2. **Blocks as rectangles.** Each other block (``blocks``) is laid out on
   its own (``compose``), and what its parts cover plus a margin is its
   rectangle. A set of identical channels is one rectangle of identical
   tiles in a strict grid, in channel order.
3. **Rectangles in signal-flow order.** One at a time, the block most
   wired to what is already placed goes next (the connectors' blocks are
   placed first, so this runs connector, protection, conditioning,
   processing). Each time, the centres of the blocks still to place are
   solved as a quadratic over the nets the blocks share, placed blocks
   and fixed parts holding it; the next block takes the free spot nearest
   its solved centre, turned the quarter that brings its pins nearest
   what they connect to. Rectangles keep off each other, the fixed parts,
   keepouts and holes, inside the outline. On a board too crowded for
   that, the margin shrinks, then what must be free is the block's parts
   rather than its whole rectangle (``packed``), then the block is drawn
   again with its parts closer, and failing that it goes to the least
   crowded spot (``overflow``).
4. **Parts.** Each part is put where its block says. When parts collide,
   the whole block moves to the nearest spot where it fits, then each of
   its groups (the head, one side's columns), and only then single parts;
   every move is recorded with its distance, and a move over ``FLAG_AT``
   is reported as a floorplan problem rather than accepted quietly.

Positions are on a ``grid`` lattice (0.25 mm by default) through the
board origin; the legaliser's raster is a quarter of that, aligned so the
lattice points are cell centres.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .. import geom
from ..route.grid import GridSpec, fill_polygon, shape_distance
from .blocks import ROLE_ORDER, Block, Netlist, find_blocks, natural, supply_nets
from .compose import Layout, Shapes, compose
from .placer import BIG_NET, TURNS
from ...units import MILS_PER_MM

MM = MILS_PER_MM
#: Parts are snapped to this lattice, mils.
GRID = 0.25 * MM
#: Space between two parts' outlines in a block, mils.
GAP = 0.3 * MM
#: The space a block that fits nowhere is drawn again with: just more
#: than the legaliser's raster rounds two outlines out by.
TIGHT_GAP = 0.1 * MM
#: Room kept round each block's rectangle, mils (twice this between two).
MARGIN = 0.5 * MM
#: A legaliser move longer than this (mils) is a floorplan problem.
FLAG_AT = 80.0
#: When more than this share of the movable parts is flagged, the blocks
#: did not fit the board and it is placed by the analytic strategy instead
#: (``Placer(fallback=...)``; the result says so). Over the benchmark
#: boards, placed then routed, the boards where the blocks routed worse
#: were the ones with about two parts in five flagged, and handing those
#: over routed better than either strategy alone; between a fifth and two
#: fifths the gain hardly changed.
FALLBACK_SHARE = 0.25
#: Cells in the floorplan's raster, about.
FLOOR_CELLS = 200000

SIDES = ("top", "bottom")


@dataclass
class Unit:
    """What the floorplan places as one rectangle: a block, or a set of
    identical channels as a grid of tiles."""

    blocks: list[Block]
    tile: Layout                    # the first block's layout, unturned
    sides: tuple[str, ...]
    layouts: list[Layout] = field(default_factory=list)   # per block, unturned
    turn: float = 0.0
    cols: int = 1
    origins: list[tuple[float, float]] = field(default_factory=list)
    placed: bool = False
    packed: bool = False
    overflow: bool = False

    @property
    def area(self) -> float:
        w, h = self.tile.size
        return w * h * len(self.blocks)


def place_blocks(placer, grid: float = GRID, gap: float = GAP, margin: float = MARGIN,
                 flag_at: float = FLAG_AT) -> dict:
    """Place the placer's movable parts by blocks; the placer's parts end
    where they go. Returns what ``Placer.run`` returns, with the blocks,
    every legaliser move and the flagged ones."""
    t0 = time.perf_counter()
    _align(placer, grid)
    movable = {p.ref for p in placer.parts if not p.fixed}
    blocks = find_blocks(placer.board, movable)
    sh = Shapes(placer)
    fp = Floorplan(placer, blocks, sh, grid, gap, margin)
    fp.run()
    leg = Legaliser(placer, grid, flag_at)
    done = set()
    for name in fp.sequence:
        b, poses, groups = fp.records[name]
        rank = {m.ref: (m.rank, ROLE_ORDER[m.role], natural(m.ref)) for m in b.members}
        refs = sorted((r for r in poses if r in movable), key=lambda r: rank.get(r, (9, 9, natural(r))))
        leg.put_block([(placer.by_ref[r], *poses[r]) for r in refs], groups)
        done.update(refs)
    ox, oy = placer.lattice
    for r in sorted(movable - done, key=natural):
        p = placer.by_ref[r]
        leg.put(p, _snap_abs(p.x, ox, grid), _snap_abs(p.y, oy, grid), p.rot % 360.0)
    return {
        "failed": leg.failed, "body_overlaps": leg.body_overlaps,
        "seconds": round(time.perf_counter() - t0, 1), "hpwl": round(placer.hpwl(), 1),
        "strategy": "blocks",
        "blocks": fp.report(),
        "legaliser_moves": leg.moves,
        "flagged_moves": [m for m in leg.moves if m["distance"] > flag_at],
        "floorplan_packed": [b.name for u in fp.units if u.packed for b in u.blocks],
        "floorplan_overflow": [b.name for u in fp.units if u.overflow for b in u.blocks],
    }


def _align(placer, grid: float) -> None:
    """A raster a quarter of the lattice, its cell centres on the lattice
    points, so a part whose origin is on the lattice sits on a cell
    exactly and its masks stamp without rounding."""
    ox, oy = (list(placer.board.meta.get("origin") or [0.0, 0.0]) + [0.0, 0.0])[:2]
    c = grid / 4
    x0, y0, x1, y1 = placer.box
    lx = ox + math.floor((x0 - ox) / grid) * grid
    ly = oy + math.floor((y0 - oy) / grid) * grid
    placer.cell = c
    placer.box = (lx - c / 2, ly - c / 2, x1, y1)
    placer.lattice = (ox, oy)


def _snap_abs(v: float, o: float, grid: float) -> float:
    return o + round((v - o) / grid) * grid


def _up(v: float, grid: float) -> float:
    return math.ceil(v / grid - 1e-9) * grid


def _fast(n: int) -> int:
    """The least size of at least ``n`` with no prime factor above 5: an
    FFT of a size with a large prime factor is many times slower."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


class Correlator:
    """How much of a mask lands on busy cells, at every offset where it
    lies wholly on the raster (a "valid" correlation), with the raster
    transformed once for many masks. A circular transform as big as the
    raster is enough: the wrap only reaches the offsets where the mask
    would hang off the raster, which are not kept. numpy's FFT, since
    scipy.signal took seconds just to import on a loaded machine."""

    def __init__(self, busy: np.ndarray):
        self.H, self.W = busy.shape
        self.shape = (_fast(self.H), _fast(self.W))
        self.F = np.fft.rfft2(busy, s=self.shape)

    def valid(self, mask: np.ndarray) -> np.ndarray:
        h, w = mask.shape
        full = np.fft.irfft2(self.F * np.fft.rfft2(mask[::-1, ::-1], s=self.shape), s=self.shape)
        return full[h - 1:self.H, w - 1:self.W]


def keepouts(board) -> list[tuple[geom.Shape, tuple[str, ...]]]:
    """Keepout regions and keepout tracks and arcs, with the sides of the
    board they close to parts: a top or bottom copper layer its own side,
    the keep-out layer (and multi-layer) both. Keepouts on inner layers
    close nothing to parts."""
    def sides(layer):
        if layer == "TopLayer":
            return ("top",)
        if layer == "BottomLayer":
            return ("bottom",)
        if layer in ("KeepOutLayer", "MultiLayer"):
            return SIDES
        return ()

    out = []
    for r in board.regions:
        if (r.keepout or r.kind == "keepout") and len(r.outline) >= 3 and sides(r.layer):
            out.append((geom.polygon(r.outline, r.holes), sides(r.layer)))
    for t in board.tracks:
        if t.keepout and sides(t.layer):
            out.append((t.shape(), sides(t.layer)))
    for a in board.arcs:
        if a.keepout and sides(a.layer):
            out += [(s, sides(a.layer)) for s in a.shapes()]
    return out


def free_copper(board) -> list[tuple[geom.Shape, tuple[str, ...]]]:
    """Copper already on the board that belongs to no part, with the sides
    it closes to parts: tracks, arcs, fills and regions on the top or
    bottom layer, and vias on the outer layers they reach. Moving a part
    leaves this copper where it is; placed on it, the part's pads short it
    (two moved resistors took a board's batch DRC from 0 to 10, a short
    and a clearance among them). Poured copper is left out: the pour is
    poured again round the parts. Copper on inner layers matters only to
    through-hole parts and is left to the DRC of the placed board."""
    outer = {"TopLayer": ("top",), "BottomLayer": ("bottom",)}
    out = []
    for t in board.tracks:
        if not t.comp and not t.keepout and t.layer in outer:
            out.append((t.shape(), outer[t.layer]))
    for a in board.arcs:
        if not a.comp and not a.keepout and a.layer in outer:
            out += [(s, outer[a.layer]) for s in a.shapes()]
    for r in board.regions:
        if (not r.comp and r.kind == "copper" and r.layer in outer
                and len(r.outline) >= 3):
            out.append((geom.polygon(r.outline, r.holes), outer[r.layer]))
    copper = board.copper_layers()
    for v in board.vias:
        if v.comp:
            continue
        spanned = board.layers_between(v.low_layer, v.high_layer)
        sides = tuple(s for layer, s in (("TopLayer", "top"), ("BottomLayer", "bottom"))
                      if layer in spanned and layer in copper)
        if sides:
            out.append((geom.circle(v.x, v.y, v.diameter), sides))
    return out


# ---------------------------------------------------------------------------
# Floorplan
# ---------------------------------------------------------------------------

class Floorplan:
    def __init__(self, placer, blocks: list[Block], sh: Shapes, grid: float, gap: float,
                 margin: float):
        self.pl = placer
        self.board = placer.board
        self.blocks = blocks
        self.sh = sh
        self.grid, self.gap, self.margin = grid, gap, margin
        self.ox_l, self.oy_l = placer.lattice
        b = self.board
        xs = [q[0] for q in b.outline]
        ys = [q[1] for q in b.outline]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.fc = max(grid / 2, math.sqrt((self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1])
                                          / FLOOR_CELLS))
        self.W = int(math.ceil((self.bbox[2] - self.bbox[0]) / self.fc)) + 1
        self.H = int(math.ceil((self.bbox[3] - self.bbox[1]) / self.fc)) + 1
        self.occ = self._skeleton()
        # Per block: (block, part -> (x, y, turn) on the board, part -> group).
        self.records: dict[str, tuple[Block, dict, dict]] = {}
        self.sequence: list[str] = []
        self.rects: dict[str, tuple[float, float, float, float]] = {}
        nl = Netlist(b)
        power = supply_nets(nl)
        # Ordinary nets: (component or "", pad index) of every pad on them.
        self.nets = [[(b.pads[i].comp, i) for i in ps] for n, ps in nl.by_net.items()
                     if n not in power and 2 <= len(ps) <= BIG_NET]
        self.unit_of: dict[str, int] = {}
        self.units: list[Unit] = []
        self.where: dict[str, tuple[float, float, float]] = {}

    # -- the skeleton ------------------------------------------------------

    def _skeleton(self) -> dict[str, np.ndarray]:
        """Cells closed to blocks, per side: off the board or within the
        outline rule of its edge, cutouts, keepouts, pads of no part,
        fixed parts. A cell is closed when anything reaches into it."""
        from scipy.ndimage import distance_transform_edt

        fc = self.fc
        spec = GridSpec(self.bbox[0] + fc / 2, self.bbox[1] + fc / 2, fc, self.W, self.H)
        inside = fill_polygon(spec, [self.board.outline] + list(self.board.cutouts))
        dist = distance_transform_edt(np.pad(inside, 1))[1:-1, 1:-1]
        inside &= dist * fc > max(self.pl.edge_margin, 0.0) + fc / 2
        occ = {s: ~inside for s in SIDES}
        half = 0.71 * fc
        for pad in self.board.pads:
            if pad.comp:
                continue
            for layer in pad.layers():
                s = pad.shape_on(layer)
                if s is None:
                    continue
                sides = SIDES if not pad.is_smd else ("bottom",) if layer == "BottomLayer" else ("top",)
                self._stamp_shape(occ, sides, s, self.pl.spacing / 2 + half)
                break
        for p in self.pl.parts:
            if not p.fixed or not p.keep:
                continue
            for pl, kind in zip(p.keep, p.kinds):
                pts = [self._world(p, q) for q in pl]
                grow = half + (self.pl.spacing / 2 if kind == "copper" else 0.0)
                self._stamp_shape(occ, self.pl._sides(p), geom.polygon(pts), grow)
        for shape, sides in keepouts(self.board):
            self._stamp_shape(occ, sides, shape, half)
        for shape, sides in free_copper(self.board):
            self._stamp_shape(occ, sides, shape, self.pl.spacing / 2 + half)
        return occ

    def _world(self, p, q):
        from .transform import _Frame
        return _Frame(p.x, p.y, p.rot, p.side).to_world(*q)

    def _stamp_shape(self, occ, sides, s: geom.Shape, grow: float) -> None:
        fc = self.fc
        x0, y0, x1, y1 = s.bbox
        i0 = max(0, int(math.floor((x0 - grow - self.bbox[0]) / fc)))
        j0 = max(0, int(math.floor((y0 - grow - self.bbox[1]) / fc)))
        i1 = min(self.W, int(math.ceil((x1 + grow - self.bbox[0]) / fc)) + 1)
        j1 = min(self.H, int(math.ceil((y1 + grow - self.bbox[1]) / fc)) + 1)
        if i0 >= i1 or j0 >= j1:
            return
        X = self.bbox[0] + (np.arange(i0, i1) + 0.5) * fc
        Y = self.bbox[1] + (np.arange(j0, j1) + 0.5) * fc
        d = shape_distance(geom.Shape(s.kind, s.pts, s.r + grow, s.holes), X[None, :], Y[:, None])
        m = d <= 0.0
        for side in sides:
            occ[side][j0:j1, i0:i1] |= m

    def _cells(self, box):
        """The raster cells a box covers, clipped: (j0, j1, i0, i1)."""
        fc = self.fc
        i0 = max(0, int(math.floor((box[0] - self.bbox[0]) / fc)))
        j0 = max(0, int(math.floor((box[1] - self.bbox[1]) / fc)))
        i1 = min(self.W, int(math.ceil((box[2] - self.bbox[0]) / fc)))
        j1 = min(self.H, int(math.ceil((box[3] - self.bbox[1]) / fc)))
        return j0, j1, i0, i1

    def _close(self, box, sides) -> None:
        j0, j1, i0, i1 = self._cells(box)
        for s in sides:
            if j0 < j1 and i0 < i1:
                self.occ[s][j0:j1, i0:i1] = True

    def _block_sides(self, block: Block) -> tuple[str, ...]:
        sides = set()
        for r in block.refs():
            if r == block.head and block.anchored:
                continue
            sides |= set(self.pl._sides(self.pl.by_ref[r]))
        return tuple(s for s in SIDES if s in sides) or (self.pl.by_ref[block.refs()[0]].side,)

    # -- the run -------------------------------------------------------------

    def run(self) -> None:
        for b in self.blocks:
            if b.anchored:
                self._anchor(b)
        self._make_units()
        while True:
            todo = [k for k, u in enumerate(self.units) if not u.placed]
            if not todo:
                break
            centres = self._solve(todo)
            k = self._next(todo)
            self._place_unit(k, centres)
        self._turn_half()

    def _anchor(self, b: Block) -> None:
        """A block round a fixed head, built where the head stands, its
        columns on the head's sides that face open board."""
        head = self.pl.by_ref[b.head]
        found = self._open_sides(head)
        # The open sides, each of them alone, then any side: the first
        # whose columns land on nothing already there (another connector's
        # parts, a fixed part beside it), else the one that lands on least.
        trials = [found] + ([s for s in found] if len(found) > 1 else []) + \
            (["RTLB"] if found != "RTLB" else [])
        best = None
        for sides in trials:
            lay = compose(b, self.sh, self.grid, self.gap, head.rot, sides)
            poses = {}
            for r, (x, y, rot) in lay.poses.items():
                if r != b.head:
                    poses[r] = (_snap_abs(head.x + x, self.ox_l, self.grid),
                                _snap_abs(head.y + y, self.oy_l, self.grid), rot)
            hits = self._hits(poses)
            if best is None or hits < best[0]:
                best = (hits, lay, poses)
            if not hits:
                break
        _, lay, poses = best
        box = (head.x + lay.box[0], head.y + lay.box[1], head.x + lay.box[2], head.y + lay.box[3])
        self._record(b, poses, lay.groups, box)
        self._close_parts(poses, self.margin, self._block_sides(b))

    def _hits(self, poses) -> int:
        """How many of these parts would stand on something closed."""
        n = 0
        for r, (x, y, rot) in poses.items():
            e = self.sh.extent(r, rot)
            j0, j1, i0, i1 = self._cells((x + e[0], y + e[1], x + e[2], y + e[3]))
            p = self.pl.by_ref[r]
            if j0 >= j1 or i0 >= i1 or any(self.occ[s][j0:j1, i0:i1].any() for s in self.pl._sides(p)):
                n += 1
        return n

    def _open_sides(self, head) -> str:
        """The sides of a fixed head that face open board: a strip beyond
        each, as deep as the head is wide, at least half free. All four
        when none is."""
        bx0, by0, bx1, by1 = self.sh.body(head.ref, head.rot)
        x0, y0, x1, y1 = bx0 + head.x, by0 + head.y, bx1 + head.x, by1 + head.y
        d = max(min(x1 - x0, y1 - y0), 4 * self.fc, 60.0)
        strips = {"R": (x1, y0, x1 + d, y1), "L": (x0 - d, y0, x0, y1),
                  "T": (x0, y1, x1, y1 + d), "B": (x0, y0 - d, x1, y0)}
        busy = self.occ[head.side]
        out = ""
        for s in "RTLB":
            j0, j1, i0, i1 = self._cells(strips[s])
            area = (j1 - j0) * (i1 - i0)
            if area > 0 and busy[j0:j1, i0:i1].mean() <= 0.5:
                out += s
        return out or "RTLB"

    def _record(self, b: Block, poses: dict, groups: dict, box) -> None:
        if b.name not in self.records:
            self.sequence.append(b.name)
        self.records[b.name] = (b, poses, groups)
        self.where.update(poses)
        self.rects[b.name] = box

    def _close_parts(self, poses, grow, sides) -> None:
        """Close the cells each part of a block covers, grown by ``grow``:
        the block's own outline, not its rectangle."""
        for r, (x, y, rot) in poses.items():
            e = self.sh.extent(r, rot)
            self._close((x + e[0] - grow, y + e[1] - grow, x + e[2] + grow, y + e[3] + grow), sides)

    def _make_units(self) -> None:
        sets: dict[int, list[Block]] = {}
        for b in self.blocks:
            if b.anchored:
                continue
            if b.channel_set >= 0:
                sets.setdefault(b.channel_set, []).append(b)
            else:
                self._add_unit([b])
        for k in sorted(sets):
            self._add_unit(sorted(sets[k], key=lambda b: b.channel_index))

    def _layouts(self, group: list[Block], gap: float) -> list[Layout]:
        first = group[0]
        tile = compose(first, self.sh, self.grid, gap)
        layouts = [tile]
        for b in group[1:]:
            # Member j of each copy corresponds to member j of the first.
            pairs = list(zip(b.members, first.members))
            layouts.append(Layout({m.ref: tile.poses[f.ref] for m, f in pairs}, tile.box,
                                  {m.ref: tile.groups[f.ref] for m, f in pairs}))
        return layouts

    def _add_unit(self, group: list[Block]) -> None:
        layouts = self._layouts(group, self.gap)
        sides = tuple(sorted({s for b in group for s in self._block_sides(b)}, key=SIDES.index))
        u = Unit(group, layouts[0], sides, layouts)
        for b in group:
            for r in b.refs():
                self.unit_of[r] = len(self.units)
        self.units.append(u)

    # -- where the blocks want to be -----------------------------------------

    def _terminal(self, comp: str, pad: int):
        """A pad's place now: ('u', unit) when its block is still to place,
        else ('f', x, y)."""
        k = self.unit_of.get(comp)
        if k is not None and not self.units[k].placed:
            return ("u", k)
        if comp in self.where:
            x, y, rot = self.where[comp]
            px, py = self.sh.pin(comp, pad, rot)
            return ("f", x + px, y + py)
        q = self.board.pads[pad]
        return ("f", q.x, q.y)

    def _solve(self, todo: list[int]) -> dict[int, tuple[float, float]]:
        """Centres of the blocks still to place: a quadratic over the nets
        they share, with placed blocks and fixed parts as anchors."""
        col = {k: n for n, k in enumerate(todo)}
        n = len(todo)
        A = np.zeros((n, n))
        bx = np.zeros(n)
        by = np.zeros(n)
        for net in self.nets:
            terms, seen = [], set()
            for comp, pad in net:
                t = self._terminal(comp, pad)
                if t[0] == "u":
                    if t[1] in seen:
                        continue
                    seen.add(t[1])
                terms.append(t)
            if len(terms) < 2 or not seen:
                continue
            w = 1.0 / (len(terms) - 1)
            for a in range(len(terms)):
                for c in range(a + 1, len(terms)):
                    ta, tc = terms[a], terms[c]
                    if ta[0] == "u" and tc[0] == "u":
                        i, j = col[ta[1]], col[tc[1]]
                        A[i, i] += w
                        A[j, j] += w
                        A[i, j] -= w
                        A[j, i] -= w
                    elif ta[0] == "u" or tc[0] == "u":
                        u, f = (ta, tc) if ta[0] == "u" else (tc, ta)
                        i = col[u[1]]
                        A[i, i] += w
                        bx[i] += w * f[1]
                        by[i] += w * f[2]
        # A weak pull to the middle of the board holds blocks wired to
        # nothing placed yet.
        eps = 1e-3 * max(1.0, float(np.mean(np.diag(A))))
        cx, cy = (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2
        A += eps * np.eye(n)
        bx += eps * cx
        by += eps * cy
        X = np.linalg.solve(A, bx)
        Y = np.linalg.solve(A, by)
        return {k: (float(X[col[k]]), float(Y[col[k]])) for k in todo}

    def _next(self, todo: list[int]) -> int:
        """The block most wired to what is placed (fixed parts included),
        then the largest."""
        pull = {k: 0.0 for k in todo}
        for net in self.nets:
            terms = [self._terminal(c, p) for c, p in net]
            fixed = sum(1 for t in terms if t[0] == "f")
            if not fixed:
                continue
            units = {t[1] for t in terms if t[0] == "u"}
            for k in units:
                pull[k] += fixed / (len(terms) - 1)
        return max(todo, key=lambda k: (round(pull[k], 9), self.units[k].area, -k))

    # -- placing a rectangle -------------------------------------------------

    def _shape_of(self, u: Unit, turn: float, cols: int):
        """(width, height, tile pitch) of a unit turned, its tiles in
        ``cols`` columns."""
        tw, th = u.tile.turned(turn).size
        n = len(u.blocks)
        rows = (n + cols - 1) // cols
        px = _up(tw + 2 * self.gap, self.grid)
        py = _up(th + 2 * self.gap, self.grid)
        return (cols - 1) * px + tw, (rows - 1) * py + th, (px, py)

    def _place_unit(self, k: int, centres) -> None:
        u = self.units[k]
        target = centres[k]
        n = len(u.blocks)
        options = [(turn, cols) for turn in TURNS for cols in range(1, n + 1)]
        S = self._integral(u.sides)
        slack = self.grid
        best = None
        for margin in (self.margin, self.margin / 2, 0.0):
            for turn, cols in options:
                w, h, pitch = self._shape_of(u, turn, cols)
                spot = self._nearest_free(S, w + 2 * margin + slack, h + 2 * margin + slack, target)
                if spot is None:
                    continue
                x0, y0 = spot
                origins = self._origins(u, turn, cols, pitch, x0 + margin, y0 + margin + h)
                key = (self._wire(u, turn, origins, centres), turn, cols)
                if best is None or key < best[0]:
                    best = (key, turn, cols, origins)
            if best is not None:
                break
        if best is None:
            best = self._pack(u, target, centres)
        if best is None and self.gap > TIGHT_GAP:
            # Still nowhere: the block drawn tight, its parts nearly outline
            # to outline (their copper still a clearance apart), before it
            # is given up to the legaliser.
            u.layouts = self._layouts(u.blocks, TIGHT_GAP)
            u.tile = u.layouts[0]
            best = self._pack(u, target, centres)
        if best is None:
            # Nowhere free even for its parts: the least crowded spot, and
            # the legaliser sorts the parts out.
            u.overflow = True
            cols = max(1, int(round(math.sqrt(n))))
            w, h, pitch = self._shape_of(u, 0.0, cols)
            x0, y0 = self._least_crowded(S, w, h, target)
            best = (None, 0.0, cols, self._origins(u, 0.0, cols, pitch, x0, y0 + h))
        _, u.turn, u.cols, u.origins = best
        u.placed = True
        for b, lay, (tx, ty) in zip(u.blocks, u.layouts, u.origins):
            poses = self._set_block(b, lay, u.turn, tx, ty)
            self._close_parts(poses, self.margin if not u.packed else 0.0, u.sides)
        if not u.packed:
            x0 = min(self.rects[b.name][0] for b in u.blocks)
            y0 = min(self.rects[b.name][1] for b in u.blocks)
            x1 = max(self.rects[b.name][2] for b in u.blocks)
            y1 = max(self.rects[b.name][3] for b in u.blocks)
            m = self.margin
            self._close((x0 - m, y0 - m, x1 + m, y1 + m), u.sides)

    def _pack(self, u: Unit, target, centres):
        """Where the unit's parts fit, its rectangle allowed over other
        blocks' empty corners: each part's outline must be free, not the
        whole box. The best (key, turn, cols, origins), or None."""
        busy = np.zeros((self.H, self.W), dtype=np.float32)
        for s in u.sides:
            busy = np.maximum(busy, self.occ[s].astype(np.float32))
        n = len(u.blocks)
        shapes = sorted({1, n, max(1, int(round(math.sqrt(n))))})

        def centred(turn):
            w, h, pitch = self._shape_of(u, turn, shapes[0])
            return self._origins(u, turn, shapes[0], pitch, target[0] - w / 2, target[1] + h / 2)

        # The two turns that suit the wiring best, centred on the target:
        # each turn tried is a transform of the whole raster.
        turns = sorted(TURNS, key=lambda t: (self._wire(u, t, centred(t), centres), t))[:2]
        options = []
        for turn in turns:
            for cols in shapes:
                w, h, pitch = self._shape_of(u, turn, cols)
                origins = self._origins(u, turn, cols, pitch, 0.0, h)
                hc = int(math.ceil(h / self.fc)) + 2
                wc = int(math.ceil(w / self.fc)) + 2
                if hc > self.H or wc > self.W:
                    continue
                mask = np.zeros((hc, wc), dtype=np.float32)
                for lay, (tx, ty) in zip(u.layouts, origins):
                    for r, (x, y, rot) in lay.turned(turn).poses.items():
                        e = self.sh.extent(r, rot)
                        i0 = int(math.floor((tx + x + e[0]) / self.fc)) + 1
                        j0 = int(math.floor((ty + y + e[1]) / self.fc)) + 1
                        i1 = int(math.ceil((tx + x + e[2]) / self.fc)) + 1
                        j1 = int(math.ceil((ty + y + e[3]) / self.fc)) + 1
                        mask[max(j0, 0):j1, max(i0, 0):i1] = 1.0
                options.append((turn, cols, w, h, origins, mask))
        if not options:
            return None
        corr = Correlator(busy)
        best = None
        for turn, cols, w, h, origins, mask in options:
            jj, ii = np.nonzero(corr.valid(mask) < 0.5)
            if not len(jj):
                continue
            # Unit point X fell in mask cell floor(X / fc) + 1; with the
            # mask's first cell on board cell ii, X lands here plus X.
            x0 = self.bbox[0] + (ii + 1) * self.fc
            y0 = self.bbox[1] + (jj + 1) * self.fc
            d = np.hypot(x0 + w / 2 - target[0], y0 + h / 2 - target[1])
            kk = int(np.argmin(d))
            # The mask was drawn with the bottom left of the unit's box at
            # the origin: the unit's origins move with the spot.
            at = [(_snap_abs(tx + float(x0[kk]), self.ox_l, self.grid),
                   _snap_abs(ty + float(y0[kk]), self.oy_l, self.grid)) for tx, ty in origins]
            key = (self._wire(u, turn, at, centres), turn, cols)
            if best is None or key < best[0]:
                best = (key, turn, cols, at)
        if best is not None:
            u.packed = True
        return best

    def _origins(self, u: Unit, turn: float, cols: int, pitch, left: float, top: float):
        """Each tile's frame origin (on the lattice), tiles filled from the
        top left, row by row, in channel order."""
        t = u.tile.turned(turn)
        out = []
        for idx in range(len(u.blocks)):
            row, c = divmod(idx, cols)
            bx = left + c * pitch[0]
            by = top - row * pitch[1]
            out.append((_snap_abs(bx - t.box[0], self.ox_l, self.grid),
                        _snap_abs(by - t.box[3], self.oy_l, self.grid)))
        return out

    def _set_block(self, b: Block, lay: Layout, turn: float, tx: float, ty: float) -> dict:
        t = lay.turned(turn)
        poses = {r: (tx + x, ty + y, rot) for r, (x, y, rot) in t.poses.items()}
        self._record(b, poses, t.groups, (tx + t.box[0], ty + t.box[1], tx + t.box[2], ty + t.box[3]))
        return poses

    def _wire(self, u: Unit, turn: float, origins, centres) -> float:
        """Distance from the unit's pins, so placed, to the middle of what
        each of their nets reaches outside the unit."""
        me = {r for b in u.blocks for r in b.refs()}
        pos = {}
        for lay, (tx, ty) in zip(u.layouts, origins):
            for r, (x, y, rot) in lay.turned(turn).poses.items():
                pos[r] = (tx + x, ty + y, rot)
        total = 0.0
        for net in self.nets:
            inside = [(c, p) for c, p in net if c in me]
            if not inside or len(inside) == len(net):
                continue
            xs, ys = [], []
            for c, p in net:
                if c in me:
                    continue
                t = self._terminal(c, p)
                if t[0] == "u":
                    if t[1] in centres:
                        xs.append(centres[t[1]][0])
                        ys.append(centres[t[1]][1])
                else:
                    xs.append(t[1])
                    ys.append(t[2])
            if not xs:
                continue
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            for c, p in inside:
                x, y, rot = pos[c]
                px, py = self.sh.pin(c, p, rot)
                total += abs(x + px - mx) + abs(y + py - my)
        return total

    def _integral(self, sides) -> np.ndarray:
        busy = np.zeros((self.H, self.W), dtype=bool)
        for s in sides:
            busy |= self.occ[s]
        S = np.zeros((self.H + 1, self.W + 1), dtype=np.int32)
        S[1:, 1:] = np.cumsum(np.cumsum(busy, axis=0), axis=1)
        return S

    def _windows(self, S, w: float, h: float):
        hc = int(math.ceil(h / self.fc))
        wc = int(math.ceil(w / self.fc))
        if hc > self.H or wc > self.W or hc < 1 or wc < 1:
            return None
        return S[hc:, wc:] - S[:-hc, wc:] - S[hc:, :-wc] + S[:-hc, :-wc]

    def _nearest_free(self, S, w, h, target):
        """Lower-left corner of the free spot for a w x h rectangle whose
        centre is nearest ``target``, or None."""
        win = self._windows(S, w, h)
        if win is None:
            return None
        jj, ii = np.nonzero(win == 0)
        if not len(jj):
            return None
        x0 = self.bbox[0] + ii * self.fc
        y0 = self.bbox[1] + jj * self.fc
        d = np.hypot(x0 + w / 2 - target[0], y0 + h / 2 - target[1])
        k = int(np.argmin(d))
        return float(x0[k]), float(y0[k])

    def _least_crowded(self, S, w, h, target):
        win = self._windows(S, w, h)
        if win is None:
            return self.bbox[0], self.bbox[1]
        jj, ii = np.nonzero(win == win.min())
        x0 = self.bbox[0] + ii * self.fc
        y0 = self.bbox[1] + jj * self.fc
        d = np.hypot(x0 + w / 2 - target[0], y0 + h / 2 - target[1])
        k = int(np.argmin(d))
        return float(x0[k]), float(y0[k])

    def _turn_half(self) -> None:
        """With everything placed, a block (not a set) may turn half round
        in its rectangle where that brings its pins nearer what they
        connect to: the same rectangle, the other way about."""
        for u in self.units:
            if len(u.blocks) != 1 or u.overflow or u.packed:
                continue
            b, lay = u.blocks[0], u.layouts[0]
            now = lay.turned(u.turn)
            other = (u.turn + 180.0) % 360.0
            alt = lay.turned(other)
            tx, ty = u.origins[0]
            # The same rectangle: the turned box lands where the old one was.
            ax = _snap_abs(tx + now.box[0] - alt.box[0], self.ox_l, self.grid)
            ay = _snap_abs(ty + now.box[1] - alt.box[1], self.oy_l, self.grid)
            here = self._wire(u, u.turn, [(tx, ty)], {})
            there = self._wire(u, other, [(ax, ay)], {})
            if there < here - 1e-6:
                u.turn, u.origins = other, [(ax, ay)]
                self._set_block(b, lay, other, ax, ay)

    # -- result ----------------------------------------------------------------

    def report(self) -> list[dict]:
        out = []
        for b in self.blocks:
            box = self.rects.get(b.name)
            if box is None:
                continue
            d = {"name": b.name, "rect": [round(v, 1) for v in box],
                 "members": b.refs(), "head": b.head, "kind": b.kind,
                 "anchored": b.anchored}
            if b.channel_set >= 0:
                d["channel"] = [b.channel_set, b.channel_index]
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# Legaliser
# ---------------------------------------------------------------------------

class Legaliser:
    """Parts to their block's spots, or the nearest legal ones, recorded."""

    def __init__(self, placer, grid: float, flag_at: float):
        self.pl = placer
        self.grid = grid
        self.flag_at = flag_at
        self.step = int(round(grid / placer.cell))
        # Keepouts and the board's own copper are in the occupancy already.
        placer.occ, placer.occ_cu = placer._occupancy()
        placer.body_overlaps = []
        self.moves: list[dict] = []
        self.failed: list[str] = []
        self.body_overlaps: list[str] = placer.body_overlaps
        # Lattice offsets within reach, nearest first.
        k = int(math.ceil(flag_at / grid))
        offs = [(dj, di) for dj in range(-k, k + 1) for di in range(-k, k + 1)
                if (dj or di) and math.hypot(dj, di) * grid <= flag_at]
        self.shifts = sorted(offs, key=lambda o: (math.hypot(*o), o))

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        pl = self.pl
        return (int(round((y - pl.box[1]) / pl.cell - 0.5)), int(round((x - pl.box[0]) / pl.cell - 0.5)))

    def put_block(self, items, groups: dict) -> None:
        """A block's parts ([(part, x, y, turn)], inner first): where its
        layout puts them; else all of them moved together to the nearest
        lattice spot within reach where they fit; else group by group
        (the head, each side's columns, the rows); else part by part."""
        if not items:
            return
        if self._group(items):
            return
        by: dict[str, list] = {}
        for it in items:
            by.setdefault(groups.get(it[0].ref, ""), []).append(it)
        keys = sorted(by, key=lambda g: (g != "head", -len(by[g]), g))
        for g in keys:
            if len(by[g]) > 1 and self._group(by[g]):
                continue
            for p, x, y, rot in by[g]:
                self.put(p, x, y, rot)

    def _group(self, items) -> bool:
        """Place a group as it stands, or shifted together by the nearest
        lattice step that fits all of it within reach; record any shift."""
        cells = [(p, *self._cell(x, y), rot % 360.0, x, y) for p, x, y, rot in items]
        for dj, di in [(0, 0)] + self.shifts:
            if not all(self._fits(p, J + dj * self.step, I + di * self.step, rot)
                       for p, J, I, rot, _, _ in cells):
                continue
            placed = []
            for p, J, I, rot, x, y in cells:
                JJ, II = J + dj * self.step, I + di * self.step
                if not self._fits(p, JJ, II, rot):
                    # Two parts of the group overlap each other: undo.
                    for q in placed:
                        self.pl._lift(q)
                    break
                self.pl._place(p, JJ, II, rot)
                placed.append(p)
            else:
                if dj or di:
                    for p, J, I, rot, x, y in cells:
                        self._record(p, x, y, rot)
                return True
        return False

    def _fits(self, p, J: int, I: int, rot: float) -> bool:
        oj, oi, m = self.pl._masks(p)[rot]
        return self.pl._fits_all(self.pl.occ, p, J + oj, I + oi, m)

    def put(self, p, x: float, y: float, rot: float) -> None:
        pl = self.pl
        rot = rot % 360.0
        J, I = self._cell(x, y)
        if self._fits(p, J, I, rot):
            pl._place(p, J, I, rot)
            return
        reach = int(math.ceil(self.flag_at / pl.cell))
        hit = self._search(p, J, I, [rot], reach)
        if hit is None:
            hit = self._growing(p, J, I, 4 * reach, pl.occ, pl._masks(p))
        if hit is not None:
            pl._place(p, hit[0], hit[1], hit[2])
        else:
            # Nowhere whole: by its copper, its body over other bodies, as
            # the analytic legaliser does (``body_overlaps``).
            hit = self._growing(p, J, I, 4 * reach, pl.occ_cu, pl._masks_cu(p))
            if hit is None:
                p.x, p.y, p.rot = x, y, rot
                self.failed.append(p.ref)
                return
            pl._place(p, hit[0], hit[1], hit[2])
            p.overlapping = True
            self.body_overlaps.append(p.ref)
        self._record(p, x, y, rot)

    def _growing(self, p, J: int, I: int, reach: int, occ, masks):
        """``_search`` over any turn, the window growing fourfold until it
        holds the board: one search of the whole raster took a second a
        part, and most parts find room near where they were."""
        whole = max(occ[p.side].shape)
        while True:
            last = reach >= whole
            hit = self._search(p, J, I, list(TURNS), None if last else reach, occ, masks)
            if hit is not None or last:
                return hit
            reach *= 4

    def _record(self, p, x: float, y: float, rot: float) -> None:
        d = math.hypot(p.x - x, p.y - y)
        self.moves.append({"designator": p.ref, "from": [round(x, 2), round(y, 2)],
                           "to": [round(p.x, 2), round(p.y, 2)],
                           "rotation_from": rot, "rotation_to": p.rot, "distance": round(d, 2)})

    def _search(self, p, J: int, I: int, rots, reach, occ=None, masks=None):
        """The free pose nearest cell (J, I) on the lattice, trying the
        turns given, within ``reach`` cells (the whole board for None), on
        the raster ``occ`` with the part's ``masks`` (what it keeps out,
        by default): (J, I, turn) or None."""
        pl = self.pl
        occ = pl.occ if occ is None else occ
        masks = pl._masks(p) if masks is None else masks
        busy = occ[p.side] if not p.through else (occ["top"] | occ["bottom"])
        H, W = busy.shape
        best = None
        for rot in rots:
            if masks.get(rot) is None:
                continue
            oj, oi, m = masks[rot]
            h, w = m.shape
            if reach is None:
                j0, i0, j1, i1 = 0, 0, H, W
            else:
                j0, i0 = max(0, J + oj - reach), max(0, I + oi - reach)
                j1, i1 = min(H, J + oj + reach + h), min(W, I + oi + reach + w)
            if j1 - j0 < h or i1 - i0 < w:
                continue
            win = busy[j0:j1, i0:i1].astype(np.float32)
            hits = Correlator(win).valid(m.astype(np.float32))
            aa, bb = np.nonzero(hits < 0.5)
            if not len(aa):
                continue
            JJ, II = aa + j0 - oj, bb + i0 - oi
            on = ((JJ - J) % self.step == 0) & ((II - I) % self.step == 0)
            if not on.any():
                continue
            JJ, II = JJ[on], II[on]
            d = np.hypot(JJ - J, II - I)
            k = int(np.argmin(d))
            cand = (float(d[k]), rots.index(rot), int(JJ[k]), int(II[k]), rot)
            if best is None or cand[:2] < best[:2]:
                best = cand
        return None if best is None else best[2:]
