# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read KiCad libraries into the neutral model the Altium emitter uses.

The part providers surface plenty of KiCad parts (a registry, and the
libraries KiCad installs locally), but this server had an EasyEDA->Altium
converter and NO KiCad->Altium path, so those hits were a dead end for an
Altium user.

Rather than write a second Altium emitter, this parses KiCad into the
SAME neutral model ``libimport.easyeda.document`` produces, so
``build_altium_plan`` works on it unchanged. The neutral frame is mils,
Y-up, origin-relative.

The three conversions that must be right, and are each easy to get
backwards:

* UNITS. KiCad is millimetres throughout; the neutral model is mils.
* Y AXIS. ``.kicad_sym`` is Y-UP (same as neutral, no flip).
  ``.kicad_mod`` is Y-DOWN, so footprint Y negates. Getting this wrong
  mirrors a footprint, which still looks plausible.
* PIN ANGLE. KiCad's pin angle points from the connection end toward
  the body, which is the same convention the neutral model uses, so it
  passes through. (EasyEDA's is 180 degrees off; that asymmetry is why
  it is stated here rather than assumed.)
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Optional

from eda_agent.libimport.easyeda.document import (
    EasyEdaComponent,
    EasyEdaFootprint,
    EasyEdaSymbol,
)
from eda_agent.libimport.easyeda.shapes import (
    EeArc,
    EeCircle,
    EePad,
    EePin,
    EePolyline,
    EeRect,
    EeText,
)
from eda_agent.libimport.kicad.sexpr import find, find_all, loads, value

__all__ = ["read_kicad_footprint", "read_kicad_symbol", "MM_TO_MIL"]

from eda_agent.units import MILS_PER_MM as MM_TO_MIL

#: KiCad layer name -> EasyEDA layer id, the neutral model's vocabulary.
_LAYER_TO_ID = {
    "F.Cu": 1, "B.Cu": 2, "F.SilkS": 3, "B.SilkS": 4,
    "F.Paste": 5, "B.Paste": 6, "F.Mask": 7, "B.Mask": 8,
    "Edge.Cuts": 10, "Cmts.User": 12, "F.Fab": 13, "B.Fab": 14,
}

#: KiCad pin electrical type -> the neutral model's numeric code.
_ELEC_TO_ID = {
    "input": 1, "output": 2, "bidirectional": 3, "power_in": 4,
    "power_out": 4, "passive": 0, "unspecified": 0, "free": 0,
    # Altium has exact equivalents for these three, so they are carried
    # rather than flattened. Collapsing open-collector to passive is
    # what stops ERC asking for the pull-up.
    "tri_state": 12, "open_collector": 10, "open_emitter": 11,
    # No Altium equivalent: its pin vocabulary has no "not connected"
    # (an unused pin is marked with a No-ERC directive instead), so
    # passive is the honest destination rather than a worse guess.
    "no_connect": 0,
}


