# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Reading KiCad libraries into the neutral model.

This exists to close a real gap: the part providers surface plenty of
KiCad parts, but the server could only export Altium TO KiCad, never
back, so those hits were a dead end for an Altium user.

The reader parses into the SAME neutral model the EasyEDA importer
produces, so ``build_altium_plan`` is shared rather than duplicated.
That is what these tests pin, along with the three conversions that are
each easy to get backwards and easy to miss:

* millimetres to mils
* Y axis: ``.kicad_sym`` is Y-UP (no flip), ``.kicad_mod`` is Y-DOWN
  (negate). A sign error mirrors a footprint, which still looks like a
  plausible part.
* pin angle: KiCad's convention already matches the neutral model, so
  it passes through unchanged. EasyEDA's is 180 degrees off, and that
  asymmetry is exactly the sort of thing that gets "helpfully" made
  consistent by someone who has not checked.
"""

from __future__ import annotations

import math
import re

import pytest

from eda_agent.libimport.kicad.reader import (
    MM_TO_MIL,
    read_kicad_footprint,
    read_kicad_symbol,
)
from eda_agent.libimport.kicad.sexpr import dumps, loads

# --------------------------- s-expressions ---------------------------


@pytest.mark.parametrize("text", [
    '(footprint "2.5\\" header" (layer "F.Cu"))',   # escaped quote
    '(descr "resistor (see note) 1%")',             # parens inside a string
    '(property "Ref" "R" (at 0 0 0))',
    '(pin passive line (at -5.08 0 0) (length 2.54))',
])
def test_sexpr_round_trips(text):
    """Escaping is where a hand-rolled reader usually breaks.

    A footprint named ``2.5"`` or a description containing ``(see
    note)`` defeats a split-on-whitespace parser.
    """
    once = dumps(loads(text))
    assert dumps(loads(once)) == once


def test_sexpr_rejects_malformed_input():
    """Half-parsed is worse than refused: it looks like a valid library."""
    for bad in ('(unterminated "string', "(unclosed list", "not a list"):
        with pytest.raises(ValueError):
            loads(bad)


# ------------------------------ symbols ------------------------------

_SYM = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "TESTPART" (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 0 0))
    (property "Datasheet" "https://example.invalid/ds.pdf" (at 0 0 0))
    (symbol "TESTPART_0_1"
      (rectangle (start -5.08 5.08) (end 5.08 -5.08)
        (stroke (width 0) (type default)) (fill (type background)))
      (circle (center 0 0) (radius 2.54)
        (stroke (width 0) (type default)) (fill (type none)))
    )
    (symbol "TESTPART_1_1"
      (pin input line (at -7.62 2.54 0) (length 2.54)
        (name "IN") (number "1"))
      (pin output line (at 7.62 2.54 180) (length 2.54)
        (name "OUT") (number "2"))
    )
  )
)'''


def test_symbol_units_convert_mm_to_mils():
    comp = read_kicad_symbol(_SYM)
    pins = {p.number: p for p in comp.symbol.shapes if p.kind == "pin"}
    # -7.62 mm is exactly -300 mil.
    assert pins["1"].x == pytest.approx(-300.0)
    assert pins["1"].y == pytest.approx(100.0)
    assert pins["1"].length == pytest.approx(100.0)


def test_symbol_y_axis_is_not_flipped():
    """.kicad_sym is Y-UP already, like the neutral frame.

    Negating here would flip every symbol vertically, which still draws
    as a plausible part and is easy to miss by eye.
    """
    comp = read_kicad_symbol(_SYM)
    pins = {p.number: p for p in comp.symbol.shapes if p.kind == "pin"}
    # KiCad y=+2.54 must stay POSITIVE in the neutral model.
    assert pins["1"].y > 0


def test_symbol_pin_angle_passes_through():
    """KiCad's pin angle already matches the neutral convention.

    EasyEDA's is 180 degrees off; do not "make them consistent".
    """
    comp = read_kicad_symbol(_SYM)
    pins = {p.number: p for p in comp.symbol.shapes if p.kind == "pin"}
    assert pins["1"].rotation == 0.0
    assert pins["2"].rotation == 180.0


def test_symbol_reads_body_art_from_nested_units():
    """Art lives in NAME_0_1 sub-symbols, not on the parent."""
    comp = read_kicad_symbol(_SYM)
    kinds = {s.kind for s in comp.symbol.shapes}
    assert "rect" in kinds and "circle" in kinds


def test_symbol_metadata_extracted():
    comp = read_kicad_symbol(_SYM)
    assert comp.symbol.prefix == "U"
    assert comp.datasheet.endswith("ds.pdf")


def test_named_symbol_selected_from_a_multi_symbol_library():
    lib = _SYM.replace("(symbol \"TESTPART\"",
                       "(symbol \"OTHER\" (property \"Reference\" \"R\" "
                       "(at 0 0 0))) (symbol \"TESTPART\"", 1)
    comp = read_kicad_symbol(lib, name="TESTPART")
    assert comp.symbol.name == "TESTPART"


# ---------------------------- footprints -----------------------------

_MOD = '''(footprint "TESTFP" (version 20251024) (layer "F.Cu")
  (pad "1" smd rect (at -2.54 1.27) (size 1.27 0.635)
    (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" thru_hole circle (at 2.54 1.27) (size 1.524 1.524)
    (drill 0.762) (layers "*.Cu" "*.Mask"))
  (fp_line (start -5 -5) (end 5 -5) (stroke (width 0.12) (type solid))
    (layer "F.SilkS"))
  (fp_circle (center 0 0) (end 1 0) (stroke (width 0.12) (type solid))
    (layer "F.SilkS"))
  (fp_text user "MARK" (at 0 3) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
)'''


def test_footprint_y_axis_is_flipped():
    """.kicad_mod is Y-DOWN while the neutral frame is Y-UP.

    Missing this mirrors the footprint, and a mirrored land pattern is
    a board that cannot be assembled.
    """
    comp = read_kicad_footprint(_MOD)
    pads = {p.number: p for p in comp.footprint.shapes if p.kind == "pad"}
    # KiCad y=+1.27 (downward) must become NEGATIVE in the neutral frame.
    assert pads["1"].cy == pytest.approx(-50.0)
    assert pads["1"].cx == pytest.approx(-100.0)


def test_footprint_drill_becomes_a_radius():
    """The neutral model stores a hole RADIUS, KiCad gives a diameter."""
    comp = read_kicad_footprint(_MOD)
    pads = {p.number: p for p in comp.footprint.shapes if p.kind == "pad"}
    assert pads["2"].is_through_hole
    assert pads["2"].hole_radius == pytest.approx(0.762 * MM_TO_MIL / 2)


def test_footprint_shapes_are_read():
    comp = read_kicad_footprint(_MOD)
    kinds = {s.kind for s in comp.footprint.shapes}
    assert {"pad", "track", "circle", "text"} <= kinds


def test_a_custom_pad_without_primitives_is_its_anchor():
    comp = read_kicad_footprint(
        _MOD.replace("smd rect", "smd custom", 1))
    pad = next(s for s in comp.footprint.shapes if s.kind == "pad")
    assert (pad.shape, pad.custom_outlines) == ("RECT", [])


# ------------------------- the whole point ---------------------------

def test_kicad_files_produce_an_altium_plan():
    """The gap this closes: a KiCad part becoming an Altium library.

    The plan is built by the SAME emitter the EasyEDA importer uses, so
    every fix made there applies here too rather than needing a second
    copy that drifts.
    """
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_SYM)
    fp = read_kicad_footprint(_MOD)
    comp = EasyEdaComponent(mpn=sym.mpn, symbol=sym.symbol,
                            footprint=fp.footprint)

    plan = build_altium_plan(comp, "T.SchLib", "T.PcbLib")
    tools = [s["tool"] for s in plan["steps"]]
    assert "lib_create_symbol" in tools
    assert "lib_add_pins" in tools
    assert "lib_create_footprint" in tools
    assert "lib_add_footprint_pads" in tools
    assert tools[-1] == "lib_link_footprint"

    pins = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_pins")["args"]["pins"]
    assert {p["designator"] for p in pins} == {"1", "2"}


def test_arc_reconstruction_preserves_the_curve():
    """KiCad stores start/mid/end; the neutral model wants endpoint form.

    The radius and both flags have to be recovered from the circle
    through the three points, so check the MIDPOINT rather than the
    endpoints: endpoints alone would pass with a wrong radius or sweep.
    """
    from eda_agent.libimport.easyeda.geometry import svg_arc_to_center

    # A quarter circle of radius 1 mm. The mid point must be written to
    # full precision: at 4 decimals it is not quite on the unit circle,
    # and the recovered radius picks up that error rather than any fault
    # in the reconstruction.
    half = math.sqrt(2.0) / 2.0
    mod = f'''(footprint "A" (layer "F.Cu")
      (fp_arc (start 1 0) (mid {half:.9f} {half:.9f}) (end 0 1)
        (stroke (width 0.12) (type solid)) (layer "F.SilkS")))'''
    comp = read_kicad_footprint(mod)
    arc = next(s for s in comp.footprint.shapes if s.kind == "arc")
    geom = svg_arc_to_center(arc.x1, arc.y1, arc.rx, arc.ry, arc.rotation,
                             arc.large_arc, arc.sweep, arc.x2, arc.y2)
    assert geom is not None
    assert geom.rx == pytest.approx(1.0 * MM_TO_MIL, rel=1e-6)
    assert geom.cx == pytest.approx(0.0, abs=1e-3)
    assert geom.cy == pytest.approx(0.0, abs=1e-3)
    mx, my = geom.midpoint
    # The source midpoint, converted: x*k, and y NEGATED for the flip.
    # A wrong sweep flag sends this to the far side of the circle.
    assert mx == pytest.approx(half * MM_TO_MIL, rel=1e-6)
    assert my == pytest.approx(-half * MM_TO_MIL, rel=1e-6)


# ------------- derived symbols and multi-unit parts -------------------
#
# Two format features that are silent when mishandled, and between them
# cover most of KiCad's standard libraries: 12209 of 22728 shipped
# symbols are derived, and 813 are multi-unit.

_DERIVED = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "PARENT"
    (property "Reference" "U" (at 0 0 0))
    (property "Datasheet" "https://example.invalid/parent.pdf" (at 0 0 0))
    (property "Footprint" "PKG:PARENT_FP" (at 0 0 0))
    (symbol "PARENT_1_1"
      (pin input line (at -5.08 0 0) (length 2.54)
        (name "A") (number "1"))
      (pin output line (at 5.08 0 180) (length 2.54)
        (name "Y") (number "2"))))
  (symbol "CHILD" (extends "PARENT")
    (property "Reference" "U" (at 0 0 0))
    (property "Datasheet" "https://example.invalid/child.pdf" (at 0 0 0)))
)'''


def test_a_derived_symbol_inherits_its_parents_geometry():
    """Over half the standard library is derived.

    Without following the link the symbol converts to an Altium part
    with no pins, which is the single largest correctness gap this
    reader could have.
    """
    comp = read_kicad_symbol(_DERIVED, name="CHILD")
    pins = {p.number for p in comp.symbol.shapes if p.kind == "pin"}
    assert pins == {"1", "2"}
    assert comp.symbol.name == "CHILD", "the part keeps its own name"
    assert any("derived" in w for w in comp.warnings), (
        "inheriting geometry silently would hide where it came from")


def test_a_derived_symbol_overrides_the_properties_it_restates():
    """Own values win, unstated ones are inherited. That is the point
    of the mechanism: the child usually differs only in metadata."""
    comp = read_kicad_symbol(_DERIVED, name="CHILD")
    assert comp.datasheet.endswith("child.pdf")     # restated: child wins
    assert comp.footprint_ref == "PKG:PARENT_FP"    # unstated: inherited


def test_a_missing_parent_is_reported_not_silently_empty():
    """A library referencing a parent it does not contain."""
    orphan = _DERIVED.replace('(extends "PARENT")', '(extends "ABSENT")')
    comp = read_kicad_symbol(orphan, name="CHILD")
    assert any("ABSENT" in w for w in comp.warnings)


def test_an_extends_cycle_terminates():
    """Malformed input must not hang the server."""
    cyclic = ('(kicad_symbol_lib (version 1) (generator t)\n'
              '  (symbol "A" (extends "B"))\n'
              '  (symbol "B" (extends "A")))')
    comp = read_kicad_symbol(cyclic, name="A")
    assert comp.symbol is not None


_MULTI = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "QUAD"
    (property "Reference" "U" (at 0 0 0))
    (symbol "QUAD_0_1"
      (rectangle (start -2.54 2.54) (end 2.54 -2.54)
        (stroke (width 0) (type default)) (fill (type none))))
    (symbol "QUAD_1_1"
      (pin input line (at -5.08 2.54 0) (length 2.54)
        (name "A1") (number "1")))
    (symbol "QUAD_1_2"
      (pin input line (at -5.08 2.54 0) (length 2.54)
        (name "A1") (number "1")))
    (symbol "QUAD_2_1"
      (pin input line (at -5.08 0 0) (length 2.54)
        (name "A2") (number "4")))
    (symbol "QUAD_3_1"
      (pin power_in line (at 0 5.08 270) (length 2.54)
        (name "VCC") (number "14"))))
)'''


def test_units_are_converted_one_at_a_time_not_merged():
    """Merging is the dangerous failure here.

    Every unit sits at the same coordinates by design, so a merged
    symbol has pins stacked on top of each other and still looks like a
    part that converted.
    """
    first = read_kicad_symbol(_MULTI, unit=1)
    second = read_kicad_symbol(_MULTI, unit=2)
    third = read_kicad_symbol(_MULTI, unit=3)

    def numbers(comp):
        return sorted(p.number for p in comp.symbol.shapes if p.kind == "pin")

    assert numbers(first) == ["1"]
    assert numbers(second) == ["4"]
    assert numbers(third) == ["14"]
    assert any("3 units" in w for w in first.warnings)


def test_shared_unit_zero_art_goes_with_every_unit():
    """Unit 0 is the common body, not a unit of its own."""
    comp = read_kicad_symbol(_MULTI, unit=2)
    assert any(s.kind == "rect" for s in comp.symbol.shapes)
    assert not any(p.kind == "pin" and p.number == "1"
                   for p in comp.symbol.shapes)


def test_only_one_body_style_is_taken():
    """QUAD_1_1 and QUAD_1_2 are the same unit drawn two ways.

    Taking both duplicates every pin of that unit, which then reaches
    Altium as a symbol with two pins numbered 1.
    """
    comp = read_kicad_symbol(_MULTI, unit=1)
    numbers = [p.number for p in comp.symbol.shapes if p.kind == "pin"]
    assert numbers == ["1"], f"pins duplicated across body styles: {numbers}"
    assert any("body styles" in w for w in comp.warnings)


def test_an_out_of_range_unit_falls_back_and_still_converts():
    """Better a real unit plus a warning than an empty symbol."""
    comp = read_kicad_symbol(_MULTI, unit=99)
    assert any(p.kind == "pin" for p in comp.symbol.shapes)


def test_a_single_unit_symbol_is_unaffected():
    """The common case must not gain a spurious warning."""
    comp = read_kicad_symbol(_SYM)
    assert len([p for p in comp.symbol.shapes if p.kind == "pin"]) == 2
    assert not any("units" in w for w in comp.warnings)


# ---------------------- rounded rectangle pads -----------------------

_RR = '''(footprint "RR" (layer "F.Cu")
  (pad "1" smd roundrect (at 0 0) (size 1.2 0.6) (roundrect_rratio 0.25)
    (layers "F.Cu"))
  (pad "2" smd roundrect (at 2 0) (size 1.2 0.6) (roundrect_rratio 0.5)
    (layers "F.Cu"))
  (pad "3" smd roundrect (at 4 0) (size 1.2 0.6) (layers "F.Cu"))
  (pad "4" smd rect (at 6 0) (size 1.2 0.6) (layers "F.Cu")))'''


def test_rounded_rectangle_pads_keep_their_rounding():
    """The modern IPC default shape, on nearly every SMD footprint.

    Flattening it to a plain rectangle throws away rounding that Altium
    supports natively, so the converted land pattern would differ from
    the source for no reason.
    """
    comp = read_kicad_footprint(_RR)
    pads = {p.number: p for p in comp.footprint.shapes if p.kind == "pad"}
    assert pads["1"].shape == "ROUNDRECT"
    assert pads["1"].corner_ratio == pytest.approx(0.25)
    assert pads["4"].shape == "RECT", "a plain rect must stay plain"


def test_a_roundrect_without_a_ratio_takes_kicads_default():
    comp = read_kicad_footprint(_RR)
    pads = {p.number: p for p in comp.footprint.shapes if p.kind == "pad"}
    assert pads["3"].corner_ratio == pytest.approx(0.25)


def test_corner_radius_is_converted_not_just_scaled_by_100():
    """The two tools measure the radius against DIFFERENT things.

    Altium's documentation defines the value as "the percentage of half
    of the shortest pad side, where 100% completely rounds the shortest
    side". KiCad's roundrect_rratio is the radius over the WHOLE shorter
    side. So the conversion carries a factor of two, and multiplying by
    100 would halve every corner radius while looking plausible.
    """
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    comp = EasyEdaComponent(
        mpn="RR", footprint=read_kicad_footprint(_RR).footprint)
    step = next(s for s in build_altium_plan(comp, "T.SchLib",
                                             "T.PcbLib")["steps"]
                if s["tool"] == "lib_add_footprint_pads")
    by_num = {p["designator"]: p for p in step["args"]["pads"]}

    assert by_num["1"]["shape"] == "roundrect"
    assert by_num["1"]["corner_radius"] == 50      # 0.25 -> 50%, not 25%
    assert by_num["2"]["corner_radius"] == 100     # 0.5 fully rounds it
    # A plain rectangle must not acquire a corner radius.
    assert by_num["4"]["shape"] == "rectangular"
    assert "corner_radius" not in by_num["4"]


def test_corner_radius_cannot_exceed_what_altium_accepts():
    """A malformed ratio must be clamped, not passed through."""
    from eda_agent.libimport.easyeda.altium import _corner_radius_pct

    assert _corner_radius_pct(0.0) == 0
    assert _corner_radius_pct(0.5) == 100
    assert _corner_radius_pct(9.0) == 100
    assert _corner_radius_pct(-1.0) == 0


# ------------- losses the API cannot express, but must report --------

def _plan_warnings(mod_text):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    comp = EasyEdaComponent(
        mpn="X", footprint=read_kicad_footprint(mod_text).footprint)
    return build_altium_plan(comp, "T.SchLib", "T.PcbLib")["warnings"]


def _plan_pads(mod_text):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    comp = EasyEdaComponent(
        mpn="X", footprint=read_kicad_footprint(mod_text).footprint)
    plan = build_altium_plan(comp, "T.SchLib", "T.PcbLib")
    step = next(s for s in plan["steps"] if s["tool"] == "lib_add_footprint_pads")
    return step["args"]["pads"]


def test_a_slotted_hole_keeps_its_length():
    """A round hole of the slot's width would not take a rectangular lead."""
    mod = ('(footprint "S" (layer "F.Cu")\n'
           '  (pad "1" thru_hole oval (at 0 0) (size 2 3)\n'
           '    (drill oval 0.8 1.6) (layers "*.Cu")))')
    pad = _plan_pads(mod)[0]
    assert (pad["hole_size"], pad["hole_length"]) == (31, 63)


