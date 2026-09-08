# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Lay a repeated subcircuit out the way a human does: as one line.

WHY. Found by rendering rather than by any number: on a macropad sheet the
human drew nine switch-plus-diode pairs as a tidy grid with 18 wires and 8700
mils of wire, and the engine scattered the same 21 parts with 86 wires and
52000 mils. Every scalar measure on that sheet looked unremarkable. The
motif catalogue cannot express it: it names analogue SHAPES (bypass cap,
divider, RC low-pass), not "this same group appears nine times".

MEASURED before writing any placement rule. Over 707 eligible corpus sheets,
81 (11%) repeat a subcircuit three or more times, and of the 85 arrays those
sheets contain:

  - 37 are a single COLUMN and 23 a single ROW, so 71% are one line
  - 25 are a true grid, mostly 2xN
  - the pitch between instances is a median 500 mils, IQR 250 to 975

So this places a line, not a lattice. A 2xN grid laid as one line is still
regular, which is the property that was missing; a lattice can come later if
the line turns out to cost anything.

IDENTICAL INSTANCES ARE DRAWN IDENTICALLY. Each instance is re-seated on the
internal shape of one exemplar, so the ninth switch-diode pair looks like the
first. That is the half of "regular" that spacing alone does not give.

Naming-agnostic throughout: instances are found structurally, by the multiset
of refdes KINDS in a connected component over non-rail nets, never by parsing
an index out of a refdes.
"""
from __future__ import annotations

import collections
from typing import Any, Mapping, Optional

from eda_agent.design.layout import PlacedPart
from eda_agent.design.plan import DesignPlan

#: Spacing between neighbouring instances, mils. The human median is 500; the
#: pitch actually used is the larger of this and what the instance's own
#: extent needs, so a wide instance is not overlapped by its neighbour.
ARRAY_PITCH_MILS = 500

#: Fewest instances that count as an array. Two of anything is a coincidence;
#: the corpus measurement counted three or more.
ARRAY_MIN_INSTANCES = 3

#: Parts with more pins than this are cut out before grouping: an IC ties the
#: whole sheet into one component and there is then nothing repeated to see.
_MAX_INSTANCE_PIN_COUNT = 4

#: A net wider than this is a bus or a rail, not the inside of an instance.
_MAX_INSTANCE_NET_WIDTH = 6


def _kind(refdes: str) -> str:
    from eda_agent.design.motifs import _kind_from_refdes

    return _kind_from_refdes(refdes)


def find_repeated_groups(plan: DesignPlan) -> list[list[list[str]]]:
    """Sets of structurally identical multi-part groups, 3 or more of each.

    Returns a list of arrays; each array is a list of instances; each
    instance is a sorted list of refdes. Deterministic.
    """
    pin_count: collections.Counter = collections.Counter()
    for net in plan.nets:
        for pin_ref in net.pins:
            pin_count[pin_ref.refdes] += 1
    small = {p.refdes for p in plan.parts
             if pin_count[p.refdes] <= _MAX_INSTANCE_PIN_COUNT}
    if not small:
        return []

    parent = {r: r for r in small}

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for net in plan.nets:
        if net.is_power or net.is_ground:
            continue
        refs = sorted({pr.refdes for pr in net.pins} & small)
        if len(refs) > _MAX_INSTANCE_NET_WIDTH:
            continue
        for r in refs[1:]:
            union(refs[0], r)

    comps: dict[str, list[str]] = {}
    for r in sorted(small):
        comps.setdefault(find(r), []).append(r)

    by_signature: dict[str, list[list[str]]] = {}
    for members in comps.values():
        if len(members) < 2:
            continue
        sig = "+".join(sorted(_kind(r) for r in members))
        by_signature.setdefault(sig, []).append(sorted(members))
    return [instances for _sig, instances in sorted(by_signature.items())
            if len(instances) >= ARRAY_MIN_INSTANCES]


def _members_in_role_order(instance: list[str]) -> list[str]:
    """One stable ordering of an instance's members, shared by all instances.

    Members correspond across instances by (kind, refdes) order. Two members
    of the SAME kind inside one instance are interchangeable as far as this
    can tell, and putting them in refdes order at least makes the choice the
    same for every instance and every run.
    """
    return sorted(instance, key=lambda r: (_kind(r), r))


def _half(refdes: str, body_half, pin_counts) -> tuple[int, int]:
    if body_half and refdes in body_half:
        return body_half[refdes]
    from eda_agent.design.force_directed import _bbox_half

    h = _bbox_half(pin_counts.get(refdes, 2))
    return (h, h)


def resnap_repeated_arrays(
    plan: DesignPlan,
    placements: list[PlacedPart],
    *,
    body_half: Optional[Mapping[str, tuple[int, int]]] = None,
    pitch_mils: int = ARRAY_PITCH_MILS,
    grid_mils: int = 100,
) -> list[PlacedPart]:
    """Re-seat each repeated subcircuit as an evenly spaced line.

    Keeps the coarse decision the placer made: the line is centred on the
    instances' own median position, and their order along it is the order
    they were already in, so nothing crosses that did not cross before. What
    is imposed is regularity, which is what the placer has no way to express.

    The whole array is left alone when any slot would land on a part outside
    it. Half a regular array is worse than none, and the overlap is what the
    shove exists to prevent.
    """
    arrays = find_repeated_groups(plan)
    if not arrays:
        return placements
    by_refdes = {p.refdes: p for p in placements}
    pin_counts: collections.Counter = collections.Counter()
    for net in plan.nets:
        for pin_ref in net.pins:
            pin_counts[pin_ref.refdes] += 1

    def snap(v: float) -> int:
        return int(round(v / grid_mils)) * grid_mils

    out = dict(by_refdes)
    for instances in arrays:
        instances = [inst for inst in instances
                     if all(r in by_refdes for r in inst)]
        if len(instances) < ARRAY_MIN_INSTANCES:
            continue
        ordered = [_members_in_role_order(inst) for inst in instances]
        if len({len(o) for o in ordered}) != 1:
            continue                    # not really the same shape
        centroids = [
            (sum(by_refdes[r].x_mils for r in inst) / len(inst),
             sum(by_refdes[r].y_mils for r in inst) / len(inst))
            for inst in ordered
        ]
        # The line runs along whichever axis the instances are already more
        # spread on, so this regularises the placer's arrangement rather than
        # rotating it.
        spread_x = max(c[0] for c in centroids) - min(c[0] for c in centroids)
        spread_y = max(c[1] for c in centroids) - min(c[1] for c in centroids)
        along_x = spread_x >= spread_y
        order = sorted(range(len(ordered)),
                       key=lambda i: (centroids[i][0] if along_x
                                      else centroids[i][1], ordered[i][0]))

        # The exemplar is the tightest instance: the others are re-drawn on
        # its shape, so the array reads as one thing repeated.
        def extent(idx: int) -> int:
            xs = [by_refdes[r].x_mils for r in ordered[idx]]
            ys = [by_refdes[r].y_mils for r in ordered[idx]]
            return (max(xs) - min(xs)) + (max(ys) - min(ys))

        exemplar = min(range(len(ordered)), key=lambda i: (extent(i),
                                                           ordered[i][0]))
        ex_members = ordered[exemplar]
        ex_cx, ex_cy = centroids[exemplar]
        offsets = [(by_refdes[r].x_mils - ex_cx, by_refdes[r].y_mils - ex_cy)
                   for r in ex_members]

        # Pitch must clear the instance's own extent on the line's axis.
        inst_span = max(
            (max(by_refdes[r].x_mils for r in inst)
             - min(by_refdes[r].x_mils for r in inst)) if along_x
            else (max(by_refdes[r].y_mils for r in inst)
                  - min(by_refdes[r].y_mils for r in inst))
            for inst in ordered)
        widest = max(
            max(_half(r, body_half, pin_counts)[0 if along_x else 1]
                for r in inst)
            for inst in ordered)
        # A whole number of grid steps. A fractional pitch, or a centre on a
        # half step, is rounded per slot and the spacing comes out uneven:
        # 500 mils about a 4-slot midpoint gives offsets of 250 and 750, and
        # Python's round() takes 33.5 up and 38.5 down, so the gaps measured
        # 400, 600, 400. Even spacing is the whole point of this pass.
        pitch = max(pitch_mils, inst_span + 2 * widest + grid_mils)
        pitch = -(-pitch // grid_mils) * grid_mils

        n = len(order)
        mid_along = sorted(c[0] if along_x else c[1] for c in centroids)[n // 2]
        fixed = sorted(c[1] if along_x else c[0] for c in centroids)[n // 2]
        # Snap the line's ORIGIN once, then step by a grid-multiple
        # pitch, so every slot is on the grid and no slot is rounded
        # on its own. Snapping the midpoint and then subtracting half
        # the span put the origin on a half step (3350) and the gaps
        # came out 400, 600, 400 again.
        first = snap(mid_along - ((n - 1) * pitch) / 2.0)

        proposal: dict[str, tuple[int, int]] = {}
        for slot, idx in enumerate(order):
            centre = first + slot * pitch
            cx = centre if along_x else fixed
            cy = fixed if along_x else centre
            for member, (dx, dy) in zip(ordered[idx], offsets):
                proposal[member] = (snap(cx + dx), snap(cy + dy))

        inside = {r for inst in ordered for r in inst}
        if _any_collision(proposal, inside, out, pin_counts, body_half):
            continue
        for refdes, (nx, ny) in proposal.items():
            p = out[refdes]
            out[refdes] = PlacedPart(refdes=refdes, sheet=p.sheet, x_mils=nx,
                                     y_mils=ny, rotation=p.rotation)
    return [out[p.refdes] for p in placements]


def _any_collision(proposal, inside, placed, pin_counts, body_half) -> bool:
    """Would any proposed slot sit on a part OUTSIDE the array?

    Members of the array are not checked against each other: they are being
    laid out together on a pitch that already clears them, and measuring them
    against one another would have every instance block its neighbour.
    """
    from eda_agent.design.force_directed import bodies_overlap

    for refdes, (nx, ny) in proposal.items():
        mine = _half(refdes, body_half, pin_counts)
        for other, op in placed.items():
            if other in inside:
                continue
            if bodies_overlap(nx, ny, op.x_mils, op.y_mils, mine,
                              _half(other, body_half, pin_counts)):
                return True
    return False
