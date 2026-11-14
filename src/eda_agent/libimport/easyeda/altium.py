# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Emit an Altium install plan from a normalized EasyEDA component.

Altium's .SchLib / .PcbLib are OLE compound documents, undocumented and
not worth synthesizing. This bridge already exposes a full library
authoring API, so the emitter produces an ORDERED PLAN of existing MCP
tool calls instead of a file:

    app_set_active_document(.SchLib)
      lib_create_symbol -> lib_add_pins -> lib_add_symbol_* (body art)
    app_set_active_document(.PcbLib)
      lib_create_footprint -> lib_add_footprint_pads -> tracks/arcs/text
    app_set_active_document(.SchLib)
      lib_link_footprint

THE STEP ORDER IS LOAD BEARING. These tools are stateful: they take no
library_path and no component name, they act on the ACTIVE document and
on the component that the preceding create call made current. Executing
the steps out of order, or dropping an app_set_active_document, edits
whichever library happens to be focused. tests assert this ordering, and
also assert every step against the real registered tool signatures,
because a plan can otherwise stay perfectly self-consistent while every
argument name is wrong.

Same shape as :mod:`eda_agent.libimport.cse`: a pure offline function
returning ``{"ok": True, "steps": [{"tool": ..., "args": {...}}, ...]}``.
Driving Altium with the plan is the caller's job, which keeps this
module testable with no bridge and lets the agent review or edit the
plan before anything is written.

Units are Altium's schematic/PCB mils, and the neutral model is already
mils Y-up, so no axis flip is needed here.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Optional

from eda_agent.bridge.payload import unsendable_chars
from eda_agent.libimport.easyeda.document import EasyEdaComponent
from eda_agent.libimport.easyeda.geometry import svg_arc_to_center
from eda_agent.libimport.easyeda.shapes import PIN_ELECTRIC

__all__ = ["build_altium_plan"]

#: Neutral electrical name -> the string lib_add_pins expects.
#: These are the exact values that tool documents; capitalised variants
#: are not accepted.
_ALTIUM_ELEC = {
    "undefined": "passive",
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "power": "power",
    # Altium names these exactly, and Library.pas maps the strings to
    # eElectricOpenCollector / eElectricOpenEmitter / eElectricHiZ.
    "open_collector": "open_collector",
    "open_emitter": "open_emitter",
    "hiz": "hiz",
}

#: EasyEDA layer id -> Altium layer name for footprint primitives.
_ALTIUM_LAYER = {
    1: "TopLayer", 2: "BottomLayer",
    3: "TopOverlay", 4: "BottomOverlay",
    5: "TopPaste", 6: "BottomPaste",
    7: "TopSolder", 8: "BottomSolder",
    10: "KeepOutLayer", 11: "MultiLayer",
    # 13/14 are EasyEDA's top/bottom assembly layers. They must not share
    # a destination or bottom-side assembly art silently lands on the top
    # layer, where it reads as a real top-side marking.
    12: "Mechanical1", 13: "Mechanical13", 14: "Mechanical14",
}

#: Neutral layer ids that land on the BOTTOM of the board. Text here
#: must be mirrored to read correctly, since it is viewed through the
#: board. Taken from _ALTIUM_LAYER above: bottom copper, bottom
#: overlay/paste/solder, and the bottom assembly layer.
_BOTTOM_SIDE_LAYERS = frozenset({2, 4, 6, 8, 14})

#: EasyEDA pad shape -> the shape strings lib_add_footprint_pads takes.
_ALTIUM_PAD_SHAPE = {
    "ELLIPSE": "round",
    "RECT": "rectangular",
    "ROUNDRECT": "roundrect",
    "OVAL": "round",       # round with x_size != y_size is a stadium
    "POLYGON": "rectangular",
}


def _corner_radius_pct(ratio: float) -> int:
    """Neutral corner ratio -> the percentage Altium's pad expects.

    The two measure the radius against different things, so this is not
    a multiply by 100. Altium's documentation defines the value as "the
    percentage of half of the shortest pad side, where 100% completely
    rounds the shortest side", while KiCad's ``roundrect_rratio`` is the
    radius over the WHOLE shorter side. Hence the factor of two: KiCad's
    0.25 default is 50% in Altium, and a fully rounded end is 0.5 there
    and 100 here.
    """
    return max(0, min(100, int(round(float(ratio or 0.0) * 200.0))))


def _pin_rotation(rotation: float) -> int:
    """Snap a neutral pin angle to the 0/90/180/270 lib_add_pins wants."""
    return int(round((rotation % 360) / 90.0) * 90) % 360


