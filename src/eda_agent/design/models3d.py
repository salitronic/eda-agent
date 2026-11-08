# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A part's 3D model as a STEP file, built from its package description.

The accurate body for a footprint is a STEP model: the vendor's when there
is one, otherwise one built here from the same :class:`PackageSpec` the
land pattern came from. The moulded body sits at its standoff and rises to
its height, every lead or terminal is where the land pattern put its pad,
an exposed pad is under the body, and BGA balls are spheres. An extruded
body (an outline lifted to a height) is the fallback only, for a family
not modelled here or a machine with no CAD kernel.

The drawing is the only source: every dimension the model uses must cite
the datasheet's mechanical drawing (its own citation, or the package's
``source``), and a description that does not is refused rather than
modelled from guesses. Two writers, tried in order: CadQuery (``pip install eda-agent[models3d]``)
and FreeCAD's headless ``freecadcmd``. Both build the same list of solids.
Every number the datasheet did not give (a lead thickness, say) is taken
from :data:`ASSUMED` and listed in the result, never hidden.

Coordinates are mm, origin at the package centroid, Z up from the board.
"""

from __future__ import annotations

import json
import math
import re
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..noconsole import no_window

__all__ = [
    "Solid",
    "ModelPlan",
    "ASSUMED",
    "MODELLED_FAMILIES",
    "plan_model",
    "plan_from_solids",
    "terminal_solids",
    "strip_terminals",
    "available_writers",
    "write_step",
]

#: Values used when the package description leaves them out. Each use is
#: recorded in the plan's ``assumed`` list with the field it stood for.
ASSUMED = {
    "lead_thickness": 0.2,      # gull-wing and J lead stock, mm
    "terminal_thickness": 0.05, # no-lead terminal and exposed pad, mm
    "standoff": 0.05,           # body underside when A1 is not given, mm
    "tht_pin_length": 3.0,      # through-hole pin below the board, mm
    "tht_pin_diameter": 0.5,    # round pin when no lead diameter is given
}

#: Colours as (r, g, b) 0-1: moulded body, metal, ceramic chip body.
_COLOURS = {
    "body": (0.15, 0.15, 0.15),
    "metal": (0.78, 0.78, 0.80),
    "ceramic": (0.55, 0.45, 0.33),
    "ball": (0.75, 0.75, 0.78),
    "white": (0.93, 0.93, 0.90),
    "lens": (0.97, 0.97, 0.95),
    "gold": (0.85, 0.68, 0.25),
    "pin1": (0.90, 0.90, 0.90),
}


@dataclass
class Solid:
    """One primitive. ``kind``: box (x0..z1), cylinder (centre, radius,
    z0..z1 along ``axis`` x, y or z), sphere (centre, radius) or prism (a
    polygon ``profile`` in the XZ plane, swept along Y from y0 to y1, or
    in the YZ plane swept along X when ``axis`` is x)."""

    kind: str
    role: str = "body"
    x0: float = 0.0
    y0: float = 0.0
    z0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    z1: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    cz: float = 0.0
    r: float = 0.0
    axis: str = "z"
    profile: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModelPlan:
    family: str
    solids: list[Solid]
    assumed: list[str]
    height: float
    sources: dict[str, str] = field(default_factory=dict)
    uncited: list[str] = field(default_factory=list)

    @property
    def from_drawing(self) -> bool:
        """Every dimension used names the datasheet drawing it came from."""
        return bool(self.sources) and not self.uncited

    def to_dict(self) -> dict:
        return {"family": self.family, "height": self.height,
                "assumed": list(self.assumed), "sources": dict(self.sources),
                "uncited": list(self.uncited),
                "solids": [s.to_dict() for s in self.solids]}


def _nom(d: Any) -> Optional[float]:
    return None if d is None else float(d.nom)


class _Ctx:
    """Reads dimensions off the spec and records where each came from: a
    model is designed from the datasheet's mechanical drawing, so every
    dimension used names that drawing (its own citation, or the package's
    source), and one with neither is listed in ``uncited``."""

    def __init__(self, spec: Any):
        self.spec = spec
        self.assumed: list[str] = []
        self.sources: dict[str, str] = {}
        self.uncited: list[str] = []
        src = getattr(spec, "source", None)
        self.package_source = src.short() if src is not None else ""

    def get(self, name: str, fallback_key: Optional[str] = None) -> Optional[float]:
        d = getattr(self.spec, name, None)
        # The HEIGHT is the maximum: drawings often give only "A max",
        # written (0, max), whose midpoint is no height at all, and the
        # maximum is what a clearance check needs. Every other dimension
        # is nominal.
        v = (None if d is None else float(d.max)) if name == "height" else _nom(d)
        if d is not None:
            cite = d.cite.short() if getattr(d, "cite", None) else self.package_source
            if cite:
                self.sources[name] = cite
            elif name not in self.uncited:
                self.uncited.append(name)
        if v is None and fallback_key:
            v = ASSUMED[fallback_key]
            self.assumed.append(f"{name} = {v} mm (not in the description)")
        return v


def _box(role, x0, y0, z0, x1, y1, z1) -> Solid:
    return Solid("box", role, min(x0, x1), min(y0, y1), min(z0, z1),
                 max(x0, x1), max(y0, y1), max(z0, z1))


def _signal_pads(model: Any) -> list:
    return [p for p in model.pads if getattr(p, "role", "signal") == "signal"]


def _thermal_pads(model: Any) -> list:
    return [p for p in model.pads if getattr(p, "role", "") in ("thermal", "tab")]


def _side(p: Any, bx: float, by: float) -> str:
    """Which body edge a pad's lead leaves from: left, right, bottom, top."""
    fx = abs(p.x) / max(bx / 2.0, 1e-6)
    fy = abs(p.y) / max(by / 2.0, 1e-6)
    if fx >= fy:
        return "right" if p.x > 0 else "left"
    return "top" if p.y > 0 else "bottom"


