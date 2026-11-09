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

        pins: list[dict[str, Any]] = []
        for s in comp.symbol.shapes:
            if s.kind != "pin":
                continue
            pins.append({
                "designator": s.number,
                "name": s.name or s.number,
                "x": int(round(s.x)),
                "y": int(round(s.y)),
                "rotation": _pin_rotation(s.rotation),
                "length": int(round(s.length)) or 300,
                "electrical_type": _ALTIUM_ELEC.get(
                    PIN_ELECTRIC.get(s.electric, "undefined"), "passive"),
            })
            # A hidden pin is electrically real; only its visibility is
            # carried across. Emitting it visible would add every NC and
            # supply rail the source deliberately hides.
            if not getattr(s, "display", True):
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
            if not getattr(s, "name_visible", True):
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
        if pins:
            # Pins read from a library, not typed from a pin table: they
            # carry their own id so a new symbol takes them.
            from ...design.pin_table import IMPORTED
            import hashlib
            imported = "imported-" + hashlib.sha1(
                repr(pins).encode()).hexdigest()[:12]
            IMPORTED[imported] = len(pins)
            steps.append({"tool": "lib_add_pins",
                          "args": {"pins": pins, "datasheet": {"imported": imported}}})
        else:
            warnings.append("symbol has no pins")

        if include_body_art:
            steps.extend(_symbol_art_steps(comp, schlib_path, sym_name))
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
                    entry: dict[str, Any] = {
                        "text": t.text,
                        "x": int(round(t.x)),
                        "y": int(round(t.y)),
                        "rotation": _pin_rotation(getattr(t, "rotation", 0)),
                    }
                    if part_count > 1:
                        entry["owner_part_id"] = getattr(t, "unit", 1)
                    items.append(entry)
                steps.append({"tool": "lib_add_symbol_text",
                              "args": {"texts": items}})
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

        pads: list[dict[str, Any]] = []
        unnamed_pads: list[dict[str, Any]] = []
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
                "x": int(round(s.cx)),
                "y": int(round(s.cy)),
                "x_size": int(round(s.width)),
                "y_size": int(round(s.height)),
                "shape": _ALTIUM_PAD_SHAPE.get(s.shape, "round"),
                # A drilled pad is forced through-hole by the tool, so
                # only an SMD pad's layer is meaningful.
                "layer": "BottomLayer" if s.layer == 2 else "TopLayer",
                "hole_size": int(round(s.hole_radius * 2)),
                "rotation": float(s.rotation or 0),
            }
            if pad["shape"] == "roundrect":
                pad["corner_radius"] = _corner_radius_pct(
                    getattr(s, "corner_ratio", 0.0))
            # lib_add_footprint_pads DROPS any pad with a blank
            # designator (counted as skipped_invalid, not an error), so
            # a numberless pad would vanish with nothing to notice.
            if pad["designator"]:
                pads.append(pad)
            else:
                unnamed_pads.append(pad)
        # An unplated HOLE (mounting hole, tooling hole) cannot be
        # expressed here. Emitting it as a pad does NOT work:
        # lib_add_footprint_pads drops any pad whose designator is empty
        # (counted as skipped_invalid), so the step would vanish
        # silently. Giving it a designator is worse, not better: in
        # Altium a designator makes the pad connectable, so a mounting
        # hole would show up as a real net-joinable pad.
        if unnamed_pads:
            spots = ", ".join(f"({p['x']},{p['y']})"
                              for p in unnamed_pads[:4])
            warnings.append(
                f"{len(unnamed_pads)} pad(s) at {spots} carry no pad "
                f"number and were NOT emitted; lib_add_footprint_pads "
                f"requires a designator and silently discards blanks. "
                f"Add them by hand if they are real copper.")

        if apertures:
            spots = ", ".join(
                f"({int(round(a.cx))},{int(round(a.cy))})"
                for a in apertures[:4])
            warnings.append(
                f"{len(apertures)} solder-paste / mask APERTURE(s) at "
                f"{spots} were NOT emitted. They carry no copper, and "
                f"an Altium pad always does, so adding them as pads "
                f"would short the pads they subdivide. Draw them as "
                f"regions on the paste or mask layer by hand if the "
                f"stencil needs them.")

        # A SLOTTED drill becomes a round one here: the pad payload
        # carries a single hole_size and has no slot length. The
        # resulting hole is the right diameter and the wrong shape, so
        # a part with a rectangular lead will not fit, and nothing
        # downstream would reveal it.
        slots = [s for s in comp.footprint.shapes
                 if s.kind == "pad" and getattr(s, "is_slot", False)]
        if slots:
            spots = ", ".join(
                f"{s.number or '?'} at ({int(round(s.cx))},"
                f"{int(round(s.cy))})" for s in slots[:4])
            warnings.append(
                f"{len(slots)} pad(s) have a SLOTTED hole ({spots}) which "
                f"was emitted as a ROUND hole of the same width; this "
                f"API has no slot length. Edit the hole shape by hand, or "
                f"a rectangular lead will not fit.")

        # Same for plating: an unplated pad is emitted as a normal
        # plated one, which puts copper in a hole meant to have none.
        unplated = [s for s in comp.footprint.shapes
                    if s.kind == "pad" and s.number
                    and getattr(s, "is_through_hole", False)
                    and not getattr(s, "plated", True)]
        if unplated:
            spots = ", ".join(
                f"{s.number} at ({int(round(s.cx))},{int(round(s.cy))})"
                for s in unplated[:4])
            warnings.append(
                f"{len(unplated)} pad(s) are UNPLATED in the source "
                f"({spots}) but were emitted as ordinary plated pads; "
                f"this API cannot set plating. Clear the plating by hand "
                f"if the hole is meant to be bare.")

        holes = [s for s in comp.footprint.shapes if s.kind == "hole"]
        if holes:
            spots = ", ".join(f"({int(round(h.cx))},{int(round(h.cy))})"
                              for h in holes[:4])
            warnings.append(
                f"{len(holes)} unplated hole(s) at {spots} were NOT "
                f"created; this API has no NPTH primitive (a pad needs a "
                f"designator, which would make the hole connectable). "
                f"Add them by hand, or the board will not be drilled for "
                f"them.")
        if pads:
            # Lands read from a vendor library, not drawn by hand: they
            # carry their own id so a new footprint takes them.
            from ...design.landpattern.compare_figure import ISSUED
            import hashlib
            imported = "imported-" + hashlib.sha1(
                repr((fp_name, pads)).encode()).hexdigest()[:12]
            ISSUED[imported] = len(pads)
            steps.append({"tool": "lib_add_footprint_pads",
                          "args": {"pads": pads, "compared": imported}})
        else:
            warnings.append("footprint has no pads")

        steps.extend(_footprint_art_steps(
            comp, pcblib_path, fp_name, warnings))

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


