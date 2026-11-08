# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The datasheet's land-pattern figure beside the lands built from it.

Every check of a hand-built footprint compared the lands with the
builder's own reading of the drawing, so a misreading passed: the
BGT60LTR11AIP rows on an even 0.5 mm grid where the figure prints 0.75,
the TPSM63603 corner lands without their step, a chamfered SIM7080G pad
built square. Each showed at once with the zoomed figure next to the
lands, drawn alike: every land numbered, the gap between each pair of
neighbouring rows and columns written on, the land sizes listed. A bottom
view is matched by mirroring the lands.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable

#: Rows or columns closer than this are one line of lands.
_SAME = 0.002

#: Every ``compared`` id handed out, with the number of lands it covers:
#: lands placed one by one into a new footprint need one of these.
ISSUED: dict[str, int] = {}


def land_outlines(model: Any) -> list[list[tuple[float, float]]]:
    """The copper outlines of the lands that are not rectangles."""
    return [list(g.points) for g in getattr(model, "graphics", [])
            if getattr(g, "layer", "") == "copper"]


def compared_id(pads: Iterable[Any], view: str, outlines: Iterable[Any] = ()) -> str:
    """Identifies one set of lands as compared: any later change to a land
    changes it, so a footprint can be held to the lands that were looked at."""
    rows = sorted((str(p.number), round(float(p.x), 4), round(float(p.y), 4),
                   round(float(p.width), 4), round(float(p.height), 4),
                   round(float(getattr(p, "rotation", 0) or 0), 2),
                   str(getattr(p, "shape", ""))) for p in pads)
    shapes = sorted(tuple((round(float(x), 4), round(float(y), 4)) for x, y in o)
                    for o in outlines)
    return hashlib.sha1(repr((rows, shapes, view)).encode()).hexdigest()[:12]


def _lines(values: Iterable[float]) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > _SAME:
            out.append(v)
    return out


def _corners(p: Any, mirror: bool) -> list[tuple[float, float]]:
    w, h = float(p.width) / 2, float(p.height) / 2
    a = math.radians(float(getattr(p, "rotation", 0) or 0))
    c, s = math.cos(a), math.sin(a)
    pts = []
    for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h)):
        x, y = float(p.x) + dx * c - dy * s, float(p.y) + dx * s + dy * c
        pts.append((-x if mirror else x, y))
    return pts


def built_values(pads: list[Any]) -> list[float]:
    """Distances the built lands show: sizes, gaps between land lines,
    spans, and positions from the origin, per axis."""
    vals: set[float] = set()
    xs = _lines(float(p.x) for p in pads)
    ys = _lines(float(p.y) for p in pads)
    for line in (xs, ys):
        for i, a in enumerate(line):
            vals.add(round(abs(a), 3))
            for b in line[i + 1:]:
                vals.add(round(b - a, 3))
    for p in pads:
        vals.update((round(float(p.width), 3), round(float(p.height), 3)))
    for axis in ("x", "y"):
        lo = min(float(getattr(p, axis)) - float(p.width if axis == "x" else p.height) / 2
                 for p in pads)
        hi = max(float(getattr(p, axis)) + float(p.width if axis == "x" else p.height) / 2
                 for p in pads)
        vals.add(round(hi - lo, 3))
    return sorted(vals)


