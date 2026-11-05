# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The board as the layout engines see it: exact geometry, no guesses.

Everything a placer or router needs to make a decision that holds on the
real board lives here, and nothing is approximated at this layer:

* every pad's copper on every layer it occupies, with its real shape,
  size, corner radius and offset, plus the hole;
* every component's position, side, rotation, lock state, height and the
  outline it must keep clear (courtyard, or failing that its body);
* the true board outline with its cutouts, the copper layer stack with
  plane nets, keepouts per layer, rooms;
* the rules, typed, with their scopes, so a clearance is looked up for the
  two objects in hand rather than assumed global;
* the copper already on the board.

Coordinates are mils as floats. The Altium read converts from internal
units (10000 per mil), which float64 carries exactly at board scale.

The model is plain data with a JSON round trip, so a board read once from
Altium can be replayed offline as a fixture: engines are developed and
benchmarked without a live session, and the result is still about a real
board.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import geom

SCHEMA_VERSION = 1

TOP = "top"
BOTTOM = "bottom"


@dataclass
class PadCopper:
    """One pad's copper on one copper layer."""

    layer: str
    shape: str = "round"          # round | rect | roundrect | octagon
    w: float = 0.0
    h: float = 0.0
    corner_pct: float = 0.0       # roundrect: % of half the shorter side
    offset_x: float = 0.0         # from the hole, pad frame
    offset_y: float = 0.0


@dataclass
class Pad:
    comp: str                     # owning designator, "" for a free pad
    name: str
    x: float
    y: float
    rotation: float = 0.0
    net: str = ""
    copper: list[PadCopper] = field(default_factory=list)
    hole: float = 0.0             # drill diameter; 0 for a surface pad
    hole_type: str = "round"      # round | square | slot
    hole_width: float = 0.0       # slot length
    hole_rotation: float = 0.0
    plated: bool = True
    #: Solder mask, as on the object: tented per side, the expansion taken
    #: from the hole edge, and a hand-set (top, bottom) expansion in mils.
    #: None leaves the expansion to the board's mask rule.
    tent_top: bool = False
    tent_bottom: bool = False
    mask_from_hole: bool = False
    mask_manual: list[float] | None = None

    @property
    def key(self) -> str:
        return f"{self.comp}.{self.name}" if self.comp else f"~{self.name}@{self.x:.2f},{self.y:.2f}"

    @property
    def is_smd(self) -> bool:
        return self.hole <= 0.0

    def shape_on(self, layer: str) -> geom.Shape | None:
        for c in self.copper:
            if c.layer == layer:
                return geom.pad_shape(self.x, self.y, c.w, c.h, c.shape,
                                      self.rotation, c.corner_pct,
                                      (c.offset_x, c.offset_y))
        return None

    def layers(self) -> list[str]:
        return [c.layer for c in self.copper]


@dataclass
class Component:
    ref: str
    footprint: str = ""
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    side: str = TOP
    locked: bool = False
    height: float = 0.0
    comment: str = ""
    # The outline other parts must keep out of, in world coordinates. The
    # courtyard when the footprint has one, else the 3D body, else the
    # extent of its copper and body primitives.
    courtyard: list[tuple[float, float]] = field(default_factory=list)
    courtyard_source: str = ""    # courtyard | body | primitives | pads
    bodies: list[dict] = field(default_factory=list)  # {outline, height, standoff}


@dataclass
class Layer:
    name: str
    kind: str = "signal"          # signal | plane
    order: int = 0                # 0 = top, increasing downwards
    plane_net: str = ""
    copper_mils: float = 1.4


@dataclass
class Track:
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    net: str = ""
    comp: str = ""                # part of a footprint when set
    keepout: bool = False

    def shape(self) -> geom.Shape:
        return geom.capsule(self.x1, self.y1, self.x2, self.y2, self.width)


@dataclass
class Arc:
    layer: str
    cx: float
    cy: float
    radius: float
    a1: float
    a2: float
    width: float
    net: str = ""
    comp: str = ""
    keepout: bool = False

    def shapes(self) -> list[geom.Shape]:
        return geom.arc_track(self.cx, self.cy, self.radius, self.a1,
                              self.a2, self.width)


@dataclass
class Via:
    x: float
    y: float
    diameter: float
    hole: float
    low_layer: str = ""
    high_layer: str = ""
    net: str = ""
    # Per-layer copper diameter when the via stack is not uniform; empty
    # means ``diameter`` on every layer it spans.
    layer_diameters: dict[str, float] = field(default_factory=dict)
    comp: str = ""                # part of a footprint (thermal vias) when set
    #: Solder mask, as on Pad.
    tent_top: bool = False
    tent_bottom: bool = False
    mask_from_hole: bool = False
    mask_manual: list[float] | None = None

    def shape_on(self, layer: str) -> geom.Shape:
        return geom.circle(self.x, self.y,
                           self.layer_diameters.get(layer, self.diameter))


