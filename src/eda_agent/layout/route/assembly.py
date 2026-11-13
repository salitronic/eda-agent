# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Assembly in the routing brief: where vias may go, how far apart parts
are, and which way parts face.

The figures are the AI's, from the assembler's design rules or IPC-7351
and the parts' datasheets, each with its source; this keeps and measures
them.

* ``via_to_pad``: a via keeps this far (edge to edge, mils) from every
  surface pad, its own net's too, so the paste does not wick down an
  open via and starve the joint. The router keeps it, the review measures
  it. A BGA's balls and an exposed pad take vias by design and are left
  out, with the parts they belong to.
* ``vias_under_parts`` false: no via under a part's body (its 3D body,
  or its courtyard when it has none), where an open via traps flux or
  shorts to a metal case. Same exceptions.
* ``part_gap``: the parts of the round's nets keep this far apart, body
  to body, for the pick-and-place nozzle, inspection and rework. The
  placement sets it, so the review only names a pair that falls short.

``orient`` rules name parts that must face one way: at ``rotation`` (and
on ``side``) when given, else all alike, as polarised parts in a row are
set for inspection; and ``pins``, each pin of a part and the net it must
be on ("D1.K": "VIN"), from the datasheet's polarity marking.
"""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np

from .. import geom
from ..audit import body_outline
from ..model import LayoutBoard

#: Degrees two rotations may differ and still be one.
ANGLE_TOL = 0.5


def _under(comp) -> list[list[tuple[float, float]]]:
    """The outlines of a part's body: its 3D bodies, else its courtyard."""
    rings = [list(b.get("outline") or []) for b in comp.bodies or []]
    rings = [r for r in rings if len(r) >= 3]
    if rings:
        return rings
    ring = body_outline(comp)
    return [ring] if ring else []


def via_bars(board: LayoutBoard, rule, via_pads: Iterable[int] = ()) -> list[tuple]:
    """What keeps a via out, as (shape, the gap a via's edge keeps from it,
    'pad' or 'body', the pad key or part). ``via_pads`` are the pads that
    may take a via (BGA balls, exposed pads), left out with their parts."""
    if rule is None:
        return []
    free = set(via_pads)
    exempt = {board.pads[i].comp for i in free if board.pads[i].comp}
    out = []
    if rule.via_to_pad:
        for i, p in enumerate(board.pads):
            if i in free or p.comp in exempt or not p.is_smd:
                continue
            for c in p.copper:
                s = p.shape_on(c.layer)
                if s is not None:
                    out.append((s, rule.via_to_pad, "pad", p.key))
    if rule.vias_under_parts is False:
        for comp in board.components:
            if comp.ref in exempt:
                continue
            for ring in _under(comp):
                out.append((geom.polygon(ring), 0.0, "body", comp.ref))
    return out


def barred(bars: list[tuple], x: float, y: float, diameter: float) -> Optional[tuple]:
    """The first bar a via at (x, y) breaks, with its gap: (bar, gap)."""
    disc = geom.circle(x, y, diameter)
    for bar in bars:
        s, keep = bar[0], bar[1]
        if not geom.bboxes_near(disc, s, keep):
            continue
        g = geom.clearance(disc, s)
        if g < keep - 1e-6:
            return bar, g
    return None


def bar_cells(bars: list[tuple], spec, radius: float) -> np.ndarray:
    """(k, 2) grid cells (row, column) a via of ``radius`` cannot be
    centred on."""
    from .grid import shape_distance

    out = []
    for s, keep, _what, _owner in bars:
        reach = radius + keep
        x0, y0, x1, y1 = s.bbox
        i0, j0, i1, j1 = spec.window(x0 - reach, y0 - reach, x1 + reach, y1 + reach)
        if i0 >= i1 or j0 >= j1:
            continue
        X = spec.x(np.arange(i0, i1))[None, :]
        Y = spec.y(np.arange(j0, j1))[:, None]
        ys, xs = np.nonzero(shape_distance(s, X, Y) < reach - 1e-6)
        out.append(np.stack([ys + j0, xs + i0], axis=1))
    if not out:
        return np.zeros((0, 2), dtype=np.int64)
    return np.unique(np.concatenate(out).astype(np.int64), axis=0)


def via_misses(vias: Iterable, rule, board: LayoutBoard,
               via_pads: Iterable[int] = ()) -> list[tuple]:
    """(via, bar, gap) for each via that breaks the rule."""
    bars = via_bars(board, rule, via_pads)
    if not bars:
        return []
    out = []
    for v in vias:
        hit = barred(bars, v.x, v.y, v.diameter)
        if hit is not None:
            out.append((v, hit[0], hit[1]))
    return out


def gap_misses(board: LayoutBoard, rule, refs: Iterable[str]) -> list[tuple]:
    """(a, b, gap) for each pair of parts on one side, one of them in
    ``refs``, whose bodies are closer than the rule's part_gap."""
    if rule is None or not rule.part_gap:
        return []
    want = set(refs)
    shapes = []
    for c in board.components:
        ring = body_outline(c)
        if ring:
            shapes.append((c, geom.polygon(ring)))
    out = []
    for i in range(len(shapes)):
        ca, sa = shapes[i]
        for j in range(i + 1, len(shapes)):
            cb, sb = shapes[j]
            if ca.side != cb.side or not ({ca.ref, cb.ref} & want):
                continue
            if not geom.bboxes_near(sa, sb, rule.part_gap):
                continue
            g = geom.clearance(sa, sb)
            if g < rule.part_gap - 1e-6:
                a, b = sorted((ca.ref, cb.ref))
                out.append((a, b, max(g, 0.0)))
    return out


def _turn(a: float) -> float:
    return a % 360.0


def _same(a: float, b: float) -> bool:
    d = abs(_turn(a) - _turn(b))
    return min(d, 360.0 - d) <= ANGLE_TOL


def orient_misses(board: LayoutBoard, rules: Iterable) -> list[tuple]:
    """(rule, part, what, text) for each part an orient rule finds facing
    the wrong way or with a pin on the wrong net."""
    comps = {c.ref: c for c in board.components}
    pads = {p.key: p for p in board.pads if p.comp}
    out = []
    for r in rules:
        placed = [comps[p] for p in r.parts if p in comps]
        if r.rotation is not None:
            for c in placed:
                if not _same(c.rotation, r.rotation) or (r.side and c.side != r.side):
                    out.append((r, c.ref, "rotation",
                                f"{c.ref} is at {_turn(c.rotation):g} degrees on {c.side}, "
                                f"where it should be at {_turn(r.rotation):g}"
                                + (f" on {r.side}" if r.side else "")))
        elif len(placed) > 1:
            first = placed[0]
            for c in placed[1:]:
                if c.side != first.side or not _same(c.rotation, first.rotation):
                    out.append((r, c.ref, "rotation",
                                f"{c.ref} is at {_turn(c.rotation):g} degrees on {c.side}, "
                                f"{first.ref} at {_turn(first.rotation):g} on {first.side}; "
                                "the rule wants them alike"))
        for key, net in r.pins.items():
            p = pads.get(key)
            if p is not None and p.net != net:
                out.append((r, p.comp, f"pin:{p.name}",
                            f"{key} is on {p.net or 'no net'}, where it should be on {net}"))
    return out


__all__ = ["ANGLE_TOL", "bar_cells", "barred", "gap_misses", "orient_misses",
           "via_bars", "via_misses"]
