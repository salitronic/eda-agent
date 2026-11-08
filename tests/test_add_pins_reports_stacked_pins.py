# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pins given at one point are reported: Altium accepts them, but a stack
hides how many pins share the net and prints their numbers over each other."""
from __future__ import annotations

from eda_agent.tools.library import _stacked_pins


def test_pins_at_one_point_are_grouped():
    pins = [{"designator": "A1", "x": 0, "y": 0}, {"designator": "A2", "x": 0, "y": 0},
            {"designator": "B1", "x": 0, "y": -100}]
    assert _stacked_pins(pins) == [["A1", "A2"]]


def test_the_same_point_on_another_part_or_rotation_is_not_a_stack():
    pins = [{"designator": "1", "x": 0, "y": 0, "owner_part_id": 1},
            {"designator": "2", "x": 0, "y": 0, "owner_part_id": 2},
            {"designator": "3", "x": 0, "y": 0, "rotation": 180, "owner_part_id": 1}]
    assert _stacked_pins(pins) == []