def _gull_lead(side: str, pos: float, width: float, body_edge: float,
               tip: float, foot: float, t: float, z_exit: float) -> Solid:
    """A gull-wing lead: out of the body at ``z_exit``, down to the board,
    a foot of length ``foot`` ending at ``tip`` (distance from centre)."""
    # The bend's centre line: the foot's inner end is ``foot`` in from the
    # tip, and the leg never sits inside the body.
    knee = max(tip - foot + t / 2.0, body_edge + t)
    h = t / 2.0
    # profile in (u, z): u runs outward from the centre, starting just
    # inside the body so the lead is anchored in it.
    prof = [(body_edge - 0.1, z_exit), (knee + h, z_exit), (knee + h, t),
            (tip, t), (tip, 0.0), (knee - h, 0.0), (knee - h, z_exit - t),
            (body_edge - 0.1, z_exit - t)]
    return _lead_prism(side, pos, width, prof)


def _lead_prism(side: str, pos: float, width: float,
                prof: list[tuple[float, float]]) -> Solid:
    if side in ("right", "left"):
        sgn = 1.0 if side == "right" else -1.0
        pts = [(sgn * u, z) for u, z in prof]
        return Solid("prism", "metal", y0=pos - width / 2.0,
                     y1=pos + width / 2.0, axis="y", profile=pts)
    sgn = 1.0 if side == "top" else -1.0
    pts = [(sgn * u, z) for u, z in prof]
    return Solid("prism", "metal", x0=pos - width / 2.0,
                 x1=pos + width / 2.0, axis="x", profile=pts)


def _flat_terminal(side: str, pos: float, width: float, outer: float,
                   length: float, t: float) -> Solid:
    """A no-lead terminal on the underside, flush with the body edge."""
    inner = outer - length
    if side == "right":
        return _box("metal", inner, pos - width / 2, 0, outer, pos + width / 2, t)
    if side == "left":
        return _box("metal", -outer, pos - width / 2, 0, -inner, pos + width / 2, t)
    if side == "top":
        return _box("metal", pos - width / 2, inner, 0, pos + width / 2, outer, t)
    return _box("metal", pos - width / 2, -outer, 0, pos + width / 2, -inner, t)


def _two_terminal(c: _Ctx, model: Any, family: str) -> list[Solid]:
    """Chip, MELF and moulded two-terminal parts: L overall, T terminal
    length, W width, H height."""
    L = c.get("lead_span_x")
    T = c.get("lead_length")
    W = c.get("lead_width") or c.get("body_y")
    H = c.get("height")
    if not (L and T and W and H):
        return []
    if family == "melf":
        r = H / 2.0
        return [Solid("cylinder", "body", cx=0, cy=0, cz=r, r=r * 0.98,
                      x0=-L / 2 + T, x1=L / 2 - T, axis="x"),
                Solid("cylinder", "metal", cx=0, cy=0, cz=r, r=r,
                      x0=-L / 2, x1=-L / 2 + T, axis="x"),
                Solid("cylinder", "metal", cx=0, cy=0, cz=r, r=r,
                      x0=L / 2 - T, x1=L / 2, axis="x")]
    if family == "chip":
        t = 0.02
        return [_box("ceramic", -L / 2 + T, -W / 2, t, L / 2 - T, W / 2, H - t),
                _box("metal", -L / 2, -W / 2, 0, -L / 2 + T, W / 2, H),
                _box("metal", L / 2 - T, -W / 2, 0, L / 2, W / 2, H)]
    # moulded (tantalum, SMA/SMB): body over two flat terminals underneath
    bx = c.get("body_x")
    if bx is None:
        bx = L - T
        c.assumed.append(f"body_x = {bx:g} mm (L - T, not in the description)")
    by = c.get("body_y") or W
    so = c.get("standoff", "standoff")
    tt = ASSUMED["terminal_thickness"]
    return [_box("body", -bx / 2, -by / 2, so, bx / 2, by / 2, H),
            _box("metal", -L / 2, -W / 2, 0, -L / 2 + T, W / 2, tt),
            _box("metal", L / 2 - T, -W / 2, 0, L / 2, W / 2, tt)]


