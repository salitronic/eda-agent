# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pure-Python schematic layout engine.

Takes a :class:`~eda_agent.design.plan.DesignPlan` and computes a complete,
deterministic, grid-snapped schematic layout:

* per-symbol XY position (mils) + rotation,
* a per-net representation decision (``wire`` / ``net_label`` /
  ``power_port``) that reuses the tier policy in ``design._wiring``,
* an orthogonal route polyline for every net that stays a wire,
* an aesthetic badness score (crossings + bends + alignment + area +
  length) whose field names line up with ``design.quality.LayoutScore`` so
  the two placement engines are directly comparable.

The module imports nothing from the bridge or Altium. It is side-effect
free: given a ``DesignPlan`` (and optionally real per-symbol pin geometry)
it returns plain dataclasses that the executor/emitter translate into
``sch_place_*`` MCP calls. With no pin geometry supplied, pins are
synthesised from pin counts via a deterministic perimeter model so the
whole pipeline is unit-testable offline.

Determinism: every iteration is sorted by refdes or net name, the
force-directed pass uses a fixed seed (no ``random`` module), and
coincident points are nudged with a fixed golden-angle offset. Two runs
on the same input produce byte-identical output.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

from eda_agent.design import _wiring
from eda_agent.design.force_directed import (
    SHEET_MAX_X_MILS,
    SHEET_MAX_Y_MILS,
    SHEET_ORIGIN_X_MILS,
    SHEET_ORIGIN_Y_MILS,
    SNAP_GRID_MILS,
    _BOUNDARY_K,
    _bbox_half,
    _rotation_for_part,
)
from eda_agent.design.plan import DesignPlan, Net
from eda_agent.design.router import (
    _STUB_LEN_MILS,
    _pin_direction_vector,
)

__all__ = [
    "PinSlot",
    "PlacedSymbol",
    "NetDecision",
    "NetRoute",
    "LayoutScore",
    "LayoutWeights",
    "SchematicLayout",
    "compute_schematic_layout",
    "decide_net_representation",
    "estimate_wire_bends",
    "group_blocks",
    "coarse_place",
    "order_signal_flow",
    "assign_rotations",
    "snap_and_legalize",
    "route_wire_nets",
    "score_layout",
    "to_executor_payload",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fixed seed for the deterministic golden-angle nudge applied to
# coincident points. No ``random`` module is used anywhere in this file.
_FR_SEED = 0x5C_4E_A7_01

# Golden angle in radians; used to spread initial / coincident points
# evenly without a random generator.
_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))

# Coulomb-style repulsion + Hooke spring tuning for the coarse pass.
_FR_C = 0.55              # ideal-distance scale factor (k = C*sqrt(area/N))
_FR_REPEL_CUTOFF = 4000.0
_FR_COOL_FLOOR = 0.05     # fraction of the initial temperature kept at the end

# A wire whose pin bounding box spans more than this is promoted to a
# net label even when the base tier rule says "wire".
#
# MEASURED against 2662 nets on the human-drawn KiCad demo sheets, split
# by whether the draughtsman labelled them. Span does discriminate: the
# median LABELLED net spans 2800 mils and the median unlabelled one 500,
# so the criterion is sound. At 3000 this gate fires on 47.6% of what a
# human labels, and humans label about half the nets on a sheet while
# still drawing a wire on nearly all of them -- the idiom is a short
# stub plus a label, not a long wire.
#
# On span ALONE the corpus separates best around 800 mils, but that is
# not a recommendation to move this number: span alone is a known sprawl
# trap here, and the measurement above ignores whatever else a gate
# pairs with span.
#
# NOTE THIS IS NOT THE PIPELINE'S GATE. build_canvas_from_plan uses
# pipeline._LABEL_SPAN_MILS; this module's neat-layout engine is not run
# in that hot path (see the NOTE in build_best_canvas_from_plan). Tuning
# this constant will not move the engine's wire length, which was
# confirmed by measuring it: patching this value to 2000 and 1500
# changed six sheets' totals by exactly nothing.
_DEFAULT_LABEL_SPAN_MILS = 3000
# Target bounding span for a functional block after compaction. Kept below the
# label-span gate so a small block's intra-block nets stay wire-traceable
# rather than being promoted to labels once the parts have been spread.
_BLOCK_COMPACT_SPAN_MILS = 2000
# Symbols whose centres fall within this distance on one axis are snapped to a
# shared row/column line so the drawing reads as aligned (a core aesthetic).
_ALIGN_TOL_MILS = 150
# Per-cell routing penalty (in bend-equivalents) for stepping onto a grid cell
# already used by an earlier net's wire, so later nets route around them.
_ROUTE_CROSS_WEIGHT = 3.0

# Manhattan stub length leaving each pin before the main route.
_STUB_MILS = _STUB_LEN_MILS

# Channel resolution for the routing grid (mils between routing tracks).
_ROUTE_GRID = SNAP_GRID_MILS

# Cap on the routing-grid dimension so Dijkstra stays cheap on big sheets.
_MAX_ROUTE_CELLS = 120


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PinSlot:
    """One pin of a symbol in the symbol's local coordinate frame.

    ``offset`` is the pin's electrical (wire-connection) point relative to
    the symbol anchor, in mils. ``side`` is one of ``"left"``, ``"right"``,
    ``"top"``, ``"bottom"`` and gives the outward direction. ``name`` /
    ``number`` identify the pin so a :class:`~eda_agent.design.plan.PinRef`
    can be resolved against it.
    """

    number: str
    name: str
    offset: Tuple[int, int]
    side: str = "left"


@dataclass(frozen=True)
class _Pt:
    """A continuous (pre-snap) 2D point in mils."""

    x: float
    y: float


@dataclass(frozen=True)
class PlacedSymbol:
    """Final placement record for one part."""

    refdes: str
    sheet: str
    x_mils: int
    y_mils: int
    rotation: int
    bbox: Tuple[int, int, int, int]  # (x_min, y_min, x_max, y_max), world mils
    pins: Mapping[str, Tuple[int, int]]  # pin number -> world (x, y)


@dataclass(frozen=True)
class NetDecision:
    """Representation choice for one net.

    ``kind`` is one of ``"wire"``, ``"net_label"``, ``"power_port"``.
    ``style`` / ``orientation`` are populated for power ports. ``bends`` /
    ``span_mils`` carry the pre-route estimate used to gate wire-vs-label
    so the decision is inspectable in tests.
    """

    net_name: str
    kind: str
    style: Optional[str] = None
    orientation: Optional[int] = None
    bends: int = 0
    span_mils: int = 0


@dataclass(frozen=True)
class NetSegment:
    """One axis-aligned segment of a route, world mils."""

    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class NetRoute:
    """Realised orthogonal route for one wire-tier net."""

    net_name: str
    segments: Tuple[NetSegment, ...]
    junctions: Tuple[Tuple[int, int], ...] = ()


@dataclass
class LayoutWeights:
    """Objective weights. Defaults mirror ``design.quality`` so scores from
    the two engines are directly comparable on their shared fields.
    """

    crossings: float = 100.0
    bends: float = 40.0
    alignment: float = 20.0
    aspect: float = 50.0
    length: float = 0.01
    # RMS spread of per-net lengths (mils, linear). Twice the total-length
    # weight so 'wires roughly even' is a gentle tie-breaker, never able to
    # overpower the crossing/bend aesthetics the way the old mils^2 variance
    # term did (it alone was ~87% of a typical total).
    length_spread: float = 0.02


@dataclass
class LayoutScore:
    """Aesthetic badness for a realised layout. ``total`` is the number to
    minimise. Shared field names (``total``, ``wire_crossings``,
    ``aspect_ratio_penalty``, ``total_wire_length``) line up with
    ``design.quality.LayoutScore`` so the pipeline's best-of-N selector can
    compare scores across engines; ``total_bends`` and ``alignment_penalty``
    are the two new terms.
    """

    total: float = 0.0
    wire_crossings: int = 0
    total_bends: int = 0
    alignment_penalty: float = 0.0
    aspect_ratio_penalty: float = 0.0
    total_wire_length: int = 0
    length_spread: float = 0.0  # RMS spread of per-net lengths (mils)
    breakdown: dict[str, float] = field(default_factory=dict)

    def __lt__(self, other: "LayoutScore") -> bool:
        return self.total < other.total


