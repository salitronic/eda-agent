# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""describe_window reads near the root first, and says when it stopped.

The walk was depth-first with a 400-element budget. On an Engineering
Change Order with 47 rows the grid came first in tree order and used the
whole budget before the walker reached the buttons, and the reply was
``ok: True, count: 400`` with nothing to say it was cut short. The caller
saw one button of five. text_of inherits the walk, so the ``offered``
list that invoke() returns to explain a failed lookup was full of grid
rows instead of the buttons.
"""
from __future__ import annotations

import pytest

from eda_agent.ui import uia


class _Node:
    def __init__(self, name, kids=(), kind="pane"):
        self.name = name
        self.kind = kind
        self.kids = list(kids)


BUTTONS = ["OK", "Cancel", "Help", "Validate Changes", "Execute Changes"]


def _eco_dialog(grid_first: bool, rows: int = 600) -> _Node:
    grid = _Node("Changes", [_Node("Rows", [
        _Node(f"row {i}", kind="data item") for i in range(rows)])])
    bar = _Node("Buttons", [_Node(b, kind="button") for b in BUTTONS])
    return _Node("Engineering Change Order", [grid, bar] if grid_first
                 else [bar, grid])


@pytest.fixture()
def fake_uia(monkeypatch):
    def install(root):
        monkeypatch.setattr(uia, "_element", lambda hwnd: root)
        monkeypatch.setattr(uia, "_children", lambda node: list(node.kids))
        monkeypatch.setattr(uia, "_describe", lambda node: {
            "name": node.name, "type": node.kind, "automation_id": "",
            "class": "", "enabled": True, "offscreen": False, "rect": None})
    return install


@pytest.mark.parametrize("grid_first", [True, False],
                         ids=["grid-first", "buttons-first"])
def test_the_buttons_survive_a_grid_that_fills_the_budget(fake_uia, grid_first):
    """Both orders, because a depth-first walk starves the buttons when
    the grid comes first and a last-in-first-out one starves them when
    it comes second. Only reading level by level survives both."""
    fake_uia(_eco_dialog(grid_first))
    out = uia.describe_window(1)
    names = {e["name"] for e in out["elements"]}
    missing = [b for b in BUTTONS if b not in names]
    assert not missing, f"lost to the grid: {missing}"


def test_a_cut_short_read_says_so(fake_uia):
    fake_uia(_eco_dialog(grid_first=True))
    out = uia.describe_window(1)
    assert out["ok"] is True
    assert out["count"] == 400
    assert out["truncated"] is True
    assert "note" in out


def test_a_tree_that_fits_is_not_truncated(fake_uia):
    fake_uia(_eco_dialog(grid_first=True, rows=10))
    out = uia.describe_window(1)
    assert out["truncated"] is False
    assert "note" not in out


def test_exactly_the_limit_is_not_truncation(fake_uia):
    """Nothing was left out, so claiming a cut would be its own false
    report, in the other direction."""
    fake_uia(_Node("root", [_Node("a"), _Node("b"), _Node("c")]))
    out = uia.describe_window(1, limit=3)
    assert out["count"] == 3
    assert out["truncated"] is False


def test_one_over_the_limit_is_truncation(fake_uia):
    fake_uia(_Node("root", [_Node("a"), _Node("b"), _Node("c"), _Node("d")]))
    out = uia.describe_window(1, limit=3)
    assert out["count"] == 3
    assert out["truncated"] is True


def test_depth_still_bounds_the_walk_and_is_not_truncation(fake_uia):
    fake_uia(_Node("root", [_Node("a", [_Node("b", [_Node("c", [
        _Node("d")])])])]))
    out = uia.describe_window(1, depth=1)
    assert [e["name"] for e in out["elements"]] == ["a", "b"]
    assert [e["depth"] for e in out["elements"]] == [0, 1]
    assert out["truncated"] is False


def test_unnamed_containers_are_walked_through(fake_uia):
    """A nameless pane uses no budget but its children still count."""
    fake_uia(_Node("root", [_Node("", [_Node("OK", kind="button")],
                                  kind="")]))
    out = uia.describe_window(1)
    assert [e["name"] for e in out["elements"]] == ["OK"]


def test_the_offered_list_names_buttons_not_grid_rows(fake_uia):
    """text_of feeds the explanation a failed press returns."""
    fake_uia(_eco_dialog(grid_first=True))
    offered = uia.text_of(1)[:60]
    assert "OK" in offered and "Execute Changes" in offered