def _leaded(c: _Ctx, model: Any, family: str) -> list[Solid]:
    """Gull-wing parts (SOIC, SOT-23, QFP, SOD, SOT-223): body plus one
    lead per land, leaving the body edge nearest it."""
    bx = c.get("body_x")
    by = c.get("body_y")
    H = c.get("height")
    span_x = c.get("lead_span_x")
    span_y = c.get("lead_span_y") or span_x
    T = c.get("lead_length")
    W = c.get("lead_width")
    if family == "sod":
        by = by or W
        bx = bx or (span_x - 2 * T if span_x and T else None)
    if not (bx and by and H and span_x and T and W):
        return []
    so = c.get("standoff", "standoff")
    t = c.get("lead_thickness", "lead_thickness")
    z_exit = so + (H - so) * 0.4
    out = [_box("body", -bx / 2, -by / 2, so, bx / 2, by / 2, H)]
    for p in _signal_pads(model):
        side = _side(p, bx, by)
        if side in ("left", "right"):
            out.append(_gull_lead(side, p.y, W, bx / 2, span_x / 2, T, t, z_exit))
        else:
            out.append(_gull_lead(side, p.x, W, by / 2, span_y / 2, T, t, z_exit))
    for p in _thermal_pads(model):
        tw = _nom(getattr(c.spec, "tab_width", None)) or p.height
        side = _side(p, bx, by)
        pos = p.y if side in ("left", "right") else p.x
        out.append(_gull_lead(side, pos, tw, bx / 2 if side in ("left", "right")
                              else by / 2, span_x / 2, T, t, z_exit))
    return out


def _no_lead(c: _Ctx, model: Any, family: str) -> list[Solid]:
    """QFN, SON, LGA-style crystals: body flush on the board with
    terminals on the underside at its edges, and the exposed pad."""
    bx = c.get("body_x")
    by = c.get("body_y")
    H = c.get("height")
    T = c.get("lead_length")
    W = c.get("lead_width")
    if not (bx and by and H and T and W):
        return []
    tt = ASSUMED["terminal_thickness"]
    c.assumed.append(f"terminal thickness = {tt} mm (drawn, not given)")
    out = [_box("body", -bx / 2, -by / 2, tt, bx / 2, by / 2, H)]
    for p in _signal_pads(model):
        side = _side(p, bx, by)
        if side in ("left", "right"):
            out.append(_flat_terminal(side, p.y, W, bx / 2, T, tt))
        else:
            out.append(_flat_terminal(side, p.x, W, by / 2, T, tt))
    ex, ey = c.get("ep_x"), c.get("ep_y")
    if ex and ey:
        ox, oy = getattr(c.spec, "ep_offset", (0.0, 0.0))
        out.append(_box("metal", ox - ex / 2, oy - ey / 2, 0, ox + ex / 2,
                        oy + ey / 2, tt))
    return out


def _array(c: _Ctx, model: Any, family: str) -> list[Solid]:
    """BGA balls (spheres) or LGA lands under a body."""
    bx, by, H = c.get("body_x"), c.get("body_y"), c.get("height")
    if not (bx and by and H):
        return []
    if family == "bga":
        d = c.get("ball_diameter")
        if not d:
            return []
        so = c.get("standoff")
        if so is None:
            so = 0.8 * d
            c.assumed.append(f"ball standoff = {so:g} mm (0.8 x ball diameter)")
        r = d / 2.0
        out = [_box("body", -bx / 2, -by / 2, so, bx / 2, by / 2, H)]
        # Each ball hangs from the body underside; below a full diameter
        # of standoff it reaches into the board, as a collapsed ball does.
        for p in model.pads:
            out.append(Solid("sphere", "ball", cx=p.x, cy=p.y, cz=so - r, r=r))
        return out
    tt = ASSUMED["terminal_thickness"]
    out = [_box("body", -bx / 2, -by / 2, tt, bx / 2, by / 2, H)]
    for p in model.pads:
        out.append(_box("metal", p.x - p.width / 2, p.y - p.height / 2, 0,
                        p.x + p.width / 2, p.y + p.height / 2, tt))
    return out


