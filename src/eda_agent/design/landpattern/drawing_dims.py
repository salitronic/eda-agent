# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Check built pads against the dimensions printed on the package drawing.

Custom pads are a hand transcription of a drawing. Twice the BGT60LTR11AIP
came out on an even 0.5 mm row grid where its bottom view prints 0.75 mm
between rows 3 and 4: the pads were self-consistent and matched nothing but
the transcriber's assumption. The ADXL372 land pattern prints 0.80 for the
top and bottom lands and 0.925 for the side lands; twice every land was
built 0.80 long, and 0.925, a number the reader could not place, was
simply left out. So every printed number is listed (``numbers``) and each
must be placed by a dimension measured on the pads actually built:

* ``between`` ``centres`` (default): centre to centre (pitch, row gap);
  ``outer``: far edge to far edge (a span); ``gap``: near edge to near edge;
* ``from_edge`` / ``to_edge`` (``centre``, ``near``, ``far``) for a number
  between unlike references (one land's outer edge to another's inner);
* ``{pad, axis}``: one land's length or width;
* ``ORIGIN`` as a pad: the drawing's centre lines;
* ``D#2``: the second land numbered D (a pin with several lands).

A module's land drawing also prints numbers no pad box carries: the body
(13.2 x 16.6), the two legs of a cut corner on a land given as an
``outline`` (0.6 x 0.6), the distance to a keep-out line (11.2). With none
of them placeable the whole check had to be switched off for that part, so:

* ``BODY`` as a pad: the package body, ``body_x`` by ``body_y`` (nominal);
* ``{pad, edge, mm, axis}``: one edge of that land's ``outline``, edge 1
  running from its first point to its second; ``axis`` gives one leg of a
  slanted edge, no axis its length;
* a printed number that measures nothing built is listed in ``numbers`` as
  ``{mm, not_built: "what it measures"}`` and is reported back, not dropped.

A through-hole connector's layout prints the drill of each hole and often
no copper diameter at all (0.89 x10, 1.02 x4, 1.63 x2, 3.25 x2). A drill
is built, so it is not a ``not_built`` number, and no pad box measures it:

* ``{pad, mm, hole: true}``: that pad's drill diameter (a slot's width);
  ``hole: "length"`` a slot's length.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .spec import SpecError

#: Built against printed: a drawing's own rounding, not a misread.
DEFAULT_TOL_MM = 0.01

#: The centre lines of the drawing (the footprint origin).
ORIGIN = "ORIGIN"

#: The package body as given in the spec (body_x by body_y, nominal).
BODY = "BODY"

#: A reason shorter than this says nothing about what the number measures.
_MIN_REASON = 8

_BETWEEN = {"centres": ("centre", "centre"), "outer": ("far", "far"),
            "gap": ("near", "near")}
_ALIASES = {"": "centres", "centre": "centres", "center": "centres",
            "centers": "centres", "outer edges": "outer", "edges": "outer",
            "near edges": "gap"}
_EDGES = ("centre", "near", "far")