def test_an_unplated_pad_stays_unplated():
    """Emitting it plated puts copper in a hole meant to have none."""
    mod = ('(footprint "S" (layer "F.Cu")\n'
           '  (pad "1" np_thru_hole circle (at 0 0) (size 2 2)\n'
           '    (drill 1.5) (layers "*.Cu")))')
    assert _plan_pads(mod)[0]["plated"] is False


def test_a_numberless_mounting_hole_is_drilled():
    """KiCad's mounting holes have no pad number; the board still needs them."""
    mod = ('(footprint "H" (layer "F.Cu")\n'
           '  (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2)\n'
           '    (drill 3.2) (layers "*.Cu" "*.Mask")))')
    pad = _plan_pads(mod)[0]
    assert (pad["designator"], pad["plated"], pad["hole_size"]) == ("", False, 126)


def test_an_ordinary_footprint_collects_no_such_warnings():
    """These must fire on the real thing only.

    A warning on every plain through-hole pad would train the reader to
    ignore all of them, which is worse than not warning at all.
    """
    mod = ('(footprint "C" (layer "F.Cu")\n'
           '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
           '  (pad "2" thru_hole circle (at 2 0) (size 1.5 1.5)\n'
           '    (drill 0.8) (layers "*.Cu")))')
    assert _plan_warnings(mod) == []