def _num(text: Any, default: float = 0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _at(node: list) -> tuple[float, float, float]:
    """``(at x y [angle])`` in mm/degrees, or zeros."""
    child = find(node, "at")
    if not child:
        return (0.0, 0.0, 0.0)
    return (_num(child[1] if len(child) > 1 else 0),
            _num(child[2] if len(child) > 2 else 0),
            _num(child[3] if len(child) > 3 else 0))


def _layer_id(node: list, default: int = 3) -> int:
    name = value(node, "layer", 1)
    if isinstance(name, str):
        return _LAYER_TO_ID.get(name, default)
    layers = find(node, "layers")
    if layers:
        for item in layers[1:]:
            if isinstance(item, str) and item in _LAYER_TO_ID:
                return _LAYER_TO_ID[item]
    return default


#: A unit sub-symbol is named ``<owner>_<unit>_<style>``. Unit 0 holds
#: art shared by every unit. Style 0 is art common to every body style,
#: style 1 the standard drawing, and 2 upward the alternates (KiCad's
#: DeMorgan equivalent): the SAME unit drawn differently, with the same
#: pins. Taking two styles at once therefore duplicates every pin.
_UNIT_SUFFIX = re.compile(r"_(\d+)_(\d+)$")


def node_has_bare_hide(node: list) -> bool:
    """True if a node carries ``hide`` as a bare token.

    KiCad wrote ``hide`` as a plain atom before the 2024 format and as
    ``(hide yes)`` after it. Both appear in the wild because a library
    can be older than the installed KiCad, so both are accepted.
    """
    return any(item == "hide" for item in node if isinstance(item, str))


#: Fallback text height in mils when a node carries no font size. Every
#: one of the 18597 fp_text entries in the installed corpus states its
#: size, so this is close to unreachable in practice; it exists so a
#: hand-written or truncated file still produces legible text rather
#: than a zero-height one.
_DEFAULT_TEXT_MILS = 60.0


def _text_effects(node: list) -> tuple[float, float]:
    """``(height, stroke_width)`` in mils from a node's font effects.

    KiCad states text size as ``(effects (font (size w h) (thickness
    t)))``, where the pair is width and HEIGHT and the two are usually
    equal. Height is what "text size" means to a fab house and to
    Altium, so height is what is carried.

    This used to be hardcoded at 60 mils for every string. The corpus
    holds 91 distinct heights from 0.4 mm up: the common 1 mm case was
    arriving 52% oversized and 0.4 mm text nearly 4x, which on a dense
    footprint puts silkscreen across the pads.
    """
    size_mils, width_mils = _DEFAULT_TEXT_MILS, 0.0
    effects = find(node, "effects")
    if effects is None:
        return size_mils, width_mils
    font = find(effects, "font")
    if font is None:
        return size_mils, width_mils
    size = find(font, "size")
    if size is not None and len(size) > 2:
        height = _num(size[2])
        if height > 0:
            size_mils = height * MM_TO_MIL
    thickness = _num(value(font, "thickness", 1, 0))
    if thickness > 0:
        width_mils = thickness * MM_TO_MIL
    return size_mils, width_mils


def _text_is_mirrored(node: list) -> bool:
    """True when the node's effects carry KiCad's ``mirror`` justify.

    Spelled ``(effects ... (justify mirror))``, and it may sit beside
    other justify tokens such as ``left`` or ``bottom``.
    """
    effects = find(node, "effects")
    if effects is None:
        return False
    justify = find(effects, "justify")
    if justify is None:
        return False
    return any(item == "mirror" for item in justify if isinstance(item, str))


def _unit_style_of(sub: list) -> Optional[tuple[int, int]]:
    """``(unit, style)`` for a sub-symbol, or None if unreadable."""
    if len(sub) < 2:
        return None
    match = _UNIT_SUFFIX.search(str(sub[1]))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def read_kicad_symbol(text: str, name: Optional[str] = None,
                      unit: Optional[int] = None) -> EasyEdaComponent:
    """Parse a ``.kicad_sym`` document into the neutral model.

    ``name`` selects one symbol from a multi-symbol library; the first
    is used when omitted. ``unit`` selects which unit of a multi-unit
    part to convert (see below).

    No Y flip: ``.kicad_sym`` is already Y-up, like the neutral frame.

    Two things about the format that are easy to miss, and silent when
    missed:

    DERIVED SYMBOLS. Over half the entries in KiCad's standard
    libraries (12209 of 22728 as shipped with 10.0.1) carry no geometry
    of their own: they are ``(extends "PARENT")`` and inherit the
    parent's pins and body, overriding only properties. Reading one
    without following the link yields an empty symbol, which converts
    to an Altium part with no pins.

    MULTI-UNIT PARTS. A quad gate keeps each gate in its own unit
    sub-symbol, every one drawn at the same coordinates. By default
    (``unit=None``) ALL units are read and each pin is tagged with the
    unit that owns it, which the Altium emitter turns into a real
    multi-part component via ``part_count`` / ``owner_part_id``. Pass a
    unit number to convert just that one instead.

    Never merge units into a flat symbol: they share coordinates by
    design, so a merged symbol has every unit's pins stacked on the same
    points and still looks like a part that converted.
    """
    root = loads(text)
    symbols = [n for n in find_all(root, "symbol")]
    if not symbols:
        raise ValueError("no symbol found in this .kicad_sym")

    chosen = symbols[0]
    if name:
        for sym in symbols:
            if len(sym) > 1 and str(sym[1]) == name:
                chosen = sym
                break

    sym_name = str(chosen[1]) if len(chosen) > 1 else (name or "SYMBOL")
    comp = EasyEdaComponent(mpn=sym_name)

    # Properties come from the whole inheritance chain, nearest first,
    # so a derived symbol's own datasheet or footprint wins over the
    # parent's while everything it does not restate is inherited.
    chain = [chosen]
    seen = {sym_name}
    parent = value(chosen, "extends", 1)
    while isinstance(parent, str) and parent and parent not in seen:
        seen.add(parent)
        found = next((s for s in symbols
                      if len(s) > 1 and str(s[1]) == parent), None)
        if found is None:
            comp.warnings.append(
                f"symbol {sym_name!r} extends {parent!r}, which is not in "
                f"this file; geometry could not be inherited")
            break
        chain.append(found)
        parent = value(found, "extends", 1)

    prefix = "U"
    prefix_set = False
    for link in chain:
        for prop in find_all(link, "property"):
            if len(prop) <= 2:
                continue
            key, val = str(prop[1]).lower(), str(prop[2])
            if key == "reference" and not prefix_set:
                prefix, prefix_set = val.rstrip("?") or "U", True
            elif key == "datasheet" and not comp.datasheet:
                comp.datasheet = val
            elif key == "description" and not comp.description:
                comp.description = val
            elif key == "footprint" and not comp.footprint_ref:
                # "Library:Name", pointing into KiCad's footprint
                # libraries. Kept as the pointer the source gave rather
                # than resolved here: this reader is handed text, not a
                # library tree.
                comp.footprint_ref = val

    # Pin name / number visibility is declared once per SYMBOL in KiCad,
    # as (pin_names (hide yes)) / (pin_numbers (hide yes)), and it is how
    # essentially every passive is drawn: 2447 symbols in KiCad 10.0.1's
    # libraries hide pin names and 412 hide pin numbers. Altium models
    # the same thing PER PIN (ISch_Pin.ShowName / ShowDesignator), so the
    # symbol-level flag is pushed down onto every pin below.
    #
    # Resolved along the inheritance chain nearest-first, exactly like
    # the properties above: a derived symbol that restates the flag wins,
    # one that stays silent inherits it. Reading only `chosen` would draw
    # pin numbers on every derived passive, and derived symbols are more
    # than half the library.
    def _visible(tag: str) -> bool:
        for link in chain:
            node = find(link, tag)
            if node is None:
                continue
            if node_has_bare_hide(node):        # KiCad 6/7 spelling
                return False
            hide = find(node, "hide")           # 2024 format onward
            if hide is not None:
                return str(value(node, "hide", 1, "")) != "yes"
            # Declared without a hide flag, e.g. (pin_names (offset 1)),
            # means visible AND stops the search: the nearest link that
            # speaks is authoritative, so a parent cannot re-hide what a
            # child has shown.
            return True
        return True     # absent entirely: KiCad's default is visible

    names_visible = _visible("pin_names")
    numbers_visible = _visible("pin_numbers")

    # Geometry comes from the nearest link in the chain that has any.
    # A derived symbol usually has none at all, but the format permits
    # it to add some, and then it is that symbol's own geometry that is
    # authoritative rather than the parent's.
    owner = next((link for link in chain if find_all(link, "symbol")),
                 chosen)
    subs = find_all(owner, "symbol")
    tagged = [(sub, _unit_style_of(sub)) for sub in subs]
    units = sorted({t[0] for _, t in tagged if t and t[0] > 0})
    if unit is None:
        wanted = set(units) or {1}          # every unit: a multi-part part
        want = units[0] if units else 1
    else:
        want = unit if unit in units else (units[0] if units else unit)
        wanted = {want}

    # One body style only. Style 0 is shared art, so the drawing itself
    # is the lowest style above it, normally 1.
    styles = sorted({t[1] for _, t in tagged
                     if t and (t[0] == 0 or t[0] in wanted) and t[1] > 0})
    style = styles[0] if styles else 1

    shapes: list[Any] = []
    # The top level can carry art directly; unit 0 is art shared by
    # every unit, and both belong with whichever unit is converted.
    shapes.extend(_symbol_shapes(owner))
    for sub, tag in tagged:
        if tag is None:
            shapes.extend(_symbol_shapes(sub))
            continue
        number, sub_style = tag
        if number != 0 and number not in wanted:
            continue
        if sub_style not in (0, style):
            continue
        part = _symbol_shapes(sub)
        # Tag this unit's pins and art so the emitter can assign them to
        # the right sub-part. Unit 0 is shared by every sub-part, which is
        # what owner_part_id 0 means on the Altium side.
        for shape in part:
            shape.unit = number
            if shape.kind == "pin":
                # Symbol-level in KiCad, per-pin in the neutral model and
                # in Altium; see _visible above.
                shape.name_visible = names_visible
                shape.number_visible = numbers_visible
        shapes.extend(part)

    comp.unit_count = max(1, len(units))
    comp.unit = want
    if len(styles) > 1:
        comp.warnings.append(
            f"symbol {sym_name!r} is drawn in {len(styles)} body styles "
            f"{styles} (KiCad's DeMorgan alternates); style {style} was "
            f"converted. They differ only in the drawing, not the pins")
    if len(units) > 1 and unit is not None:
        comp.warnings.append(
            f"symbol {sym_name!r} has {len(units)} units {units}; only "
            f"unit {want} was converted because one was requested. Omit "
            f"the unit argument to build the whole multi-part component "
            f"instead")
    if owner is not chosen:
        comp.warnings.append(
            f"symbol {sym_name!r} is derived from "
            f"{str(owner[1])!r}; geometry was inherited from it")

    comp.symbol = EasyEdaSymbol(name=sym_name, prefix=prefix, shapes=shapes)
    if not any(s.kind == "pin" for s in shapes):
        comp.warnings.append(f"symbol {sym_name!r} has no pins")
    return comp


def _symbol_shapes(body: list) -> list[Any]:
    out: list[Any] = []
    k = MM_TO_MIL

    for node in find_all(body, "rectangle"):
        start = find(node, "start") or []
        end = find(node, "end") or []
        if len(start) < 3 or len(end) < 3:
            continue
        x1, y1 = _num(start[1]) * k, _num(start[2]) * k
        x2, y2 = _num(end[1]) * k, _num(end[2]) * k
        rect = EeRect(kind="rect", raw="")
        rect.x, rect.y = min(x1, x2), min(y1, y2)
        rect.width, rect.height = abs(x2 - x1), abs(y2 - y1)
        fill = find(node, "fill")
        rect.fill = bool(fill and value(fill, "type", 1) == "background")
        out.append(rect)

    for node in find_all(body, "circle"):
        centre = find(node, "center") or []
        if len(centre) < 3:
            continue
        circ = EeCircle(kind="circle", raw="")
        circ.cx, circ.cy = _num(centre[1]) * k, _num(centre[2]) * k
        circ.radius = _num(value(node, "radius", 1, 0)) * k
        out.append(circ)

    for tag in ("polyline", "bezier"):
        for node in find_all(body, tag):
            pts = find(node, "pts")
            if not pts:
                continue
            points = [(_num(p[1]) * k, _num(p[2]) * k)
                      for p in find_all(pts, "xy") if len(p) >= 3]
            if len(points) < 2:
                continue
            poly = EePolyline(kind="polyline", raw="")
            poly.points = points
            out.append(poly)

    for node in find_all(body, "arc"):
        start = find(node, "start") or []
        mid = find(node, "mid") or []
        end = find(node, "end") or []
        if len(start) < 3 or len(mid) < 3 or len(end) < 3:
            continue
        arc = _arc_from_three_points(
            (_num(start[1]) * k, _num(start[2]) * k),
            (_num(mid[1]) * k, _num(mid[2]) * k),
            (_num(end[1]) * k, _num(end[2]) * k))
        if arc is not None:
            out.append(arc)

    for node in find_all(body, "text"):
        txt = EeText(kind="text", raw="")
        txt.text = str(node[1]) if len(node) > 1 else ""
        tx, ty, tangle = _at(node)
        txt.x, txt.y = tx * k, ty * k
        # SYMBOL text states its angle in DECIDEGREES, unlike everything
        # else in the same file: pins and properties there use plain
        # degrees, and so does fp_text in a .kicad_mod. Measured across
        # the installed corpus, symbol text angles are 0/900/1800/2700
        # and no 90/180/270 appears at all, while pin angles are only
        # ever 0/90/180/270. Carrying the raw number would turn a
        # quarter turn into a half turn.
        txt.rotation = (tangle / 10.0) % 360.0
        txt.font_size, txt.stroke_width = _text_effects(node)
        txt.visible = True
        if txt.text:
            out.append(txt)

    for node in find_all(body, "pin"):
        pin = EePin(kind="pin", raw="")
        px, py, angle = _at(node)
        pin.x, pin.y = px * k, py * k
        # KiCad's pin angle already points from the connection end
        # toward the body, matching the neutral convention.
        pin.rotation = angle % 360.0
        pin.length = _num(value(node, "length", 1, 0)) * k
        number = find(node, "number")
        name_node = find(node, "name")
        pin.number = str(number[1]) if number and len(number) > 1 else ""
        pin.name = str(name_node[1]) if name_node and len(name_node) > 1 else ""
        # A bare "~" is KiCad's legacy marker for "no name" (KiCad 10
        # writes an empty string instead, and none of its 541462 pin
        # names is a lone tilde). Taken literally it becomes a pin
        # labelled "~" on the converted symbol.
        #
        # ONLY an exact match: "~{RESET}" is KiCad's overbar syntax for
        # an active-low signal and is a real name.
        if pin.name == "~":
            pin.name = ""
        elec = str(node[1]) if len(node) > 1 else "passive"
        pin.electric = _ELEC_TO_ID.get(elec, 0)
        # KiCad's third token is the pin's graphic style. Both notations
        # for active-low map to the dot: "inverted" draws the bubble and
        # "*_low" draws IEEE's angled wedge, but they mean the same
        # thing and Altium draws it as a dot either way.
        style = str(node[2]) if len(node) > 2 else "line"
        pin.dot = "inverted" in style or style.endswith("_low")
        pin.clock = "clock" in style
        # A hidden pin is still ELECTRICALLY REAL (this is how symbols
        # carry supply rails and no-connects), so it converts like any
        # other and only its visibility is carried across. 5378 of the
        # 106032 pin definitions in KiCad 10.0.1's libraries are hidden,
        # so dropping them would lose real pins and showing them all
        # would clutter every symbol that hides its NCs.
        hide = find(node, "hide")
        pin.display = not (
            node_has_bare_hide(node)
            or (hide is not None and str(value(node, "hide", 1, "")) == "yes")
        )
        out.append(pin)

    return out


def _arc_from_three_points(start, mid, end) -> Optional[EeArc]:
    """Neutral arc from KiCad's start/mid/end form.

    The neutral model stores SVG endpoint form (radii plus the
    large-arc and sweep flags), so the radius and the two flags have to
    be recovered from the circle through the three points.
    """
    (x1, y1), (xm, ym), (x2, y2) = start, mid, end
    d = 2.0 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
    if abs(d) < 1e-9:
        return None  # collinear: not an arc
    ux = ((x1 ** 2 + y1 ** 2) * (ym - y2)
          + (xm ** 2 + ym ** 2) * (y2 - y1)
          + (x2 ** 2 + y2 ** 2) * (y1 - ym)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x2 - xm)
          + (xm ** 2 + ym ** 2) * (x1 - x2)
          + (x2 ** 2 + y2 ** 2) * (xm - x1)) / d
    radius = math.hypot(x1 - ux, y1 - uy)

    a1 = math.atan2(y1 - uy, x1 - ux)
    am = math.atan2(ym - uy, xm - ux)
    a2 = math.atan2(y2 - uy, x2 - ux)

    # Sweep is positive if start -> mid -> end runs counter-clockwise.
    def norm(a):
        while a < 0:
            a += 2 * math.pi
        return a % (2 * math.pi)

    ccw = norm(am - a1) < norm(a2 - a1)
    span = norm(a2 - a1) if ccw else norm(a1 - a2)

    arc = EeArc(kind="arc", raw="")
    arc.x1, arc.y1 = x1, y1
    arc.x2, arc.y2 = x2, y2
    arc.rx = arc.ry = radius
    arc.rotation = 0.0
    arc.large_arc = 1 if span > math.pi else 0
    arc.sweep = 1 if ccw else 0
    return arc


