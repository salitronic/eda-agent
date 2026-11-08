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
    kind: str = "centres"   # centres | outer | gap | size | edges
    start_edge: str = "centre"
    end_edge: str = "centre"

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


def read_numbers(values: Iterable[Any] | None) -> tuple[float, ...]:
    out = []
    for v in values or ():
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


def check_dims(dims: Iterable[DrawingDim],
               pads: Iterable[Any]) -> list[dict[str, Any]]:
    """One row per dimension: printed, built, ok. A pad named in a
    dimension and missing from the footprint is a failure, not a skip."""
    at: dict[str, Any] = {ORIGIN: _Origin()}
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
        if missing:
            row.update(ok=False, built_mm=None,
                       why=f"no pad named {', '.join(missing)} in the footprint")
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
                "ORIGIN for the drawing's centre lines")
    left = unplaced_numbers(numbers, dims)
    if left:
        return (f"no drawing_dims entry measures {', '.join(f'{n:g}' for n in left)}"
                f" printed on the drawing: work out what each spans on the "
                f"drawing (a land's length, a span, a gap, an edge to a centre "
                f"line) and add it; lands built without it are not checked")
    return None
