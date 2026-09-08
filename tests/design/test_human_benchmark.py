# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Comparing this engine to a human on the same netlist.

The preference corpus was 312 votes on ONE design, 95% for the same
side, most cast under two seconds apart, and it produced a 58% model. A
human-drawn sheet gives the other half of a pair for nothing: same
netlist, same symbols, one layout drawn by a professional.

These tests cover the plumbing, not the verdict. Whether the engine
wins on a given sheet is a measurement, and measurements do not belong
in assertions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.design.human_benchmark import (
    _clean_net,
    _clean_refdes,
    compare_sheet,
    plan_from_sheet,
)
from eda_agent.design.kicad_sheet_reader import read_sheet
from eda_agent.design.plan import DesignPlan
from tests.design.test_kicad_sheet_reader import SHEET


def test_a_sheet_becomes_a_valid_plan():
    """The schema is the contract; the reader adapts to it."""
    payload = plan_from_sheet(read_sheet(SHEET))
    plan = DesignPlan.model_validate(payload)
    assert {p.refdes for p in plan.parts} == {"U1", "U2"}
    assert plan.nets


@pytest.mark.parametrize("raw,expected", [
    ("U1", "U1"),
    ("Module302", "MODULE302"),   # KiCad allows it, DesignPlan does not
    ("C12A", "C12A"),
    ("weird", None),              # no number: dropped, never mangled
    ("", None),
])
def test_designators_are_cleaned_or_dropped(raw, expected):
    """Mangling a refdes could collide it with a different part."""
    assert _clean_refdes(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("SIG", "SIG"),
    ("+3V3", "+3V3"),
    ("+3V3(A)", "+3V3_A_"),       # parentheses are not in the pattern
    ("N$1", "N_1"),               # the netlister convention the schema bans
    ("3V3", "N_3V3"),             # a name may not start with a digit
])
def test_net_names_are_cleaned_to_the_schema(raw, expected):
    assert _clean_net(raw) == expected


def test_two_nets_cleaning_to_the_same_name_stay_distinct():
    """Otherwise the plan describes a short that the sheet does not have."""
    sheet = read_sheet(SHEET)
    payload = plan_from_sheet(sheet)
    names = [n["name"] for n in payload["nets"]]
    assert len(names) == len(set(names))


def test_a_comparison_reports_why_it_skipped():
    """A skipped sheet must say so, not return zeros that read as a tie."""
    result = compare_sheet(SHEET, "fixture", max_parts=1)
    assert not result.ok
    assert "exceeds max_parts" in result.note
    assert result.engine_total == 0.0


def test_the_net_cap_exists_because_cost_follows_connectivity():
    """MEASURED: five parts and thirty-seven nets took 176 seconds,
    while thirty-part sheets finished in under a second. A part-only
    guard admits exactly the wrong sheets."""
    result = compare_sheet(SHEET, "fixture", max_nets=0)
    assert not result.ok
    assert "exceeds max_nets" in result.note


def test_the_human_layout_is_scored_in_the_canvas_frame():
    """KiCad Y is DOWN and the canvas is Y-UP.

    Without the flip every vertical convention reads inverted, which is
    silent: the score still comes out a number.
    """
    import inspect

    from eda_agent.design import human_benchmark

    source = inspect.getsource(human_benchmark.human_canvas_from_sheet)
    assert "y=-sym.y" in source, "the Y flip is missing"


def test_a_sheet_instantiated_twice_is_skipped_not_crashed():
    """A flat canvas holds one instance per refdes.

    fpga-hp-banks.kicad_sch places IC14 twice, and add_instance raised
    ValueError, losing the sheet with an exception instead of a reason.
    Comparing a multi-instance sheet flatly would not mean anything, so
    it is skipped and said.

    The count must be taken over what will be PLACED, not over the
    plan's parts: the plan holds only symbols sitting on a net, so a
    repeated instance missing from it passed an earlier version of this
    check and still collided.
    """
    from eda_agent.design.human_benchmark import compare_sheet

    text = """
(kicad_sch
  (lib_symbols
    (symbol "Lib:R"
      (property "Reference" "R")
      (symbol "R_1_1"
        (rectangle (start -1.27 2.54) (end 1.27 -2.54))
        (pin passive line (at 0 5.08 270) (length 2.54)
          (name "A") (number "1"))
        (pin passive line (at 0 -5.08 90) (length 2.54)
          (name "B") (number "2")))))
  (symbol (lib_id "Lib:R") (at 25.4 25.4 0)
    (property "Reference" "R1") (property "Value" "10k"))
  (symbol (lib_id "Lib:R") (at 50.8 25.4 0)
    (property "Reference" "R1") (property "Value" "10k"))
  (wire (pts (xy 25.4 20.32) (xy 50.8 20.32)))
)
"""
    result = compare_sheet(text, "dup.kicad_sch")
    assert "more than once" in result.note, result.note
    assert "R1" in result.note


_DEMOS = Path("C:/Program Files/KiCad/10.0/share/kicad/demos")


@pytest.mark.skipif(not _DEMOS.is_dir(),
                    reason="KiCad demo projects are not installed here")
