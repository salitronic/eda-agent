# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""drawing_dims measures land sizes, outer spans and gaps, not only pitches.

The ADXL372 land pattern (Fig. 111) prints 0.80 for the top and bottom
lands and 0.925 for the side lands; the side lands were built 0.80 long
and every pad-to-pad number still matched.
"""
from __future__ import annotations

from types import SimpleNamespace as P

import pytest

from eda_agent.design.landpattern.drawing_dims import check_dims, read_dims
from eda_agent.design.landpattern.spec import SpecError

# Side land 9 as built (0.80 long, outer edge at 1.75) and as drawn (0.925).
_BUILT = [P(number="9", x=1.35, y=-1.0, width=0.8, height=0.3, rotation=0),
          P(number="5", x=-1.35, y=-1.0, width=0.8, height=0.3, rotation=0),
          P(number="6", x=-0.5, y=-1.275, width=0.3, height=0.8, rotation=0)]
_DRAWN = [P(number="9", x=1.2875, y=-1.0, width=0.925, height=0.3, rotation=0),
          P(number="5", x=-1.2875, y=-1.0, width=0.925, height=0.3, rotation=0)]


def test_a_land_length_is_measured_and_a_short_land_refused():
    dims = read_dims([{"pad": "9", "axis": "x", "mm": 0.925}])
    bad = check_dims(dims, _BUILT)[0]
    assert not bad["ok"] and bad["built_mm"] == 0.8
    assert "pad 9 is 0.800 mm along x" in bad["why"] and "0.925" in bad["why"]
    assert check_dims(dims, _DRAWN)[0]["ok"]


def test_outer_span_and_gap():
    span = read_dims([{"from": "5", "to": "9", "axis": "x", "mm": 3.5,
                       "between": "outer"}])
    assert check_dims(span, _BUILT)[0]["ok"] and check_dims(span, _DRAWN)[0]["ok"]
    gap = read_dims([{"from": "5", "to": "9", "axis": "x", "mm": 1.65,
                      "between": "gap"}])
    assert check_dims(gap, _DRAWN)[0]["ok"]
    assert not check_dims(gap, _BUILT)[0]["ok"]


def test_a_rotated_land_measures_its_turned_extent():
    turned = [P(number="1", x=0, y=0, width=0.8, height=0.3, rotation=90)]
    assert check_dims(read_dims([{"pad": "1", "axis": "y", "mm": 0.8}]), turned)[0]["ok"]


def test_centres_stay_the_default():
    row = check_dims(read_dims([{"from": "5", "to": "9", "axis": "x",
                                 "mm": 2.7}]), _BUILT)[0]
    assert row["ok"] and row["between"] == "centres"


@pytest.mark.parametrize("bad", [
    {"pad": "9", "mm": 0.925},                       # a size needs its axis
    {"from": "5", "to": "9", "mm": 1, "between": "edge"},
    {"from": "5", "mm": 1},
])
def test_unclear_dimensions_are_refused(bad):
    with pytest.raises(SpecError):
        read_dims([bad])


def test_unlike_edges_and_the_centre_lines():
    """VL53L5CX Fig. 28: 1.342 runs from the outer edge of the last row
    land to the edge of the thermal pad; 1.300 from the centre line to
    the outer edge of a row."""
    a1 = P(number="A1", x=2.25, y=-1.05, width=0.5, height=0.5, rotation=0)
    b4 = P(number="B4", x=0, y=0, width=2.316, height=1.2, rotation=0)
    dims = read_dims([
        {"from": "A1", "to": "B4", "axis": "x", "mm": 1.342,
         "from_edge": "far", "to_edge": "near"},
        {"from": "ORIGIN", "to": "A1", "axis": "y", "mm": 1.3, "to_edge": "far"},
        {"from": "ORIGIN", "to": "B4", "axis": "x", "mm": 1.158, "to_edge": "far"}])
    assert [r["ok"] for r in check_dims(dims, [a1, b4])] == [True, True, True]


def test_every_printed_number_must_be_placed():
    from eda_agent.design.landpattern.drawing_dims import (
        drawing_refusal, read_numbers, unplaced_numbers)
    dims = read_dims([{"pad": "1", "axis": "x", "mm": 0.8},
                      {"from": "1", "to": "2", "axis": "y", "mm": 0.5}])
    nums = read_numbers([3.5, 0.8, 0.5, 0.925])
    assert unplaced_numbers(nums, dims) == [3.5, 0.925]
    assert "0.925" in drawing_refusal(True, dims, nums)
    assert drawing_refusal(True, dims, ()) is not None
    assert drawing_refusal(False, (), ()) is None
    with pytest.raises(SpecError):
        read_numbers(["16x"])


def test_a_pin_with_several_lands_names_each_land():
    """IRF6648: four drain lands numbered D; "D" alone measured the first."""
    lands = [P(number="D", x=-1.0, y=0, width=0.5, height=2, rotation=0),
             P(number="D", x=1.0, y=0, width=0.5, height=2, rotation=0)]
    ambiguous = check_dims(read_dims([{"from": "D", "to": "D", "axis": "x",
                                       "mm": 2.0}]), lands)[0]
    assert not ambiguous["ok"] and "D#1" in ambiguous["why"]
    named = check_dims(read_dims([{"from": "D#1", "to": "D#2", "axis": "x",
                                   "mm": 2.0}]), lands)[0]
    assert named["ok"]
