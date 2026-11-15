# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Package sanity checks: each misreading caught, no real package flagged.

Every package below is a datasheet case from ``_landpattern_cases`` (the
source and page are in that file), with one number changed the way a
person reading the drawing gets it wrong: the pitch read from the body
length, mils typed as mm, D and E swapped, E and E1 swapped, the lead
width larger than the pitch, an exposed pad bigger than the body, a foot
read as the whole lead. Each must be caught, and the message must name
the dimensions, their values and where they came from.
"""

from __future__ import annotations

import pytest

from eda_agent.design.landpattern.check import CHECK_IDS, check_package
from tests.design import _landpattern_cases as cases


def _with(make, **changes) -> dict:
    d = make().to_dict()
    d.update(changes)
    return d


def _hits(report: dict, check: str, severity: str = "error") -> list[dict]:
    found = [f for f in report["findings"]
             if f["check"] == check and f["severity"] == severity]
    assert found, (check, severity, report["findings"])
    return found


@pytest.mark.parametrize("name", sorted(cases.ALL_CASES))
def test_every_datasheet_case_passes_clean(name):
    r = check_package(cases.ALL_CASES[name]())
    assert r["ok"], r["findings"]
    assert r["findings"] == [], r["findings"]
    assert r["examined"] == ["readable", "family", *CHECK_IDS]


def test_the_case_file_covers_34_packages():
    assert len(cases.ALL_CASES) == 34


# --- the mistake classes --------------------------------------------------------


def test_pitch_read_as_the_body_length():
    """TI D0008A: body D 4.81-5.00 read into the pitch."""
    r = check_package(_with(cases.soic8, pitch=4.9))
    f = _hits(r, "row_fits_body")[0]
    assert "pitch 4.9 mm" in f["message"] and "body_y" in f["message"]
    assert "15.21 mm" in f["message"]
    assert "body length read as the pitch" in f["hint"]
    assert {d["name"] for d in f["dims"]} == {"pitch", "lead_width", "body_y"}
    # The lead width keeps its own citation, the drawing page.
    width = next(d for d in f["dims"] if d["name"] == "lead_width")
    assert "D0008A package outline, p. 54" in width["cite"]


def test_mils_typed_as_mm_for_the_pitch():
    r = check_package(_with(cases.soic8, pitch=50))
    f = _hits(r, "units")[0]
    assert "50 as mils is 1.27 mm" in f["message"]
    assert f["hint"] == "mils typed as mm"
    assert "mils typed as mm" in _hits(r, "row_fits_body")[0]["hint"]


def test_mils_typed_as_mm_for_the_lead_width():
    """D0008A b is 0.31-0.51 mm, 12.2-20.1 mil."""
    r = check_package(_with(cases.soic8, lead_width=[12.2, 20.1]))
    f = _hits(r, "units")[0]
    assert "lead_width 12.2 to 20.1 mm" in f["message"]
    assert "0.4102 mm" in f["message"]
    _hits(r, "terminal_vs_pitch")


def test_an_outline_in_mils_is_flagged_with_the_conversion():
    """D0008A E1, D and E entered in mils (150-157, 189-197, 228-244)."""
    r = check_package(_with(cases.soic8, body_x=[150, 157], body_y=[189, 197],
                            lead_span_x=[228, 244]))
    f = _hits(r, "row_fits_body", "warning")[0]
    assert "the body in mils?" in f["hint"] and "4.9022 mm" in f["hint"]
    units = _hits(r, "units", "warning")
    assert {u["dims"][0]["name"] for u in units} == {"body_x", "body_y",
                                                      "lead_span_x"}


def test_swapped_d_and_e():
    r = check_package(_with(cases.soic8, body_x=[4.81, 5.00],
                            body_y=[3.81, 3.98]))
    f = _hits(r, "row_fits_body")[0]
    assert "look swapped" in f["hint"]
    assert "body_y 3.81 to 3.98 mm" in f["message"]


def test_swapped_e_and_e1():
    """Lead span (E) and body width (E1) exchanged."""
    r = check_package(_with(cases.soic8, lead_span_x=[3.81, 3.98],
                            body_x=[5.80, 6.19]))
    f = _hits(r, "span_vs_body")[0]
    assert "JEDEC E" in f["hint"] and "E1" in f["hint"]
    assert "lead_span_x 3.81 to 3.98 mm" in f["message"]


def test_lead_width_larger_than_the_pitch():
    r = check_package(_with(cases.soic8, lead_width=[1.30, 1.40]))
    f = _hits(r, "terminal_vs_pitch")[0]
    assert "is not below pitch 1.27 mm" in f["message"]
    assert "touch" in f["message"]


def test_lead_width_equal_to_the_pitch_names_the_likely_misread():
    r = check_package(_with(cases.tssop8, lead_width=0.65))
    assert "pitch read into lead_width" in _hits(r, "terminal_vs_pitch")[0]["hint"]


def test_exposed_pad_larger_than_the_body():
    """TI RGT0016C body 2.9-3.1, exposed pad typed as 3.2-3.3."""
    r = check_package(_with(cases.qfn16, ep_x=[3.2, 3.3], ep_y=[3.2, 3.3]))
    msgs = [f["message"] for f in _hits(r, "exposed_pad")]
    assert any("ep_x 3.2 to 3.3 mm" in m and "larger than body_x" in m
               for m in msgs)


def test_exposed_pad_over_the_terminals():
    r = check_package(_with(cases.qfn16, ep_x=[2.4, 2.6], ep_y=[2.4, 2.6]))
    f = _hits(r, "exposed_pad")[0]
    assert "overlaps the terminals" in f["message"]
    assert "2.2 mm between them" in f["message"]


def test_exposed_pad_turned_the_other_way_to_the_body():
    """TI BQA0014A: body 2.4-2.6 x 2.9-3.1, pad 0.9-1.1 x 1.4-1.6, swapped."""
    r = check_package(_with(cases.qfn14_rect, ep_x=[1.4, 1.6], ep_y=[0.9, 1.1]))
    assert any("ep_x and ep_y swapped" in f.get("hint", "")
               for f in _hits(r, "exposed_pad", "warning"))


def test_ball_as_big_as_the_pitch():
    r = check_package(_with(cases.bga80, ball_diameter=[0.5, 0.6]))
    assert "merge" in _hits(r, "terminal_vs_pitch")[0]["message"]
    r = check_package(_with(cases.bga80, ball_diameter=[0.35, 0.45]))
    assert "80 % of pitch" in _hits(r, "terminal_vs_pitch", "warning")[0]["message"]


def test_foot_read_as_the_whole_lead():
    """D0008A: the foot L is 0.41-1.27; 2.5-3.1 is longer than the lead's
    reach past the body ((6.19 - 3.81) / 2 = 1.19)."""
    r = check_package(_with(cases.soic8, lead_length=[2.5, 3.1]))
    f = _hits(r, "terminal_length")[0]
    assert "1.19 mm" in f["message"] and "JEDEC L" in f["hint"]


def test_two_terminals_that_meet_in_the_middle():
    """KEMET 0603 is 1.45-1.75 long; a band of 0.9 nominal leaves nothing."""
    r = check_package(_with(lambda: cases.kemet_chip("0603"),
                            lead_length=[0.8, 1.0]))
    assert "meet in the middle" in _hits(r, "terminal_length")[0]["message"]


def test_limits_out_of_order_are_reported_once():
    r = check_package(_with(cases.soic8, lead_span_x={"min": 6.19, "nom": 6.0,
                                                      "max": 5.8}))
    assert [f["check"] for f in r["findings"]] == ["limits"]
    assert "out of order" in r["findings"][0]["message"]


@pytest.mark.parametrize("make,changes,needle", [
    (cases.soic8, {"pins": 7}, "odd pin count"),
    (cases.sot23_3, {"pins": 4}, "3, 5 or 6"),
    (cases.qfn16, {"pins": 18}, "four equal sides"),
    (cases.qfn14_rect, {"pins_per_side": [5, 2, 5, 3]}, "adding up to"),
    (cases.bga80, {"pins": 81}, "has 80"),
    (cases.bga80, {"depopulated": ["Z9"]}, "not in a 9 x 9 grid"),
    # A depopulated BGA described without its count came out full.
    (cases.bga80, {"pins": 0, "depopulated": ()}, "pins is required for bga"),
    (cases.bme280_lga, {"pins": 0}, "pins is required for lga"),
    (cases.usb_b, {"pins": 5}, "4 numbered contacts"),
])
def test_pin_counts_the_family_cannot_have(make, changes, needle):
    r = check_package(_with(make, **changes))
    assert any(needle in f["message"] for f in _hits(r, "pin_count")), \
        r["findings"]


def test_tab_wider_than_the_body():
    """Diodes SOT223 D 6.45-6.55; tab b1 2.90-3.10 typed as 7.0."""
    r = check_package(_with(cases.sot223, tab_width=7.0))
    assert "tab comes out of the body" in _hits(r, "tab")[0]["message"]


def test_standoff_above_the_top():
    r = check_package(_with(cases.sot223, standoff=[1.70, 1.80]))
    assert "swapped" in _hits(r, "height")[0]["hint"]


def test_inches_typed_as_mm():
    r = check_package(_with(cases.soic8, pitch=0.05))
    assert "1.27 mm" in _hits(r, "units", "warning")[0]["message"]


def test_vendor_pattern_inconsistent_with_itself():
    """TI D0008A example layout: land 1.55 x 0.6 on 5.4. A land width read
    as 1.6 overlaps its neighbour at 1.27; a span read as 1.5 overlaps the
    other row."""
    d = cases.soic8().to_dict()
    d["vendor"]["pad_width"] = 1.6
    r = check_package(d)
    f = _hits(r, "vendor_pattern")[0]
    assert "p. 55" in f["message"] and "touch" in f["message"]
    d = cases.soic8().to_dict()
    d["vendor"]["span"] = 1.5
    assert "overlap" in _hits(check_package(d), "vendor_pattern")[0]["message"]


def test_unreadable_and_unknown_packages():
    r = check_package("{not json")
    assert not r["ok"] and r["findings"][0]["check"] == "readable"
    r = check_package({"family": "tsop99", "pins": 8})
    assert not r["ok"] and r["findings"][0]["check"] == "family"
    r = check_package({"family": "soic", "pins": 8})
    assert {f["check"] for f in r["findings"]} == {"required"}


def test_nominal_only_terminals_are_a_note_not_a_finding():
    """Abracon ABS07 prints its terminal length and span without a
    tolerance; that is how the datasheet is, not a misreading."""
    r = check_package(cases.abs07_crystal())
    assert r["findings"] == []
    assert any("no tolerance" in n for n in r["notes"])
