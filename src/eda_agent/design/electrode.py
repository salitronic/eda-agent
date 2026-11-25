# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Sensor electrode geometry: outlines for custom pads, in plain numbers.

Each pattern answers a list of electrodes. An electrode is one pad: a
designator, an anchor and an outline of ``[x, y]`` points, or ``[x, y,
sweep]`` where the edge to the next point is an arc of ``sweep`` degrees
(counterclockwise when positive), the form lib_add_footprint_regions and a
custom pad take. Outlines run counterclockwise. A meander also answers
tracks, the copper between its two pads.

Lengths are in whatever unit the caller works in; nothing here converts.

Patterns:
    interdigitated  two combs whose fingers interleave (capacitive,
                    humidity, conductivity and touch sensing)
    slider          a row of touch segments with interleaved teeth
    wheel           a ring cut into touch segments
    meander         a serpentine trace between two pads (heater, strain)

The capacitance of an interdigitated pair is estimated by conformal mapping
for thin fingers on a half-space of relative permittivity er under air: per
unit length of each gap, C = eps0 * (er + 1) / 2 * K(k) / K(k') with
k = sin(pi/2 * w / (w + g)) (the metallisation ratio of the period), summed
over the gaps between neighbouring fingers. It neglects the fingers' ends,
the buses and copper thickness, and is a first estimate, not a field solve.
"""
from __future__ import annotations

import math
from typing import Any

EPS0 = 8.8541878128e-12  # F/m

PATTERNS = ("interdigitated", "slider", "wheel", "meander")


def _ellipk(k: float) -> float:
    """The complete elliptic integral of the first kind K(k), by the
    arithmetic-geometric mean."""
    a, b = 1.0, math.sqrt(max(0.0, 1.0 - k * k))
    for _ in range(60):
        if abs(a - b) < 1e-15 * a:
            break
        a, b = (a + b) / 2.0, math.sqrt(a * b)
    return math.pi / (2.0 * a)


def idc_capacitance(fingers_total: int, overlap_m: float, width_m: float,
                    gap_m: float, er: float) -> float:
    """Capacitance in farads of an interdigitated pair, as the module text
    describes: ``fingers_total - 1`` gaps, each ``overlap_m`` long."""
    eta = width_m / (width_m + gap_m)
    k = math.sin(math.pi / 2.0 * eta)
    kp = math.sqrt(1.0 - k * k)
    per_len = EPS0 * (er + 1.0) / 2.0 * _ellipk(k) / _ellipk(kp)
    return max(0, fingers_total - 1) * overlap_m * per_len


def _comb(fingers: int, width: float, gap: float, length: float, bus: float,
          round_tips: bool) -> list[list[float]]:
    """One comb: a bus along y = 0..bus from x = 0, fingers rising from it at
    a pitch of 2 * (width + gap), each ``length`` long above the bus."""
    pitch = 2.0 * (width + gap)
    right = (fingers - 1) * pitch + width
    top = bus + length
    pts: list[list[float]] = [[0.0, 0.0], [right, 0.0]]
    for i in reversed(range(fingers)):
        x0, x1 = i * pitch, i * pitch + width
        if round_tips:
            r = width / 2.0
            pts.append([x1, top - r, 180.0])
            pts.append([x0, top - r])
        else:
            pts.append([x1, top])
            pts.append([x0, top])
        if i:
            pts.append([x0, bus])
            pts.append([x0 - pitch + width, bus])
    return pts


def _turn(pts: list[list[float]], cx: float, cy: float) -> list[list[float]]:
    """The outline turned half a turn about (cx, cy); still counterclockwise."""
    return [[2 * cx - p[0], 2 * cy - p[1], *p[2:]] for p in pts]


def _box(pts: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def interdigitated(fingers: int = 5, finger_width: float = 0.2, gap: float = 0.2,
                   finger_length: float = 4.0, bus_width: float = 0.5,
                   round_tips: bool = True, **_: Any) -> dict[str, Any]:
    """Two combs, ``fingers`` each, interleaved with ``gap`` between every
    finger and the other comb, centred on the origin. Comb 1's bus runs
    along the bottom, comb 2's along the top."""
    w, g, length, bus = finger_width, gap, finger_length, bus_width
    pitch = 2.0 * (w + g)
    a = _comb(fingers, w, g, length, bus, round_tips)
    # Comb 2: the same comb turned half a turn, its fingers between comb 1's.
    # Its bus lies one gap above comb 1's finger tips.
    height = 2.0 * bus + length + g
    width_all = (fingers - 1) * pitch + w + (w + g)
    b = _turn(a, (width_all - 0.0) / 2.0, height / 2.0)
    shift_x, shift_y = -width_all / 2.0, -height / 2.0
    a = [[p[0] + shift_x, p[1] + shift_y, *p[2:]] for p in a]
    b = [[p[0] + shift_x, p[1] + shift_y, *p[2:]] for p in b]
    overlap = length - g
    return {
        "pattern": "interdigitated",
        "electrodes": [
            {"designator": "1", "outline": a, "anchor": [0.0, shift_y + bus / 2.0]},
            {"designator": "2", "outline": b, "anchor": [0.0, -shift_y - bus / 2.0]},
        ],
        "tracks": [],
        "min_gap": g,
        "size": [width_all, height],
        "gaps_between_fingers": 2 * fingers - 1,
        "overlap": overlap,
    }


def slider(segments: int = 5, segment_width: float = 8.0, height: float = 8.0,
           gap: float = 0.5, teeth: int = 2, tooth_depth: float = 0.0,
           **_: Any) -> dict[str, Any]:
    """A row of touch segments, ``segment_width`` apart centre to centre,
    whose shared boundaries zig-zag with ``teeth`` teeth (``tooth_depth``
    deep, default 40 % of a segment), so a finger between two segments
    couples to both. Neighbours are ``gap`` apart; the outer ends are
    straight."""
    depth = tooth_depth or 0.4 * segment_width
    total = segments * segment_width
    left = -total / 2.0
    y0, y1 = -height / 2.0, height / 2.0
    step = height / max(1, teeth)
    # A slanted edge moved sideways by h is h * cos(slant) away from where
    # it was, so each side moves gap / 2 / cos(slant) to leave gap square
    # to the edge.
    half = gap / 2.0 * math.hypot(depth, step / 2.0) / (step / 2.0)

    def zigzag(b: float, offset: float) -> list[list[float]]:
        """The boundary at x = b moved ``offset``, bottom to top."""
        pts = []
        for t in range(teeth):
            ya = y0 + t * step
            pts.append([b - depth / 2.0 + offset, ya])
            pts.append([b + depth / 2.0 + offset, ya + step / 2.0])
        pts.append([b - depth / 2.0 + offset, y1])
        return pts

    electrodes = []
    for s in range(segments):
        xa = left + s * segment_width
        xb = xa + segment_width
        if s < segments - 1:
            right = zigzag(xb, -half)
        else:
            right = [[xb, y0], [xb, y1]]
        if s > 0:
            left_edge = list(reversed(zigzag(xa, half)))
        else:
            left_edge = [[xa, y1], [xa, y0]]
        pts = _dedupe(right + left_edge)
        electrodes.append({"designator": str(s + 1), "outline": pts,
                           "anchor": [(xa + xb) / 2.0, 0.0]})
    return {"pattern": "slider", "electrodes": electrodes, "tracks": [],
            "min_gap": gap, "size": [total, height]}


def wheel(segments: int = 3, outer_diameter: float = 20.0, inner_diameter: float = 8.0,
          gap: float = 0.5, **_: Any) -> dict[str, Any]:
    """A ring cut into ``segments`` equal sectors with a straight gap of
    ``gap`` between neighbours, the first centred on 90 degrees."""
    ro, ri = outer_diameter / 2.0, inner_diameter / 2.0
    span = 360.0 / segments
    electrodes = []
    for s in range(segments):
        mid = 90.0 + s * span
        # The gap is cut straight: each side moved gap/2 off the radial line.
        half_o = span / 2.0 - math.degrees(math.asin(min(1.0, gap / 2.0 / ro)))
        half_i = span / 2.0 - math.degrees(math.asin(min(1.0, gap / 2.0 / ri)))
        a0o, a1o = math.radians(mid - half_o), math.radians(mid + half_o)
        a0i, a1i = math.radians(mid - half_i), math.radians(mid + half_i)
        pts = [
            [ri * math.cos(a0i), ri * math.sin(a0i)],
            [ro * math.cos(a0o), ro * math.sin(a0o), 2.0 * half_o],
            [ro * math.cos(a1o), ro * math.sin(a1o)],
            [ri * math.cos(a1i), ri * math.sin(a1i), -2.0 * half_i],
        ]
        rm = (ro + ri) / 2.0
        electrodes.append({"designator": str(s + 1), "outline": pts,
                           "anchor": [rm * math.cos(math.radians(mid)),
                                      rm * math.sin(math.radians(mid))]})
    return {"pattern": "wheel", "electrodes": electrodes, "tracks": [],
            "min_gap": gap, "size": [outer_diameter, outer_diameter]}


def meander(turns: int = 6, trace_width: float = 0.3, gap: float = 0.3,
            run_length: float = 10.0, pad_size: float = 1.5, **_: Any) -> dict[str, Any]:
    """A serpentine of ``turns`` runs, ``run_length`` long, ``gap`` apart,
    between two square pads at its ends."""
    pitch = trace_width + gap
    height = (turns - 1) * pitch
    x0, x1 = -run_length / 2.0, run_length / 2.0
    y = -height / 2.0
    path = [[x0, y]]
    for t in range(turns):
        x_end = x1 if t % 2 == 0 else x0
        path.append([x_end, y])
        if t < turns - 1:
            y += pitch
            path.append([x_end, y])
    start, end = path[0], path[-1]
    tracks = [{"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "width": trace_width}
              for a, b in zip(path, path[1:])]
    electrodes = []
    for name, (px, py), side in (("1", start, -1.0), ("2", end, -1.0 if turns % 2 == 0 else 1.0)):
        cx = px + side * pad_size / 2.0
        h = pad_size / 2.0
        electrodes.append({"designator": name, "anchor": [cx, py], "outline": [
            [cx - h, py - h], [cx + h, py - h], [cx + h, py + h], [cx - h, py + h]]})
    resistance_squares = (turns * run_length + (turns - 1) * pitch) / trace_width
    return {"pattern": "meander", "electrodes": electrodes, "tracks": tracks,
            "min_gap": gap, "size": [run_length + 2 * pad_size, height + pad_size],
            "squares": resistance_squares}


def _dedupe(pts: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for p in pts:
        if not out or (abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9):
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out


def _flatten(outline: list[list[float]], step_deg: float = 10.0) -> list[tuple[float, float]]:
    """The outline as straight points, each arc edge cut every step_deg."""
    pts: list[tuple[float, float]] = []
    n = len(outline)
    for k, p in enumerate(outline):
        x, y = p[0], p[1]
        pts.append((x, y))
        sweep = p[2] if len(p) > 2 else 0.0
        if not sweep:
            continue
        nx, ny = outline[(k + 1) % n][0], outline[(k + 1) % n][1]
        chord = math.hypot(nx - x, ny - y)
        half = math.radians(sweep) / 2.0
        r = chord / 2.0 / math.sin(abs(half))
        mx, my = (x + nx) / 2.0, (y + ny) / 2.0
        d = r * math.cos(half)
        ux, uy = (nx - x) / chord, (ny - y) / chord
        sign = 1.0 if sweep > 0 else -1.0
        cx, cy = mx - sign * uy * d * (1 if abs(sweep) <= 180 else 1), my + sign * ux * d
        a0 = math.atan2(y - cy, x - cx)
        count = max(2, int(abs(sweep) / step_deg))
        for i in range(1, count):
            a = a0 + math.radians(sweep) * i / count
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _segments(pts: list[tuple[float, float]]):
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def _seg_dist(a, b, c, d) -> float:
    def pt_seg(p, s0, s1):
        vx, vy = s1[0] - s0[0], s1[1] - s0[1]
        L = vx * vx + vy * vy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - s0[0]) * vx + (p[1] - s0[1]) * vy) / L))
        return math.hypot(p[0] - s0[0] - t * vx, p[1] - s0[1] - t * vy)
    if _crosses(a, b, c, d):
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d), pt_seg(c, a, b), pt_seg(d, a, b))


