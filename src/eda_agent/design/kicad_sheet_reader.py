# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read placement out of a human-drawn ``.kicad_sch``.

WHAT THIS IS FOR. The structural layout features have no ground truth.
They were written from convention and are weighted by hand, and the last
attempt to learn weights produced a 58%-accurate model because its
corpus was one design labelled 297-to-15 in under two seconds a vote.

A human-drawn sheet is the ground truth that corpus never had. If the
features are worth anything, a professionally maintained schematic
should score LOW on them. If it does not, the feature is wrong and no
amount of training data will rescue it.

DELIBERATELY PARTIAL. This reads symbol placements, labels and wires,
which is what the structural features consume. It does not attempt to
be a schematic importer: no hierarchy resolution, no netlist extraction,
no bus expansion. Those are needed to lay a sheet out again and are a
separate job.

Units: KiCad schematics are in millimetres, and this package works in
mils. Converted on the way out so callers never see a mixed frame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from eda_agent.libimport.kicad.sexpr import find, find_all, loads, value
from eda_agent.units import MILS_PER_MM


def _mil(mm: Any) -> int:
    """KiCad schematics are millimetre-based; this package is mils.

    The factor comes from eda_agent.units, which owns it. Writing it
    out here forked the fact, and tests/test_units.py exists to catch
    exactly that.
    """
    try:
        return int(round(float(mm) * MILS_PER_MM))
    except (TypeError, ValueError):
        return 0


@dataclass
class SheetSymbol:
    """One placed symbol, in mils, Y as KiCad stores it."""

    reference: str
    lib_id: str
    x: int
    y: int
    rotation: int = 0
    #: The Value property as drawn, e.g. '100nF'. Carried because it is
    #: the only thing on a sheet that distinguishes a decoupling cap
    #: from a timing or coupling one without a netlist.
    value: str = ""

    @property
    def prefix(self) -> str:
        """Designator letters, e.g. 'C' from 'C12'. Empty when unusual."""
        letters = "".join(c for c in self.reference if c.isalpha())
        return letters


@dataclass
class SheetPin:
    """One pin of a placed symbol, in world mils."""

    reference: str        # owning symbol
    number: str
    name: str
    x: int
    y: int
    electrical_type: str  # input / output / bidirectional / passive / power_in
    #: True when this pin belongs to a #PWR pseudo-part, i.e. a supply or
    #: ground GLYPH rather than a real component. Carried because the
    #: glyph's position relative to the pins it feeds is the convention
    #: worth measuring, and that needs the two told apart.
    is_power_glyph: bool = False
    #: The glyph's text, e.g. 'GND' or '+3V3'. Empty for a real part.
    glyph_text: str = ""


@dataclass
class SheetLabel:
    text: str
    x: int
    y: int


@dataclass
class SheetWire:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class KicadSheet:
    """What a human drew, in the units this package uses."""

    path: str = ""
    title: str = ""
    #: The frame the human actually drew on. More than half the KiCad
    #: demo sheets are A3, and laying their netlist out on an assumed
    #: A4 gives the engine less room than the person had.
    paper: str = "A4"
    symbols: list[SheetSymbol] = field(default_factory=list)
    pins: list[SheetPin] = field(default_factory=list)
    labels: list[SheetLabel] = field(default_factory=list)
    wires: list[SheetWire] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.symbols


def _property_value(node: list, name: str) -> str:
    """A symbol's named property, e.g. Reference or Value."""
    for prop in find_all(node, "property"):
        if len(prop) > 2 and str(prop[1]) == name:
            return str(prop[2])
    return ""


def _at(node: list) -> tuple[int, int, int]:
    """(x, y, rotation) in mils and degrees. (0, 0, 0) when absent."""
    at = find(node, "at")
    if not at or len(at) < 3:
        return (0, 0, 0)
    rot = 0
    if len(at) > 3:
        try:
            rot = int(float(at[3]))
        except (TypeError, ValueError):
            rot = 0
    return (_mil(at[1]), _mil(at[2]), rot)



