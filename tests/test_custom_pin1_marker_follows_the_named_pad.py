# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A custom footprint's pin-1 marker sits by pin 1, not by the first pad listed.

The BGT60TR13C ball map was listed from row 9, so the marker and the
assembly chamfer went to A9 while A1 is in the opposite corner.
"""
from __future__ import annotations

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.spec import PackageSpec


def _pads(names):
    # A9 top left first, A1 bottom left last.
    at = {"A9": (-1.0, 1.0), "M9": (1.0, 1.0), "M1": (1.0, -1.0), "A1": (-1.0, -1.0)}
    return [{"number": n, "x": at[n][0], "y": at[n][1], "width": 0.25,
             "height": 0.25, "shape": "circle"} for n in names]


def _model(**extra):
    return build_footprint(PackageSpec(
        family="custom", body_x=3.0, body_y=3.0,
        custom_pads=_pads(["A9", "M9", "M1", "A1"]),
        source={"document": "test", "page": "1"}, **extra))


def test_a1_is_pin_1_when_there_is_no_pad_1():
    assert tuple(_model().pin1) == (-1.0, -1.0)


def test_the_pad_the_spec_names_is_pin_1():
    assert tuple(_model(pin1="M9").pin1) == (1.0, 1.0)
