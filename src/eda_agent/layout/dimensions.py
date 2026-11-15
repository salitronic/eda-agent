# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Where a dimension's references, line and callouts go, from its points.

``pcb.create_dimension`` and ``pcb.edit_dimension`` place a dimension's
references as free points (a script cannot bind one to a board object), plus
what each kind needs besides: the line of a linear dimension, the callout row
of a datum, one line per measured point of a baseline. This module works
those out from the points a caller gives, in mils, and builds the handler's
parameters. All of it is plain geometry, so it is checked without Altium.

Points per kind:

* ``linear``: the two measured points.
* ``angular``: the vertex, then one point on each line.
* ``radial``: the arc's centre, and the point on the arc to call out from
  unless a radius is given. Drawn as a leader labelled with the radius.
* ``leader``: the arrow tip, any bends, the end where the label sits.
* ``datum``: the origin, then each point called out from it.
* ``baseline``: the base, then each point measured from it.
* ``center``: the centre.
* ``original``: the start and the end.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ..units import MM_PER_MIL

KINDS = ("linear", "angular", "radial", "leader", "datum", "baseline", "center",
         "original")

#: Points each kind needs at least.
MIN_POINTS = {"linear": 2, "angular": 3, "radial": 1, "leader": 2, "datum": 2,
              "baseline": 2, "center": 1, "original": 2}

#: A diameter takes its value from the arc it is bound to, so none is made
#: here. A radial does too (made by script it draws "R 0.00"), so a radius
#: is drawn as a leader labelled with it (radial_callout).
BOUND_ONLY_KINDS = ("linear_diameter", "radial_diameter")

#: TDimensionUnit values seen in Altium's own files.
UNITS = {"mil": 0, "mils": 0, "mm": 2, "millimeters": 2, "deg": 4, "degrees": 4}

#: The leader's label frame.
SHAPES = {"none": 0, "round": 1, "circle": 1, "square": 2}


def _pts(points: Any) -> list[tuple[float, float]]:
    out = []
    for p in points or []:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError("points are [x, y] pairs")
        out.append((float(p[0]), float(p[1])))
    return out


def _text(points: list[tuple[float, float]]) -> str:
    return "/".join(f"{x:.4f},{y:.4f}" for x, y in points)


def _axes(angle: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """The measuring direction and the normal to its left."""
    a = math.radians(angle)
    return (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))