def _unit_index(name: str):
    """The unit number of a ``NAME_<unit>_<style>`` sub-symbol, or None.

    KiCad names sub-units by unit and body style, and unit 0 holds what
    is common to all of them. An earlier rule tested only for the
    literal ``_1_1``, so unit 2 of a multi-unit part was emitted as a
    library entry of its own AND its pins were folded into the parent:
    on the demo corpus one FPGA contributed 485 pins that sat inside a
    body box unioned across three unrelated units.
    """
    parts = name.rsplit("_", 2)
    if len(parts) < 3:
        return None
    try:
        return int(parts[1]) if parts[2].isdigit() else None
    except ValueError:
        return None


def _own_units(defn: list) -> list:
    """The parent node plus the sub-units that describe ONE symbol.

    Unit 0 is shared and unit 1 is the first real one. Later units are
    alternative gates of the same package, and unioning them produces a
    body that contains its own pins.
    """
    nodes = [defn]
    for node in find_all(defn, "symbol"):
        if len(node) < 2:
            continue
        unit = _unit_index(str(node[1]))
        if unit is None or unit <= 1:
            nodes.append(node)
    return nodes


def _definition_pins(defn: list) -> list[tuple]:
    """(number, name, x_mm, y_mm, electrical_type, angle, length_mm).

    Pins live on the sub-units (``NAME_1_1``) rather than on the parent
    symbol node, so both levels are walked.
    """
    out: list[tuple[str, str, float, float, str]] = []
    for node in _own_units(defn):
        for pin in find_all(node, "pin"):
            if len(pin) < 2:
                continue
            etype = str(pin[1])
            at = find(pin, "at")
            if not at or len(at) < 3:
                continue
            try:
                px, py = float(at[1]), float(at[2])
            except (TypeError, ValueError):
                continue
            angle = 0.0
            if len(at) > 3:
                try:
                    angle = float(at[3])
                except (TypeError, ValueError):
                    angle = 0.0
            length = 2.54
            ln = find(pin, "length")
            if ln and len(ln) > 1:
                try:
                    length = float(ln[1])
                except (TypeError, ValueError):
                    length = 2.54
            num = find(pin, "number")
            nam = find(pin, "name")
            out.append((
                str(num[1]) if num and len(num) > 1 else "",
                str(nam[1]) if nam and len(nam) > 1 else "",
                px, py, etype, angle, length,
            ))
    return out


def _place_pin(px_mm: float, py_mm: float, sx: int, sy: int,
               rotation: int) -> tuple[int, int]:
    """Symbol-local mm to world mils.

    A schematic is Y-DOWN and a symbol definition is Y-UP, so the local
    y is subtracted rather than added. Rotation is applied about the
    symbol origin before that flip.

    Verified rather than assumed: read_sheet_file callers can check that
    the pins land on wire endpoints, which is what pin_wire_agreement
    reports.
    """
    px, py = _mil(px_mm), _mil(py_mm)
    r = rotation % 360
    if r == 90:
        px, py = -py, px
    elif r == 180:
        px, py = -px, -py
    elif r == 270:
        px, py = py, -px
    return (sx + px, sy - py)


