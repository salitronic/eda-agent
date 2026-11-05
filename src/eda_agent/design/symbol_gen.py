# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Parametric IC schematic-symbol geometry (pure Python, offline).

The schematic-side analog of :mod:`footprint_gen`. Given pins already grouped
onto the LEFT and RIGHT sides (the functional grouping a human/LLM decides per
the design discipline -- inputs left, outputs right), this lays them out into a
clean, grid-aligned symbol: a body rectangle plus every pin's exact
``Location`` / ``Orientation`` / wire-connection. A tool then emits it via
``lib_create_symbol`` + ``lib_add_pins`` (bulk) + ``lib_add_symbol_rectangle``.

It follows the discipline's symbol rules exactly:

* Pins go ONLY on the left and right (never top/bottom).
* Origin: the TOP-LEFTMOST pin's WIRE-CONNECTION point is at (0, 0). With the
  standard 200-mil pin length and a left pin pointing left (orientation 2), its
  Location is (200, 0) so the wire snaps at (0, 0); the top-right pin's Location
  is (body_width, 0), wire at (body_width + 200, 0).
* Everything on the 100-mil grid; pins step DOWN by 100 mils.

The caller supplies the side groupings; this is purely the geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

PIN_LENGTH = 200
PIN_PITCH = 100
GRID = 100
_CHAR_W = 70          # ~mils per pin-name character, for inner-width sizing
_NAME_GAP = 300       # clear gap between the two name columns
_MIN_INNER = 600      # minimum inner body width (SOIC-8-ish)


@dataclass(frozen=True)
class SymbolGeometry:
    """Computed IC symbol: pins + body rectangle, ready to emit."""

    pins: tuple[dict, ...]        # {designator,name,x,y,length,rotation,
                                  #  electrical_type}
    body: dict                    # {x1,y1,x2,y2}
    width_mils: int
    height_mils: int


def _snap(v: float) -> int:
    return int(round(v / GRID) * GRID)


def generate_ic_symbol(
    left_pins: list,
    right_pins: list,
    *,
    min_inner_width: int = _MIN_INNER,
    pin_length: int = PIN_LENGTH,
    pin_pitch: int = PIN_PITCH,
    char_width: int = _CHAR_W,
    name_gap: int = _NAME_GAP,
) -> SymbolGeometry:
    """Lay out an IC symbol from left/right pin groups.

    Each pin dict needs ``designator`` and ``name``; ``electrical_type``
    (default "passive") and ``hidden`` are passed through. ``None`` in a
    side's list keeps that row free, which spaces pin groups apart. Left
    pins are placed top->down
    starting at the origin row; right pins likewise on the far side. The
    body width is sized to clear the pin-name text on both sides.
    ``pin_length`` and ``pin_pitch`` stay on the 100-mil grid.

    Returns a :class:`SymbolGeometry`. Raises if both sides are empty.
    """
    for label, value in (("pin_length", pin_length), ("pin_pitch", pin_pitch)):
        if value <= 0 or value % GRID:
            raise ValueError(f"{label} must be a positive multiple of {GRID} mil")
    if char_width <= 0 or name_gap < 0:
        raise ValueError("char_width must be above zero and name_gap 0 or more")
    real = [p for p in (*left_pins, *right_pins) if p is not None]
    if not real:
        raise ValueError("need at least one pin on a side")

    # Every pin needs a designator, and designators must be unique across
    # the whole symbol -- a missing or duplicated pin number makes an
    # invalid component that ERC and the netlist would mis-resolve.
    designators: list[str] = []
    for p in real:
        if not isinstance(p, dict):
            raise ValueError(f"a pin is a dict, or None for a gap row (got {p!r})")
        d = str(p.get("designator", "")).strip()
        if not d:
            raise ValueError(
                f"every pin needs a non-empty 'designator' (offending pin: "
                f"{p!r})")
        designators.append(d)
    dupes = sorted({d for d in designators if designators.count(d) > 1})
    if dupes:
        raise ValueError(f"duplicate pin designator(s): {dupes}")

    max_l_name = max((len(str(p.get("name", ""))) for p in left_pins
                      if p is not None), default=0)
    max_r_name = max((len(str(p.get("name", ""))) for p in right_pins
                      if p is not None), default=0)
    inner = max(min_inner_width,
                _snap((max_l_name + max_r_name) * char_width + name_gap))
    # The body spans from the end of the left stubs to the start of the right
    # ones; a left pin points left (rotation 180, orientation 2), so its wire
    # end sits at x=0, and a right pin points right (rotation 0).
    edges = {180: pin_length, 0: pin_length + inner}
    pins: list[dict] = []
    for rotation, column in ((180, left_pins), (0, right_pins)):
        for row, p in enumerate(column):
            if p is None:
                continue
            pins.append({
                "designator": str(p.get("designator", "")),
                "name": str(p.get("name", "")),
                "x": edges[rotation], "y": -row * pin_pitch,
                "length": pin_length, "rotation": rotation,
                "electrical_type": p.get("electrical_type", "passive"),
                "hidden": bool(p.get("hidden", False))})

    # One pitch of margin above the first row and below the last.
    rows = max(len(left_pins), len(right_pins))
    body_bottom = -rows * pin_pitch
    body = {"x1": edges[180], "y1": pin_pitch, "x2": edges[0], "y2": body_bottom}
    width = edges[0] + pin_length
    height = pin_pitch - body_bottom
    return SymbolGeometry(pins=tuple(pins), body=body,
                          width_mils=width, height_mils=height)