# ----------------------------- 3D bodies -----------------------------

_MOD_3D = ('(footprint "M" (layer "F.Cu")\n'
           '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
           '  (model "${KICAD10_3DMODEL_DIR}/Pkg.3dshapes/M.step"\n'
           '    (offset (xyz 0 0 0)) (scale (xyz 1 1 1))))')


def _plan_with(model_path=""):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    fp = read_kicad_footprint(_MOD_3D).footprint
    fp.model_3d_path = model_path
    sym = read_kicad_symbol(_SYM)
    comp = EasyEdaComponent(mpn="M", symbol=sym.symbol, footprint=fp)
    return build_altium_plan(comp, "T.SchLib", "T.PcbLib")


def test_a_3d_model_reference_is_read():
    comp = read_kicad_footprint(_MOD_3D)
    assert comp.footprint.model_3d_ref.endswith("M.step")


def test_a_resolved_3d_model_is_linked_in_the_plan():
    """KiCad ships STEP and Altium's linker takes STEP.

    So a local part can carry a real 3D body instead of a footprint with
    nothing above the board.
    """
    steps = _plan_with(r"C:\models\M.step")["steps"]
    link = next(s for s in steps if s["tool"] == "lib_link_3d_model")
    assert link["args"]["model_path"] == r"C:\models\M.step"


def test_the_3d_link_runs_while_the_pcblib_is_still_active():
    """These tools act on the ACTIVE document, so order is load bearing.

    lib_link_3d_model edits the footprint, so it has to come after the
    switch to the .PcbLib and before the switch back to the .SchLib for
    lib_link_footprint. Placed wrongly it would edit whichever library
    happened to be focused.
    """
    steps = _plan_with(r"C:\models\M.step")["steps"]
    tools = [s["tool"] for s in steps]
    link_at = tools.index("lib_link_3d_model")

    switches = [i for i, s in enumerate(steps)
                if s["tool"] == "app_set_active_document"]
    active = max(i for i in switches if i < link_at)
    assert steps[active]["args"]["file_path"] == "T.PcbLib"
    assert link_at < tools.index("lib_link_footprint")


def test_an_unresolved_3d_model_is_reported_and_not_linked():
    """A guessed path would either fail on execution or attach the
    wrong shape, so nothing is invented; the reference is reported."""
    plan = _plan_with("")
    assert not any(s["tool"] == "lib_link_3d_model" for s in plan["steps"])
    assert any("3D model" in w for w in plan["warnings"])


def test_a_footprint_with_no_model_says_nothing_about_one():
    plan_steps = _plan_warnings(
        '(footprint "N" (layer "F.Cu")\n'
        '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))')
    assert not any("3D" in w for w in plan_steps)


# ------------- multi-part components and hidden pins -----------------

def test_all_units_are_read_by_default_and_tagged():
    """A quad gate is ONE Altium component with four sub-parts.

    Reading a single unit and telling the user to convert the rest by
    hand was a limitation of this converter, not of the library API:
    lib_create_symbol takes part_count and lib_add_pins takes
    owner_part_id, so the whole part can be built in one pass.
    """
    comp = read_kicad_symbol(_MULTI)
    by_unit = {}
    for p in comp.symbol.shapes:
        if p.kind == "pin":
            by_unit.setdefault(p.unit, []).append(p.number)
    assert by_unit == {1: ["1"], 2: ["4"], 3: ["14"]}
    assert comp.unit_count == 3
    # No "only unit N converted" warning: nothing was left behind.
    assert not any("only unit" in w for w in comp.warnings)


def test_a_multi_unit_symbol_becomes_a_multi_part_altium_component():
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_MULTI)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="QUAD", symbol=sym.symbol),
        "T.SchLib", "T.PcbLib")

    create = next(s for s in plan["steps"]
                  if s["tool"] == "lib_create_symbol")
    assert create["args"]["part_count"] == 3

    pins = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_pins")["args"]["pins"]
    assert {p["designator"]: p["owner_part_id"] for p in pins} == {
        "1": 1, "4": 2, "14": 3}


