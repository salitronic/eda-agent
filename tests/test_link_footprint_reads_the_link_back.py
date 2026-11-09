# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_link_footprint reads the stored link back.

Altium's Models pane is not redrawn after a scripted link and showed the
previous part; the agent stopped twice per take to check the links.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import library


class _Bridge:
    def __init__(self, pins=("1", "2"), pads=("1", "2"), libs=(r"C:\lib\Parts.PcbLib",)):
        self.calls = []
        self.pins = pins
        self.pads = pads
        self.libs = libs

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == "application.get_open_documents":
            return {"documents": [{"file_path": p} for p in
                                  (r"C:\lib\Parts.SchLib", *self.libs)]}
        if command == "library.get_pad_geometry":
            if params["footprint_name"] != "FP1":
                from eda_agent.bridge.exceptions import AltiumError
                raise AltiumError("Footprint not found", code="NO_FOOTPRINT")
            return {"name": "FP1", "pads": [{"name": n, "plated": "true"}
                                            for n in self.pads]}
        if command == "library.get_pin_list":
            return {"pins": [{"designator": n, "display_mode": 0} for n in self.pins]}
        if command == "application.get_active_document":
            return {"file_path": r"C:\lib\Parts.SchLib"}
        if command == "library.link_footprint":
            return {"success": True, "footprint": "FP1", "replaced": True}
        if command == "library.get_component_details":
            return {"models": [{"model_name": "FP1", "model_type": "PCBLIB"},
                               {"model_name": "U1.step", "model_type": "PCB3DLib"}]}
        raise AssertionError(command)


def _tools(monkeypatch, bridge):
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    library.register_library_tools(_Mcp())
    return tools


def test_the_link_reply_lists_the_footprints_stored(monkeypatch):
    b = _Bridge()
    out = asyncio.run(_tools(monkeypatch, b)["lib_link_footprint"](
        component_name="U1", footprint_name="FP1"))
    assert out["success"] and out["footprints"] == ["FP1"]
    assert b.calls[-1] == "library.get_component_details"
    assert out["pin_pad_parity"]["ok"]


def test_pins_and_pads_that_differ_are_not_linked(monkeypatch):
    b = _Bridge(pins=("1", "2", "3"), pads=("1", "2", "EP"))
    out = asyncio.run(_tools(monkeypatch, b)["lib_link_footprint"](
        component_name="U1", footprint_name="FP1"))
    assert not out["success"] and out["check"] == "pins_pads"
    assert out["pin_pad_parity"]["pins_without_pad"] == ["3"]
    assert out["pin_pad_parity"]["pads_without_pin"] == ["EP"]
    assert "pins with no pad: 3" in out["reason"]
    assert "library.link_footprint" not in b.calls


def test_a_footprint_in_no_open_library_is_not_linked_unchecked(monkeypatch):
    b = _Bridge(libs=())
    out = asyncio.run(_tools(monkeypatch, b)["lib_link_footprint"](
        component_name="U1", footprint_name="FP1"))
    assert not out["success"] and "no open .PcbLib holds" in out["reason"]
    assert "library.link_footprint" not in b.calls


def test_the_panel_can_switch_the_pin_check_off(monkeypatch):
    from eda_agent import preferences
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": "pins_pads"})
    b = _Bridge(pins=("1", "2", "3"), pads=("1",), libs=())
    out = asyncio.run(_tools(monkeypatch, b)["lib_link_footprint"](
        component_name="U1", footprint_name="FP1"))
    assert out["success"] and out["unchecked"]["check"] == "pins_pads"
    assert "library.get_pin_list" not in b.calls