@dataclass(frozen=True)
class PassiveSymbol:
    """A 2-pin passive symbol: pins + glyph primitives, ready to emit.

    Horizontal layout, pin 1 wire-end at the origin (0,0) pointing left,
    pin 2 wire-end at (400,0) pointing right, body in x in [100, 300]."""

    pins: tuple[dict, ...]
    lines: tuple[dict, ...]        # {x1,y1,x2,y2}
    rectangles: tuple[dict, ...]   # {x1,y1,x2,y2}
    polygons: tuple[dict, ...]     # {points: [(x,y),...]}
    arcs: tuple[dict, ...] = ()    # {x,y,radius,start,end} degrees


# Standard 2-pin horizontal frame (rule-14 origin at pin-1 wire-end).
_BODY_L = 100
_BODY_R = 300
#: Half height of the resistor and fuse bodies, mil: 80 mil tall over a
#: 200 mil length.
_SLIM_HALF = 40


def _two_pins(et1="passive", et2="passive"):
    # A discrete shows neither pin name nor number: left visible, the
    # names "1" and "2" print inside the glyph, across a capacitor's plates.
    hidden = {"show_name": False, "show_designator": False}
    return (
        {"designator": "1", "name": "1", "x": _BODY_L, "y": 0,
         "length": _BODY_L, "rotation": 180, "electrical_type": et1, **hidden},
        {"designator": "2", "name": "2", "x": _BODY_R, "y": 0,
         "length": _BODY_L, "rotation": 0, "electrical_type": et2, **hidden},
    )


