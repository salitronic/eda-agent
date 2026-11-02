# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Exact-shape DRC and connectivity on a LayoutBoard.

Two questions, both answered from the board's real copper:

* **Connectivity.** Which pads of each net are joined by copper? Union-find
  over everything conductive: pads, tracks, arcs, vias, poured and solid
  regions. Two items join when they are on a common layer, carry the same
  net (or one carries none) and their shapes touch. A net whose pads fall
  into more than one group is unrouted, and the count of extra groups is
  the number of connections still missing.

* **Clearance.** Which pairs of copper on the same layer, belonging to
  different nets, sit closer than the clearance the board's rules demand?
  Distances come from ``geom``: exact, not boxed, not gridded.

It is the referee for everything the placer and router produce, so it has
to be right on a board a person finished. On a human-routed public board
it must report every net connected and next to no violations; a DRC that
flags a finished board is measuring itself.

Broad-phase is a uniform grid hash on bounding boxes, so a board with tens
of thousands of primitives is checked pair by pair only where they are
actually close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from . import geom
from .model import LayoutBoard
from .rules import RuleSet

# Conductive items are held as (kind, index, net, layer, shape, owner).
# owner is a pad key or component designator; it lets a check tell a pad's
# own footprint copper apart from someone else's.


@dataclass
class Item:
    kind: str           # pad | track | arc | via | region
    idx: int            # index into the board's list of that kind
    net: str
    layer: str
    shape: geom.Shape
    comp: str = ""      # owning component, "" for free copper

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.comp or self.net or '-'}#{self.idx}"


@dataclass
class Violation:
    rule: str
    layer: str
    a: str
    b: str
    gap: float
    required: float
    at: tuple[float, float]

    def as_dict(self) -> dict:
        return {"rule": self.rule, "layer": self.layer, "a": self.a,
                "b": self.b, "gap": round(self.gap, 4),
                "required": round(self.required, 4),
                "at": [round(self.at[0], 2), round(self.at[1], 2)]}


@dataclass
class DrcReport:
    nets: int = 0
    connected_nets: int = 0
    unrouted: dict[str, int] = field(default_factory=dict)   # net -> missing links
    violations: list[Violation] = field(default_factory=list)
    checked_pairs: int = 0
    items: int = 0

    @property
    def missing_connections(self) -> int:
        return sum(self.unrouted.values())

    @property
    def completion(self) -> float:
        """Share of the connections a net needs that exist in copper."""
        needed = self.connections_needed
        if not needed:
            return 1.0
        return 1.0 - self.missing_connections / needed

    connections_needed: int = 0

    def summary(self) -> dict:
        return {
            "nets": self.nets, "connected_nets": self.connected_nets,
            "unrouted_nets": len(self.unrouted),
            "missing_connections": self.missing_connections,
            "connections_needed": self.connections_needed,
            "completion": round(self.completion, 4),
            "violations": len(self.violations),
            "items": self.items, "checked_pairs": self.checked_pairs,
        }


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def conductive_items(board: LayoutBoard) -> list[Item]:
    """Everything that carries current, one Item per (object, layer)."""
    items: list[Item] = []
    copper = set(board.copper_layers())
    # On an internal plane layer the copper is the plane itself (read from
    # its split regions). A track, arc or region drawn there is a split
    # line or a void cut OUT of the plane, not copper: counted as copper,
    # every split line on a board with planes became a false violation.
    planes = {l.name for l in board.layers if l.kind == "plane"}
    signal = copper - planes
    for i, p in enumerate(board.pads):
        for c in p.copper:
            s = p.shape_on(c.layer)
            if s is not None:
                items.append(Item("pad", i, p.net, c.layer, s, p.comp))
    for i, t in enumerate(board.tracks):
        if t.keepout or t.layer not in signal:
            continue
        items.append(Item("track", i, t.net, t.layer, t.shape(), t.comp))
    for i, a in enumerate(board.arcs):
        if a.keepout or a.layer not in signal:
            continue
        for s in a.shapes():
            items.append(Item("arc", i, a.net, a.layer, s, a.comp))
    for i, v in enumerate(board.vias):
        for layer in board.layers_between(v.low_layer, v.high_layer):
            if layer in copper:
                items.append(Item("via", i, v.net, layer, v.shape_on(layer), v.comp))
    for i, r in enumerate(board.regions):
        if r.kind in ("keepout", "cutout", "pour_boundary") or r.layer not in copper:
            continue
        if r.layer in planes and r.kind != "plane":
            continue
        kind = "plane" if r.kind == "plane" else "region"
        items.append(Item(kind, i, r.net, r.layer, r.shape(), r.comp))
    return items