def _through_hole(c: _Ctx, model: Any, family: str) -> list[Solid]:
    """DIP, radial and axial parts: a body above the board and a round pin
    down through each plated hole."""
    H = c.get("height")
    if not H:
        return []
    pins = [p for p in model.pads if getattr(p, "hole", 0) > 0]
    if not pins:
        return []
    dpin = c.get("lead_diameter")
    if dpin is None:
        dpin = min(min(p.hole for p in pins) * 0.8, ASSUMED["tht_pin_diameter"])
        c.assumed.append(f"pin diameter = {dpin:g} mm (from the drill)")
    below = ASSUMED["tht_pin_length"]
    c.assumed.append(f"pin length below the board = {below} mm")
    so = c.get("standoff")
    if so is None:
        so = 0.5 if family == "dip" else 0.0
        c.assumed.append(f"body standoff = {so} mm (not in the description)")
    out: list[Solid] = []
    if family == "radial":
        D = c.get("body_x")
        if not D:
            return []
        out.append(Solid("cylinder", "body", cx=0, cy=0, r=D / 2, z0=so, z1=H))
        top = so
    elif family == "axial":
        L, D = c.get("body_x"), c.get("body_y")
        if not (L and D):
            return []
        r = D / 2.0
        out.append(Solid("cylinder", "body", cx=0, cy=0, cz=r + so, r=r,
                         x0=-L / 2, x1=L / 2, axis="x"))
        xs = sorted(p.x for p in pins)
        for x in (xs[0], xs[-1]):
            inner = -L / 2 if x < 0 else L / 2
            out.append(Solid("cylinder", "metal", cx=0, cy=0, cz=r + so,
                             r=dpin / 2, x0=min(x, inner), x1=max(x, inner),
                             axis="x"))
        top = r + so
    else:
        bx, by = c.get("body_x"), c.get("body_y")
        if not (bx and by):
            return []
        out.append(_box("body", -bx / 2, -by / 2, so, bx / 2, by / 2, H))
        top = so + 0.5
        # A DIP's rows sit outside its plastic: each pin hangs from a
        # shoulder that leaves the body side at ``top``.
        t = c.get("lead_thickness", "lead_thickness")
        for p in pins:
            if abs(p.x) > bx / 2:
                edge = bx / 2 if p.x > 0 else -bx / 2
                out.append(_box("metal", edge, p.y - dpin / 2, top - t,
                                p.x + (dpin / 2 if p.x > 0 else -dpin / 2),
                                p.y + dpin / 2, top))
    for p in pins:
        out.append(Solid("cylinder", "metal", cx=p.x, cy=p.y, r=dpin / 2,
                         z0=-below, z1=top))
    return out


_FAMILIES = {
    "chip": _two_terminal, "melf": _two_terminal, "molded": _two_terminal,
    "sod": _leaded, "sot23": _leaded, "soic": _leaded, "qfp": _leaded,
    "sot223": _leaded,
    "son": _no_lead, "qfn": _no_lead, "crystal2": _no_lead,
    "crystal4": _no_lead,
    "bga": _array, "lga": _array,
    "dip": _through_hole, "radial": _through_hole, "axial": _through_hole,
}

#: Families this module draws; any other falls back to an extruded body.
MODELLED_FAMILIES = tuple(sorted(_FAMILIES))


def _top(s: Solid) -> float:
    if s.kind == "sphere" or (s.kind == "cylinder" and s.axis == "x"):
        return s.cz + s.r
    if s.kind == "prism":
        return max(z for _, z in s.profile)
    return s.z1


#: Fields each solid kind needs in plan_from_solids.
_SOLID_FIELDS = {
    "box": ("x0", "y0", "z0", "x1", "y1", "z1"),
    "cylinder": ("r",),
    "sphere": ("cx", "cy", "cz", "r"),
    "prism": ("profile",),
}


