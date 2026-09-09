# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A route plan has to say which board state it was built on.

``fetch_geometry`` defaults to False, which is the right default (a
fetch is a round trip on a big board) and a silent trap: the natural
second call re-uses the caller's snapshot, and after the first net's
copper is placed that snapshot no longer describes the board. The plan
then routes straight through tracks that exist.

The tool cannot date a dict it was handed, so it reports where the
geometry came from and what was in it, and the caller can compare.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import route as route_mod
from eda_agent.tools.registry import ToolRegistry


def _tools():
    registry = ToolRegistry()
    route_mod.register_route_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


def _geom(bbox=(0, 0, 1000, 600), pads=None, counts=None):
    out = {
        "bbox": {"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]},
        "pads": pads if pads is not None else [
            {"x": 100, "y": 300, "x_size": 40, "y_size": 40,
             "shape": "Rectangular", "layer": "TopLayer", "net": "N",
             "rotation": 0},
            {"x": 900, "y": 300, "x_size": 40, "y_size": 40,
             "shape": "Rectangular", "layer": "TopLayer", "net": "N",
             "rotation": 0},
        ],
        "tracks": [], "vias": [],
    }
    if counts is not None:
        out["counts"] = counts
    return out


def test_a_caller_supplied_plan_says_so(monkeypatch):
    out = asyncio.run(_tools()["route_plan"](geometry=_geom()))
    assert out["ok"]
    assert out["geometry"]["source"] == "caller"
    assert "re-read" in out["geometry"]["note"]


def test_a_fetched_plan_says_it_read_the_board(monkeypatch):
    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            assert command == "generic.get_pcb_geometry"
            return _geom(counts={"pads": 2, "tracks": 0})

    monkeypatch.setattr(route_mod, "get_bridge", lambda: _Bridge())
    out = asyncio.run(_tools()["route_plan"](fetch_geometry=True))
    assert out["ok"]
    assert out["geometry"]["source"] == "live"
    assert "note" not in out["geometry"], (
        "a plan built on a fresh read needs no warning about staleness")


def test_the_reply_carries_the_board_state_it_planned_against():
    """Counts and bbox, so the caller can diff them against a fresh read
    rather than trusting its own memory of what it passed."""
    geom = _geom(bbox=(0, 0, 1811, 945),
                 counts={"pads": 113, "tracks": 466, "vias": 46})
    out = asyncio.run(_tools()["route_plan"](geometry=geom))
    assert out["geometry"]["bbox"] == {"x1": 0, "y1": 0,
                                       "x2": 1811, "y2": 945}
    assert out["geometry"]["counts"]["tracks"] == 466


def test_a_payload_with_no_counts_still_reports_its_source():
    """get_pcb_geometry carries counts; a hand-built dict need not."""
    out = asyncio.run(_tools()["route_plan"](geometry=_geom()))
    assert out["geometry"]["counts"] == {}
    assert out["geometry"]["source"] == "caller"


def test_the_stale_snapshot_this_exists_for_is_visible():
    """The reported shape: plan, place, plan again on the same dict.

    Nothing here can stop the second call, and it should not: the point
    is that its reply names the same board state as the first, which is
    what makes the staleness checkable.
    """
    geom = _geom(counts={"tracks": 0})
    first = asyncio.run(_tools()["route_plan"](geometry=geom))
    second = asyncio.run(_tools()["route_plan"](geometry=geom))
    assert first["geometry"]["counts"] == second["geometry"]["counts"]
    assert second["geometry"]["source"] == "caller"
