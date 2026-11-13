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
    "ceramic_ivory": (0.92, 0.89, 0.80),
    "nickel": (0.70, 0.70, 0.68),
    "seal_ring": (0.80, 0.68, 0.42),
    "silicon": (0.30, 0.32, 0.38),
    "backside_coat": (0.07, 0.07, 0.08),
    "substrate": (0.09, 0.22, 0.13),
    "pcb_green": (0.10, 0.38, 0.18),
    "tin": (0.82, 0.82, 0.82),
    "lcp_white": (0.94, 0.93, 0.88),
    "laser": (0.52, 0.52, 0.52),
    "ink": (0.95, 0.95, 0.95),
}

#: A solid of this role is not drawn: it is taken out of the solids it
#: overlaps (or those its ``cut_from`` names), a notch, a castellation
#: half-hole, the bore of a hollow cylinder.
CUT_ROLE = "cut"

#: Roles a printed mark (``text``, ``dot``) may take. A mark is cut
#: ``depth`` into the top face it sits on and filled with its colour, so
#: it is flush with that face and never raised or wrapped over an edge.
MARK_ROLES = ("ink", "laser", "pin1", "white")

#: How deep a mark is cut into its face (mm), and how far it must stay
#: inside that face's edges.
MARK_DEPTH = 0.008
MARK_INSET = 0.02


@dataclass
class Solid:
    """One primitive. ``kind``: box (x0..z1), cylinder (centre, radius,
    z0..z1 along ``axis`` x, y or z), sphere (centre, radius) or prism (a
    polygon ``profile`` in the XZ plane, swept along Y from y0 to y1, or
    in the YZ plane swept along X when ``axis`` is x); extrude (an XY
    ``outline`` from z0 to z1); text and dot (a mark flush in the face at
    z1).

    A box or an extrude takes ``corner_r`` (its vertical edges rounded),
    ``edge_r`` (its top edges rounded) and ``taper`` (degrees, the sides
    drawn in as they rise). A sphere takes ``clip_z0`` / ``clip_z1``, the
    planes it is cut flat at (a seated solder ball). ``rot`` (degrees
    about Z through the origin) then ``dx dy dz`` place any solid; an
    instance of a group is its solids so placed.
    """

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
    name: str = ""
    cut_from: list[str] = field(default_factory=list)
    corner_r: float = 0.0
    edge_r: float = 0.0
    taper: float = 0.0
    outline: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""
    size: float = 0.0
    bold: bool = False
    angle: float = 0.0
    depth: float = 0.0
    clip_z0: Optional[float] = None
    clip_z1: Optional[float] = None
    rot: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

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


def seated_ball(x: float, y: float, diameter: float, standoff: float) -> Solid:
    """A solder ball as seated: a sphere of the ball's diameter centred
    half way up the standoff and cut flat at the board (z 0) and at the
    package underside (z standoff), a barrel that neither hangs below
    the board nor floats under the body."""
    return Solid("sphere", "ball", cx=x, cy=y, cz=standoff / 2.0,
                 r=diameter / 2.0, clip_z0=0.0, clip_z1=standoff)


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
        out = [_box("body", -bx / 2, -by / 2, so, bx / 2, by / 2, H)]
        for p in model.pads:
            out.append(seated_ball(p.x, p.y, d, so))
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