def read_sheet(text: str, path: str = "") -> KicadSheet:
    """Parse one ``.kicad_sch`` document.

    Only top-level ``symbol`` nodes are placements. The ``lib_symbols``
    block near the top of every file contains symbol DEFINITIONS with
    the same tag, and counting those as placed parts would report a
    sheet with four components as having forty.
    """
    tree = loads(text)
    if not tree:
        return KicadSheet(path=path)
    root = tree[0] if isinstance(tree[0], list) else tree

    sheet = KicadSheet(path=path)
    title_block = find(root, "title_block")
    if title_block:
        sheet.title = str(value(title_block, "title", default="") or "")
    paper = find(root, "paper")
    if paper and len(paper) > 1:
        name = str(paper[1]).strip().upper()
        if name:
            sheet.paper = name

    # Definitions live under lib_symbols and must not be counted.
    definitions = find(root, "lib_symbols")
    defs_by_name: dict[str, list] = {}
    defined: set[int] = set()
    if definitions:
        for d in find_all(definitions, "symbol"):
            defined.add(id(d))
            for sub in find_all(d, "symbol"):
                defined.add(id(sub))
            if len(d) > 1:
                defs_by_name.setdefault(str(d[1]), d)

    for node in find_all(root, "symbol"):
        if id(node) in defined:
            continue
        ref = _property_value(node, "Reference")
        if not ref:
            continue
        x, y, rot = _at(node)

        # '#PWR' and '#FLG' are pseudo-parts: real in the netlist, not
        # components. They are not placed as symbols, but their PINS are
        # kept, because a supply glyph's position relative to the pins it
        # feeds is exactly the convention worth checking.
        if ref.startswith("#"):
            defn = defs_by_name.get(str(value(node, "lib_id", default="") or ""))
            if defn is not None:
                text = _property_value(node, "Value")
                for num, nam, px, py, etype, _a, _l in _definition_pins(defn):
                    wx, wy = _place_pin(px, py, x, y, rot)
                    sheet.pins.append(SheetPin(
                        reference=ref, number=num, name=nam,
                        x=wx, y=wy, electrical_type=etype,
                        is_power_glyph=True, glyph_text=text,
                    ))
            continue
        sheet.symbols.append(SheetSymbol(
            reference=ref,
            lib_id=str(value(node, "lib_id", default="") or ""),
            x=x, y=y, rotation=rot,
            value=_property_value(node, "Value"),
        ))

        defn = defs_by_name.get(str(value(node, "lib_id", default="") or ""))
        if defn is not None:
            for num, nam, px, py, etype, _a, _l in _definition_pins(defn):
                wx, wy = _place_pin(px, py, x, y, rot)
                sheet.pins.append(SheetPin(
                    reference=ref, number=num, name=nam,
                    x=wx, y=wy, electrical_type=etype,
                ))

    for tag in ("label", "global_label", "hierarchical_label"):
        for node in find_all(root, tag):
            if len(node) < 2:
                continue
            x, y, _ = _at(node)
            sheet.labels.append(SheetLabel(text=str(node[1]), x=x, y=y))

    for node in find_all(root, "wire"):
        pts = find(node, "pts")
        if not pts:
            continue
        xys = [p for p in find_all(pts, "xy") if len(p) >= 3]
        if len(xys) < 2:
            continue
        sheet.wires.append(SheetWire(
            x1=_mil(xys[0][1]), y1=_mil(xys[0][2]),
            x2=_mil(xys[1][1]), y2=_mil(xys[1][2]),
        ))

    return sheet


def read_sheet_file(path: str | Path) -> KicadSheet:
    p = Path(path)
    return read_sheet(p.read_text(encoding="utf-8", errors="replace"), str(p))


def pin_wire_agreement(sheet: KicadSheet, tolerance: int = 10) -> float:
    """Fraction of pins that land on a wire endpoint.

    THE TRANSFORM CHECKS ITSELF. Placing a pin needs a rotation and a
    Y flip, and getting either wrong yields coordinates that look
    plausible and are meaningless. A human sheet has wires drawn to its
    pins, so agreement between the two is evidence the transform is
    right. Low agreement means the placement maths is wrong, not that
    the schematic is.
    """
    if not sheet.pins or not sheet.wires:
        return 0.0
    ends = set()
    for w in sheet.wires:
        ends.add((w.x1, w.y1))
        ends.add((w.x2, w.y2))
    hits = 0
    for pin in sheet.pins:
        for ex, ey in ends:
            if abs(ex - pin.x) <= tolerance and abs(ey - pin.y) <= tolerance:
                hits += 1
                break
    return hits / len(sheet.pins)


