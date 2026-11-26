# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""drawing_dims can place a printed hole diameter.

A through-hole RJ45's recommended layout prints where the holes are and
four drills (0.89 x10, 1.02 x4, 1.63 x2, 3.25 x2) and no copper diameter.
drawing_dims measured a land's size and nothing of its hole, so three of
the four drills could not be placed ("no drawing_dims entry measures
1.02, 1.63, 0.89") and "Footprint vs drawing" was switched off for the
part. The fourth passed only because an unplated hole's pad is its hole.
A drill is built, so it is no ``not_built`` number either.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.check import read_package
from eda_agent.design.landpattern.drawing_dims import (
    body_box, check_dims, drawing_refusal, outlines_of, read_dims,
    read_numbers, unplaced_numbers)
from eda_agent.design.landpattern.spec import SpecError

_PACKAGE = {
    "family": "custom", "name": "JACK", "height": 13.4,
    "custom_pads": [
        {"number": "1", "x": -1.27, "y": 0, "width": 1.5, "height": 1.5,
         "shape": "circle", "hole": 0.89},
        {"number": "2", "x": 1.27, "y": 0, "width": 1.5, "height": 1.5,
         "shape": "circle", "hole": 0.89},
        {"number": "SH", "x": -7.9, "y": 3.0, "width": 2.6, "height": 2.6,
         "shape": "circle", "hole": 1.63},
        {"number": "SL", "x": 7.9, "y": 3.0, "width": 1.8, "height": 3.4,
         "shape": "roundrect", "hole": 1.0, "hole_length": 2.4},
        {"number": "", "x": 0, "y": 6.0, "width": 3.25, "height": 3.25,
         "shape": "circle", "hole": 3.25, "plated": False, "role": "mounting"},
        {"number": "T", "x": 0, "y": -4.0, "width": 1.0, "height": 0.6,
         "shape": "rect"},
    ],
}


def _rows(dims):
    spec = read_package(_PACKAGE)
    model = build_footprint(spec)
    return check_dims(read_dims(dims), model.pads, body_box(spec, model),
                      outlines_of(spec))


def test_a_drill_is_measured_on_the_pad():
    rows = _rows([{"pad": "1", "mm": 0.89, "hole": True},
                  {"pad": "SH", "mm": 1.63, "hole": True}])
    assert [r["ok"] for r in rows] == [True, True], rows
    assert rows[0]["built_mm"] == 0.89 and rows[0]["hole"] == "diameter"
    assert rows[0]["between"] == "hole"


def test_a_drill_that_disagrees_is_refused_with_both_numbers():
    row = _rows([{"pad": "2", "mm": 1.02, "hole": True}])[0]
    assert not row["ok"] and row["built_mm"] == 0.89
    assert "hole of pad 2 is 0.890 mm" in row["why"] and "prints 1.02 mm" in row["why"]


def test_a_slot_has_a_width_and_a_length():
    rows = _rows([{"pad": "SL", "mm": 1.0, "hole": True},
                  {"pad": "SL", "mm": 2.4, "hole": "length"},
                  {"pad": "SL", "mm": 2.0, "hole": "length"}])
    assert [r["ok"] for r in rows] == [True, True, False]
    assert "2.400 mm long" in rows[2]["why"]


def test_a_round_hole_has_no_length():
    row = _rows([{"pad": "1", "mm": 0.89, "hole": "length"}])[0]
    assert not row["ok"] and row["built_mm"] is None
    assert "round" in row["why"] and "hole: true" in row["why"]


def test_a_land_without_a_hole_is_refused_with_the_reason():
    row = _rows([{"pad": "T", "mm": 0.6, "hole": True}])[0]
    assert not row["ok"] and "pad T has no hole" in row["why"]
    assert _rows([{"pad": "ORIGIN", "mm": 0.6, "hole": True}])[0]["ok"] is False


def test_a_pad_that_is_not_there_is_named():
    row = _rows([{"pad": "9", "mm": 0.89, "hole": True}])[0]
    assert not row["ok"] and "no pad named 9" in row["why"]


def test_the_unplated_hole_is_measured_as_a_hole_too():
    # Pads without a number are named #1, #2 in the order given.
    spec = read_package(_PACKAGE)
    model = build_footprint(spec)
    names = [str(p.number) for p in model.pads]
    peg = "#1" if "" in names else names[4]
    row = check_dims(read_dims([{"pad": peg, "mm": 3.25, "hole": True}]),
                     model.pads)[0]
    assert row["ok"], row


def test_the_land_size_form_is_unchanged_beside_it():
    rows = _rows([{"pad": "1", "mm": 1.5, "axis": "x"},
                  {"pad": "1", "mm": 0.89, "hole": False, "axis": "x"}])
    assert rows[0]["ok"] and rows[0]["between"] == "size"
    assert rows[1]["between"] == "size" and not rows[1]["ok"]


@pytest.mark.parametrize("bad", ["width", "x", 2, "true"])
def test_another_word_for_hole_is_refused(bad):
    with pytest.raises(SpecError, match="hole: true"):
        read_dims([{"pad": "1", "mm": 0.89, "hole": bad}])


def test_every_printed_drill_can_now_be_placed():
    numbers = read_numbers([0.89, 1.63, 2.54])
    dims = read_dims([{"pad": "1", "mm": 0.89, "hole": True},
                      {"from": "1", "to": "2", "mm": 2.54, "axis": "x"}])
    assert unplaced_numbers(numbers, dims) == [1.63]
    refusal = drawing_refusal(True, dims, numbers)
    assert "1.63" in refusal and "{pad, hole: true}" in refusal
    assert "{pad, mm, hole: true}" in drawing_refusal(True, (), ())