def _local_bounds(s: Any) -> tuple[float, float, float, float, float, float]:
    """(xmin, xmax, ymin, ymax, zmin, zmax) of a solid before ``rot`` and
    ``dx dy dz``. Text is estimated from its size (0.6 of it per letter)."""
    g = s.get if isinstance(s, dict) else (lambda k, d=None: getattr(s, k, d))
    f = lambda k: float(g(k, 0.0) or 0.0)  # noqa: E731
    kind = str(g("kind", "box") or "box").lower()
    axis = str(g("axis", "z") or "z").lower()
    if kind == "box":
        return (min(f("x0"), f("x1")), max(f("x0"), f("x1")),
                min(f("y0"), f("y1")), max(f("y0"), f("y1")),
                min(f("z0"), f("z1")), max(f("z0"), f("z1")))
    if kind == "sphere":
        r = f("r")
        lo = f("cz") - r if g("clip_z0", None) is None else max(f("cz") - r, f("clip_z0"))
        hi = f("cz") + r if g("clip_z1", None) is None else min(f("cz") + r, f("clip_z1"))
        return f("cx") - r, f("cx") + r, f("cy") - r, f("cy") + r, lo, hi
    if kind == "cylinder":
        r = f("r")
        if axis == "x":
            return (min(f("x0"), f("x1")), max(f("x0"), f("x1")),
                    f("cy") - r, f("cy") + r, f("cz") - r, f("cz") + r)
        if axis == "y":
            return (f("cx") - r, f("cx") + r, min(f("y0"), f("y1")),
                    max(f("y0"), f("y1")), f("cz") - r, f("cz") + r)
        return (f("cx") - r, f("cx") + r, f("cy") - r, f("cy") + r,
                min(f("z0"), f("z1")), max(f("z0"), f("z1")))
    if kind == "prism":
        prof = [(float(u), float(z)) for u, z in (g("profile", []) or [])] or [(0.0, 0.0)]
        us, zs = [u for u, _ in prof], [z for _, z in prof]
        if axis == "x":
            return (min(f("x0"), f("x1")), max(f("x0"), f("x1")),
                    min(us), max(us), min(zs), max(zs))
        return min(us), max(us), min(f("y0"), f("y1")), max(f("y0"), f("y1")), min(zs), max(zs)
    if kind == "extrude":
        pts = [(float(x), float(y)) for x, y in (g("outline", []) or [])] or [(0.0, 0.0)]
        return (min(x for x, _ in pts), max(x for x, _ in pts),
                min(y for _, y in pts), max(y for _, y in pts),
                min(f("z0"), f("z1")), max(f("z0"), f("z1")))
    if kind in ("text", "dot"):
        if kind == "dot":
            hw = hh = f("r")
        else:
            hw, hh = 0.3 * f("size") * len(str(g("text", "") or "")), f("size") / 2
        a = math.radians(f("angle"))
        ex = abs(hw * math.cos(a)) + abs(hh * math.sin(a))
        ey = abs(hw * math.sin(a)) + abs(hh * math.cos(a))
        depth = f("depth") or MARK_DEPTH
        return (f("cx") - ex, f("cx") + ex, f("cy") - ey, f("cy") + ey,
                f("z1") - depth, f("z1"))
    return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0


def solid_bounds(s: Any) -> tuple[float, float, float, float, float, float]:
    """(xmin, xmax, ymin, ymax, zmin, zmax) of a solid where it is placed."""
    x0, x1, y0, y1, z0, z1 = _local_bounds(s)
    g = s.get if isinstance(s, dict) else (lambda k, d=None: getattr(s, k, d))
    rot = math.radians(float(g("rot", 0.0) or 0.0))
    dx, dy, dz = (float(g(k, 0.0) or 0.0) for k in ("dx", "dy", "dz"))
    if rot:
        c, n = math.cos(rot), math.sin(rot)
        pts = [(x * c - y * n, x * n + y * c) for x in (x0, x1) for y in (y0, y1)]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    return x0 + dx, x1 + dx, y0 + dy, y1 + dy, z0 + dz, z1 + dz


def _drawn(s: Any) -> bool:
    """Whether a solid is part of the model (a cutter is not)."""
    role = s.get("role") if isinstance(s, dict) else getattr(s, "role", "body")
    return str(role or "body") != CUT_ROLE


def _top(s: Solid) -> float:
    return solid_bounds(s)[5]


#: Fields each solid kind needs in plan_from_solids.
_SOLID_FIELDS = {
    "box": ("x0", "y0", "z0", "x1", "y1", "z1"),
    "cylinder": ("r",),
    "sphere": ("cx", "cy", "cz", "r"),
    "prism": ("profile",),
    "extrude": ("outline", "z0", "z1"),
    "text": ("text", "size", "cx", "cy", "z1"),
    "dot": ("cx", "cy", "r", "z1"),
}

#: Fields kept as given rather than read as numbers.
_TEXT_FIELDS = ("kind", "role", "axis", "profile", "outline", "name",
                "cut_from", "text", "bold", "clip_z0", "clip_z1")


