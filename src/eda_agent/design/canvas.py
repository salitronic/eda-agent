# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SchematicCanvas: the pure-Python representation of a schematic.

The canvas holds every visual+electrical element the executor would have
asked Altium to place: component instances, wire segments, net labels,
power ports, junctions. Layout, routing, and rendering all read/write the
canvas; only the final AltiumEmitter translates it into IPC calls.

Coordinate system:
- All distances in mils.
- Origin at lower-left of the sheet (matches Altium's SchDoc convention).
- Rotation in degrees, CCW, one of {0, 90, 180, 270}.

Pin world coords:
- A SymbolInstance applies its (x, y, rotation) to each SymbolPin's
  symbol-local (sx, sy) to produce the world endpoint. Pin orientation
  also rotates with the instance.
- This is what router stubs and net labels consume; eliminating the
  Altium round-trip for pin lookups is the main reason the canvas exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolModel,
    SymbolPin,
    pin_direction,
)


# Manhattan radius (mils) within which a power/ground net's pins share one
# rail glyph; pins farther apart each get their own GND/VCC symbol. Shared by
# the pipeline (preview canvas) and the executor (Altium apply) so the two
# agree. 300 mils: only near-coincident pins (an IC's adjacent GND pins,
# a pin pair on one column) share a glyph; anything farther gets its own
# symbol. The earlier 1000-mil radius merged a decoupling ROW (caps on a
# 400-mil column pitch) into one port with a wandering spoke trunk under
# the row -- per-cap straight drops are the universal convention there.
POWER_RAIL_CLUSTER_RADIUS_MILS = 300


# Rotation in degrees -> (cos, sin) for an integer-clean 90 deg step.
# Using ints keeps every transformed coordinate exact, no floating-point
# drift on the mil grid.
_ROT_COS_SIN: dict[int, tuple[int, int]] = {
    0: (1, 0),
    90: (0, 1),
    180: (-1, 0),
    270: (0, -1),
}


def _rotate_point(x: int, y: int, rotation: int) -> tuple[int, int]:
    """Rotate (x, y) CCW around the origin by an integer 90 deg step."""
    cos_r, sin_r = _ROT_COS_SIN[rotation % 360]
    return (x * cos_r - y * sin_r, x * sin_r + y * cos_r)


@dataclass
class PinEndpoint:
    """One pin's world-frame endpoint + outward direction.

    Returned by `SymbolInstance.pin_world`. The router and label placer
    consume this; no other geometry is needed at the canvas level.
    """

    refdes: str
    pin_id: str
    x: int
    y: int
    orientation: int  # 0=right, 1=up, 2=left, 3=down (CCW from +X)
    length: int
    electrical_type: str


