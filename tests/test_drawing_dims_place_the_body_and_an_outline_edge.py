# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""drawing_dims can place the body, an outline's edge and a number not built.

A radio module's recommended land pattern prints 13.2 and 16.6 for the
body, 0.6 and 0.6 for the cut corner of the land that marks pin 1 (given as
an ``outline``), and 11.2 from the body edge to the antenna keep-out line.
drawing_dims named pads, pad edges and ORIGIN only, so none of the five
could be placed and "Footprint vs drawing" had to be switched off for the
part, although every land number on the drawing was placed and passed.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.check import read_package
from eda_agent.design.landpattern.drawing_dims import (
    body_box, check_dims, drawing_refusal, outlines_of, read_dims,
    read_not_built, read_numbers, unplaced_numbers)
from eda_agent.design.landpattern.spec import SpecError

# The corner land with its 0.6 x 0.6 cut, two plain lands numbered alike,
# and one edge land. Edge 4 of the outline is the cut.
_CUT = [[-0.6, -0.6], [0.6, -0.6], [0.6, 0.6], [0.0, 0.6], [-0.6, 0.0]]
_PACKAGE = {
    "family": "custom", "name": "MODULE", "body_x": 13.2, "body_y": 16.6,
    "height": 2.4,
    "custom_pads": [
        {"number": "49", "x": 0, "y": 0, "width": 1.2, "height": 1.2,
         "shape": "rect", "outline": _CUT},
        {"number": "49", "x": 1.7, "y": 0, "width": 1.2, "height": 1.2,
         "shape": "rect"},
        {"number": "1", "x": -6.2, "y": 0, "width": 0.8, "height": 0.4,
         "shape": "rect"},
    ],
}


def _rows(dims, package=None):
    spec = read_package(package or _PACKAGE)
    model = build_footprint(spec)
    return check_dims(read_dims(dims), model.pads, body_box(spec, model),
                      outlines_of(spec))


def test_the_body_is_measured_as_the_package_gives_it():
    rows = _rows([{"pad": "BODY", "axis": "x", "mm": 13.2},
                  {"pad": "BODY", "axis": "y", "mm": 16.6}])
    assert [r["ok"] for r in rows] == [True, True]
    wrong = _rows([{"pad": "BODY", "axis": "x", "mm": 16.6}])[0]
    assert not wrong["ok"] and wrong["built_mm"] == 13.2


def test_a_land_is_measured_from_the_body_edge():
    # Body edge at x = -6.6, the land's outer edge at -6.6: flush.
    row = _rows([{"from": "BODY", "to": "1", "axis": "x", "mm": 0.4,
                  "from_edge": "centre", "to_edge": "centre"}])[0]
    assert row["built_mm"] == 6.2 and not row["ok"]
    flush = _rows([{"from": "1", "to": "BODY", "axis": "x", "mm": 13.2,
                    "from_edge": "far", "to_edge": "far"}])[0]
    assert flush["ok"], flush


def test_body_without_a_body_in_the_package_is_refused_with_the_reason():
    bare = {k: v for k, v in _PACKAGE.items() if k not in ("body_x", "body_y")}
    row = _rows([{"pad": "BODY", "axis": "x", "mm": 13.2}], bare)[0]
    assert not row["ok"] and "body_x and body_y" in row["why"]


def test_the_two_legs_of_a_cut_corner_are_measured_on_the_outline():
    rows = _rows([{"pad": "49#1", "edge": 4, "axis": "x", "mm": 0.6},
                  {"pad": "49#1", "edge": 4, "axis": "y", "mm": 0.6},
                  {"pad": "49#1", "edge": 5, "axis": "y", "mm": 0.6},
                  {"pad": "49#1", "edge": 4, "mm": 0.8485}])
    assert [r["ok"] for r in rows] == [True, True, True, True], rows
    assert rows[0]["edge"] == 4 and rows[0]["between"] == "outline"