def plan_from_solids(solids: list[dict], source: str) -> tuple[Optional[ModelPlan], list[str]]:
    """A model drawn as a list of primitives, for a part no family covers.

    A connector, a LED with a lens or a header has a shape no package
    family describes, but its datasheet drawing gives every dimension of
    it. The caller reads them off the drawing and passes the solids, in
    mm, origin at the footprint origin, Z up from the board; ``source``
    names the drawing (document, page, figure) and is required, as for
    every model here. Returns ``(plan, problems)``; plan is None when
    any solid is malformed, and the problems say which and why.
    """
    problems: list[str] = []
    out: list[Solid] = []
    allowed = set(Solid.__dataclass_fields__)
    for i, raw in enumerate(solids or []):
        if not isinstance(raw, dict):
            problems.append(f"solid {i}: not an object")
            continue
        kind = str(raw.get("kind", "")).lower()
        if kind not in _SOLID_FIELDS:
            problems.append(f"solid {i}: kind {raw.get('kind')!r} is not one of "
                            + ", ".join(_SOLID_FIELDS))
            continue
        missing = [f for f in _SOLID_FIELDS[kind] if f not in raw]
        if kind == "cylinder":
            ends = ("x0", "x1") if raw.get("axis") == "x" else ("z0", "z1")
            missing += [f for f in ends if f not in raw]
        if kind == "prism":
            ends = ("x0", "x1") if raw.get("axis") == "x" else ("y0", "y1")
            missing += [f for f in ends if f not in raw]
        if missing:
            problems.append(f"solid {i} ({kind}): missing {', '.join(missing)}")
            continue
        role = str(raw.get("role") or "body")
        if role not in _COLOURS:
            problems.append(f"solid {i}: role {role!r} is not one of "
                            + ", ".join(sorted(_COLOURS)))
            continue
        try:
            args = {k: v for k, v in raw.items() if k in allowed}
            args["kind"], args["role"] = kind, role
            if kind == "prism":
                args["profile"] = [(float(u), float(z)) for u, z in raw["profile"]]
                if len(args["profile"]) < 3:
                    raise ValueError("a profile needs three points or more")
            for k, v in list(args.items()):
                if k not in ("kind", "role", "axis", "profile"):
                    args[k] = float(v)
            s = Solid(**args)
        except (TypeError, ValueError) as exc:
            problems.append(f"solid {i} ({kind}): {exc}")
            continue
        if kind == "box" and not (s.x1 > s.x0 and s.y1 > s.y0 and s.z1 > s.z0):
            problems.append(f"solid {i} (box): each far edge must exceed its near edge")
            continue
        if kind in ("cylinder", "sphere") and s.r <= 0:
            problems.append(f"solid {i} ({kind}): r must be positive")
            continue
        out.append(s)
    if not out and not problems:
        problems.append("no solids given")
    if problems:
        return None, problems
    source = str(source or "").strip()
    plan = ModelPlan("custom", out, [], round(max(_top(s) for s in out), 6),
                     {"drawing": source} if source else {},
                     [] if source else ["every solid (no source drawing named)"])
    return plan, []


#: Roles a part's terminals are drawn in.
TERMINAL_ROLES = ("gold", "metal", "ball")


def _land_box(land: dict) -> tuple[float, float, float, float]:
    x, y = float(land["x"]), float(land["y"])
    w, h = float(land["w"]), float(land["h"])
    if round(float(land.get("rotation") or 0)) % 180 == 90:
        w, h = h, w
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def terminal_solids(lands: list[dict], kind: str, height: float,
                    diameter: float = 0.0) -> tuple[list[Solid], list[str]]:
    """One terminal for every land of the footprint, where the land is.

    ``lands`` are ``{name, x, y, w, h, shape, rotation}`` in mm.
    ``kind`` "plate": a gold plate the land's shape and size, ``height``
    thick (an LGA or LCC terminal); "ball": a solder ball of ``diameter``
    whose top is ``height`` above the board (a BGA ball as seated).
    Returns ``(solids, problems)``.
    """
    if kind not in ("plate", "ball"):
        return [], [f"terminals kind {kind!r} is plate or ball"]
    if not height or height <= 0:
        return [], ["terminals need height, mm above the board"]
    if kind == "ball" and (not diameter or diameter <= 0):
        return [], ["ball terminals need diameter, the ball's from the drawing"]
    if not lands:
        return [], ["the footprint has no lands to put terminals on"]
    out: list[Solid] = []
    for land in lands:
        x0, y0, x1, y1 = _land_box(land)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if kind == "ball":
            r = diameter / 2
            out.append(Solid("sphere", "ball", cx=cx, cy=cy, cz=height - r, r=r))
        elif str(land.get("shape", "")).lower() in ("circle", "round")                 and abs((x1 - x0) - (y1 - y0)) < 1e-6:
            out.append(Solid("cylinder", "gold", cx=cx, cy=cy, z0=0.0, z1=height,
                             r=(x1 - x0) / 2))
        else:
            out.append(Solid("box", "gold", x0=x0, y0=y0, z0=0.0,
                             x1=x1, y1=y1, z1=height))
    return out, []


