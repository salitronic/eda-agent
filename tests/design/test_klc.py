# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Symbol conventions are checked, not learned.

Grid alignment, pin length and electrical typing are written rules, not
matters of taste. Encoding them beats fitting them: a preference model
over 312 layout comparisons scored 58% because its features could not
see a convention, and none of these needs a corpus at all.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from eda_agent.design.klc import GRID_MILS, check_symbol, check_symbols


@dataclass
class _Pin:
    designator: str
    x: int = 0
    y: int = 0
    length: int = 100
    electrical_type: str = "passive"
    name: str = ""
    orientation: int = 0


@dataclass
class _Sym:
    pins: list


def _rules(report):
    return {f.rule for f in report.findings}


def test_a_conventional_symbol_is_clean():
    sym = _Sym([
        _Pin("1", 0, 0, 100, "input"),
        _Pin("2", 0, 100, 100, "output"),
        _Pin("3", 0, 200, 100, "power"),
        _Pin("4", 0, 300, 100, "passive"),
    ])
    report = check_symbol(sym, "OK")
    assert report.ok
    assert report.findings == []
    assert report.pins_checked == 4


def test_an_off_grid_pin_is_an_error():
    """The rule that breaks connectivity silently.

    A wire drawn on grid does not meet an off-grid pin, so the sheet
    looks connected while the netlist disagrees.
    """
    sym = _Sym([_Pin("1", 50, 0), _Pin("2", 0, 130)])
    report = check_symbol(sym, "OFFGRID")
    assert not report.ok
    assert _rules(report) == {"S4.1"}
    assert sum(1 for f in report.findings if f.rule == "S4.1") == 2


@pytest.mark.parametrize("length,flagged", [
    (100, False), (150, False), (200, False), (300, False),
    (50, True),          # too short, crowds the body
    (350, True),         # too long, wastes sheet
    (
        120, True),      # not a multiple of 50
])
def test_pin_length_is_a_convention_at_both_ends(length, flagged):
    report = check_symbol(_Sym([_Pin("1", 0, 0, length)]), "LEN")
    assert ("S4.2" in _rules(report)) is flagged
    # A length note never invalidates a symbol.
    assert report.ok


def test_a_duplicate_pin_number_is_an_error():
    """Not a style note: the netlister cannot tell the two apart."""
    report = check_symbol(_Sym([_Pin("1", 0, 0), _Pin("1", 0, 100)]), "DUP")
    assert not report.ok
    assert "S4.3" in _rules(report)


def test_an_entirely_untyped_symbol_is_flagged():
    """What a generator emits when it does not know better."""
    pins = [_Pin(str(i), 0, i * 100, 100, "passive") for i in range(1, 6)]
    report = check_symbol(_Sym(pins), "UNTYPED")
    assert "S5" in _rules(report)
    assert report.ok, "unconventional, not invalid"


def test_a_small_passive_is_not_expected_to_be_typed():
    """A two-pin resistor is passive on both legs and that is correct."""
    report = check_symbol(_Sym([_Pin("1", 0, 0), _Pin("2", 0, 100)]), "R")
    assert "S5" not in _rules(report)


def test_a_pinless_symbol_reports_that_nothing_was_checked():
    """Clean over zero pins is not evidence of anything.

    Reporting it as simply 'ok' is the false-clean this project keeps
    finding: reading nothing and there being nothing are different.
    """
    report = check_symbol(_Sym([]), "EMPTY")
    assert report.pins_checked == 0
    assert "S0" in _rules(report)


def test_the_grid_is_the_documented_one():
    assert GRID_MILS == 100


def test_many_symbols_summarise():
    out = check_symbols({
        "good": _Sym([_Pin("1", 0, 0), _Pin("2", 0, 100)]),
        "bad": _Sym([_Pin("1", 25, 0)]),
    })
    assert out["symbols_checked"] == 2
    assert out["symbols_with_errors"] == 1
