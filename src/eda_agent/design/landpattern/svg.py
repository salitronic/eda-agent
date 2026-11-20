# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SVG view of a :class:`FootprintModel`, for a person to look at.

Copper, holes, paste windows, silkscreen, assembly outline, courtyard,
pad numbers, the origin and a 1 mm scale bar, Y up as in the model. The
output is a self-contained SVG with a viewBox in millimetres; to get a
PNG use ``eda_agent.render.rasterize_svg`` after ``size_svg_for_raster``,
the same path the board renders use.
"""

from __future__ import annotations

import html
import math
from typing import Optional, Sequence

from eda_agent.design.landpattern.model import (
    Arc,
    Circle,
    FootprintModel,
    Line,
    Pad,
    Polygon,
    Text,
)

__all__ = ["render_svg"]

_STYLE = {
    "bg": "#fbfbf7",
    "copper": "#c8792e",
    "copper_tht": "#b58a22",
    "hole": "#fbfbf7",
    "hole_edge": "#3a3a3a",
    "npth": "#7d7d7d",
    "paste": "#5f6b73",
    "silk": "#141414",
    "assembly": "#2763c4",
    "courtyard": "#c400c4",
    "number": "#ffffff",
    "origin": "#808080",
    "text": "#222222",
    "terminal": "#1f8a3c",
}


def _f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pad_shape(p: Pad, fill: str, extra: str = "") -> str:
    cx, cy = p.x, -p.y
    w, h = p.width, p.height
    rot = f' transform="rotate({_f(-p.rotation)} {_f(cx)} {_f(cy)})"' if p.rotation else ""
    if p.shape == "circle":
        return (f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(w / 2)}" '
                f'fill="{fill}"{extra}/>')
    rx = 0.0
    if p.shape == "roundrect":
        rx = p.corner_radius
    elif p.shape == "oval":
        rx = min(w, h) / 2
    return (f'<rect x="{_f(cx - w / 2)}" y="{_f(cy - h / 2)}" width="{_f(w)}" '
            f'height="{_f(h)}" rx="{_f(rx)}" ry="{_f(rx)}" fill="{fill}"'
            f'{rot}{extra}/>')


def _hole(p: Pad) -> str:
    cx, cy = p.x, -p.y
    if p.hole_length and p.hole_length > p.hole:
        w, h = p.hole_length, p.hole
        # The slot turns with its pad and then by its own hole rotation.
        turn = p.rotation + p.hole_rotation
        rot = f' transform="rotate({_f(-turn)} {_f(cx)} {_f(cy)})"' if turn else ""
        return (f'<rect x="{_f(cx - w / 2)}" y="{_f(cy - h / 2)}" width="{_f(w)}" '
                f'height="{_f(h)}" rx="{_f(h / 2)}" fill="{_STYLE["hole"]}" '
                f'stroke="{_STYLE["hole_edge"]}" stroke-width="0.02"{rot}/>')
    return (f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(p.hole / 2)}" '
            f'fill="{_STYLE["hole"]}" stroke="{_STYLE["hole_edge"]}" '
            'stroke-width="0.02"/>')


def _graphic(g, colour: str, dash: Optional[str] = None) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    if isinstance(g, Line):
        return (f'<line x1="{_f(g.x1)}" y1="{_f(-g.y1)}" x2="{_f(g.x2)}" '
                f'y2="{_f(-g.y2)}" stroke="{colour}" stroke-width="{_f(g.width)}" '
                f'stroke-linecap="round"{d}/>')
    if isinstance(g, Circle):
        if g.filled:
            return (f'<circle cx="{_f(g.x)}" cy="{_f(-g.y)}" r="{_f(g.r)}" '
                    f'fill="{colour}"/>')
        return (f'<circle cx="{_f(g.x)}" cy="{_f(-g.y)}" r="{_f(g.r)}" fill="none" '
                f'stroke="{colour}" stroke-width="{_f(g.width)}"{d}/>')
    if isinstance(g, Arc):
        a0 = math.radians(g.start)
        a1 = math.radians(g.start + g.sweep)
        x0, y0 = g.x + g.r * math.cos(a0), g.y + g.r * math.sin(a0)
        x1, y1 = g.x + g.r * math.cos(a1), g.y + g.r * math.sin(a1)
        large = 1 if abs(g.sweep) > 180 else 0
        # CCW in the model (Y up) is clockwise on screen once Y is flipped,
        # which is SVG's positive sweep direction.
        sweep = 1 if g.sweep > 0 else 0
        return (f'<path d="M {_f(x0)} {_f(-y0)} A {_f(g.r)} {_f(g.r)} 0 {large} '
                f'{sweep} {_f(x1)} {_f(-y1)}" fill="none" stroke="{colour}" '
                f'stroke-width="{_f(g.width)}"{d}/>')
    if isinstance(g, Polygon):
        pts = " ".join(f"{_f(x)},{_f(-y)}" for x, y in g.points)
        tag = "polygon" if g.closed else "polyline"
        fill = colour if g.filled else "none"
        return (f'<{tag} points="{pts}" fill="{fill}" stroke="{colour}" '
                f'stroke-width="{_f(g.width)}" stroke-linejoin="round"{d}/>')
    if isinstance(g, Text):
        return (f'<text x="{_f(g.x)}" y="{_f(-g.y)}" font-size="{_f(g.height)}" '
                f'fill="{colour}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="Arial, Helvetica, sans-serif">{html.escape(g.text)}</text>')
    return ""


def _bounds(model: FootprintModel) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for p in model.pads:
        b = p.bbox()
        xs += [b[0], b[2]]
        ys += [b[1], b[3]]
    for x, y in model.courtyard.points:
        xs.append(x)
        ys.append(y)
    for g in model.graphics:
        if isinstance(g, Line):
            xs += [g.x1, g.x2]
            ys += [g.y1, g.y2]
        elif isinstance(g, (Circle, Arc)):
            xs += [g.x - g.r, g.x + g.r]
            ys += [g.y - g.r, g.y + g.r]
        elif isinstance(g, Polygon):
            xs += [x for x, _ in g.points]
            ys += [y for _, y in g.points]
    if not xs:
        # An empty footprint, read from a library: draw the frame around
        # the origin rather than fail.
        return -1.0, -1.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)


def render_svg(model: FootprintModel, *, show_numbers: bool = True,
               show_paste: bool = True, title: Optional[str] = None,
               margin: Optional[float] = None, notes: Sequence[str] = (),
               terminals: Sequence[dict] = ()) -> str:
    """The footprint as an SVG string (viewBox in mm).

    ``notes`` are extra caption lines under the description (the pattern's
    pitch, span and land sizes, say). ``terminals`` are the package's
    terminal outlines (``audit.terminal_outlines``), drawn dashed over the
    lands so a land that misses its terminal shows.
    """
    x0, y0, x1, y1 = _bounds(model)
    span = max(x1 - x0, y1 - y0, 1.0)
    m = margin if margin is not None else max(0.6, 0.12 * span)
    fs = max(0.18, 0.035 * span)
    head0 = 0.14 * span + 0.6
    head = head0 + len(notes) * fs * 1.05
    vx, vy = x0 - m, -(y1 + m) - head
    vw, vh = (x1 - x0) + 2 * m, (y1 - y0) + 2 * m + head + 0.08 * span + 0.6
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(vx)} {_f(vy)} '
        f'{_f(vw)} {_f(vh)}">',
        f'<rect x="{_f(vx)}" y="{_f(vy)}" width="{_f(vw)}" height="{_f(vh)}" '
        f'fill="{_STYLE["bg"]}"/>',
    ]
    t = title if title is not None else model.name
    out.append(
        f'<text x="{_f(vx + m * 0.5)}" y="{_f(vy + head0 * 0.45)}" font-size="{_f(fs * 1.4)}" '
        f'fill="{_STYLE["text"]}" font-family="Arial, Helvetica, sans-serif" '
        f'font-weight="bold">{html.escape(t)}</text>')
    out.append(
        f'<text x="{_f(vx + m * 0.5)}" y="{_f(vy + head0 * 0.8)}" font-size="{_f(fs * 0.8)}" '
        f'fill="{_STYLE["text"]}" font-family="Arial, Helvetica, sans-serif">'
        f'{html.escape(model.description[:160])}</text>')
    for k, line in enumerate(notes):
        out.append(
            f'<text x="{_f(vx + m * 0.5)}" '
            f'y="{_f(vy + head0 * 0.8 + (k + 1) * fs * 1.05)}" '
            f'font-size="{_f(fs * 0.8)}" fill="{_STYLE["text"]}" '
            f'font-family="Arial, Helvetica, sans-serif">'
            f'{html.escape(str(line)[:160])}</text>')

    # courtyard and assembly first, under the copper
    out.append('<g id="courtyard">')
    drawn = model.layer("courtyard")
    if drawn:
        # A footprint read from a library has its courtyard as drawn,
        # which is not always the rectangle model.courtyard holds.
        for g in drawn:
            out.append(_graphic(g, _STYLE["courtyard"]))
    else:
        out.append(_graphic(model.courtyard, _STYLE["courtyard"]))
    out.append('</g>')
    others = [g for g in model.graphics
              if g.layer not in ("courtyard", "assembly", "silk")]
    if others:
        out.append('<g id="other-layers">')
        for g in others:
            out.append(_graphic(g, _STYLE["origin"], dash="0.12,0.12"))
        out.append('</g>')
    out.append('<g id="assembly">')
    for g in model.layer("assembly"):
        out.append(_graphic(g, _STYLE["assembly"], dash="0.25,0.15"))
    out.append(_graphic(model.ref, _STYLE["assembly"]))
    out.append('</g>')

    out.append('<g id="copper">')
    for p in model.pads:
        if p.kind == "npth":
            out.append(_pad_shape(p, _STYLE["npth"], ' fill-opacity="0.35"'))
        else:
            fill = _STYLE["copper_tht"] if p.kind == "tht" else _STYLE["copper"]
            out.append(_pad_shape(p, fill))
    out.append('</g>')
    if show_paste:
        out.append('<g id="paste">')
        for p in model.pads:
            for a in p.paste_apertures:
                out.append(
                    f'<rect x="{_f(a.x - a.width / 2)}" y="{_f(-a.y - a.height / 2)}" '
                    f'width="{_f(a.width)}" height="{_f(a.height)}" '
                    f'fill="{_STYLE["paste"]}" fill-opacity="0.55"/>')
        out.append('</g>')
    if terminals:
        out.append('<g id="terminals">')
        sw = _f(max(0.02, 0.004 * span))
        for t in terminals:
            tx0, ty0, tx1, ty1 = t["x0"], t["y0"], t["x1"], t["y1"]
            if t.get("shape") == "circle":
                out.append(
                    f'<circle cx="{_f((tx0 + tx1) / 2)}" cy="{_f(-(ty0 + ty1) / 2)}" '
                    f'r="{_f((tx1 - tx0) / 2)}" fill="none" '
                    f'stroke="{_STYLE["terminal"]}" stroke-width="{sw}" '
                    f'stroke-dasharray="0.08,0.05"/>')
            else:
                out.append(
                    f'<rect x="{_f(tx0)}" y="{_f(-ty1)}" width="{_f(tx1 - tx0)}" '
                    f'height="{_f(ty1 - ty0)}" fill="none" '
                    f'stroke="{_STYLE["terminal"]}" stroke-width="{sw}" '
                    f'stroke-dasharray="0.08,0.05"/>')
        out.append('</g>')
    out.append('<g id="holes">')
    for p in model.pads:
        if p.hole > 0:
            out.append(_hole(p))
    out.append('</g>')

    out.append('<g id="silk">')
    for g in model.layer("silk"):
        out.append(_graphic(g, _STYLE["silk"]))
    out.append('</g>')

    if show_numbers:
        out.append('<g id="numbers">')
        for p in model.pads:
            if not p.number:
                continue
            size = min(p.width, p.height)
            if p.role == "thermal":
                size = min(size, 0.8)
            h = max(0.08, min(0.6 * size, 1.2))
            if len(p.number) > 2:
                h *= 0.75
            colour = _STYLE["number"]
            if p.hole > 0:
                # The hole is drawn light; a dark number stays readable on it.
                colour = _STYLE["text"]
                h = max(0.08, min(h, 0.8 * p.hole))
            out.append(
                f'<text x="{_f(p.x)}" y="{_f(-p.y)}" font-size="{_f(h)}" '
                f'fill="{colour}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="Arial, Helvetica, '
                f'sans-serif" font-weight="bold">{html.escape(p.number)}</text>')
        out.append('</g>')

    # origin cross and scale bar
    c = 0.04 * span
    out.append(
        f'<g id="origin" stroke="{_STYLE["origin"]}" stroke-width="{_f(0.004 * span + 0.01)}">'
        f'<line x1="{_f(-c)}" y1="0" x2="{_f(c)}" y2="0"/>'
        f'<line x1="0" y1="{_f(-c)}" x2="0" y2="{_f(c)}"/></g>')
    sb_y = -(y0 - m * 0.5)
    out.append(
        f'<g id="scale"><line x1="{_f(x0)}" y1="{_f(sb_y)}" x2="{_f(x0 + 1)}" '
        f'y2="{_f(sb_y)}" stroke="{_STYLE["text"]}" stroke-width="{_f(fs * 0.25)}"/>'
        f'<text x="{_f(x0 + 1.15)}" y="{_f(sb_y)}" font-size="{_f(fs * 0.8)}" '
        f'fill="{_STYLE["text"]}" dominant-baseline="central" '
        f'font-family="Arial, Helvetica, sans-serif">1 mm</text></g>')
    out.append("</svg>")
    return "\n".join(s for s in out if s)