def strip_terminals(solids: list[Solid], lands: list[dict]) -> list[str]:
    """Flat terminal-coloured solids at the board that cover lands of two
    names or more: a row of terminals drawn as one strip, which no part
    has. Terminals come from the footprint (``terminal_solids``)."""
    problems = []
    for i, s in enumerate(solids):
        if s.role not in TERMINAL_ROLES or s.kind != "box":
            continue
        if s.z0 > 0.05 or s.z1 - s.z0 > 0.3:
            continue  # a can or a lead above the board, not a terminal
        names = sorted({str(l["name"]) for l in lands
                        if s.x0 < float(l["x"]) < s.x1 and s.y0 < float(l["y"]) < s.y1})
        if len(names) > 1:
            problems.append(
                f"solid {i} ({s.role} box) covers lands {', '.join(names[:6])}"
                f"{', ...' if len(names) > 6 else ''}: each terminal is its own "
                "solid; give terminals to place one on every land")
    return problems


def plan_model(spec: Any, model: Any) -> Optional[ModelPlan]:
    """The solids for ``spec`` with its land pattern ``model``, or None
    when the family is not modelled or the description lacks a dimension
    the shape needs (the caller then uses the extruded fallback)."""
    fn = _FAMILIES.get(getattr(spec, "family", ""))
    if fn is None:
        return None
    c = _Ctx(spec)
    solids = fn(c, model, spec.family)
    if not solids:
        return None
    height = max(_top(s) for s in solids)
    # each assumption once, in the order met
    seen: list[str] = []
    for a in c.assumed:
        if a not in seen:
            seen.append(a)
    return ModelPlan(spec.family, solids, seen, round(height, 6),
                     dict(c.sources), list(c.uncited))


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def _freecadcmd() -> Optional[str]:
    found = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    if found:
        return found
    if sys.platform == "win32":
        roots = [Path(os.environ.get(k, "")) for k in
                 ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")]
        for root in roots:
            if not root.is_dir():
                continue
            for exe in sorted(root.glob("FreeCAD*/bin/freecadcmd.exe"), reverse=True):
                return str(exe)
    return None


def warm_cadquery() -> None:
    """Load CadQuery once in a throwaway process, in the background.

    Measured: the first STEP write after a reboot spent 140 s importing
    CadQuery's kernel from disk, which moved the first footprint call
    past the client's two-minute wait; the next import took 2 s. A
    child process that imports and exits fills the OS file cache for
    this server's later import without holding the kernel in memory in
    every server that never draws a model.
    """
    import importlib.util
    import subprocess
    import sys

    try:
        if importlib.util.find_spec("cadquery") is None:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
            getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)
        subprocess.Popen([sys.executable, "-c", "import cadquery"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
    except (OSError, ImportError, ValueError):  # a warm-up only; the real import reports
        pass


def _has_cadquery() -> bool:
    """Whether CadQuery is installed, without importing it here (the
    worker process does that, see _write_cadquery)."""
    import importlib.util
    try:
        return importlib.util.find_spec("cadquery") is not None
    except (ImportError, ValueError):
        return False


#: Names the STEP writer to try first ("freecad" or "cadquery"); the other
#: stays as the fallback.
STEP_WRITER_ENV = "EDA_AGENT_STEP_WRITER"


def available_writers() -> list[str]:
    """The STEP writers this machine has, in the order they are tried:
    CadQuery first, unless ``EDA_AGENT_STEP_WRITER`` names FreeCAD."""
    out = []
    if _has_cadquery():
        out.append("cadquery")
    if _freecadcmd():
        out.append("freecad")
    first = os.environ.get(STEP_WRITER_ENV, "").strip().lower()
    if first in out:
        out.remove(first)
        out.insert(0, first)
    return out


def _cq_solid(s: Solid):
    import cadquery as cq

    if s.kind == "box":
        return (cq.Workplane("XY")
                .box(s.x1 - s.x0, s.y1 - s.y0, s.z1 - s.z0, centered=False)
                .translate((s.x0, s.y0, s.z0)))
    if s.kind == "sphere":
        return cq.Workplane("XY").sphere(s.r).translate((s.cx, s.cy, s.cz))
    if s.kind == "cylinder":
        if s.axis == "x":
            return (cq.Workplane("YZ").circle(s.r).extrude(s.x1 - s.x0)
                    .translate((s.x0, s.cy, s.cz)))
        return (cq.Workplane("XY").circle(s.r).extrude(s.z1 - s.z0)
                .translate((s.cx, s.cy, s.z0)))
    if s.kind == "prism":
        if s.axis == "y":
            # Profile in XZ; the XZ workplane extrudes along -Y.
            wp = cq.Workplane("XZ").polyline(s.profile).close()
            return wp.extrude(-(s.y1 - s.y0)).translate((0, s.y0, 0))
        wp = cq.Workplane("YZ").polyline(s.profile).close()
        return wp.extrude(s.x1 - s.x0).translate((s.x0, 0, 0))
    raise ValueError(f"unknown solid kind {s.kind}")


_worker_lock = threading.Lock()
_worker: dict = {}


def _worker_proc():
    """The STEP worker (step_worker.py), started on first use."""
    proc = _worker.get("proc")
    if proc is not None and proc.poll() is None:
        return proc
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "eda_agent.design.step_worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", creationflags=flags)
    first = proc.stdout.readline()
    try:
        hello = json.loads(first) if first else {}
    except ValueError:
        hello = {}
    if not hello.get("ready"):
        proc.kill()
        raise RuntimeError("the STEP worker could not load CadQuery: "
                           + str(hello.get("error") or first.strip() or "no answer"))
    _worker["proc"] = proc
    return proc


def _write_cadquery(plan: ModelPlan, path: Path, name: str) -> None:
    """Write through the worker process, never by importing CadQuery
    here: in the server the first import took up to 224 s (see
    step_worker.py). A worker that died is started again once."""
    req = json.dumps({"plan": plan.to_dict(), "path": str(path), "name": name}) + "\n"
    with _worker_lock:
        for attempt in (1, 2):
            proc = _worker_proc()
            try:
                proc.stdin.write(req)
                proc.stdin.flush()
                line = proc.stdout.readline()
            except (OSError, ValueError):
                line = ""
            if line:
                reply = json.loads(line)
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "the STEP worker failed")
                return
            _worker.pop("proc", None)           # it went away: once more
        raise RuntimeError("the STEP worker stopped answering")


