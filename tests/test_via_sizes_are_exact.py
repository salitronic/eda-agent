# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_place_via places the size it is given, to a fraction of a mil.

It took whole mils: a 1.2/0.6 mm via went out as 47/24 mil and came back
1.194/0.610 mm, which a metric Routing Via rule flags. The script parsed
the sizes with StrToIntDef, which returns its DEFAULT for "47.244", so
sending decimals alone would have placed a silent 50/28 mil via.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_module

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "altium"


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"placed": True}


def _place_via(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(DummyMcp())
    return captured["pcb_place_via"], bridge


@pytest.mark.parametrize("kwargs,size,hole", [
    ({"size_mm": 1.2, "hole_size_mm": 0.6}, "47.2441", "23.622"),
    ({"size": 47.5, "hole_size": 23.25}, "47.5", "23.25"),
    ({}, "50", "28"),
])
def test_sizes_go_out_unrounded(monkeypatch, kwargs, size, hole):
    tool, bridge = _place_via(monkeypatch)
    asyncio.run(tool(x=1000.5, y=2000, **kwargs))
    (command, params), = bridge.calls
    assert command == "pcb.place_via"
    assert (params["size"], params["hole_size"]) == (size, hole)
    assert params["x"] == "1000.5"


def _routine(name: str) -> str:
    code = (SCRIPTS / "PCB.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\(", code)
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


@pytest.mark.parametrize("name,var,param", [
    ("PCB_PlaceVia", "ViaSize", "SizeStr"),
    ("PCB_PlaceVia", "ViaHole", "HoleSizeStr"),
    ("PCB_PlaceTrack", "TWidth", "WidthStr"),
])
def test_the_script_reads_and_writes_fractions(name, var, param):
    body = _routine(name)
    assert re.search(rf"\b{var}\s*,?[^;]*:\s*Double;", body) or \
        re.search(rf"\b{var}\b[^;:]*:\s*Double", body), f"{var} is not a Double"
    assert f"StrToFloatDef({param}" in body
    assert f"StrToIntDef({param}" not in body
    assert f"MilsToCoordF({var})" in body
    assert f"IntToStr({var})" not in body


def test_a_via_with_no_ring_is_refused_before_anything_is_made():
    body = _routine("PCB_PlaceVia")
    refuse = body.find("'BAD_SIZE'")
    assert refuse > 0
    assert refuse < body.find("PcbBeginEdit(0)")


def test_the_simulator_keeps_the_fraction(e2e_bridge):
    res = e2e_bridge.send_command(
        "pcb.place_via", {"x": "500", "y": "600", "size": "47.2441",
                          "hole_size": "23.622"}, timeout=5.0)
    assert res["size"] == pytest.approx(47.2441)
    assert res["hole_size"] == pytest.approx(23.622)


def test_the_simulator_refuses_a_hole_as_wide_as_the_pad(e2e_bridge):
    from eda_agent.bridge.altium_bridge import AltiumCommandError
    with pytest.raises(AltiumCommandError):
        e2e_bridge.send_command("pcb.place_via", {"x": "1", "y": "1", "size": "20",
                                                  "hole_size": "20"}, timeout=5.0)