@dataclass
class Region:
    """Solid copper, a pour's poured copper, a cutout or a keepout area."""

    layer: str
    outline: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)
    net: str = ""
    kind: str = "copper"          # copper | pour | cutout | keepout
    keepout: bool = False
    comp: str = ""
    source: str = ""              # region | fill | polygon:<name>

    def shape(self) -> geom.Shape:
        return geom.polygon(self.outline, self.holes)


@dataclass
class Rule:
    """One design rule, typed where we understand it, raw always."""

    name: str
    kind: str                     # clearance | width | via | diffpair | ...
    scope1: str = "All"
    scope2: str = "All"
    priority: int = 1
    enabled: bool = True
    values: dict[str, Any] = field(default_factory=dict)
    descriptor: str = ""          # Altium's own text, kept for audit


@dataclass
class Room:
    name: str
    outline: list[tuple[float, float]]
    scope: str = ""
    layer: str = ""


@dataclass
class DiffPair:
    name: str
    positive: str
    negative: str


@dataclass
class LayoutBoard:
    name: str = ""
    source: str = ""                         # where it was read from
    outline: list[tuple[float, float]] = field(default_factory=list)
    cutouts: list[list[tuple[float, float]]] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    pads: list[Pad] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    net_classes: dict[str, list[str]] = field(default_factory=dict)
    diff_pairs: list[DiffPair] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    rooms: list[Room] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    schema: int = SCHEMA_VERSION

    # -- queries ------------------------------------------------------------

    def copper_layers(self) -> list[str]:
        return [l.name for l in sorted(self.layers, key=lambda l: l.order)]

    def signal_layers(self) -> list[str]:
        return [l.name for l in sorted(self.layers, key=lambda l: l.order)
                if l.kind == "signal"]

    def nets(self) -> list[str]:
        names = {p.net for p in self.pads if p.net}
        names |= {t.net for t in self.tracks if t.net}
        names |= {v.net for v in self.vias if v.net}
        return sorted(names)

    def pads_by_net(self) -> dict[str, list[Pad]]:
        out: dict[str, list[Pad]] = {}
        for p in self.pads:
            if p.net:
                out.setdefault(p.net, []).append(p)
        return out

    def component(self, ref: str) -> Component | None:
        for c in self.components:
            if c.ref == ref:
                return c
        return None

    def outline_shape(self) -> geom.Shape:
        return geom.polygon(self.outline, self.cutouts)

    def layers_between(self, low: str, high: str) -> list[str]:
        """Copper layers a via from ``low`` to ``high`` passes through."""
        order = self.copper_layers()
        if low not in order or high not in order:
            return order
        i, j = sorted((order.index(low), order.index(high)))
        return order[i:j + 1]

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LayoutBoard":
        return _build(cls, data)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        text = json.dumps(self.to_dict(), separators=(",", ":"))
        if path.suffix == ".gz":
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(text)
        else:
            path.write_text(text, encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LayoutBoard":
        path = Path(path)
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


# JSON has no tuples, so points come back as two-element lists. Nested
# dataclasses are rebuilt from their field annotations.
_NESTED = {
    "components": Component, "pads": Pad, "tracks": Track, "arcs": Arc,
    "vias": Via, "regions": Region, "rules": Rule, "rooms": Room,
    "diff_pairs": DiffPair, "layers": Layer, "copper": PadCopper,
}
_POINT_LISTS = {"outline", "courtyard"}
_RING_LISTS = {"cutouts", "holes"}


def _build(cls, data: dict):
    kwargs = {}
    names = {f.name for f in fields(cls)}
    for key, value in data.items():
        if key not in names:
            continue
        if key in _NESTED and isinstance(value, list):
            kwargs[key] = [_build(_NESTED[key], v) if isinstance(v, dict) else v
                           for v in value]
        elif key in _POINT_LISTS and isinstance(value, list):
            kwargs[key] = [tuple(p) for p in value]
        elif key in _RING_LISTS and isinstance(value, list):
            kwargs[key] = [[tuple(p) for p in ring] for ring in value]
        else:
            kwargs[key] = value
    return cls(**kwargs)


def is_layout_board(obj: Any) -> bool:
    return is_dataclass(obj) and isinstance(obj, LayoutBoard)