def _expand_instances(solids: list, groups: Optional[dict],
                      problems: list[str]) -> list:
    """Each ``{"kind": "instance", "group": name, rot, dx, dy, dz}`` as
    the group's solids placed there. A name inside a group (and the
    cut_from that point at it) gets ``@n``, the instance's number, so two
    copies cut only their own solids."""
    out: list = []
    groups = groups or {}
    count = 0
    for i, raw in enumerate(solids or []):
        if not (isinstance(raw, dict) and str(raw.get("kind", "")).lower() == "instance"):
            out.append(raw)
            continue
        name = str(raw.get("group", ""))
        members = groups.get(name)
        if not isinstance(members, list) or not members:
            problems.append(f"solid {i} (instance): no group {name!r}; groups: "
                            + (", ".join(sorted(groups)) or "none given"))
            continue
        count += 1
        try:
            rot = float(raw.get("rot") or 0)
            dx, dy, dz = (float(raw.get(k) or 0) for k in ("dx", "dy", "dz"))
        except (TypeError, ValueError):
            problems.append(f"solid {i} (instance): rot, dx, dy and dz are numbers")
            continue
        for m in members:
            if not isinstance(m, dict) or str(m.get("kind", "")).lower() == "instance":
                problems.append(f"group {name!r}: a member is not a solid "
                                "(a group holds no instances)")
                break
            if float(m.get("rot") or 0):
                problems.append(f"group {name!r}: place a member with its own "
                                "coordinates, not rot; the instance turns it")
                break
            c = dict(m)
            c["rot"] = rot
            c["dx"] = float(m.get("dx") or 0) * math.cos(math.radians(rot)) \
                - float(m.get("dy") or 0) * math.sin(math.radians(rot)) + dx
            c["dy"] = float(m.get("dx") or 0) * math.sin(math.radians(rot)) \
                + float(m.get("dy") or 0) * math.cos(math.radians(rot)) + dy
            c["dz"] = float(m.get("dz") or 0) + dz
            if c.get("name"):
                c["name"] = f"{c['name']}@{count}"
            if c.get("cut_from"):
                c["cut_from"] = [f"{n}@{count}" for n in c["cut_from"]]
            out.append(c)
    return out


def _solid_problem(s: Solid) -> str:
    """Why a parsed solid cannot be built, or empty."""
    if s.kind == "box" and not (s.x1 > s.x0 and s.y1 > s.y0 and s.z1 > s.z0):
        return "each far edge must exceed its near edge"
    if s.kind in ("cylinder", "sphere", "dot") and s.r <= 0:
        return "r must be positive"
    if s.kind == "extrude":
        if len(s.outline) < 3:
            return "an outline needs three points or more"
        if not s.z1 > s.z0:
            return "z1 must exceed z0"
    if s.kind in ("box", "extrude"):
        x0, x1, y0, y1, z0, z1 = _local_bounds(s)
        if s.corner_r < 0 or s.edge_r < 0:
            return "corner_r and edge_r cannot be negative"
        if s.corner_r and s.corner_r * 2 >= min(x1 - x0, y1 - y0):
            return "corner_r must be under half the narrower side"
        if s.edge_r and s.edge_r >= z1 - z0:
            return "edge_r must be under the height"
        if not 0 <= s.taper < 60:
            return "taper is degrees from 0 to under 60"
        if s.taper and min(x1 - x0, y1 - y0) / 2 <= (z1 - z0) * math.tan(math.radians(s.taper)):
            return "the taper closes the top before z1"
    elif s.corner_r or s.edge_r or s.taper:
        return "corner_r, edge_r and taper are for a box or an extrude"
    if s.kind in ("text", "dot"):
        if s.role not in MARK_ROLES:
            return "a mark's role is one of " + ", ".join(MARK_ROLES)
        if s.kind == "text" and (not s.text.strip() or s.size <= 0):
            return "text needs the string and a size above 0"
        if s.depth < 0:
            return "depth cannot be negative"
    elif s.role in ("ink", "laser"):
        return "ink and laser are the colours of a text or dot mark"
    if s.role == CUT_ROLE and s.kind in ("text", "dot"):
        return "a mark is not a cut"
    if s.kind != "sphere" and (s.clip_z0 is not None or s.clip_z1 is not None):
        return "clip_z0 and clip_z1 are for a sphere"
    if s.kind == "sphere" and s.clip_z0 is not None and s.clip_z1 is not None \
            and not s.clip_z1 > s.clip_z0:
        return "clip_z1 must exceed clip_z0"
    return ""