def _crosses(a, b, c, d) -> bool:
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    return (orient(a, b, c) * orient(a, b, d) < 0) and (orient(c, d, a) * orient(c, d, b) < 0)


def check(result: dict[str, Any]) -> dict[str, Any]:
    """What the drawn copper measures: the smallest distance between two
    electrodes, and any outline that crosses itself."""
    flat = [_flatten(e["outline"]) for e in result.get("electrodes", [])]
    crossing = []
    for i, pts in enumerate(flat):
        segs = _segments(pts)
        bad = any(_crosses(*segs[a], *segs[b])
                  for a in range(len(segs)) for b in range(a + 2, len(segs))
                  if not (a == 0 and b == len(segs) - 1))
        if bad:
            crossing.append(result["electrodes"][i]["designator"])
    gap = math.inf
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            for s1 in _segments(flat[i]):
                for s2 in _segments(flat[j]):
                    gap = min(gap, _seg_dist(*s1, *s2))
    return {"measured_min_gap": None if gap == math.inf else round(gap, 6),
            "self_crossing": crossing}


def build(pattern: str, **params: Any) -> dict[str, Any]:
    """The pattern's electrodes, or ``{"error": ...}``."""
    makers = {"interdigitated": interdigitated, "slider": slider, "wheel": wheel,
              "meander": meander}
    maker = makers.get(str(pattern).strip().lower())
    if maker is None:
        return {"error": "pattern is one of " + ", ".join(PATTERNS)}
    return maker(**params)
