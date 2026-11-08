# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Silkscreen, assembly, courtyard, paste windows and pad clearance.

IPC-7351B fixes the lands and the courtyard; silkscreen, assembly lines,
polarity marks and line widths are "user-definable drafting items"
(Hausherr CADlib p. 18). The defaults below follow the drafting rules the
IPC-7351 committee chair published (Hausherr IPC paper, "Basic guidelines
and drafting recommendations"), each cited where it is set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from eda_agent.design.landpattern.ipc7351 import (
    COURTYARD_ROUND,
    EP_PASTE_COVERAGE,
    round_nearest,
    round_out,
)
from eda_agent.design.landpattern.model import Circle, Line, Pad, Polygon, Rect

__all__ = [
    "DraftingRules",
    "DEFAULT_RULES",
    "corner_ratio_for",
    "silk_outline",
    "pick_pin1_dot",
    "assembly_outline",
    "courtyard_rect",
    "ep_paste_windows",
    "enforce_pad_gaps",
    "Box",
]

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class DraftingRules:
    """Drafting defaults, mm. Override any of them per call."""

    #: Silkscreen line width, 0.2 mm (Hausherr IPC paper 2.a.iv).
    silk_width: float = 0.20
    #: Silkscreen to land clearance, 0.25 mm (Hausherr IPC paper 1.b and
    #: 2.a.i: "Silkscreen to exposed copper clearance setting defaults to
    #: 0.25 mm"). Measured from the line edge to the copper edge; the
    #: lands' mask openings are 1:1 (see ``mask_expansion``).
    silk_clearance: float = 0.25
    #: Silk pieces shorter than this after clipping are dropped.
    silk_min_segment: float = 0.20
    #: Pin 1 dot on silk, 0.5 mm (Hausherr IPC paper 3.a.i, "Default size
    #: is 0.5 mm minimum"; Hausherr CADlib p. 18, the 0.5 mm
    #: "post-assembly inspection dot").
    pin1_dot_diameter: float = 0.50
    #: Assembly outline width 0.1 mm (Hausherr IPC paper 2.b.ii).
    assembly_width: float = 0.10
    #: Assembly pin 1 chamfer: the polarity mark is 1 mm (Hausherr IPC
    #: paper 3.b.i); small bodies get a quarter of their short side.
    assembly_chamfer: float = 1.0
    #: Courtyard line width 0.05 mm, "The IPC LP Calculator uses 0.05 mm
    #: as the default line width" (Hausherr CADlib p. 18).
    courtyard_width: float = 0.05
    courtyard_round: float = COURTYARD_ROUND
    #: Rounded-rectangle lands: corner radius 25 % of the pad's short side,
    #: at most 0.25 mm (PCB Libraries forum topic 2590, T. Hausherr,
    #: 5 Mar 2024, describing the IPC-7351 based default).
    corner_ratio: float = 0.25
    corner_radius_max: float = 0.25
    #: Land to land clearance used to trim lands that crowd a thermal pad,
    #: a tab or a land on another side, 0.2 mm (Hausherr IPC paper 1.a;
    #: IPC-7351:2005 p. 14 note: keep 0.20 mm to the thermal tab by trimming
    #: the heel).
    min_pad_gap: float = 0.20
    #: Paste and mask 1:1 with the land: "The default solder mask and paste
    #: mask values are mentioned in the IPC-7351B as to be 1:1 scale of the
    #: land size" (Hausherr CADlib p. 18).
    paste_expansion: float = 0.0
    mask_expansion: float = 0.0
    #: Exposed pad paste coverage (TB389 p. 3, 50 to 80 %).
    ep_paste_coverage: float = EP_PASTE_COVERAGE
    #: Largest paste window cell. TB389 p. 2 puts thermal vias on a 1.0 to
    #: 1.2 mm grid; cells of up to 1.2 mm keep the windows between them.
    ep_paste_cell: float = 1.2
    #: Thermal land size: "max" follows Hausherr CADlib p. 30 ("1:1 scale
    #: for the maximum component lead size and land size"); "nom" follows
    #: TB389 p. 1 ("match 1:1 with the package exposed die pad").
    ep_land: str = "max"
    #: Assembly reference text height limits.
    ref_height_min: float = 0.30
    ref_height_max: float = 1.00


DEFAULT_RULES = DraftingRules()


def corner_ratio_for(width: float, height: float, rules: DraftingRules) -> float:
    short = min(width, height)
    if short <= 0:
        return 0.0
    return round(min(rules.corner_ratio, rules.corner_radius_max / short), 4)


# --- geometry helpers -----------------------------------------------------


def _grow(b: Box, m: float) -> Box:
    return (b[0] - m, b[1] - m, b[2] + m, b[3] + m)


