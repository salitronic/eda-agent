# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for parametric standard-footprint geometry (pure Python)."""

from __future__ import annotations

import pytest

from eda_agent.design.footprint_gen import generate_footprint, FootprintGeometry


def test_chip_two_pads_symmetric():
    g = generate_footprint("chip", 2, pitch=40, pad_w=24, pad_h=30)
    assert isinstance(g, FootprintGeometry)
    assert len(g.pads) == 2
    xs = sorted(p["x"] for p in g.pads)
    assert xs == [-20, 20]                       # +-pitch/2
    assert all(p["y"] == 0 for p in g.pads)
    assert all(p["x_size"] == 24 and p["y_size"] == 30 for p in g.pads)
    assert {p["designator"] for p in g.pads} == {"1", "2"}
    assert g.width_mils == 40 + 24


def test_silk_and_courtyard_emitted():
    # A chip whose body reaches past its pads keeps the outline's long
    # sides; the short sides cross the pads and are cut away.
    g = generate_footprint("chip", 2, pitch=40, pad_w=24, pad_h=30,
                           body_w=60, body_h=60, silk=True, courtyard=10)
    assert len(g.silk_tracks) == 2
    assert len(g.courtyard_tracks) == 4
    # courtyard sits on a mechanical layer, silk on overlay
    assert all(t["layer"] == "TopOverlay" for t in g.silk_tracks)
    assert all(t["layer"].startswith("Mechanical") for t in g.courtyard_tracks)
    # courtyard extends past the body and silk by `courtyard`
    cx = max(t["x1"] for t in g.courtyard_tracks)
    assert cx == pytest.approx(30 + 6 + 10)   # ink outside the body


def test_courtyard_zero_disables():
    g = generate_footprint("chip", 2, courtyard=0)
    assert g.courtyard_tracks == ()


def test_dual_ccw_numbering_and_count():
    # SOIC-8: 4 per row, row span 150, pitch 50.
    g = generate_footprint("dual", 8, pitch=50, pad_w=30, pad_h=20,
                           row_span=150)
    assert len(g.pads) == 8
    by = {p["designator"]: p for p in g.pads}
    # pin 1 = top of LEFT column; pin 8 = top of RIGHT column (CCW)
    assert by["1"]["x"] == -75 and by["1"]["y"] == 75
    assert by["4"]["x"] == -75 and by["4"]["y"] == -75   # bottom-left
    assert by["5"]["x"] == 75 and by["5"]["y"] == -75    # bottom-right
    assert by["8"]["x"] == 75 and by["8"]["y"] == 75     # top-right
    assert g.width_mils == 150 + 30


def test_quad_four_sides():
    # QFP-16: 4 per side, row span 200, pitch 50.
    g = generate_footprint("quad", 16, pitch=50, pad_w=20, pad_h=30,
                           row_span=200)
    assert len(g.pads) == 16
    # pin 1 sits on the left side (x == -row_span/2)
    p1 = next(p for p in g.pads if p["designator"] == "1")
    assert p1["x"] == -100
    # exactly 4 pads on each side (by x or y == +-100)
    left = [p for p in g.pads if p["x"] == -100]
    right = [p for p in g.pads if p["x"] == 100]
    assert len(left) == 4 and len(right) == 4


def test_quad_requires_divisible_by_four():
    with pytest.raises(ValueError):
        generate_footprint("quad", 10, pitch=50, row_span=200)


def test_dual_requires_row_span():
    with pytest.raises(ValueError):
        generate_footprint("dual", 8, pitch=50)


def test_unknown_family_rejected():
    with pytest.raises(ValueError):
        generate_footprint("bga", 64, pitch=50, row_span=200)


def test_default_shape_is_roundrect():
    g = generate_footprint("chip", 2)
    assert all(p["shape"] == "roundrect" for p in g.pads)


def test_deterministic():
    a = generate_footprint("quad", 32, pitch=40, pad_w=18, pad_h=28, row_span=260)
    b = generate_footprint("quad", 32, pitch=40, pad_w=18, pad_h=28, row_span=260)
    assert a == b


def test_sip_single_row():
    g = generate_footprint("sip", 4, pitch=100, pad_w=60, pad_h=60)
    assert len(g.pads) == 4
    xs = [p["x"] for p in g.pads]
    assert xs == [-150, -50, 50, 150]            # centred, pin 1 leftmost
    assert all(p["y"] == 0 for p in g.pads)


def test_through_hole_pin1_rectangular_rest_round():
    g = generate_footprint("sip", 3, pitch=100, pad_w=60, pad_h=60, hole=40)
    shapes = {p["designator"]: p["shape"] for p in g.pads}
    assert shapes["1"] == "rectangular"          # pin-1 orientation marker
    assert shapes["2"] == "round" and shapes["3"] == "round"
    assert all(p["hole_size"] == 40 for p in g.pads)