@dataclass
class SymbolInstance:
    """A placed component on the canvas.

    `symbol` is shared by every refdes that uses the same lib_ref; the
    instance only adds (refdes, x, y, rotation).
    """

    refdes: str
    symbol: SymbolModel
    x: int  # world position of the symbol-local origin, mils
    y: int
    rotation: int  # 0 / 90 / 180 / 270, CCW
    sheet: str = "main"
    flipped: bool = False  # mirror across vertical axis BEFORE rotation
    value: str = ""  # component value/part-number, shown under the designator
    # Collision-free annotation anchors chosen by text_placement (canvas
    # mils, lower-left of each text line). None = renderer/emitter use
    # their default offsets.
    designator_pos: Optional[tuple[int, int]] = None
    value_pos: Optional[tuple[int, int]] = None

    def pin_world(self, pin_id: str) -> Optional[PinEndpoint]:
        """World-frame endpoint for the named pin (by designator or name).

        Returns None if the pin id doesn't exist on this symbol; callers
        must treat that as a plan/symbol mismatch and surface it.
        """
        pin = self.symbol.pin_by_id(pin_id)
        if pin is None:
            return None
        return self._pin_to_world(pin)

    def _pin_to_world(self, pin: SymbolPin) -> PinEndpoint:
        # pin.x / pin.y is the BODY-ATTACH end in symbol-local coords (the
        # SchLib convention -- see symbols._bbox_from_pins). The wire-snap
        # / electrical end is at body_attach + length * orientation_vector.
        # Routing, shorts detection, junction calc and the SVG renderer all
        # need the electrical end; expose that here.
        from eda_agent.design.symbols import pin_direction
        dx, dy = pin_direction(pin.orientation)
        local_x = pin.x + pin.length * dx
        local_y = pin.y + pin.length * dy
        # Apply flip BEFORE rotation: mirror across vertical axis so
        # x -> -x, leaving y intact. The pin's orientation also flips
        # left<->right (0 <-> 2); up/down (1, 3) stay.
        sx = -local_x if self.flipped else local_x
        sy = local_y
        new_orient = pin.orientation
        if self.flipped and new_orient in (0, 2):
            new_orient = 2 if new_orient == 0 else 0
        rx, ry = _rotate_point(sx, sy, self.rotation)
        new_orient = (new_orient + self.rotation // 90) % 4
        return PinEndpoint(
            refdes=self.refdes,
            pin_id=pin.designator,
            x=self.x + rx,
            y=self.y + ry,
            orientation=new_orient,
            length=pin.length,
            electrical_type=pin.electrical_type,
        )

    def all_pin_endpoints(self) -> list[PinEndpoint]:
        """World-frame endpoint for every pin on this instance."""
        return [self._pin_to_world(p) for p in self.symbol.pins]

    def world_bbox(self) -> SymbolBBox:
        """Rotated + translated body bbox in world coords.

        Useful for the layout's collision pass and the SVG renderer's body
        rectangle. The bbox stays axis-aligned because rotation is
        constrained to 90 deg steps.
        """
        bb = self.symbol.body_bbox
        # Mirror x first if flipped.
        if self.flipped:
            x_min, x_max = -bb.x_max, -bb.x_min
        else:
            x_min, x_max = bb.x_min, bb.x_max
        corners = [
            _rotate_point(x_min, bb.y_min, self.rotation),
            _rotate_point(x_max, bb.y_min, self.rotation),
            _rotate_point(x_min, bb.y_max, self.rotation),
            _rotate_point(x_max, bb.y_max, self.rotation),
        ]
        xs = [self.x + c[0] for c in corners]
        ys = [self.y + c[1] for c in corners]
        return SymbolBBox(
            x_min=min(xs), y_min=min(ys), x_max=max(xs), y_max=max(ys)
        )


@dataclass(frozen=True)
class WireSegment:
    """One Manhattan wire segment, world coords in mils."""

    x1: int
    y1: int
    x2: int
    y2: int
    sheet: str = "main"
    net: str = ""  # net name the segment belongs to, for debugging / SVG colouring

    def length(self) -> int:
        return abs(self.x1 - self.x2) + abs(self.y1 - self.y2)


@dataclass(frozen=True)
class NetLabel:
    """A net label dropped at a wire endpoint or pin endpoint.

    ``justification`` uses Altium's TTextJustification numbering for the
    horizontal cases used here: 0 = text extends RIGHT from the anchor
    (bottom-left justified), 2 = text ENDS at the anchor (bottom-right
    justified). The anchor (x, y) is the electrical hotspot and must sit
    on the wire/stub regardless -- justification only controls which way
    the text grows, so a label on a LEFT-facing pin can read to the left
    without its anchor leaving the wire.
    """

    text: str
    x: int
    y: int
    orientation: int  # 0=right, 1=up, 2=left, 3=down
    sheet: str = "main"
    justification: int = 0  # 0=grow right, 2=end at anchor (grow left)


@dataclass(frozen=True)
class PowerPort:
    """A power-port glyph (VCC / GND / earth / signal-gnd)."""

    text: str
    x: int
    y: int
    style: str  # "circle", "arrow", "bar", "wave", "gnd_power", "gnd_signal", "gnd_earth"
    sheet: str = "main"


@dataclass(frozen=True)
class Junction:
    """A solder-dot at a 3+ wire join."""

    x: int
    y: int
    sheet: str = "main"


@dataclass(frozen=True)
class BusSegment:
    """One Manhattan segment of a bus polyline, world coords in mils.

    A bus carries several nets at once (a data/address bus), so unlike a
    WireSegment it has no single ``net``; the member nets are identified by the
    net labels placed on the bus entries that tap it.
    """

    x1: int
    y1: int
    x2: int
    y2: int
    sheet: str = "main"

    def length(self) -> int:
        return abs(self.x1 - self.x2) + abs(self.y1 - self.y2)


@dataclass(frozen=True)
class BusEntry:
    """A 45-degree entry tapping one signal off a bus.

    Runs from the bus line to the start of a pin's wire stub; Altium draws it
    as the short diagonal between a wire and a bus. ``net`` is the signal it
    carries (for emit / debugging).
    """

    x1: int
    y1: int
    x2: int
    y2: int
    sheet: str = "main"
    net: str = ""


# Standard schematic sheet drawing areas in mils (landscape). A4 keeps
# Altium's inner-area default (11500x7600); the rest are the ISO/US series.
# A planner picks a sheet via the `size` string; the dimensions follow.
_SHEET_DIMENSIONS: dict[str, tuple[int, int]] = {
    "A4": (11500, 7600),
    # A5 is 210 x 148 mm. Absent from this table it fell back to A4,
    # which is 81% MORE area than the sheet has: five of the KiCad demo
    # sheets are A5, and their layouts were being judged against a
    # frame they were not drawn on.
    "A5": (8270, 5830),
    "A3": (16540, 11690),
    "A2": (23390, 16540),
    "A1": (33110, 23390),
    "A0": (46810, 33110),
    "A": (11000, 8500),
    "B": (17000, 11000),
    "C": (22000, 17000),
    "D": (34000, 22000),
    "E": (44000, 34000),
    # KiCad writes the US series by these names, and they are the same
    # sheets as ANSI A / A / B above: Letter is 11 x 8.5 in, Legal is
    # 14 x 8.5, Ledger (Tabloid) is 17 x 11. Without them a US-locale
    # schematic silently falls back to A4, which is the same hole A5
    # was in.
    "USLETTER": (11000, 8500),
    "USLEGAL": (14000, 8500),
    "USLEDGER": (17000, 11000),
}


def sheet_dimensions(size: str) -> tuple[int, int]:
    """Drawing-area (width, height) in mils for a sheet size string.

    Unknown sizes fall back to A4. Shared by ``Sheet`` and the layout so the
    placement area scales with the requested sheet, not a hardcoded A4.
    """
    return _SHEET_DIMENSIONS.get(str(size).upper(), (11500, 7600))


@dataclass
class Sheet:
    """One schematic sheet. ``width_mils``/``height_mils`` follow ``size``."""

    name: str
    title: str = ""
    size: str = "A4"
    # Default A4 inner area at Altium's defaults. The renderer + emitter
    # respect this for the body frame; layout treats it as a soft hint.
    width_mils: int = 11500
    height_mils: int = 7600

    def __post_init__(self) -> None:
        # Derive the drawing area from the size string so a planner asking for
        # A3/A2/A0 actually gets that frame -- previously the size was
        # decorative and every sheet stayed A4. Explicitly-set dimensions
        # (anything other than the A4 default) are preserved.
        if self.width_mils == 11500 and self.height_mils == 7600:
            self.width_mils, self.height_mils = sheet_dimensions(self.size)


@dataclass
class SchematicCanvas:
    """The complete pure-Python schematic state.

    One canvas per design; multiple sheets share the canvas. The layout
    populates `instances`; the router populates `wires` + `junctions`;
    the net-label/port placer populates `labels` + `power_ports`.

    The canvas is the single source of truth that the SVG renderer reads
    and the AltiumEmitter writes out.
    """

    sheets: list[Sheet] = field(default_factory=list)
    instances: list[SymbolInstance] = field(default_factory=list)
    wires: list[WireSegment] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    power_ports: list[PowerPort] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    buses: list[BusSegment] = field(default_factory=list)
    bus_entries: list[BusEntry] = field(default_factory=list)

    def add_sheet(self, sheet: Sheet) -> None:
        if any(s.name == sheet.name for s in self.sheets):
            raise ValueError(f"sheet {sheet.name!r} already on canvas")
        self.sheets.append(sheet)

    def add_instance(self, inst: SymbolInstance) -> None:
        if any(i.refdes == inst.refdes for i in self.instances):
            raise ValueError(f"refdes {inst.refdes!r} already placed")
        self.instances.append(inst)

    def add_wires(self, wires: Iterable[WireSegment]) -> None:
        self.wires.extend(wires)

    def add_labels(self, labels: Iterable[NetLabel]) -> None:
        self.labels.extend(labels)

    def add_power_ports(self, ports: Iterable[PowerPort]) -> None:
        self.power_ports.extend(ports)

    def add_junctions(self, junctions: Iterable[Junction]) -> None:
        self.junctions.extend(junctions)

    def add_buses(self, buses: Iterable[BusSegment]) -> None:
        self.buses.extend(buses)

    def add_bus_entries(self, entries: Iterable[BusEntry]) -> None:
        self.bus_entries.extend(entries)

    def instance_by_refdes(self, refdes: str) -> Optional[SymbolInstance]:
        for inst in self.instances:
            if inst.refdes == refdes:
                return inst
        return None

    def pin_world(self, refdes: str, pin_id: str) -> Optional[PinEndpoint]:
        """Convenience: world endpoint for a refdes/pin pair.

        Returns None if either the refdes isn't placed or the pin doesn't
        exist on its symbol. Callers should treat that as a plan/symbol
        mismatch (not a transient Altium failure).
        """
        inst = self.instance_by_refdes(refdes)
        if inst is None:
            return None
        return inst.pin_world(pin_id)

    def instances_on(self, sheet: str) -> list[SymbolInstance]:
        return [i for i in self.instances if i.sheet == sheet]

    def wires_on(self, sheet: str) -> list[WireSegment]:
        return [w for w in self.wires if w.sheet == sheet]

    def labels_on(self, sheet: str) -> list[NetLabel]:
        return [l for l in self.labels if l.sheet == sheet]

    def power_ports_on(self, sheet: str) -> list[PowerPort]:
        return [p for p in self.power_ports if p.sheet == sheet]

    def junctions_on(self, sheet: str) -> list[Junction]:
        return [j for j in self.junctions if j.sheet == sheet]

    def buses_on(self, sheet: str) -> list[BusSegment]:
        return [b for b in self.buses if b.sheet == sheet]

    def bus_entries_on(self, sheet: str) -> list[BusEntry]:
        return [e for e in self.bus_entries if e.sheet == sheet]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot.

        Symbols are serialized by reference (lib_path + lib_ref) rather
        than inlined, so a canvas snapshot is small enough to commit as a
        regression fixture without dragging in 100 pin definitions per
        IC. The deserializer needs a SymbolCache to re-hydrate.
        """
        return {
            "sheets": [
                {
                    "name": s.name,
                    "title": s.title,
                    "size": s.size,
                    "width_mils": s.width_mils,
                    "height_mils": s.height_mils,
                }
                for s in self.sheets
            ],
            "instances": [
                {
                    "refdes": i.refdes,
                    "lib_path": i.symbol.lib_path,
                    "lib_ref": i.symbol.lib_ref,
                    "x": i.x,
                    "y": i.y,
                    "rotation": i.rotation,
                    "sheet": i.sheet,
                }
                for i in self.instances
            ],
            "wires": [
                {"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2,
                 "sheet": w.sheet, "net": w.net}
                for w in self.wires
            ],
            "labels": [
                {"text": l.text, "x": l.x, "y": l.y,
                 "orientation": l.orientation, "sheet": l.sheet}
                for l in self.labels
            ],
            "power_ports": [
                {"text": p.text, "x": p.x, "y": p.y,
                 "style": p.style, "sheet": p.sheet}
                for p in self.power_ports
            ],
            "junctions": [
                {"x": j.x, "y": j.y, "sheet": j.sheet}
                for j in self.junctions
            ],
            "buses": [
                {"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2,
                 "sheet": b.sheet}
                for b in self.buses
            ],
            "bus_entries": [
                {"x1": e.x1, "y1": e.y1, "x2": e.x2, "y2": e.y2,
                 "sheet": e.sheet, "net": e.net}
                for e in self.bus_entries
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def pin_direction_world(orientation: int) -> tuple[int, int]:
    """Outward (dx, dy) unit vector for a world-frame pin orientation.

    Same convention as symbols.pin_direction but re-exported here so the
    router/renderer can stay canvas-only without reaching into symbols.
    """
    return pin_direction(orientation)