def _resolve(sheet: KicadSheet, tolerance: int = 10):
    """Group pins into nets from the drawn geometry.

    A schematic states connectivity three ways and all three matter:
    wires joined end to end, pins sitting on a wire endpoint, and labels
    that merge otherwise-disjoint groups by name. Union-find over
    coincident points handles the first two, and the labels merge the
    result.

    WHY THIS IS NEEDED. Every layout feature that tried to associate two
    things by DISTANCE has been wrong: a power glyph to its nearest pin,
    a bypass cap to its nearest IC, an output to its nearest input. The
    relationship is electrical, and on a drawn sheet this is where it
    lives.

    Returns ``{net_name: [SheetPin, ...]}``. A group with no label gets
    a synthetic ``N_<n>`` name.

    A label or pin part way ALONG a wire attaches to it, not just one at
    an endpoint. Endpoints alone under-merge silently: the net splits in
    two and each half looks complete.

    KNOWN LIMITS, because a partial answer presented as a full one is
    worse than none: no bus expansion, no hierarchy, and two wires that
    merely cross are not joined (which is correct without a junction,
    and this does not read junctions yet).

    Returns ``(nets, wire_net_names)``. The second is parallel to
    ``sheet.wires`` and holds ONE COPY of the union-find: a caller that
    wants the wires labelled must not re-derive connectivity, because
    two copies drifting apart is how a net silently becomes two.
    """
    parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find_root(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find_root(a), find_root(b)
        if ra != rb:
            parent[ra] = rb

    def snap(x: int, y: int) -> tuple[int, int]:
        # Coincidence within tolerance, by quantising rather than by
        # pairwise comparison, which would be quadratic on 45k pins.
        t = max(1, tolerance)
        return (int(round(x / t)) * t, int(round(y / t)) * t)

    for w in sheet.wires:
        union(snap(w.x1, w.y1), snap(w.x2, w.y2))

    def on_segment(px: int, py: int, w) -> bool:
        """Whether a point lies on a wire, not merely at its ends.

        A label is normally placed part way ALONG a wire, and a pin can
        meet one at a T. Unioning endpoints alone leaves both stranded,
        which under-merges the netlist silently: every affected net
        splits into two that each look complete.
        """
        if min(w.x1, w.x2) - tolerance <= px <= max(w.x1, w.x2) + tolerance            and min(w.y1, w.y2) - tolerance <= py <= max(w.y1, w.y2) + tolerance:
            # Cross product against the segment, scaled by its length so
            # the tolerance stays in mils rather than in area.
            dx, dy = w.x2 - w.x1, w.y2 - w.y1
            span = max(1.0, (dx * dx + dy * dy) ** 0.5)
            cross = abs(dx * (py - w.y1) - dy * (px - w.x1)) / span
            return cross <= tolerance
        return False

    def attach(px: int, py: int) -> None:
        key = snap(px, py)
        find_root(key)
        for w in sheet.wires:
            if on_segment(px, py, w):
                union(key, snap(w.x1, w.y1))
                break

    pins_at: dict[tuple[int, int], list] = {}
    for pin in sheet.pins:
        attach(pin.x, pin.y)
        pins_at.setdefault(snap(pin.x, pin.y), []).append(pin)

    # Labels name a group and merge groups that share a name.
    label_groups: dict[str, list] = {}
    for lab in sheet.labels:
        attach(lab.x, lab.y)
        label_groups.setdefault(lab.text, []).append(snap(lab.x, lab.y))
    for points in label_groups.values():
        for other in points[1:]:
            union(points[0], other)

    # A power symbol is a GLOBAL connection by name: every GND glyph on
    # a sheet is the same net whether or not a wire joins them, and the
    # net is called what the glyph says. Treating them as anonymous
    # groups split each rail into a dozen N_<n> nets and left the
    # rail-direction feature with 13 matchable glyphs on the whole
    # corpus, which read as a convention nobody violates.
    glyph_groups: dict[str, list] = {}
    for pin in sheet.pins:
        if not pin.is_power_glyph or not pin.glyph_text:
            continue
        glyph_groups.setdefault(pin.glyph_text, []).append(snap(pin.x, pin.y))
    for points in glyph_groups.values():
        for other in points[1:]:
            union(points[0], other)

    name_of: dict[tuple[int, int], str] = {}
    for text, points in label_groups.items():
        name_of[find_root(points[0])] = text
    for text, points in glyph_groups.items():
        name_of.setdefault(find_root(points[0]), text)

    nets: dict[str, list] = {}
    anonymous = 0
    for key, pins in pins_at.items():
        root = find_root(key)
        name = name_of.get(root)
        if name is None:
            anonymous += 1
            # N_1 rather than the netlister's N$1: the DesignPlan net
            # name pattern forbids '$', and a name the schema rejects
            # makes the whole plan unusable downstream.
            name = f"N_{anonymous}"
            name_of[root] = name
        nets.setdefault(name, []).extend(pins)

    # Wires carry a net name too. A segment on a group with no pin on it
    # still gets one, or two unrelated wires would both read as "" and
    # score as the same net.
    wire_names: list[str] = []
    for w in sheet.wires:
        root = find_root(snap(w.x1, w.y1))
        name = name_of.get(root)
        if name is None:
            anonymous += 1
            name = f"N_{anonymous}"
            name_of[root] = name
        wire_names.append(name)
    return nets, wire_names


def extract_nets(sheet: KicadSheet, tolerance: int = 10) -> dict[str, list]:
    """``{net_name: [SheetPin, ...]}`` from the drawn geometry.

    See ``_resolve`` for how the association is made and what it does
    not cover.
    """
    return _resolve(sheet, tolerance)[0]


def wire_nets(sheet: KicadSheet, tolerance: int = 10) -> list[str]:
    """The net name of each entry in ``sheet.wires``, in order.

    Needed to rebuild a human sheet on this package's canvas: the
    scorer does not count two SAME-net wires as crossing, so wires
    handed over without their nets make a tidy sheet look tangled.
    """
    return _resolve(sheet, tolerance)[1]


def _definition_body(defn: list):
    """The union of a symbol's drawn rectangles, in mils, or None.

    Graphics live on the sub-units (``NAME_1_1``) like the pins do, so
    both levels are walked. A symbol drawn entirely from polylines or
    arcs has no rectangle and returns None; the caller falls back to
    the pin envelope for those rather than inventing a box.
    """
    from eda_agent.design.symbols import SymbolBBox

    xs: list[int] = []
    ys: list[int] = []

    def take(node, *keys) -> None:
        for key in keys:
            point = find(node, key)
            if point and len(point) >= 3:
                xs.append(_mil(point[1]))
                ys.append(_mil(point[2]))

    for node in _own_units(defn):
        for rect in find_all(node, "rectangle"):
            take(rect, "start", "end")
        # A passive is drawn with POLYLINES, not a rectangle: a
        # capacitor is two parallel strokes. Reading rectangles alone
        # left every such symbol falling back to the pin envelope,
        # which now holds body-attach ends -- and those coincide at the
        # origin for a two-pin part, giving a zero-height body that
        # reads as overlapping whatever it sits near.
        for poly in find_all(node, "polyline"):
            pts = find(poly, "pts")
            for xy in find_all(pts, "xy") if pts else []:
                if len(xy) >= 3:
                    xs.append(_mil(xy[1]))
                    ys.append(_mil(xy[2]))
        for arc in find_all(node, "arc"):
            take(arc, "start", "mid", "end")
        for circle in find_all(node, "circle"):
            centre, radius = find(circle, "center"), find(circle, "radius")
            if not centre or len(centre) < 3:
                continue
            r = _mil(radius[1]) if radius and len(radius) > 1 else 0
            cx, cy = _mil(centre[1]), _mil(centre[2])
            xs.extend((cx - r, cx + r))
            ys.extend((cy - r, cy + r))
    if not xs:
        return None
    return SymbolBBox(x_min=min(xs), y_min=min(ys),
                      x_max=max(xs), y_max=max(ys))


def symbols_from_sheet(text: str, lib_path: str = "") -> dict:
    """Build SymbolModels from a sheet's own ``lib_symbols`` block.

    A ``.kicad_sch`` embeds the full definition of every symbol it
    places, so one file is a self-contained fixture: real pins, real
    geometry, real electrical types, and no live EDA tool to extract
    them from. That matters because every design test until now has run
    against hand-written mock symbols, which agree with whatever the
    test author assumed.

    Returns ``{lib_id: SymbolModel}`` ready for a MockExtractor.

    Pin orientation: KiCad's angle points from the connection end INTO
    the body and this package's points away from it, so the mapping
    adds 180 degrees. The fixture that first "verified" this agreed
    with the same wrong assumption the code made; what settled it was a
    real symbol whose drawn rectangle could be compared against where
    its pins end.
    """
    from eda_agent.design.symbols import (
        _PIN_DIRECTION,
        SymbolBBox,
        SymbolModel,
        SymbolPin,
    )

    tree = loads(text)
    if not tree:
        return {}
    root = tree[0] if isinstance(tree[0], list) else tree
    definitions = find(root, "lib_symbols")
    if not definitions:
        return {}

    out: dict[str, Any] = {}
    for defn in find_all(definitions, "symbol"):
        if len(defn) < 2:
            continue
        name = str(defn[1])
        if _unit_index(name) is not None:
            continue          # a sub-unit, not a library entry
        raw = _definition_pins(defn)
        if not raw:
            continue

        pins = []
        for num, nam, px_mm, py_mm, etype, angle, length_mm in raw:
            # KiCad's angle points from the connection end INTO the
            # body; this package's orientation points AWAY from it. The
            # two are 180 degrees apart, so the mapping is a rotation
            # and not the division an earlier draft used.
            #
            # MEASURED, not reasoned: NTS0104_DHVQFN draws its body as
            # a rectangle from (2.54, 2.54) to (17.78, -17.78) mm and
            # puts its left-hand pins at x=0 with angle 0 and length
            # 2.54, so each one runs from x=0 to x=2.54 -- up to the
            # body's left edge, pointing right, INTO it.
            orientation = (int(round(angle / 90.0)) + 2) % 4
            length = _mil(length_mm)
            # KiCad's (at) is the ELECTRICAL end; SymbolPin holds the
            # BODY-ATTACH end, because _pin_to_world adds length along
            # the orientation to recover the electrical one. Storing the
            # connection point verbatim extends it twice: MEASURED, 56
            # of 8759 canvas pins landed on the wire the sheet drew to
            # them, and every residual was one pin length along that
            # pin's own axis.
            #
            # Only the PLACEMENT is Y-down. A symbol's local frame is
            # already Y-up, so nothing is negated here; negating it
            # anyway mirrored each body around its pins and left a
            # residue of exactly twice each local y.
            dx, dy = _PIN_DIRECTION[orientation]
            pins.append(SymbolPin(
                designator=num or nam or "?",
                name=nam or num or "",
                x=_mil(px_mm) - length * dx,
                y=_mil(py_mm) - length * dy,
                orientation=orientation,
                length=length,
                electrical_type=etype,
            ))
        # The DRAWN rectangle, not the pin envelope. Deriving the box
        # from pin coordinates makes it span the connection ends, so
        # every legitimate stub wire lands INSIDE the "body" and scores
        # as passing through it: the engine's own scorer charged 1600
        # for four such crossings on a two-part sheet where the human
        # scored zero for the same netlist.
        box = _definition_body(defn)
        if box is None:
            xs = [q.x for q in pins]
            ys = [q.y for q in pins]
            box = SymbolBBox(x_min=min(xs), y_min=min(ys),
                             x_max=max(xs), y_max=max(ys))
        out[name] = SymbolModel(
            lib_path=lib_path or "sheet",
            lib_ref=name,
            pins=tuple(pins),
            body_bbox=box,
            designator_prefix=_property_value(defn, "Reference") or "U",
        )
    return out
