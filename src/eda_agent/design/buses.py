# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Bus detection for the schematic pipeline.

A *bus* is a group of related signal nets that run together between the same
devices -- a data bus (D0..D7), an address bus, a parallel-peripheral or
memory interface. A professional schematic draws such a group as a single
thick BUS line with short 45-degree bus entries tapping each pin, rather than
as N separate wires or N label pairs: it groups the signals visually and cuts
label clutter on wide interfaces.

This module only DETECTS buses (the structural grouping). The geometry (bus
polyline + entries + per-net labels) and the Altium emit are layered on top.

Detection is naming-agnostic -- it does NOT rely on ``D0/D1/...`` or bracket
notation -- because the project plans nets by topology, not by name. The
signature: a set of >= ``min_width`` SIGNAL nets that each connect exactly the
same set of parts, where that set holds at least two multi-pin parts (ICs).
The canonical hit is a wide data/address bus between two chips (each member net
= ``{U1, U2}``). Power and ground nets are never buses.

NDA scope: reads only the current plan's topology; no cross-project state.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from eda_agent.design.canvas import (
    BusEntry,
    BusSegment,
    NetLabel,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.plan import DesignPlan

# A part is treated as a bus endpoint device (not a passive) when it touches at
# least this many nets -- the same >=4 "this is an IC" heuristic used elsewhere
# in the design engine. A 2-pin passive never anchors a bus.
_IC_NET_THRESHOLD = 4

# Default minimum bus width. Below this, a few nets shared between two chips are
# more readable as individual wires/labels than as a bus glyph; real buses are
# 4 bits and up (a byte lane, a nibble, an address/data bus).
DEFAULT_MIN_WIDTH = 4


@dataclass(frozen=True)
class BusGroup:
    """One detected bus.

    ``nets`` are the member signal-net names (sorted, so the result is
    deterministic). ``parts`` is the set of refdes the bus spans -- typically
    the two ICs whose pins the bus connects. ``endpoints`` is the subset of
    ``parts`` that are multi-pin devices (the IC anchors the geometry will tap).
    """

    nets: tuple[str, ...]
    parts: frozenset[str]
    endpoints: frozenset[str]

    @property
    def width(self) -> int:
        return len(self.nets)


def _net_count_by_refdes(plan: DesignPlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for net in plan.nets:
        for pin_ref in dict.fromkeys(pr.refdes for pr in net.pins):
            counts[pin_ref] = counts.get(pin_ref, 0) + 1
    return counts


def detect_buses(
    plan: DesignPlan,
    *,
    min_width: int = DEFAULT_MIN_WIDTH,
) -> list[BusGroup]:
    """Find buses in ``plan`` (see module docstring for the signature).

    Returns a list of :class:`BusGroup`, deterministically ordered (by the
    member nets). Empty when no group of >= ``min_width`` signal nets shares the
    same multi-IC part set -- so a design with no wide interface is unaffected.
    """
    net_counts = _net_count_by_refdes(plan)
    ground_power = {
        n.name for n in plan.nets
        if n.is_power or n.is_ground or (n.role or "") == "ground"
    }

    # Group signal nets by the exact set of parts they connect.
    by_parts: dict[frozenset[str], list[str]] = {}
    for net in plan.nets:
        if net.name in ground_power:
            continue
        parts = frozenset(pr.refdes for pr in net.pins)
        if len(parts) < 2:
            continue
        by_parts.setdefault(parts, []).append(net.name)

    buses: list[BusGroup] = []
    for parts, nets in by_parts.items():
        if len(nets) < min_width:
            continue
        endpoints = frozenset(
            p for p in parts if net_counts.get(p, 0) >= _IC_NET_THRESHOLD)
        if len(endpoints) < 2:
            continue
        buses.append(BusGroup(
            nets=tuple(sorted(nets)), parts=parts, endpoints=endpoints))

    buses.sort(key=lambda b: b.nets)
    return buses


# Default geometry (mils). DEPTH is how far the bus line sits past the pin
# column; ENTRY is the 45-degree bus-entry size (the wire stub fills the rest).
_BUS_DEPTH = 500
_BUS_ENTRY = 100

# Unit outward vector for each pin orientation (0=right,1=up,2=left,3=down) and
# its 90-degree perpendicular (the axis the bus line runs along).
_DIR = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}