def generate_passive_symbol(kind: str) -> PassiveSymbol:
    """Geometry for a standard 2-pin passive glyph.

    ``kind``: "resistor"/"r" | "capacitor"/"c" |
    "polarized_capacitor"/"cap_pol" | "inductor"/"l" | "diode"/"d" |
    "led" | "crystal"/"xtal" | "fuse"/"f". Uses the IEC rectangle for R/L,
    two plates for C (plus a '+' marker for the polarized variant), a
    triangle+bar for D, the diode plus emission arrows for the LED, two
    plates around the resonator for a crystal, and a rectangle with a
    through-lead for a fuse. The LED, crystal, and polarized-cap glyphs
    carry sub-100-mil detail, so emit their lines/polygons with
    ``grid<=25``. Emit via ``lib_add_pins`` +
    ``lib_add_symbol_rectangle``/``lib_add_symbol_lines``/
    ``lib_add_symbol_polygon``.
    """
    k = kind.strip().lower()
    pins = _two_pins()
    lines: list[dict] = []
    rects: list[dict] = []
    polys: list[dict] = []
    arcs: list[dict] = []

    # All glyph features sit on the 100-mil grid: the symbol primitive tools
    # snap to 100, so sub-100 detail (e.g. plates 40 mils apart) would
    # collapse. Body spans x in [100, 300] (the pin body-attach points),
    # y in [-100, 100].
    if k in ("resistor", "r", "res"):
        # A long, narrow body (200 x 80 mil, 2.5:1) reads as a resistor; the
        # full 200 x 200 square it used to be reads as a box.
        rects.append({"x1": _BODY_L, "y1": -_SLIM_HALF, "x2": _BODY_R, "y2": _SLIM_HALF})
    elif k in ("inductor", "l", "ind"):
        # Four half-turn humps across the body, the conventional coil.
        for cx in (125, 175, 225, 275):
            arcs.append({"x": cx, "y": 0, "radius": 25, "start": 0, "end": 180})
    elif k in ("capacitor", "c", "cap"):
        # Two plates a narrow gap apart in the middle, leads in from the
        # pins. Plates at the body edges read as two unrelated bars.
        lines.append({"x1": _BODY_L, "y1": 0, "x2": 175, "y2": 0})
        lines.append({"x1": 225, "y1": 0, "x2": _BODY_R, "y2": 0})
        lines.append({"x1": 175, "y1": -100, "x2": 175, "y2": 100})
        lines.append({"x1": 225, "y1": -100, "x2": 225, "y2": 100})
    elif k in ("polarized_capacitor", "cap_pol", "electrolytic", "cp"):
        # Same plates plus a '+' by the positive (pin-1) plate -- the
        # unambiguous polarity marker for an electrolytic / tantalum.
        lines.append({"x1": _BODY_L, "y1": 0, "x2": 175, "y2": 0})
        lines.append({"x1": 225, "y1": 0, "x2": _BODY_R, "y2": 0})
        lines.append({"x1": 175, "y1": -100, "x2": 175, "y2": 100})
        lines.append({"x1": 225, "y1": -100, "x2": 225, "y2": 100})
        lines.append({"x1": 115, "y1": 75, "x2": 155, "y2": 75})   # + horiz
        lines.append({"x1": 135, "y1": 55, "x2": 135, "y2": 95})   # + vert
    elif k in ("diode", "d"):
        # Triangle (anode at the base, apex pointing right) + cathode bar.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (_BODY_R, 0)]})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
    elif k in ("led", "diode_led"):
        # Diode + two emission arrows pointing away (up-right) -- the LED.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (_BODY_R, 0)]})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
        for x0 in (175, 225):                    # two parallel arrow shafts
            lines.append({"x1": x0, "y1": 125, "x2": x0 + 75, "y2": 200})
            # arrowhead barbs at the tip
            lines.append({"x1": x0 + 75, "y1": 200, "x2": x0 + 50, "y2": 200})
            lines.append({"x1": x0 + 75, "y1": 200, "x2": x0 + 75, "y2": 175})
    elif k in ("crystal", "xtal", "y"):
        # Two plates (the electrodes) close around the resonator.
        lines.append({"x1": _BODY_L, "y1": 0, "x2": 150, "y2": 0})
        lines.append({"x1": 250, "y1": 0, "x2": _BODY_R, "y2": 0})
        lines.append({"x1": 150, "y1": -100, "x2": 150, "y2": 100})
        lines.append({"x1": 250, "y1": -100, "x2": 250, "y2": 100})
        rects.append({"x1": 175, "y1": -75, "x2": 225, "y2": 75})
    elif k in ("fuse", "f"):
        # IEC fuse: a rectangle with the lead passing straight through.
        rects.append({"x1": _BODY_L, "y1": -_SLIM_HALF, "x2": _BODY_R, "y2": _SLIM_HALF})
        lines.append({"x1": _BODY_L, "y1": 0, "x2": _BODY_R, "y2": 0})
    elif k in ("zener", "z", "tvs", "tvs_uni"):
        # Diode with the cathode bar's ends hooked in opposite directions.
        # A unidirectional TVS is drawn the same way.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (_BODY_R, 0)]})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
        lines.append({"x1": _BODY_R, "y1": 100, "x2": _BODY_R - 25, "y2": 100})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R + 25, "y2": -100})
    elif k in ("schottky", "sd"):
        # Diode with an S-shaped cathode bar.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (_BODY_R, 0)]})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
        lines.append({"x1": _BODY_R, "y1": 100, "x2": _BODY_R - 25, "y2": 100})
        lines.append({"x1": _BODY_R - 25, "y1": 100, "x2": _BODY_R - 25, "y2": 75})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R + 25, "y2": -100})
        lines.append({"x1": _BODY_R + 25, "y1": -100, "x2": _BODY_R + 25, "y2": -75})
    elif k in ("tvs_bidirectional", "tvs_bi", "bidirectional_tvs"):
        # Two diodes tip to tip on a shared, hooked bar: conducts both ways.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (200, 0)]})
        polys.append({"points": [(_BODY_R, -100), (_BODY_R, 100), (200, 0)]})
        lines.append({"x1": 200, "y1": -100, "x2": 200, "y2": 100})
        lines.append({"x1": 200, "y1": 100, "x2": 175, "y2": 100})
        lines.append({"x1": 200, "y1": -100, "x2": 225, "y2": -100})
    elif k in ("switch", "spst", "sw"):
        # Open single-throw contact: a blade lifted off the right contact.
        lines.append({"x1": _BODY_L, "y1": 0, "x2": 275, "y2": 75})
        lines.append({"x1": _BODY_R, "y1": 0, "x2": _BODY_R, "y2": 25})
    elif k in ("push_button", "pushbutton", "button", "tactile"):
        # Normally-open push button: contacts, a bridging bar above, plunger.
        lines.append({"x1": _BODY_L, "y1": 0, "x2": _BODY_L, "y2": 25})
        lines.append({"x1": _BODY_R, "y1": 0, "x2": _BODY_R, "y2": 25})
        lines.append({"x1": 75, "y1": 75, "x2": 325, "y2": 75})
        lines.append({"x1": 200, "y1": 75, "x2": 200, "y2": 150})
        lines.append({"x1": 150, "y1": 150, "x2": 250, "y2": 150})
    elif k in _TRANSISTOR_KINDS:
        return _transistor_symbol(_TRANSISTOR_KINDS[k])
    else:
        raise ValueError(
            f"unknown passive kind {kind!r}; use one of: "
            f"{', '.join(PASSIVE_KINDS)}")

    return PassiveSymbol(pins=pins, lines=tuple(lines),
                         rectangles=tuple(rects), polygons=tuple(polys),
                         arcs=tuple(arcs))


