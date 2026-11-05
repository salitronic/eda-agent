# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_place_ring puts parts evenly round a circle, each facing the centre.

The round LED take computed 61 LED positions and rotations itself, ring by
ring, and got the rotations wrong once.
"""

from __future__ import annotations

import asyncio
import math

from eda_agent.tools import pcb


def _place(monkeypatch, **kw):
    sent = []

    class _B:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"applied": len(params["placements"].split("|"))}

    monkeypatch.setattr(pcb, "get_bridge", lambda: _B())
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    pcb.register_pcb_tools(_Mcp())
    out = asyncio.run(tools["pcb_place_ring"](**kw))
    return out, sent


def test_a_full_ring_is_spaced_evenly_clockwise_from_the_top(monkeypatch):
    out, sent = _place(monkeypatch, designators=["L1", "L2", "L3", "L4"],
                       center_x=5000, center_y=5000, radius=1000)
    xy = [(p["x"], p["y"]) for p in out["positions"]]
    assert xy == [(5000, 6000), (6000, 5000), (5000, 4000), (4000, 5000)]
    assert [p["rotation"] for p in out["positions"]] == [90, 0, 270, 180]
    assert sent[0][0] == "pcb.import_placement"
    assert sent[0][1]["placements"].split("|")[1] == "L2,6000,5000,0,"


def test_an_arc_uses_both_ends_and_a_fixed_rotation_holds(monkeypatch):
    out, sent = _place(monkeypatch, designators=["A", "B", "C"], center_x=0,
                       center_y=0, radius=100, start_angle=0, span=90,
                       clockwise=False, rotation=45, side="bottom")
    angles = [math.degrees(math.atan2(p["y"], p["x"])) for p in out["positions"]]
    assert [round(a) for a in angles] == [0, 45, 90]
    assert all(p["rotation"] == 45 for p in out["positions"])
    assert sent[0][1]["placements"].endswith(",45,BottomLayer")