_INDEXED_NET = re.compile(r"^(.*?)(\d+)$")


def bus_name(nets: tuple[str, ...] | list[str]) -> Optional[str]:
    """Altium bus name for a group, e.g. ``("D0".."D7") -> "D[0..7]"``.

    Only when every member net is ``<prefix><index>`` with ONE shared prefix
    and a CONTIGUOUS index run (so ``D[0..7]`` never implies a missing bit).
    Returns ``None`` otherwise -- arbitrarily-named bus nets keep just their
    per-signal labels, no bus name (the connection still works via those).
    """
    matched = [_INDEXED_NET.match(n) for n in nets]
    if not matched or not all(matched):
        return None
    prefixes = {m.group(1) for m in matched}
    if len(prefixes) != 1:
        return None
    idx = sorted(int(m.group(2)) for m in matched)
    if idx != list(range(idx[0], idx[-1] + 1)):
        return None
    return f"{prefixes.pop()}[{idx[0]}..{idx[-1]}]"


_BUS_NAME = re.compile(r"^(.*?)\[(\d+)\.\.(\d+)\]$")


def bus_name_covers(label_text: str, net: str) -> bool:
    """Does a bus NAME label name this net? ``D[0..7]`` covers ``D3``.

    The inverse of :func:`bus_name`, and the shorts detector needs it: a
    bus line carries a name label like ``D[0..7]`` and runs past the very
    wires it collects, so comparing label text against net name as plain
    strings reports every one of those as a cross-net short and blocks
    the emit. Measured on the 8-bit inter-IC fixture: two such failures,
    on ``D0`` and ``D7``, for a bus that takes the sheet from 17 wire
    crossings to none.
    """
    m = _BUS_NAME.match(label_text or "")
    if m is None:
        return False
    n = _INDEXED_NET.match(net or "")
    if n is None or n.group(1) != m.group(1):
        return False
    lo, hi = int(m.group(2)), int(m.group(3))
    return lo <= int(n.group(2)) <= hi


@dataclass(frozen=True)
class BusGeometry:
    """Drawable pieces of one IC's bus stub: the bus polyline segment, the
    per-pin 45-degree entries, the short wire stubs from pin to entry, the
    per-net labels (which carry the actual connectivity), and an optional bus
    NAME label (``D[0..7]``) on the bus line."""

    segments: tuple[BusSegment, ...]
    entries: tuple[BusEntry, ...]
    stubs: tuple[WireSegment, ...]
    labels: tuple[NetLabel, ...]
    bus_label: Optional[NetLabel] = None
    # (net, refdes, pin) actually drawn. The caller erases a member's existing
    # wiring before redrawing, so it has to know which PINS came back, not just
    # which nets: a member can be combed at one pin of an IC and left bare at
    # another. Carries its own net rather than being read off in step with
    # ``stubs``, so the two cannot drift apart.
    covered: tuple[tuple[str, str, str], ...] = ()