#: Canonical kinds, for messages and docs. Aliases are accepted too.
PASSIVE_KINDS = (
    "resistor", "capacitor", "polarized_capacitor", "inductor", "diode",
    "led", "crystal", "fuse", "zener", "schottky", "tvs",
    "tvs_bidirectional", "switch", "push_button", "nmos", "pmos", "npn",
    "pnp",
)

_TRANSISTOR_KINDS = {
    "nmos": "nmos", "nfet": "nmos", "n_mosfet": "nmos", "mosfet_n": "nmos",
    "pmos": "pmos", "pfet": "pmos", "p_mosfet": "pmos", "mosfet_p": "pmos",
    "npn": "npn", "bjt_npn": "npn",
    "pnp": "pnp", "bjt_pnp": "pnp",
}

#: Default pin numbers by role, the common SOT-23 assignment: a MOSFET is
#: 1=G 2=S 3=D and a BJT 1=B 2=E 3=C. Check the datasheet; the caller can
#: override with ``pin_map``.
TRANSISTOR_DEFAULT_PINS = {
    "nmos": {"G": "1", "S": "2", "D": "3"},
    "pmos": {"G": "1", "S": "2", "D": "3"},
    "npn": {"B": "1", "E": "2", "C": "3"},
    "pnp": {"B": "1", "E": "2", "C": "3"},
}


def _arrowhead(tip: tuple[float, float], tail: tuple[float, float],
               length: float = 40, half_width: float = 18) -> dict:
    """A filled arrowhead at ``tip`` pointing away from ``tail``."""
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / n, dy / n
    bx, by = tip[0] - ux * length, tip[1] - uy * length
    px, py = -uy * half_width, ux * half_width
    pts = [tip, (bx + px, by + py), (bx - px, by - py)]
    return {"points": [(int(round(x)), int(round(y))) for x, y in pts]}


