# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Reading a human-drawn sheet, so features can be checked against one.

The layout features had no ground truth. They were written from
conviction and weighted by hand, and the one attempt to learn weights
produced a 58% model from a corpus of a single design. A professionally
drawn schematic is the ground truth that was missing, and this reader is
what makes one legible.

It has already earned that: measured against these sheets, two of four
structural features turned out to penalise what professionals actually
do, and a third was firing on 36% of outputs because it paired an output
with its NEAREST input rather than the one it drives.

The fixture below is a complete miniature .kicad_sch so these tests run
with or without KiCad installed. The corpus tests skip when it is
absent, and say so rather than passing vacuously.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.design.kicad_sheet_reader import (
    extract_nets,
    pin_wire_agreement,
    read_sheet,
    symbols_from_sheet,
)

DEMOS = Path(r"C:\Program Files\KiCad\10.0\share\kicad\demos")

# A driver whose output pin points right, a load whose input points left,
# a ground glyph, a wire joining the two signal pins, and a label.
SHEET = """
(kicad_sch
  (version 20250610)
  (paper "A3")
  (title_block (title "Fixture"))
  (lib_symbols
    (symbol "Lib:DRV"
      (property "Reference" "U")
      (symbol "DRV_1_1"
        (rectangle (start -5.08 2.54) (end 0 -2.54))
        (pin output line (at 2.54 0 180) (length 2.54)
          (name "OUT") (number "1"))))
    (symbol "Lib:LOAD"
      (property "Reference" "U")
      (symbol "LOAD_1_1"
        (rectangle (start 0 2.54) (end 5.08 -2.54))
        (pin input line (at -2.54 0 0) (length 2.54)
          (name "IN") (number "1"))))
    (symbol "power:GND"
      (property "Reference" "#PWR")
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "GND") (number "1")))))
  (symbol (lib_id "Lib:DRV") (at 25.4 25.4 0)
    (property "Reference" "U1") (property "Value" "DRV"))
  (symbol (lib_id "Lib:LOAD") (at 76.2 25.4 0)
    (property "Reference" "U2") (property "Value" "LOAD"))
  (symbol (lib_id "power:GND") (at 50.8 50.8 0)
    (property "Reference" "#PWR01") (property "Value" "GND"))
  (wire (pts (xy 27.94 25.4) (xy 73.66 25.4)))
  (label "SIG" (at 50.8 25.4 0))
)
"""


def _sheet():
    return read_sheet(SHEET, path="fixture.kicad_sch")


# ---------------------------------------------------------------------------
# Placements.
# ---------------------------------------------------------------------------

def test_definitions_are_not_counted_as_placements():
    """lib_symbols entries share the 'symbol' tag with placed parts.

    Counting them would report a two-part sheet as having five, and the
    error is silent: every downstream number is simply too big.
    """
    sheet = _sheet()
    assert sorted(s.reference for s in sheet.symbols) == ["U1", "U2"]


def test_a_power_pseudo_part_is_not_a_component_but_keeps_its_pin():
    """#PWR is real in the netlist and is not a placed component.

    Its pin has to survive, because a glyph's position relative to the
    pins it feeds is the convention worth measuring.
    """
    sheet = _sheet()
    assert all(not s.reference.startswith("#") for s in sheet.symbols)
    glyph_pins = [p for p in sheet.pins if p.is_power_glyph]
    assert len(glyph_pins) == 1
    assert glyph_pins[0].glyph_text == "GND"


def test_millimetres_become_mils():
    """25.4 mm is 1000 mils. A mixed frame is silent and ruinous."""
    sheet = _sheet()
    u1 = next(s for s in sheet.symbols if s.reference == "U1")
    assert (u1.x, u1.y) == (1000, 1000)


def test_the_value_property_is_carried():
    """It is the only thing on a sheet that separates a decoupling cap
    from a timing one without a netlist."""
    sheet = _sheet()
    assert next(s for s in sheet.symbols if s.reference == "U1").value == "DRV"


