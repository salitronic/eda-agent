# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Symbols made in one batch call each get their own pins (#263).

The symbol writers (add_pins, add_symbol_rectangle, ...) take no symbol
name: they write into the symbol created last. Gathered into bulk
requests, every item's create_symbol ran before any item's add_pins, so
all the pins of a three-symbol batch landed on the third symbol and the
first two stayed empty, while every reply said success. Measured on
Altium 25.8.1: 0, 0 and 15 pins for 3, 5 and 7 sent.

The fake library below files objects the way the bridge does, on the
symbol created last, and the bridge is the real send path, so gathering
is what is tested.
"""

from __future__ import annotations

import asyncio

import pytest

import eda_agent.bridge as bridge_pkg
from eda_agent.bridge import altium_bridge as ab
from eda_agent.tools import bulk as tool_bulk
from eda_agent.tools import library as lib_tools


class _SchLib:
    """A SchLib behind the real send path: writers go to the last symbol."""

    send_command_async = ab.AltiumBridge.send_command_async
    send_bulk_async = ab.AltiumBridge.send_bulk_async
    _bulk_item_outcome = staticmethod(ab.AltiumBridge._bulk_item_outcome)

    def __init__(self):
        self.symbols: dict[str, list] = {}
        self.last = None
        self.order: list = []

    async def _library_to_focus(self, command, exc):
        return ""

    def _one(self, command, p):
        self.order.append(command)
        if command == "application.get_active_document":
            return {"file_path": r"C:\lib\Parts.SchLib"}
        if command == "library.create_symbol":
            self.symbols[p["name"]] = []
            self.last = p["name"]
            return {"success": True, "name": p["name"], "verified": True}
        if command == "library.add_pins":
            pins = [dict(kv.split("=", 1) for kv in op.split(";"))
                    for op in p["pins"].split("~~")]
            self.symbols[self.last].extend(pins)
            return {"added": len(pins), "failed": 0, "total": len(pins)}
        if command == "library.get_pin_list":
            pins = self.symbols.get(p["component_name"], [])
            return {"component": p["component_name"], "count": len(pins),
                    "pins": [{"designator": q["designator"]} for q in pins]}
        return {"success": True}

    async def send_command_direct(self, command, params=None, timeout=None):
        params = dict(params or {})
        count = params.get("bulk_count")
        if count is None:
            return self._one(command, params)
        return {"bulk": True, "items": [
            {"protocol_version": 1, "id": "x", "success": True,
             "data": self._one(command, params[f"bulk_item_{i}"]), "error": None}
            for i in range(count)]}


@pytest.fixture
def schlib(monkeypatch):
    lib = _SchLib()
    monkeypatch.setattr(bridge_pkg, "get_bridge", lambda: lib)
    monkeypatch.setattr(lib_tools, "get_bridge", lambda: lib)
    return lib


def _tools():
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = tool_bulk.with_items(fn, fn.__name__)
                return tools[fn.__name__]
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    lib_tools.register_library_tools(_Mcp())
    return tools


def _pins(n, base):
    return [{"designator": f"{base}{i}", "name": f"{base}{i}"}
            for i in range(1, n + 1)]


def test_each_ic_symbol_in_a_batch_gets_its_own_pins(schlib):
    tool = _tools()["lib_create_ic_symbol"]
    out = asyncio.run(tool(items=[
        {"name": "A", "left_pins": _pins(3, "A"), "right_pins": []},
        {"name": "B", "left_pins": _pins(5, "B"), "right_pins": []},
        {"name": "C", "left_pins": _pins(7, "C"), "right_pins": []}]))
    assert {k: len(v) for k, v in schlib.symbols.items()} == \
        {"A": 3, "B": 5, "C": 7}, (
        "the pins went to the symbol created last: the items' creates "
        "were gathered ahead of their pins")
    assert out["failed"] == 0
    assert [i["pins_verified"] for i in out["items"]] == [True, True, True]


def test_the_symbol_creators_run_their_items_one_after_another():
    for name in ("lib_create_ic_symbol", "lib_create_symbol_in_parts",
                 "lib_create_passive_symbol"):
        assert name in tool_bulk.SEQUENTIAL_TOOLS, name


def test_each_part_symbol_in_a_batch_gets_its_own_pins(schlib):
    tool = _tools()["lib_create_symbol_in_parts"]
    out = asyncio.run(tool(items=[
        {"name": "P", "parts": [{"left_pins": _pins(2, "P"), "right_pins": []},
                                {"left_pins": _pins(1, "PP"), "right_pins": []}]},
        {"name": "Q", "parts": [{"left_pins": _pins(4, "Q"), "right_pins": []},
                                {"left_pins": _pins(2, "QQ"), "right_pins": []}]}]))
    assert {k: len(v) for k, v in schlib.symbols.items()} == {"P": 3, "Q": 6}
    assert [i["ok"] for i in out["items"]] == [True, True]


def test_pins_found_on_another_symbol_fail_the_item(schlib):
    """The read-back is what makes 'verified' mean the pins are there."""
    real = schlib._one

    def misfile(command, p):
        if command == "library.add_pins":
            schlib.last = "ELSEWHERE"
            schlib.symbols.setdefault("ELSEWHERE", [])
        return real(command, p)

    schlib._one = misfile
    out = asyncio.run(_tools()["lib_create_ic_symbol"](
        name="X", left_pins=_pins(3, "X"), right_pins=[]))
    assert out["ok"] is False and out["pins_verified"] is False
    assert out["pins_found"] == 0 and out["missing_pins"] == ["X1", "X2", "X3"]


def test_an_unreadable_pin_list_does_not_fail_the_tool():
    class _Quiet:
        async def send_command_async(self, command, params=None, timeout=None):
            return {"echo": True}
    out = asyncio.run(lib_tools._pin_check(_Quiet(), "X", _pins(2, "X")))
    assert out["pins_verified"] is None and "ok" not in out


def test_the_standard_footprint_names_the_footprint_it_fills(monkeypatch):
    sent = []

    class _PcbLib:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, dict(params or {})))
            return {"success": True}

    monkeypatch.setattr(lib_tools, "get_bridge", lambda: _PcbLib())
    asyncio.run(_tools()["lib_create_standard_footprint"](
        name="SOIC8_TEST", family="dual", pin_count=8, row_span=200))
    writes = [p for c, p in sent if c in ("library.add_footprint_pads",
                                          "library.add_footprint_tracks")]
    assert writes and all(p.get("footprint_name") == "SOIC8_TEST" for p in writes)


def test_a_part_symbol_whose_pins_are_elsewhere_is_not_ok(schlib):
    real = schlib._one

    def misfile(command, p):
        if command == "library.add_pins":
            schlib.last = "ELSEWHERE"
            schlib.symbols.setdefault("ELSEWHERE", [])
        return real(command, p)

    schlib._one = misfile
    out = asyncio.run(_tools()["lib_create_symbol_in_parts"](
        name="R", parts=[{"left_pins": _pins(2, "R"), "right_pins": []},
                         {"left_pins": _pins(1, "RR"), "right_pins": []}]))
    assert out["ok"] is False and out["pins_verified"] is False