def test_a_single_part_symbol_carries_no_multi_part_fields():
    """The common case must not gain arguments it does not need."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_SYM)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="ONE", symbol=sym.symbol),
        "T.SchLib", "T.PcbLib")

    create = next(s for s in plan["steps"]
                  if s["tool"] == "lib_create_symbol")
    assert "part_count" not in create["args"]
    pins = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_pins")["args"]["pins"]
    assert not any("owner_part_id" in p for p in pins)


def test_asking_for_one_unit_still_converts_only_that_unit():
    """The old behaviour stays reachable, and now says why."""
    comp = read_kicad_symbol(_MULTI, unit=2)
    numbers = sorted(p.number for p in comp.symbol.shapes if p.kind == "pin")
    assert numbers == ["4"]
    assert any("only unit 2" in w for w in comp.warnings)


_HIDDEN = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "H"
    (property "Reference" "U" (at 0 0 0))
    (symbol "H_1_1"
      (pin power_in line (at -5.08 0 0) (length 2.54)
        (hide yes) (name "VDD") (number "1"))
      (pin no_connect line (at -5.08 -2.54 0) (length 2.54)
        hide (name "NC") (number "2"))
      (pin input line (at -5.08 2.54 0) (length 2.54)
        (name "IN") (number "3")))))'''


def test_hidden_pins_are_kept_but_stay_hidden():
    """A hidden pin is electrically real, so it must convert.

    5378 of the 106032 pin definitions in KiCad 10.0.1's libraries are
    hidden. Dropping them would lose supply rails; showing them all
    would add every NC the source deliberately hides. Both forms of the
    directive appear in the wild, because a library can predate the
    KiCad that opens it.
    """
    comp = read_kicad_symbol(_HIDDEN)
    vis = {p.number: p.display for p in comp.symbol.shapes if p.kind == "pin"}
    assert vis == {"1": False, "2": False, "3": True}


def test_pin_visibility_reaches_the_altium_plan():
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_HIDDEN)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="H", symbol=sym.symbol), "T.SchLib", "T.PcbLib")
    pins = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_pins")["args"]["pins"]
    by_num = {p["designator"]: p for p in pins}
    assert by_num["1"].get("hidden") is True
    assert by_num["2"].get("hidden") is True
    # A visible pin must not carry the flag at all.
    assert "hidden" not in by_num["3"]


_POWER_UNIT = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "DUAL"
    (property "Reference" "U" (at 0 0 0))
    (symbol "DUAL_1_1"
      (pin output line (at 5.08 0 180) (length 2.54)
        (name "OUT1") (number "1")))
    (symbol "DUAL_2_1"
      (pin output line (at 5.08 0 180) (length 2.54)
        (name "OUT2") (number "7")))
    (symbol "DUAL_3_1"
      (pin power_in line (at 0 5.08 270) (length 2.54)
        (name "V+") (number "8"))
      (pin power_in line (at 0 -5.08 90) (length 2.54)
        (name "V-") (number "4")))))'''


def test_a_power_only_sub_part_is_flagged_not_reinterpreted():
    """Both representations are legitimate, so neither is assumed.

    KiCad splits a dual op-amp's supply rails into their own unit;
    Altium can express the same thing as pins SHARED by every part
    (owner_part_id 0). Converting one into the other silently would
    change what the file says, so the source structure is kept and the
    alternative is named. Detected from the pins' electrical types, not
    guessed from a name.
    """
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_POWER_UNIT)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="DUAL", symbol=sym.symbol), "T.SchLib",
        "T.PcbLib")

    create = next(s for s in plan["steps"]
                  if s["tool"] == "lib_create_symbol")
    assert create["args"]["part_count"] == 3, "the source says three units"

    pins = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_pins")["args"]["pins"]
    assert {p["designator"]: p["owner_part_id"] for p in pins} == {
        "1": 1, "7": 2, "8": 3, "4": 3}
    note = [w for w in plan["warnings"] if "only power pins" in w]
    assert note and "owner_part_id to 0" in note[0]


def test_a_mixed_sub_part_is_not_flagged_as_a_power_unit():
    """A stage that merely HAS a power pin is not a supply unit."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    mixed = _POWER_UNIT.replace(
        '(pin power_in line (at 0 -5.08 90) (length 2.54)\n'
        '        (name "V-") (number "4"))',
        '(pin input line (at 0 -5.08 90) (length 2.54)\n'
        '        (name "IN") (number "4"))')
    sym = read_kicad_symbol(mixed)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="DUAL", symbol=sym.symbol), "T.SchLib",
        "T.PcbLib")
    assert not [w for w in plan["warnings"] if "only power pins" in w]


_SHARED_PINS = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "QUADGATE"
    (property "Reference" "U" (at 0 0 0))
    (symbol "QUADGATE_0_1"
      (pin power_in line (at 0 7.62 270) (length 2.54)
        (name "Vdd") (number "14"))
      (pin power_in line (at 0 -7.62 90) (length 2.54)
        (name "Vss") (number "7")))
    (symbol "QUADGATE_1_1"
      (pin input line (at -7.62 2.54 0) (length 2.54)
        (name "A") (number "1")))
    (symbol "QUADGATE_2_1"
      (pin input line (at -7.62 2.54 0) (length 2.54)
        (name "A") (number "5")))))'''


def test_unit_zero_pins_become_pins_shared_by_every_sub_part():
    """Unit 0 is not a sub-part; it is what EVERY sub-part carries.

    This is how a quad gate holds its supply rails, and it maps exactly
    onto Altium's ``owner_part_id`` 0. Not a hypothetical corner: 678 of
    the unit-0 sub-symbols in KiCad 10.0.1's libraries contain pins.

    Assigning them to a sub-part instead would put Vdd/Vss on only one
    gate of four, and the other three would have no supply.
    """
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_SHARED_PINS)
    by_unit = {}
    for p in sym.symbol.shapes:
        if p.kind == "pin":
            by_unit.setdefault(p.unit, []).append(p.number)
    assert sorted(by_unit[0]) == ["14", "7"]

    plan = build_altium_plan(
        EasyEdaComponent(mpn="Q", symbol=sym.symbol), "T.SchLib", "T.PcbLib")
    create = next(s for s in plan["steps"]
                  if s["tool"] == "lib_create_symbol")
    # Shared pins must NOT inflate the sub-part count.
    assert create["args"]["part_count"] == 2

    pins = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_pins")["args"]["pins"]
    owners = {p["designator"]: p["owner_part_id"] for p in pins}
    assert owners == {"14": 0, "7": 0, "1": 1, "5": 2}


def test_shared_pins_are_not_reported_as_a_power_sub_part():
    """The source already used the shared form; nothing to suggest."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_SHARED_PINS)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="Q", symbol=sym.symbol), "T.SchLib", "T.PcbLib")
    assert not [w for w in plan["warnings"] if "only power pins" in w]


# ------------------ paste / mask apertures, Altium side ---------------

_APERTURE_MOD = '''(footprint "AP" (layer "F.Cu")
  (pad "1" smd roundrect (at -0.25 0) (size 0.4 0.3)
    (roundrect_rratio 0.25) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "9" smd roundrect (at 0 0) (size 0.27 0.27)
    (roundrect_rratio 0.25) (layers "F.Paste"))
  (pad "" smd rect (at 0.25 0) (size 0.2 0.2) (layers "F.Mask")))'''


def test_an_aperture_never_becomes_altium_copper():
    """A paste or mask aperture carries no copper; an Altium pad always
    does. Emitting one as a pad puts metal where the source has none,
    shorting the very pads the aperture exists to subdivide.

    The aperture here is given a DESIGNATOR on purpose. Every one of the
    332 in KiCad 10.0.1's sampled libraries is nameless, so the
    pre-existing blank-designator guard happened to catch them all --
    but that is a property of the corpus, not of apertures, and a named
    one would have been emitted as copper.
    """
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    comp = read_kicad_footprint(_APERTURE_MOD)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="AP", footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")

    pads = next(s["args"]["pads"] for s in plan["steps"]
                if s["tool"] == "lib_add_footprint_pads")
    assert [p["designator"] for p in pads] == ["1"], (
        "only the copper pad may be emitted")
    assert all(p["layer"] in ("TopLayer", "BottomLayer") for p in pads)


def test_apertures_become_regions_on_their_layer_not_copper():
    """A paste or mask opening has no copper, and an Altium pad always
    does: it is drawn as a region on the paste or mask layer, never as a
    pad that would short the pads it subdivides."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    comp = read_kicad_footprint(_APERTURE_MOD)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="AP", footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")

    pads = next((s["args"]["pads"] for s in plan["steps"]
                 if s["tool"] == "lib_add_footprint_pads"), [])
    assert all(p["layer"] in ("TopLayer", "BottomLayer") for p in pads)
    regions = [r for s in plan["steps"] if s["tool"] == "lib_add_footprint_regions"
               for r in s["args"]["regions"]
               if r["layer"] in ("TopPaste", "BottomPaste", "TopSolder", "BottomSolder")]
    assert len(regions) == 2, regions


# --------------- absolute layer checks (not round trips) --------------
#
# A round trip cannot catch a reader bug that is SYMMETRIC. Force every
# region onto top copper and both passes read it that way, so the
# signatures still agree and the corpus round trip passes while every
# fabrication layer is wrong. Verified by mutating exactly that.
#
# The fix is not a better round trip, it is a different KIND of test:
# assert the absolute value against source whose correct answer is
# known independently.

_LAYERS_MOD = '''(footprint "L" (layer "F.Cu")
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
  (fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1))
    (stroke (width 0.1) (type solid)) (fill solid) (layer "B.SilkS"))
  (fp_poly (pts (xy 3 0) (xy 4 0) (xy 4 1))
    (stroke (width 0.1) (type solid)) (fill solid) (layer "F.Paste"))
  (fp_line (start 0 0) (end 1 0)
    (stroke (width 0.1) (type solid)) (layer "B.Cu"))
  (fp_circle (center 0 0) (end 1 0)
    (stroke (width 0.1) (type solid)) (layer "F.Fab")))'''


def test_each_shape_keeps_the_layer_it_was_drawn_on():
    """Absolute layer values, checked against a known source.

    Layers decide what gets fabricated. A region that drifts from back
    silkscreen to top copper is metal on a finished board, and it
    survives every round trip because the reader reports it the same
    way twice.
    """
    comp = read_kicad_footprint(_LAYERS_MOD)
    by_kind = {}
    for s in comp.footprint.shapes:
        by_kind.setdefault(s.kind, []).append(s)

    regions = sorted(by_kind["solid_region"], key=lambda s: s.points[0][0])
    assert regions[0].layer == 4, "B.SilkS is layer 4"
    assert regions[1].layer == 5, "F.Paste is layer 5"
    assert by_kind["track"][0].layer == 2, "B.Cu is layer 2"
    assert by_kind["circle"][0].layer == 13, "F.Fab is layer 13"
    assert by_kind["pad"][0].layer == 1, "F.Cu is layer 1"


def test_a_closed_region_carries_no_repeated_final_vertex():
    """One representation per outline.

    KiCad writes some polygons closed explicitly and some implicitly.
    Keeping both means the same outline has two forms: it grows a vertex
    on every trip through a writer that closes explicitly, and any
    consumer walking the edges counts a zero-length one.
    """
    closed = _LAYERS_MOD.replace(
        "(pts (xy 0 0) (xy 1 0) (xy 1 1))",
        "(pts (xy 0 0) (xy 1 0) (xy 1 1) (xy 0 0))")
    comp = read_kicad_footprint(closed)
    region = min((s for s in comp.footprint.shapes
                  if s.kind == "solid_region"),
                 key=lambda s: s.points[0][0])
    assert len(region.points) == 3
    assert region.points[0] != region.points[-1]


# ----------------------- pin electrical types -------------------------

_ELEC_SYM = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "ELEC"
    (property "Reference" "U" (at 0 0 0))
    (symbol "ELEC_1_1"
      (pin open_collector line (at -5.08 0 0) (length 2.54)
        (name "OC") (number "1"))
      (pin open_emitter line (at -5.08 -2.54 0) (length 2.54)
        (name "OE") (number "2"))
      (pin tri_state line (at -5.08 -5.08 0) (length 2.54)
        (name "TS") (number "3"))
      (pin no_connect line (at -5.08 -7.62 0) (length 2.54)
        (name "NC") (number "4"))
      (pin power_in line (at -5.08 -10.16 0) (length 2.54)
        (name "VCC") (number "5"))
      (pin passive line (at -5.08 -12.7 0) (length 2.54)
        (name "P") (number "6")))))'''