def _transistor_symbol(kind: str,
                       pin_map: dict[str, str] | None = None) -> PassiveSymbol:
    """MOSFET or BJT: control pin on the left, the other two top and bottom.

    The control pin's wire end is the origin (rule 14). Top and bottom pins
    leave at x=300 and land their wire ends on the 100-mil grid at
    (300, +/-300). Glyph detail is finer than the pin grid, so emit lines
    and polygons without snapping them to 100.
    """
    roles = dict(TRANSISTOR_DEFAULT_PINS[kind])
    roles.update({k.upper(): str(v) for k, v in (pin_map or {}).items()})
    lines: list[dict] = []
    polys: list[dict] = []

    def pin(role, x, y, rotation):
        return {"designator": roles[role], "name": role, "x": x, "y": y,
                "length": 200, "rotation": rotation,
                "electrical_type": "passive", "show_name": False}

    if kind in ("nmos", "pmos"):
        # Drain on top, source below, gate on the left (an N device's drain
        # is the higher potential; a P device conventionally flips that,
        # so its source is on top).
        top, bottom = ("D", "S") if kind == "nmos" else ("S", "D")
        pins = (pin("G", 200, 0, 180), pin(top, 300, 100, 90),
                pin(bottom, 300, -100, 270))
        lines.append({"x1": 200, "y1": -100, "x2": 200, "y2": 100})   # gate
        for y1, y2 in ((50, 100), (-25, 25), (-100, -50)):             # channel
            lines.append({"x1": 250, "y1": y1, "x2": 250, "y2": y2})
        lines.append({"x1": 250, "y1": 75, "x2": 300, "y2": 75})
        lines.append({"x1": 300, "y1": 75, "x2": 300, "y2": 100})
        lines.append({"x1": 250, "y1": -75, "x2": 300, "y2": -75})
        lines.append({"x1": 300, "y1": -75, "x2": 300, "y2": -100})
        # Body tied to the source, with the arrow on the body connection:
        # into the channel for N, out of it for P.
        src_y = -75 if kind == "nmos" else 75
        lines.append({"x1": 250, "y1": 0, "x2": 300, "y2": 0})
        lines.append({"x1": 300, "y1": 0, "x2": 300, "y2": src_y})
        if kind == "nmos":
            polys.append(_arrowhead((252, 0), (300, 0)))
        else:
            polys.append(_arrowhead((298, 0), (250, 0)))
    else:
        # Collector on top for NPN, emitter on top for PNP; arrow on the
        # emitter, out of the base for NPN, into it for PNP.
        top, bottom = ("C", "E") if kind == "npn" else ("E", "C")
        pins = (pin("B", 200, 0, 180), pin(top, 300, 100, 90),
                pin(bottom, 300, -100, 270))
        lines.append({"x1": 200, "y1": -100, "x2": 200, "y2": 100})   # base
        lines.append({"x1": 200, "y1": 50, "x2": 300, "y2": 100})
        lines.append({"x1": 200, "y1": -50, "x2": 300, "y2": -100})
        if kind == "npn":
            polys.append(_arrowhead((300, -100), (200, -50)))
        else:
            polys.append(_arrowhead((210, 55), (300, 100)))
    return PassiveSymbol(pins=pins, lines=tuple(lines), rectangles=(),
                         polygons=tuple(polys))


def generate_transistor_symbol(kind: str,
                               pin_map: dict[str, str] | None = None
                               ) -> PassiveSymbol:
    """Public entry for a MOSFET/BJT with an optional role->pin override."""
    k = _TRANSISTOR_KINDS.get(kind.strip().lower())
    if k is None:
        raise ValueError(f"unknown transistor kind {kind!r}; use "
                         f"nmos/pmos/npn/pnp")
    bad = set(k_.upper() for k_ in (pin_map or {})) - set(
        TRANSISTOR_DEFAULT_PINS[k])
    if bad:
        raise ValueError(f"pin_map roles for {k} are "
                         f"{sorted(TRANSISTOR_DEFAULT_PINS[k])}, not "
                         f"{sorted(bad)}")
    return _transistor_symbol(k, pin_map)


