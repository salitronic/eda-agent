# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One land that serves two pins can be built as two lands that meet.

A ten-pin DFN's land pattern draws one centre ground land, 0.40 x 1.55 mm,
under pins 3 and 8. Given as its two halves, pad 3 the lower 0.40 x 0.775
and pad 8 the upper, meeting at y = 0, the copper is the datasheet's and
both numbers exist for the pins-against-pads check. The build refused it:
"lands 3 and 8 overlap; the package dimensions leave no room between
them". Touching counted as overlapping, and the guide covered one pin
with several lands, not one land with several pins. The halves had to be
pulled 0.2 mm apart, copper the datasheet does not draw.

Two hand-placed lands of different numbers that meet along an edge are
now one copper land, built with a warning that both pins must be on one
net. Reaching into each other is still refused, as is touching between
lands the builder sized itself.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.check import read_package
from eda_agent.design.landpattern.spec import SpecError


def _pad(number, x, y, w, h, **more):
    return {"number": number, "x": x, "y": y, "width": w, "height": h,
            "shape": "rect", **more}


def _package(*centre):
    rows = [_pad(n, x, s * 0.4375, 0.2, 0.675) for n, x, s in (
        ("1", -1.0, -1), ("2", -0.5, -1), ("4", 0.5, -1), ("5", 1.0, -1),
        ("6", 1.0, 1), ("7", 0.5, 1), ("9", -0.5, 1), ("10", -1.0, 1))]
    return {"family": "custom", "name": "DFN10", "body_x": 2.5, "body_y": 1.0,
            "height": 0.58, "custom_pads": [*centre, *rows]}


def _build(*centre):
    return build_footprint(read_package(_package(*centre)))


def _touching(model):
    return [w for w in model.warnings if "touch" in w]


def test_two_halves_that_meet_are_built_as_given():
    model = _build(_pad("3", 0, -0.3875, 0.4, 0.775), _pad("8", 0, 0.3875, 0.4, 0.775))
    lower, upper = model.pads[0], model.pads[1]
    assert (lower.number, lower.y, lower.height) == ("3", -0.3875, 0.775)
    assert (upper.number, upper.y, upper.height) == ("8", 0.3875, 0.775)
    assert lower.bbox()[3] == pytest.approx(upper.bbox()[1])       # they meet
    assert lower.bbox()[1] == pytest.approx(-0.775) and upper.bbox()[3] == pytest.approx(0.775)


def test_the_build_says_they_are_one_land_on_one_net():
    said = _touching(_build(_pad("3", 0, -0.3875, 0.4, 0.775),
                            _pad("8", 0, 0.3875, 0.4, 0.775)))
    assert len(said) == 1
    assert "lands 3 and 8" in said[0] and "one net" in said[0]


def test_side_by_side_halves_meet_too():
    said = _touching(_build(_pad("3", -0.1, 0, 0.2, 0.3), _pad("8", 0.1, 0, 0.2, 0.3)))
    assert len(said) == 1


def test_a_turned_land_meets_by_its_turned_box():
    # 0.775 x 0.4 turned a quarter is 0.4 x 0.775.
    said = _touching(_build(_pad("3", 0, -0.3875, 0.775, 0.4, rotation=90),
                            _pad("8", 0, 0.3875, 0.4, 0.775)))
    assert len(said) == 1


@pytest.mark.parametrize("upper_y", [0.3775, 0.2, 0.0])
def test_lands_that_reach_into_each_other_are_still_refused(upper_y):
    with pytest.raises(SpecError, match="lands 3 and 8 overlap") as exc:
        _build(_pad("3", 0, -0.3875, 0.4, 0.775), _pad("8", 0, upper_y, 0.4, 0.775))
    assert "meet along an edge" in str(exc.value)      # how it is given instead


def test_lands_that_meet_at_a_corner_only_are_refused():
    with pytest.raises(SpecError, match="overlap"):
        _build(_pad("3", -0.1, -0.15, 0.2, 0.3), _pad("8", 0.1, 0.15, 0.2, 0.3))


def test_lands_apart_say_nothing():
    model = _build(_pad("3", 0, -0.4375, 0.4, 0.675), _pad("8", 0, 0.4375, 0.4, 0.675))
    assert not _touching(model)


def test_one_number_on_two_lands_is_as_before():
    model = _build(_pad("3", 0, -0.3875, 0.4, 0.775), _pad("3", 0, 0.3875, 0.4, 0.775))
    assert not _touching(model)


def test_lands_the_builder_sized_are_not_let_through():
    """The exception is for lands the caller placed. A pattern worked out
    from package dimensions whose lands meet still has no room."""
    from eda_agent.design.landpattern import builder
    from eda_agent.design.landpattern.model import Pad

    def pad(number, y, group):
        return Pad(number=number, x=0.0, y=y, width=0.4, height=0.775, group=group)

    assert builder._one_land(pad("3", -0.3875, "custom"), pad("8", 0.3875, "custom"))
    assert not builder._one_land(pad("3", -0.3875, ""), pad("8", 0.3875, ""))
    assert not builder._one_land(pad("3", -0.3875, "custom"), pad("8", 0.3875, "ep"))
