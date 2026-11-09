# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A 3D model's solids against the package outline drawing.

The solids of a hand-drawn model are read off the drawing one by one, and
nothing compared their sum with the drawing's own totals: a body a
millimetre long or a seated height taken from the wrong column passed and
was only seen in the 3D view, if at all. The drawing's body size, overall
span and maximum height are given as printed, and the model's extents
must match them.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

#: Measured against a single printed value: the larger of these.
ABS_TOL_MM = 0.05
REL_TOL = 0.02

#: Keys of ``drawing`` and what each is measured on.
KEYS = {
    "body_x": "the body solids' extent along X",
    "body_y": "the body solids' extent along Y",
    "span_x": "every solid's extent along X (leads included)",
    "span_y": "every solid's extent along Y (leads included)",
    "height": "the top of the highest solid above the board",
}

#: Roles that are the package body rather than its terminals.
BODY_ROLES = ("body", "ceramic", "lens", "white", "pin1")


def _extent(s: Any) -> tuple[float, float, float, float, float]:
    """(xmin, xmax, ymin, ymax, ztop) of one solid."""
    g = s.get if isinstance(s, dict) else (lambda k, d=0.0: getattr(s, k, d))
    kind = str(g("kind", "box")).lower()
    axis = str(g("axis", "z") or "z").lower()
    f = lambda k: float(g(k, 0.0) or 0.0)  # noqa: E731
    if kind == "box":
        return (min(f("x0"), f("x1")), max(f("x0"), f("x1")),
                min(f("y0"), f("y1")), max(f("y0"), f("y1")), max(f("z0"), f("z1")))
    if kind == "sphere":
        r = f("r")
        return f("cx") - r, f("cx") + r, f("cy") - r, f("cy") + r, f("cz") + r
    if kind == "cylinder":
        r = f("r")
        if axis == "x":
            return (min(f("x0"), f("x1")), max(f("x0"), f("x1")),
                    f("cy") - r, f("cy") + r, f("cz") + r)
        if axis == "y":
            return (f("cx") - r, f("cx") + r, min(f("y0"), f("y1")),
                    max(f("y0"), f("y1")), f("cz") + r)
        return f("cx") - r, f("cx") + r, f("cy") - r, f("cy") + r, max(f("z0"), f("z1"))
    if kind == "prism":
        prof = [(float(u), float(z)) for u, z in (g("profile", []) or [])]
        us = [u for u, _ in prof] or [0.0]
        top = max((z for _, z in prof), default=0.0)
        if axis == "x":
            return (min(f("x0"), f("x1")), max(f("x0"), f("x1")),
                    min(us), max(us), top)
        return min(us), max(us), min(f("y0"), f("y1")), max(f("y0"), f("y1")), top
    return 0.0, 0.0, 0.0, 0.0, 0.0


def _bounds(solids: Iterable[Any]) -> Optional[tuple[float, float, float]]:
    ext = [_extent(s) for s in solids]
    if not ext:
        return None
    return (max(e[1] for e in ext) - min(e[0] for e in ext),
            max(e[3] for e in ext) - min(e[2] for e in ext),
            max(e[4] for e in ext))


def _limits(value: Any) -> tuple[float, float, str]:
    """(low, high, as printed) for a value or a [min, max] pair."""
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError("a drawing dimension is a number or [min, max]")
        lo, hi = sorted(float(v) for v in value)
        return lo - 0.01, hi + 0.01, f"{lo:g} to {hi:g}"
    v = float(value)
    tol = max(ABS_TOL_MM, REL_TOL * abs(v))
    return v - tol, v + tol, f"{v:g}"


def check_model_dims(solids: list[Any], drawing: dict[str, Any]) -> dict[str, Any]:
    """``{ok, rows, reason?}``: one row per drawing dimension given, with
    the model's measure and whether it is inside what the drawing prints."""
    if not isinstance(drawing, dict) or not drawing:
        return {"ok": False, "rows": [], "reason": (
            "give drawing: the package outline's body_x, body_y and height "
            "(the seated height) in mm as printed, each a nominal number or "
            "[min, max] (a height printed only as a maximum is [0, max]), "
            "and span_x / span_y across the leads where the drawing gives "
            "them")}
    unknown = sorted(set(drawing) - set(KEYS))
    if unknown:
        return {"ok": False, "rows": [], "reason": (
            f"drawing keys {', '.join(unknown)} are not known; use "
            + ", ".join(KEYS))}
    missing = [k for k in ("body_x", "body_y", "height") if k not in drawing]
    if missing:
        return {"ok": False, "rows": [], "reason": (
            f"drawing needs {', '.join(missing)} from the outline drawing")}
    body = [s for s in solids if str((s.get("role") if isinstance(s, dict)
                                      else getattr(s, "role", "body")) or "body"
                                     ).lower() in BODY_ROLES]
    whole = _bounds(solids)
    # A connector is its metal shell: with no body solid, every solid is it.
    inner = _bounds(body) if body else whole
    if whole is None:
        return {"ok": False, "rows": [], "reason": "the model has no solids"}
    measure = {"span_x": whole[0], "span_y": whole[1], "height": whole[2]}
    if inner is not None:
        measure.update(body_x=inner[0], body_y=inner[1])
    rows: list[dict[str, Any]] = []
    for key, printed in drawing.items():
        try:
            lo, hi, shown = _limits(printed)
        except (TypeError, ValueError) as e:
            return {"ok": False, "rows": rows, "reason": f"drawing.{key}: {e}"}
        got = measure.get(key)
        if got is None:
            rows.append({"dim": key, "drawing": shown, "model": None, "ok": False,
                         "why": "no solid has a body role (body, ceramic, lens)"})
            continue
        ok = lo <= got <= hi
        rows.append({"dim": key, "drawing": shown, "model": round(got, 3), "ok": ok,
                     **({} if ok else {"why": f"{KEYS[key]} is {got:.3f} mm"})})
    bad = [r for r in rows if not r["ok"]]
    out: dict[str, Any] = {"ok": not bad, "rows": rows}
    if bad:
        out["reason"] = ("the model does not match the outline drawing: " + "; ".join(
            f"{r['dim']} drawing {r['drawing']}, model {r['model']}" for r in bad)
            + ". Correct the solids from the drawing")
    return out
