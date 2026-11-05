# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""One pour's poured copper, holes included, from the regions it holds.

The board's copper read hands every pour out as a page of objects, and
pcb_get_polygons sums the copper of a pour through Region.Holes, whose
points count from 0 or from 1 depending on the contour (RegionCopperSqMils
has to guess, and gives up with copper_area_exact false). Each region's own
geometric polygon lists its contours from 0 and says which are holes, so
pcb.pour_copper reads one pour's copper exactly.

MEASURED (AD 25.8): IPCB_Region.GeometricPolygon is declared and reads
the outline and every hole; IPCB_Polygon.GeometricPolygon is not. On a
pour of 112 contours (111 holes, 2498 vertices) the reply matched the
board's copper read point for point and to the square mil.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from eda_agent.tools import pcb

REPO = Path(__file__).resolve().parents[1]
ALTIUM = REPO / "scripts" / "altium"
sys.path.insert(0, str(ALTIUM))
import lint  # noqa: E402


def _routine(name: str) -> str:
    text = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    head = f"Function {name}("
    at = text.index(head)
    return text[at:text.index("\nEnd;", at)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def keep(fn):
            self.tools[fn.__name__] = fn
            return fn
        return keep

    def __getattr__(self, name):
        return lambda *a, **k: (lambda fn: fn)


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return mcp.tools["pcb_get_pour_copper"], bridge


def test_the_tool_sends_the_index_and_trimmed_name(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(index=3, name=" GND Top "))
    assert bridge.sent == [("pcb.pour_copper", {"index": "3", "name": "GND Top"})]
    asyncio.run(tool(index=0))
    assert bridge.sent[-1] == ("pcb.pour_copper", {"index": "0"})


def test_a_bad_index_is_refused_before_altium_is_asked(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    for bad in (-1, True, 1.5):
        assert asyncio.run(tool(index=bad))["ok"] is False
    assert bridge.sent == []


def test_the_command_is_dispatched():
    dispatch = _routine("HandlePCBCommand")
    assert "'pour_copper':" in dispatch
    assert "PCB_PourCopper(Params, RequestId)" in dispatch


def test_the_pour_is_found_and_checked_as_pour_outline_does():
    body = _routine("PCB_PourCopper")
    assert "Pour := PourAtPosition(Board, Wanted, Passed);" in body
    assert "'POUR_CHANGED'" in body
    assert "UpperCase(Expected) <> UpperCase(Pour.Name)" in body


def test_each_region_is_read_through_its_own_geometric_polygon():
    body = _routine("PCB_PourCopper")
    assert re.search(r"Region\s*:\s*IPCB_Region;", body)
    regions = body[body.index("MkSet(eRegionObject)"):]
    assert "Shape := Region.GeometricPolygon;" in regions
    # Not the pour's own, which this binding does not declare.
    assert not re.search(r"\bPour\.GeometricPolygon\b", body)
    # Every contour, from 0, sorted into outlines and holes by IsHole.
    assert "For I := 0 To Shape.Count - 1 Do" in body
    assert "If Shape.IsHole(I) Then" in body
    assert "PieceArea := PieceArea - Contour.Area / 100000000.0;" in body
    assert "PieceArea := PieceArea + Contour.Area / 100000000.0;" in body


def test_contour_points_count_from_zero_in_mils():
    body = _routine("PourContourJson")
    assert "For J := 0 To Contour.Count - 1 Do" in body
    assert "CoordToMilsF(Contour.x(J))" in body
    assert "CoordToMilsF(Contour.y(J))" in body


def test_both_walks_are_filtered_and_their_iterators_destroyed():
    body = _routine("PCB_PourCopper")
    assert body.count("Iter := Pour.GroupIterator_Create;") == 2
    assert body.count("Pour.GroupIterator_Destroy(Iter);") == 2
    assert "Iter.AddFilter_ObjectSet(MkSet(eRegionObject));" in body
    assert "Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject));" in body


def test_the_linter_refuses_the_polygons_geometric_polygon_not_the_regions():
    bad = lint.RULE_KNOWN_WRONG_PROPERTY.pattern
    assert bad.search("    Shape := Pour.GeometricPolygon;")
    assert bad.search("    Shape := Polygon.GeometricPolygon;")
    assert not bad.search("    Shape := Region.GeometricPolygon;")
    assert not lint.RULE_KNOWN_WRONG_METHOD.pattern.search(
        "    Shape := Region.GeometricPolygon;")