def test_erc_relevant_pin_types_are_not_flattened_to_passive():
    """Pin type is what ERC reasons about, so collapsing it hides faults.

    An open-collector output recorded as passive defeats the check for a
    missing pull-up, and two of them driving one net stops being a
    reported conflict. These are not rare: KiCad 10.0.1's libraries hold
    1827 open-collector, 1858 tri-state and 119 open-emitter pins.

    Asserted as ABSOLUTE values rather than through a round trip. A trip
    cannot see a symmetric reader fault -- map every type to passive on
    the way in and it maps back out again, matching itself perfectly
    while every pin is wrong.
    """
    from eda_agent.libimport.easyeda.shapes import PIN_ELECTRIC

    comp = read_kicad_symbol(_ELEC_SYM)
    kinds = {p.number: PIN_ELECTRIC.get(p.electric)
             for p in comp.symbol.shapes if p.kind == "pin"}
    assert kinds == {
        "1": "open_collector",
        "2": "open_emitter",
        "3": "hiz",          # KiCad's tri_state is Altium's high-Z
        "4": "undefined",    # no Altium equivalent; see below
        "5": "power",
        "6": "undefined",
    }


def test_those_types_reach_altium_with_their_own_names():
    """Altium names all three exactly, and Library.pas maps the strings
    to eElectricOpenCollector / eElectricOpenEmitter / eElectricHiZ."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    sym = read_kicad_symbol(_ELEC_SYM)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="E", symbol=sym.symbol), "T.SchLib", "T.PcbLib")
    pins = next(s["args"]["pins"] for s in plan["steps"]
                if s["tool"] == "lib_add_pins")
    assert {p["designator"]: p["electrical_type"] for p in pins} == {
        "1": "open_collector",
        "2": "open_emitter",
        "3": "hiz",
        # Altium's pin vocabulary has no "not connected" -- an unused pin
        # is marked with a No-ERC directive instead -- so passive is the
        # honest destination rather than a worse guess.
        "4": "passive",
        "5": "power",
        "6": "passive",
    }


def test_pin_type_survives_a_kicad_round_trip():
    """The writer must know the reverse mapping too."""
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym
    from eda_agent.libimport.easyeda.shapes import PIN_ELECTRIC

    sym = read_kicad_symbol(_ELEC_SYM)
    back = read_kicad_symbol(symbol_to_kicad_sym(sym))
    kinds = {p.number: PIN_ELECTRIC.get(p.electric)
             for p in back.symbol.shapes if p.kind == "pin"}
    for number in ("1", "2", "3", "5"):
        before = next(PIN_ELECTRIC.get(p.electric)
                      for p in sym.symbol.shapes
                      if p.kind == "pin" and p.number == number)
        assert kinds[number] == before, f"pin {number} changed type"


# ---------------- active-low and clock pin markers --------------------

_MARKER_SYM = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "MARK"
    (property "Reference" "U" (at 0 0 0))
    (symbol "MARK_1_1"
      (pin input inverted (at -5.08 0 0) (length 2.54)
        (name "RESET") (number "1"))
      (pin input clock (at -5.08 -2.54 0) (length 2.54)
        (name "CLK") (number "2"))
      (pin input inverted_clock (at -5.08 -5.08 0) (length 2.54)
        (name "CLKN") (number "3"))
      (pin input input_low (at -5.08 -7.62 0) (length 2.54)
        (name "OE") (number "4"))
      (pin input line (at -5.08 -10.16 0) (length 2.54)
        (name "A") (number "5")))))'''


def test_active_low_and_clock_markers_are_read():
    """A pin drawn without its bubble reads as active-high.

    KiCad spells active-low two ways -- "inverted" draws the bubble,
    "*_low" draws IEEE's angled wedge -- and they mean the same thing.
    Only the first was being read, so 61 input_low and 38 output_low
    pins in KiCad 10.0.1 lost their marker, and every clock marker (517
    clock plus 28 inverted_clock) was dropped outright.
    """
    comp = read_kicad_symbol(_MARKER_SYM)
    got = {p.number: (p.dot, p.clock)
           for p in comp.symbol.shapes if p.kind == "pin"}
    assert got == {
        "1": (True, False),    # inverted
        "2": (False, True),    # clock
        "3": (True, True),     # inverted_clock is both
        "4": (True, False),    # input_low is active-low too
        "5": (False, False),   # plain
    }


def _pin_args(sym, mpn="M"):
    """The pins lib_add_pins would receive, keyed by designator."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    plan = build_altium_plan(
        EasyEdaComponent(mpn=mpn, symbol=sym.symbol), "T.SchLib", "T.PcbLib")
    step = next(s for s in plan["steps"] if s["tool"] == "lib_add_pins")
    return {p["designator"]: p for p in step["args"]["pins"]}


def test_markers_reach_the_altium_plan():
    """The bubble and the wedge are carried, not described in a warning.

    They ride separate properties -- the inversion bubble on the pin's
    OUTER edge, the clock wedge on the INNER -- so pin 3 (inverted_clock)
    is the one that proves they are independent rather than a single
    three-way choice.
    """
    pins = _pin_args(read_kicad_symbol(_MARKER_SYM))
    got = {d: (p.get("symbol_outer_edge"), p.get("symbol_inner_edge"))
           for d, p in pins.items()}
    assert got == {
        "1": ("dot", None),       # inverted
        "2": (None, "clock"),     # clock
        "3": ("dot", "clock"),    # inverted_clock is both
        "4": ("dot", None),       # input_low is active-low too
        "5": (None, None),        # plain
    }


def test_an_undecorated_pin_adds_no_fields_at_all():
    """A pin with no marker must produce the payload it produced before.

    The Pascal only writes Symbol_OuterEdge / Symbol_InnerEdge when the
    field is present, and a fresh pin already carries eNoSymbol. Emitting
    "no_symbol" explicitly would still be correct Altium-side but would
    change the payload for every symbol this codebase has ever authored,
    which is a far larger blast radius than the feature deserves.
    """
    from eda_agent.tools.library import _pins_payload

    pins = _pin_args(read_kicad_symbol(_SYM), mpn="P")
    assert not any("symbol_outer_edge" in p or "symbol_inner_edge" in p
                   for p in pins.values())
    payload, _ = _pins_payload(list(pins.values()))
    assert "symbol_" not in payload


def test_markers_survive_the_payload_grammar():
    """End to end: KiCad source -> plan -> the exact string Pascal parses.

    Read back with the GetBatchField mirror that the FPC suite proves
    equivalent to the compiled original, so this is a statement about
    what Altium will actually receive.
    """
    from tests.test_cross_validate import get_batch_field, next_batch_op
    from eda_agent.tools.library import _pins_payload

    pins = _pin_args(read_kicad_symbol(_MARKER_SYM))
    payload, skipped = _pins_payload(
        [pins[d] for d in ("1", "2", "3", "4", "5")])
    assert skipped == 0

    ops, remaining = [], payload
    while True:
        op, remaining = next_batch_op(remaining)
        if not op:
            break
        ops.append(op)
    assert len(ops) == 5

    read = [(get_batch_field(o, "symbol_outer_edge"),
             get_batch_field(o, "symbol_inner_edge")) for o in ops]
    assert read == [("dot", ""), ("", "clock"), ("dot", "clock"),
                    ("dot", ""), ("", "")]