def test_dip8_is_dual_through_hole():
    g = generate_footprint("dual", 8, pitch=100, pad_w=60, pad_h=60,
                           row_span=300, hole=32)
    assert len(g.pads) == 8
    assert all(p["hole_size"] == 32 for p in g.pads)
    p1 = next(p for p in g.pads if p["designator"] == "1")
    assert p1["shape"] == "rectangular" and p1["x"] == -150


def test_smd_pads_have_zero_hole():
    g = generate_footprint("chip", 2, pitch=40)
    assert all(p["hole_size"] == 0 for p in g.pads)


def test_sot23_is_dual_three_pin():
    g = generate_footprint("dual", 3, pitch=95, pad_w=24, pad_h=40,
                           row_span=200)
    assert len(g.pads) == 3
    left = [p for p in g.pads if p["x"] < 0]
    right = [p for p in g.pads if p["x"] > 0]
    assert len(left) == 2 and len(right) == 1    # 2+1 = SOT-23


def test_sot23_5_pins_4_and_5_face_pins_3_and_1():
    """SOT-23-5 / SC-70-5: pins 1, 2, 3 down the left at the pitch, pins 4
    and 5 opposite pins 3 and 1, nothing opposite pin 2. Centring the
    right column put 4 and 5 half a pitch off the pads they face."""
    g = generate_footprint("dual", 5, pitch=37.4, pad_w=42, pad_h=24,
                           row_span=102)
    y = {p["designator"]: p["y"] for p in g.pads}
    x = {p["designator"]: p["x"] for p in g.pads}
    assert y["1"] == pytest.approx(37.4) and y["3"] == pytest.approx(-37.4)
    assert y["4"] == pytest.approx(y["3"]) and y["5"] == pytest.approx(y["1"])
    assert x["4"] == x["5"] == pytest.approx(51) and x["1"] == pytest.approx(-51)
    # Unchanged: an even count, and the three-pin SOT-23 (pin 3 centred).
    g3 = generate_footprint("dual", 3, pitch=75, pad_w=24, pad_h=40,
                            row_span=200)
    assert next(p["y"] for p in g3.pads if p["designator"] == "3") == 0
    g8 = generate_footprint("dual", 8, pitch=50, pad_w=24, pad_h=60,
                            row_span=200)
    assert sorted(p["y"] for p in g8.pads if p["x"] > 0) == [-75, -25, 25, 75]


def test_bga_grid_count_and_designators():
    g = generate_footprint("bga", 0, pitch=40, pad_w=20, rows=4, cols=5)
    assert len(g.pads) == 20
    desigs = {p["designator"] for p in g.pads}
    # JEDEC row letters A..D, cols 1..5; A1 present, no 'I' row at index 8
    assert "A1" in desigs and "D5" in desigs
    a1 = next(p for p in g.pads if p["designator"] == "A1")
    # A1 is top-left: most negative x, most positive y
    assert a1["x"] == min(p["x"] for p in g.pads)
    assert a1["y"] == max(p["y"] for p in g.pads)
    assert all(p["shape"] == "round" and p["hole_size"] == 0 for p in g.pads)


def test_bga_row_letters_skip_ioqsxz():
    # 9 rows -> A B C D E F G H J (skip I at index 8)
    g = generate_footprint("bga", 0, pitch=40, rows=9, cols=1)
    rows_used = sorted({p["designator"][0] for p in g.pads})
    assert "I" not in rows_used
    assert rows_used[-1] == "J"          # 9th row letter is J, not I


def test_bga_requires_rows_cols():
    with pytest.raises(ValueError):
        generate_footprint("bga", 0, pitch=40)


def test_qfn_exposed_pad():
    g = generate_footprint("quad", 16, pitch=50, pad_w=20, pad_h=30,
                           row_span=200, exposed_pad=120)
    assert len(g.pads) == 17                     # 16 perimeter + 1 EP
    ep = g.pads[-1]
    assert ep["designator"] == "17"              # pin_count+1
    assert ep["x"] == 0 and ep["y"] == 0         # centred
    assert ep["x_size"] == 120 and ep["y_size"] == 120


def test_quad_no_exposed_pad_by_default():
    g = generate_footprint("quad", 16, pitch=50, row_span=200)
    assert len(g.pads) == 16


def test_tab_package_signal_pins_plus_tab():
    # SOT-223: 3 signal pads on one row + a large tab pad (pin 4) opposite
    g = generate_footprint("tab", 3, pitch=90, pad_w=24, pad_h=60,
                           row_span=260, tab_w=140, tab_h=80)
    assert len(g.pads) == 4
    by = {p["designator"]: p for p in g.pads}
    # signal pins 1..3 on one row, evenly pitched
    assert [by[str(k)]["y"] for k in (1, 2, 3)] == [-130, -130, -130]
    assert by["1"]["x"] < by["2"]["x"] < by["3"]["x"]
    # tab is pin 4, opposite row, centred, larger
    assert by["4"]["y"] == 130 and by["4"]["x"] == 0
    assert by["4"]["x_size"] == 140 and by["4"]["y_size"] == 80
    assert by["4"]["shape"] == "rectangular"


