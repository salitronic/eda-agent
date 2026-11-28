# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_place_custom_pads makes a free pad on a board whose copper is an
outline, through the same route a footprint's custom pad takes: the outline
drawn as a region and linked to the pad with LinkCustomShape.

The outline, anchor, rounding and hole checks are the footprint's; a board
pad may have no name and carries a net.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import pcb

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"
_L = [[0, 0], [80, 0], [80, 20], [20, 20], [20, 60], [0, 60]]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kw):
        self.sent.append((command, params))
        return {"added": 1}


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Mcp())
    return tools["pcb_place_custom_pads"], bridge


def _fields(op: str) -> dict[str, str]:
    return dict(f.split("=", 1) for f in op.split(";"))


def test_a_board_pad_goes_out_with_its_net_and_an_anchor_in_the_copper(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(pads=[{"outline": _L, "net": "GND", "name": "TP1"}], units="mm"))
    command, params = bridge.sent[0]
    assert command == "pcb.place_custom_pads" and params["units"] == "mm"
    f = _fields(params["pads"])
    assert (f["designator"], f["net"], f["layer"]) == ("TP1", "GND", "TopLayer")
    # The L's box middle (40, 30) is in its notch; the anchor is not.
    assert (float(f["x"]), float(f["y"])) != (40.0, 30.0)
    assert f["points"] == "0,0/80,0/80,20/20,20/20,60/0,60"


def test_a_board_pad_may_have_no_name():
    payload, problems = pcb._custom_pads_payload([{"outline": _L}], board=True)
    assert problems == [] and _fields(payload)["designator"] == ""
    assert "net" not in _fields(payload)


def test_a_footprint_pad_still_needs_its_designator():
    _, problems = pcb._custom_pads_payload([{"outline": _L}])
    assert problems == ["custom pad 0 has no designator"]


def test_refusals_send_nothing(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    got = asyncio.run(tool(pads=[{"outline": _L, "x": 40, "y": 30}]))
    assert got["ok"] is False and "outside the outline" in got["reason"]
    got = asyncio.run(tool(pads=[{"outline": [[0, 0], [10, 0], [10, 3], [0, 3]],
                                  "corner_radius_length": 2}]))
    assert got["ok"] is False and "too short" in got["reason"]
    got = asyncio.run(tool(pads=[{"outline": _L, "hole_size": 30}]))
    assert got["ok"] is False and "hole" in got["reason"]
    assert asyncio.run(tool(pads=[]))["ok"] is False
    assert bridge.sent == []


def test_rounded_corners_and_a_hole_go_out(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    hexagon = [[1.5, 0], [0.75, 1.3], [-0.75, 1.3], [-1.5, 0], [-0.75, -1.3], [0.75, -1.3]]
    asyncio.run(tool(pads=[{"outline": hexagon, "x": 0, "y": 0, "hole_size": 1.0,
                            "corner_radius_length": 0.1}], units="mm"))
    f = _fields(bridge.sent[0][1]["pads"])
    assert f["hole_size"] == "1" and f["arcs"]


def _body(name: str) -> str:
    text = _PCB.read_text(encoding="utf-8", errors="replace")
    start = text.index(f"Function {name}(")
    return re.sub(r"\{[^}]*\}", "", text[start:text.index("\nEnd;", start)])


def test_the_script_links_the_outline_as_the_pads_shape_and_binds_its_net():
    body = _body("PCB_PlaceCustomPads")
    assert "LibOutlineRegion(Layer" in body and ", UnitsStr, 0, 0)" in body
    assert "Pad.LinkCustomShape(Region)" in body and "Pad.HasCustomShapes" in body
    assert "BindPrimitiveToNet(FoundNet, Pad)" in body
    assert "Pad.Layer := eMultiLayer" in body
    # The region is the pad's shape only; it is never added to the board.
    assert "Board.AddPCBObject(Region)" not in body


def test_the_dispatcher_routes_it():
    text = _PCB.read_text(encoding="utf-8", errors="replace")
    assert "'place_custom_pads':       Result := PCB_PlaceCustomPads(Params, RequestId);" in text