# --------- absolute pad geometry (the round trip cannot see this) -----
#
# Pad rotation, plating and slot length are otherwise covered only by
# the corpus round trip, which is blind to a SYMMETRIC error. Pad
# rotation is the clearest case: the reader negates it for the Y mirror
# and the writer negates it back, so dropping BOTH negations round-trips
# perfectly while every rotated pad reaches Altium mirrored -- Altium
# takes the neutral value as-is and never sees the KiCad file at all.

_GEOM_MOD = '''(footprint "G" (layer "F.Cu")
  (pad "1" smd rect (at 1 2 90) (size 2 1) (layers "F.Cu"))
  (pad "2" thru_hole oval (at 0 0) (size 3 2) (drill oval 1.6 0.8)
    (layers "*.Cu" "*.Mask"))
  (pad "3" np_thru_hole circle (at 5 0) (size 2 2) (drill 1.5)
    (layers "*.Cu" "*.Mask")))'''


def test_pad_rotation_is_mirrored_with_the_y_axis():
    """A .kicad_mod is Y-down and the neutral model is Y-up.

    The mirror that flips the position negates the angle too. Asserted
    absolutely because Altium consumes the neutral value directly: a
    reader that skips this hands Altium a mirrored pad while a KiCad
    round trip still matches itself.
    """
    comp = read_kicad_footprint(_GEOM_MOD)
    pad = next(p for p in comp.footprint.shapes
               if p.kind == "pad" and p.number == "1")
    assert pad.rotation == 270.0, "KiCad 90 mirrors to 270 in a Y-up frame"
    # And the position mirrors with it.
    assert pad.cy < 0, "a positive KiCad y is negative in the neutral frame"


def test_a_wide_slot_keeps_its_short_axis_as_the_hole():
    """``(drill oval X Y)`` gives both axes and either may be longer.

    Reading the FIRST number as the diameter doubles the hole on every
    slot that runs horizontally, which is most mounting slots. 1.6 x 0.8
    here is a wide slot on purpose: taking nums[0] would report a 0.8mm
    radius instead of 0.4mm.
    """
    comp = read_kicad_footprint(_GEOM_MOD)
    pad = next(p for p in comp.footprint.shapes
               if p.kind == "pad" and p.number == "2")
    assert pad.is_slot
    assert pad.hole_radius == pytest.approx(0.4 * MM_TO_MIL, rel=1e-6)
    assert pad.hole_length == pytest.approx(1.6 * MM_TO_MIL, rel=1e-6)


def test_plating_is_read_from_the_pad_type():
    """Copper in a hole meant to be bare is a real fabrication error."""
    comp = read_kicad_footprint(_GEOM_MOD)
    by_num = {p.number: p for p in comp.footprint.shapes if p.kind == "pad"}
    assert by_num["2"].plated is True
    assert by_num["3"].plated is False
    assert by_num["3"].is_through_hole


def test_the_writer_emits_exactly_one_graphic_style_token():
    """KiCad's grammar is ``(pin <electrical> <graphic_style> ...)``.

    The writer emitted "line inverted" -- two tokens where the format
    allows one -- so every inverted pin produced a malformed file, and a
    reader taking the second token saw a plain "line" and dropped the
    bubble. Checked on the TEXT because that is where the extra token
    lives; a parse-then-compare hides it behind whichever token the
    reader happens to pick.
    """
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    text = symbol_to_kicad_sym(read_kicad_symbol(_MARKER_SYM))
    styles = re.findall(r"\(pin\s+(\S+)\s+(\S+)\s", text)
    assert styles, "no pins were written"
    for elec, style in styles:
        assert style in ("line", "inverted", "clock", "inverted_clock"), (
            f"{style!r} is not a KiCad graphic style")
    # And the markers themselves must be the right ones.
    assert sorted(s for _, s in styles) == sorted(
        ["inverted", "clock", "inverted_clock", "inverted", "line"])


def test_written_markers_reload_in_this_reader():
    """The round trip, stated as its own assertion.

    Emitting a style the reader cannot parse back is the failure the
    two-token bug produced, so this checks the pair rather than either
    half.
    """
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    original = read_kicad_symbol(_MARKER_SYM)
    back = read_kicad_symbol(symbol_to_kicad_sym(original))
    before = {p.number: (p.dot, p.clock)
              for p in original.symbol.shapes if p.kind == "pin"}
    after = {p.number: (p.dot, p.clock)
             for p in back.symbol.shapes if p.kind == "pin"}
    assert after == before


# --------------------- pin label visibility --------------------------
#
# KiCad declares this ONCE PER SYMBOL, (pin_names (hide yes)) /
# (pin_numbers (hide yes)); the neutral model and Altium's ISch_Pin both
# carry it per pin. Every passive in KiCad's libraries relies on it, so
# ignoring it draws "1" and "2" and the pin names on every imported
# resistor.

def _vis_lib(decl: str, child_decl: str = "") -> str:
    """A two-symbol library: a parent carrying ``decl``, and a child
    extending it that carries ``child_decl`` (nothing by default)."""
    return f'''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "PARENT"
    {decl}
    (property "Reference" "R" (at 0 0 0))
    (symbol "PARENT_1_1"
      (pin passive line (at -5.08 0 0) (length 2.54)
        (name "A") (number "1"))
      (pin passive line (at 5.08 0 0) (length 2.54)
        (name "B") (number "2"))))
  (symbol "CHILD"
    (extends "PARENT")
    {child_decl}
    (property "Reference" "R" (at 0 0 0))))'''


def _flags(text, name="PARENT"):
    pins = [p for p in read_kicad_symbol(text, name=name).symbol.shapes
            if p.kind == "pin"]
    assert pins, "fixture produced no pins"
    return {(p.name_visible, p.number_visible) for p in pins}


def test_symbol_level_hide_reaches_every_pin():
    """The 2024 spelling, which is what current KiCad writes."""
    assert _flags(_vis_lib("(pin_names (hide yes))")) == {(False, True)}
    assert _flags(_vis_lib("(pin_numbers (hide yes))")) == {(True, False)}
    assert _flags(_vis_lib(
        "(pin_names (hide yes)) (pin_numbers (hide yes))")) == {(False, False)}


def test_the_pre_2024_bare_hide_spelling_is_accepted():
    """KiCad wrote a bare ``hide`` atom before the 2024 format, and a
    library can be older than the installed KiCad, so both appear."""
    assert _flags(_vis_lib("(pin_names (offset 1.016) hide)")) == {
        (False, True)}
    assert _flags(_vis_lib("(pin_numbers hide)")) == {(True, False)}


def test_labels_are_visible_by_default():
    """Absent entirely, and declared with no hide flag, both mean shown.

    The second is the common one: (pin_names (offset 1.016)) appears on
    2091 symbols in the corpus and sets only the offset.
    """
    assert _flags(_vis_lib("")) == {(True, True)}
    assert _flags(_vis_lib("(pin_names (offset 1.016))")) == {(True, True)}


def test_a_derived_symbol_inherits_the_parents_hide():
    """Derived symbols are more than half the library, and a passive
    that inherits its geometry inherits this with it. Reading only the
    child's own node shows pin numbers on nearly every one of them."""
    assert _flags(_vis_lib("(pin_names (hide yes))"), name="CHILD") == {
        (False, True)}


def test_a_child_that_restates_the_flag_wins_over_its_parent():
    """Nearest link in the chain is authoritative, matching how the
    properties above are resolved. A parent must not re-hide what the
    child has explicitly shown."""
    lib = _vis_lib("(pin_names (hide yes))", "(pin_names (offset 1.016))")
    assert _flags(lib, name="CHILD") == {(True, True)}
    # ...and the parent itself is unaffected by the child.
    assert _flags(lib, name="PARENT") == {(False, True)}


def test_hidden_labels_reach_the_altium_plan_and_the_payload():
    """Altium stores this per pin, so the symbol-level flag fans out.

    Only the False case is sent: visible is Altium's default, and
    stating it would add two fields to every pin of every symbol.
    """
    from eda_agent.tools.library import _pins_payload
    from tests.test_cross_validate import get_batch_field, next_batch_op

    pins = _pin_args(read_kicad_symbol(
        _vis_lib("(pin_names (hide yes))")), mpn="R")
    assert all(p.get("show_name") is False for p in pins.values())
    assert all("show_designator" not in p for p in pins.values())

    payload, _ = _pins_payload([pins["1"], pins["2"]])
    op, _rest = next_batch_op(payload)
    assert get_batch_field(op, "show_name") == "false"
    assert get_batch_field(op, "show_designator") == ""