def build_bus_geometry(
    bus: BusGroup,
    ic: SymbolInstance,
    plan: DesignPlan,
    sheet: str,
    *,
    depth: int = _BUS_DEPTH,
    entry: int = _BUS_ENTRY,
    restrict_nets: Optional[set[str]] = None,
) -> Optional[BusGeometry]:
    """Bus stub for ONE endpoint IC of ``bus``.

    Lays a bus line parallel to the IC's bus-pin column (the side most of the
    bus's pins point), ``depth`` mils out. Each member pin gets: a wire stub
    from the pin to the bus-entry start, a 45-degree :class:`BusEntry` onto the
    bus line, and a :class:`NetLabel` (the net's identity -- this is what makes
    the connection; the bus line is the visual grouping). All entries slant the
    same way for a clean comb. Returns ``None`` when fewer than two of the bus's
    pins are found on this IC on a single side (no clean bus to draw -- the
    caller falls back to per-pin labels). Pure geometry; nothing is mutated.

    ``restrict_nets`` limits the members considered, so the caller can draw
    the same subset at every endpoint of the bus. Without it the dominant-side
    filter below can keep a net here and drop it at the other end, which leaves
    that pin with nothing.
    """
    # EVERY pin of each member on this IC. Keying one pin per net dropped
    # the rest: a member that reaches the same IC twice (a passthrough, or a
    # buffer with its input and output on one net) was combed at one pin and
    # left bare at the other, with its original wiring already erased.
    net_pins: dict[str, list[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            if pr.refdes == ic.refdes:
                net_pins.setdefault(net.name, []).append(pr.pin)

    placed = []
    for net in bus.nets:
        if restrict_nets is not None and net not in restrict_nets:
            continue
        for pin_id in net_pins.get(net, ()):
            ep = ic.pin_world(pin_id)
            if ep is not None:
                placed.append((net, pin_id, ep))
    if len({n for n, _, _ in placed}) < 2:
        return None

    # Keep only the pins on the dominant side (a clean bus is one column).
    dom = Counter(ep.orientation for _, _, ep in placed).most_common(1)[0][0]
    placed = [(n, pid, ep) for n, pid, ep in placed if ep.orientation == dom]
    if len({n for n, _, _ in placed}) < 2:
        return None
    dx, dy = _DIR[dom]
    perp = (-dy, dx)  # bus line runs along this axis; entries slant +perp
    pxv, pyv = perp

    segments: list[BusSegment] = []
    entries: list[BusEntry] = []
    stubs: list[WireSegment] = []
    labels: list[NetLabel] = []
    landings: list[tuple[int, int]] = []
    covered: list[tuple[str, str, str]] = []
    for net, pin_id, ep in placed:
        covered.append((net, ic.refdes, pin_id))
        x, y = ep.x, ep.y
        # stub: pin -> entry start (depth - entry out along the pin direction)
        sx = x + dx * (depth - entry)
        sy = y + dy * (depth - entry)
        stubs.append(WireSegment(x, y, sx, sy, sheet=sheet, net=net))
        # 45-degree entry: out by `entry` along the pin dir AND along +perp
        lx = sx + (dx + pxv) * entry
        ly = sy + (dy + pyv) * entry
        entries.append(BusEntry(sx, sy, lx, ly, sheet=sheet, net=net))
        landings.append((lx, ly))
        # label on the stub, near the pin
        labels.append(NetLabel(text=net, x=x + dx * 150, y=y + dy * 150,
                               orientation=dom, sheet=sheet))

    # The landings are collinear along `perp` (same depth); the bus line spans
    # them, extended half an entry past each end so the comb sits ON the bus.
    proj = lambda p: p[0] * pxv + p[1] * pyv  # noqa: E731 - coord along perp
    lo = min(landings, key=proj)
    hi = max(landings, key=proj)
    seg = BusSegment(
        x1=lo[0] - pxv * entry, y1=lo[1] - pyv * entry,
        x2=hi[0] + pxv * entry, y2=hi[1] + pyv * entry, sheet=sheet)
    segments.append(seg)

    # Optional Altium bus NAME label (D[0..7]) at the bus line's far end.
    name = bus_name(bus.nets)
    name_label = None
    if name is not None:
        name_label = NetLabel(text=name, x=seg.x2 + pxv * entry,
                              y=seg.y2 + pyv * entry, orientation=dom,
                              sheet=sheet)

    return BusGeometry(
        segments=tuple(segments), entries=tuple(entries),
        stubs=tuple(stubs), labels=tuple(labels), bus_label=name_label,
        covered=tuple(covered))


def _geometry_covering_every_pin(
    bus: BusGroup, canvas, plan: DesignPlan,
) -> tuple[Optional[list[BusGeometry]], set[str]]:
    """Bus geometry that redraws EVERY pin of every member it keeps.

    A bus member connects through the net label the comb puts on each of its
    pins, and the caller erases that member's existing wires and labels before
    redrawing. So a member the comb reaches at some of its pins and not others
    is worse than not drawing the bus at all: the pins it missed end up on no
    wire, no label and no port.

    Coverage is therefore judged per PIN, against the member's full pin list in
    the plan. That subsumes the per-net case (a member absent from one
    endpoint's comb is missing all of that endpoint's pins) and also catches a
    member reaching one IC twice, or reaching a part that is not an endpoint of
    the bus at all.

    Narrowing the member set can move a dominant side and change what is
    coverable, so this re-solves until the kept set stops shrinking. Returns
    ``(geoms, members)``, or ``(None, set())`` when no bus of at least two
    fully-covered members survives.
    """
    insts = []
    for ic_ref in sorted(bus.endpoints):
        inst = canvas.instance_by_refdes(ic_ref)
        if inst is None:
            return None, set()
        insts.append(inst)

    required: dict[str, set[tuple[str, str]]] = {}
    for net in plan.nets:
        if net.name in bus.nets:
            required[net.name] = {(pr.refdes, pr.pin) for pr in net.pins}

    allowed: Optional[set[str]] = None
    for _ in range(len(bus.nets) + 1):
        geoms: list[BusGeometry] = []
        for inst in insts:
            geo = build_bus_geometry(
                bus, inst, plan, inst.sheet, restrict_nets=allowed)
            if geo is None:
                return None, set()
            geoms.append(geo)
        drawn: dict[str, set[tuple[str, str]]] = {}
        for geo in geoms:
            for net_name, refdes, pin_id in geo.covered:
                drawn.setdefault(net_name, set()).add((refdes, pin_id))
        kept = {n for n, need in required.items()
                if n in drawn and need <= drawn[n]}
        if len(kept) < 2:
            return None, set()
        if allowed is not None and kept == allowed:
            return geoms, kept
        allowed = kept
    return None, set()


def _wire_crossings(canvas, plan) -> int:
    # Local import: quality imports canvas, and buses imports canvas, so going
    # buses -> quality at call time avoids an import cycle at module load.
    from eda_agent.design.quality import score_canvas
    return score_canvas(canvas, plan).wire_crossings


def _bus_segment_crossings(canvas, bus_nets=frozenset()) -> int:
    """Crossings that INVOLVE a bus line, excluding its OWN members.

    score_canvas only sees WireSegments, so a bus line crossing a wire is
    invisible to the wire-crossing gate. This counts the axis-aligned crossings
    the added bus segments introduce: crossings over (wires + buses) minus the
    wires-only crossings, leaving exactly the bus-involved ones. A clean bus
    adds none; any > 0 means the bus line cuts across a wire and the caller
    should fall back to per-pin labels.

    ``bus_nets`` are the nets the bus CARRIES, and their stubs are exempt.
    Without that this counted the bus crossing its own entry stubs as a
    fault and declined buses that improve the drawing outright: measured
    on the 8-bit inter-IC fixture, drawing the bus takes the sheet from 17
    wire crossings to 0 and the score from 2039 to 300, and this gate
    reported 12 and reverted the lot. The same-net exemption is the rule
    ``_count_wire_crossings`` already applies to wires, where two segments
    of one net meeting is a junction rather than a readability fault.
    """
    from eda_agent.design.quality import _count_wire_crossings
    bus_segs = [(b.x1, b.y1, b.x2, b.y2) for b in canvas.buses]
    if not bus_segs:
        return 0
    foreign = [(w.x1, w.y1, w.x2, w.y2) for w in canvas.wires
               if w.net not in bus_nets]
    return (_count_wire_crossings(foreign + bus_segs)
            - _count_wire_crossings(foreign))


def apply_bus_drawing(
    canvas,
    plan: DesignPlan,
    *,
    min_width: int = DEFAULT_MIN_WIDTH,
    gate_crossings: bool = True,
) -> list[str]:
    """Redraw detected buses as bus glyphs on an already-wired canvas.

    Post-pass: for each detected bus whose geometry draws cleanly at BOTH
    endpoint ICs (:func:`build_bus_geometry` returns geometry, i.e. >=2 of its
    pins sit on one side), drop the bus nets' per-pin labels and any wires and
    add the bus line + 45-degree entries + stubs + labels instead. A bus that
    can't be drawn cleanly is left exactly as the wiring produced it (no
    regression).

    ``gate_crossings`` (default on) reverts the WHOLE change if it raised the
    wire-crossing count -- a bus is a readability win, so it must never add
    crossings; if it would, the per-pin form is kept. Returns the bus net names
    actually drawn as buses (empty when nothing was drawn). Mutates ``canvas``
    in place.
    """
    buses = detect_buses(plan, min_width=min_width)
    if not buses:
        return []

    drawable: list[tuple[BusGroup, list[BusGeometry]]] = []
    covered_nets: set[str] = set()
    for bus in buses:
        geoms, members = _geometry_covering_every_pin(bus, canvas, plan)
        if geoms is None:
            continue
        drawable.append((bus, geoms))
        covered_nets |= members
    if not drawable:
        return []

    # Only the nets the geometry actually draws. The bus's OWN net list is
    # wider than that: build_bus_geometry keeps one dominant pin column per
    # endpoint, so a member whose pin at some endpoint points elsewhere is not
    # in the comb. Stripping by bus.nets deleted such a net's wires and labels
    # and drew nothing back, leaving both its pins on no wire, no label and no
    # port. Found on a public XIAO carrier board: a six-net SPI/UART group
    # where TX left both the MCU and the header on a different side from the
    # other five, so TX alone was erased -- the pipeline warned that the net
    # was disconnected and shipped the sheet with ok=True anyway.
    #
    # MEASURED, old drawing against new, over the 69 public sheets that
    # have a bus group and place: 91 groups drawn / 498 members touched /
    # 76 members left disconnected, against 90 / 421 / 0. The whole cost of
    # the fix is one group and 77 comb members; the demo corpus shows none of
    # this, because its bus members leave their ICs in one tidy column, while
    # real boards route a group out of whichever side it fits.
    bus_nets = covered_nets
    saved = (list(canvas.wires), list(canvas.labels),
             list(canvas.buses), list(canvas.bus_entries))
    before = _wire_crossings(canvas, plan) if gate_crossings else 0

    canvas.wires[:] = [w for w in canvas.wires if w.net not in bus_nets]
    canvas.labels[:] = [l for l in canvas.labels if l.text not in bus_nets]
    for _bus, geoms in drawable:
        for geo in geoms:
            canvas.add_wires(geo.stubs)
            canvas.add_bus_entries(geo.entries)
            canvas.add_buses(geo.segments)
            canvas.add_labels(geo.labels)
            if geo.bus_label is not None:
                canvas.add_labels([geo.bus_label])

    # ON THE TOTAL, not on the addition. A bus REPLACES the per-pin wires
    # it collects, so the honest comparison is the whole sheet before
    # against the whole sheet after, counting the bus line's own crossings
    # as crossings. The old rule judged the addition in isolation and
    # ignored the removal, which refused a bus that took a board from 24
    # wire crossings to 10 because the bus line itself crossed one foreign
    # wire. A bus that adds crossings without removing any still fails
    # this, which is the case the gate exists for.
    after = (_wire_crossings(canvas, plan)
             + _bus_segment_crossings(canvas, bus_nets))
    if gate_crossings and after > before:
        (canvas.wires[:], canvas.labels[:],
         canvas.buses[:], canvas.bus_entries[:]) = saved
        return []
    return sorted(bus_nets)