@dataclass(frozen=True)
class DrawingDim:
    """One printed number and what it is measured between."""

    start: str
    end: str
    mm: float
    axis: str = ""          # "x", "y" or "" for the straight line
    tol: float = DEFAULT_TOL_MM
    cite: str = ""
    kind: str = "centres"   # centres | outer | gap | size | edges | outline | hole
    start_edge: str = "centre"
    end_edge: str = "centre"
    edge: int = 0           # outline: which edge, 1 = first point to second
    part: str = ""          # hole: diameter or length (of a slot)

    @classmethod
    def from_any(cls, value: Any) -> "DrawingDim":
        if isinstance(value, DrawingDim):
            return value
        if not isinstance(value, Mapping):
            raise SpecError("a drawing dimension is an object {from, to, mm, "
                            "axis, between} or {pad, mm, axis}")
        d = dict(value)
        axis = str(d.get("axis") or "").strip().lower()
        if axis not in ("", "x", "y"):
            raise SpecError(f"drawing dimension axis is x, y or empty, not {axis!r}")
        mm, tol = d.get("mm"), float(d.get("tol", DEFAULT_TOL_MM))
        if mm is None:
            raise SpecError(f"a drawing dimension needs mm; got {sorted(d)}")
        cite = str(d.get("cite") or "")
        if "pad" in d and d.get("edge") is not None:
            try:
                edge = int(d["edge"])
            except (TypeError, ValueError):
                edge = 0
            if edge < 1 or isinstance(d["edge"], bool):
                raise SpecError("an outline edge {pad, edge, mm, axis} counts "
                                f"edges from 1, not {d['edge']!r}")
            pad = str(d["pad"]).strip().upper()
            return cls(pad, pad, float(mm), axis, tol, cite, "outline", edge=edge)
        if "pad" in d and d.get("hole") not in (None, False, ""):
            part = "diameter" if d["hole"] is True else str(d["hole"]).strip().lower()
            if part not in ("diameter", "length"):
                raise SpecError("a hole {pad, mm, hole} is hole: true for the "
                                "drill diameter (a slot's width) or hole: "
                                f"\"length\" for a slot's length, not {d['hole']!r}")
            pad = str(d["pad"]).strip().upper()
            return cls(pad, pad, float(mm), "", tol, cite, "hole", part=part)
        if "pad" in d and not (d.get("between") or d.get("kind")):
            if not axis:
                raise SpecError("a land size {pad, mm, axis} needs axis x or y")
            pad = str(d["pad"]).strip().upper()
            return cls(pad, pad, float(mm), axis, tol, cite, "size")
        start, end = d.get("from", d.get("start")), d.get("to", d.get("end"))
        if start is None or end is None:
            raise SpecError("a drawing dimension needs from and to (pad "
                            f"numbers or ORIGIN); got {sorted(d)}")
        kind = str(d.get("between") or d.get("kind") or "").strip().lower()
        kind = _ALIASES.get(kind, kind)
        if kind not in _BETWEEN:
            raise SpecError(f"between is centres, outer or gap, not {kind!r}")
        se, ee = _BETWEEN[kind]
        if "from_edge" in d or "to_edge" in d:
            se = str(d.get("from_edge") or se).strip().lower()
            ee = str(d.get("to_edge") or ee).strip().lower()
            if se not in _EDGES or ee not in _EDGES:
                raise SpecError("from_edge and to_edge are centre, near or far")
            kind = "edges"
        if (se, ee) != ("centre", "centre") and not axis:
            raise SpecError("a dimension to a land's edge needs axis x or y")
        return cls(str(start).strip().upper(), str(end).strip().upper(),
                   float(mm), axis, tol, cite, kind, se, ee)


def read_dims(values: Iterable[Any] | None) -> tuple[DrawingDim, ...]:
    return tuple(DrawingDim.from_any(v) for v in (values or ()))


def _not_built(value: Any) -> dict[str, Any] | None:
    """``{mm, what}`` for a number given as measuring nothing built."""
    if not isinstance(value, Mapping):
        return None
    what = str(value.get("not_built") or "").strip()
    try:
        mm = float(value.get("mm"))
    except (TypeError, ValueError):
        mm = None
    if mm is None or len(what) < _MIN_REASON:
        raise SpecError(
            "a drawing number that measures nothing built is {mm, not_built: "
            "what it measures on the drawing (a keep-out line to the body "
            f"edge, a view scale)}}; got {dict(value)!r}")
    return {"mm": mm, "what": what}