def test_a_fully_visible_symbol_sends_no_visibility_fields():
    """Same blast-radius rule as the IEEE decorations: an unremarkable
    symbol must produce the payload it produced before this feature."""
    from eda_agent.tools.library import _pins_payload

    pins = _pin_args(read_kicad_symbol(_vis_lib("")), mpn="R")
    payload, _ = _pins_payload(list(pins.values()))
    assert "show_name" not in payload
    assert "show_designator" not in payload


def test_the_writer_declares_the_hide_once_per_symbol():
    """KiCad has no per-pin form, so the writer narrows back."""
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    comp = read_kicad_symbol(_vis_lib(
        "(pin_names (hide yes)) (pin_numbers (hide yes))"))
    text = symbol_to_kicad_sym(comp)
    assert "(pin_names (hide yes))" in text
    assert "(pin_numbers (hide yes))" in text
    assert _flags(text) == {(False, False)}


def test_the_writer_reports_pins_that_disagree():
    """The model can express what KiCad cannot: some pins hiding their
    name and others not. Half-applying that would silently show labels
    the source hid, so it is reported instead."""
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    comp = read_kicad_symbol(_vis_lib("(pin_names (hide yes))"))
    pins = [p for p in comp.symbol.shapes if p.kind == "pin"]
    pins[0].name_visible = True          # now they disagree
    text = symbol_to_kicad_sym(comp)
    assert "(pin_names (hide yes))" not in text
    assert any("mixes name_visible" in w for w in comp.warnings), comp.warnings


# ---------------------- closed-outline geometry ----------------------
#
# An fp_poly is a closed AREA. The reader strips the repeated final
# vertex so the model has one representation of a closure, which means
# the closure survives only as the `closed` flag; a consumer that walks
# consecutive pairs then draws every edge but the last and leaves a
# notch. Measured on the installed corpus: 6028 polygons across 3626
# footprints, every one of them missing its closing edge.

_POLY_MOD = '''(footprint "POLYFP" (version 20251024) (layer "F.Cu")
  (fp_poly (pts (xy 0 0) (xy 2.54 0) (xy 1.27 2.54) (xy 0 0))
    (stroke (width 0.12) (type solid)) (fill none) (layer "F.SilkS"))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
)'''