def test_the_engine_matches_the_human_on_a_sheet_it_used_to_scatter():
    """End to end, because the wiring is what the structural test cannot
    check.

    subsheet2 is the sheet the tight tolerance missed: before the
    shared-axis pass the engine scored 1.00 here, meaning NO part shared
    a row or column with any other, against the human's 0.20. It needs
    the looser of the two tolerances, so this fails if either the pass
    is dropped, the loose tolerance is removed, or the candidate stops
    being offered to the scorer.
    """
    from eda_agent.design.human_benchmark import compare_sheet

    matches = list(_DEMOS.rglob("subsheet2.kicad_sch"))
    if not matches:
        pytest.skip("this demo project is not installed")
    path = matches[0]
    result = compare_sheet(
        path.read_text(encoding="utf-8", errors="replace"), str(path))
    assert result.ok, result.note

    human = (result.human_breakdown or {}).get("alignment", 0.0) / 40.0
    engine = (result.engine_breakdown or {}).get("alignment", 0.0) / 40.0
    assert engine <= human + 0.01, (
        f"the engine scatters this sheet again: alignment {engine:.2f} "
        f"against the human's {human:.2f}")


@pytest.mark.skipif(not _DEMOS.is_dir(),
                    reason="KiCad demo projects are not installed here")
def test_convention_cost_is_reported_beside_the_total_not_inside_it():
    """The engine ranks on score PLUS convention; this reports both.

    Folding convention into the totals would silently change what every
    number in this comparison means, and half of the pipeline's terms
    (forced label demotions, span-labelled mils) have no analogue on a
    sheet a person drew. So the total stays the sum of its breakdown
    and the convention cost sits beside it.

    USB is the sheet that showed why it has to be reported at all: the
    HUMAN carries 360 of convention cost there, three pin-side
    violations, against the engine's 120.
    """
    from eda_agent.design.human_benchmark import compare_sheet

    matches = list(_DEMOS.rglob("USB.kicad_sch"))
    if not matches:
        pytest.skip("this demo project is not installed")
    path = matches[0]
    result = compare_sheet(
        path.read_text(encoding="utf-8", errors="replace"), str(path))
    assert result.ok, result.note

    for total, breakdown in ((result.human_total, result.human_breakdown),
                             (result.engine_total, result.engine_breakdown)):
        assert abs(total - sum(breakdown.values())) < 0.01, (
            "the total must remain the sum of its breakdown; convention "
            "cost belongs beside it, not folded in")

    assert result.human_convention > 0, (
        "USB's human sheet has pin-side violations and they must be seen")


def test_both_canvases_are_judged_against_the_same_frame():
    """The aspect term's target comes from the sheet's proportions.

    A3 is 1.415 wide and A4 is 1.513, so leaving the human canvas on
    the default while the plan hands the engine A3 scores the two
    against different targets.
    """
    import inspect

    from eda_agent.design import human_benchmark

    source = inspect.getsource(human_benchmark.human_canvas_from_sheet)
    assert 'CanvasSheet(name="main", size=sheet.paper' in source, (
        "the human canvas must be built on the frame the human used")


_RAIL_SHEET = """
(kicad_sch
  (paper "A4")
  (lib_symbols
    (symbol "Lib:R"
      (property "Reference" "R")
      (symbol "R_1_1"
        (rectangle (start -1.27 2.54) (end 1.27 -2.54))
        (pin passive line (at 0 5.08 270) (length 2.54)
          (name "A") (number "1"))
        (pin passive line (at 0 -5.08 90) (length 2.54)
          (name "B") (number "2"))))
    (symbol "power:GND"
      (property "Reference" "#PWR")
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "GND") (number "1")))))
  (symbol (lib_id "Lib:R") (at 25.4 25.4 0)
    (property "Reference" "R1") (property "Value" "10k"))
  (symbol (lib_id "Lib:R") (at 50.8 25.4 0)
    (property "Reference" "R2") (property "Value" "10k"))
  (symbol (lib_id "power:GND") (at 38.1 30.48 0)
    (property "Reference" "#PWR01") (property "Value" "GND"))
  (wire (pts (xy 25.4 30.48) (xy 50.8 30.48)))
  (wire (pts (xy 25.4 20.32) (xy 50.8 20.32)))
  (label "SIG" (at 38.1 20.32 0))
)
"""


def test_a_net_carrying_a_power_symbol_is_flagged_as_a_rail():
    """The plan is where the engine learns which nets are rails.

    Left unflagged it falls back to matching names, which catches GND
    and VDD and misses whatever else a designer named, so the engine
    was being handed less than a real plan carries.

    The sheet needs a rail with two PART pins on it: a net whose only
    member is the glyph is dropped before it can be flagged, which is
    what the reader's own fixture does and why it cannot test this.
    """
    from eda_agent.design.kicad_sheet_reader import read_sheet
    from eda_agent.design.human_benchmark import plan_from_sheet

    sheet = read_sheet(_RAIL_SHEET, path="rail.kicad_sch")
    nets = {n["name"]: n for n in plan_from_sheet(sheet)["nets"]}
    assert "GND" in nets, sorted(nets)
    assert nets["GND"].get("is_ground") is True
    assert not nets["GND"].get("is_power")

    # A signal net the human did not hang a glyph on stays unflagged.
    assert "SIG" in nets, sorted(nets)
    assert not nets["SIG"].get("is_power")
    assert not nets["SIG"].get("is_ground")
