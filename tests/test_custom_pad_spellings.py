# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A custom pad given with common spellings is read, and one missing a
field is refused by name instead of raising."""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.check import check_package
from eda_agent.design.landpattern.spec import CustomPad, SpecError

_BASE = {"family": "custom", "body_x": 6.4, "body_y": 3.03, "height": 1.5,
         "source": {"document": "DS", "page": "1"}}


@pytest.mark.parametrize("pad", [
    {"designator": "A1", "x": 1, "y": 2, "w": 0.5, "h": 0.4},
    {"name": "A1", "x": 1, "y": 2, "x_size": 0.5, "y_size": 0.4},
    {"number": "A1", "x": 1, "y": 2, "width": 0.5, "height": 0.4},
])
def test_each_spelling_reads_the_same_pad(pad):
    p = CustomPad.from_any(pad)
    assert (p.number, p.x, p.y, p.width, p.height) == ("A1", 1, 2, 0.5, 0.4)


def test_a_missing_field_is_named():
    with pytest.raises(SpecError, match="width, height missing"):
        CustomPad.from_any({"number": "A1", "x": 1, "y": 2})


def test_the_package_check_reports_it_instead_of_raising():
    report = check_package(dict(_BASE, custom_pads=[{"number": "A1", "x": 1, "y": 2}]))
    assert report["errors"] >= 1