def read_not_built(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    """The printed numbers given as measuring nothing that is built, each
    with what it measures: reported with the result so a land's own number
    cannot leave the check unseen."""
    return [n for n in (_not_built(v) for v in (values or ())) if n]


def read_numbers(values: Iterable[Any] | None) -> tuple[float, ...]:
    """The printed numbers a dimension has to place."""
    out = []
    for v in values or ():
        if _not_built(v):
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            raise SpecError(f"drawing numbers are the printed values in mm, "
                            f"not {v!r}") from None
    return tuple(out)


def unplaced_numbers(numbers: Iterable[float],
                     dims: Iterable[DrawingDim]) -> list[float]:
    """The printed numbers no dimension measures."""
    dims = tuple(dims)
    return [n for n in numbers
            if not any(abs(d.mm - n) <= max(d.tol, 1e-6) for d in dims)]


def _extent(pad: Any) -> tuple[float, float]:
    """The pad's X and Y extent, its rotation applied."""
    w, h = float(pad.width), float(pad.height)
    a = math.radians(float(getattr(pad, "rotation", 0.0) or 0.0))
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + h * s, w * s + h * c


class _Origin:
    number, x, y, width, height, rotation = ORIGIN, 0.0, 0.0, 0.0, 0.0, 0.0


@dataclass(frozen=True)
class _Box:
    """The body, measured like a land."""

    number: str
    x: float
    y: float
    width: float
    height: float
    rotation: float = 0.0


def body_box(spec: Any, model: Any) -> tuple[float, float, float, float] | None:
    """The body a drawing prints: the spec's nominal ``body_x`` by
    ``body_y`` about the built body's centre, or None when the spec gives
    none (the built model then only has the box round its pads)."""
    bx, by = getattr(spec, "body_x", None), getattr(spec, "body_y", None)
    b = getattr(model, "body", None)
    if bx is None or by is None or b is None:
        return None
    cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    return (cx - bx.nom / 2, cy - by.nom / 2, cx + bx.nom / 2, cy + by.nom / 2)


def outlines_of(spec: Any) -> dict[str, tuple]:
    """The ``outline`` of each custom land that has one, under the names
    ``check_dims`` gives the lands: ``D#1``, ``D#2`` in the order given,
    and ``D`` for the first."""
    out: dict[str, tuple] = {}
    count: dict[str, int] = {}
    for cp in getattr(spec, "custom_pads", ()) or ():
        n = str(cp.number).strip().upper()
        count[n] = count.get(n, 0) + 1
        pts = tuple(cp.outline or ())
        if pts:
            out[f"{n}#{count[n]}"] = pts
            if count[n] == 1:
                out[n] = pts
    return out


def _edge(d: DrawingDim, pts: tuple) -> tuple[float, str]:
    """The built length of outline edge ``d.edge`` and where it runs."""
    (x1, y1), (x2, y2) = pts[d.edge - 1], pts[d.edge % len(pts)]
    built = {"x": abs(x2 - x1), "y": abs(y2 - y1)}.get(
        d.axis, math.hypot(x2 - x1, y2 - y1))
    return built, f"({x1:g}, {y1:g}) to ({x2:g}, {y2:g})"


def _measure(d: DrawingDim, p1: Any, p2: Any) -> float:
    (x1, y1), (x2, y2) = (float(p1.x), float(p1.y)), (float(p2.x), float(p2.y))
    if not d.axis:
        return math.hypot(x2 - x1, y2 - y1)
    i = 0 if d.axis == "x" else 1
    h1, h2 = _extent(p1)[i] / 2, _extent(p2)[i] / 2
    if d.kind == "size":
        return 2 * h1
    c1, c2 = (x1, x2) if i == 0 else (y1, y2)
    s = 1.0 if c2 >= c1 else -1.0
    e1 = {"centre": c1, "near": c1 + s * h1, "far": c1 - s * h1}[d.start_edge]
    e2 = {"centre": c2, "near": c2 - s * h2, "far": c2 + s * h2}[d.end_edge]
    return abs(e2 - e1)


def _describe(d: DrawingDim) -> str:
    if d.kind == "size":
        return f"pad {d.start} is"
    if d.kind == "edges":
        return (f"the {d.start_edge} of {d.start} to the {d.end_edge} of "
                f"{d.end} is")
    return {"centres": "the centres are", "outer": "the outer edges are",
            "gap": "the facing edges are"}[d.kind]


def check_dims(dims: Iterable[DrawingDim], pads: Iterable[Any],
               body: tuple[float, float, float, float] | None = None,
               outlines: Mapping[str, tuple] | None = None
               ) -> list[dict[str, Any]]:
    """One row per dimension: printed, built, ok. A pad named in a
    dimension and missing from the footprint is a failure, not a skip.

    ``body`` is the package body (``body_box``) that ``BODY`` names;
    ``outlines`` the lands' outlines (``outlines_of``) that ``edge``
    measures."""
    at: dict[str, Any] = {ORIGIN: _Origin()}
    if body is not None:
        x0, y0, x1, y1 = body
        at[BODY] = _Box(BODY, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0)
    outlines = outlines or {}
    count: dict[str, int] = {}
    for p in pads:
        n = str(p.number).strip().upper()
        count[n] = count.get(n, 0) + 1
        at[f"{n}#{count[n]}"] = p
        at.setdefault(n, p)
    repeated = {n for n, c in count.items() if c > 1}
    rows: list[dict[str, Any]] = []
    for d in dims:
        row: dict[str, Any] = {"from": d.start, "to": d.end, "between": d.kind,
                               "axis": d.axis or "line", "drawing_mm": d.mm}
        if d.kind == "edges":
            row["edges"] = f"{d.start_edge}-{d.end_edge}"
        if d.kind == "outline":
            row["edge"] = d.edge
        if d.kind == "hole":
            row["hole"] = d.part
        if d.cite:
            row["cite"] = d.cite
        twice = sorted({n for n in (d.start, d.end) if n in repeated})
        if twice:
            row.update(ok=False, built_mm=None, why=(
                f"{', '.join(twice)} names several lands: say which, "
                f"{twice[0]}#1 for the first given in custom_pads, "
                f"{twice[0]}#2 for the second"))
            rows.append(row)
            continue
        missing = sorted({n for n in (d.start, d.end) if n not in at})
        if missing == [BODY]:
            row.update(ok=False, built_mm=None, why=(
                "the package gives no body_x and body_y, so BODY has "
                "nothing to measure: give the body as the drawing prints it"))
            rows.append(row)
            continue
        if missing:
            row.update(ok=False, built_mm=None,
                       why=f"no pad named {', '.join(missing)} in the footprint")
            rows.append(row)
            continue
        if d.kind == "outline":
            pts = outlines.get(d.start)
            if not pts or d.edge > len(pts):
                row.update(ok=False, built_mm=None, why=(
                    f"the outline of pad {d.start} has {len(pts)} edges, no "
                    f"edge {d.edge}" if pts else
                    f"pad {d.start} is given without an outline, so it has "
                    f"no edge {d.edge}: {{pad, mm, axis}} measures its box"))
                rows.append(row)
                continue
            built, where = _edge(d, pts)
            row.update(built_mm=round(built, 4), ok=abs(built - d.mm) <= d.tol)
            if not row["ok"]:
                row["why"] = (
                    f"edge {d.edge} of pad {d.start}'s outline runs {where}: "
                    f"{built:.3f} mm" + (f" along {d.axis}" if d.axis else "")
                    + f", the drawing prints {d.mm:g} mm")
            rows.append(row)
            continue
        if d.kind == "hole":
            pad = at[d.start]
            drill = float(getattr(pad, "hole", 0.0) or 0.0)
            built = drill if d.part == "diameter" else float(
                getattr(pad, "hole_length", 0.0) or 0.0)
            if built <= 0:
                row.update(ok=False, built_mm=None, why=(
                    f"pad {d.start} has no hole: {{pad, mm, axis}} measures "
                    f"a land's size" if drill <= 0 else
                    f"the hole of pad {d.start} is round, {drill:g} mm, not "
                    f"a slot: hole: true measures its diameter"))
                rows.append(row)
                continue
            row.update(built_mm=round(built, 4), ok=abs(built - d.mm) <= d.tol)
            if not row["ok"]:
                row["why"] = (f"the hole of pad {d.start} is {built:.3f} mm"
                              + (" long" if d.part == "length" else "")
                              + f", the drawing prints {d.mm:g} mm")
            rows.append(row)
            continue
        built = _measure(d, at[d.start], at[d.end])
        row.update(built_mm=round(built, 4), ok=abs(built - d.mm) <= d.tol)
        if not row["ok"]:
            row["why"] = (f"{_describe(d)} {built:.3f} mm along "
                          f"{d.axis or 'the line'}, the drawing prints {d.mm:g} mm")
        rows.append(row)
    return rows


#: Package keys that check the built pads and are not PackageSpec fields.
DRAWING_KEYS = ("drawing_dims", "drawing_numbers", "compared")


def pop_drawing(package: Any) -> tuple[Any, Any, Any]:
    """``(package without the drawing keys, drawing_dims, drawing_numbers)``."""
    if not isinstance(package, dict) or not any(k in package for k in DRAWING_KEYS):
        return package, None, None
    package = dict(package)
    package.pop("compared", None)
    return package, package.pop("drawing_dims", None), package.pop("drawing_numbers", None)


def drawing_refusal(custom: bool, dims: tuple[DrawingDim, ...],
                    numbers: tuple[float, ...]) -> str | None:
    """Why custom pads cannot be checked as given, or None."""
    if not custom:
        return None
    if not dims or not numbers:
        return ("custom_pads are a hand transcription: give drawing_numbers, "
                "every number printed on the land drawing as read, and "
                "drawing_dims placing each one: {from, to, mm, axis, between "
                "centres|outer|gap} or from_edge/to_edge centre|near|far for "
                "unlike edges, {pad, mm, axis} for a land's length or width, "
                "{pad, edge, mm, axis} for an edge of its outline, "
                "{pad, mm, hole: true} for its drill, "
                "ORIGIN for the drawing's centre lines, BODY for the body")
    left = unplaced_numbers(numbers, dims)
    if left:
        return (f"no drawing_dims entry measures {', '.join(f'{n:g}' for n in left)}"
                f" printed on the drawing: work out what each spans on the "
                f"drawing (a land's length, a span, a gap, an edge to a centre "
                f"line; BODY is the body, {{pad, edge}} an edge of a land's "
                f"outline, {{pad, hole: true}} its drill) and add it; lands "
                f"built without it are not checked. "
                f"A number that measures nothing built (a keep-out line) goes "
                f"in drawing_numbers as {{mm, not_built: what it measures}}")
    return None
