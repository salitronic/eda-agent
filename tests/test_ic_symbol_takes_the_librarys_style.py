# SPDX-License-Identifier: Apache-2.0
"""An IC symbol can be drawn in the style of the library it goes into.

lib_create_ic_symbol drew every rectangle symbol with 200 mil pins, a pale
yellow body and a black outline. A library whose own ICs use 300 mil pins
and another fill then got one symbol that matches none of its neighbours,
and putting that right took a write per primitive.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import library as lib_mod
from eda_agent.tools.registry import ToolRegistry

LEFT = [{"designator": "1", "name": "1"}, {"designator": "2", "name": "2"}]
RIGHT = [{"designator": "3", "name": "3"}]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


@pytest.fixture
def tool(monkeypatch):
    bridge = _Bridge()

    async def passes(*args, **kwargs):
        return {}

    async def gate(datasheet, pins):
        return None, {}

    monkeypatch.setattr(lib_mod, "get_bridge", lambda: bridge)
    monkeypatch.setattr(lib_mod, "_pin_table_gate", gate)
    monkeypatch.setattr(lib_mod, "_create_symbol", passes)
    monkeypatch.setattr(lib_mod, "_pin_check", passes)
    monkeypatch.setattr(lib_mod, "_show_new_symbol", passes)
    registry = ToolRegistry()
    lib_mod.register_library_tools(registry)
    fn = {t.name: t.fn for t in asyncio.run(registry.list_tools())}["lib_create_ic_symbol"]
    return bridge, fn


def _sent(bridge, command):
    return next(params for name, params in bridge.sent if name == command)


def _pin_lengths(bridge):
    ops = _sent(bridge, "library.add_pins")["pins"].split("~~")
    return {dict(kv.split("=", 1) for kv in op.split(";"))["length"] for op in ops}


def test_the_defaults_are_what_they_were(tool):
    bridge, fn = tool
    out = asyncio.run(fn(name="X", left_pins=LEFT, right_pins=RIGHT, body="rectangle"))
    assert out.get("ok", True) is not False, out
    rect = _sent(bridge, "library.add_symbol_rectangle")
    assert (rect["fill_color"], rect["border_color"]) == (8454143, 0)
    assert _pin_lengths(bridge) == {"200"}


def test_pin_length_and_colours_are_taken_from_the_call(tool):
    bridge, fn = tool
    asyncio.run(fn(name="X", left_pins=LEFT, right_pins=RIGHT, body="rectangle",
                   pin_length=300, fill_color=11599871, border_color=128))
    rect = _sent(bridge, "library.add_symbol_rectangle")
    assert (rect["fill_color"], rect["border_color"]) == (11599871, 128)
    assert _pin_lengths(bridge) == {"300"}


@pytest.mark.parametrize("bad", [{"pin_length": 250}, {"pin_length": 0},
                                 {"fill_color": -1}, {"border_color": 16777216}])
def test_a_bad_value_is_refused_before_anything_is_written(tool, bad):
    bridge, fn = tool
    out = asyncio.run(fn(name="X", left_pins=LEFT, right_pins=RIGHT, body="rectangle", **bad))
    assert out["ok"] is False and out["reason"]
    assert bridge.sent == []
