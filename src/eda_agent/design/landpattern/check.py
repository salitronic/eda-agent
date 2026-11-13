# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Physical sanity checks on a package description, before any land is drawn.

A datasheet number read wrong rarely gives a spec the engine refuses: it
gives a plausible footprint for a package that cannot exist. These checks
look for the geometry a misread number breaks: limits out of order, a lead
wider than its pitch, a row of pins longer than the body it comes out of,
a lead span smaller than the body, a foot longer than the lead's reach, an
exposed pad bigger than the body or over the terminals, a ball as big as
its pitch, a value 25.4 times off its neighbours (mils typed as mm), and a
pin count the family cannot have.

Every finding names the dimensions involved with their values and
citations, and says why the combination cannot be right. ``error`` means
impossible; ``warning`` means unusual enough to read the drawing again.
Nothing here knows any part by name.

Allowances: a row may run 0.10 mm past the body maximum, and a foot 0.10
mm past the lead's reach, before it counts; both are well under the
smallest misreading these checks exist for (a pitch, a body length or a
factor of 25.4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from eda_agent.units import MM_PER_MIL

from eda_agent.design.landpattern.spec import (
    FAMILIES,
    Dim,
    PackageSpec,
    SpecError,
    _DIM_FIELDS,
    dim,
)

__all__ = ["Finding", "check_package", "describe_dim", "read_package"]

#: Room a row of pins may take past the body maximum, mm.
ROW_ALLOWANCE = 0.10
#: Room a gull-wing foot may take past the lead's reach outside the body.
REACH_ALLOWANCE = 0.10
#: Adjacent terminals closer than this are unusual (warning only).
MIN_TERMINAL_GAP = 0.10
#: A row filling less than this share of its body is unusual.
MIN_ROW_FILL = 0.15
#: Ball diameter above this share of the pitch is unusual.
MAX_BALL_RATIO = 0.75

_TWO_TERMINAL = ("chip", "melf", "molded", "sod", "crystal2")
_GULLWING_LIKE = ("soic", "sot23", "sot223", "sot89", "dpak", "qfp", "plcc",
                  "sod")
_NOLEAD = ("son", "qfn")
_QUAD = ("qfn", "qfp", "plcc")
_TAB = ("sot89", "sot223", "dpak")
#: Size dims, against which the terminal-scale dims are judged for units.
_SCALE_DIMS = ("body_x", "body_y", "lead_span_x", "lead_span_y")
_TERMINAL_DIMS = ("pitch", "pitch_y", "lead_width", "lead_length",
                  "ball_diameter", "lead_diameter", "lead_thickness", "ep_x",
                  "ep_y", "tab_width", "tab_length", "standoff")


@dataclass
class Finding:
    """One thing about the package that cannot be, or should not be."""

    check: str
    severity: str                       # "error" | "warning"
    message: str
    dims: list[dict] = field(default_factory=list)
    hint: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"check": self.check, "severity": self.severity,
                             "message": self.message, "dims": self.dims}
        if self.hint:
            d["hint"] = self.hint
        return d


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _cite_text(d: Optional[Dim], spec: PackageSpec) -> str:
    if d is not None and d.cite is not None and d.cite.short():
        return d.cite.short()
    if spec.source is not None and spec.source.short():
        return f"{spec.source.short()} (package source)"
    return "no citation"


def describe_dim(name: str, d: Optional[Dim], spec: PackageSpec) -> str:
    """``lead_width 0.31 to 0.51 mm (TI ..., p. 54)``."""
    if d is None:
        return f"{name} (not given)"
    if abs(d.max - d.min) < 1e-12:
        vals = f"{_fmt(d.nom)} mm"
    elif abs(d.nom - (d.min + d.max) / 2.0) < 1e-9:
        vals = f"{_fmt(d.min)} to {_fmt(d.max)} mm"
    else:
        vals = f"{_fmt(d.min)} / {_fmt(d.nom)} / {_fmt(d.max)} mm"
    return f"{name} {vals} ({_cite_text(d, spec)})"


def _dim_record(name: str, d: Optional[Dim], spec: PackageSpec) -> dict:
    out: dict[str, Any] = {"name": name}
    if d is not None:
        out.update({"min": d.min, "nom": d.nom, "max": d.max})
    out["cite"] = _cite_text(d, spec)
    return out


def read_package(package: Any) -> PackageSpec:
    """A :class:`PackageSpec` from the forms a caller has: the object, its
    ``to_dict`` form, or that as JSON text. Raises :class:`SpecError`."""
    import json

    if isinstance(package, PackageSpec):
        return PackageSpec.from_dict(package.to_dict())
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except json.JSONDecodeError as exc:
            raise SpecError(f"package is not valid JSON ({exc}); pass the "
                            "PackageSpec as an object or its JSON text")
    if not isinstance(package, dict) or not package:
        raise SpecError("package must be a non-empty object: the family and "
                        "the dimensions that family needs")
    try:
        return PackageSpec.from_dict(package)
    except SpecError:
        raise
    except (TypeError, ValueError) as exc:
        raise SpecError(f"package could not be read: {exc}")


