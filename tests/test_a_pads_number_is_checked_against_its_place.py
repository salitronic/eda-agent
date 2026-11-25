# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A pad's number is checked against where the pin-layout figure puts it.

Nothing tied a pad's NUMBER to its POSITION: the pin table gives number and
name, the land-pattern drawing sizes and gaps. A footprint with one side
numbered the other way round passed every check.
"""
from __future__ import annotations

import asyncio

from eda_agent.design.pinout_fit import check_pinout


def _part():
    """Sixteen pins round a square, 1 at the top left going anticlockwise
    as seen from above, and a centre pad 17 drawn as four lands."""
    pads = []
    for i in range(4):
        pads.append({"designator": str(1 + i), "x": -3.0, "y": 1.5 - i})            # left, downwards
        pads.append({"designator": str(5 + i), "x": -1.5 + i, "y": -3.0})           # bottom, rightwards
        pads.append({"designator": str(9 + i), "x": 3.0, "y": -1.5 + i})            # right, upwards
        pads.append({"designator": str(13 + i), "x": 1.5 - i, "y": 3.0})            # top, leftwards
    for x in (-0.5, 0.5):
        for y in (-0.5, 0.5):
            pads.append({"designator": "17", "x": x, "y": y})
    return pads


def _figure(pads, bottom=False, extra=()):
    """The figure's labels: each number printed a little outside its pin,
    the picture 0.1 of its width per mm and y running down."""
    seen, labels = set(), []
    for pad in pads:
        name = pad["designator"]
        if name in seen:
            continue
        seen.add(name)
        group = [p for p in pads if p["designator"] == name]
        x = sum(p["x"] for p in group) / len(group)
        y = sum(p["y"] for p in group) / len(group)
        x, y = x * 1.08, y * 1.08                       # printed beside the pin, not on it
        u = 0.5 + (-x if bottom else x) * 0.1
        labels.append({"value": name, "at": [round(u, 4), round(0.5 - y * 0.07, 4)]})
    return labels + list(extra)


def test_a_footprint_numbered_as_the_figure_passes():
    pads = _part()
    out = check_pinout(pads, _figure(pads), "top")
    assert out["ok"], out
    assert (out["pads"], out["checked"]) == (17, 17)
    assert out["fit"]["labels_left_out"] == 0


def test_one_side_numbered_the_other_way_round_is_named_pad_by_pad():
    pads = _part()
    swap = {"9": "12", "10": "11", "11": "10", "12": "9"}
    wrong = [dict(p, designator=swap.get(p["designator"], p["designator"])) for p in pads]
    out = check_pinout(wrong, _figure(pads), "top")
    assert not out["ok"]
    assert {m["pad"]: m["figure_has"] for m in out["mismatches"]} == swap
    assert "pad 9 where the figure has 12" in out["reason"]


def test_a_bottom_view_read_as_a_top_view_is_refused():
    pads = _part()
    figure = _figure(pads, bottom=True)
    assert check_pinout(pads, figure, "bottom")["ok"]
    out = check_pinout(pads, figure, "top")
    assert not out["ok"] and "left to right" in out["reason"]


def test_a_mirrored_footprint_is_refused_though_every_label_finds_its_pad():
    pads = _part()
    mirrored = [dict(p, x=-p["x"]) for p in pads]
    out = check_pinout(mirrored, _figure(pads), "top")
    assert not out["ok"] and not out["mismatches"]
    assert "mirrored" in out["reason"]


def test_stray_numbers_on_the_page_do_not_disturb_it():
    pads = _part()
    stray = [{"value": "3", "at": [0.05, 0.02]}, {"value": "17", "at": [0.95, 0.97]},
             {"value": "Figure", "at": [0.5, 0.9]}]
    out = check_pinout(pads, _figure(pads, extra=stray), "top")
    assert out["ok"], out


def test_a_pad_the_figure_does_not_number_is_not_passed():
    pads = _part()
    figure = [lb for lb in _figure(pads) if lb["value"] != "7"]
    out = check_pinout(pads, figure, "top")
    assert not out["ok"] and out["unlabelled"] == ["7"]
    assert "NOT checked" in out["reason"]


def test_a_figure_without_text_or_with_too_little_is_said_so():
    pads = _part()
    assert "none of the footprint's pad numbers" in check_pinout(pads, [], "top")["reason"]
    assert "needed to place the figure" in check_pinout(pads, _figure(pads)[:3], "top")["reason"]
    one_row = [lb for lb in _figure(pads) if lb["value"] in ("5", "6", "7", "8")]
    assert "one line" in check_pinout(pads, one_row, "top")["reason"]
    assert "view is" in check_pinout(pads, _figure(pads), "side")["reason"]


def test_the_tool_is_registered_as_one_that_needs_no_editor():
    from eda_agent.tools import footprint as fp_tools
    from eda_agent.tools.metadata import _OFFLINE_BY_NAME
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    fp_tools.register_footprint_tools(registry)
    names = {t.name for t in asyncio.run(registry.list_tools())}
    assert "footprint_compare_pinout" in names and "footprint_compare_pinout" in _OFFLINE_BY_NAME