class _Grid:
    """Uniform spatial hash over item bounding boxes.

    Each item is filed under every cell its box, grown by half of
    ``reach``, touches: two items closer than ``reach`` then share a cell
    and are offered as a pair. Filed by the bare box, a track and a pad
    either side of a cell boundary were never compared, and a track 22.7
    mil from a pad it had to keep 30 from passed.
    """

    def __init__(self, items: list[Item], cell: float, reach: float = 0.0):
        self.cell = cell
        self.cells: dict[tuple[str, int, int], list[int]] = {}
        for n, it in enumerate(items):
            for key in self._keys(it.layer, it.shape.bbox, reach / 2):
                self.cells.setdefault(key, []).append(n)

    def _keys(self, layer, bbox, pad):
        c = self.cell
        x0, y0 = int(math.floor((bbox[0] - pad) / c)), int(math.floor((bbox[1] - pad) / c))
        x1, y1 = int(math.floor((bbox[2] + pad) / c)), int(math.floor((bbox[3] + pad) / c))
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                yield (layer, gx, gy)

    def candidate_pairs(self) -> Iterable[tuple[int, int]]:
        seen: set[tuple[int, int]] = set()
        for members in self.cells.values():
            m = len(members)
            for i in range(m):
                a = members[i]
                for j in range(i + 1, m):
                    b = members[j]
                    key = (a, b) if a < b else (b, a)
                    if key not in seen:
                        seen.add(key)
                        yield key