def _custom_pad_outlines(node: list, px: float, py: float, angle: float
                         ) -> list[list[tuple[float, float]]]:
    """A custom pad's primitives as closed outlines in footprint millimetres
    (KiCad's Y down). They are given in the pad's own frame, turned with it:
    a polygon or rectangle as drawn, a circle as 32 points, a line as the
    band its width covers."""
    t = math.radians(angle)

    def place(x: float, y: float) -> tuple[float, float]:
        return (px + x * math.cos(t) + y * math.sin(t),
                py - x * math.sin(t) + y * math.cos(t))

    def pt(n, key):
        p = find(n, key)
        return (_num(p[1]), _num(p[2])) if p and len(p) > 2 else None

    out: list[list[tuple[float, float]]] = []
    prims = find(node, "primitives") or []
    for item in prims[1:] if prims else []:
        if not isinstance(item, list) or not item:
            continue
        tag = str(item[0])
        width = _num(value(item, "width", 1, 0))
        if tag == "gr_poly":
            pts = [(_num(p[1]), _num(p[2])) for p in find_all(find(item, "pts") or [], "xy")
                   if len(p) > 2]
            if len(pts) >= 3:
                out.append([place(*p) for p in pts])
        elif tag == "gr_rect":
            a, b = pt(item, "start"), pt(item, "end")
            if a and b:
                out.append([place(*p) for p in ((a[0], a[1]), (b[0], a[1]),
                                                 (b[0], b[1]), (a[0], b[1]))])
        elif tag == "gr_circle":
            c, e = pt(item, "center"), pt(item, "end")
            if c and e:
                r = math.dist(c, e) + width / 2
                out.append([place(c[0] + r * math.cos(2 * math.pi * i / 32),
                                  c[1] + r * math.sin(2 * math.pi * i / 32))
                            for i in range(32)])
        elif tag == "gr_line" and width > 0:
            a, b = pt(item, "start"), pt(item, "end")
            if a and b and a != b:
                ux, uy = b[0] - a[0], b[1] - a[1]
                n = math.hypot(ux, uy)
                nx, ny = -uy / n * width / 2, ux / n * width / 2
                out.append([place(*p) for p in ((a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                                                 (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny))])
    return out


def read_kicad_footprint(text: str) -> EasyEdaComponent:
    """Parse a ``.kicad_mod`` document into the neutral model.

    Y NEGATES here: ``.kicad_mod`` is Y-down while the neutral frame is
    Y-up. Skipping this mirrors the footprint, which still looks like a
    plausible part.
    """
    root = loads(text)
    if not root or root[0] != "footprint":
        raise ValueError("not a .kicad_mod document")
    name = str(root[1]) if len(root) > 1 else "FOOTPRINT"

    k = MM_TO_MIL
    shapes: list[Any] = []
    warnings: list[str] = []

    def fy(v: float) -> float:
        return -v * k

    for node in find_all(root, "pad"):
        pad = EePad(kind="pad", raw="")
        pad.number = str(node[1]) if len(node) > 1 else ""
        ptype = str(node[2]) if len(node) > 2 else "smd"
        pshape = str(node[3]) if len(node) > 3 else "rect"
        px, py, angle = _at(node)
        pad.cx, pad.cy = px * k, fy(py)
        size = find(node, "size") or []
        pad.width = _num(size[1] if len(size) > 1 else 0) * k
        pad.height = _num(size[2] if len(size) > 2 else 0) * k
        # Y is flipped for the position just above, and a mirror negates
        # rotation too. Rect and oval pads are 180-symmetric so getting
        # this wrong is invisible on most of a library, which is exactly
        # why it has to be right: the first roundrect or trapezoid pad
        # with a real angle would land mirrored with nothing to show it.
        pad.rotation = (-angle) % 360.0
        drill = find(node, "drill")
        if drill:
            nums = [_num(v) for v in drill[1:] if _is_number(v)]
            if nums:
                # ``(drill oval X Y)`` gives BOTH axes of a slot, in that
                # order, and either may be the long one. The neutral
                # model keeps the slot's length and the radius of its
                # round ends, so those come from max and min -- not from
                # the first number, which is the length whenever the
                # slot runs horizontally and silently doubles the hole.
                pad.hole_radius = min(nums) * k / 2.0
                if len(nums) > 1:
                    pad.hole_length = max(nums) * k
        pad.plated = ptype != "np_thru_hole"
        pad.layer = 11 if ptype in ("thru_hole", "np_thru_hole") else \
            _layer_id(node, 1)
        # Copper decides an SMD pad's side whatever order the layers are
        # listed in; a pad with none is an opening on every paste and mask
        # layer it names.
        named = [str(v) for v in (find(node, "layers") or [])[1:]]
        if pad.layer != 11:
            copper = [n for n in named if n in ("F.Cu", "B.Cu")]
            if copper:
                pad.layer = _LAYER_TO_ID[copper[0]]
        pad.aperture_layers = [_LAYER_TO_ID[n] for n in named
                               if _LAYER_TO_ID.get(n) in (5, 6, 7, 8)]
        pad.shape = {"rect": "RECT", "roundrect": "ROUNDRECT",
                     "circle": "ELLIPSE", "oval": "OVAL",
                     "custom": "POLYGON",
                     "trapezoid": "POLYGON"}.get(pshape, "RECT")
        if pad.shape == "ROUNDRECT":
            # Ratio of the corner radius to the SHORTER side. KiCad's
            # own default when the field is absent.
            ratio = find(node, "roundrect_rratio")
            pad.corner_ratio = (_num(ratio[1]) if ratio and len(ratio) > 1
                                else 0.25)
        if pshape == "custom":
            # The anchor is a plain pad; the copper is the anchor plus the
            # primitives, kept as outlines in the footprint's frame.
            options = find(node, "options") or []
            anchor = find(options, "anchor") if options else None
            if anchor and len(anchor) > 1 and str(anchor[1]) == "circle":
                pad.shape = "ELLIPSE"
            else:
                pad.shape = "RECT"
            pad.custom_outlines = [
                [(x * k, fy(y)) for x, y in poly]
                for poly in _custom_pad_outlines(node, px, py, angle)]
        elif pshape == "trapezoid":
            warnings.append(
                f"pad {pad.number!r} is a trapezoid pad, emitted as its "
                f"bounding rectangle; verify against the land pattern")
        shapes.append(pad)

    for tag in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc",
                "fp_text"):
        for node in find_all(root, tag):
            shape = _footprint_shape(tag, node, k, fy)
            if shape is not None:
                shapes.append(shape)

    comp = EasyEdaComponent(mpn=name, package=name, warnings=warnings)
    comp.footprint = EasyEdaFootprint(name=name, shapes=shapes)
    model = find(root, "model")
    if model and len(model) > 1:
        comp.footprint.model_3d_name = str(model[1])
        # Kept verbatim, variable and all. Resolving "${KICAD10_3DMODEL_DIR}"
        # needs the install tree, which this reader is not given: it is
        # handed text. Whoever knows the tree resolves it into
        # model_3d_path.
        comp.footprint.model_3d_ref = str(model[1])
    if not any(s.kind == "pad" for s in shapes):
        comp.warnings.append(f"footprint {name!r} has no pads")
    return comp


