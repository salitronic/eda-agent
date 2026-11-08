# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Ball names follow the datasheet's grid, not one fixed convention.

The LTM8060 letters its 15 columns A to R along the long side and numbers
its 11 rows upwards, with A1 bottom left in the top view. The generator
only knew letters down the rows from A1 top left, so the pads had to be
redrawn by hand to the ball map.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.spec import PackageSpec, SpecError


def _bga(**options):
    return PackageSpec(family="bga", rows=11, cols=15, pitch=1.0,
                       ball_diameter=0.6, body_x=16.0, body_y=11.9,
                       options=options,
                       source={"document": "test", "page": "1"})


def _at(model, name):
    pad = next(p for p in model.pads if p.number == name)
    return round(pad.x, 3), round(pad.y, 3)


def test_the_default_letters_the_rows_from_a1_top_left():
    m = build_footprint(_bga())
    assert _at(m, "A1") == (-7.0, 5.0)
    assert _at(m, "A15") == (7.0, 5.0)
    assert _at(m, "B1") == (-7.0, 4.0)


def test_letters_along_x_with_a1_bottom_left_match_the_ltm8060():
    m = build_footprint(_bga(letters="x", a1="bottom_left"))
    assert _at(m, "A1") == (-7.0, -5.0)
    assert _at(m, "A11") == (-7.0, 5.0)
    assert _at(m, "R1") == (7.0, -5.0)  # 15th letter: I, O, Q skipped
    assert len(m.pads) == 165


def test_an_unknown_corner_is_refused():
    with pytest.raises(SpecError, match="a1"):
        build_footprint(_bga(a1="middle"))