def test_a_wrong_edge_says_where_that_edge_runs():
    row = _rows([{"pad": "49#1", "edge": 1, "axis": "x", "mm": 0.6}])[0]
    assert not row["ok"] and row["built_mm"] == 1.2
    assert "(-0.6, -0.6) to (0.6, -0.6)" in row["why"]
    assert "1.200 mm along x" in row["why"]


def test_an_edge_of_a_land_without_an_outline_is_refused():
    row = _rows([{"pad": "49#2", "edge": 1, "axis": "x", "mm": 1.2}])[0]
    assert not row["ok"] and "without an outline" in row["why"]
    past = _rows([{"pad": "49#1", "edge": 6, "axis": "x", "mm": 0.6}])[0]
    assert not past["ok"] and "has 5 edges" in past["why"]


def test_a_repeated_number_still_has_to_say_which_land():
    row = _rows([{"pad": "49", "edge": 5, "axis": "x", "mm": 0.6}])[0]
    assert not row["ok"] and "49#1" in row["why"]


@pytest.mark.parametrize("edge", [0, -1, "two", True])
def test_an_edge_is_counted_from_one(edge):
    with pytest.raises(SpecError, match="from 1"):
        read_dims([{"pad": "49#1", "edge": edge, "axis": "x", "mm": 0.6}])


def test_a_number_not_built_is_named_and_does_not_count_as_unplaced():
    raw = [13.2, 16.6, 0.6,
           {"mm": 11.2, "not_built": "antenna keep-out line to the body edge"}]
    dims = read_dims([{"pad": "BODY", "axis": "x", "mm": 13.2},
                      {"pad": "BODY", "axis": "y", "mm": 16.6},
                      {"pad": "49#1", "edge": 5, "axis": "x", "mm": 0.6}])
    assert read_numbers(raw) == (13.2, 16.6, 0.6)
    assert read_not_built(raw) == [
        {"mm": 11.2, "what": "antenna keep-out line to the body edge"}]
    assert unplaced_numbers(read_numbers(raw), dims) == []
    assert drawing_refusal(True, dims, read_numbers(raw)) is None


def test_a_plain_number_nobody_placed_is_still_refused():
    dims = read_dims([{"pad": "BODY", "axis": "x", "mm": 13.2}])
    why = drawing_refusal(True, dims, read_numbers([13.2, 0.925]))
    assert why and "0.925" in why and "not_built" in why


@pytest.mark.parametrize("bad", [
    {"mm": 11.2},                                 # no reason
    {"mm": 11.2, "not_built": ""},
    {"mm": 11.2, "not_built": "n/a"},             # says nothing
    {"not_built": "antenna keep-out line"},       # no number
])
def test_a_number_not_built_needs_its_number_and_what_it_measures(bad):
    with pytest.raises(SpecError, match="not_built"):
        read_numbers([bad])
    with pytest.raises(SpecError, match="not_built"):
        read_not_built([bad])


def test_the_check_tool_measures_them_and_reports_what_is_not_built():
    import asyncio

    from eda_agent.tools import footprint

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    footprint.register_footprint_tools(_Mcp())
    out = asyncio.run(tools["footprint_check_package"]({
        **_PACKAGE,
        "drawing_numbers": [13.2, 16.6, 0.6, {
            "mm": 11.2, "not_built": "antenna keep-out line to the body edge"}],
        "drawing_dims": [{"pad": "BODY", "axis": "x", "mm": 13.2},
                         {"pad": "BODY", "axis": "y", "mm": 16.6},
                         {"pad": "49#1", "edge": 4, "axis": "x", "mm": 0.6}]}))
    assert all(r["ok"] for r in out["drawing_dims"]), out["drawing_dims"]
    assert "unplaced_numbers" not in out
    assert out["numbers_not_built"] == [
        {"mm": 11.2, "what": "antenna keep-out line to the body edge"}]