def _is_number(text: Any) -> bool:
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def _footprint_shape(tag: str, node: list, k: float, fy):
    if tag == "fp_line":
        start = find(node, "start") or []
        end = find(node, "end") or []
        if len(start) < 3 or len(end) < 3:
            return None
        poly = EePolyline(kind="track", raw="")
        poly.points = [(_num(start[1]) * k, fy(_num(start[2]))),
                       (_num(end[1]) * k, fy(_num(end[2])))]
        poly.stroke_width = _num(_stroke_width(node)) * k
        poly.layer = _layer_id(node)
        return poly

    if tag == "fp_rect":
        start = find(node, "start") or []
        end = find(node, "end") or []
        if len(start) < 3 or len(end) < 3:
            return None
        x1, y1 = _num(start[1]) * k, fy(_num(start[2]))
        x2, y2 = _num(end[1]) * k, fy(_num(end[2]))
        rect = EeRect(kind="rect", raw="")
        rect.x, rect.y = min(x1, x2), min(y1, y2)
        rect.width, rect.height = abs(x2 - x1), abs(y2 - y1)
        rect.stroke_width = _num(_stroke_width(node)) * k
        rect.layer = _layer_id(node)
        return rect

    if tag == "fp_poly":
        pts = find(node, "pts")
        if not pts:
            return None
        points = [(_num(p[1]) * k, fy(_num(p[2])))
                  for p in find_all(pts, "xy") if len(p) >= 3]
        # A closed polygon is stored WITHOUT a repeated final vertex.
        # Some sources write the closure explicitly and some leave it
        # implied, and carrying both forms means the same outline has
        # two representations: a round trip through a writer that closes
        # explicitly grows a point every time, and any consumer that
        # walks the edges counts a zero-length one. Normalise on the way
        # in so the model has a single answer.
        while len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if len(points) < 3:
            return None
        poly = EePolyline(kind="solid_region", raw="")
        poly.points = points
        # The polygon's own fill. KiCad 6 and later write (fill solid) or
        # (fill none), and an outline-only polygon (a courtyard, a fab
        # outline) must not arrive filled; KiCad 5 wrote no fill token and
        # its polygons were filled.
        fill_node = find(node, "fill")
        fill_word = str(fill_node[1]).lower() if fill_node and len(fill_node) > 1 else ""
        if fill_word in ("type",) and len(fill_node) > 2:
            fill_word = str(fill_node[2]).lower()
        poly.fill = "none" if fill_word in ("none", "no") else "solid"
        poly.stroke_width = _num(_stroke_width(node)) * k
        # An fp_poly IS a closed area, and the loop above has just
        # stripped the repeated final vertex that said so. Without this
        # flag the consumer sees a plain open run of points and draws
        # every edge but the one back to the start, which leaves a
        # visible notch in an outline that is meant to be sealed.
        poly.closed = True
        poly.layer = _layer_id(node)
        return poly

    if tag == "fp_circle":
        centre = find(node, "center") or []
        end = find(node, "end") or []
        if len(centre) < 3 or len(end) < 3:
            return None
        circ = EeCircle(kind="circle", raw="")
        circ.cx, circ.cy = _num(centre[1]) * k, fy(_num(centre[2]))
        circ.radius = math.hypot(_num(end[1]) - _num(centre[1]),
                                 _num(end[2]) - _num(centre[2])) * k
        circ.stroke_width = _num(_stroke_width(node)) * k
        circ.layer = _layer_id(node)
        return circ

    if tag == "fp_arc":
        start = find(node, "start") or []
        mid = find(node, "mid") or []
        end = find(node, "end") or []
        if len(start) < 3 or len(mid) < 3 or len(end) < 3:
            return None
        arc = _arc_from_three_points(
            (_num(start[1]) * k, fy(_num(start[2]))),
            (_num(mid[1]) * k, fy(_num(mid[2]))),
            (_num(end[1]) * k, fy(_num(end[2]))))
        if arc is not None:
            arc.stroke_width = _num(_stroke_width(node)) * k
            arc.layer = _layer_id(node)
        return arc

    if tag == "fp_text":
        kind = str(node[1]) if len(node) > 1 else "user"
        content = str(node[2]) if len(node) > 2 else ""
        # reference/value are placeholders Altium supplies itself.
        if kind in ("reference", "value") or not content:
            return None
        txt = EeText(kind="text", raw="")
        tx, ty, angle = _at(node)
        txt.x, txt.y = tx * k, fy(ty)
        txt.text = content
        # Mirrored with the Y flip, like the pads. The neutral model
        # negates text rotation on a mirror too (document._to_mils_yup).
        txt.rotation = (-angle) % 360.0
        txt.font_size, txt.stroke_width = _text_effects(node)
        txt.mirror = _text_is_mirrored(node)
        txt.visible = True
        txt.layer = _layer_id(node)
        return txt

    return None