def _union(boxes: Iterable[Box]) -> Box:
    bl = list(boxes)
    return (min(b[0] for b in bl), min(b[1] for b in bl),
            max(b[2] for b in bl), max(b[3] for b in bl))


def box_gap(a: Box, b: Box) -> float:
    """Euclidean gap between two boxes, negative overlap as -min overlap."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    if dx > 0 and dy > 0:
        return math.hypot(dx, dy)
    return max(dx, dy)


def _segment_inside_t(x1, y1, x2, y2, b: Box) -> Optional[tuple[float, float]]:
    """Liang-Barsky: the parameter range of the segment inside box ``b``."""
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - b[0]), (dx, b[2] - x1),
                 (-dy, y1 - b[1]), (dy, b[3] - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    if t1 - t0 <= 1e-12:
        return None
    return (t0, t1)


def clip_segment(x1, y1, x2, y2, keepouts: Sequence[Box],
                 min_len: float) -> list[tuple[float, float, float, float]]:
    """The parts of a segment outside every keepout, longer than min_len."""
    cuts = []
    for b in keepouts:
        r = _segment_inside_t(x1, y1, x2, y2, b)
        if r:
            cuts.append(r)
    cuts.sort()
    keep = []
    t = 0.0
    for a, c in cuts:
        if a > t:
            keep.append((t, a))
        t = max(t, c)
    if t < 1.0:
        keep.append((t, 1.0))
    length = math.hypot(x2 - x1, y2 - y1)
    out = []
    for a, c in keep:
        if (c - a) * length + 1e-9 >= min_len:
            out.append((round(x1 + (x2 - x1) * a, 4), round(y1 + (y2 - y1) * a, 4),
                        round(x1 + (x2 - x1) * c, 4), round(y1 + (y2 - y1) * c, 4)))
    return out


def pad_keepouts(pads: Sequence[Pad], margin: float) -> list[Box]:
    out = []
    for p in pads:
        b = p.bbox()
        m = margin + max(p.mask_expansion, 0.0)
        out.append(_grow(b, m))
    return out


# --- silkscreen -----------------------------------------------------------


def copper_boxes(polygons: Sequence[Any]) -> list[Box]:
    """The bounding box of each copper polygon (a land drawn as an outline)."""
    out = []
    for g in polygons:
        pts = list(getattr(g, "points", None) or [])
        if getattr(g, "layer", "copper") == "copper" and pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def silk_clear_of_lands(body: Box, pads: Sequence[Pad], rules: DraftingRules,
                        copper: Sequence[Box] = ()) -> list[tuple[float, float]]:
    """A rectangle around the body, each side moved out only as far as it
    must go to clear every land: the outline when lands sit at the body
    edge all round and the body outline itself would be cut away."""
    # A hair past the keepout, which counts its own edge as inside.
    margin = rules.silk_clearance + rules.silk_width / 2.0 + 0.005
    boxes = [_grow(b, margin) for b in pad_keepouts(pads, 0.0)]
    boxes += [_grow(b, margin) for b in copper]
    x0, y0, x1, y1 = body
    for bx0, by0, bx1, by1 in boxes:
        x0, y0 = min(x0, bx0), min(y0, by0)
        x1, y1 = max(x1, bx1), max(y1, by1)
    return [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]


def silk_outline(outline: Sequence[tuple[float, float]], pads: Sequence[Pad],
                 rules: DraftingRules, *, closed: bool = True,
                 copper: Sequence[Box] = ()) -> list[Line]:
    """Silk lines along ``outline`` with everything near copper cut away.

    The keepout around each land is the clearance plus half the line
    width, so the line EDGE stays ``silk_clearance`` off the copper.
    ``copper`` adds the boxes of lands drawn as outlines.
    """
    margin = rules.silk_clearance + rules.silk_width / 2.0
    keep = pad_keepouts(pads, margin) + [_grow(b, margin) for b in copper]
    pts = list(outline)
    if closed:
        pts = pts + [pts[0]]
    # Clip every edge, then join the surviving pieces that still touch into
    # chains, and judge the minimum length on the chain: an arc drawn as
    # many short edges must not vanish because each edge is short.
    pieces: list[tuple[float, float, float, float]] = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        pieces.extend(clip_segment(ax, ay, bx, by, keep, 1e-6))
    chains: list[list[tuple[float, float, float, float]]] = []
    for s in pieces:
        if chains and abs(chains[-1][-1][2] - s[0]) < 1e-6 and abs(
                chains[-1][-1][3] - s[1]) < 1e-6:
            chains[-1].append(s)
        else:
            chains.append([s])
    if closed and len(chains) > 1:
        a, z = chains[0], chains[-1]
        if abs(z[-1][2] - a[0][0]) < 1e-6 and abs(z[-1][3] - a[0][1]) < 1e-6:
            chains[0] = z + a
            chains.pop()
    lines: list[Line] = []
    for ch in chains:
        length = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in ch)
        if length + 1e-9 < rules.silk_min_segment:
            continue
        for s in ch:
            if math.hypot(s[2] - s[0], s[3] - s[1]) > 1e-6:
                lines.append(Line("silk", *s, width=rules.silk_width))
    return lines


def silk_arc_points(cx: float, cy: float, r: float, a0: float, a1: float,
                    n: int = 48) -> list[tuple[float, float]]:
    """Points along an arc (degrees, CCW), for clipping round outlines."""
    pts = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def pick_pin1_dot(candidates: Sequence[tuple[float, float]], pads: Sequence[Pad],
                  rules: DraftingRules, inside: Optional[Box] = None
                  ) -> Optional[Circle]:
    """The first candidate centre whose dot clears every land.

    A candidate that also fits inside ``inside`` (the courtyard before any
    silk is added) wins over one that does not, so the mark does not grow
    the courtyard when it has a choice.
    """
    r = rules.pin1_dot_diameter / 2.0
    keep = pad_keepouts(pads, rules.silk_clearance + r)
    clear = []
    for (x, y) in candidates:
        if any(b[0] < x < b[2] and b[1] < y < b[3] for b in keep):
            continue
        clear.append((x, y))
    if not clear:
        return None
    if inside is not None:
        for (x, y) in clear:
            if (inside[0] <= x - r and x + r <= inside[2]
                    and inside[1] <= y - r and y + r <= inside[3]):
                return Circle("silk", round(x, 4), round(y, 4), r, 0.0, True)
    x, y = clear[0]
    return Circle("silk", round(x, 4), round(y, 4), r, 0.0, True)


# --- assembly -------------------------------------------------------------


def assembly_outline(box: Box, rules: DraftingRules, *, chamfer: bool = True,
                     corner: tuple[int, int] = (-1, 1)) -> Polygon:
    """The body outline with a chamfer at the pin 1 corner.

    ``corner`` is (sign of x, sign of y) of that corner; (-1, 1), the top
    left, is the IPC zero orientation.
    """
    x0, y0, x1, y1 = box
    pts = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
    if chamfer:
        c = round(min(rules.assembly_chamfer, 0.25 * min(x1 - x0, y1 - y0)), 4)
        cx = x0 if corner[0] < 0 else x1
        cy = y1 if corner[1] > 0 else y0
        out = []
        for (px, py) in pts:
            if abs(px - cx) < 1e-9 and abs(py - cy) < 1e-9:
                # Replace the corner by its two chamfer points, in path order
                # (the path runs clockwise from the top left).
                nxt = {(x0, y1): ((x0, y1 - c), (x0 + c, y1)),
                       (x1, y1): ((x1 - c, y1), (x1, y1 - c)),
                       (x1, y0): ((x1, y0 + c), (x1 - c, y0)),
                       (x0, y0): ((x0 + c, y0), (x0, y0 + c))}[(px, py)]
                out.extend(nxt)
            else:
                out.append((px, py))
        pts = out
    return Polygon("assembly", [(round(a, 4), round(b, 4)) for a, b in pts],
                   rules.assembly_width)


def ref_height(box: Box, rules: DraftingRules) -> float:
    short = min(box[2] - box[0], box[3] - box[1])
    h = min(rules.ref_height_max, max(rules.ref_height_min, 0.3 * short))
    return round_nearest(h, 0.05)


# --- courtyard ------------------------------------------------------------


def courtyard_rect(pads: Sequence[Pad], body: Optional[Box], excess: float,
                   rules: DraftingRules, extra: Sequence[Box] = (),
                   copper: Sequence[Box] = ()) -> tuple[Box, dict]:
    """IPC courtyard: the larger of land pattern and body, plus the excess,
    each edge rounded outward. Silk that would fall outside it (``extra``)
    pushes it out, because "all silkscreen outline data [must] be located
    inside the placement courtyard" (Hausherr CADlib p. 18). ``copper``
    adds the boxes of lands drawn as outlines."""
    boxes = [p.bbox() for p in pads] + list(copper)
    if body is not None:
        boxes.append(body)
    base = _grow(_union(boxes), excess)
    step = rules.courtyard_round

    def rounded(b: Box) -> Box:
        return (round_out(b[0], step), round_out(b[1], step),
                round_out(b[2], step), round_out(b[3], step))

    cy = rounded(base)
    info = {"excess": excess, "round": step, "ipc": list(cy),
            "grown_for_silk": False}
    if extra:
        ex = _union(extra)
        if ex[0] < cy[0] or ex[1] < cy[1] or ex[2] > cy[2] or ex[3] > cy[3]:
            cy = rounded(_union([cy, _grow(ex, rules.courtyard_width / 2.0)]))
            info["grown_for_silk"] = True
    info["final"] = list(cy)
    return cy, info


def courtyard_polygon(b: Box, rules: DraftingRules) -> Polygon:
    x0, y0, x1, y1 = b
    return Polygon("courtyard", [(x0, y1), (x1, y1), (x1, y0), (x0, y0)],
                   rules.courtyard_width)


# --- exposed pad paste ------------------------------------------------------


def ep_paste_windows(cx: float, cy: float, w: float, h: float,
                     rules: DraftingRules) -> tuple[list[Rect], float]:
    """A grid of paste windows over a thermal land and the coverage reached.

    The land is cut into equal cells no larger than ``ep_paste_cell``; each
    window is its cell scaled by sqrt(coverage) about the cell centre, so
    the windows cover ``ep_paste_coverage`` of the land before rounding.
    Sizes round to 0.01 mm.
    """
    cov = rules.ep_paste_coverage
    nx = max(1, math.ceil(w / rules.ep_paste_cell - 1e-9))
    ny = max(1, math.ceil(h / rules.ep_paste_cell - 1e-9))
    cw, ch = w / nx, h / ny
    k = math.sqrt(cov)
    ww = round_nearest(cw * k, 0.01)
    wh = round_nearest(ch * k, 0.01)
    out = []
    for i in range(nx):
        for j in range(ny):
            out.append(Rect(round(cx - w / 2 + (i + 0.5) * cw, 4),
                            round(cy - h / 2 + (j + 0.5) * ch, 4), ww, wh))
    actual = (nx * ny * ww * wh) / (w * h) if w * h > 0 else 0.0
    return out, round(actual, 4)


# --- land clearance -----------------------------------------------------------


def _trim_inner(p: Pad, amount: float) -> None:
    """Shorten a land from its end nearest the origin, keeping the outer end.

    A row land's long axis points at the package centre, so its inner end
    is the heel; trimming the heel is the IPC remedy (IPC-7351:2005 p. 14
    note). The row (``group``) decides the axis when it is known.
    """
    along_x = p.width >= p.height
    if p.group in ("left", "right"):
        along_x = True
    elif p.group in ("top", "bottom"):
        along_x = False
    if along_x:
        new = max(p.width - amount, 0.0)
        shift = (p.width - new) / 2.0
        p.x = round(p.x + (shift if p.x > 0 else -shift), 6)
        p.width = round(new, 6)
    else:
        new = max(p.height - amount, 0.0)
        shift = (p.height - new) / 2.0
        p.y = round(p.y + (shift if p.y > 0 else -shift), 6)
        p.height = round(new, 6)


def enforce_pad_gaps(pads: list[Pad], min_gap: float,
                     min_length: float = 0.2) -> list[str]:
    """Trim lands that crowd a land of another number in another group.

    Lands in the same row (same ``group``) are sized by the side goal and
    left alone. Against a thermal pad or tab the signal land is trimmed;
    between two signal lands (corner lands of a quad) both are. Lands the
    caller placed by hand (group ``custom``, role ``mounting``) are never
    trimmed; an overlap there is reported by the builder instead. Returns
    a note per trimmed land.
    """
    notes: list[str] = []
    step = 0.01
    before = {id(p): (p.width, p.height) for p in pads}

    def fixed(p: Pad) -> bool:
        return p.group == "custom" or p.role == "mounting"

    for _ in range(400):
        worst = None
        for i, a in enumerate(pads):
            if a.kind != "smd" or fixed(a):
                continue
            for b in pads[i + 1:]:
                if b.kind != "smd" or a.number == b.number or fixed(b):
                    continue
                if a.group and a.group == b.group:
                    continue
                gap = box_gap(a.bbox(), b.bbox())
                if gap < min_gap - 1e-6 and (worst is None or gap < worst[0]):
                    worst = (gap, a, b)
        if worst is None:
            break
        gap, a, b = worst
        victims = []
        a_big = a.role in ("thermal", "tab")
        b_big = b.role in ("thermal", "tab")
        if a_big and not b_big:
            victims = [b]
        elif b_big and not a_big:
            victims = [a]
        else:
            victims = [a, b]
        trimmed = False
        for v in victims:
            if max(v.width, v.height) - step >= min_length:
                _trim_inner(v, step)
                trimmed = True
        if not trimmed:
            notes.append(
                f"lands {a.number} and {b.number} are {gap:.3f} mm apart, under "
                f"the {min_gap} mm clearance, and cannot be trimmed further")
            break
    for p in pads:
        w0, h0 = before[id(p)]
        if (round(w0, 6), round(h0, 6)) != (round(p.width, 6), round(p.height, 6)):
            notes.append(
                f"land {p.number} trimmed at its heel from {w0:.3f} x {h0:.3f} "
                f"to {p.width:.3f} x {p.height:.3f} mm to keep {min_gap} mm "
                "to a neighbouring land")
    return notes