def draw_lands(pads: list[Any], height_px: int, view: str = "top",
               title: str = "", width_px: int = 0,
               outlines: Iterable[Any] = (),
               body: Any = None) -> Any:
    """A PIL image of the lands, as large as fits ``width_px`` by
    ``height_px``, annotated. ``body`` (x0, y0, x1, y1) is drawn dashed,
    so lands meant to straddle the body edge show where they sit."""
    from PIL import Image, ImageDraw, ImageFont

    mirror = view == "bottom"
    outlines = list(outlines)
    pts = [c for p in pads for c in _corners(p, mirror)]
    if body is not None:
        bx0, by0, bx1, by1 = (float(v) for v in body)
        if mirror:
            bx0, bx1 = -bx1, -bx0
        pts += [(bx0, by0), (bx1, by1)]
    pts += [(-x if mirror else x, y) for o in outlines for x, y in o]
    x_lo, x_hi = min(x for x, _ in pts), max(x for x, _ in pts)
    y_lo, y_hi = min(y for _, y in pts), max(y for _, y in pts)
    width_px = width_px or height_px
    margin = int(min(width_px, height_px) * 0.12)
    try:
        small = ImageFont.load_default(size=max(13, int(margin / 5)))
    except TypeError:  # Pillow before 10.1: one size only
        small = ImageFont.load_default()
    readable = 2.2 * getattr(small, "size", 11)  # a gap label needs this much room
    # The legend under the lands: its lines, and room kept for all of them.
    sizes: dict[tuple[float, float], list[str]] = {}
    for p in pads:
        sizes.setdefault((round(float(p.width), 3), round(float(p.height), 3)),
                         []).append(str(p.number))
    lines = [title or "built lands", "seen from below (mirrored)" if mirror
             else "seen from above", "red: gaps between centres of the most common land, mm"]
    if body is not None:
        lines.append(f"blue dashed: package body {bx1 - bx0:g} x {by1 - by0:g} mm")
    for (w, h), names in sorted(sizes.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"{w:g} x {h:g} mm: {len(names)} ({', '.join(names[:6])}"
                     f"{', ...' if len(names) > 6 else ''})")
    lines = lines[:9]
    line_h = getattr(small, "size", 11) * 1.25
    legend_h = len(lines) * line_h + 12
    scale = min((width_px - 2 * margin) / max(x_hi - x_lo, 0.2),
                max(height_px - 1.6 * margin - legend_h, 0.3 * height_px)
                / max(y_hi - y_lo, 0.2))
    width_px = int((x_hi - x_lo) * scale) + 2 * margin
    img = Image.new("RGB", (max(width_px, 420), height_px), "white")
    draw = ImageDraw.Draw(img)

    def px(x: float, y: float) -> tuple[float, float]:
        return (margin + (x - x_lo) * scale, margin + (y_hi - y) * scale)

    for p in pads:
        corners = [px(*c) for c in _corners(p, mirror)]
        if str(getattr(p, "shape", "")).lower() in ("circle", "round"):
            (cx, cy) = px(-float(p.x) if mirror else float(p.x), float(p.y))
            r = float(p.width) * scale / 2
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(232, 176, 96),
                         outline=(140, 80, 20))
        else:
            draw.polygon(corners, fill=(232, 176, 96), outline=(140, 80, 20))
    for o in outlines:
        draw.polygon([px(-x if mirror else x, y) for x, y in o],
                     fill=(232, 176, 96), outline=(140, 80, 20))
    if body is not None:  # dashed, over the lands so it shows where they cross it
        (ax, ay), (cx2, cy2) = px(bx0, by1), px(bx1, by0)
        dash = max(6, int(scale * 0.25))
        for (x1, y1, x2, y2) in ((ax, ay, cx2, ay), (cx2, ay, cx2, cy2),
                                 (cx2, cy2, ax, cy2), (ax, cy2, ax, ay)):
            n = max(1, int(math.hypot(x2 - x1, y2 - y1) / dash))
            for i in range(0, n, 2):
                t0, t1 = i / n, min(i + 1, n) / n
                draw.line([(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0),
                           (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)],
                          fill=(30, 90, 200), width=2)
    for p in pads:  # numbers over every land, drawn last so none is covered
        cx, cy = px(-float(p.x) if mirror else float(p.x), float(p.y))
        draw.text((cx, cy), str(p.number), fill=(20, 20, 120), font=small,
                  anchor="mm")

    # Gaps between neighbouring land lines, written along the top and left.
    # Gaps of the most common land size: lines of every kind of land
    # together made gaps no drawing prints.
    groups: dict[tuple[float, float], list[Any]] = {}
    for p in pads:
        groups.setdefault((round(float(p.width), 3), round(float(p.height), 3)),
                          []).append(p)
    common = max(groups.values(), key=len)
    xs = _lines((-float(p.x) if mirror else float(p.x)) for p in common)
    ys = _lines(float(p.y) for p in common)
    for a, b in zip(xs, xs[1:]):
        (xa, _), (xb, _) = px(a, y_hi), px(b, y_hi)
        y = margin * 0.55
        draw.line([(xa, y - 6), (xa, y + 6)], fill=(180, 0, 0))
        draw.line([(xb, y - 6), (xb, y + 6)], fill=(180, 0, 0))
        draw.line([(xa, y), (xb, y)], fill=(180, 0, 0))
        if xb - xa >= readable:
            draw.text(((xa + xb) / 2, y - 6), f"{b - a:.3f}".rstrip("0").rstrip("."),
                      fill=(180, 0, 0), font=small, anchor="mb")
    for a, b in zip(ys, ys[1:]):
        (_, ya), (_, yb) = px(x_lo, a), px(x_lo, b)
        x = margin * 0.55
        draw.line([(x - 6, ya), (x + 6, ya)], fill=(180, 0, 0))
        draw.line([(x - 6, yb), (x + 6, yb)], fill=(180, 0, 0))
        draw.line([(x, ya), (x, yb)], fill=(180, 0, 0))
        if ya - yb >= readable * 0.5:
            draw.text((x - 8, (ya + yb) / 2), f"{b - a:.3f}".rstrip("0").rstrip("."),
                      fill=(180, 0, 0), font=small, anchor="rm")
    draw.multiline_text((8, height_px - legend_h + 4), "\n".join(lines),
                        fill="black", font=small, spacing=int(line_h - getattr(small, "size", 11)))
    return img