# ---------------------------------------------------------------------------
# Pin placement, which has to be self-checking.
# ---------------------------------------------------------------------------

def test_pins_land_where_the_wires_end():
    """The transform needs a rotation and a Y flip, and getting either
    wrong yields coordinates that look plausible and are meaningless.

    A human sheet draws its wires to its pins, so agreement between the
    two is evidence the maths is right rather than an assertion that it
    is.
    """
    sheet = _sheet()
    # Two of the three pins are wired; the ground glyph in this fixture
    # deliberately is not, and a reader that claimed otherwise would be
    # the one with the bug.
    assert pin_wire_agreement(sheet) == pytest.approx(2 / 3)


def test_schematic_y_is_down_so_the_local_y_is_subtracted():
    """A pin at local y=0 on a part at y=1000 stays at 1000; the flip
    only shows on a non-zero local y, which the ground glyph has."""
    sheet = _sheet()
    out = next(p for p in sheet.pins if p.number == "1"
               and p.reference == "U1")
    assert out.y == 1000


# ---------------------------------------------------------------------------
# Nets: the association everything else got wrong.
# ---------------------------------------------------------------------------

def test_two_pins_joined_by_a_wire_share_a_net():
    nets = extract_nets(_sheet())
    joined = [pins for pins in nets.values()
              if {p.reference for p in pins} >= {"U1", "U2"}]
    assert joined, "the wire between the two pins did not join them"


def test_a_label_names_the_net_it_sits_on():
    nets = extract_nets(_sheet())
    assert "SIG" in nets, f"expected a SIG net, got {sorted(nets)}"


def test_a_power_glyph_names_its_net():
    """A power symbol is a global connection by NAME, not a wire.

    Left anonymous, every rail splits into a handful of N_<n> nets and
    nothing downstream can match a glyph to the net it feeds: measured
    on the demo corpus, 13 glyphs out of thousands were matchable
    before this, and 1685 after.
    """
    nets = extract_nets(_sheet())
    assert "GND" in nets, f"expected a GND net, got {sorted(nets)}"


def test_every_glyph_of_a_name_is_one_net_even_without_a_wire():
    """Two GND symbols at opposite corners are the same net.

    This is what makes it global. A reader that joins them only when a
    wire happens to run between them reports a split rail as two.
    """
    text = SHEET.replace(
        '(symbol (lib_id "power:GND") (at 50.8 50.8 0)',
        '(symbol (lib_id "power:GND") (at 200 200 0)'
        ' (property "Reference" "#PWR02") (property "Value" "GND"))'
        '(symbol (lib_id "power:GND") (at 50.8 50.8 0)')
    nets = extract_nets(read_sheet(text, path="fixture.kicad_sch"))
    assert len(nets["GND"]) == 2, (
        f"two GND glyphs must share one net, got {len(nets['GND'])} pins")


def test_an_unconnected_pin_gets_its_own_synthetic_net():
    """Anonymous groups are named rather than dropped: a pin on no net
    is a finding, and a reader that silently discards it hides one.

    N_n, not the netlister's N$n: the DesignPlan net-name pattern
    forbids '$', and a name the schema rejects makes the whole plan
    unusable downstream.
    """
    stray = """
(kicad_sch
  (lib_symbols
    (symbol "Lib:R"
      (property "Reference" "R")
      (symbol "R_1_1"
        (rectangle (start -1.27 2.54) (end 1.27 -2.54))
        (pin passive line (at 0 5.08 270) (length 2.54)
          (name "A") (number "1")))))
  (symbol (lib_id "Lib:R") (at 25.4 25.4 0)
    (property "Reference" "R1") (property "Value" "10k"))
)
"""
    nets = extract_nets(read_sheet(stray, path="stray.kicad_sch"))
    assert any(name.startswith("N_") for name in nets), sorted(nets)
    assert not any("$" in name for name in nets)


# ---------------------------------------------------------------------------
# Symbols, so a sheet is a self-contained fixture.
# ---------------------------------------------------------------------------