def test_tab_requires_tab_size():
    with pytest.raises(ValueError, match="tab_w > 0"):
        generate_footprint("tab", 3, pitch=90, pad_w=24, pad_h=60,
                           row_span=260)


def test_tab_requires_row_span():
    with pytest.raises(ValueError, match="row_span"):
        generate_footprint("tab", 3, pitch=90, pad_w=24, pad_h=60,
                           tab_w=140, tab_h=80)


def test_tab_through_hole_to220():
    # TO-220: 3 leads through-hole + a tab pad
    g = generate_footprint("tab", 3, pitch=100, pad_w=60, pad_h=60,
                           row_span=300, tab_w=200, tab_h=120, hole=40)
    leads = [p for p in g.pads if p["designator"] in ("1", "2", "3")]
    assert all(p["hole_size"] == 40 for p in leads)
    assert next(p for p in g.pads if p["designator"] == "1")["shape"] \
        == "rectangular"   # pin-1 marker on through-hole


def test_header_two_row_col_major_numbering():
    # 2x3 header: col-major odd/even -- 1 top of col0, 2 below it, 3 top col1...
    g = generate_footprint("header", 6, pitch=100, pad_w=60, pad_h=60,
                           row_span=100, hole=40)
    assert len(g.pads) == 6
    by = {p["designator"]: p for p in g.pads}
    # pin 1 top, pin 2 directly below (same column)
    assert by["1"]["y"] > 0 and by["2"]["y"] < 0
    assert by["1"]["x"] == by["2"]["x"]
    # odd pins on the top row, even on the bottom
    assert all(by[str(k)]["y"] > 0 for k in (1, 3, 5))
    assert all(by[str(k)]["y"] < 0 for k in (2, 4, 6))
    # columns advance left->right
    assert by["1"]["x"] < by["3"]["x"] < by["5"]["x"]
    # pin 1 is the rectangular orientation marker (through-hole)
    assert by["1"]["shape"] == "rectangular"
    assert all(p["hole_size"] == 40 for p in g.pads)


def test_header_requires_even_count():
    with pytest.raises(ValueError, match="even pin_count"):
        generate_footprint("header", 5, pitch=100, pad_w=60, pad_h=60,
                           row_span=100)


def test_header_requires_row_span():
    with pytest.raises(ValueError, match="row_span"):
        generate_footprint("header", 6, pitch=100, pad_w=60, pad_h=60)


def test_header_distinct_from_dual_numbering():
    # the whole point: header != dual for the same pin count
    hdr = generate_footprint("header", 6, pitch=100, pad_w=60, pad_h=60,
                             row_span=100)
    dual = generate_footprint("dual", 6, pitch=100, pad_w=60, pad_h=60,
                              row_span=100)
    hdr_pos = {p["designator"]: (p["x"], p["y"]) for p in hdr.pads}
    dual_pos = {p["designator"]: (p["x"], p["y"]) for p in dual.pads}
    assert hdr_pos != dual_pos     # different geometry/numbering


def test_rejects_nonpositive_pad():
    with pytest.raises(ValueError, match="pad_w must be > 0"):
        generate_footprint("chip", 2, pad_w=0, pad_h=30)
    with pytest.raises(ValueError, match="pad_h must be > 0"):
        generate_footprint("chip", 2, pad_w=24, pad_h=-30)


def test_rejects_nonpositive_pitch():
    with pytest.raises(ValueError, match="pitch must be > 0"):
        generate_footprint("dual", 8, pitch=-50, pad_w=30, pad_h=20,
                            row_span=150)


def test_rejects_negative_hole():
    with pytest.raises(ValueError, match="hole must be >= 0"):
        generate_footprint("sip", 3, pitch=100, pad_w=60, pad_h=60, hole=-40)


def test_rejects_hole_bigger_than_pad():
    # a drill >= the pad leaves no annular ring -- unmanufacturable
    with pytest.raises(ValueError, match="annular ring"):
        generate_footprint("sip", 3, pitch=100, pad_w=40, pad_h=40, hole=80)


def test_valid_through_hole_still_works():
    g = generate_footprint("sip", 3, pitch=100, pad_w=60, pad_h=60, hole=40)
    assert len(g.pads) == 3 and all(p["hole_size"] == 40 for p in g.pads)


def test_bga_depopulation_skip():
    full = generate_footprint("bga", 0, pitch=40, pad_w=20, rows=3, cols=3)
    dep = generate_footprint("bga", 0, pitch=40, pad_w=20, rows=3, cols=3,
                             skip=["A1", "c3"])   # case-insensitive
    assert len(full.pads) == 9
    assert len(dep.pads) == 7
    desigs = {p["designator"] for p in dep.pads}
    assert "A1" not in desigs and "C3" not in desigs and "B2" in desigs