def side_by_side(figure: Any, lands: Any) -> Any:
    from PIL import Image

    h = max(figure.height, lands.height)
    out = Image.new("RGB", (figure.width + lands.width + 12, h), "white")
    out.paste(figure, (0, 0))
    out.paste(lands, (figure.width + 12, 0))
    return out


#: A ball name: row letters then a column number.
_BALL_NAME = re.compile(r"^[A-Z]{1,2}\d{1,2}$")


def grid_named(pads: list[Any]) -> bool:
    """True when the lands carry ball names (A1, H8): their names come from
    a ball map, which a land-pattern figure often does not print."""
    names = [str(p.number) for p in pads]
    return len(names) >= 4 and sum(bool(_BALL_NAME.match(n)) for n in names) >= 0.8 * len(names)


def names_in_figure(pads: list[Any], printed: list[dict]) -> int:
    """How many of the lands' names the figure prints as text."""
    built = {str(p.number) for p in pads}
    return len({t["value"] for t in printed if t["value"] in built})


def axis_names(labels: list[dict], pads: list[Any]) -> list[dict]:
    """Ball names placed from a map that prints only its row letters down
    one side and its column numbers along another: each land's name is put
    where its letter's row meets its number's column."""
    letters = [t for t in labels if t["value"].isalpha()]
    numbers = [t for t in labels if t["value"].isdigit()]
    if len(letters) < 3 or len(numbers) < 3:
        return []

    def most(values: list[float]) -> float:
        bins: dict[float, int] = {}
        for v in values:
            bins[round(v, 2)] = bins.get(round(v, 2), 0) + 1
        return max(bins, key=bins.get)

    col_x = most([t["at"][0] for t in letters])  # the letters stand in a column
    row_y = most([t["at"][1] for t in numbers])  # the numbers stand in a row
    rows = {t["value"]: t["at"][1] for t in letters if abs(t["at"][0] - col_x) <= 0.03}
    cols = {t["value"]: t["at"][0] for t in numbers if abs(t["at"][1] - row_y) <= 0.03}
    out = []
    for p in pads:
        m = re.match(r"^([A-Z]{1,2})(\d{1,2})$", str(p.number))
        if m and m.group(1) in rows and m.group(2) in cols:
            out.append({"value": m.group(0), "at": [cols[m.group(2)], rows[m.group(1)]]})
    return out


def check_ball_names(pads: list[Any], printed: list[dict], view: str) -> dict:
    """Each ball name the ball map prints as text, matched by position to the
    land built there.

    The map and the lands are each scaled to their own extent and the
    lands may be turned a quarter turn at a time against the map (a
    footprint is often drawn turned), never mirrored: ``view`` is the side
    the map is drawn from, and a bottom view is mirrored to match. Returns
    ``{checked, mismatched, turned}`` for the turn that fits best, or
    ``{checked: 0}`` when the map prints too few names to place it.
    """
    built = {str(p.number): p for p in pads}
    seen: dict[str, dict] = {}
    for t in printed:
        if t["value"] in built:
            seen.setdefault(t["value"], t)
    tokens = list(seen.values())
    if len(tokens) < max(4, len(built) // 2):
        return {"checked": 0}
    mirror = view == "bottom"
    base = {n: ((-float(p.x) if mirror else float(p.x)), float(p.y)) for n, p in built.items()}

    def unit(vals: list[float]) -> tuple[float, float]:
        lo, hi = min(vals), max(vals)
        return lo, (hi - lo) or 1.0

    tx = unit([t["at"][0] for t in tokens])
    ty = unit([-t["at"][1] for t in tokens])  # the image's y runs down
    marks = [(t["value"], ((t["at"][0] - tx[0]) / tx[1], (-t["at"][1] - ty[0]) / ty[1]))
             for t in tokens]
    best: dict[str, Any] = {}
    for turn, f in ((0, lambda x, y: (x, y)), (90, lambda x, y: (-y, x)),
                    (180, lambda x, y: (-x, -y)), (270, lambda x, y: (y, -x))):
        pts = {n: f(x, y) for n, (x, y) in base.items()}
        bx = unit([x for x, _ in pts.values()])
        by = unit([y for _, y in pts.values()])
        norm = {n: ((x - bx[0]) / bx[1], (y - by[0]) / by[1]) for n, (x, y) in pts.items()}
        wrong = []
        for name, (u, v) in marks:
            here = min(norm, key=lambda n: (norm[n][0] - u) ** 2 + (norm[n][1] - v) ** 2)
            if here != name:
                wrong.append({"map": name, "built": here})
        if not best or len(wrong) < len(best["mismatched"]):
            best = {"checked": len(tokens), "mismatched": wrong, "turned": turn}
    return best