# ---------------------------------------------------------------------------
# Amplifiers: the triangle
# ---------------------------------------------------------------------------

import re as _re

_AMP_WORDS = _re.compile(
    r"op[\s\-_]?amp|operational amplifier|comparator|current[\s\-_]?sense|"
    r"current[\s\-_]?shunt|instrumentation amplifier|difference amplifier|"
    r"differential amplifier|in[\s\-_]?amp\b|amplifier", _re.I)

# Role patterns are matched against the pin NAME with an optional unit
# letter or number (A/B/C/D or 1-4) before or after, e.g. "+INA", "INA+",
# "IN1-", "OUTB", "VOUT_A".
_IN_POS = _re.compile(r"^(?:\+IN|IN\+|INP|IN_?P|VIN\+|\+VIN|NONINV|NI|\+)$",
                      _re.I)
_IN_NEG = _re.compile(r"^(?:-IN|IN-|INN|IN_?N|VIN-|-VIN|INV|-)$", _re.I)
_OUT = _re.compile(r"^(?:V?OUT|VO|O|OUTPUT)$", _re.I)
_SUPPLY_POS = _re.compile(
    r"^(?:V\+|\+V|VCC\+?|VDD|AVDD|VS\+?|\+VS|VPOS|V_?POS|VCC|VS|PWR)$", _re.I)
_SUPPLY_NEG = _re.compile(
    r"^(?:V-|-V|VEE|VSS|AVSS|VS-|-VS|VNEG|V_?NEG|GND|AGND|DGND|PGND)$", _re.I)
_UNIT = _re.compile(r"^(?P<a>[A-D1-4])?(?P<core>.+?)(?P<b>[A-D1-4])?$", _re.I)


def _norm(name: str) -> str:
    return str(name).strip().replace(" ", "").replace("_", "").upper()


def _role(name: str) -> tuple[str | None, str]:
    """(role, unit) for one pin name; role None when it is not an amp pin."""
    n = _norm(name)
    for pattern, role in ((_SUPPLY_POS, "v+"), (_SUPPLY_NEG, "v-")):
        if pattern.match(n):
            return role, ""
    for pattern, role in ((_IN_POS, "in+"), (_IN_NEG, "in-"), (_OUT, "out")):
        if pattern.match(n):
            return role, ""
    # Unit suffix or prefix: OUTA, INA+, +INA, IN1-, OUT2, A_OUT ...
    for core, unit in ((n[:-1], n[-1]), (n[1:], n[0])):
        if unit in "ABCD1234" and core:
            core = core.rstrip("_")
            for pattern, role in ((_IN_POS, "in+"), (_IN_NEG, "in-"),
                                  (_OUT, "out")):
                if pattern.match(core):
                    return role, unit
    # "+INA" style: sign first, unit last.
    m = _re.match(r"^([+-])IN([A-D1-4])$", n)
    if m:
        return ("in+" if m.group(1) == "+" else "in-"), m.group(2)
    m = _re.match(r"^IN([A-D1-4])([+-])$", n)
    if m:
        return ("in+" if m.group(2) == "+" else "in-"), m.group(1)
    return None, ""


def classify_amplifier_pins(pins: list[dict]) -> dict | None:
    """Group pins into amplifier units, or None when they do not form one.

    Returns ``{"units": [{"unit", "in+", "in-", "out", "extra": [...]}],
    "v+": [...], "v-": [...], "extra": [...]}``. A unit needs both inputs
    and an output. Pins that fit no role go to ``extra``: on a single
    amplifier they join the left side under the inputs; on a multi-unit
    part they go on the power part.
    """
    units: dict[str, dict] = {}
    supply_pos: list[dict] = []
    supply_neg: list[dict] = []
    extra: list[dict] = []
    for p in pins:
        role, unit = _role(p.get("name", ""))
        if role == "v+":
            supply_pos.append(p)
        elif role == "v-":
            supply_neg.append(p)
        elif role in ("in+", "in-", "out"):
            u = units.setdefault(unit, {"unit": unit})
            if role in u:
                extra.append(p)          # a second OUT etc. is not a unit role
            else:
                u[role] = p
        else:
            extra.append(p)
    whole = [u for u in units.values()
             if all(r in u for r in ("in+", "in-", "out"))]
    if not whole:
        return None
    for u in units.values():
        if u not in whole:
            extra.extend(u[r] for r in ("in+", "in-", "out") if r in u)
    whole.sort(key=lambda u: u["unit"])
    for u in whole:
        u["extra"] = []
    return {"units": whole, "v+": supply_pos, "v-": supply_neg,
            "extra": extra}


