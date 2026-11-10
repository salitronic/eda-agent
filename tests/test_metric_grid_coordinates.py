# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic coordinates keep their fraction of a mil.

On a metric sheet a pin end sits at 30905.45 mil. The wire tool cut every
coordinate to a whole mil, so the wire ended 0.45 mil short of the pin
and the connection was not made. The bridge already parses fractions.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import generic


def _tools(monkeypatch):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(generic, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(Mcp())
    return tools, sent


def test_wire_ends_keep_their_fraction(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["sch_place_wires"](
        [{"x1": 30905.45, "y1": 22637.75, "x2": 31000, "y2": 22145.625}]))
    assert sent == [("generic.place_wires",
                     {"wires": "x1=30905.45;y1=22637.75;x2=31000;y2=22145.625"})]


def test_a_metric_grid_keeps_its_fraction(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["obj_set_grid"](snap_grid=19.685, visible_grid=98.4252,
                                      hotspot_grid=3.937))
    assert sent[0][1] == {"snap_grid": "19.685", "visible_grid": "98.4252",
                          "hotspot_grid": "3.937"}


def test_a_symbol_location_keeps_its_fraction(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["sch_set_component_part_id"](
        designator="U1", part_id=2, location_x=1968.5, location_y=-393.7))
    assert sent[0][1]["location_x"] == "1968.5"
    assert sent[0][1]["location_y"] == "-393.7"
