# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The hand-template schematic builder: anchors, corners, splits, checks."""

from __future__ import annotations

import pytest

from eda_agent.design import sch_template as st

# A two-pin passive as lib_create_passive_symbol draws it: pins at the body
# ends, 100 mil long, pointing out along x.
PASSIVE = [
    {"designator": "1", "name": "1", "x": -100, "y": 0, "orientation": 2, "length": 100},
    {"designator": "2", "name": "2", "x": 100, "y": 0, "orientation": 0, "length": 100},
]
# A small IC: two pins left, two right, 200 mil long.
IC = [
    {"designator": "1", "name": "VIN", "x": 0, "y": 0, "orientation": 2, "length": 200},
    {"designator": "2", "name": "GND", "x": 0, "y": -200, "orientation": 2, "length": 200},
    {"designator": "3", "name": "OUT", "x": 600, "y": 0, "orientation": 0, "length": 200},
    {"designator": "4", "name": "GND", "x": 600, "y": -200, "orientation": 0, "length": 200},
]


def _pins(**parts):
    out = {}
    for ref, (lib, x, y, rot) in parts.items():
        out[ref] = st.place_symbol_pins(ref, lib, x, y, rot)
    return out


def test_pin_ends_follow_rotation():
    p = {p.number: p for p in st.place_symbol_pins("C1", PASSIVE, 1000, 2000, 90)}
    # Rotated 90: pin 1 (left) now points down, its end 200 below the origin.
    assert (p["1"].x, p["1"].y, p["1"].orientation) == (1000, 1800, 3)
    assert (p["2"].x, p["2"].y, p["2"].orientation) == (1000, 2200, 1)


def test_pins_from_sheet_adds_the_length_along_the_orientation():
    rows = [{"refdes": "U1", "pin_number": "1", "pin_name": "VIN", "x_mils": 3700,
             "y_mils": 9200, "orientation": 2, "pin_length_mils": 300}]
    pin = st.pins_from_sheet(rows)["U1"][0]
    assert (pin.x, pin.y) == (3400, 9200)


def test_an_anchor_by_name_and_by_number_and_an_ambiguous_name():
    pins = _pins(U1=(IC, 1000, 1000, 0))
    d = st.build({"parts": [{"designator": "U1"}],
                  "wires": [{"path": ["U1.VIN", [600, 1000]]},
                            {"path": ["U1.GND", [600, 800]]}]}, pins)
    assert any("2 pins are named GND" in e["detail"] for e in d.errors)
    d = st.build({"parts": [{"designator": "U1"}],
                  "wires": [{"path": ["U1.VIN", [600, 1000]]},
                            {"path": ["U1.2", [600, 800]]}]}, pins)
    assert d.ok, d.errors


def test_a_diagonal_gets_one_corner_in_the_asked_order():
    d = st.build({"parts": [], "wires": [
        {"path": [[0, 0], [300, 200]]},
        {"path": [[1000, 0], [1300, 200]], "corner": "vh"}]}, {})
    ends = sorted((s.x1, s.y1, s.x2, s.y2) for s in d.segments)
    assert (0, 0, 300, 0) in ends and (300, 0, 300, 200) in ends
    assert (1000, 0, 1000, 200) in ends and (1000, 200, 1300, 200) in ends


def _buck_like():
    """VIN rail with a cap hanging to ground, an IC, a divider."""
    pins = _pins(U1=(IC, 1000, 1000, 0), C1=(PASSIVE, 500, 700, 90))
    template = {
        "parts": [{"designator": "U1", "x": 1000, "y": 1000},
                  {"designator": "C1", "x": 500, "y": 700, "rotation": 90}],
        "wires": [
            {"net": "VIN", "path": [[300, 1000], "U1.VIN"]},
            {"net": "VIN", "path": [[500, 1000], "C1.2"]},
            {"net": "GND", "path": ["C1.1", [500, 400]]},
            {"net": "GND", "path": ["U1.2", [700, 400]]},
            {"net": "GND", "path": [[500, 400], [700, 400]]},
        ],
        "ports": [{"net": "VIN", "at": [300, 1000], "style": "bar"},
                  {"net": "GND", "at": [600, 400]}],
        "nets": {"VIN": ["U1.VIN", "C1.2"], "GND": ["U1.2", "C1.1"]},
    }
    return template, pins