def looks_like_amplifier(pins: list[dict], description: str = "") -> bool:
    """True when the description names an amplifier/comparator, or when the
    pin names form at least one complete IN+/IN-/OUT unit."""
    if description and _AMP_WORDS.search(description):
        return classify_amplifier_pins(pins) is not None
    return classify_amplifier_pins(pins) is not None


@dataclass(frozen=True)
class AmplifierSymbol:
    """Triangle amplifier symbol, possibly multi-part.

    ``parts`` holds one entry per Altium part, each with ``polygons``,
    ``rectangles``, ``lines``, ``texts`` and ``pins``, all carrying
    ``owner_part_id``."""

    parts: tuple[dict, ...]
    part_count: int
    body_fill: int = 8454143


#: Altium's standard light-yellow functional-block fill (discipline rule 17).
BODY_FILL = 8454143


_ROLE_TYPES = {"in+": "input", "in-": "input", "out": "output",
               "v+": "power", "v-": "power"}


def _pin(p: dict, x: int, y: int, rotation: int, part: int,
         show_name: bool) -> dict:
    # The caller's electrical type wins; otherwise the role decides it, so
    # ERC sees inputs, an output and power pins rather than all passive.
    etype = p.get("electrical_type")
    if not etype:
        etype = _ROLE_TYPES.get(_role(p.get("name", ""))[0] or "", "passive")
    return {"designator": str(p.get("designator", "")),
            "name": str(p.get("name", "")), "x": x, "y": y, "length": 200,
            "rotation": rotation, "electrical_type": etype,
            "owner_part_id": part, "show_name": show_name}


def _triangle_part(unit: dict, part: int, supply_pos: list[dict],
                   supply_neg: list[dict], extra_left: list[dict],
                   invert_on_top: bool) -> dict:
    """One triangle: inputs left, output at the apex, supplies top/bottom.

    Geometry, all pins on the 100-mil grid with the top-left input's wire
    end at the origin (rule 14):

    * inputs (and any extra left pins) at y = 0, -200, -400 ...
      pointing left from x=200;
    * the base of the triangle on x=200 from y=+200 down to one pitch
      below the last left pin; the apex at the base's mid-height, as far
      right as the base is tall;
    * the output leaves the apex pointing right;
    * supply pins leave the slanted edges straight up and down at
      x = 400, 600 ..., where the edge crosses the grid exactly.
    """
    first, second = ((unit["in-"], unit["in+"]) if invert_on_top
                     else (unit["in+"], unit["in-"]))
    left = [first, second, *extra_left]
    y_top = 200
    y_bot = -(len(left) - 1) * 200 - 200
    height = y_top - y_bot
    yc = (y_top + y_bot) // 2
    apex_x = 200 + height
    pins = []
    for i, p in enumerate(left):
        is_input = p is unit["in+"] or p is unit["in-"]
        pins.append(_pin(p, 200, -i * 200, 180, part, show_name=not is_input))
    pins.append(_pin(unit["out"], apex_x, yc, 0, part, show_name=False))

    # Slanted edges: y = y_top - (x-200)/2 on top, y_bot + (x-200)/2 below.
    slots = [x for x in range(400, apex_x - 100, 200)]
    if len(supply_pos) > len(slots) or len(supply_neg) > len(slots):
        raise ValueError(
            f"{max(len(supply_pos), len(supply_neg))} supply pins on one "
            f"edge do not fit a triangle this size ({len(slots)} places)")
    for x, p in zip(slots, supply_pos):
        pins.append(_pin(p, x, y_top - (x - 200) // 2, 90, part,
                         show_name=True))
    for x, p in zip(slots, supply_neg):
        pins.append(_pin(p, x, y_bot + (x - 200) // 2, 270, part,
                         show_name=True))

    # "+" and "-" just inside the base, beside each input.
    texts = []
    for i, p in enumerate((first, second)):
        texts.append({"text": "+" if p is unit["in+"] else "-",
                      "x": 230, "y": -i * 200 - 40, "font_size": 12,
                      "owner_part_id": part})
    return {
        "part": part,
        "polygons": [{"points": [(200, y_top), (200, y_bot), (apex_x, yc)],
                      "fill_color": BODY_FILL, "owner_part_id": part}],
        "rectangles": [], "lines": [], "texts": texts, "pins": pins,
    }