def plan_from_solids(solids: list[dict], source: str,
                     groups: Optional[dict] = None) -> tuple[Optional[ModelPlan], list[str]]:
    """A model drawn as a list of primitives, for a part no family covers.

    A connector, a LED with a lens or a header has a shape no package
    family describes, but its datasheet drawing gives every dimension of
    it. The caller reads them off the drawing and passes the solids, in
    mm, origin at the footprint origin, Z up from the board; ``source``
    names the drawing (document, page, figure) and is required, as for
    every model here. ``groups`` names lists of solids an ``instance``
    places. Returns ``(plan, problems)``; plan is None when any solid is
    malformed, and the problems say which and why.
    """
    problems: list[str] = []
    out: list[Solid] = []
    allowed = set(Solid.__dataclass_fields__)
    solids = _expand_instances(solids, groups, problems)
    for i, raw in enumerate(solids or []):
        if not isinstance(raw, dict):
            problems.append(f"solid {i}: not an object")
            continue
        kind = str(raw.get("kind", "")).lower()
        if kind not in _SOLID_FIELDS:
            problems.append(f"solid {i}: kind {raw.get('kind')!r} is not one of "
                            + ", ".join(list(_SOLID_FIELDS) + ["instance"]))
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
        default = "ink" if kind in ("text", "dot") else "body"
        role = str(raw.get("role") or default)
        if role not in _COLOURS and role != CUT_ROLE:
            problems.append(f"solid {i}: role {role!r} is not one of "
                            + ", ".join(sorted(list(_COLOURS) + [CUT_ROLE])))
            continue
        try:
            args = {k: v for k, v in raw.items() if k in allowed}
            args["kind"], args["role"] = kind, role
            if kind == "prism":
                args["profile"] = [(float(u), float(z)) for u, z in raw["profile"]]
                if len(args["profile"]) < 3:
                    raise ValueError("a profile needs three points or more")
            if kind == "extrude":
                args["outline"] = [(float(x), float(y)) for x, y in raw["outline"]]
            for k, v in list(args.items()):
                if k not in _TEXT_FIELDS:
                    args[k] = float(v)
            for k in ("clip_z0", "clip_z1"):
                if args.get(k) is not None:
                    args[k] = float(args[k])
            args["name"] = str(args.get("name") or "")
            cut_from = args.get("cut_from") or []
            args["cut_from"] = [cut_from] if isinstance(cut_from, str) else \
                [str(n) for n in cut_from]
            args["text"] = str(args.get("text") or "")
            args["bold"] = bool(args.get("bold"))
            s = Solid(**args)
        except (TypeError, ValueError) as exc:
            problems.append(f"solid {i} ({kind}): {exc}")
            continue
        why = _solid_problem(s)
        if why:
            problems.append(f"solid {i} ({kind}): {why}")
            continue
        if s.cut_from and s.role != CUT_ROLE:
            problems.append(f"solid {i} ({kind}): cut_from is for a solid of role cut")
            continue
        out.append(s)
    names = {s.name for s in out if s.name}
    for i, s in enumerate(out):
        unknown = [n for n in s.cut_from if n not in names]
        if unknown:
            problems.append(f"solid {i}: cut_from names {', '.join(unknown)}, "
                            "which no solid is named")
    drawn = [s for s in out if _drawn(s)]
    if out and not drawn and not problems:
        problems.append("every solid is a cut; nothing is left to draw")
    if not out and not problems:
        problems.append("no solids given")
    if problems:
        return None, problems
    source = str(source or "").strip()
    plan = ModelPlan("custom", out, [], round(max(_top(s) for s in drawn), 6),
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


_CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")


def chamfered_outline(x0: float, y0: float, x1: float, y1: float,
                      cut: Any) -> Any:
    """A land rectangle with corners cut at 45 degrees, as an outline.

    ``cut`` is ``{"size": mm, "corners": ["top_left", ...]}`` (a single
    ``corner`` also works), top being +Y. Returns the outline, or the
    problem as a string.
    """
    if not isinstance(cut, dict):
        return "a chamfer is {size, corners}"
    try:
        size = float(cut.get("size") or 0)
    except (TypeError, ValueError):
        return "chamfer size must be a number"
    corners = cut.get("corners") or ([cut["corner"]] if cut.get("corner") else [])
    if isinstance(corners, str):
        corners = [corners]
    bad = [c for c in corners if c not in _CORNERS]
    if not corners or bad:
        return "chamfer corners are " + ", ".join(_CORNERS)
    if not 0 < size < min(x1 - x0, y1 - y0):
        return "chamfer size must be above 0 and below the land's width"
    pts: list[tuple[float, float]] = []
    for name, (x, y), (ax, ay), (bx, by) in (
            ("bottom_left", (x0, y0), (0, 1), (1, 0)),
            ("bottom_right", (x1, y0), (-1, 0), (0, 1)),
            ("top_right", (x1, y1), (0, -1), (-1, 0)),
            ("top_left", (x0, y1), (1, 0), (0, -1))):
        if name in corners:
            pts += [(x + ax * size, y + ay * size), (x + bx * size, y + by * size)]
        else:
            pts.append((x, y))
    return pts


def terminal_solids(lands: list[dict], kind: str, height: float,
                    diameter: float = 0.0,
                    chamfered: Optional[dict] = None) -> tuple[list[Solid], list[str]]:
    """One terminal for every land of the footprint, where the land is.

    ``lands`` are ``{name, x, y, w, h, shape, rotation}`` in mm.
    ``kind`` "plate": a gold plate the land's shape and size, ``height``
    thick (an LGA or LCC terminal); "ball": a solder ball of ``diameter``
    seated between the board and the package underside ``height`` above
    it (seated_ball). ``chamfered`` maps a land name to its chamfer
    ``{size, corners}`` (a DFN's pin 1 pad). Returns ``(solids, problems)``.
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
    chamfers = {str(k): v for k, v in (chamfered or {}).items()}
    for land in lands:
        x0, y0, x1, y1 = _land_box(land)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cut = chamfers.get(str(land.get("name", "")))
        if kind == "ball":
            out.append(seated_ball(cx, cy, diameter, height))
        elif cut is not None:
            outline = chamfered_outline(x0, y0, x1, y1, cut)
            if isinstance(outline, str):
                return [], [f"land {land.get('name')}: {outline}"]
            out.append(Solid("extrude", "gold", z0=0.0, z1=height, outline=outline))
        elif str(land.get("shape", "")).lower() in ("circle", "round") \
                and abs((x1 - x0) - (y1 - y0)) < 1e-6:
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
        if s.role not in TERMINAL_ROLES or s.kind not in ("box", "extrude"):
            continue
        x0, x1, y0, y1, z0, z1 = solid_bounds(s)
        if z0 > 0.05 or z1 - z0 > 0.3:
            continue  # a can or a lead above the board, not a terminal
        names = sorted({str(l["name"]) for l in lands
                        if x0 < float(l["x"]) < x1 and y0 < float(l["y"]) < y1})
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


def _cq_profile(s: Solid):
    """A box or extrude: its outline, rounded at the corners, extruded
    with its taper, the top edges rounded, before placing."""
    import cadquery as cq

    if s.kind == "box":
        w, h = s.x1 - s.x0, s.y1 - s.y0
        sketch = cq.Sketch().rect(w, h)
        at = ((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2)
    else:
        sketch = cq.Sketch().polygon(list(s.outline) + [s.outline[0]])
        at = (0.0, 0.0)
    if s.corner_r:
        sketch = sketch.vertices().fillet(s.corner_r)
    body = (cq.Workplane("XY").workplane(offset=s.z0).center(*at)
            .placeSketch(sketch).extrude(s.z1 - s.z0, taper=s.taper or None))
    if s.edge_r:
        body = body.faces(">Z").edges().fillet(s.edge_r)
    return body


def _cq_mark(s: Solid):
    import cadquery as cq

    depth = s.depth or MARK_DEPTH
    if s.kind == "dot":
        mark = cq.Workplane("XY").circle(s.r).extrude(depth)
    else:
        mark = cq.Workplane("XY").text(s.text, s.size, depth,
                                       kind="bold" if s.bold else "regular",
                                       halign="center", valign="center")
    if s.angle:
        mark = mark.rotate((0, 0, 0), (0, 0, 1), s.angle)
    return mark.translate((s.cx, s.cy, s.z1 - depth))


def _cq_placed(s: Solid, shape):
    if s.rot:
        shape = shape.rotate((0, 0, 0), (0, 0, 1), s.rot)
    if s.dx or s.dy or s.dz:
        shape = shape.translate((s.dx, s.dy, s.dz))
    return shape


def _cq_solid(s: Solid):
    import cadquery as cq

    if s.kind in ("text", "dot"):
        return _cq_placed(s, _cq_mark(s))
    if s.kind in ("extrude",) or (s.kind == "box" and (s.corner_r or s.edge_r or s.taper)):
        return _cq_placed(s, _cq_profile(s))
    return _cq_placed(s, _cq_plain(s))


def _cq_plain(s: Solid):
    import cadquery as cq

    if s.kind == "box":
        return (cq.Workplane("XY")
                .box(s.x1 - s.x0, s.y1 - s.y0, s.z1 - s.z0, centered=False)
                .translate((s.x0, s.y0, s.z0)))
    if s.kind == "sphere":
        ball = cq.Workplane("XY").sphere(s.r).translate((s.cx, s.cy, s.cz))
        if s.clip_z0 is not None or s.clip_z1 is not None:
            lo = s.cz - s.r - 1 if s.clip_z0 is None else s.clip_z0
            hi = s.cz + s.r + 1 if s.clip_z1 is None else s.clip_z1
            span = 2 * s.r + 2
            ball = ball.intersect(cq.Workplane("XY").box(span, span, hi - lo, centered=(True, True, False))
                                  .translate((s.cx, s.cy, lo)))
        return ball
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


def _write_cadquery(plan: ModelPlan, path: Path, name: str,
                    review: Optional[Path] = None) -> None:
    """Write through the worker process, never by importing CadQuery
    here: in the server the first import took up to 224 s (see
    step_worker.py). A worker that died is started again once. With
    ``review``, the worker also draws the review picture there."""
    req = json.dumps({"plan": plan.to_dict(), "path": str(path), "name": name,
                      "review": str(review) if review else ""}) + "\n"
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


def _overlaps(a, b) -> bool:
    return not (a.xmax < b.xmin or b.xmax < a.xmin or a.ymax < b.ymin
                or b.ymax < a.ymin or a.zmax < b.zmin or b.zmax < a.zmin)


def build_shapes(plan: ModelPlan) -> list[tuple[str, Any, tuple]]:
    """The model as ``(label, CadQuery workplane, rgb)``, one per drawn
    solid: cut solids taken out of their targets, marks cut into the face
    they sit on and filled. Raises ValueError for a mark that is not
    flush inside a top face, or a cut that touches nothing."""
    drawn: list[list] = []
    cuts: list[Solid] = []
    marks: list[Solid] = []
    for i, s in enumerate(plan.solids):
        if s.role == CUT_ROLE:
            cuts.append(s)
        elif s.kind in ("text", "dot"):
            marks.append(s)
        else:
            drawn.append([f"{s.name or s.role}_{i}", s, _cq_solid(s)])
    for c in cuts:
        cutter = _cq_solid(c)
        box = cutter.val().BoundingBox()
        hit = 0
        for entry in drawn:
            s, shape = entry[1], entry[2]
            if c.cut_from and s.name not in c.cut_from:
                continue
            if not _overlaps(box, shape.val().BoundingBox()):
                continue
            entry[2] = shape.cut(cutter)
            hit += 1
        if not hit:
            raise ValueError(f"the cut {c.name or c.kind} at {_where(box)} "
                             "touches no solid")
    for m in marks:
        mark = _cq_solid(m)
        box = mark.val().BoundingBox()
        face = None
        for entry in drawn:
            under = entry[2].val().BoundingBox()
            if abs(under.zmax - (m.z1 + m.dz)) < 1e-4 and _overlaps(box, under):
                face = under if face is None else face
                entry[2] = entry[2].cut(mark)
        label = m.text or f"dot at {m.cx:g}, {m.cy:g}"
        if face is None:
            raise ValueError(f"the mark {label!r} is not on a top face: no solid's "
                             f"top is at z {m.z1 + m.dz:g}")
        if (box.xmin < face.xmin + MARK_INSET or box.xmax > face.xmax - MARK_INSET
                or box.ymin < face.ymin + MARK_INSET or box.ymax > face.ymax - MARK_INSET):
            raise ValueError(f"the mark {label!r} ({_where(box)}) reaches past the "
                             f"face it is on ({_where(face)}); keep it "
                             f"{MARK_INSET:g} mm inside the edges")
        drawn.append([f"mark_{label}", m, mark])
    return [(label, shape, _COLOURS.get(s.role, _COLOURS["metal"]))
            for label, s, shape in drawn]


def _where(b) -> str:
    return (f"x {b.xmin:.3f} to {b.xmax:.3f}, y {b.ymin:.3f} to {b.ymax:.3f}, "
            f"z {b.zmin:.3f} to {b.zmax:.3f}")


#: Directions the review picture looks from: above and below, from the
#: front left corner.
REVIEW_VIEWS = {"top_iso": (-1.0, -1.2, 1.1), "bottom_iso": (-1.0, -1.2, -1.1)}


def render_review(shapes: list[tuple[str, Any, tuple]], png: Path,
                  size: int = 700) -> None:
    """Two views of the model side by side as a PNG, drawn offscreen
    (VTK, which CadQuery brings): from above and from below."""
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy
    import numpy as np
    from PIL import Image

    pictures = []
    for direction in REVIEW_VIEWS.values():
        ren = vtk.vtkRenderer()
        ren.SetBackground(0.86, 0.87, 0.89)
        for _label, shape, rgb in shapes:
            data = shape.val().toVtkPolyData(0.002, 0.1)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(data)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*rgb)
            actor.GetProperty().SetSpecular(0.25)
            ren.AddActor(actor)
        cam = ren.GetActiveCamera()
        cam.SetFocalPoint(0, 0, 0)
        cam.SetPosition(*direction)
        cam.SetViewUp(0, 0, 1)
        cam.ParallelProjectionOn()
        ren.ResetCamera()
        cam.Zoom(0.92)
        win = vtk.vtkRenderWindow()
        win.SetOffScreenRendering(1)
        win.SetSize(size, size)
        win.AddRenderer(ren)
        win.Render()
        grab = vtk.vtkWindowToImageFilter()
        grab.SetInput(win)
        grab.Update()
        img = grab.GetOutput()
        w, h, _ = img.GetDimensions()
        arr = vtk_to_numpy(img.GetPointData().GetScalars()).reshape(h, w, -1)[::-1, :, :3]
        pictures.append(Image.fromarray(np.ascontiguousarray(arr)))
        win.Finalize()
    sheet = Image.new("RGB", (size * len(pictures), size), (255, 255, 255))
    for k, pic in enumerate(pictures):
        sheet.paste(pic, (k * size, 0))
    png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(png))


def _write_cadquery_here(plan: ModelPlan, path: Path, name: str,
                         review: Optional[Path] = None) -> None:
    import cadquery as cq

    shapes = build_shapes(plan)
    assy = cq.Assembly(name=name)
    for label, shape, rgb in shapes:
        assy.add(shape, name=re.sub(r"[^A-Za-z0-9_]", "_", label)[:60],
                 color=cq.Color(rgb[0], rgb[1], rgb[2]))
    if hasattr(assy, "export"):      # CadQuery 2.5 on; save is deprecated
        assy.export(str(path))
    else:
        assy.save(str(path), exportType="STEP")
    if review is not None:
        render_review(shapes, review)


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


def _cadquery_only(plan: ModelPlan) -> list[str]:
    """What in ``plan`` only the CadQuery writer draws."""
    found = []
    for s in plan.solids:
        if s.role == CUT_ROLE:
            found.append("cuts")
        if s.kind in ("extrude", "text", "dot"):
            found.append(s.kind)
        if s.corner_r or s.edge_r or s.taper:
            found.append("rounded or tapered boxes")
        if s.clip_z0 is not None or s.clip_z1 is not None:
            found.append("seated balls")
        if s.rot or s.dx or s.dy or s.dz:
            found.append("placed instances")
    return sorted(set(found))


def write_step(plan: ModelPlan, path: str | Path, *, name: str = "part",
               writers: Optional[list[str]] = None,
               review: Optional[str | Path] = None) -> dict[str, Any]:
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
                _write_cadquery(plan, path, name, Path(review) if review else None)
            elif w == "freecad":
                only = _cadquery_only(plan)
                if only:
                    raise RuntimeError("the FreeCAD writer does not draw "
                                       + ", ".join(only) + "; install CadQuery")
                _write_freecad(plan, path)
            else:
                raise ValueError(f"unknown writer {w}")
            head = path.read_bytes()[:64]
            if not head.startswith(b"ISO-10303-21"):
                raise RuntimeError("the file written is not STEP")
            out = {"ok": True, "path": str(path), "writer": w, "tried": tried}
            if review and w == "cadquery" and Path(review).is_file():
                out["review"] = str(review)
            return out
        except Exception as exc:  # each writer's failure is reported
            tried.append({"writer": w, "error": f"{type(exc).__name__}: {exc}"[:300]})
    reason = ("no STEP writer on this machine: install CadQuery "
              "(pip install eda-agent[models3d]) or FreeCAD"
              if not tried else "every STEP writer failed")
    return {"ok": False, "path": "", "writer": "", "tried": tried,
            "reason": reason}