def _cell_size(items: list[Item], margin: float) -> float:
    if not items:
        return 100.0
    spans = sorted(max(it.shape.bbox[2] - it.shape.bbox[0],
                       it.shape.bbox[3] - it.shape.bbox[1]) for it in items)
    typical = spans[len(spans) // 2]
    return max(20.0, typical + 2 * margin)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


TOUCH = 1e-3   # mils: copper closer than this is treated as touching


def _tied(ia, ib, ties, board) -> bool:
    for x, y in ((ia, ib), (ib, ia)):
        if x.kind == "pad" and x.comp in ties and y.net in ties[x.comp]:
            return True
    return False

#: A polygon with more edges than this gets a PolyIndex.
BIG_POLY = 48


class _Distances:
    """Core distances, with big polygons answered through their index."""

    def __init__(self, items: list["Item"]):
        self.index: dict[int, geom.PolyIndex] = {}
        for n, it in enumerate(items):
            s = it.shape
            if s.kind == "poly" and len(s.pts) + sum(len(h) for h in s.holes) > BIG_POLY:
                self.index[n] = geom.PolyIndex(s)

    def core(self, a: int, sa: geom.Shape, b: int, sb: geom.Shape,
             cutoff: float) -> float:
        ia, ib = self.index.get(a), self.index.get(b)
        if ia is None and ib is None:
            return geom.core_distance(sa, sb)
        if ia is not None and ib is not None:
            # Two big polygons: walk the one with fewer edges against the
            # other's index.
            if len(ia.edges) < len(ib.edges):
                ia, ib, sa, sb = ib, ia, sb, sa
            return ia.core_distance(sb, cutoff)
        if ia is not None:
            return ia.core_distance(sb, cutoff)
        return ib.core_distance(sa, cutoff)

    def gap(self, a, sa, b, sb, cutoff) -> float:
        """Edge-to-edge gap, exact up to ``cutoff``."""
        return self.core(a, sa, b, sb, cutoff + sa.r + sb.r) - sa.r - sb.r

#: A gap short of the required clearance by less than this is not reported.
#: Arcs are judged on chords that stray at most 0.05 mil from the true arc
#: (geom.arc_points), and float arithmetic adds its own crumbs. On the first
#: public board read, all four remaining "violations" were gaps of exactly
#: the required 5 mil, rounded down in the last digit.
TOLERANCE = 0.06


def connectivity(board: LayoutBoard, items: list[Item] | None = None,
                 grid: _Grid | None = None,
                 dist: _Distances | None = None) -> dict[str, list[set[int]]]:
    """For each net, the groups of its pads (by index) that copper joins.

    By INDEX, not by name: a footprint can carry several pads of one name
    (nine SCREW pads on the first public board read), and keyed by name
    they collapsed into one.

    Plane layers join every through-hole pad and via of their net: a plane
    is one sheet of copper, and the relief or connection rule decides how
    it attaches, not whether.
    """
    items = items if items is not None else conductive_items(board)
    uf = _joined(board, items, grid, dist)
    groups: dict[str, dict[int, set[int]]] = {}
    for n, it in enumerate(items):
        if it.kind != "pad" or not it.net:
            continue
        groups.setdefault(it.net, {}).setdefault(uf.find(n), set()).add(it.idx)
    return {net: list(g.values()) for net, g in groups.items()}


def pads_on_a_barrel(board: LayoutBoard) -> set[int]:
    """Surface pads that existing copper already joins to a via or a
    plated hole of their own net: such a pad reaches every layer, and a
    plane of its net, already.

    The plane fan-out used to give every surface pad of a plane net its
    own dog-bone whatever was there: on a board whose LED supply pads all
    had their via and stub already, it added 303 more vias.
    """
    return set(pad_barrels(board))


def pad_barrels(board: LayoutBoard) -> dict[int, list[tuple[float, float]]]:
    """For each surface pad that existing copper joins to a via or plated
    hole of its net, where those barrels are."""
    items = conductive_items(board)
    uf = _joined(board, items)
    at: dict[int, set] = {}
    for n, it in enumerate(items):
        if not it.net:
            continue
        if it.kind == "via":
            v = board.vias[it.idx]
            at.setdefault(uf.find(n), set()).add((v.x, v.y))
        elif it.kind == "pad" and not board.pads[it.idx].is_smd:
            p = board.pads[it.idx]
            at.setdefault(uf.find(n), set()).add((p.x, p.y))
    out: dict[int, list[tuple[float, float]]] = {}
    for n, it in enumerate(items):
        if it.kind == "pad" and it.net and board.pads[it.idx].is_smd:
            got = at.get(uf.find(n))
            if got:
                out.setdefault(it.idx, [])
                out[it.idx] = sorted(set(out[it.idx]) | got)
    return out


def _joined(board: LayoutBoard, items: list[Item], grid: "_Grid | None" = None,
            dist: "_Distances | None" = None) -> _UF:
    """Union of every pair of items copper joins (see connectivity)."""
    grid = grid or _Grid(items, _cell_size(items, TOUCH), TOUCH)
    dist = dist or _Distances(items)
    uf = _UF(len(items))
    for a, b in grid.candidate_pairs():
        ia, ib = items[a], items[b]
        if ia.net and ib.net and ia.net != ib.net:
            continue
        if not geom.bboxes_near(ia.shape, ib.shape, TOUCH):
            continue
        if dist.gap(a, ia.shape, b, ib.shape, TOUCH) <= TOUCH:
            uf.union(a, b)

    # Split planes: a through-hole pad has no copper item on a plane layer
    # (its stack lists signal layers), so join it to the split of its own
    # net that surrounds its hole. Vias carry an item on every layer they
    # span and meet their split through the geometry above.
    splits: dict[str, list[int]] = {}
    for n, it in enumerate(items):
        if it.kind == "plane" and it.net:
            splits.setdefault(it.net, []).append(n)
    if splits:
        for n, it in enumerate(items):
            if it.kind != "pad" or it.net not in splits:
                continue
            pad = board.pads[it.idx]
            if pad.is_smd:
                continue
            for m in splits[it.net]:
                idx = dist.index.get(m)
                inside = (idx.contains(pad.x, pad.y) if idx is not None
                          else geom.point_in_poly(pad.x, pad.y, items[m].shape))
                if inside:
                    uf.union(n, m)

    # Planes: every plated barrel of the plane's net meets the plane.
    for layer in board.layers:
        if layer.kind != "plane" or not layer.plane_net:
            continue
        anchor = None
        for n, it in enumerate(items):
            if it.net != layer.plane_net:
                continue
            if it.kind == "via" or (it.kind == "pad" and not board.pads[it.idx].is_smd):
                if anchor is None:
                    anchor = n
                else:
                    uf.union(anchor, n)

    # A pad or via is ONE conductor across every layer it spans: the plated
    # barrel joins them. Items are one per layer, so join them here. Missed
    # for vias at first, which split every net that changes layer: the
    # first public board read came out 40% connected when it is finished.
    first_item: dict[tuple[str, int], int] = {}
    for n, it in enumerate(items):
        if it.kind in ("pad", "via"):
            key = (it.kind, it.idx)
            if key in first_item:
                uf.union(first_item[key], n)
            else:
                first_item[key] = n
    return uf


# ---------------------------------------------------------------------------
# DRC
# ---------------------------------------------------------------------------

def net_ties(board: LayoutBoard) -> dict[str, set[str]]:
    """Parts whose pads of different nets touch each other: net ties.

    A net tie joins two nets on purpose, so its pads are drawn touching or
    overlapping. The reader does not carry Altium's component kind, but a
    footprint whose differently netted pads touch is a net tie by what it
    does. Returns each such part with the nets it ties.
    """
    by_comp: dict[str, list] = {}
    for p in board.pads:
        if p.comp and p.net:
            by_comp.setdefault(p.comp, []).append(p)
    out: dict[str, set[str]] = {}
    for ref, pads in by_comp.items():
        if len({p.net for p in pads}) < 2:
            continue
        for i in range(len(pads)):
            for j in range(i + 1, len(pads)):
                a, b = pads[i], pads[j]
                if a.net == b.net:
                    continue
                for layer in set(a.layers()) & set(b.layers()):
                    sa, sb = a.shape_on(layer), b.shape_on(layer)
                    if sa is not None and sb is not None and geom.clearance(sa, sb) <= TOUCH:
                        out.setdefault(ref, set()).update((a.net, b.net))
    return out


def run_drc(board: LayoutBoard, rules: RuleSet | None = None,
            check_clearance: bool = True, limit: int = 500) -> DrcReport:
    rules = rules or RuleSet.from_board(board)
    items = conductive_items(board)
    margin = max(rules.max_clearance(), TOUCH)
    grid = _Grid(items, _cell_size(items, margin), margin)
    dist = _Distances(items)
    report = DrcReport(items=len(items))

    groups = connectivity(board, items, grid, dist)
    pad_nets = board.pads_by_net()
    report.nets = len(pad_nets)
    for net, pads in pad_nets.items():
        if len(pads) < 2:
            continue
        report.connections_needed += len(pads) - 1
        parts = groups.get(net, [])
        missing = max(0, len(parts) - 1)
        if missing:
            report.unrouted[net] = missing
    report.connected_nets = report.nets - len(report.unrouted)

    if not check_clearance:
        return report

    ties = net_ties(board)
    for a, b in grid.candidate_pairs():
        ia, ib = items[a], items[b]
        if ia.net and ia.net == ib.net:
            continue
        # Internal plane copper is drawn as the split's whole area; the
        # clearance around a foreign barrel is an antipad Altium generates
        # from its plane clearance rule, not part of that shape.
        if ia.kind == "plane" or ib.kind == "plane":
            continue
        if ia.kind == ib.kind and ia.idx == ib.idx:
            continue    # one object: a pad's layers, an arc's chords
        # Copper on no net against copper on no net is not a pair a
        # clearance rule is about: logos, coils and footprint artwork are
        # drawn that way and touch themselves, and finished boards carry
        # many such touches; netless copper against a net is still
        # checked.
        if not ia.net and not ib.net:
            continue
        # A net tie's pads against copper of the nets it ties: the join is
        # the part's purpose, not a short.
        if ties and _tied(ia, ib, ties, board):
            continue
        # A footprint's own non-pad copper against its own pads is part of
        # the land pattern. Pad against pad within a footprint is NOT
        # exempt: that is exactly where fine-pitch violations live.
        if (ia.comp and ia.comp == ib.comp
                and (ia.kind == "pad") != (ib.kind == "pad")):
            continue
        required = rules.clearance(ia, ib, board)
        if required <= 0 or not geom.bboxes_near(ia.shape, ib.shape, required):
            continue
        report.checked_pairs += 1
        gap = dist.gap(a, ia.shape, b, ib.shape, required)
        if gap < required - TOLERANCE:
            if len(report.violations) < limit:
                bx = ia.shape.bbox
                report.violations.append(Violation(
                    "clearance", ia.layer, ia.label, ib.label,
                    gap, required, ((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)))
    return report


# ---------------------------------------------------------------------------
# Solder mask slivers
# ---------------------------------------------------------------------------

def mask_openings(board: LayoutBoard, rules: RuleSet) -> list[tuple[Item, float]]:
    """Every opening in the top and bottom solder mask, with its expansion.

    An opening is a pad's or via's copper on an outer layer, grown by its
    expansion: the one set on the object by hand, else the board's mask
    rule. Taken from the hole edge, the hole is grown instead. A side the
    object is tented on has no opening.
    """
    order = board.copper_layers()
    if not order:
        return []
    sides = {order[0]: 0}
    if len(order) > 1:
        sides[order[-1]] = 1
    out: list[tuple[Item, float]] = []

    def add(kind, idx, obj, layer, shape, comp):
        side = sides[layer]
        if (obj.tent_top, obj.tent_bottom)[side]:
            return
        if obj.mask_from_hole and getattr(obj, "hole", 0.0) > 0:
            shape = geom.circle(obj.x, obj.y, obj.hole)
        item = Item(kind, idx, obj.net, layer, shape, comp)
        if obj.mask_manual:
            expansion = float(obj.mask_manual[side])
        else:
            expansion = rules.mask_expansion(rules.facts(item, board))
        out.append((item, expansion))

    for i, p in enumerate(board.pads):
        for layer in sides:
            s = p.shape_on(layer)
            if s is not None:
                add("pad", i, p, layer, s, p.comp)
    for i, v in enumerate(board.vias):
        spanned = set(board.layers_between(v.low_layer, v.high_layer))
        for layer in sides:
            if layer in spanned:
                add("via", i, v, layer, v.shape_on(layer), v.comp)
    return out


def usual_via_mask(board: LayoutBoard) -> tuple | None:
    """The solder mask a via added to the board gets: (tent_top,
    tent_bottom, mask_from_hole, mask_manual) of most of the board's own
    vias, open on both sides when it has none. None when the board was
    read without its mask data.
    """
    if not board.meta.get("mask_read"):
        return None
    from collections import Counter
    old = [x for x in board.vias if not x.comp]
    if not old:
        return (False, False, False, None)
    tt, tb, fh, manual = Counter(
        (x.tent_top, x.tent_bottom, x.mask_from_hole, tuple(x.mask_manual or ()))
        for x in old).most_common(1)[0][0]
    return (tt, tb, fh, list(manual) or None)


def mask_slivers(board: LayoutBoard, rules: RuleSet | None = None,
                 limit: int = 500) -> list[Violation] | None:
    """Mask webs narrower than the board's Minimum Solder Mask Sliver rule.

    Two openings closer than the rule leave a strip of mask too thin to
    stay on the board; openings that meet merge into one and leave none.
    None when the board was read without its mask data (tenting and hand
    set expansions), so the check cannot know which openings exist.
    """
    if not board.meta.get("mask_read"):
        return None
    rules = rules or RuleSet.from_board(board)
    if not rules.of_type("mask_sliver"):
        return []
    openings = mask_openings(board, rules)
    items = [it for it, _ in openings]
    grow = [e for _, e in openings]
    reach = max((rr.values.get("gap", 0.0) for rr in rules.of_type("mask_sliver")),
                default=0.0) + 2 * max(grow, default=0.0)
    grid = _Grid(items, _cell_size(items, reach), reach)
    out: list[Violation] = []
    for a, b in grid.candidate_pairs():
        ia, ib = items[a], items[b]
        if ia.kind == ib.kind and ia.idx == ib.idx:
            continue
        required = rules.mask_sliver(rules.facts(ia, board), rules.facts(ib, board))
        if required <= 0:
            continue
        gap = geom.clearance(ia.shape, ib.shape) - grow[a] - grow[b]
        if 0.0 < gap < required - TOLERANCE:
            bx = ia.shape.bbox
            out.append(Violation("mask_sliver", ia.layer, ia.label, ib.label, gap, required,
                                 ((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)))
            if len(out) >= limit:
                break
    return out


def drill_holes(board: LayoutBoard) -> list[Item]:
    """Every drilled hole: each via's and each pad's, a slot as the capsule
    it is cut as. Layer "MultiLayer": a hole goes through the board."""
    out: list[Item] = []
    for i, v in enumerate(board.vias):
        if v.hole > 0:
            out.append(Item("via", i, v.net, "MultiLayer", geom.circle(v.x, v.y, v.hole), v.comp))
    for i, p in enumerate(board.pads):
        if p.hole <= 0:
            continue
        if p.hole_type == "slot" and p.hole_width > p.hole:
            half = (p.hole_width - p.hole) / 2
            a = math.radians(p.rotation + p.hole_rotation)
            dx, dy = half * math.cos(a), half * math.sin(a)
            s = geom.capsule(p.x - dx, p.y - dy, p.x + dx, p.y + dy, p.hole)
        else:
            s = geom.circle(p.x, p.y, p.hole)
        out.append(Item("pad", i, p.net, "MultiLayer", s, p.comp))
    return out


def hole_gaps(board: LayoutBoard, rules: RuleSet | None = None,
              limit: int = 500) -> list[Violation]:
    """Holes closer than the board's Hole To Hole Clearance rule, edge to
    edge, whatever their nets: a drill breaks through between two holes too
    close together, same net or not."""
    rules = rules or RuleSet.from_board(board)
    if not rules.of_type("hole_to_hole"):
        return []
    items = drill_holes(board)
    reach = max(rr.values.get("gap", 0.0) for rr in rules.of_type("hole_to_hole"))
    grid = _Grid(items, _cell_size(items, reach), reach)
    out: list[Violation] = []
    for a, b in grid.candidate_pairs():
        ia, ib = items[a], items[b]
        rr = rules.binary("hole_to_hole", rules.facts(ia, board), rules.facts(ib, board))
        required = rr.values.get("gap", 0.0) if rr else 0.0
        if required <= 0:
            continue
        gap = geom.clearance(ia.shape, ib.shape)
        if gap < required - TOLERANCE:
            bx = ia.shape.bbox
            out.append(Violation("hole_to_hole", "MultiLayer", ia.label, ib.label, gap,
                                 required, ((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)))
            if len(out) >= limit:
                break
    return out


#: The rule kinds ``verdict`` applies. An unevaluated rule of one of these
#: makes the verdict incomplete; the others are listed as not checked.
CHECKED_KINDS = ("clearance", "mask_sliver", "soldermask", "hole_to_hole")

#: Board rules this DRC does not apply at all, named so a pass is not read
#: as covering them. Altium's batch DRC is the referee for these.
UNCHECKED_KINDS = ("silk_to_silk", "silk_to_mask", "board_outline",
                   "annular_ring", "hole_size", "width", "component_clearance")


def verdict(board: LayoutBoard, rules: RuleSet | None = None, limit: int = 500,
            report: DrcReport | None = None) -> dict:
    """The DRC's answer as pass, fail or incomplete, with what it examined.

    ``fail`` when any violation was found. ``incomplete`` when nothing was
    found but a rule this check applies could not be evaluated, or the
    mask was not read: nothing found is then not the same as nothing
    there. ``pass`` only when every applicable rule was applied.
    """
    rules = rules or RuleSet.from_board(board)
    report = report or run_drc(board, rules, limit=limit)
    slivers = mask_slivers(board, rules, limit=limit)
    unknown = rules.unevaluated(CHECKED_KINDS)
    present = {rr.type for rr in rules.rules}
    not_checked = [k for k in UNCHECKED_KINDS if k in present]
    notes = []
    if slivers is None and "mask_sliver" in present:
        not_checked.insert(0, "mask_sliver")
        notes.append("the board was read without its solder mask data (tenting, hand set "
                     "expansions), so mask slivers were not checked; deploy the current "
                     "scripts and read it again")
    if unknown:
        notes.append(f"{len(unknown)} rule(s) this check applies use a scope it cannot "
                     "evaluate and were not applied")
    holes = hole_gaps(board, rules, limit=limit)
    violations = list(report.violations) + list(slivers or []) + holes
    if violations:
        status = "fail"
    elif unknown or (slivers is None and "mask_sliver" in present):
        status = "incomplete"
    else:
        status = "pass"
    return {
        "status": status,
        "violations": violations,
        "counts": {"violations": len(violations),
                   "clearance": len(report.violations),
                   "mask_sliver": len(slivers or []),
                   "hole_to_hole": len(holes),
                   "checked_pairs": report.checked_pairs,
                   "unevaluated_rules": len(unknown)},
        "unevaluated_rules": unknown,
        "not_checked": not_checked,
        "notes": notes,
        "report": report,
    }