class _Ctx:
    """The spec plus the dims that passed the limits check, so a bad limit
    is reported once and does not cascade through every later check."""

    def __init__(self, spec: PackageSpec):
        self.spec = spec
        self.bad: set[str] = set()
        self.findings: list[Finding] = []
        self.examined: list[str] = []
        self.skipped: list[dict] = []
        self.notes: list[str] = []

    def d(self, name: str) -> Optional[Dim]:
        if name in self.bad:
            return None
        return getattr(self.spec, name, None)

    def txt(self, name: str, d: Optional[Dim] = None) -> str:
        return describe_dim(name, d if d is not None else self.d(name), self.spec)

    def rec(self, *names: str) -> list[dict]:
        return [_dim_record(n, getattr(self.spec, n, None), self.spec)
                for n in names]

    def add(self, check: str, severity: str, message: str, dims: list[str],
            hint: str = "", extra: Optional[list[dict]] = None) -> None:
        recs = self.rec(*dims) + list(extra or [])
        self.findings.append(Finding(check, severity, message, recs, hint))


# --- individual checks ---------------------------------------------------------


def _check_required(c: _Ctx) -> None:
    s = c.spec
    missing = [f for f in FAMILIES[s.family]
               if getattr(s, f) in (None, 0, (), "")]
    for f in missing:
        c.add("required", "error",
              f"family {s.family!r} cannot be drawn without {f}; the engine "
              "would refuse it", [] if f in ("pins", "rows", "cols",
                                             "custom_pads") else [f])


def _check_limits(c: _Ctx) -> None:
    s = c.spec
    for name in _DIM_FIELDS:
        d = getattr(s, name)
        if d is None:
            continue
        vals = (d.min, d.nom, d.max)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
            c.bad.add(name)
            c.add("limits", "error", f"{c.txt(name, d)}: every limit must be a "
                  "finite number", [name])
            continue
        if d.min > d.nom + 1e-9 or d.nom > d.max + 1e-9:
            c.bad.add(name)
            c.add("limits", "error",
                  f"{c.txt(name, d)}: the limits are out of order (need min <= "
                  "nom <= max); a column of the dimension table was probably "
                  "read into the wrong place", [name])
            continue
        if d.min < 0:
            c.bad.add(name)
            c.add("limits", "error", f"{c.txt(name, d)}: a length cannot be "
                  "negative", [name])
            continue
        if name != "standoff" and d.max <= 0:
            c.bad.add(name)
            c.add("limits", "error", f"{c.txt(name, d)}: a size of zero draws "
                  "nothing", [name])
    for name in ("lead_span_x", "lead_length", "lead_width"):
        d = c.d(name)
        if d is not None and d.tol == 0:
            c.notes.append(
                f"{name} has no tolerance; IPC-7351 sizes lands from the min "
                "and max, so a range from the drawing gives a better pattern")
    if s.source is None or not s.source.document:
        c.notes.append("no source datasheet named; findings cannot say where "
                       "a number came from")