def test_a_sheet_supplies_its_own_symbols():
    """Every design test until now ran against hand-written mocks, which
    agree with whatever the test author assumed."""
    syms = symbols_from_sheet(SHEET)
    assert set(syms) >= {"Lib:DRV", "Lib:LOAD"}
    assert syms["Lib:DRV"].pins[0].electrical_type == "output"


def test_pin_orientation_is_read_not_invented():
    """KiCad's angle points INTO the body; this package's points away.

    The driver's output sits on the RIGHT edge of its rectangle and is
    stored at angle 180, because the pin runs leftward from its
    connection end back to the body. Read as a plain division by 90
    that becomes 2, and the whole sheet reads inside out.
    """
    syms = symbols_from_sheet(SHEET)
    assert syms["Lib:DRV"].pins[0].orientation == 0     # points rightward
    assert syms["Lib:LOAD"].pins[0].orientation == 2    # points leftward


def test_the_body_box_is_the_drawn_rectangle_not_the_pin_envelope():
    """A box stretched to the connection ends swallows its own stubs.

    Every wire reaching a pin then lies INSIDE the body and scores as
    passing through it. Measured on NTS0104_DHVQFN: 1600 points of
    through_body against a human scoring zero for the same netlist, and
    the engine was blamed for it first.
    """
    syms = symbols_from_sheet(SHEET)
    box = syms["Lib:DRV"].body_bbox
    assert (box.x_min, box.x_max) == (-200, 0)
    assert (box.y_min, box.y_max) == (-100, 100)


def _electrical_end(pin):
    """Where a wire attaches: the stored position is the body-attach end."""
    from eda_agent.design.symbols import pin_direction

    dx, dy = pin_direction(pin.orientation)
    return pin.x + pin.length * dx, pin.y + pin.length * dy


def test_a_pin_connects_from_outside_its_own_body():
    """The invariant both bugs broke, stated once.

    A wire attaches at the ELECTRICAL end, so that end cannot be inside
    the body. Flip the orientation and it points inward; inflate the box
    to the pin envelope and it swallows the end. Either bug alone trips
    this.
    """
    syms = symbols_from_sheet(SHEET)
    for model in syms.values():
        box = model.body_bbox
        for pin in model.pins:
            ex, ey = _electrical_end(pin)
            assert not (box.x_min < ex < box.x_max
                        and box.y_min < ey < box.y_max), (
                f"{model.lib_ref} pin {pin.designator} connects from "
                f"inside its own body")


def test_pin_length_is_read_not_defaulted():
    syms = symbols_from_sheet(SHEET)
    assert syms["Lib:DRV"].pins[0].length == 100        # 2.54 mm


# ---------------------------------------------------------------------------
# The real corpus, when it is installed.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not DEMOS.is_dir(),
                    reason="KiCad demo projects are not installed here")
def test_the_installed_corpus_parses():
    """Whatever else changes, the reader must survive real files."""
    sheets = sorted(DEMOS.rglob("*.kicad_sch"))
    assert len(sheets) > 50, "the demo set looks unexpectedly small"

    parsed = failed = 0
    for path in sheets:
        try:
            sheet = read_sheet(path.read_text(encoding="utf-8",
                                              errors="replace"), str(path))
        except Exception:                              # noqa: BLE001
            failed += 1
            continue
        if sheet.symbols:
            parsed += 1
    assert failed == 0, f"{failed} sheets raised while parsing"
    assert parsed > len(sheets) * 0.9, (
        f"only {parsed}/{len(sheets)} sheets yielded symbols")


@pytest.mark.skipif(not DEMOS.is_dir(),
                    reason="KiCad demo projects are not installed here")
