# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Symbol pin numbers against footprint pad names."""
from __future__ import annotations

from eda_agent.design.pin_pad_parity import pin_pad_parity


def _pins(*numbers, mode=0):
    return [{"designator": n, "display_mode": mode} for n in numbers]


def _pads(*names, plated="true"):
    return [{"name": n, "plated": plated} for n in names]


def test_matching_numbers_pass_and_an_exposed_pad_in_parts_counts_once():
    found = pin_pad_parity(_pins("1", "2", "3"), _pads("1", "2", "3", "3", "3"))
    assert found["ok"] and found["pins"] == 3 and found["pads"] == 3


def test_ball_names_against_numbers_fail_both_ways():
    found = pin_pad_parity(_pins("1", "2"), _pads("A1", "A2"))
    assert not found["ok"]
    assert found["pins_without_pad"] == ["1", "2"]
    assert found["pads_without_pin"] == ["A1", "A2"]


def test_unnamed_pads_and_unplated_holes_carry_no_net():
    pads = _pads("1", "2") + _pads("") + _pads("MH", plated="false")
    found = pin_pad_parity(_pins("1", "2"), pads)
    assert found["ok"] and found["ignored_pads"] == 2


def test_alternate_display_modes_and_shared_numbers_count_once():
    pins = _pins("1", "2") + _pins("1", "2", mode=1) + _pins("2")
    assert pin_pad_parity(pins, _pads("1", "2"))["ok"]


def test_a_pin_without_a_number_fails_and_names_sort_naturally():
    found = pin_pad_parity(_pins("1", "", "10", "2"), _pads("1"))
    assert not found["ok"] and found["pins_without_number"] == 1
    assert found["pins_without_pad"] == ["2", "10"]


def test_case_does_not_separate_a_pin_from_its_pad():
    assert pin_pad_parity(_pins("ep"), _pads("EP"))["ok"]
