# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The neutral footprint model the engine produces, in millimetres.

One :class:`FootprintModel` holds everything a backend needs to draw a
footprint: pads (with holes, paste and mask settings and paste windows),
graphics on three role layers (``silk``, ``assembly``, ``courtyard``), a
reference text position, a 3D body box, pin 1, and a record of the IPC
calculation that produced it. Coordinates are Y up with the origin at the
package centroid; layers are roles, not any one tool's layer numbers.

Everything round-trips through ``to_dict`` / ``from_dict`` as plain JSON.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Union

__all__ = [
    "Rect",
    "Pad",
    "Line",
    "Circle",
    "Arc",
    "Polygon",
    "Text",
    "Body3D",
    "FootprintModel",
    "LAYERS",
]

#: Graphic layer roles. A backend maps each to its own layer.
LAYERS = ("silk", "assembly", "courtyard")


@dataclass
class Rect:
    """An axis-aligned rectangle by centre and size (paste windows)."""

    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Pad:
    """One land. ``width`` is the X extent and ``height`` the Y extent at
    zero rotation; ``rotation`` turns it about its centre (degrees CCW).

    ``kind`` is ``smd``, ``tht`` (plated hole) or ``npth``. ``hole`` is the
    drill diameter; a slot sets ``hole_length`` (along X before rotation).
    ``hole_rotation`` turns the slot inside the pad (degrees CCW), as
    Altium's HoleRotation does, so the slot runs at ``rotation +
    hole_rotation``.
    ``paste_apertures``, when present, replace the pad's own paste with
    those windows (exposed pads); their centres are absolute.
    """

    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str = "roundrect"
    corner_ratio: float = 0.0
    rotation: float = 0.0
    kind: str = "smd"
    hole: float = 0.0
    hole_length: float = 0.0
    hole_rotation: float = 0.0
    plated: bool = True
    paste: bool = True
    paste_expansion: float = 0.0
    mask_expansion: float = 0.0
    paste_apertures: list[Rect] = field(default_factory=list)
    role: str = "signal"
    group: str = ""

    @property
    def corner_radius(self) -> float:
        if self.shape != "roundrect":
            return 0.0
        return self.corner_ratio * min(self.width, self.height)

    def bbox(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) of the copper, rotation included."""
        a = math.radians(self.rotation % 360.0)
        hw, hh = self.width / 2.0, self.height / 2.0
        if abs(math.sin(a)) < 1e-9 or abs(math.cos(a)) < 1e-9:
            if abs(math.sin(a)) > 0.5:
                hw, hh = hh, hw
            return (self.x - hw, self.y - hh, self.x + hw, self.y + hh)
        c, s = abs(math.cos(a)), abs(math.sin(a))
        ex = hw * c + hh * s
        ey = hw * s + hh * c
        return (self.x - ex, self.y - ey, self.x + ex, self.y + ey)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["paste_apertures"] = [a.to_dict() if isinstance(a, Rect) else a
                                for a in self.paste_apertures]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Pad":
        kw = {k: v for k, v in d.items() if k in {f.name for f in fields(cls)}}
        kw["paste_apertures"] = [Rect(**a) for a in d.get("paste_apertures") or []]
        return cls(**kw)


@dataclass
class Line:
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    kind: str = "line"


@dataclass
class Circle:
    layer: str
    x: float
    y: float
    r: float
    width: float
    filled: bool = False
    kind: str = "circle"


@dataclass
class Arc:
    """Centre, radius, start angle and sweep (degrees CCW, Y up)."""

    layer: str
    x: float
    y: float
    r: float
    start: float
    sweep: float
    width: float
    kind: str = "arc"


@dataclass
class Polygon:
    layer: str
    points: list[tuple[float, float]]
    width: float
    filled: bool = False
    closed: bool = True
    kind: str = "polygon"


@dataclass
class Text:
    layer: str
    text: str
    x: float
    y: float
    height: float
    thickness: float
    kind: str = "text"


Graphic = Union[Line, Circle, Arc, Polygon, Text]
_GRAPHIC_KINDS = {"line": Line, "circle": Circle, "arc": Arc,
                  "polygon": Polygon, "text": Text}


def graphic_to_dict(g: Graphic) -> dict:
    d = asdict(g)
    if isinstance(g, Polygon):
        d["points"] = [list(p) for p in g.points]
    return d


def graphic_from_dict(d: dict) -> Graphic:
    cls = _GRAPHIC_KINDS[d["kind"]]
    kw = {k: v for k, v in d.items() if k in {f.name for f in fields(cls)}}
    if cls is Polygon:
        kw["points"] = [tuple(p) for p in kw["points"]]
    return cls(**kw)


@dataclass
class Body3D:
    """The package as a box: X/Y from the maximum body, Z from the standoff
    (A1 min, or 0) to the maximum height A."""

    x0: float
    y0: float
    x1: float
    y1: float
    z0: float
    z1: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FootprintModel:
    name: str
    description: str
    family: str
    density: str
    pads: list[Pad]
    graphics: list[Graphic]
    courtyard: Polygon
    ref: Text
    body: Body3D
    pin1: tuple[float, float]
    units: str = "mm"
    origin: str = "package centroid"
    source: dict = field(default_factory=dict)
    ipc: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # -- queries ------------------------------------------------------------

    def layer(self, name: str) -> list[Graphic]:
        return [g for g in self.graphics if g.layer == name]

    def pad(self, number: str) -> Pad:
        for p in self.pads:
            if p.number == str(number):
                return p
        raise KeyError(number)

    def pads_named(self, number: str) -> list[Pad]:
        return [p for p in self.pads if p.number == str(number)]

    def copper_bbox(self) -> tuple[float, float, float, float]:
        boxes = [p.bbox() for p in self.pads]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def courtyard_bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.courtyard.points]
        ys = [p[1] for p in self.courtyard.points]
        return (min(xs), min(ys), max(xs), max(ys))

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "family": self.family,
            "density": self.density,
            "units": self.units,
            "origin": self.origin,
            "pads": [p.to_dict() for p in self.pads],
            "graphics": [graphic_to_dict(g) for g in self.graphics],
            "courtyard": graphic_to_dict(self.courtyard),
            "ref": graphic_to_dict(self.ref),
            "body": self.body.to_dict(),
            "pin1": list(self.pin1),
            "source": dict(self.source),
            "ipc": self.ipc,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FootprintModel":
        court = graphic_from_dict(d["courtyard"])
        ref = graphic_from_dict(d["ref"])
        if not isinstance(court, Polygon) or not isinstance(ref, Text):
            raise ValueError("courtyard must be a polygon and ref a text")
        return cls(
            name=d["name"], description=d["description"], family=d["family"],
            density=d["density"],
            pads=[Pad.from_dict(p) for p in d["pads"]],
            graphics=[graphic_from_dict(g) for g in d["graphics"]],
            courtyard=court, ref=ref, body=Body3D(**d["body"]),
            pin1=(float(d["pin1"][0]), float(d["pin1"][1])),
            units=d.get("units", "mm"),
            origin=d.get("origin", "package centroid"),
            source=dict(d.get("source") or {}), ipc=d.get("ipc") or {},
            warnings=list(d.get("warnings") or []))

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_json(cls, text: str) -> "FootprintModel":
        return cls.from_dict(json.loads(text))