def _stroke_width(node: list) -> float:
    stroke = find(node, "stroke")
    if stroke:
        return _num(value(stroke, "width", 1, 0))
    return _num(value(node, "width", 1, 0))


def read_kicad_files(symbol_path: Optional[str] = None,
                     footprint_path: Optional[str] = None,
                     symbol_name: Optional[str] = None,
                     unit: Optional[int] = None) -> EasyEdaComponent:
    """Build one neutral component from a .kicad_sym and/or .kicad_mod.

    ``unit`` defaults to None, meaning every unit of a multi-unit
    symbol is read and tagged so the emitter can build a single
    multi-part component. Pass a number to take just that sub-part.
    The default has to match ``read_kicad_symbol``: leaving it at 1
    here silently collapsed every multi-part component to its first
    sub-part for any caller that did not pass the argument.
    """
    comp: Optional[EasyEdaComponent] = None
    if symbol_path:
        comp = read_kicad_symbol(
            Path(symbol_path).read_text(encoding="utf-8"), symbol_name,
            unit=unit)
    if footprint_path:
        fp_comp = read_kicad_footprint(
            Path(footprint_path).read_text(encoding="utf-8"))
        if comp is None:
            comp = fp_comp
        else:
            comp.footprint = fp_comp.footprint
            comp.package = fp_comp.package
            comp.warnings.extend(fp_comp.warnings)
    if comp is None:
        raise ValueError("give at least one of symbol_path / footprint_path")
    return comp