@dataclass
class SchematicLayout:
    """Complete result of the planner."""

    sheet: str
    placed: dict[str, PlacedSymbol]
    decisions: dict[str, NetDecision]
    routes: dict[str, list[NetRoute]]
    junctions: list[Tuple[int, int]]
    score: LayoutScore
    notes: list[str] = field(default_factory=list)
    # net name -> [(refdes, pin)] for every net, used by to_executor_payload
    # to emit one glyph per pin endpoint. Carried per-layout (not global) so
    # several layouts can be built and flattened independently.
    route_membership: dict[str, list[Tuple[str, str]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def compute_schematic_layout(
    plan: DesignPlan,
    *,
    pin_geometry: Optional[Mapping[str, Sequence[PinSlot]]] = None,
    sheet: str = "main",
    grid_mils: int = SNAP_GRID_MILS,
    fr_iterations: int = 80,
    weights: Optional[LayoutWeights] = None,
    placement_hints: Optional[Mapping[str, Mapping[str, int]]] = None,
    max_wire_bends: int = 2,
) -> SchematicLayout:
    """Run the full deterministic layout pipeline and return a layout.

    Pipeline: block grouping, force-directed coarse placement on the block
    graph then within each block, signal-flow ordering, grid snap + overlap
    removal, per-net tier decision, orthogonal routing of wire-tier nets,
    scoring.

    Args:
        plan: The validated design plan.
        pin_geometry: Optional per-refdes pin slots (local offset + side +
            name/number). When ``None`` a deterministic perimeter pin model
            is synthesised from each part's pin count so the function works
            in pure unit tests.
        sheet: Sheet name the layout targets.
        grid_mils: Snap grid for final coordinates.
        fr_iterations: Force-directed iteration budget.
        weights: Objective weights; defaults to :class:`LayoutWeights`.
        placement_hints: ``{refdes: {"x", "y", "rotation"}}`` pinned
            positions that override the computed placement (same shape the
            pipeline already accepts).

    Returns:
        A :class:`SchematicLayout`. No Altium, no I/O.
    """
    weights = weights or LayoutWeights()
    notes: list[str] = list(plan.cross_check())

    # Deterministic ordering up front.
    parts = sorted(
        (p for p in plan.parts if p.sheet == sheet), key=lambda p: p.refdes
    )
    nets = sorted(plan.nets, key=lambda n: n.name)
    refdes_on_sheet = {p.refdes for p in parts}

    geometry = _resolve_pin_geometry(plan, sheet, pin_geometry)

    if not parts:
        notes.append(f"no parts on sheet {sheet!r}")
        return SchematicLayout(
            sheet=sheet,
            placed={},
            decisions={},
            routes={},
            junctions=[],
            score=LayoutScore(),
            notes=notes,
        )

    blocks = group_blocks(plan)
    refdes_to_zone = {p.refdes: p.zone for p in plan.parts}

    coarse = coarse_place(
        plan, blocks, iterations=fr_iterations, grid_mils=grid_mils
    )
    flowed = order_signal_flow(plan, coarse, blocks)
    # Snap near-collinear centres onto shared row/column lines for neatness.
    # Done before compaction: compaction scales each block uniformly toward
    # its centroid, which preserves shared rows/columns, so the alignment
    # survives the tightening that keeps intra-block nets wire-traceable.
    flowed = align_flowed(flowed, grid_mils=grid_mils)
    # Tighten each functional block so its intra-block nets stay wire-traceable.
    flowed = compact_blocks(flowed, blocks)

    # Apply pinned positions before rotation/snap so they win.
    if placement_hints:
        for refdes, hint in placement_hints.items():
            if refdes not in refdes_on_sheet:
                continue
            hx = hint.get("x")
            hy = hint.get("y")
            if hx is not None and hy is not None:
                flowed[refdes] = _Pt(float(hx), float(hy))

    rotations = assign_rotations(plan, flowed, geometry)
    if placement_hints:
        for refdes, hint in placement_hints.items():
            if refdes in refdes_on_sheet and "rotation" in hint:
                rotations[refdes] = int(hint["rotation"]) % 360

    placed = snap_and_legalize(
        plan, flowed, rotations, grid_mils=grid_mils, geometry=geometry,
        sheet=sheet,
    )

    decisions: dict[str, NetDecision] = {}
    for net in nets:
        if not any(pr.refdes in placed for pr in net.pins):
            continue
        decisions[net.name] = decide_net_representation(
            net, refdes_to_zone, placed,
            max_wire_bends=max_wire_bends,
            label_span_mils=_DEFAULT_LABEL_SPAN_MILS,
        )

    membership: dict[str, list[Tuple[str, str]]] = {
        net.name: [(pr.refdes, pr.pin) for pr in net.pins]
        for net in plan.nets
    }
    routes = route_wire_nets(
        decisions, placed, membership=membership, grid_mils=grid_mils,
    )

    junctions: list[Tuple[int, int]] = []
    for route_list in routes.values():
        for route in route_list:
            junctions.extend(route.junctions)
    junctions = sorted(set(junctions))

    score = score_layout(placed, routes, decisions, weights=weights)

    return SchematicLayout(
        sheet=sheet,
        placed=placed,
        decisions=decisions,
        routes=routes,
        junctions=junctions,
        score=score,
        notes=notes,
        route_membership=membership,
    )


# ---------------------------------------------------------------------------
# Pin geometry
# ---------------------------------------------------------------------------


def _pins_per_part(plan: DesignPlan) -> dict[str, list[str]]:
    """Map refdes -> sorted distinct pin numbers referenced by any net."""
    pins: dict[str, set[str]] = {p.refdes: set() for p in plan.parts}
    for net in plan.nets:
        for pr in net.pins:
            pins.setdefault(pr.refdes, set()).add(pr.pin)
    return {r: sorted(v) for r, v in pins.items()}


def _synthesize_pins(pin_numbers: Sequence[str]) -> list[PinSlot]:
    """Deterministic perimeter pin model from a list of pin numbers.

    Pins alternate left / right columns top-to-bottom, anchored so the
    top-leftmost pin's wire end is at (0, 0) and rows step down by 100 mil.
    A 2-pin part gets pin 1 left, pin 2 right (inline passive). Local frame
    only; the placer translates to world coordinates.
    """
    n = len(pin_numbers)
    slots: list[PinSlot] = []
    if n == 0:
        return slots
    if n <= 2:
        # Inline passive: one pin each side at y = 0.
        for i, num in enumerate(pin_numbers):
            if i == 0:
                slots.append(PinSlot(num, num, (0, 0), "left"))
            else:
                slots.append(PinSlot(num, num, (600, 0), "right"))
        return slots
    half = (n + 1) // 2
    width = 1000
    for i, num in enumerate(pin_numbers):
        if i < half:
            y = -100 * i
            slots.append(PinSlot(num, num, (0, y), "left"))
        else:
            y = -100 * (i - half)
            slots.append(PinSlot(num, num, (width, y), "right"))
    return slots


def _resolve_pin_geometry(
    plan: DesignPlan,
    sheet: str,
    pin_geometry: Optional[Mapping[str, Sequence[PinSlot]]],
) -> dict[str, list[PinSlot]]:
    """Return a complete refdes -> pin slots map, synthesising any missing.

    Real geometry supplied by the caller wins; parts absent from the map
    fall back to the synthesised perimeter model so routing/scoring still
    works offline.
    """
    pins_referenced = _pins_per_part(plan)
    out: dict[str, list[PinSlot]] = {}
    for part in plan.parts:
        if part.sheet != sheet:
            continue
        supplied = pin_geometry.get(part.refdes) if pin_geometry else None
        if supplied:
            out[part.refdes] = list(supplied)
        else:
            out[part.refdes] = _synthesize_pins(pins_referenced.get(part.refdes, []))
    return out


def _rotate_offset(off: Tuple[int, int], rotation: int) -> Tuple[int, int]:
    """Rotate a local pin offset by 0/90/180/270 degrees CCW."""
    x, y = off
    r = rotation % 360
    if r == 90:
        return (-y, x)
    if r == 180:
        return (-x, -y)
    if r == 270:
        return (y, -x)
    return (x, y)


def _world_pins(
    center: Tuple[int, int],
    rotation: int,
    slots: Sequence[PinSlot],
) -> dict[str, Tuple[int, int]]:
    """World pin coordinates for a placed symbol.

    The symbol anchor (local origin) is placed at ``center``; each pin's
    local offset is rotated then translated. Snapped to the grid.
    """
    cx, cy = center
    pins: dict[str, Tuple[int, int]] = {}
    for slot in slots:
        rx, ry = _rotate_offset(slot.offset, rotation)
        px = cx + rx
        py = cy + ry
        px = int(round(px / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        py = int(round(py / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        pins[slot.number] = (px, py)
    return pins


# ---------------------------------------------------------------------------
# Block grouping
# ---------------------------------------------------------------------------


def group_blocks(plan: DesignPlan) -> dict[str, str]:
    """Return refdes -> block_id.

    Primary key is the explicit ``Part.zone`` (a declared functional
    block). Parts without a zone are clustered by deterministic
    local-topology predicates:

    * a multi-pin part (4+ pins) absorbs any 2-pin part that connects only
      to it,
    * a 2-pin part on a power+ground pin pair next to an anchor joins that
      anchor's block,
    * remaining degree-2 series chains stay together,

    falling back to a singleton block. Matching is purely on connectivity
    graph structure, never on lib_ref text. Deterministic: parts are
    processed in refdes order.
    """
    parts = sorted(plan.parts, key=lambda p: p.refdes)
    pin_counts: dict[str, int] = {p.refdes: 0 for p in parts}
    neighbours: dict[str, set[str]] = {p.refdes: set() for p in parts}

    for net in plan.nets:
        members = sorted({pr.refdes for pr in net.pins})
        for pr in net.pins:
            pin_counts[pr.refdes] = pin_counts.get(pr.refdes, 0) + 1
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                neighbours.setdefault(a, set()).add(b)
                neighbours.setdefault(b, set()).add(a)

    block_of: dict[str, str] = {}

    # 1. Explicit zones win.
    for p in parts:
        if p.zone:
            block_of[p.refdes] = f"zone:{p.zone}"

    anchors = sorted(
        (p.refdes for p in parts if pin_counts.get(p.refdes, 0) >= 4),
        key=lambda r: r,
    )

    # 2. Absorb passives that connect only to a single anchor.
    for p in parts:
        if p.refdes in block_of:
            continue
        if pin_counts.get(p.refdes, 0) > 2:
            continue
        nbrs = sorted(neighbours.get(p.refdes, set()))
        anchor_nbrs = [n for n in nbrs if n in anchors]
        if len(set(nbrs)) <= 2 and len(anchor_nbrs) == 1:
            anchor = anchor_nbrs[0]
            block_of[p.refdes] = block_of.get(anchor, f"blk:{anchor}")

    # 3. Anchors not yet assigned anchor their own block.
    for a in anchors:
        block_of.setdefault(a, f"blk:{a}")

    # 4. Degree-2 series chains: union adjacent unassigned 2-pin parts.
    changed = True
    while changed:
        changed = False
        for p in parts:
            if p.refdes in block_of:
                continue
            if pin_counts.get(p.refdes, 0) > 2:
                continue
            for nbr in sorted(neighbours.get(p.refdes, set())):
                if nbr in block_of:
                    block_of[p.refdes] = block_of[nbr]
                    changed = True
                    break

    # 5. Singletons for anything left.
    for p in parts:
        block_of.setdefault(p.refdes, f"solo:{p.refdes}")

    return block_of


# ---------------------------------------------------------------------------
# Coarse force-directed placement
# ---------------------------------------------------------------------------


def _golden_nudge(index: int, base: float = 120.0) -> Tuple[float, float]:
    """Deterministic offset for the ``index``-th coincident point.

    Uses the golden angle so successive nudges fan out evenly. Seeded by
    a fixed constant so output is reproducible without ``random``.
    """
    angle = _GOLDEN_ANGLE * (index + (_FR_SEED % 97) / 97.0)
    radius = base * (1.0 + 0.15 * index)
    return (radius * math.cos(angle), radius * math.sin(angle))


def _block_members(blocks: Mapping[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for refdes in sorted(blocks):
        out.setdefault(blocks[refdes], []).append(refdes)
    return out


def _inter_block_edges(
    plan: DesignPlan, blocks: Mapping[str, str]
) -> dict[Tuple[str, str], int]:
    """Block-pair -> shared-net count (weighted block super-graph edges)."""
    edges: dict[Tuple[str, str], int] = {}
    for net in plan.nets:
        block_set = sorted({blocks.get(pr.refdes) for pr in net.pins if pr.refdes in blocks})
        block_set = [b for b in block_set if b is not None]
        for i, a in enumerate(block_set):
            for b in block_set[i + 1:]:
                edges[(a, b)] = edges.get((a, b), 0) + 1
    return edges


def _intra_block_edges(
    plan: DesignPlan, members: Sequence[str]
) -> set[Tuple[str, str]]:
    member_set = set(members)
    pairs: set[Tuple[str, str]] = set()
    for net in plan.nets:
        ms = sorted({pr.refdes for pr in net.pins if pr.refdes in member_set})
        for i, a in enumerate(ms):
            for b in ms[i + 1:]:
                pairs.add((a, b))
    return pairs


def _fr_solve(
    nodes: Sequence[str],
    edges: Mapping[Tuple[str, str], float],
    initial: Mapping[str, _Pt],
    sizes: Mapping[str, float],
    *,
    iterations: int,
    area_w: float,
    area_h: float,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
) -> dict[str, _Pt]:
    """Generic spring/repulsion solver on an arbitrary weighted graph.

    Ideal distance ``k = C*sqrt(area/N)``, pairwise repulsion ``k^2/d``,
    edge attraction ``d^2/k``, linear cooling, boundary pull toward the
    envelope. Deterministic: node order is the caller's (sorted) list; no
    random module.
    """
    n = max(1, len(nodes))
    area = max(1.0, area_w * area_h)
    k = _FR_C * math.sqrt(area / n)
    pos: dict[str, list[float]] = {r: [initial[r].x, initial[r].y] for r in nodes}

    if len(nodes) <= 1:
        return {r: _Pt(pos[r][0], pos[r][1]) for r in nodes}

    temp0 = 0.1 * math.sqrt(area)
    for it in range(max(1, iterations)):
        frac = it / max(1, iterations)
        temp = temp0 * max(_FR_COOL_FLOOR, 1.0 - frac)
        disp: dict[str, list[float]] = {r: [0.0, 0.0] for r in nodes}

        # Repulsion (all pairs).
        for i in range(len(nodes)):
            a = nodes[i]
            for j in range(i + 1, len(nodes)):
                b = nodes[j]
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy)
                if d < 1e-6:
                    nx, ny = _golden_nudge(i + j)
                    dx, dy = nx, ny
                    d = math.hypot(dx, dy) + 1e-6
                if d > _FR_REPEL_CUTOFF:
                    continue
                min_sep = sizes.get(a, 400.0) + sizes.get(b, 400.0)
                rep = (k * k) / d
                if d < min_sep:
                    rep += (min_sep - d) * 0.5
                ux, uy = dx / d, dy / d
                disp[a][0] += ux * rep
                disp[a][1] += uy * rep
                disp[b][0] -= ux * rep
                disp[b][1] -= uy * rep

        # Attraction along weighted edges.
        for (a, b), w in edges.items():
            if a not in pos or b not in pos:
                continue
            dx = pos[a][0] - pos[b][0]
            dy = pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) + 1e-6
            att = (d * d) / k * w
            ux, uy = dx / d, dy / d
            disp[a][0] -= ux * att
            disp[a][1] -= uy * att
            disp[b][0] += ux * att
            disp[b][1] += uy * att

        # Boundary pull keeps nodes in the sheet envelope.
        for r in nodes:
            x, y = pos[r]
            half = sizes.get(r, 400.0)
            if x < x_lo + half:
                disp[r][0] += (x_lo + half - x) * _BOUNDARY_K
            elif x > x_hi - half:
                disp[r][0] -= (x - (x_hi - half)) * _BOUNDARY_K
            if y < y_lo + half:
                disp[r][1] += (y_lo + half - y) * _BOUNDARY_K
            elif y > y_hi - half:
                disp[r][1] -= (y - (y_hi - half)) * _BOUNDARY_K

        # Apply, capped by temperature.
        for r in nodes:
            dx, dy = disp[r]
            d = math.hypot(dx, dy)
            if d > 1e-9:
                step = min(d, temp)
                pos[r][0] += (dx / d) * step
                pos[r][1] += (dy / d) * step
            pos[r][0] = max(x_lo, min(x_hi, pos[r][0]))
            pos[r][1] = max(y_lo, min(y_hi, pos[r][1]))

    return {r: _Pt(pos[r][0], pos[r][1]) for r in nodes}


def coarse_place(
    plan: DesignPlan,
    blocks: Mapping[str, str],
    *,
    iterations: int,
    grid_mils: int,
) -> dict[str, _Pt]:
    """Force-directed coarse placement.

    First runs FR on the block super-graph (blocks as nodes, inter-block
    shared-net count as edge weight), then FR within each block to spread
    members around the block centre. Returns continuous (pre-snap) centres.
    """
    members = _block_members(blocks)
    block_ids = sorted(members)
    pin_counts: dict[str, int] = {p.refdes: 0 for p in plan.parts}
    for net in plan.nets:
        for pr in net.pins:
            pin_counts[pr.refdes] = pin_counts.get(pr.refdes, 0) + 1

    # --- block super-graph ---
    block_edges_int = _inter_block_edges(plan, blocks)
    block_edges: dict[Tuple[str, str], float] = {
        k: float(v) for k, v in block_edges_int.items()
    }
    block_size: dict[str, float] = {}
    for bid in block_ids:
        member_pins = [pin_counts.get(r, 2) for r in members[bid]]
        block_size[bid] = max(
            _bbox_half(max(member_pins) if member_pins else 2),
            300.0 + 200.0 * math.sqrt(len(members[bid])),
        )

    cols = max(1, int(math.ceil(math.sqrt(len(block_ids)))))
    pitch = 2200.0
    cx0 = SHEET_ORIGIN_X_MILS + 800.0
    cy0 = SHEET_ORIGIN_Y_MILS + 800.0
    block_initial: dict[str, _Pt] = {}
    for i, bid in enumerate(block_ids):
        nx, ny = _golden_nudge(i, base=30.0)
        block_initial[bid] = _Pt(
            cx0 + (i % cols) * pitch + nx,
            cy0 + (i // cols) * pitch + ny,
        )

    block_centers = _fr_solve(
        block_ids,
        block_edges,
        block_initial,
        block_size,
        iterations=iterations,
        area_w=SHEET_MAX_X_MILS - SHEET_ORIGIN_X_MILS,
        area_h=SHEET_MAX_Y_MILS - SHEET_ORIGIN_Y_MILS,
        x_lo=SHEET_ORIGIN_X_MILS,
        x_hi=SHEET_MAX_X_MILS,
        y_lo=SHEET_ORIGIN_Y_MILS,
        y_hi=SHEET_MAX_Y_MILS,
    )

    # --- within-block FR ---
    out: dict[str, _Pt] = {}
    for bid in block_ids:
        mem = sorted(members[bid])
        center = block_centers[bid]
        if len(mem) == 1:
            out[mem[0]] = center
            continue
        sizes = {r: float(_bbox_half(pin_counts.get(r, 2))) for r in mem}
        intra = _intra_block_edges(plan, mem)
        local_edges: dict[Tuple[str, str], float] = {pair: 1.0 for pair in intra}
        span = block_size[bid] * 1.4
        local_initial: dict[str, _Pt] = {}
        for i, r in enumerate(mem):
            dx, dy = _golden_nudge(i, base=span / max(1, len(mem)))
            local_initial[r] = _Pt(center.x + dx, center.y + dy)
        local = _fr_solve(
            mem,
            local_edges,
            local_initial,
            sizes,
            iterations=max(20, iterations // 2),
            area_w=span * 2,
            area_h=span * 2,
            x_lo=max(SHEET_ORIGIN_X_MILS, center.x - span * 2),
            x_hi=min(SHEET_MAX_X_MILS, center.x + span * 2),
            y_lo=max(SHEET_ORIGIN_Y_MILS, center.y - span * 2),
            y_hi=min(SHEET_MAX_Y_MILS, center.y + span * 2),
        )
        out.update(local)
    return out


# ---------------------------------------------------------------------------
# Signal-flow ordering
# ---------------------------------------------------------------------------


def _net_source_sink(net: Net) -> Optional[str]:
    """Classify a net as a layout 'source' (left) hint.

    Power/ground nets are neither source nor sink for x-flow. A net whose
    role suggests an input/clock is a source. Returns ``"source"`` or
    ``None``.
    """
    role = (net.role or "").strip().lower()
    if role in ("input", "clock"):
        return "source"
    return None


def order_signal_flow(
    plan: DesignPlan,
    coarse: Mapping[str, _Pt],
    blocks: Mapping[str, str],
    *,
    alpha: float = 0.6,
) -> dict[str, _Pt]:
    """Impose schematic convention on the FR result.

    Builds a directed acyclic part graph (edges from edge-biased-left /
    source parts toward the rest), computes a longest-path rank per part,
    maps the rank to an x band (inputs left, outputs right), keeps the FR
    y as a barycenter with a power-top / ground-bottom bias, then blends
    ``x_final = alpha*rank_x + (1-alpha)*coarse_x``.
    """
    from eda_agent.design.force_directed import _edge_for_part

    parts = sorted(plan.parts, key=lambda p: p.refdes)
    refdes = [p.refdes for p in parts if p.refdes in coarse]
    part_by_refdes = {p.refdes: p for p in parts}
    if not refdes:
        return dict(coarse)

    # Directed edges: left-biased / source parts -> their net neighbours.
    adj: dict[str, set[str]] = {r: set() for r in refdes}
    indeg: dict[str, int] = {r: 0 for r in refdes}

    left_seed: set[str] = set()
    right_seed: set[str] = set()
    for p in parts:
        if p.refdes not in coarse:
            continue
        edge = _edge_for_part(plan, p)
        if edge == "left":
            left_seed.add(p.refdes)
        elif edge == "right":
            right_seed.add(p.refdes)

    for net in sorted(plan.nets, key=lambda n: n.name):
        if net.is_power or net.is_ground:
            continue
        members = sorted({pr.refdes for pr in net.pins if pr.refdes in coarse})
        if len(members) < 2:
            continue
        # Order members by current coarse x so the DAG follows left->right.
        members.sort(key=lambda r: (coarse[r].x, r))
        for i in range(len(members) - 1):
            a, b = members[i], members[i + 1]
            if b not in adj[a] and a not in adj.get(b, set()):
                adj[a].add(b)
                indeg[b] += 1

    # Longest-path rank via Kahn-style topological relaxation. Cycles are
    # broken by the coarse-x ordering above (edges only go to higher x).
    rank: dict[str, int] = {r: 0 for r in refdes}
    for r in sorted(left_seed):
        rank[r] = 0

    order = sorted(refdes, key=lambda r: (indeg[r], coarse[r].x, r))
    # Process repeatedly in coarse-x order so longest path settles.
    for _ in range(len(refdes)):
        changed = False
        for a in sorted(refdes, key=lambda r: (coarse[r].x, r)):
            for b in sorted(adj[a]):
                if rank[b] < rank[a] + 1:
                    rank[b] = rank[a] + 1
                    changed = True
        if not changed:
            break
    del order

    # SORTED, and this one changes the answer: max_rank is recomputed
    # from the ranks this loop is writing, so a different visit order
    # gives different ranks. A set of refdes visits in hash order.
    for r in sorted(right_seed):
        max_rank = max(rank.values()) if rank else 0
        rank[r] = max(rank[r], max_rank)

    max_rank = max(rank.values()) if rank else 0
    x_lo = SHEET_ORIGIN_X_MILS + 800.0
    x_hi = SHEET_MAX_X_MILS - 800.0
    span = x_hi - x_lo

    out: dict[str, _Pt] = {}
    for r in refdes:
        if max_rank > 0:
            rank_x = x_lo + span * (rank[r] / max_rank)
        else:
            rank_x = (x_lo + x_hi) / 2.0
        cx = coarse[r].x
        x_final = alpha * rank_x + (1.0 - alpha) * cx

        # Power-top / ground-bottom y bias.
        cy = coarse[r].y
        part = part_by_refdes[r]
        bias = _power_ground_y_bias(plan, part)
        y_final = cy + bias
        out[r] = _Pt(x_final, y_final)

    # Carry through any part not in the DAG (shouldn't happen, but safe).
    for r in coarse:
        out.setdefault(r, coarse[r])
    return out


def _power_ground_y_bias(plan: DesignPlan, part) -> float:
    """Small upward bias if a part touches power, downward if it touches
    ground only. Pulls supply rails toward the top, returns to bottom.
    """
    touches_power = False
    touches_ground = False
    for net in plan.nets:
        if not any(pr.refdes == part.refdes for pr in net.pins):
            continue
        if net.is_power:
            touches_power = True
        if net.is_ground:
            touches_ground = True
    if touches_power and not touches_ground:
        return -250.0
    if touches_ground and not touches_power:
        return 250.0
    return 0.0


# ---------------------------------------------------------------------------
# Rotations
# ---------------------------------------------------------------------------


def assign_rotations(
    plan: DesignPlan,
    positions: Mapping[str, _Pt],
    pin_geometry: Mapping[str, Sequence[PinSlot]],
) -> dict[str, int]:
    """Pick each part's rotation in {0,90,180,270}.

    Greedy single-pass Gauss-Seidel in refdes order: for each part, try
    every rotation and keep the one minimising the pin-level HPWL of its
    incident nets against already-fixed neighbours. Seeded by the
    schematic-convention rotation so a part with no useful incident
    geometry keeps the conventional orientation.
    """
    rotations: dict[str, int] = {}
    part_by_refdes = {p.refdes: p for p in plan.parts}

    # Net membership for HPWL evaluation.
    nets_of: dict[str, list[Net]] = {}
    for net in plan.nets:
        for pr in net.pins:
            nets_of.setdefault(pr.refdes, []).append(net)

    # Two Gauss-Seidel passes. In the first pass each part is evaluated
    # against a mix of fixed and merely-seeded neighbours; a part fixed
    # early can lock in an orientation that its later-fixed neighbour then
    # has to route around. The second pass re-evaluates every part with ALL
    # rotations known, which is what lets series-chain partners settle
    # face-to-face. Deterministic (same sorted order both passes).
    for _pass in range(2):
        for refdes in sorted(positions):
            part = part_by_refdes.get(refdes)
            if part is None:
                rotations[refdes] = 0
                continue
            seed = _rotation_for_part(part, list(plan.nets))
            if refdes not in rotations:
                rotations[refdes] = seed
            slots = pin_geometry.get(refdes, [])
            if not slots:
                continue

            center = (int(positions[refdes].x), int(positions[refdes].y))
            incident = nets_of.get(refdes, [])
            if not incident:
                continue

            best_rot = rotations[refdes]
            best_cost = None
            for rot in (0, 90, 180, 270):
                cost = _incident_hpwl(
                    refdes, rot, slots, center, incident, positions,
                    rotations, part_by_refdes, pin_geometry,
                )
                cost += _facing_penalty(
                    refdes, rot, slots, center, incident, positions,
                    rotations, pin_geometry,
                )
                if best_cost is None or cost < best_cost - 1e-9:
                    best_cost = cost
                    best_rot = rot
            rotations[refdes] = best_rot
    return rotations


_SIDE_DIR = {
    "left": (-1, 0),
    "right": (1, 0),
    "top": (0, 1),
    "bottom": (0, -1),
}


def _facing_penalty(
    refdes: str,
    rotation: int,
    slots: Sequence[PinSlot],
    center: Tuple[int, int],
    incident: Sequence[Net],
    positions: Mapping[str, _Pt],
    rotations: Mapping[str, int],
    pin_geometry: Mapping[str, Sequence[PinSlot]],
) -> float:
    """Penalty for pins that point AWAY from their 2-pin-net partner.

    HPWL alone cannot see orientation: flipping a 2-pin passive 180
    degrees barely moves its incident half-perimeters, so the greedy
    rotation pass happily leaves series-chain partners back-to-back --
    the connecting pins face opposite directions, a direct wire would
    pass through both bodies, and the router is forced into the visible
    wrap-around loop (the single ugliest artefact on small sheets).

    The professional convention this encodes: on a 2-pin net, the pin's
    outward direction should have a non-negative component toward the
    partner pin. Violations cost a fixed 400 mils plus half the partner
    distance, big enough to override the tiny HPWL asymmetries that
    otherwise pick the back-to-back flip, small enough not to fight the
    placement itself. Nets with 3+ pins are skipped (facing is ambiguous
    there; trunk routing handles them).
    """
    my_pins = _world_pins(center, rotation, slots)
    slot_by_number = {s.number: s for s in slots}
    total = 0.0
    for net in incident:
        if len(net.pins) != 2:
            continue
        mine = [pr for pr in net.pins if pr.refdes == refdes]
        theirs = [pr for pr in net.pins if pr.refdes != refdes]
        if len(mine) != 1 or len(theirs) != 1:
            continue
        my_pin = my_pins.get(mine[0].pin)
        my_slot = slot_by_number.get(mine[0].pin)
        if my_pin is None or my_slot is None:
            continue
        nbr = theirs[0]
        if nbr.refdes not in positions:
            continue
        nbr_center = (int(positions[nbr.refdes].x), int(positions[nbr.refdes].y))
        nbr_slots = pin_geometry.get(nbr.refdes, [])
        nbr_pins = _world_pins(
            nbr_center, rotations.get(nbr.refdes, 0), nbr_slots,
        )
        target = nbr_pins.get(nbr.pin, nbr_center)
        dx = target[0] - my_pin[0]
        dy = target[1] - my_pin[1]
        if dx == 0 and dy == 0:
            continue
        base = _SIDE_DIR.get(my_slot.side, (1, 0))
        ox, oy = _rotate_offset(base, rotation)
        dot = ox * dx + oy * dy
        if dot < 0:
            dist = abs(dx) + abs(dy)
            total += 400.0 + 0.5 * dist
    return total


def _incident_hpwl(
    refdes: str,
    rotation: int,
    slots: Sequence[PinSlot],
    center: Tuple[int, int],
    incident: Sequence[Net],
    positions: Mapping[str, _Pt],
    rotations: Mapping[str, int],
    part_by_refdes: Mapping[str, object],
    pin_geometry: Mapping[str, Sequence[PinSlot]],
) -> float:
    """Sum of incident-net half-perimeter wirelengths if ``refdes`` takes
    ``rotation``. Neighbour pins use their already-fixed rotation, or the
    neighbour centre when no geometry is available.
    """
    my_pins = _world_pins(center, rotation, slots)
    total = 0.0
    for net in incident:
        xs: list[int] = []
        ys: list[int] = []
        for pr in net.pins:
            if pr.refdes == refdes:
                if pr.pin in my_pins:
                    xs.append(my_pins[pr.pin][0])
                    ys.append(my_pins[pr.pin][1])
                continue
            if pr.refdes not in positions:
                continue
            nbr_center = (int(positions[pr.refdes].x), int(positions[pr.refdes].y))
            nbr_rot = rotations.get(pr.refdes, 0)
            nbr_slots = pin_geometry.get(pr.refdes, [])
            nbr_pins = _world_pins(nbr_center, nbr_rot, nbr_slots)
            if pr.pin in nbr_pins:
                xs.append(nbr_pins[pr.pin][0])
                ys.append(nbr_pins[pr.pin][1])
            else:
                xs.append(nbr_center[0])
                ys.append(nbr_center[1])
        if len(xs) >= 2:
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


# ---------------------------------------------------------------------------
# Snap + legalize
# ---------------------------------------------------------------------------


def compact_blocks(
    positions: Mapping[str, _Pt],
    blocks: Mapping[str, str],
    *,
    target_span_mils: float = _BLOCK_COMPACT_SPAN_MILS,
) -> dict[str, _Pt]:
    """Pull each block's members toward their shared centroid.

    Force-directed placement spreads a block's parts across the sheet, which
    pushes intra-block nets past the label-span gate so they stop being drawn
    as wires. Scaling each block's members uniformly toward their centroid
    keeps a small functional block tight enough to stay wire-traceable while
    preserving the relative (signal-flow) arrangement inside the block. Blocks
    that already fit, and singleton blocks, are left untouched. Deterministic.
    """
    out: dict[str, _Pt] = {r: _Pt(p.x, p.y) for r, p in positions.items()}
    members: dict[str, list[str]] = {}
    for refdes, blk in blocks.items():
        if refdes in out:
            members.setdefault(blk, []).append(refdes)
    for blk in sorted(members):
        refs = members[blk]
        if len(refs) < 2:
            continue
        cx = sum(out[r].x for r in refs) / len(refs)
        cy = sum(out[r].y for r in refs) / len(refs)
        xs = [out[r].x for r in refs]
        ys = [out[r].y for r in refs]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        if span <= target_span_mils or span <= 1e-6:
            continue
        scale = target_span_mils / span
        for r in sorted(refs):
            out[r] = _Pt(cx + (out[r].x - cx) * scale,
                         cy + (out[r].y - cy) * scale)
    return out


def align_flowed(
    positions: Mapping[str, _Pt],
    *,
    tol: float = _ALIGN_TOL_MILS,
    grid_mils: int = SNAP_GRID_MILS,
) -> dict[str, _Pt]:
    """Snap near-collinear symbol centres onto shared row/column lines.

    Node alignment is a standard drawing aesthetic: symbols whose x (or y)
    coordinates fall within ``tol`` of each other are grouped and snapped to
    the group's grid-snapped mean, so they line up on a common column (or
    row). Run before legalization, so the overlap-removal pass is the safety
    backstop: any alignment that would cause an overlap is undone there.
    Deterministic.
    """
    out: dict[str, _Pt] = {r: _Pt(p.x, p.y) for r, p in positions.items()}
    if len(out) < 2:
        return out

    def _snapped(axis) -> dict[str, float]:
        order = sorted(out, key=lambda r: (axis(out[r]), r))
        groups: list[list[str]] = []
        cur: list[str] = []
        last: Optional[float] = None
        for r in order:
            v = axis(out[r])
            if last is not None and v - last > tol:
                groups.append(cur)
                cur = []
            cur.append(r)
            last = v
        if cur:
            groups.append(cur)
        snap: dict[str, float] = {}
        for g in groups:
            mean = sum(axis(out[r]) for r in g) / len(g)
            line = round(mean / grid_mils) * grid_mils
            for r in g:
                snap[r] = float(line)
        return snap

    nx = _snapped(lambda p: p.x)
    ny = _snapped(lambda p: p.y)
    return {r: _Pt(nx[r], ny[r]) for r in out}


def snap_and_legalize(
    plan: DesignPlan,
    positions: Mapping[str, _Pt],
    rotations: Mapping[str, int],
    *,
    grid_mils: int,
    geometry: Mapping[str, Sequence[PinSlot]],
    sheet: str = "main",
    body_half: Mapping[str, int] | None = None,
) -> dict[str, PlacedSymbol]:
    """Snap centres to the grid, clamp to the sheet, remove body overlaps.

    Overlap removal is a deterministic iterative shove along the cheaper
    axis, snapping back to the grid each round so collinear rows/columns
    survive when possible. Builds the final :class:`PlacedSymbol` records
    with world pin coordinates.
    """
    refdes = sorted(positions)
    pin_counts: dict[str, int] = {p.refdes: 0 for p in plan.parts}
    for net in plan.nets:
        for pr in net.pins:
            pin_counts[pr.refdes] = pin_counts.get(pr.refdes, 0) + 1
    sheet_of = {p.refdes: p.sheet for p in plan.parts}

    # Real drawn bodies when the caller knows them; the pin-count
    # estimate otherwise. The estimate keys on the PLAN's pin count, so
    # it undersizes a large part wired on a few pins and oversizes a
    # passive by about five times.
    from eda_agent.design.force_directed import _half_map

    half = _half_map({r: pin_counts.get(r, 2) for r in refdes}, body_half)

    def snap(v: float) -> int:
        return int(round(v / grid_mils) * grid_mils)

    pos: dict[str, list[int]] = {}
    for r in refdes:
        # A PAIR per part: _half_map reports (hx, hy) so a tall thin body is
        # not separated horizontally by its height.
        hx, hy = half[r]
        x = max(SHEET_ORIGIN_X_MILS + hx, min(SHEET_MAX_X_MILS - hx, snap(positions[r].x)))
        y = max(SHEET_ORIGIN_Y_MILS + hy, min(SHEET_MAX_Y_MILS - hy, snap(positions[r].y)))
        pos[r] = [x, y]

    # Deterministic shove: push overlapping pairs apart along cheaper axis.
    for _ in range(60):
        any_overlap = False
        delta: dict[str, list[int]] = {r: [0, 0] for r in refdes}
        for i in range(len(refdes)):
            a = refdes[i]
            for j in range(i + 1, len(refdes)):
                b = refdes[j]
                if sheet_of.get(a) != sheet_of.get(b):
                    continue
                ax, ay = pos[a]
                bx, by = pos[b]
                # PER AXIS, so a tall thin part is not separated
                # horizontally by its height.
                ox = half[a][0] + half[b][0] + grid_mils - abs(ax - bx)
                oy = half[a][1] + half[b][1] + grid_mils - abs(ay - by)
                if ox <= 0 or oy <= 0:
                    continue
                any_overlap = True
                if ox <= oy:
                    axis = 0
                    push = ox
                    sign = 1 if (bx - ax) >= 0 else -1
                    if bx == ax:
                        sign = 1 if a < b else -1
                else:
                    axis = 1
                    push = oy
                    sign = 1 if (by - ay) >= 0 else -1
                    if by == ay:
                        sign = 1 if a < b else -1
                step = int(math.ceil(push / 2.0 / grid_mils) * grid_mils)
                delta[a][axis] -= sign * step
                delta[b][axis] += sign * step
        for r in refdes:
            for axis in (0, 1):
                lo = (SHEET_ORIGIN_X_MILS if axis == 0 else SHEET_ORIGIN_Y_MILS) + half[r][axis]
                hi = (SHEET_MAX_X_MILS if axis == 0 else SHEET_MAX_Y_MILS) - half[r][axis]
                new = pos[r][axis] + delta[r][axis]
                pos[r][axis] = snap(max(lo, min(hi, new)))
        if not any_overlap:
            break

    placed: dict[str, PlacedSymbol] = {}
    for r in refdes:
        x, y = pos[r]
        rot = rotations.get(r, 0)
        slots = geometry.get(r, [])
        pins = _world_pins((x, y), rot, slots)
        hx, hy = half[r]
        if pins:
            xs = [px for px, _ in pins.values()] + [x - hx, x + hx]
            ys = [py for _, py in pins.values()] + [y - hy, y + hy]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        else:
            bbox = (x - hx, y - hy, x + hx, y + hy)
        placed[r] = PlacedSymbol(
            refdes=r,
            sheet=sheet_of.get(r, sheet),
            x_mils=x,
            y_mils=y,
            rotation=rot,
            bbox=bbox,
            pins=pins,
        )
    return placed


# ---------------------------------------------------------------------------
# Net representation decision
# ---------------------------------------------------------------------------


def estimate_wire_bends(pins_world: Sequence[Tuple[int, int]]) -> int:
    """Cheap pre-route bend estimate for a candidate wire net.

    Models the net as an orthogonal trunk with branch stubs: the trunk runs
    along one axis and every pin that does not already share the busiest row
    (for a horizontal trunk) or column (for a vertical trunk) needs one bend
    to branch off it. The cheaper of the two trunk orientations is used.

    Returns 0 for a straight collinear run, 1 for a two-pin L or a single
    branch off a shared rail, and grows with the number of off-trunk
    branches (e.g. a rail with several taps in a row stays low, while a
    fully scattered net costs one bend per pin). Pure geometry.
    """
    if len(pins_world) < 2:
        return 0
    xs = [p[0] for p in pins_world]
    ys = [p[1] for p in pins_world]
    if len(set(xs)) == 1 or len(set(ys)) == 1:
        return 0
    n = len(pins_world)
    busiest_row = max(ys.count(y) for y in set(ys))
    busiest_col = max(xs.count(x) for x in set(xs))
    h_branches = n - busiest_row   # pins off the busiest row (horizontal trunk)
    v_branches = n - busiest_col   # pins off the busiest column (vertical trunk)
    return max(1, min(h_branches, v_branches))


def decide_net_representation(
    net: Net,
    refdes_to_zone: Mapping[str, Optional[str]],
    placed: Mapping[str, PlacedSymbol],
    *,
    max_wire_bends: int = 2,
    label_span_mils: int = 3000,
) -> NetDecision:
    """Single source of truth for the wire | net_label | power_port choice.

    Delegates the base tier to ``_wiring._net_representation`` (port for
    power/ground, label for forced/cross-zone, wire for single-zone) and
    then refines the ``wire`` verdict with a bend/span estimate: a wire
    whose route would exceed ``max_wire_bends`` or whose pin bounding box
    spans more than ``label_span_mils`` is promoted to ``net_label``.

    Maps the internal strings (``port`` -> ``power_port``,
    ``label_per_pin`` -> ``net_label``, ``wire`` -> ``wire``). Power/ground
    nets carry the ground style and orientation from ``_wiring``.
    """
    base = _wiring._net_representation(net, dict(refdes_to_zone))

    if base == "port":
        if net.is_ground:
            style = _wiring._ground_style(net.name)
        else:
            style = "circle"
        orientation = _wiring._power_port_orientation(0, net.is_ground)
        return NetDecision(
            net_name=net.name,
            kind="power_port",
            style=style,
            orientation=orientation,
        )

    if base == "label_per_pin":
        return NetDecision(net_name=net.name, kind="net_label")

    # base == "wire": refine with bend/span estimate.
    pins_world: list[Tuple[int, int]] = []
    for pr in net.pins:
        sym = placed.get(pr.refdes)
        if sym is None:
            continue
        if pr.pin in sym.pins:
            pins_world.append(sym.pins[pr.pin])
        else:
            pins_world.append((sym.x_mils, sym.y_mils))

    if len(pins_world) < 2:
        return NetDecision(net_name=net.name, kind="wire", bends=0, span_mils=0)

    xs = [p[0] for p in pins_world]
    ys = [p[1] for p in pins_world]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    bends = estimate_wire_bends(pins_world)

    if bends > max_wire_bends or span > label_span_mils:
        return NetDecision(
            net_name=net.name, kind="net_label", bends=bends, span_mils=span
        )
    return NetDecision(
        net_name=net.name, kind="wire", bends=bends, span_mils=span
    )


# ---------------------------------------------------------------------------
# Wire routing
# ---------------------------------------------------------------------------


def _stub_end(
    pin: Tuple[int, int], side: str, length: int
) -> Tuple[int, int]:
    """Pin stub far end. ``side`` gives the outward direction."""
    sx = {"left": -1, "right": 1, "top": 0, "bottom": 0}.get(side, 0)
    sy = {"left": 0, "right": 0, "top": 1, "bottom": -1}.get(side, 0)
    return (pin[0] + sx * length, pin[1] + sy * length)


def _pin_side_lookup(
    placed: Mapping[str, PlacedSymbol],
) -> dict[Tuple[str, str], str]:
    """No-op placeholder kept for symmetry; sides come from geometry."""
    return {}


def _orient_path(
    start: Tuple[int, int], end: Tuple[int, int]
) -> list[NetSegment]:
    """Bend-minimal orthogonal path between two points.

    Straight when collinear (0 bends); otherwise a single L (1 bend),
    horizontal-first. Pure geometry; short-circuits the Dijkstra for the
    common cases.
    """
    sx, sy = start
    ex, ey = end
    if sx == ex or sy == ey:
        if (sx, sy) == (ex, ey):
            return []
        return [NetSegment(sx, sy, ex, ey)]
    return [
        NetSegment(sx, sy, ex, sy),
        NetSegment(ex, sy, ex, ey),
    ]


def _dijkstra_route(
    start: Tuple[int, int],
    end: Tuple[int, int],
    *,
    grid_mils: int,
    bend_weight: float,
    occupied: Optional[frozenset] = None,
    cross_weight: float = 0.0,
) -> list[NetSegment]:
    """Bend-minimal Manhattan route on a partial grid spanning the two
    points' bounding box. State is (node, entry_dir); cost is
    length + bend_weight*bends + cross_weight per step onto an ``occupied``
    world cell (so later nets route around earlier wires). Falls back to a
    plain L if the grid is degenerate.
    """
    sx, sy = start
    ex, ey = end
    if sx == ex or sy == ey:
        return _orient_path(start, end)

    x0, x1 = (sx, ex) if sx <= ex else (ex, sx)
    y0, y1 = (sy, ey) if sy <= ey else (ey, sy)
    nx = (x1 - x0) // grid_mils + 1
    ny = (y1 - y0) // grid_mils + 1
    if nx > _MAX_ROUTE_CELLS or ny > _MAX_ROUTE_CELLS:
        return _orient_path(start, end)

    def node(px: int, py: int) -> Tuple[int, int]:
        return ((px - x0) // grid_mils, (py - y0) // grid_mils)

    start_n = node(sx, sy)
    end_n = node(ex, ey)

    # dir: 0=+x,1=-x,2=+y,3=-y,-1=none
    best: dict[Tuple[int, int, int], float] = {}
    prev: dict[Tuple[int, int, int], Optional[Tuple[int, int, int]]] = {}
    heap: list[Tuple[float, int, int, int]] = [(0.0, start_n[0], start_n[1], -1)]
    best[(start_n[0], start_n[1], -1)] = 0.0
    prev[(start_n[0], start_n[1], -1)] = None
    final: Optional[Tuple[int, int, int]] = None

    moves = [(1, 0, 0), (-1, 0, 1), (0, 1, 2), (0, -1, 3)]
    while heap:
        cost, cx, cy, cdir = heapq.heappop(heap)
        state = (cx, cy, cdir)
        if best.get(state, math.inf) < cost - 1e-9:
            continue
        if (cx, cy) == end_n:
            final = state
            break
        for dx, dy, ndir in moves:
            ax, ay = cx + dx, cy + dy
            if not (0 <= ax < nx and 0 <= ay < ny):
                continue
            step = float(grid_mils)
            turn = bend_weight * grid_mils if (cdir != -1 and cdir != ndir) else 0.0
            cross = 0.0
            if occupied and cross_weight > 0.0:
                if (x0 + ax * grid_mils, y0 + ay * grid_mils) in occupied:
                    cross = cross_weight * grid_mils
            ncost = cost + step + turn + cross
            nstate = (ax, ay, ndir)
            if ncost < best.get(nstate, math.inf) - 1e-9:
                best[nstate] = ncost
                prev[nstate] = state
                heapq.heappush(heap, (ncost, ax, ay, ndir))

    if final is None:
        return _orient_path(start, end)

    # Reconstruct grid path, then collapse collinear runs into segments.
    path_cells: list[Tuple[int, int]] = []
    cur: Optional[Tuple[int, int, int]] = final
    while cur is not None:
        path_cells.append((cur[0], cur[1]))
        cur = prev[cur]
    path_cells.reverse()

    pts = [(x0 + c[0] * grid_mils, y0 + c[1] * grid_mils) for c in path_cells]
    return _points_to_segments(pts)


def _points_to_segments(pts: Sequence[Tuple[int, int]]) -> list[NetSegment]:
    """Collapse a polyline of grid points into axis-aligned segments,
    merging collinear consecutive runs.
    """
    if len(pts) < 2:
        return []
    segs: list[NetSegment] = []
    run_start = pts[0]
    prev = pts[0]
    cur_dir: Optional[Tuple[int, int]] = None
    for p in pts[1:]:
        dx = (p[0] > prev[0]) - (p[0] < prev[0])
        dy = (p[1] > prev[1]) - (p[1] < prev[1])
        d = (dx, dy)
        if cur_dir is None:
            cur_dir = d
        elif d != cur_dir:
            segs.append(NetSegment(run_start[0], run_start[1], prev[0], prev[1]))
            run_start = prev
            cur_dir = d
        prev = p
    segs.append(NetSegment(run_start[0], run_start[1], prev[0], prev[1]))
    return [s for s in segs if not (s.x1 == s.x2 and s.y1 == s.y2)]


def route_wire_nets(
    decisions: Mapping[str, NetDecision],
    placed: Mapping[str, PlacedSymbol],
    *,
    membership: Optional[Mapping[str, list[Tuple[str, str]]]] = None,
    grid_mils: int,
    bend_weight: float = 5.0,
) -> dict[str, list[NetRoute]]:
    """Produce orthogonal route polylines for every ``wire``-tier net.

    Each pin gets a short stub; the stub ends are linked with bend-minimal
    Manhattan paths (straight / single-L short-circuit, otherwise a partial
    -grid Dijkstra whose cost is length + bend_weight*bends). Junctions are
    detected from coincident endpoints + T-intersections. Returns net-name
    -> list of :class:`NetRoute`.
    """
    # Stub geometry needs the pin side; recover it from each placed symbol's
    # pin position relative to its centre (outward direction).
    routes: dict[str, list[NetRoute]] = {}

    # We need, per net, the world pins and a stub direction. The decision
    # objects don't carry the Net, so the caller passes wire nets only via
    # `placed` + the decision map. Rebuild pin endpoints from placed pins:
    # decide_net_representation already filtered to wire kind.
    # Net membership (net -> [(refdes, pin)]) is passed in explicitly by the
    # caller, since a NetDecision only carries its kind, not its pins.
    if membership is None:
        # No membership supplied: nothing to route deterministically.
        return {}

    # Gather per-net pin geometry, then route shortest-span nets first so the
    # longer nets route around the wires already placed (occupancy-aware).
    pending: list[Tuple[int, str, list, list]] = []
    for net_name in sorted(n for n, d in decisions.items() if d.kind == "wire"):
        world: list[Tuple[int, int]] = []
        sides: list[str] = []
        for (refdes, pin) in membership.get(net_name, []):
            sym = placed.get(refdes)
            if sym is None or pin not in sym.pins:
                continue
            world.append(sym.pins[pin])
            sides.append(_infer_side(sym, sym.pins[pin]))
        if len(world) < 2:
            continue
        xs = [p[0] for p in world]
        ys = [p[1] for p in world]
        span = (max(xs) - min(xs)) + (max(ys) - min(ys))
        pending.append((span, net_name, world, sides))
    pending.sort(key=lambda t: (t[0], t[1]))

    occupied: set[Tuple[int, int]] = set()
    for _span, net_name, world, sides in pending:
        stub_ends = [
            _stub_end(world[i], sides[i], _STUB_MILS) for i in range(len(world))
        ]
        # Snap stub ends to grid.
        stub_ends = [
            (int(round(x / grid_mils) * grid_mils), int(round(y / grid_mils) * grid_mils))
            for (x, y) in stub_ends
        ]

        segments: list[NetSegment] = []
        # Stub segments pin -> stub end.
        for i in range(len(world)):
            if world[i] != stub_ends[i]:
                segments.append(
                    NetSegment(world[i][0], world[i][1], stub_ends[i][0], stub_ends[i][1])
                )

        # Connect stub ends as a chain in spatial order (deterministic),
        # avoiding the cells earlier nets already occupy.
        order = sorted(range(len(stub_ends)), key=lambda i: stub_ends[i])
        occ = frozenset(occupied)
        for k in range(len(order) - 1):
            a = stub_ends[order[k]]
            b = stub_ends[order[k + 1]]
            segments.extend(
                _dijkstra_route(a, b, grid_mils=grid_mils, bend_weight=bend_weight,
                                occupied=occ, cross_weight=_ROUTE_CROSS_WEIGHT)
            )

        segments = _merge_collinear(segments)
        junctions = tuple(
            _wiring._detect_junctions(
                [(s.x1, s.y1, s.x2, s.y2) for s in segments]
            )
        )
        routes[net_name] = [
            NetRoute(net_name=net_name, segments=tuple(segments), junctions=junctions)
        ]
        # Mark this net's cells so later (longer) nets route around them.
        for s in segments:
            occupied.update(_segment_cells(s, grid_mils))

    return routes


def _infer_side(sym: PlacedSymbol, pin: Tuple[int, int]) -> str:
    """Outward direction of a pin from the symbol centre."""
    dx = pin[0] - sym.x_mils
    dy = pin[1] - sym.y_mils
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    return "top" if dy >= 0 else "bottom"


def _segment_cells(seg: NetSegment, grid_mils: int) -> list[Tuple[int, int]]:
    """Grid cells a (grid-aligned) axis-parallel segment passes through.

    Endpoints are snapped to the grid so the cells line up with the world
    cells the Dijkstra router visits, making the occupancy check exact.
    """
    def snap(v: int) -> int:
        return int(round(v / grid_mils) * grid_mils)
    x1, y1, x2, y2 = snap(seg.x1), snap(seg.y1), snap(seg.x2), snap(seg.y2)
    cells: list[Tuple[int, int]] = []
    if y1 == y2:
        lo, hi = sorted((x1, x2))
        x = lo
        while x <= hi:
            cells.append((x, y1))
            x += grid_mils
    elif x1 == x2:
        lo, hi = sorted((y1, y2))
        y = lo
        while y <= hi:
            cells.append((x1, y))
            y += grid_mils
    return cells


def _merge_collinear(segments: Sequence[NetSegment]) -> list[NetSegment]:
    """Merge consecutive collinear same-direction segments and drop zero
    length ones. Order-preserving.
    """
    out: list[NetSegment] = []
    for s in segments:
        if s.x1 == s.x2 and s.y1 == s.y2:
            continue
        if out:
            last = out[-1]
            # Same horizontal line, touching.
            if last.y1 == last.y2 == s.y1 == s.y2 and last.x2 == s.x1:
                out[-1] = NetSegment(last.x1, last.y1, s.x2, s.y2)
                continue
            if last.x1 == last.x2 == s.x1 == s.x2 and last.y2 == s.y1:
                out[-1] = NetSegment(last.x1, last.y1, s.x2, s.y2)
                continue
        out.append(s)
    return out


# Side channel for net membership keyed by the decisions object identity.
# compute_schematic_layout registers it; route_wire_nets reads it. This
# keeps route_wire_nets' signature aligned with the spec (decisions +
# placed only) while still giving it the pin endpoints it needs.
# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _count_crossings(
    routes: Mapping[str, list[NetRoute]],
) -> int:
    """Proper orthogonal intersections between segments of DIFFERENT nets.

    Shared endpoints are excluded. A horizontal segment of one net crosses
    a vertical segment of another when they meet at an interior point of
    both.
    """
    horiz: list[Tuple[int, int, int, str]] = []  # x_lo, x_hi, y, net
    vert: list[Tuple[int, int, int, str]] = []    # x, y_lo, y_hi, net
    for net_name in sorted(routes):
        for route in routes[net_name]:
            for s in route.segments:
                if s.y1 == s.y2:
                    horiz.append((min(s.x1, s.x2), max(s.x1, s.x2), s.y1, net_name))
                elif s.x1 == s.x2:
                    vert.append((s.x1, min(s.y1, s.y2), max(s.y1, s.y2), net_name))
    count = 0
    for hx_lo, hx_hi, hy, hnet in horiz:
        for vx, vy_lo, vy_hi, vnet in vert:
            if hnet == vnet:
                continue
            if hx_lo < vx < hx_hi and vy_lo < hy < vy_hi:
                count += 1
    return count


def _count_bends(routes: Mapping[str, list[NetRoute]]) -> int:
    """Interior orientation flips summed over all route polylines."""
    bends = 0
    for net_name in sorted(routes):
        for route in routes[net_name]:
            dirs: list[str] = []
            for s in route.segments:
                if s.x1 == s.x2 and s.y1 == s.y2:
                    continue
                dirs.append("h" if s.y1 == s.y2 else "v")
            for i in range(1, len(dirs)):
                if dirs[i] != dirs[i - 1]:
                    bends += 1
    return bends


def _alignment_score(placed: Mapping[str, PlacedSymbol]) -> float:
    """Fraction of symbol centres sharing a snapped x or y line with at
    least one other symbol. Higher is better.
    """
    if len(placed) < 2:
        return 1.0
    xs: dict[int, int] = {}
    ys: dict[int, int] = {}
    for sym in placed.values():
        xs[sym.x_mils] = xs.get(sym.x_mils, 0) + 1
        ys[sym.y_mils] = ys.get(sym.y_mils, 0) + 1
    aligned = 0
    for sym in placed.values():
        if xs[sym.x_mils] >= 2 or ys[sym.y_mils] >= 2:
            aligned += 1
    return aligned / len(placed)


def _area_and_aspect(
    placed: Mapping[str, PlacedSymbol],
    routes: Mapping[str, list[NetRoute]],
) -> Tuple[int, float]:
    """Bounding-box area (W*H) of bodies + route segments, plus the aspect
    penalty in [0, 1).
    """
    xs: list[int] = []
    ys: list[int] = []
    for sym in placed.values():
        xs.extend([sym.bbox[0], sym.bbox[2]])
        ys.extend([sym.bbox[1], sym.bbox[3]])
    for route_list in routes.values():
        for route in route_list:
            for s in route.segments:
                xs.extend([s.x1, s.x2])
                ys.extend([s.y1, s.y2])
    if not xs or not ys:
        return 0, 0.0
    w = max(1, max(xs) - min(xs))
    h = max(1, max(ys) - min(ys))
    ratio = max(w, h) / min(w, h)
    aspect_pen = 1.0 - 1.0 / ratio
    return w * h, aspect_pen


def _length_stats(
    routes: Mapping[str, list[NetRoute]],
) -> Tuple[int, float]:
    """Total Manhattan length and the RMS spread of per-net lengths.

    The spread is the standard deviation (root-mean-square deviation from the
    mean) of the per-net lengths, NOT the variance. Variance carries mils^2
    units, so it grows with the square of wire length and swamps every other
    aesthetic term -- a layout that is uniform-ish but has one long bus scores
    catastrophically for a reason that has nothing to do with readability.
    The RMS spread is linear in mils, directly comparable to the total-length
    term, so 'wires are roughly even' stays a gentle tie-breaker behind the
    primary crossing / bend aesthetics rather than dominating the objective.
    """
    per_net: list[int] = []
    for net_name in sorted(routes):
        total = 0
        for route in routes[net_name]:
            for s in route.segments:
                total += abs(s.x1 - s.x2) + abs(s.y1 - s.y2)
        per_net.append(total)
    total_len = sum(per_net)
    if len(per_net) < 2:
        return total_len, 0.0
    mean = total_len / len(per_net)
    var = sum((v - mean) ** 2 for v in per_net) / len(per_net)
    return total_len, math.sqrt(var)


def score_layout(
    placed: Mapping[str, PlacedSymbol],
    routes: Mapping[str, list[NetRoute]],
    decisions: Mapping[str, NetDecision],
    *,
    weights: LayoutWeights,
) -> LayoutScore:
    """Compute the aesthetic badness number from the realised geometry.

    ``total = w_x*crossings + w_b*bends + w_a*(1-alignment) +
    w_aspect*aspect + w_area*length + w_v*length_spread``. Lower is better.
    The breakdown is preserved so failures explain themselves.
    """
    del decisions  # representation choice already realised in routes/placed

    crossings = _count_crossings(routes)
    bends = _count_bends(routes)
    alignment = _alignment_score(placed)
    alignment_pen = 1.0 - alignment
    area, aspect_pen = _area_and_aspect(placed, routes)
    total_len, length_spread = _length_stats(routes)

    breakdown = {
        "crossings": crossings * weights.crossings,
        "bends": bends * weights.bends,
        "alignment": alignment_pen * weights.alignment,
        "aspect": aspect_pen * weights.aspect,
        "length": total_len * weights.length,
        "length_spread": length_spread * weights.length_spread,
    }
    total = sum(breakdown.values())

    return LayoutScore(
        total=total,
        wire_crossings=crossings,
        total_bends=bends,
        alignment_penalty=alignment_pen,
        aspect_ratio_penalty=aspect_pen,
        total_wire_length=total_len,
        length_spread=length_spread,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Executor payload
# ---------------------------------------------------------------------------


def to_executor_payload(layout: SchematicLayout) -> dict:
    """Flatten a :class:`SchematicLayout` into the plain-dict contract the
    executor/emitter consume.

    Keys and units (mils, 0/90/180/270 rotation, 0-3 label orientation)
    match the ``sch_place_*`` tool surface. ``placements`` carry
    ``lib_reference`` etc. as empty strings here because the layout engine
    works on geometry only; the caller fills library identity from the
    plan's parts. No Altium import.
    """
    placements = []
    for refdes in sorted(layout.placed):
        sym = layout.placed[refdes]
        placements.append({
            "lib_reference": "",
            "library_path": "",
            "x": sym.x_mils,
            "y": sym.y_mils,
            "designator": sym.refdes,
            "rotation": sym.rotation,
            "footprint": "",
        })

    wires = []
    for net_name in sorted(layout.routes):
        for route in layout.routes[net_name]:
            for s in route.segments:
                wires.append({"x1": s.x1, "y1": s.y1, "x2": s.x2, "y2": s.y2})

    net_labels = []
    power_ports = []
    for net_name in sorted(layout.decisions):
        dec = layout.decisions[net_name]
        if dec.kind not in ("net_label", "power_port"):
            continue
        # One glyph per pin endpoint of the net.
        endpoints = layout.route_membership.get(net_name, [])
        for (refdes, pin) in endpoints:
            sym = layout.placed.get(refdes)
            if sym is None or pin not in sym.pins:
                continue
            px, py = sym.pins[pin]
            side = _infer_side(sym, (px, py))
            ex, ey = _stub_end((px, py), side, _STUB_MILS)
            if dec.kind == "net_label":
                net_labels.append({
                    "text": net_name,
                    "x": ex,
                    "y": ey,
                    "orientation": 0,
                })
            else:
                power_ports.append({
                    "text": net_name,
                    "x": ex,
                    "y": ey,
                    "style": dec.style or "circle",
                    "orientation": dec.orientation if dec.orientation is not None else 1,
                })

    junctions = [{"x": x, "y": y} for (x, y) in sorted(layout.junctions)]

    return {
        "placements": placements,
        "wires": wires,
        "net_labels": net_labels,
        "power_ports": power_ports,
        "junctions": junctions,
        "score": {
            "total": layout.score.total,
            "wire_crossings": layout.score.wire_crossings,
            "total_bends": layout.score.total_bends,
            "alignment_penalty": layout.score.alignment_penalty,
            "aspect_ratio_penalty": layout.score.aspect_ratio_penalty,
            "total_wire_length": layout.score.total_wire_length,
            "length_spread": layout.score.length_spread,
            "breakdown": dict(layout.score.breakdown),
        },
        "sheet": layout.sheet,
    }