def test_a_clean_template_passes_and_names_altiums_junctions():
    template, pins = _buck_like()
    d = st.build(template, pins)
    assert d.ok, d.errors
    # The cap's wire ends on the rail's middle: a T, split into a vertex.
    assert (500, 1000) in d.auto_junctions
    assert set(d.netlist["VIN"]) == {"U1.1", "C1.2"}
    assert set(d.netlist["GND"]) == {"U1.2", "C1.1"}
    assert "U1.3" in d.unconnected_pins
    port = next(p for p in d.ports if p["net"] == "GND")
    assert (port["style"], port["orientation"]) == ("gnd_power", 3)


def test_two_names_on_one_piece_of_copper_is_a_short():
    template, pins = _buck_like()
    template["wires"].append({"net": "GND", "path": [[500, 1000], [500, 1100]]})
    d = st.build(template, pins)
    assert any(e["check"] == "short" for e in d.errors)


def test_the_expected_netlist_catches_a_missing_and_an_extra_pin():
    template, pins = _buck_like()
    template["nets"]["VIN"].append("U1.OUT")
    d = st.build(template, pins)
    assert any(e["check"] == "netlist" and e["where"] == "VIN" for e in d.errors)


def test_wires_on_top_of_each_other_are_refused():
    d = st.build({"parts": [], "wires": [{"path": [[0, 0], [500, 0]]},
                                         {"path": [[300, 0], [800, 0]]}]}, {})
    assert any(e["check"] == "overlap" for e in d.errors)


def test_a_crossing_a_body_and_a_dangling_end_are_warned():
    pins = _pins(U1=(IC, 1000, 1000, 0))
    d = st.build({"parts": [{"designator": "U1"}], "wires": [
        {"path": [[900, 900], [1800, 900]]},          # through U1's body
        {"path": [[1300, 700], [1300, 1200]]},        # crosses it
    ]}, pins)
    checks = {w["check"] for w in d.warnings}
    assert {"through_body", "crossing", "dangling"} <= checks


def test_a_port_on_nothing_is_an_error_and_an_unknown_style_too():
    d = st.build({"parts": [], "wires": [], "ports": [
        {"net": "VCC", "at": [0, 0]},
        {"net": "GND", "at": [0, 0], "style": "power_ground"}]}, {})
    checks = [e["check"] for e in d.errors]
    assert "floating" in checks and "ports" in checks


def test_off_grid_points_are_reported():
    d = st.build({"parts": [], "wires": [{"path": [[0, 0], [150, 0]]}]}, {})
    assert any(w["check"] == "off_grid" for w in d.warnings)


def test_an_unknown_template_key_is_refused():
    with pytest.raises(st.TemplateError):
        st.build({"parts": [], "wire": []}, {})


def test_the_preview_png_is_written(tmp_path):
    pytest.importorskip("matplotlib")
    template, pins = _buck_like()
    d = st.build(template, pins)
    out = st.render_png(d, str(tmp_path / "t.png"), title="buck")
    assert (tmp_path / "t.png").stat().st_size > 1000 and out.endswith("t.png")


def _texts(d):
    return [w for w in d.warnings if w["check"] == "text_overlap"]


def test_a_ground_symbol_under_a_value_is_warned_about():
    """A cap value sat over its ground symbol on screen and was found only
    on the Altium screenshot."""
    pins = _pins(C1=(PASSIVE, 1000, 1000, 90), C2=(PASSIVE, 1200, 1000, 90))
    d = st.build({"parts": [
        {"designator": "C1", "lib_reference": "C", "x": 1000, "y": 1000,
         "rotation": 90, "value": "100nF"},
        {"designator": "C2", "lib_reference": "C", "x": 1200, "y": 1000,
         "rotation": 90, "value": "100nF"}],
        "ports": [{"net": "GND", "at": "C1.1"}, {"net": "GND", "at": "C2.1"}]}, pins)
    found = _texts(d)
    assert found and any("value" in w["detail"] for w in found), d.warnings


def test_parts_spaced_apart_draw_no_text_warning():
    pins = _pins(R1=(PASSIVE, 1000, 1000, 0), R2=(PASSIVE, 3000, 3000, 0))
    d = st.build({"parts": [
        {"designator": "R1", "lib_reference": "R", "x": 1000, "y": 1000, "value": "10k"},
        {"designator": "R2", "lib_reference": "R", "x": 3000, "y": 3000, "value": "10k"}]},
        pins)
    assert not _texts(d), _texts(d)