def _power_part(part: int, supply_pos: list[dict], supply_neg: list[dict],
                extra: list[dict]) -> dict:
    """Power part of a multi-unit amplifier: a small block, supplies leaving
    the top and bottom, anything else on the left. The first supply's wire
    end is at the origin."""
    n = max(len(supply_pos), len(supply_neg), 1)
    width = 200 * n
    pins = []
    for i, p in enumerate(supply_pos):
        pins.append(_pin(p, i * 200, -200, 90, part, show_name=True))
    body_bot = -600 - 200 * max(len(extra) - 1, 0)
    for i, p in enumerate(supply_neg):
        pins.append(_pin(p, i * 200, body_bot, 270, part, show_name=True))
    for i, p in enumerate(extra):
        pins.append(_pin(p, -100, -400 - i * 200, 180, part, show_name=True))
    rect = {"x1": -100, "y1": -200, "x2": width - 100, "y2": body_bot,
            "fill_color": BODY_FILL, "owner_part_id": part}
    return {"part": part, "polygons": [], "rectangles": [rect], "lines": [],
            "texts": [], "pins": pins}


def generate_amplifier_symbol(pins: list[dict], *,
                              invert_on_top: bool = False
                              ) -> AmplifierSymbol:
    """Triangle symbol for an op-amp, comparator, current-sense or
    instrumentation amplifier, from its pins (``designator`` + ``name``).

    One unit: a single triangle carries every pin; supplies on the slanted
    edges, anything else on the left under the inputs. Several units
    (dual/quad): one triangle per part, plus a last part holding the
    supplies and any shared pins. ``invert_on_top`` puts IN- above IN+.

    Raises ValueError when the names do not give a complete IN+/IN-/OUT
    unit, so the caller can fall back to a rectangle.
    """
    groups = classify_amplifier_pins(pins)
    if groups is None:
        raise ValueError(
            "no complete amplifier unit (IN+, IN- and OUT) in these pin names")
    designators = [str(p.get("designator", "")).strip() for p in pins]
    if not all(designators):
        raise ValueError("every pin needs a non-empty 'designator'")
    dupes = sorted({d for d in designators if designators.count(d) > 1})
    if dupes:
        raise ValueError(f"duplicate pin designator(s): {dupes}")
    units = groups["units"]
    if len(units) == 1:
        part = _triangle_part(units[0], 1, groups["v+"], groups["v-"],
                              groups["extra"], invert_on_top)
        return AmplifierSymbol(parts=(part,), part_count=1)
    parts = [_triangle_part(u, i + 1, [], [], [], invert_on_top)
             for i, u in enumerate(units)]
    if groups["v+"] or groups["v-"] or groups["extra"]:
        parts.append(_power_part(len(units) + 1, groups["v+"], groups["v-"],
                                 groups["extra"]))
    return AmplifierSymbol(parts=tuple(parts), part_count=len(parts))


__all__ = ["SymbolGeometry", "generate_ic_symbol",
           "PassiveSymbol", "generate_passive_symbol", "PASSIVE_KINDS",
           "generate_transistor_symbol", "TRANSISTOR_DEFAULT_PINS",
           "AmplifierSymbol", "generate_amplifier_symbol",
           "classify_amplifier_pins", "looks_like_amplifier", "BODY_FILL",
           "PIN_LENGTH", "PIN_PITCH"]