def _tracks(comp):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    plan = build_altium_plan(
        EasyEdaComponent(mpn="P", footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")
    step = next((s for s in plan["steps"]
                 if s["tool"] == "lib_add_footprint_tracks"), None)
    return step["args"]["tracks"] if step else []


def test_a_closed_polygon_is_marked_closed_and_deduplicated():
    """Both halves matter: the repeated vertex goes, the flag stays."""
    comp = read_kicad_footprint(_POLY_MOD)
    polys = [s for s in comp.footprint.shapes if s.kind == "solid_region"]
    assert len(polys) == 1
    assert polys[0].closed is True
    # The explicit closure vertex is normalised away, so the outline is
    # three points, not four.
    assert len(polys[0].points) == 3
    assert polys[0].points[0] != polys[0].points[-1]


def test_a_triangle_emits_three_edges_not_two():
    """The absolute check. A round trip cannot see this: the writer and
    the reader would drop and re-derive the same edge and agree."""
    segments = _tracks(read_kicad_footprint(_POLY_MOD))
    assert len(segments) == 3, f"expected a sealed triangle, got {segments}"
    # The third segment must run from the last vertex back to the first.
    ends = {(s["x1"], s["y1"], s["x2"], s["y2"]) for s in segments}
    starts = {(s["x1"], s["y1"]) for s in segments}
    stops = {(s["x2"], s["y2"]) for s in segments}
    assert starts == stops, (
        f"outline is not a closed loop: starts {starts} stops {stops} "
        f"({ends})")


def test_an_open_run_of_points_gains_no_edge():
    """Only shapes that CLAIM to be closed get the extra segment; an
    fp_line chain that happens to have three points must stay open."""
    open_mod = _POLY_MOD.replace(
        '(fp_poly (pts (xy 0 0) (xy 2.54 0) (xy 1.27 2.54) (xy 0 0))\n'
        '    (stroke (width 0.12) (type solid)) (fill none) '
        '(layer "F.SilkS"))',
        '(fp_line (start 0 0) (end 2.54 0) '
        '(stroke (width 0.12) (type solid)) (layer "F.SilkS"))')
    segments = _tracks(read_kicad_footprint(open_mod))
    assert len(segments) == 1, segments


def test_an_already_closed_point_list_is_not_closed_twice():
    """Belt and braces: a shape whose points still repeat the first
    vertex must not gain a zero-length segment on top."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import (
        EasyEdaComponent, EasyEdaFootprint,
    )
    from eda_agent.libimport.easyeda.shapes import EePolyline

    poly = EePolyline(kind="solid_region", raw="", layer=3, closed=True,
                      stroke_width=6,
                      points=[(0, 0), (100, 0), (50, 80), (0, 0)])
    plan = build_altium_plan(
        EasyEdaComponent(mpn="P", footprint=EasyEdaFootprint(
            name="P", shapes=[poly])), "T.SchLib", "T.PcbLib")
    step = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_footprint_tracks")
    segments = step["args"]["tracks"]
    assert len(segments) == 3, segments
    assert not any(s["x1"] == s["x2"] and s["y1"] == s["y2"]
                   for s in segments), f"zero-length segment: {segments}"


# ------------------------- bottom-side text --------------------------

_TEXT_MOD = '''(footprint "TXTFP" (version 20251024) (layer "F.Cu")
  (fp_text user "TOP" (at 0 3) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
  (fp_text user "BOT" (at 0 -3) (layer "B.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
)'''


def _text_steps(comp):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    plan = build_altium_plan(
        EasyEdaComponent(mpn="T", footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")
    return {s["args"]["text"]: s["args"]
            for s in plan["steps"] if s["tool"] == "lib_add_footprint_text"}


def test_bottom_side_text_is_mirrored_and_top_side_is_not():
    """Not a style choice. audit_find_mirrored_pcb_text treats unmirrored
    bottom-overlay text as a violation, so emitting it plain makes this
    importer produce libraries the same server then reports as broken.
    """
    steps = _text_steps(read_kicad_footprint(_TEXT_MOD))
    assert steps["BOT"]["layer"] == "BottomOverlay"
    assert steps["BOT"].get("mirror") is True
    assert steps["TOP"]["layer"] == "TopOverlay"
    assert steps["TOP"].get("mirror") is None, (
        "mirroring TOP-side text is the other half of the same audit "
        "violation")


def test_a_source_mirror_flag_is_honoured_on_non_bottom_layers():
    """EasyEDA carries an explicit mirror flag. The layer wins where it
    settles the question; elsewhere the source is the only evidence."""
    from eda_agent.libimport.easyeda.document import (
        EasyEdaComponent, EasyEdaFootprint,
    )
    from eda_agent.libimport.easyeda.shapes import EeText
    from eda_agent.libimport.easyeda.altium import build_altium_plan

    txt = EeText(kind="text", raw="", text="M", x=0, y=0, font_size=50,
                 layer=12, mirror=True)          # Mechanical1, neither side
    plan = build_altium_plan(
        EasyEdaComponent(mpn="T", footprint=EasyEdaFootprint(
            name="T", shapes=[txt])), "T.SchLib", "T.PcbLib")
    step = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_footprint_text")
    assert step["args"].get("mirror") is True


# ---------------------- text size and stroke -------------------------
#
# Both were hardcoded: the reader stamped 60 mils on every string and
# the writer emitted 1 mm for every string. The installed corpus states
# an explicit size on all 18597 of its fp_text entries, across 91
# distinct heights, so the constant was wrong nearly every time. The
# common 1 mm case arrived 52% oversized and 0.4 mm text almost 4x,
# which on a dense footprint drags silkscreen over the pads.

_SIZED_MOD = '''(footprint "SIZEFP" (version 20251024) (layer "F.Cu")
  (fp_text user "SMALL" (at 0 3) (layer "F.SilkS")
    (effects (font (size 0.5 0.5) (thickness 0.1))))
  (fp_text user "BIG" (at 0 -3) (layer "F.SilkS")
    (effects (font (size 2 2) (thickness 0.3))))
  (fp_text user "FLIP" (at 0 -6) (layer "B.SilkS")
    (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
)'''


def _texts(comp):
    return {t.text: t for t in comp.footprint.shapes if t.kind == "text"}


def test_text_height_comes_from_the_file_not_a_constant():
    got = _texts(read_kicad_footprint(_SIZED_MOD))
    assert got["SMALL"].font_size == pytest.approx(0.5 * MM_TO_MIL, rel=1e-6)
    assert got["BIG"].font_size == pytest.approx(2.0 * MM_TO_MIL, rel=1e-6)
    # The two must actually differ; a constant would make them equal.
    assert got["SMALL"].font_size < got["BIG"].font_size


def test_text_stroke_width_is_read():
    got = _texts(read_kicad_footprint(_SIZED_MOD))
    assert got["SMALL"].stroke_width == pytest.approx(0.1 * MM_TO_MIL, rel=1e-6)
    assert got["BIG"].stroke_width == pytest.approx(0.3 * MM_TO_MIL, rel=1e-6)


def test_kicads_mirror_justify_is_read():
    got = _texts(read_kicad_footprint(_SIZED_MOD))
    assert got["FLIP"].mirror is True
    assert got["SMALL"].mirror is False


def test_a_node_with_no_font_effects_falls_back_rather_than_vanishing():
    """Zero-height text would be invisible, which is worse than a guess."""
    bare = _SIZED_MOD.replace(
        '(fp_text user "SMALL" (at 0 3) (layer "F.SilkS")\n'
        '    (effects (font (size 0.5 0.5) (thickness 0.1))))',
        '(fp_text user "SMALL" (at 0 3) (layer "F.SilkS"))')
    got = _texts(read_kicad_footprint(bare))
    assert got["SMALL"].font_size == pytest.approx(60.0)


def test_the_altium_plan_carries_size_and_stroke():
    steps = _text_steps(read_kicad_footprint(_SIZED_MOD))
    assert steps["SMALL"]["size"] == round(0.5 * MM_TO_MIL)
    assert steps["BIG"]["size"] == round(2.0 * MM_TO_MIL)
    assert steps["SMALL"]["width"] == round(0.1 * MM_TO_MIL)
    assert steps["BIG"]["width"] == round(0.3 * MM_TO_MIL)


def test_text_size_survives_a_write_and_re_read():
    """The writer emitted a hardcoded 1 mm, so this round trip flattened
    every height to the same value while staying green on geometry."""
    from eda_agent.libimport.easyeda.kicad import footprint_to_kicad_mod

    before = _texts(read_kicad_footprint(_SIZED_MOD))
    again = _texts(read_kicad_footprint(
        footprint_to_kicad_mod(read_kicad_footprint(_SIZED_MOD))))
    for label in ("SMALL", "BIG"):
        assert again[label].font_size == pytest.approx(
            before[label].font_size, rel=1e-3), label
        assert again[label].stroke_width == pytest.approx(
            before[label].stroke_width, rel=1e-3), label
    assert again["FLIP"].mirror is True


# ------------------- symbol text: the decidegree trap ----------------
#
# .kicad_sym states SYMBOL text angles in decidegrees while pins and
# properties in the same file use plain degrees, and .kicad_mod's
# fp_text uses degrees too. Measured across the installed corpus:
# symbol text angles are 0/900/1800/2700 with no 90/180/270 anywhere,
# pin angles are only ever 0/90/180/270. Reading the raw number as
# degrees turns a quarter turn into a half turn.

_SYMTEXT = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "NOTED"
    (property "Reference" "U" (at 0 0 0))
    (symbol "NOTED_0_1"
      (text "UP" (at 0 5.08 0)
        (effects (font (size 1.27 1.27))))
      (text "SIDEWAYS" (at 0 2.54 900)
        (effects (font (size 0.508 0.508)))))
    (symbol "NOTED_1_1"
      (pin passive line (at -5.08 0 90) (length 2.54)
        (name "A") (number "1")))))'''


def _sym_texts(comp):
    return {t.text: t for t in comp.symbol.shapes if t.kind == "text"}


def test_symbol_text_angle_is_decidegrees():
    """900 in the file is a quarter turn, not a half turn."""
    got = _sym_texts(read_kicad_symbol(_SYMTEXT))
    assert got["SIDEWAYS"].rotation == pytest.approx(90.0)
    assert got["UP"].rotation == pytest.approx(0.0)


def test_a_pin_in_the_same_file_still_uses_plain_degrees():
    """The units differ WITHIN one file, so this pins the asymmetry:
    applying the text rule to pins would flatten every pin to 9
    degrees."""
    comp = read_kicad_symbol(_SYMTEXT)
    pin = next(p for p in comp.symbol.shapes if p.kind == "pin")
    assert pin.rotation == pytest.approx(90.0)


def test_symbol_text_size_is_read_not_assumed():
    got = _sym_texts(read_kicad_symbol(_SYMTEXT))
    assert got["UP"].font_size == pytest.approx(1.27 * MM_TO_MIL, rel=1e-6)
    assert got["SIDEWAYS"].font_size == pytest.approx(
        0.508 * MM_TO_MIL, rel=1e-6)


def test_symbol_text_rotation_and_size_survive_a_round_trip():
    """The writer emitted a literal 0 angle and a constant 1.27 size, so
    every rotated note came back upright and every size identical."""
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    before = _sym_texts(read_kicad_symbol(_SYMTEXT))
    again = _sym_texts(read_kicad_symbol(
        symbol_to_kicad_sym(read_kicad_symbol(_SYMTEXT))))
    for label in ("UP", "SIDEWAYS"):
        assert again[label].rotation == pytest.approx(
            before[label].rotation), label
        assert again[label].font_size == pytest.approx(
            before[label].font_size, rel=1e-3), label


# ------------------- symbol text reaches Altium ----------------------

def _sym_plan(text_source):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent

    comp = read_kicad_symbol(text_source)
    return build_altium_plan(
        EasyEdaComponent(mpn="N", symbol=comp.symbol), "S.SchLib", "P.PcbLib")


def test_symbol_body_text_is_placed_not_dropped():
    """These used to be discarded with a warning saying the library API
    had no symbol text primitive. It has one, ISch_Label, and the items
    are not decoration: 2922 of them across 72 installed libraries are
    polarity marks, pin-group headings and NC annotations, which change
    what the symbol says.
    """
    plan = _sym_plan(_SYMTEXT)
    step = next((s for s in plan["steps"]
                 if s["tool"] == "lib_add_symbol_text"), None)
    assert step is not None, [s["tool"] for s in plan["steps"]]
    placed = {t["text"]: t for t in step["args"]["texts"]}
    assert set(placed) == {"UP", "SIDEWAYS"}
    # Decidegrees in the source, quarter turns on the way out.
    assert placed["SIDEWAYS"]["rotation"] == 90
    assert placed["UP"]["rotation"] == 0


def test_the_symbol_text_step_matches_the_real_tool_signature():
    """A plan can stay perfectly self-consistent while every argument
    name is wrong, so the step is checked against the REGISTERED tool
    rather than against this module's idea of it."""
    import inspect

    # Registered locally rather than imported from another test module.
    # This used to reach into the EasyEDA converter test for the helper,
    # which coupled a KiCad test to a file it has nothing to do with:
    # deleting that file broke this one, and the failure named a missing
    # module rather than anything about symbol text.
    from eda_agent.tools.application import register_application_tools
    from eda_agent.tools.library import register_library_tools

    class _Capture:
        def __init__(self):
            self.fns = {}

        def tool(self, *args, **kwargs):
            def deco(fn):
                self.fns[fn.__name__] = fn
                return fn
            return deco

    cap = _Capture()
    register_library_tools(cap)
    register_application_tools(cap)

    step = next(s for s in _sym_plan(_SYMTEXT)["steps"]
                if s["tool"] == "lib_add_symbol_text")
    sig = inspect.signature(cap.fns["lib_add_symbol_text"])
    assert not set(step["args"]) - set(sig.parameters)
    required = {n for n, p in sig.parameters.items()
                if p.default is inspect.Parameter.empty}
    assert not required - set(step["args"])


def test_the_unmapped_font_height_is_reported_with_its_range():
    """The source states height in mils, the tool takes Altium's own
    font size, and the relation is not documented anywhere this project
    can check. Saying so beats inventing a factor that would silently
    resize every note while looking like it worked.
    """
    warns = [w for w in _sym_plan(_SYMTEXT)["warnings"]
             if "font size" in w]
    assert warns, _sym_plan(_SYMTEXT)["warnings"]
    # The actual source heights must appear, so the reader can act on it.
    assert "20-50 mils" in warns[0], warns[0]


def test_a_symbol_without_text_emits_no_text_step():
    plan = _sym_plan(_SYM)
    assert not [s for s in plan["steps"]
                if s["tool"] == "lib_add_symbol_text"]
    assert not [w for w in plan["warnings"] if "font size" in w]


def test_symbol_text_survives_the_payload_grammar():
    """End to end into the exact string Pascal will parse."""
    from tests.test_cross_validate import get_batch_field, next_batch_op

    step = next(s for s in _sym_plan(_SYMTEXT)["steps"]
                if s["tool"] == "lib_add_symbol_text")
    op_strs = []
    for item in step["args"]["texts"]:
        op_strs.append(";".join([
            f"text={item['text']}",
            f"x={item['x']}", f"y={item['y']}",
            f"rotation={item['rotation']}",
        ]))
    payload = "~~".join(op_strs)

    ops, remaining = [], payload
    while True:
        op, remaining = next_batch_op(remaining)
        if not op:
            break
        ops.append(op)
    assert len(ops) == 2
    # "POLARITY +" style content must survive intact, including spaces.
    assert {get_batch_field(o, "text") for o in ops} == {"UP", "SIDEWAYS"}