def unsendable_in_plan(steps) -> list[tuple[str, str]]:
    """(field, offending characters) for every plan value the wire flattens.

    A plan step is ``{"tool", "args"}`` and its args carry the text an
    import writes into Altium. Anything above U+00FF is replaced with
    ``?`` on the way across, so naming it while the plan is still a plan
    is the last useful moment: afterwards the part exists and the field
    is simply wrong.

    Non-strings are skipped, since a coordinate cannot be flattened. A
    malformed step is tolerated rather than raising: a diagnostic that
    breaks the import it is diagnosing is worse than none.
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps or []:
        tool = (step or {}).get("tool", "?")
        for key, value in ((step or {}).get("args") or {}).items():
            if not isinstance(value, str):
                continue
            chars = unsendable_chars(value)
            if not chars:
                continue
            entry = (f"{tool}.{key}", chars)
            if entry not in seen:
                seen.add(entry)
                found.append(entry)
    return found


def build_altium_plan(
    comp: EasyEdaComponent,
    schlib_path: str,
    pcblib_path: str,
    *,
    symbol_name: Optional[str] = None,
    footprint_name: Optional[str] = None,
    include_body_art: bool = True,
    grid: Optional[int] = None,
    exact: bool = False,
) -> dict[str, Any]:
    """Ordered MCP-tool plan that recreates ``comp`` in Altium.

    Args:
        comp: normalized component from ``document.parse_component``.
        schlib_path: destination .SchLib (must exist or be creatable by
            the caller; the plan does not create libraries).
        pcblib_path: destination .PcbLib.
        symbol_name / footprint_name: override the names taken from the
            component.
        include_body_art: emit the symbol's rectangles / polylines /
            circles. Off gives pins only, which is enough when the body
            will be drawn by hand.
        grid: the snap grid, in mils, for the symbol's pins, body and
            text; 1 keeps the source's geometry exactly. Omitted, each
            library tool snaps to its own default grid.
        exact: keep the source's geometry: the footprint's lengths in
            millimetres as the source has them, rather than rounded to
            whole mils (a 0.8 mm drill would otherwise become 31 mil,
            0.787 mm), and a pin length of 0 as 0.

    Returns:
        ``{"ok": True, "steps": [...], "warnings": [...], "summary": {...}}``
    """
    steps: list[dict[str, Any]] = []
    warnings: list[str] = list(comp.warnings)

    sym_name = symbol_name or (comp.symbol.name if comp.symbol else "")
    fp_name = footprint_name or (comp.footprint.name if comp.footprint else "")

    # ---- symbol -------------------------------------------------------
    if comp.symbol is not None:
        # The library tools act on the ACTIVE document and the current
        # component, they take no library_path. Activating the target
        # .SchLib is therefore a required first step, not a nicety.
        steps.append({"tool": "app_set_active_document", "args": {
            "file_path": schlib_path,
        }})
        # A multi-part component (quad gate, dual op-amp) becomes a REAL
        # Altium multi-part symbol rather than N separate symbols the
        # user has to merge: part_count declares the sub-parts and each
        # pin names its owner below. Sub-part 0 is Altium's "shared by
        # every part", which is exactly what a source's shared-unit pins
        # mean.
        part_units = {getattr(s, "unit", 1) for s in comp.symbol.shapes
                      if s.kind == "pin"}
        part_count = max([u for u in part_units if u > 0] or [1])
        create_args: dict[str, Any] = {
            "name": sym_name,
            "designator_prefix": comp.symbol.prefix or "U",
            "description": comp.description or comp.mpn,
        }
        if part_count > 1:
            create_args["part_count"] = part_count
        steps.append({"tool": "lib_create_symbol", "args": create_args})

        # A sub-part whose pins are ALL power rails is a drafting
        # convention, not a functional stage: KiCad splits a dual
        # op-amp's V+/V- into their own unit. Altium can express the
        # same thing as pins SHARED by every part (owner_part_id 0),
        # which many libraries prefer. The source structure is kept
        # rather than reinterpreted, because both forms are legitimate
        # and only one of them is what the file actually says; the
        # choice is surfaced instead of made silently.
        for unit_id in sorted(u for u in part_units if u > 0):
            kinds = {PIN_ELECTRIC.get(s.electric, "undefined")
                     for s in comp.symbol.shapes
                     if s.kind == "pin" and getattr(s, "unit", 1) == unit_id}
            if kinds and kinds <= {"power"} and part_count > 1:
                warnings.append(
                    f"sub-part {unit_id} carries only power pins, which "
                    f"is how the source separates the supply rails. It "
                    f"is emitted as a real sub-part; if you would rather "
                    f"the rails appeared on every part, set those pins' "
                    f"owner_part_id to 0 and drop part_count to "
                    f"{part_count - 1}")

        # Pins that share a wire end in one part (a KiCad stack: six IOVDD
        # pins, one shown): every one of them is joined by the wire there.
        ends = Counter((getattr(s, "unit", 1), round(s.x), round(s.y))
                       for s in comp.symbol.shapes if s.kind == "pin")
        pins: list[dict[str, Any]] = []
        for s in comp.symbol.shapes:
            if s.kind != "pin":
                continue
            stacked = ends[(getattr(s, "unit", 1), round(s.x), round(s.y))] > 1
            # A neutral pin sits at its wire end and points into the body;
            # Altium's sits at the body end and points out to the wire end
            # (its electrical end is Location plus the length along the
            # orientation). Move one length inward and turn half a turn.
            # A source with no length gets 300 mil; an exact one keeps its
            # own, 0 included (a test point's pin is its connection dot).
            length = int(round(s.length)) if exact else (int(round(s.length)) or 300)
            inward = _pin_rotation(s.rotation)
            dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[inward]
            pins.append({
                "designator": s.number,
                "name": s.name or s.number,
                "x": int(round(s.x)) + dx * length,
                "y": int(round(s.y)) + dy * length,
                "rotation": (inward + 180) % 360,
                "length": length,
                "electrical_type": _ALTIUM_ELEC.get(
                    PIN_ELECTRIC.get(s.electric, "undefined"), "passive"),
            })
            # A hidden pin is electrically real; only its visibility is
            # carried across. Emitting it visible would add every NC and
            # supply rail the source deliberately hides. A hidden pin in a
            # stack stays visible: Altium joins a hidden pin to no wire,
            # only to its hidden net, so it would leave the stack's net.
            if not getattr(s, "display", True) and not stacked:
                pins[-1]["hidden"] = True
            if part_count > 1:
                pins[-1]["owner_part_id"] = getattr(s, "unit", 1)
            # The inversion bubble hangs off the OUTER edge of the pin and
            # the clock wedge off the INNER edge; they are independent, so
            # an inverted clock carries both. Dropping either one produces
            # a symbol that states the wrong thing rather than an
            # incomplete one: a pin drawn without its bubble reads as
            # active-high.
            if getattr(s, "dot", False):
                pins[-1]["symbol_outer_edge"] = "dot"
            if getattr(s, "clock", False):
                pins[-1]["symbol_inner_edge"] = "clock"
            # Label visibility. KiCad declares this once per symbol and
            # Altium stores it per pin, so the reader has already pushed
            # the flag down onto every pin. Only the False case is sent:
            # visible is Altium's default, and saying so explicitly would
            # add two fields to every pin of every symbol for no change
            # in what gets drawn.
            # A pin with no name takes its number as Altium's name, which
            # would then be drawn beside the pin: keep it hidden.
            if not getattr(s, "name_visible", True) or not s.name:
                pins[-1]["show_name"] = False
            if not getattr(s, "number_visible", True):
                pins[-1]["show_designator"] = False
        # Same filter on the pin side: a pin with no designator is
        # discarded by lib_add_pins without failing.
        unnamed_pins = [p for p in pins if not p["designator"]]
        pins = [p for p in pins if p["designator"]]
        if unnamed_pins:
            warnings.append(
                f"{len(unnamed_pins)} pin(s) carry no pin number and were "
                f"NOT emitted; lib_add_pins requires a designator and "
                f"silently discards blanks.")
        # The body goes in BEFORE the pins: Altium paints in the order
        # things were added, and a filled body added after the pins paints
        # over their names.
        if include_body_art:
            steps.extend(_symbol_art_steps(comp, schlib_path, sym_name,
                                           part_count=part_count, grid=grid))
        if pins:
            # Pins read from a library, not typed from a pin table: they
            # carry their own id so a new symbol takes them.
            from ...design.pin_table import IMPORTED
            import hashlib
            imported = "imported-" + hashlib.sha1(
                repr(pins).encode()).hexdigest()[:12]
            IMPORTED[imported] = len(pins)
            pin_args: dict[str, Any] = {"pins": pins, "datasheet": {"imported": imported}}
            if grid is not None:
                pin_args["grid"] = grid
            steps.append({"tool": "lib_add_pins", "args": pin_args})
        else:
            warnings.append("symbol has no pins")

        if include_body_art:
            texts = [s for s in comp.symbol.shapes
                     if s.kind == "text" and s.text]
            if texts:
                # Altium's primitive for free text on a symbol is an
                # ISch_Label, which lib_add_symbol_text now places. These
                # used to be dropped with a warning, which cost 2922
                # items across 72 of the installed KiCad libraries:
                # polarity marks, pin-group headings and NC annotations,
                # i.e. things that change what the symbol SAYS rather
                # than how it looks.
                items: list[dict[str, Any]] = []
                for t in texts:
                    for owner in _owners(t, part_count):
                        entry: dict[str, Any] = {
                            "text": t.text,
                            "x": int(round(t.x)),
                            "y": int(round(t.y)),
                            "rotation": _pin_rotation(getattr(t, "rotation", 0)),
                        }
                        if owner is not None:
                            entry["owner_part_id"] = owner
                        items.append(entry)
                text_args: dict[str, Any] = {"texts": items}
                if grid is not None:
                    text_args["grid"] = grid
                steps.append({"tool": "lib_add_symbol_text", "args": text_args})
                # Height is deliberately NOT sent. The source states it
                # in mils and the tool takes Altium's own font size, and
                # the relation between the two is not documented
                # anywhere this project can check. Reporting the range
                # is honest; inventing a factor would silently resize
                # every note and look like it had worked.
                heights = sorted({int(round(t.font_size)) for t in texts
                                  if getattr(t, "font_size", 0)})
                if heights:
                    warnings.append(
                        f"{len(texts)} symbol text item(s) were placed at "
                        f"the default font size; the source heights "
                        f"({heights[0]}-{heights[-1]} mils) were not "
                        f"mapped, because Altium's font size is not in "
                        f"mils and the conversion is not documented. "
                        f"Adjust by hand if the size matters.")

    # ---- footprint ----------------------------------------------------
    if comp.footprint is not None:
        steps.append({"tool": "app_set_active_document", "args": {
            "file_path": pcblib_path,
        }})
        steps.append({"tool": "lib_create_footprint", "args": {
            "name": fp_name,
            "description": comp.description or comp.mpn,
        }})

        L = _length(exact)
        pads: list[dict[str, Any]] = []
        apertures: list[Any] = []
        for s in comp.footprint.shapes:
            if s.kind != "pad":
                continue
            # A pad on a PASTE or MASK layer is a stencil aperture, not
            # copper. Altium's pad primitive is copper by definition, so
            # emitting one here would put metal where the source has
            # none -- shorting adjacent pads on the fine-pitch parts
            # that use paste subdivision.
            #
            # These are skipped on that ground rather than on the blank
            # designator they happen to carry. Every one of the 332 in
            # KiCad 10.0.1's sampled libraries is nameless, so the
            # existing no-designator guard catches them today, but that
            # is a coincidence of the corpus and not a property of an
            # aperture. One with a name would have become copper.
            if s.layer in (5, 6, 7, 8):
                apertures.append(s)
                continue
            pad: dict[str, Any] = {
                "designator": s.number,
                "x": L(s.cx),
                "y": L(s.cy),
                "x_size": L(s.width),
                "y_size": L(s.height),
                "shape": _ALTIUM_PAD_SHAPE.get(s.shape, "round"),
                # A drilled pad is forced through-hole by the tool, so
                # only an SMD pad's layer is meaningful.
                "layer": "BottomLayer" if s.layer == 2 else "TopLayer",
                "hole_size": L(s.hole_radius * 2),
                "rotation": float(s.rotation or 0),
            }
            if pad["shape"] == "roundrect":
                pad["corner_radius"] = _corner_radius_pct(
                    getattr(s, "corner_ratio", 0.0))
            if s.is_through_hole and s.is_slot:
                pad["hole_length"] = L(s.hole_length)
            unplated = s.is_through_hole and not s.plated
            if unplated:
                pad["plated"] = False
            # lib_add_footprint_pads DROPS a plated pad with a blank
            # designator (counted as skipped_invalid, not an error), so
            # a numberless copper pad would vanish with nothing to
            # notice. An unplated hole may have none: it joins no net.
            if not pad["designator"] and not unplated:
                # Copper with no number, a fiducial: kept, with no net.
                pad["unnumbered"] = True
            pads.append(pad)
        # A bare hole (mounting, tooling) is an unplated pad with no
        # designator: a designator would make it a pad a net can join.
        for h in comp.footprint.shapes:
            if h.kind != "hole" or not getattr(h, "diameter", 0):
                continue
            d = L(h.diameter)
            pads.append({"designator": "", "x": L(h.cx), "y": L(h.cy),
                         "x_size": d, "y_size": d, "shape": "round",
                         "layer": "TopLayer", "hole_size": d, "rotation": 0.0,
                         "plated": False})
        # A paste or mask opening with no copper is a region on its own
        # layer: an Altium pad always carries copper, and one here would
        # short the pads the opening subdivides.
        aperture_regions = [{"layer": _ALTIUM_LAYER[lay],
                             "points": [[L(x), L(y)] for x, y in _pad_outline(a)]}
                            for a in apertures
                            for lay in (getattr(a, "aperture_layers", None) or [a.layer])]
        # A custom-shaped pad: its anchor is the pad above; the rest of its
        # copper, and the mask and paste openings over it, are regions in
        # the exact outline.
        for s in comp.footprint.shapes:
            if s.kind != "pad" or not getattr(s, "custom_outlines", None):
                continue
            copper = ["TopLayer", "BottomLayer"] if s.layer == 11 else \
                [_ALTIUM_LAYER.get(s.layer, "TopLayer")]
            layers = copper + [_ALTIUM_LAYER[lay] for lay in getattr(s, "aperture_layers", [])]
            for outline in s.custom_outlines:
                for lay in layers:
                    aperture_regions.append({"layer": lay,
                                             "points": [[L(x), L(y)] for x, y in outline]})

        if pads:
            # Lands read from a vendor library, not drawn by hand: they
            # carry their own id so a new footprint takes them.
            from ...design.landpattern.compare_figure import ISSUED
            import hashlib
            imported = "imported-" + hashlib.sha1(
                repr((fp_name, pads)).encode()).hexdigest()[:12]
            ISSUED[imported] = len(pads)
            pad_args: dict[str, Any] = {"pads": pads, "compared": imported}
            if exact:
                pad_args["units"] = "mm"
            steps.append({"tool": "lib_add_footprint_pads", "args": pad_args})
        else:
            warnings.append("footprint has no pads")

        art = _footprint_art_steps(comp, pcblib_path, fp_name, warnings, L=L)
        if aperture_regions:
            art.append({"tool": "lib_add_footprint_regions",
                        "args": {"regions": aperture_regions}})
        if exact:
            for step in art:
                step["args"]["units"] = "mm"
        steps.extend(art)

    # ---- 3D body ------------------------------------------------------
    # Only when the caller resolved the reference to a real STEP file on
    # this machine. lib_link_3d_model loads the geometry, so a path that
    # does not exist would fail at execution time, and a guessed one
    # would attach the wrong shape. This runs BEFORE the schematic-side
    # linking below because it needs the .PcbLib still active.
    model_path = getattr(comp.footprint, "model_3d_path", "") \
        if comp.footprint is not None else ""
    if model_path:
        steps.append({"tool": "lib_link_3d_model", "args": {
            "component_name": fp_name,
            "model_path": model_path,
        }})
    elif comp.footprint is not None and getattr(
            comp.footprint, "model_3d_ref", ""):
        warnings.append(
            f"the footprint names a 3D model "
            f"({comp.footprint.model_3d_ref}) that was not resolved to a "
            f"file here, so no 3D body was linked; pass a resolved STEP "
            f"path to lib_link_3d_model by hand")

    # ---- linking ------------------------------------------------------
    if comp.symbol is not None and comp.footprint is not None:
        # Linking is a schematic-side edit, so the .SchLib has to be
        # active again after the footprint work.
        steps.append({"tool": "app_set_active_document", "args": {
            "file_path": schlib_path,
        }})
        steps.append({"tool": "lib_link_footprint", "args": {
            "component_name": sym_name,
            "footprint_name": fp_name,
            "footprint_library": pcblib_path,
        }})

    if comp.footprint is not None and comp.footprint.model_3d_uuid:
        warnings.append(
            "a 3D model is referenced by uuid; fetch it separately and "
            "attach with lib_link_3d_model (EasyEDA serves OBJ, Altium "
            "wants STEP, so a conversion may be required)")

    # Name the text the bridge will flatten. Altium's DelphiScript
    # strings are single byte and UnescapeJsonString emits '?' for any
    # codepoint above 255, so an LCSC description in Chinese imports as
    # question marks with nothing reporting it.
    #
    # This lives in the shared plan builder rather than in either import
    # tool: lib_easyeda_import and lib_kicad_import both call it, and
    # putting the scan in one of them is how the two drift apart.
    for field, chars in unsendable_in_plan(steps):
        warnings.append(
            f"{field} contains characters the bridge cannot carry "
            f"({chars}); Altium will receive '?' for each of them")

    return {
        "ok": True,
        "steps": steps,
        "warnings": warnings,
        "summary": {
            "symbol": sym_name or None,
            "footprint": fp_name or None,
            "pin_count": sum(
                len(s["args"]["pins"]) for s in steps
                if s["tool"] == "lib_add_pins"),
            "pad_count": sum(
                len(s["args"]["pads"]) for s in steps
                if s["tool"] == "lib_add_footprint_pads"),
            "step_count": len(steps),
        },
    }


def _length(exact: bool):
    """A neutral length (mils) as a footprint tool takes it: whole mils,
    or with ``exact`` millimetres unrounded (the step then says so)."""
    if exact:
        return lambda v: round(float(v) * 0.0254, 6)
    return lambda v: int(round(v))


def _pad_outline(pad) -> list[tuple[float, float]]:
    """A pad's outline as a polygon in the footprint frame (mils): its
    rectangle, or 32 points round a circle or an oval's ends, turned by
    its rotation."""
    w, h = float(pad.width), float(pad.height)
    if str(getattr(pad, "shape", "")).upper() in ("ELLIPSE", "OVAL", "ROUND", "CIRCLE"):
        r = min(w, h) / 2.0
        dx, dy = (w / 2.0 - r, 0.0) if w >= h else (0.0, h / 2.0 - r)
        pts = []
        for i in range(32):
            a = 2 * math.pi * i / 32
            sx = dx if math.cos(a) >= 0 else -dx
            sy = dy if math.sin(a) >= 0 else -dy
            pts.append((sx + r * math.cos(a), sy + r * math.sin(a)))
    else:
        pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    t = math.radians(float(getattr(pad, "rotation", 0) or 0))
    c, s = math.cos(t), math.sin(t)
    return [(pad.cx + x * c - y * s, pad.cy + x * s + y * c) for x, y in pts]


def _ccw(arc) -> tuple[float, float]:
    """An arc's ends as Altium draws them: counter-clockwise from start to
    end. A clockwise arc (end before start) is the same curve from its
    other end."""
    s, e = arc.start_angle, arc.end_angle
    if e < s:
        s, e = e, s
    return round(s % 360.0, 3), round(e % 360.0 if e - s < 360.0 else s % 360.0 + 360.0, 3)


def _owners(shape, part_count: int) -> list[Optional[int]]:
    """The parts a symbol primitive goes on: None for a one-part symbol
    (the current part), its own unit, or every part for unit 0 (art the
    source shares across units)."""
    if part_count <= 1:
        return [None]
    unit = int(getattr(shape, "unit", 0) or 0)
    return [unit] if unit > 0 else list(range(1, part_count + 1))


def _symbol_art_steps(
    comp: EasyEdaComponent, schlib_path: str, sym_name: str,
    part_count: int = 1, grid: Optional[int] = None,
) -> list[dict[str, Any]]:
    # These tools take neither library_path nor component_name: they act
    # on the current symbol, which lib_create_symbol has just made
    # current. The caller must keep the emitted step ORDER.
    steps: list[dict[str, Any]] = []
    # The body first: Altium paints in the order things are added, while
    # KiCad paints a filled background behind everything else wherever it
    # sits in the file (a DIP switch keeps its body after its switches).
    # Every rectangle, then other filled shapes, then the drawing.
    shapes = sorted(comp.symbol.shapes,
                    key=lambda s: 0 if s.kind == "rect" else 1 if _is_filled(s) else 2)
    for s in shapes:
        if s.kind == "pin" or s.kind == "text":
            continue
        for owner in _owners(s, part_count):
            for step in _symbol_art_shape(s):
                args = step["args"]
                if owner is not None:
                    if "lines" in args:
                        for line in args["lines"]:
                            line["owner_part_id"] = owner
                    else:
                        args["owner_part_id"] = owner
                if grid is not None:
                    args["grid"] = grid
                steps.append(step)
    return steps


def _is_filled(s) -> bool:
    """A symbol polygon drawn filled: a background a later outline sits on."""
    fill = getattr(s, "fill", None)
    return s.kind == "polygon" and str(fill or "").lower() not in ("", "none")


def _symbol_art_shape(s) -> list[dict[str, Any]]:
    """The library-tool steps that draw one symbol primitive."""
    steps: list[dict[str, Any]] = []
    if s.kind == "rect":
        rect: dict[str, Any] = {
            "x1": int(round(s.x)), "y1": int(round(s.y)),
            "x2": int(round(s.x + s.width)),
            "y2": int(round(s.y + s.height))}
        # A source that says whether the body is filled is followed;
        # one that does not leaves Altium's default.
        if isinstance(getattr(s, "fill", None), bool):
            rect["is_solid"] = s.fill
        steps.append({"tool": "lib_add_symbol_rectangle", "args": rect})
    elif s.kind in ("polyline", "polygon") and len(s.points) >= 2:
        pts = [(int(round(x)), int(round(y))) for x, y in s.points]
        if s.kind == "polygon":
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            if len(pts) >= 3:
                # vertices is a flat comma-separated string, not a list
                # of pairs.
                steps.append({
                    "tool": "lib_add_symbol_polygon",
                    "args": {"vertices": ",".join(f"{x},{y}" for x, y in pts)}})
        else:
            # Same closure rule as the footprint tracks below: the model
            # field means the same thing on both sides.
            if getattr(s, "closed", False) and len(pts) >= 3 and pts[0] != pts[-1]:
                pts.append(pts[0])
            lines = [{"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}
                     for a, b in zip(pts, pts[1:])]
            steps.append({"tool": "lib_add_symbol_lines", "args": {"lines": lines}})
    elif s.kind in ("circle", "ellipse") and s.radius > 0:
        steps.append({"tool": "lib_add_symbol_arc", "args": {
            "x_center": int(round(s.cx)), "y_center": int(round(s.cy)),
            "radius": int(round(s.radius)),
            "start_angle": 0.0, "end_angle": 360.0}})
    elif s.kind == "arc" and getattr(s, "is_valid", False):
        arc = svg_arc_to_center(s.x1, s.y1, s.rx, s.ry, s.rotation,
                                s.large_arc, s.sweep, s.x2, s.y2)
        if arc is not None:
            steps.append({"tool": "lib_add_symbol_arc", "args": {
                "x_center": int(round(arc.cx)),
                "y_center": int(round(arc.cy)),
                # Altium symbol arcs are circular; an ellipse is
                # approximated by its mean radius.
                "radius": int(round((arc.rx + arc.ry) / 2.0)),
                "start_angle": _ccw(arc)[0],
                "end_angle": _ccw(arc)[1]}})
    return steps


def _is_real_region(shape) -> bool:
    """Whether a SOLIDREGION is drawing to reproduce.

    The same rule the KiCad emitter applies (``_is_real_pour``): not a
    ``fill="cutout"``, and on a layer this importer maps. Real EasyEDA
    footprints carry a region per pad on layer 100 (lead outlines) and the
    body outline on layer 99, neither in the documented map; an RP2040
    has 114 and 2. Drawn here through the TopOverlay fallback, each lead
    outline became a silkscreen ring over its pad (#39.22).
    """
    if str(getattr(shape, "fill", "") or "").lower() == "cutout":
        return False
    return getattr(shape, "layer", None) in _ALTIUM_LAYER


def _footprint_art_steps(
    comp: EasyEdaComponent, pcblib_path: str, fp_name: str,
    warnings: Optional[list[str]] = None,
    L=None,
) -> list[dict[str, Any]]:
    # Like the symbol art, these act on the CURRENT footprint.
    L = L or _length(False)
    steps: list[dict[str, Any]] = []
    warned_elliptical: list[str] = []

    tracks: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    skipped_regions = 0
    for s in comp.footprint.shapes:
        layer = _ALTIUM_LAYER.get(getattr(s, "layer", 3), "TopOverlay")
        if s.kind == "solid_region" and not _is_real_region(s):
            skipped_regions += 1
            continue
        if (s.kind == "solid_region" and len(s.points) >= 3
                and str(getattr(s, "fill", "") or "").lower() == "solid"):
            # Filled, as the source draws it: lib_add_footprint_regions
            # places a region primitive inside the footprint. A polygon
            # the source leaves unfilled is drawn as its closed outline
            # below.
            regions.append({
                "layer": layer,
                "points": [[L(px), L(py)] for px, py in s.points]})
            continue
        if s.kind in ("track", "polyline", "solid_region") \
                and len(s.points) >= 2:
            pts = list(s.points)
            # A closed shape's last edge runs back to the first point.
            # The model stores that closure IMPLICITLY (the repeated
            # final vertex is normalised away on read), so walking
            # consecutive pairs alone emits every edge except the
            # closing one and leaves a notch in an outline that is
            # meant to be sealed.
            if getattr(s, "closed", False) and len(pts) >= 3 \
                    and pts[0] != pts[-1]:
                pts.append(pts[0])
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                tracks.append({
                    "x1": L(x1), "y1": L(y1), "x2": L(x2), "y2": L(y2),
                    "width": L(s.stroke_width) or L(6),
                    "layer": layer,
                })
        elif s.kind == "rect":
            x1, y1 = L(s.x), L(s.y)
            x2 = L(s.x + s.width)
            y2 = L(s.y + s.height)
            w = L(s.stroke_width) or L(6)
            for a, b in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                         ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
                tracks.append({"x1": a[0], "y1": a[1],
                               "x2": b[0], "y2": b[1],
                               "width": w, "layer": layer})
        elif s.kind == "circle" and s.radius > 0:
            steps.append({"tool": "lib_add_footprint_arc", "args": {
                "x_center": L(s.cx),
                "y_center": L(s.cy),
                "radius": L(s.radius),
                "start_angle": 0.0, "end_angle": 360.0,
                "width": L(s.stroke_width) or L(6),
                "layer": layer}})
        elif s.kind == "arc" and s.is_valid:
            arc = svg_arc_to_center(s.x1, s.y1, s.rx, s.ry, s.rotation,
                                    s.large_arc, s.sweep, s.x2, s.y2)
            if arc is not None:
                # Altium arcs are circular; an elliptical source is
                # approximated by its mean radius, so say so rather than
                # let a squashed outline pass as faithful.
                if not arc.is_circular:
                    warned_elliptical.append(fp_name)
                steps.append({"tool": "lib_add_footprint_arc", "args": {
                    "x_center": L(arc.cx),
                    "y_center": L(arc.cy),
                    "radius": L((arc.rx + arc.ry) / 2.0),
                    "start_angle": _ccw(arc)[0],
                    "end_angle": _ccw(arc)[1],
                    "width": L(s.stroke_width) or L(6),
                    "layer": layer}})
        elif s.kind == "text" and s.text and s.visible:
            text_args: dict[str, Any] = {
                "x": L(s.x), "y": L(s.y),
                "text": s.text,
                # the tool calls this "size", not "height"
                "size": L(s.font_size) or L(60),
                "rotation": int(round(s.rotation)) % 360,
                "layer": layer}
            # Stroke width is what makes text legible at a given height;
            # the tool's default of 8 mils is a fixed guess that reads
            # heavy under small text and thin under large. Sent only when
            # the source states it, so nothing changes for a source that
            # does not.
            if int(round(getattr(s, "stroke_width", 0) or 0)) > 0:
                text_args["width"] = L(s.stroke_width)
            # Text on a bottom-side layer has to be mirrored or it reads
            # backwards once the board is made. This is not a preference:
            # audit_find_mirrored_pcb_text reports unmirrored bottom
            # overlay text as a violation, so emitting it plain means
            # this importer produces libraries our own audit rejects.
            # The layer decides, because it is the physical fact; a
            # source flag is honoured only where the layer leaves the
            # question open.
            if getattr(s, "layer", 3) in _BOTTOM_SIDE_LAYERS:
                text_args["mirror"] = True
            elif getattr(s, "mirror", False):
                text_args["mirror"] = True
            steps.append({"tool": "lib_add_footprint_text",
                          "args": text_args})

    if warned_elliptical and warnings is not None:
        warnings.append(
            f"{len(warned_elliptical)} elliptical arc(s) approximated by "
            f"their mean radius; Altium arcs are circular. Check the "
            f"silkscreen against the datasheet outline.")
    if regions:
        steps.insert(0, {"tool": "lib_add_footprint_regions",
                         "args": {"regions": regions}})
    if tracks:
        # One bulk call: lib_add_footprint_tracks exists precisely so a
        # silkscreen outline is not N round trips.
        steps.insert(0, {"tool": "lib_add_footprint_tracks",
                         "args": {"tracks": tracks}})
    if skipped_regions and warnings is not None:
        warnings.append(
            f"{skipped_regions} region(s) not drawn: cutouts, or on EasyEDA "
            f"layers with no Altium counterpart (99 is the body outline, 100 "
            f"the outline of each lead). Drawn, they became silkscreen rings "
            f"over the pads.")
    return steps