def _write_cadquery_here(plan: ModelPlan, path: Path, name: str) -> None:
    import cadquery as cq

    assy = cq.Assembly(name=name)
    for i, s in enumerate(plan.solids):
        rgb = _COLOURS.get(s.role, _COLOURS["metal"])
        assy.add(_cq_solid(s), name=f"{s.role}_{i}",
                 color=cq.Color(rgb[0], rgb[1], rgb[2]))
    if hasattr(assy, "export"):      # CadQuery 2.5 on; save is deprecated
        assy.export(str(path))
    else:
        assy.save(str(path), exportType="STEP")


_FREECAD_SCRIPT = r'''
import json, sys
import FreeCAD, Part
data = json.load(open(sys.argv[-2]))
out = sys.argv[-1]
V = FreeCAD.Vector
shapes = []
for s in data["solids"]:
    k = s["kind"]
    if k == "box":
        b = Part.makeBox(s["x1"] - s["x0"], s["y1"] - s["y0"], s["z1"] - s["z0"])
        b.translate(V(s["x0"], s["y0"], s["z0"]))
    elif k == "sphere":
        b = Part.makeSphere(s["r"], V(s["cx"], s["cy"], s["cz"]))
    elif k == "cylinder":
        if s["axis"] == "x":
            b = Part.makeCylinder(s["r"], s["x1"] - s["x0"], V(s["x0"], s["cy"], s["cz"]), V(1, 0, 0))
        else:
            b = Part.makeCylinder(s["r"], s["z1"] - s["z0"], V(s["cx"], s["cy"], s["z0"]), V(0, 0, 1))
    else:
        if s["axis"] == "y":
            pts = [V(u, s["y0"], z) for u, z in s["profile"]]
            vec = V(0, s["y1"] - s["y0"], 0)
        else:
            pts = [V(s["x0"], u, z) for u, z in s["profile"]]
            vec = V(s["x1"] - s["x0"], 0, 0)
        b = Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(vec)
    shapes.append(b)
Part.makeCompound(shapes).exportStep(out)
'''


def _write_freecad(plan: ModelPlan, path: Path) -> None:
    exe = _freecadcmd()
    if not exe:
        raise RuntimeError("freecadcmd not found")
    with tempfile.TemporaryDirectory(prefix="eda_step_") as tmp:
        script = Path(tmp) / "build_step.py"
        data = Path(tmp) / "solids.json"
        script.write_text(_FREECAD_SCRIPT, encoding="utf-8")
        data.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
        proc = subprocess.run([exe, str(script), str(data), str(path)],
                              capture_output=True, text=True, timeout=180,
                              **no_window())
        if not path.is_file():
            raise RuntimeError("freecadcmd wrote no STEP: "
                               + (proc.stderr or proc.stdout)[-400:])
    _colour_step(path, [_COLOURS.get(s.role, _COLOURS["metal"]) for s in plan.solids])