def _row_axes(s: PackageSpec) -> list[tuple[str, int, str, str, str]]:
    """(row label, count, pitch dim, terminal dim, body dim along the row)."""
    f = s.family
    b = "lead_width"
    if f in ("soic", "son", "dip"):
        return [("each row", s.pins // 2, "pitch", b, "body_y")] if s.pins else []
    if f in ("sot23", "sot89"):
        return [("the lead row", 3, "pitch", b, "body_y")]
    if f in ("sot223", "dpak"):
        n = s.pins or 3
        return [("the lead row", n, "pitch", b, "body_y")]
    if f == "crystal4":
        return [("each side", 2, "pitch", b, "body_y")]
    if f in _QUAD:
        if s.pins_per_side is not None and len(s.pins_per_side) == 4:
            nl, nb, nr, nt = s.pins_per_side
        elif s.pins and s.pins % 4 == 0:
            nl = nb = nr = nt = s.pins // 4
        else:
            return []
        py = "pitch_y" if s.pitch_y is not None else "pitch"
        return [("the left side", nl, py, b, "body_y"),
                ("the bottom side", nb, "pitch", b, "body_x"),
                ("the right side", nr, py, b, "body_y"),
                ("the top side", nt, "pitch", b, "body_x")]
    if f == "bga":
        py = "pitch_y" if s.pitch_y is not None else "pitch"
        return [("a ball row", s.cols, "pitch", "ball_diameter", "body_x"),
                ("a ball column", s.rows, py, "ball_diameter", "body_y")]
    if f == "lga":
        py = "pitch_y" if s.pitch_y is not None else "pitch"
        t = "lead_length" if s.lead_length is not None else "lead_width"
        return [("a land row", s.cols, "pitch", "lead_width", "body_x"),
                ("a land column", s.rows, py, t, "body_y")]
    if f == "header":
        rows = s.rows or 1
        n = s.pins // rows if rows else s.pins
        t = "lead_diameter" if s.lead_diameter is not None else "lead_width"
        return [("the pin row", n, "pitch", t, "body_y")] if n else []
    if f in ("to220", "to92"):
        t = "lead_diameter" if s.lead_diameter is not None else "lead_width"
        return [("the lead row", s.pins, "pitch", t, "body_x")] if s.pins else []
    return []


def _check_terminal_vs_pitch(c: _Ctx) -> None:
    s = c.spec
    pairs: list[tuple[str, str]] = []
    f = s.family
    if f in ("soic", "son", "sot23", "sot89", "sot223", "dpak", "crystal4",
             "qfn", "qfp", "plcc", "dip", "header", "to220", "to92"):
        t = "lead_width"
        if f in ("dip", "header", "to220", "to92") and s.lead_diameter is not None:
            t = "lead_diameter"
        pairs.append((t, "pitch"))
        if f in _QUAD and s.pitch_y is not None:
            pairs.append((t, "pitch_y"))
    elif f in ("radial", "axial", "connector"):
        pairs.append(("lead_diameter", "pitch"))
    elif f == "lga":
        pairs.append(("lead_width", "pitch"))
        pairs.append(("lead_length" if s.lead_length is not None else
                      "lead_width", "pitch_y" if s.pitch_y is not None
                      else "pitch"))
    for t, p in pairs:
        dt, dp = c.d(t), c.d(p)
        if dt is None or dp is None:
            continue
        gap = dp.nom - dt.max
        if dt.max >= dp.nom - 1e-9:
            hint = ""
            if abs(dt.nom - dp.nom) < 0.02:
                hint = f"{t} equals the pitch: the pitch read into {t}?"
            c.add("terminal_vs_pitch", "error",
                  f"{c.txt(t)} is not below {c.txt(p)}: neighbouring "
                  "terminals would touch or overlap", [t, p], hint)
        elif gap < MIN_TERMINAL_GAP - 1e-9:
            c.add("terminal_vs_pitch", "warning",
                  f"{c.txt(t)} leaves only {_fmt(gap)} mm between terminals "
                  f"at {c.txt(p)}, under {_fmt(MIN_TERMINAL_GAP)} mm; read "
                  "both numbers off the drawing again", [t, p])
    if f == "bga":
        for p in ("pitch", "pitch_y"):
            dp, db = c.d(p), c.d("ball_diameter")
            if dp is None or db is None:
                continue
            if db.max >= dp.nom - 1e-9:
                c.add("terminal_vs_pitch", "error",
                      f"{c.txt('ball_diameter')} is not below {c.txt(p)}: "
                      "neighbouring balls would merge when they reflow",
                      ["ball_diameter", p])
            elif db.nom > MAX_BALL_RATIO * dp.nom:
                c.add("terminal_vs_pitch", "warning",
                      f"{c.txt('ball_diameter')} is {_fmt(db.nom / dp.nom * 100)} "
                      f"% of {c.txt(p)}, above {_fmt(MAX_BALL_RATIO * 100)} %, "
                      "which leaves little between neighbouring balls; read "
                      "both numbers again", ["ball_diameter", p])


def _check_rows_fit(c: _Ctx) -> None:
    s = c.spec
    for label, n, pname, tname, bname in _row_axes(s):
        dp, dt, db = c.d(pname), c.d(tname), c.d(bname)
        if n < 1 or dp is None or dt is None or db is None:
            continue
        if s.family == "sot89":
            try:
                cw = dim((s.options or {}).get("center_lead_width"))
            except SpecError:
                cw = None
            t_max = max(dt.max, cw.max if cw is not None else 0.0)
        else:
            t_max = dt.max
        need = (n - 1) * dp.nom + t_max
        if need > db.max + ROW_ALLOWANCE + 1e-9:
            other = "body_x" if bname == "body_y" else "body_y"
            do = c.d(other)
            hint = ""
            if do is not None and need <= do.max + ROW_ALLOWANCE:
                hint = (f"the row fits {other} ({_fmt(do.max)} mm): body_x and "
                        "body_y look swapped (body_x runs across the rows, "
                        "body_y along them)")
            elif abs(dp.nom - db.nom) < 0.15 or (do is not None and
                                                 abs(dp.nom - do.nom) < 0.15):
                hint = f"{pname} is close to a body dimension: the body length read as the pitch?"
            elif dp.nom * MM_PER_MIL >= 0.2 and \
                    (n - 1) * dp.nom * MM_PER_MIL + t_max <= db.max + ROW_ALLOWANCE:
                hint = (f"{pname} {_fmt(dp.nom)} as mils is "
                        f"{_fmt(dp.nom * MM_PER_MIL)} mm, which fits: mils "
                        "typed as mm?")
            c.add("row_fits_body", "error",
                  f"{label}: {n} terminals at {c.txt(pname)} with "
                  f"{c.txt(tname)} need {_fmt(need)} mm ((n - 1) x pitch + "
                  f"terminal width), but {c.txt(bname)} is at most "
                  f"{_fmt(db.max)} mm; the row cannot be longer than the body "
                  "it comes out of", [pname, tname, bname], hint)
        elif n > 1 and db.nom > 0 and need / db.nom < MIN_ROW_FILL:
            hint = ""
            if need / (db.nom * MM_PER_MIL) >= 0.3:
                hint = (f"{bname} {_fmt(db.nom)} as mils is "
                        f"{_fmt(db.nom * MM_PER_MIL)} mm: the body in mils?")
            c.add("row_fits_body", "warning",
                  f"{label}: {n} terminals at {c.txt(pname)} fill only "
                  f"{_fmt(need / db.nom * 100)} % of {c.txt(bname)}, under "
                  f"{_fmt(MIN_ROW_FILL * 100)} %; check the body and the "
                  "pitch are in the same unit", [pname, tname, bname],
                  hint)


def _span_axes(s: PackageSpec) -> list[tuple[str, str]]:
    out = [("lead_span_x", "body_x")]
    if s.family in ("qfp", "plcc"):
        out.append(("lead_span_y" if s.lead_span_y is not None else
                    "lead_span_x", "body_y"))
    if s.family == "qfn" and s.lead_span_y is not None:
        out.append(("lead_span_y", "body_y"))
    return out


def _check_span_vs_body(c: _Ctx) -> None:
    s = c.spec
    f = s.family
    for sname, bname in _span_axes(s):
        ds, db = c.d(sname), c.d(bname)
        if ds is None or db is None:
            continue
        if f in _GULLWING_LIKE:
            if ds.max <= db.min + 1e-9:
                c.add("span_vs_body", "error",
                      f"{c.txt(sname)} is not larger than {c.txt(bname)}; the "
                      "leads leave the body and reach past it, so the tip to "
                      "tip span must exceed the body", [sname, bname],
                      "the lead span (JEDEC E) and the body width (E1) look "
                      "swapped")
            elif ds.min < db.max - 1e-9:
                c.add("span_vs_body", "warning",
                      f"{c.txt(sname)} overlaps {c.txt(bname)}: at some "
                      "tolerance combination the leads would end inside the "
                      "body", [sname, bname])
        elif f in ("chip", "melf", "molded"):
            if db.min > ds.max + ROW_ALLOWANCE + 1e-9:
                c.add("span_vs_body", "error",
                      f"{c.txt(bname)} is longer than {c.txt(sname)}, the "
                      "overall length over the terminals", [sname, bname])
        elif f in ("crystal2", "crystal4"):
            if ds.nom > db.max + ROW_ALLOWANCE + 1e-9:
                c.add("span_vs_body", "error",
                      f"{c.txt(sname)} (the outer terminal edges) reaches past "
                      f"{c.txt(bname)}; bottom terminations sit under the body",
                      [sname, bname])
        elif f in _NOLEAD:
            if abs(ds.nom - db.nom) > 0.3 + 1e-9:
                c.add("span_vs_body", "warning",
                      f"{c.txt(sname)} differs from {c.txt(bname)} by "
                      f"{_fmt(abs(ds.nom - db.nom))} mm; the terminals of a "
                      "no-lead part end at the body edge", [sname, bname])


def _check_terminal_length(c: _Ctx) -> None:
    s = c.spec
    f = s.family
    dt = c.d("lead_length")
    if dt is None:
        return
    spans: list[str] = []
    if f in _TWO_TERMINAL or f in ("soic", "son", "sot23", "crystal4"):
        spans = ["lead_span_x" if s.lead_span_x is not None else "body_x"]
    elif f in _QUAD:
        spans = ["lead_span_x" if s.lead_span_x is not None else "body_x"]
        if s.lead_span_y is not None:
            spans.append("lead_span_y")
        elif f == "qfn":
            spans.append("body_y")
    elif f == "sot223" and s.tab_length is None:
        spans = ["lead_span_x"]
    for sname in spans:
        ds = c.d(sname)
        if ds is None:
            continue
        if 2 * dt.nom >= ds.nom - 1e-9:
            c.add("terminal_length", "error",
                  f"two terminals of {c.txt('lead_length')} do not fit in "
                  f"{c.txt(sname)}: they would meet in the middle. "
                  "lead_length is one terminal (T or L), the span the whole "
                  "length over both", ["lead_length", sname],
                  "the overall length, or the body, read into lead_length?")
        elif 2 * dt.max >= ds.min - 1e-9:
            c.add("terminal_length", "warning",
                  f"at the tolerance extremes two terminals of "
                  f"{c.txt('lead_length')} meet inside {c.txt(sname)}",
                  ["lead_length", sname])
    # Asymmetric parts: a lead and the tab across one span.
    if f in ("sot89", "dpak") or (f == "sot223" and s.tab_length is not None):
        ds, dtab = c.d("lead_span_x"), c.d("tab_length")
        if ds is not None and dtab is not None:
            if dt.nom + dtab.nom >= ds.nom - 1e-9:
                c.add("terminal_length", "error",
                      f"{c.txt('lead_length')} plus {c.txt('tab_length')} do "
                      f"not fit in {c.txt('lead_span_x')}: the lead foot and "
                      "the tab would overlap", ["lead_length", "tab_length",
                                                "lead_span_x"])
    # Gull wing: the foot sits outside the body, within the lead's reach.
    if f in ("soic", "sot23", "sot223", "qfp", "sod"):
        pairs = [("lead_span_x", "body_x")]
        if f == "qfp":
            pairs.append(("lead_span_y" if s.lead_span_y is not None else
                          "lead_span_x", "body_y"))
        for sname, bname in pairs:
            ds, db = c.d(sname), c.d(bname)
            if ds is None or db is None or ds.max <= db.min:
                continue
            reach_most = (ds.max - db.min) / 2.0
            reach_nom = (ds.nom - db.nom) / 2.0
            if dt.min > reach_most + REACH_ALLOWANCE + 1e-9:
                c.add("terminal_length", "error",
                      f"even the shortest foot, {c.txt('lead_length')}, is "
                      f"longer than the lead's reach outside the body "
                      f"((span max - body min) / 2 = {_fmt(reach_most)} mm "
                      f"from {c.txt(sname)} and {c.txt(bname)})",
                      ["lead_length", sname, bname],
                      "lead_length is the foot (JEDEC L), not the whole lead")
            elif dt.nom > reach_nom + REACH_ALLOWANCE + 1e-9:
                c.add("terminal_length", "warning",
                      f"the nominal foot, {c.txt('lead_length')}, is longer "
                      f"than the nominal reach outside the body "
                      f"({_fmt(reach_nom)} mm from {c.txt(sname)} and "
                      f"{c.txt(bname)})", ["lead_length", sname, bname])


def _check_exposed_pad(c: _Ctx) -> None:
    s = c.spec
    ex, ey = c.d("ep_x"), c.d("ep_y")
    if (s.ep_x is None) != (s.ep_y is None):
        c.add("exposed_pad", "error", "an exposed pad needs both ep_x and "
              "ep_y; only one was given", ["ep_x", "ep_y"])
        return
    if ex is None or ey is None:
        return
    ox, oy = s.ep_offset
    for e, bname, off in ((ex, "body_x", ox), (ey, "body_y", oy)):
        ename = "ep_x" if e is ex else "ep_y"
        db = c.d(bname)
        if db is None:
            continue
        if e.max > db.max + 1e-9:
            other = "body_y" if bname == "body_x" else "body_x"
            do = c.d(other)
            hint = ""
            if do is not None and e.max <= do.max:
                hint = "ep_x and ep_y, or body_x and body_y, look swapped"
            c.add("exposed_pad", "error",
                  f"{c.txt(ename)} is larger than {c.txt(bname)}: the exposed "
                  "pad is part of the body's underside", [ename, bname], hint)
            continue
        if abs(off) + e.max / 2.0 > db.max / 2.0 + 1e-9:
            c.add("exposed_pad", "error",
                  f"{c.txt(ename)} offset by {_fmt(off)} mm reaches past the "
                  f"edge of {c.txt(bname)}", [ename, bname])
    # No-lead parts: terminals run in from the body edge on the sides that
    # carry them, and the pad must stay clear of them.
    dt = c.d("lead_length")
    axes: list[tuple[Dim, str, str]] = []
    if s.family == "son":
        axes = [(ex, "ep_x", "body_x")]
    elif s.family == "qfn":
        axes = [(ex, "ep_x", "body_x"), (ey, "ep_y", "body_y")]
    for e, ename, bname in axes:
        db = c.d(bname)
        if db is None or dt is None or e.max > db.max:
            continue
        if e.nom > db.nom - 2 * dt.nom + 1e-9:
            c.add("exposed_pad", "error",
                  f"{c.txt(ename)} overlaps the terminals: at nominal sizes "
                  f"{c.txt(bname)} less two terminals of "
                  f"{c.txt('lead_length')} leaves "
                  f"{_fmt(db.nom - 2 * dt.nom)} mm between them",
                  [ename, bname, "lead_length"])
        elif e.max > db.min - 2 * dt.max + 1e-9:
            c.add("exposed_pad", "warning",
                  f"at the tolerance extremes {c.txt(ename)} reaches the "
                  f"terminals ({c.txt(bname)}, {c.txt('lead_length')})",
                  [ename, bname, "lead_length"])
    bx, by = c.d("body_x"), c.d("body_y")
    if bx is not None and by is not None and min(bx.nom, by.nom) > 0 and \
            min(ex.nom, ey.nom) > 0:
        body_long_y = by.nom > 1.15 * bx.nom
        body_long_x = bx.nom > 1.15 * by.nom
        ep_long_y = ey.nom > 1.15 * ex.nom
        ep_long_x = ex.nom > 1.15 * ey.nom
        if (body_long_y and ep_long_x) or (body_long_x and ep_long_y):
            c.add("exposed_pad", "warning",
                  f"the exposed pad ({c.txt('ep_x')}, {c.txt('ep_y')}) runs the "
                  f"other way to the body ({c.txt('body_x')}, "
                  f"{c.txt('body_y')})", ["ep_x", "ep_y", "body_x", "body_y"],
                  "ep_x and ep_y swapped?")


def _check_tab(c: _Ctx) -> None:
    s = c.spec
    if s.family not in _TAB:
        return
    dtw, dby, dlw = c.d("tab_width"), c.d("body_y"), c.d("lead_width")
    if dtw is not None and dby is not None and \
            dtw.max > dby.max + ROW_ALLOWANCE + 1e-9:
        c.add("tab", "error", f"{c.txt('tab_width')} is wider than "
              f"{c.txt('body_y')}; the tab comes out of the body",
              ["tab_width", "body_y"])
    if dtw is not None and dlw is not None and dtw.max < dlw.min - 1e-9:
        c.add("tab", "warning", f"{c.txt('tab_width')} is narrower than a "
              f"lead ({c.txt('lead_width')})", ["tab_width", "lead_width"],
              "tab_width and lead_width swapped?")


def _check_pin_count(c: _Ctx) -> None:
    s = c.spec
    f = s.family
    if f in ("soic", "son", "dip") and s.pins % 2:
        c.add("pin_count", "error", f"{f} has two equal rows, so an odd pin "
              f"count ({s.pins}) cannot be right", [])
    if f == "sot23" and s.pins not in (3, 5, 6):
        c.add("pin_count", "error", f"SOT-23 comes with 3, 5 or 6 leads, not "
              f"{s.pins}", [])
    if f in _QUAD:
        pps = s.pins_per_side
        if pps is not None:
            if len(pps) != 4 or sum(pps) != s.pins:
                c.add("pin_count", "error",
                      f"pins_per_side {list(pps)} must be four counts (left, "
                      f"bottom, right, top) adding up to pins ({s.pins})", [])
            elif pps[0] != pps[2] or pps[1] != pps[3]:
                c.add("pin_count", "warning",
                      f"pins_per_side {list(pps)} puts a different count on "
                      "opposite sides; check the order is left, bottom, right, "
                      "top", [])
        elif s.pins % 4:
            c.add("pin_count", "error",
                  f"{s.pins} pins do not divide over four equal sides; give "
                  "pins_per_side (left, bottom, right, top)", [])
    elif s.pins_per_side is not None:
        c.add("pin_count", "warning", f"pins_per_side is only read for quad "
              f"families; {f} ignores it", [])
    if f in ("bga", "lga") and s.rows > 0 and s.cols > 0:
        from eda_agent.design.landpattern.smd import bga_row_label
        names = [bga_row_label(r) + str(k + 1)
                 for r in range(s.rows) for k in range(s.cols)]
        unknown = [d for d in s.depopulated if d not in names]
        if unknown:
            c.add("pin_count", "error", f"depopulated positions {unknown} are "
                  f"not in a {s.rows} x {s.cols} grid", [])
        rename = (s.options or {}).get("names")
        if rename is not None:
            if len(rename) != len(names):
                c.add("pin_count", "error",
                      f"options['names'] has {len(rename)} entries for a "
                      f"{s.rows} x {s.cols} grid of {len(names)}", [])
            count = sum(1 for k, n in enumerate(names)
                        if n not in s.depopulated and k < len(rename)
                        and str(rename[k]))
        else:
            count = len(names) - len(set(s.depopulated) & set(names))
        if not s.pins:
            # Without the printed count a depopulated grid is built full:
            # a 153-ball part described as 14 x 14 came out with 196.
            c.add("pin_count", "error",
                  f"pins is required for {f}: give the ball count the "
                  f"datasheet prints, so the {s.rows} x {s.cols} grid "
                  f"({count} positions) is checked against it", [])
        elif s.pins != count:
            c.add("pin_count", "error",
                  f"pins says {s.pins}, but a {s.rows} x {s.cols} grid less "
                  f"{len(s.depopulated)} depopulated position(s) has {count}",
                  [])
    if f == "dpak" and s.lead_numbers is not None and s.pins and \
            len(s.lead_numbers) != s.pins:
        c.add("pin_count", "error", f"lead_numbers has {len(s.lead_numbers)} "
              f"entries for {s.pins} lead slots", [])
    if f == "header" and s.rows == 2 and s.pins % 2:
        c.add("pin_count", "error", f"a two-row header cannot have {s.pins} "
              "pins", [])
    if f in ("custom", "connector") and s.pins and s.custom_pads:
        named = {p.number for p in s.custom_pads if p.number}
        if len(named) != s.pins:
            c.add("pin_count", "error",
                  f"pins says {s.pins}, but the drawing lists "
                  f"{len(named)} numbered contacts", [])


def _units_hint(v: float) -> str:
    return f"{_fmt(v)} as mils is {_fmt(v * MM_PER_MIL)} mm"


def _check_units(c: _Ctx) -> None:
    s = c.spec
    scale_vals = [c.d(n).nom for n in _SCALE_DIMS if c.d(n) is not None]
    scale = max(scale_vals) if scale_vals else None
    for name in _TERMINAL_DIMS:
        d = c.d(name)
        if d is None or d.nom <= 0:
            continue
        if name == "pitch" and s.family in ("axial", "radial"):
            continue
        if scale is not None and d.nom > 1.5 * scale and \
                d.nom * MM_PER_MIL <= scale:
            c.add("units", "error",
                  f"{c.txt(name)} is larger than the whole package (its "
                  f"largest outline dimension is {_fmt(scale)} mm); "
                  f"{_units_hint(d.nom)}", [name],
                  "mils typed as mm")
            continue
        if name in ("pitch", "pitch_y") and d.nom > 12.7:
            c.add("units", "warning",
                  f"{c.txt(name)} is a pitch few parts have; "
                  f"{_units_hint(d.nom)}", [name], "mils typed as mm?")
        if name in ("pitch", "pitch_y") and d.nom < 0.2:
            c.add("units", "warning",
                  f"{c.txt(name)} is below any package pitch; as inches it "
                  f"would be {_fmt(d.nom * 25.4)} mm", [name],
                  "inches typed as mm?")
        if name in ("lead_width", "lead_length", "ball_diameter",
                    "lead_diameter") and d.nom > 10.0:
            c.add("units", "warning",
                  f"{c.txt(name)} is larger than any terminal; "
                  f"{_units_hint(d.nom)}", [name], "mils typed as mm?")
    for name in _SCALE_DIMS:
        d = c.d(name)
        if d is not None and d.nom > 150.0:
            c.add("units", "warning",
                  f"{c.txt(name)} is larger than a component footprint "
                  f"normally is; {_units_hint(d.nom)}", [name],
                  "mils typed as mm?")
        if d is not None and 0 < d.nom < 0.1:
            c.add("units", "warning",
                  f"{c.txt(name)} is smaller than any package; as inches it "
                  f"would be {_fmt(d.nom * 25.4)} mm", [name],
                  "inches typed as mm?")


def _check_height(c: _Ctx) -> None:
    dh, ds = c.d("height"), c.d("standoff")
    if dh is None:
        c.notes.append("no package height; a 3D body and the footprint "
                       "height cannot be set")
        return
    if ds is not None and dh.max > 0 and ds.min > dh.max + 1e-9:
        c.add("height", "error", f"{c.txt('standoff')} is above the top of "
              f"the package, {c.txt('height')}", ["standoff", "height"],
              "standoff (A1) and height (A) swapped?")


def _check_vendor(c: _Ctx) -> None:
    """The vendor's recommended land pattern, checked on its own terms."""
    v = c.spec.vendor
    if v is None:
        return
    cite = v.cite.short() if v.cite is not None and v.cite.short() else \
        "no citation"

    def vrec(*pairs: tuple[str, Optional[float]]) -> list[dict]:
        return [{"name": f"vendor.{n}", "value": val, "cite": cite}
                for n, val in pairs]

    if v.pad_width is not None and v.pitch is not None and \
            v.pad_width >= v.pitch - 1e-9:
        c.findings.append(Finding(
            "vendor_pattern", "error",
            f"the vendor land width {_fmt(v.pad_width)} mm is not below its "
            f"pitch {_fmt(v.pitch)} mm ({cite}): neighbouring lands would "
            "touch", vrec(("pad_width", v.pad_width), ("pitch", v.pitch)),
            "land length and width swapped?"))
    for span, label in ((v.span, "span"), (v.span_y, "span_y")):
        if span is None or v.pad_length is None:
            continue
        if v.pad_length >= span - 1e-9:
            c.findings.append(Finding(
                "vendor_pattern", "error",
                f"the vendor land length {_fmt(v.pad_length)} mm is not below "
                f"its centre-to-centre {label} {_fmt(span)} mm ({cite}): the "
                "two rows would overlap", vrec(("pad_length", v.pad_length),
                                              (label, span)),
                "the span taken toe to toe, or the land length misread?"))
            continue
        ep = v.ep_x if label == "span" else v.ep_y
        if ep is not None and ep / 2.0 > span / 2.0 - v.pad_length / 2.0 + 1e-9:
            c.findings.append(Finding(
                "vendor_pattern", "error",
                f"the vendor thermal land {_fmt(ep)} mm reaches the lands of "
                f"the {label} {_fmt(span)} mm with lands {_fmt(v.pad_length)} "
                f"mm long ({cite})", vrec(("ep", ep), (label, span),
                                          ("pad_length", v.pad_length))))
    if v.land_diameter is not None and v.pitch is not None and \
            v.land_diameter >= v.pitch - 1e-9:
        c.findings.append(Finding(
            "vendor_pattern", "error",
            f"the vendor land {_fmt(v.land_diameter)} mm is not below its "
            f"pitch {_fmt(v.pitch)} mm ({cite})",
            vrec(("land_diameter", v.land_diameter), ("pitch", v.pitch))))
    pads = list(v.pads)
    for i, a in enumerate(pads):
        for b in pads[i + 1:]:
            if a.number == b.number:
                continue
            if abs(a.x - b.x) < (a.width + b.width) / 2.0 - 1e-9 and \
                    abs(a.y - b.y) < (a.height + b.height) / 2.0 - 1e-9:
                c.findings.append(Finding(
                    "vendor_pattern", "error",
                    f"vendor lands {a.number} and {b.number} overlap ({cite})",
                    vrec((f"pad {a.number}", a.x), (f"pad {b.number}", b.x))))


_CHECKS: tuple[tuple[str, Callable[[_Ctx], None]], ...] = (
    ("limits", _check_limits),
    ("required", _check_required),
    ("terminal_vs_pitch", _check_terminal_vs_pitch),
    ("row_fits_body", _check_rows_fit),
    ("span_vs_body", _check_span_vs_body),
    ("terminal_length", _check_terminal_length),
    ("exposed_pad", _check_exposed_pad),
    ("tab", _check_tab),
    ("pin_count", _check_pin_count),
    ("units", _check_units),
    ("height", _check_height),
    ("vendor_pattern", _check_vendor),
)

#: Check ids, in the order they run.
CHECK_IDS = tuple(name for name, _ in _CHECKS)


def check_package(package: Any) -> dict:
    """Run every check on a package description.

    ``package`` is a :class:`PackageSpec`, its ``to_dict`` form or that as
    JSON text. Returns ``{ok, errors, warnings, findings, examined,
    notes}``; ``ok`` is true when no finding is an error. ``examined``
    lists the checks that ran, so a clean result says what it covered.
    """
    try:
        spec = read_package(package)
    except SpecError as exc:
        f = Finding("readable", "error", str(exc))
        return {"ok": False, "errors": 1, "warnings": 0,
                "findings": [f.to_dict()], "examined": ["readable"],
                "notes": []}
    if spec.family not in FAMILIES:
        f = Finding("family", "error",
                    f"unknown family {spec.family!r}; known: "
                    f"{', '.join(sorted(FAMILIES))}")
        return {"ok": False, "errors": 1, "warnings": 0,
                "findings": [f.to_dict()], "examined": ["readable", "family"],
                "notes": []}
    c = _Ctx(spec)
    for name, fn in _CHECKS:
        fn(c)
        c.examined.append(name)
    errors = sum(1 for f in c.findings if f.severity == "error")
    warnings = sum(1 for f in c.findings if f.severity == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings,
            "family": spec.family, "name": spec.name,
            "findings": [f.to_dict() for f in c.findings],
            "examined": ["readable", "family"] + c.examined,
            "notes": c.notes}