def _symbol_art_steps(
    comp: EasyEdaComponent, schlib_path: str, sym_name: str,
) -> list[dict[str, Any]]:
    # These tools take neither library_path nor component_name: they act
    # on the current symbol, which lib_create_symbol has just made
    # current. The caller must keep the emitted step ORDER.
    steps: list[dict[str, Any]] = []

    for s in comp.symbol.shapes:
        if s.kind == "rect":
            steps.append({"tool": "lib_add_symbol_rectangle", "args": {
                "x1": int(round(s.x)), "y1": int(round(s.y)),
                "x2": int(round(s.x + s.width)),
                "y2": int(round(s.y + s.height))}})
        elif s.kind in ("polyline", "polygon") and len(s.points) >= 2:
            pts = [(int(round(x)), int(round(y))) for x, y in s.points]
            if s.kind == "polygon":
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                if len(pts) >= 3:
                    # vertices is a flat comma-separated string, not a
                    # list of pairs.
                    steps.append({
                        "tool": "lib_add_symbol_polygon",
                        "args": {"vertices": ",".join(
                            f"{x},{y}" for x, y in pts)}})
            else:
                # Same closure rule as the footprint tracks below. The
                # symbol reader happens to keep the repeated vertex, so
                # this is currently a no-op there, but the model field
                # means the same thing on both sides and honouring it in
                # only one of them is how the footprint path came to
                # drop its closing edge.
                if getattr(s, "closed", False) and len(pts) >= 3 \
                        and pts[0] != pts[-1]:
                    pts.append(pts[0])
                lines = [{"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}
                         for a, b in zip(pts, pts[1:])]
                steps.append({"tool": "lib_add_symbol_lines",
                              "args": {"lines": lines}})
        elif s.kind in ("circle", "ellipse") and s.radius > 0:
            steps.append({"tool": "lib_add_symbol_arc", "args": {
                "x_center": int(round(s.cx)), "y_center": int(round(s.cy)),
                "radius": int(round(s.radius)),
                "start_angle": 0.0, "end_angle": 360.0}})
        elif s.kind == "arc" and getattr(s, "is_valid", False):
            # Symbol arcs were silently dropped: the footprint path
            # handled them but this one never had a branch, so curved
            # symbol art vanished with no warning even though
            # lib_add_symbol_arc exists.
            arc = svg_arc_to_center(s.x1, s.y1, s.rx, s.ry, s.rotation,
                                    s.large_arc, s.sweep, s.x2, s.y2)
            if arc is not None:
                steps.append({"tool": "lib_add_symbol_arc", "args": {
                    "x_center": int(round(arc.cx)),
                    "y_center": int(round(arc.cy)),
                    # Altium symbol arcs are circular; an ellipse is
                    # approximated by its mean radius.
                    "radius": int(round((arc.rx + arc.ry) / 2.0)),
                    "start_angle": round(arc.start_angle, 3),
                    "end_angle": round(arc.end_angle, 3)}})
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
) -> list[dict[str, Any]]:
    # Like the symbol art, these act on the CURRENT footprint.
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
                "points": [[int(round(px)), int(round(py))]
                           for px, py in s.points]})
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
                    "x1": int(round(x1)), "y1": int(round(y1)),
                    "x2": int(round(x2)), "y2": int(round(y2)),
                    "width": int(round(s.stroke_width)) or 6,
                    "layer": layer,
                })
        elif s.kind == "rect":
            x1, y1 = int(round(s.x)), int(round(s.y))
            x2 = int(round(s.x + s.width))
            y2 = int(round(s.y + s.height))
            w = int(round(s.stroke_width)) or 6
            for a, b in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                         ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
                tracks.append({"x1": a[0], "y1": a[1],
                               "x2": b[0], "y2": b[1],
                               "width": w, "layer": layer})
        elif s.kind == "circle" and s.radius > 0:
            steps.append({"tool": "lib_add_footprint_arc", "args": {
                "x_center": int(round(s.cx)),
                "y_center": int(round(s.cy)),
                "radius": int(round(s.radius)),
                "start_angle": 0.0, "end_angle": 360.0,
                "width": int(round(s.stroke_width)) or 6,
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
                    "x_center": int(round(arc.cx)),
                    "y_center": int(round(arc.cy)),
                    "radius": int(round((arc.rx + arc.ry) / 2.0)),
                    "start_angle": round(arc.start_angle, 3),
                    "end_angle": round(arc.end_angle, 3),
                    "width": int(round(s.stroke_width)) or 6,
                    "layer": layer}})
        elif s.kind == "text" and s.text and s.visible:
            text_args: dict[str, Any] = {
                "x": int(round(s.x)), "y": int(round(s.y)),
                "text": s.text,
                # the tool calls this "size", not "height"
                "size": int(round(s.font_size)) or 60,
                "rotation": int(round(s.rotation)) % 360,
                "layer": layer}
            # Stroke width is what makes text legible at a given height;
            # the tool's default of 8 mils is a fixed guess that reads
            # heavy under small text and thin under large. Sent only when
            # the source states it, so nothing changes for a source that
            # does not.
            if int(round(getattr(s, "stroke_width", 0) or 0)) > 0:
                text_args["width"] = int(round(s.stroke_width))
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