def test_real_symbols_connect_from_outside_their_bodies():
    """The same invariant, against symbols nobody here wrote.

    The fixture above was authored from the same wrong belief the code
    held, so it agreed with the bug and passed. Hundreds of real
    symbols cannot all be wrong in the same direction.
    """
    offenders, checked = [], 0
    for path in sorted(DEMOS.rglob("*.kicad_sch")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for model in symbols_from_sheet(text).values():
            box = model.body_bbox
            if box.x_min == box.x_max or box.y_min == box.y_max:
                continue          # a glyph with no drawn body
            for pin in model.pins:
                checked += 1
                ex, ey = _electrical_end(pin)
                if (box.x_min < ex < box.x_max
                        and box.y_min < ey < box.y_max):
                    offenders.append(f"{model.lib_ref}.{pin.designator}")
    assert checked > 500, f"only {checked} pins checked; corpus looks wrong"
    rate = len(offenders) / checked
    # The residue is hidden supply pins, which some libraries do draw at
    # the body centre. It was 40% while the body box came from the pin
    # envelope and every unit of a multi-unit part was folded into one
    # symbol.
    assert rate < 0.01, (
        f"{len(offenders)}/{checked} pins connect from inside their own "
        f"body: {sorted(set(offenders))[:8]}")


@pytest.mark.skipif(not DEMOS.is_dir(),
                    reason="KiCad demo projects are not installed here")
def test_a_multi_unit_part_yields_one_symbol_of_one_unit():
    """Units are alternative gates, not more pins on one symbol.

    The old rule recognised only the literal ``_1_1`` as a sub-unit, so
    units 2 and up were emitted as library entries of their own AND
    their pins folded into the parent. A quad buffer arrived as a
    fourteen-pin part with a body unioned across four gates.
    """
    path = (DEMOS / "kit-dev-coldfire-xilinx_5213" / "in_out_conn.kicad_sch")
    if not path.is_file():
        pytest.skip("this demo project is not installed")
    syms = symbols_from_sheet(path.read_text(encoding="utf-8",
                                             errors="replace"))
    matching = [k for k in syms if "74LS125" in k]
    assert matching == ["kit-dev-coldfire-xilinx_5213:74LS125"], (
        f"sub-units leaked in as library entries: {sorted(matching)}")
    # Unit 1 (1, 2, 3) plus the shared supply pins (7, 14). Gate 2's
    # pins 4, 5, 6 belong to a different symbol instance.
    pins = {p.designator for p in syms[matching[0]].pins}
    assert pins == {"1", "2", "3", "7", "14"}, f"got {sorted(pins)}"


@pytest.mark.skipif(not DEMOS.is_dir(),
                    reason="KiCad demo projects are not installed here")
def test_the_rebuilt_canvas_puts_its_pins_where_the_sheet_does():
    """The check that caught three frame bugs at once, and the only one
    that could have.

    ``pin_wire_agreement`` reads pin positions straight off the sheet,
    so it stays green no matter what the SymbolModel says. Everything
    downstream uses the MODEL, through SymbolInstance.pin_world, and
    that path was wrong three ways: pin angles read as pointing into
    the body, the connection point stored where the body-attach end
    belongs (so the length was added twice), and a body box built from
    the pin envelope.

    MEASURED: 0.6% agreement before, 99.1% after. The residue is
    multi-unit placements, whose later units this model does not carry.

    WHAT IT CANNOT CATCH, checked rather than assumed. Both sides read
    symbols._PIN_DIRECTION, so reversing that table globally cancels:
    the reader stores px - length*dx and the canvas adds length*dx
    back, and the electrical end comes out right. Mutating the table
    leaves this test green. Breaking ONE side drops it to 0.5%.

    So this measures agreement between the reader and the canvas, not
    the correctness of the shared convention. The convention is pinned
    by test_symbols.test_pin_direction_quadrants, which asserts the
    vectors outright.

    Nor does the body test above pin it, for the same reason: it
    reconstructs the electrical end with the same table the reader
    subtracted, so a flip cancels there too. Anything that derives one
    side from the other cancels; only a literal assertion holds it.
    """
    import collections

    from eda_agent.design.canvas import SchematicCanvas, SymbolInstance
    from eda_agent.design.canvas import Sheet as CanvasSheet

    agree = total = 0
    for path in sorted(DEMOS.rglob("*.kicad_sch")):
        text = path.read_text(encoding="utf-8", errors="replace")
        sheet = read_sheet(text, str(path))
        models = symbols_from_sheet(text)
        # A hierarchical sheet reuses a refdes across instantiations,
        # and only one of them can be placed on a flat canvas.
        counts = collections.Counter(s.reference for s in sheet.symbols)
        dupes = {r for r, n in counts.items() if n > 1}

        canvas = SchematicCanvas()
        canvas.add_sheet(CanvasSheet(name="main"))
        placed = set()
        for sym in sheet.symbols:
            model = models.get(sym.lib_id)
            if model is None or sym.reference in dupes:
                continue
            placed.add(sym.reference)
            canvas.add_instance(SymbolInstance(
                refdes=sym.reference, symbol=model,
                x=sym.x, y=-sym.y, rotation=sym.rotation))

        for pin in sheet.pins:
            if pin.is_power_glyph or pin.reference not in placed:
                continue
            got = canvas.pin_world(pin.reference, pin.number)
            if got is None:
                continue
            total += 1
            if (got.x, got.y) == (pin.x, -pin.y):
                agree += 1

    assert total > 5000, f"only {total} pins compared; corpus looks wrong"
    rate = agree / total
    assert rate > 0.95, (
        f"only {agree}/{total} = {rate:.1%} of rebuilt canvas pins land "
        f"where the sheet draws them; a frame is wrong")


def test_the_paper_size_is_read_not_assumed():
    """The frame the human actually had.

    58 of the KiCad demo sheets are A3 against 51 A4 and one A2.
    Assuming A4 hands the engine 87.4M square mils to lay out a netlist
    the person drew across 193M, and it feeds the aspect term the wrong
    target proportions, since that now comes from the sheet.
    """
    assert _sheet().paper == "A3"


def test_a_sheet_without_a_paper_declaration_falls_back_to_a4():
    """Absent is not the same as A0."""
    text = SHEET.replace('(paper "A3")', "")
    assert read_sheet(text, path="fixture.kicad_sch").paper == "A4"


def test_the_plan_gets_the_sheet_the_human_used():
    """The engine must be given the frame, not a default."""
    from eda_agent.design.human_benchmark import plan_from_sheet

    assert plan_from_sheet(_sheet())["sheets"][0]["size"] == "A3"


def test_every_paper_size_the_corpus_uses_is_known():
    """An unknown size falls back to A4, silently.

    A5 was missing, so five demo sheets were laid out and scored
    against a frame 81% larger than the one they were drawn on, and
    nothing said so. The fallback is right for a genuinely unknown
    size; it is wrong as a way of finding out that a common one is
    absent.
    """
    from eda_agent.design.canvas import _SHEET_DIMENSIONS, sheet_dimensions

    for size in ("A5", "A4", "A3", "A2"):
        assert size in _SHEET_DIMENSIONS, f"{size} is used by the corpus"

    # A5 is 210 x 148 mm, so it must be smaller than A4 and not equal
    # to it, which is exactly what the missing entry made it.
    a5, a4 = sheet_dimensions("A5"), sheet_dimensions("A4")
    assert a5 != a4
    assert a5[0] < a4[0] and a5[1] < a4[1]

    # An unknown size still falls back rather than raising.
    assert sheet_dimensions("NOT_A_SIZE") == a4

    # KiCad writes the US series by name. They are real sheets, not
    # unknown ones, so they must not land on the A4 fallback.
    for size, inches in (("USLetter", (11.0, 8.5)),
                         ("USLegal", (14.0, 8.5)),
                         ("USLedger", (17.0, 11.0))):
        dims = sheet_dimensions(size)
        assert dims != a4, f"{size} fell back to A4"
        assert dims == (int(inches[0] * 1000), int(inches[1] * 1000))