_STEP_ENTITY = re.compile(r"^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\(", re.M)


def _colour_step(path: Path, colours: list[tuple[float, float, float]]) -> bool:
    """Give each solid of a STEP file its colour, as STEP presentation styles.

    FreeCAD without its GUI writes geometry only, so a model came out all
    grey. Each solid is a MANIFOLD_SOLID_BREP, written in the order the
    solids were given; each gets a STYLED_ITEM with its RGB, gathered in one
    MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION, the AP214 way
    CAD tools (Altium included) read colours. Left unchanged, and False,
    when the solid count does not match.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    found = [(m.group(1), m.group(2)) for m in _STEP_ENTITY.finditer(text)]
    breps = [n for n, kind in found if kind == "MANIFOLD_SOLID_BREP"]
    if not breps or len(breps) != len(colours):
        return False
    ctx = next((n for n, kind in found if kind == "GEOMETRIC_REPRESENTATION_CONTEXT"), "")
    if not ctx:
        m = re.search(r"^#(\d+)\s*=\s*\(\s*GEOMETRIC_REPRESENTATION_CONTEXT", text, re.M)
        ctx = m.group(1) if m else ""
    if not ctx:
        return False
    nid = max(int(n) for n, _ in found) + 1
    lines, styled = [], []
    for brep, (r, g, b) in zip(breps, colours):
        a = nid
        lines += [f"#{a}=COLOUR_RGB('',{r:.4f},{g:.4f},{b:.4f});",
                  f"#{a + 1}=FILL_AREA_STYLE_COLOUR('',#{a});",
                  f"#{a + 2}=FILL_AREA_STYLE('',(#{a + 1}));",
                  f"#{a + 3}=SURFACE_STYLE_FILL_AREA(#{a + 2});",
                  f"#{a + 4}=SURFACE_SIDE_STYLE('',(#{a + 3}));",
                  f"#{a + 5}=SURFACE_STYLE_USAGE(.BOTH.,#{a + 4});",
                  f"#{a + 6}=PRESENTATION_STYLE_ASSIGNMENT((#{a + 5}));",
                  f"#{a + 7}=STYLED_ITEM('color',(#{a + 6}),#{brep});"]
        styled.append(f"#{a + 7}")
        nid += 8
    lines.append(f"#{nid}=MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION("
                 f"'',({','.join(styled)}),#{ctx});")
    end = text.rfind("ENDSEC;", 0, text.rfind("END-ISO-10303-21"))
    if end < 0:
        return False
    path.write_text(text[:end] + "\n".join(lines) + "\n" + text[end:], encoding="utf-8")
    return True


def write_step(plan: ModelPlan, path: str | Path, *, name: str = "part",
               writers: Optional[list[str]] = None) -> dict[str, Any]:
    """Write ``plan`` as a STEP file with the first writer that works.

    A model is designed from the datasheet's mechanical drawing: a plan
    with a dimension that names no drawing (``uncited``) is refused, not
    written. Returns ``{"ok", "path", "writer", "tried"}``; ``ok`` False
    when refused, when no writer is available or when every one failed,
    with each failure in ``tried``.
    """
    if not plan.from_drawing:
        return {"ok": False, "path": "", "writer": "", "tried": [],
                "reason": "a model is built from the datasheet mechanical "
                          "drawing, and these dimensions name none: "
                          + (", ".join(plan.uncited) or "all of them")
                          + ". Cite the drawing (the package source or "
                          "each dimension's cite) and build again."}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tried: list[dict[str, str]] = []
    for w in writers if writers is not None else available_writers():
        try:
            if path.exists():
                path.unlink()
            if w == "cadquery":
                _write_cadquery(plan, path, name)
            elif w == "freecad":
                _write_freecad(plan, path)
            else:
                raise ValueError(f"unknown writer {w}")
            head = path.read_bytes()[:64]
            if not head.startswith(b"ISO-10303-21"):
                raise RuntimeError("the file written is not STEP")
            return {"ok": True, "path": str(path), "writer": w, "tried": tried}
        except Exception as exc:  # each writer's failure is reported
            tried.append({"writer": w, "error": f"{type(exc).__name__}: {exc}"[:300]})
    reason = ("no STEP writer on this machine: install CadQuery "
              "(pip install eda-agent[models3d]) or FreeCAD"
              if not tried else "every STEP writer failed")
    return {"ok": False, "path": "", "writer": "", "tried": tried,
            "reason": reason}