def angular_references(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Altium's order for an angular: near and far point on line A, then on B.

    The near point is half way from the vertex, so each line runs from near
    the vertex out to the point given.
    """
    (vx, vy), (ax, ay), (bx, by) = points[:3]
    return [(vx + (ax - vx) / 2, vy + (ay - vy) / 2), (ax, ay),
            (vx + (bx - vx) / 2, vy + (by - vy) / 2), (bx, by)]


def baseline_lines(points: list[tuple[float, float]], angle: float,
                   offset: float, spacing: float) -> list[tuple[float, float]]:
    """One line position per measured point: stacked ``spacing`` apart from
    ``offset`` off the base, each just past its own point along the axis."""
    (u0, u1), (n0, n1) = _axes(angle)
    bx, by = points[0]
    out = []
    for i, (px, py) in enumerate(points[1:]):
        along = (px - bx) * u0 + (py - by) * u1 + 100.0
        side = offset + i * spacing
        out.append((bx + along * u0 + side * n0, by + along * u1 + side * n1))
    return out


def radial_callout(points: list[tuple[float, float]], radius: Optional[float],
                   unit: str = "", precision: Optional[int] = None
                   ) -> tuple[tuple[float, float], tuple[float, float],
                              tuple[float, float], str]:
    """A radius called out by a leader: its tip on the arc, a bend out along
    the radius, the end where the label sits, and the label ("R 250.00").

    ``points`` are the centre and, optionally, the point on the arc the
    leader touches (default: at 45 degrees). The radius is the one given,
    else the distance between the two points, in mils; the label shows it
    in mm when ``unit`` says so.
    """
    if not points:
        raise ValueError("radial needs the arc's centre")
    cx, cy = points[0]
    if radius is None and len(points) > 1:
        radius = math.dist(points[0], points[1])
    if radius is None or float(radius) <= 0:
        raise ValueError("radial needs the arc's centre and its radius, or a "
                         "second point on the arc")
    r = float(radius)
    if len(points) > 1 and math.dist(points[0], points[1]) > 1e-9:
        d = math.dist(points[0], points[1])
        ux, uy = (points[1][0] - cx) / d, (points[1][1] - cy) / d
    else:
        ux = uy = math.sqrt(0.5)
    tip = (cx + r * ux, cy + r * uy)
    out = 0.5 * r + 100.0
    bend = (tip[0] + out * ux, tip[1] + out * uy)
    end = (bend[0] + (200.0 if ux >= 0 else -200.0), bend[1])
    shown = r * MM_PER_MIL if unit.strip().lower() in ("mm", "millimeters") else r
    digits = 2 if precision is None else max(0, int(precision))
    return tip, bend, end, f"R {shown:.{digits}f}"


def params_for(kind: str, points: Any, *, angle: float = 0.0,
               offset: Optional[float] = 300.0, spacing: Optional[float] = 300.0,
               radius: Optional[float] = None, label_unit: str = "",
               label_precision: Optional[int] = None) -> dict[str, str]:
    """The geometry parameters of ``pcb.create_dimension`` / ``edit_dimension``.

    ``offset`` sets how far the line (linear), the callout row (datum) or the
    first line (baseline) sits from the points, along the normal to the left
    of the measuring direction; ``None`` leaves an edited dimension's own.
    """
    kind = kind.strip().lower()
    if kind in BOUND_ONLY_KINDS:
        raise ValueError(f"a {kind} takes its value from the arc it is bound to and "
                         "reads 0 free: place it in Altium on the arc, or draw a "
                         "leader labelled with the value")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    pts = _pts(points)
    if len(pts) < MIN_POINTS[kind]:
        raise ValueError(f"{kind} needs at least {MIN_POINTS[kind]} points")
    out: dict[str, str] = {"kind": kind}
    (u0, u1), (n0, n1) = _axes(angle)
    if kind == "angular":
        refs = angular_references(pts)
        out["points"] = _text(refs)
        if radius is None:
            v = pts[0]
            radius = 0.6 * min(math.dist(v, pts[1]), math.dist(v, pts[2]))
        out["radius"] = f"{float(radius):.4f}"
        return out
    if kind == "radial":
        tip, bend, end, label = radial_callout(pts, radius, label_unit, label_precision)
        return {"kind": "leader", "points": _text([tip, bend, end]), "label": label}
    out["points"] = _text(pts if kind != "center" else pts[:1])
    if kind == "linear" and offset is not None:
        x, y = pts[0]
        out["line_x"] = f"{x + offset * n0:.4f}"
        out["line_y"] = f"{y + offset * n1:.4f}"
    if kind == "datum" and offset is not None:
        x, y = pts[0]
        out["text_x"] = f"{x + offset * n0:.4f}"
        out["text_y"] = f"{y + offset * n1:.4f}"
    if kind == "baseline":
        out["text_locations"] = _text(baseline_lines(
            pts, angle, 300.0 if offset is None else offset,
            300.0 if spacing is None else spacing))
    return out


def unit_code(unit: str) -> str:
    """Altium's TDimensionUnit for a unit name, as text."""
    key = unit.strip().lower()
    if key not in UNITS:
        raise ValueError("unit is mil, mm or deg")
    return str(UNITS[key])


def shape_code(shape: str) -> str:
    key = shape.strip().lower()
    if key not in SHAPES:
        raise ValueError("shape is none, round or square")
    return str(SHAPES[key])
