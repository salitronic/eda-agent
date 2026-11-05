# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_plane_connections covers vias and free pads, and says which planes an
object really touches.

The reader took one part's pads and reported the connection the RULES give,
so every SMD pad answered "relief" though it never meets a plane, no via
could be read at all, and a hand-edited thermal relief was invisible (the
rule's values read the same on such a pad). Now each object carries the
planes it passes through with the plane's net and whether it joins it, the
ruling rule names and its manual relief count; without a designator the
board's free vias or free pads are read.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from tests.pascal_source import load

from eda_agent.tools import pcb

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _units() -> dict:
    return load(_ALTIUM / "PCB.pas", minimum=20)


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def reg(fn):
            self.tools[fn.__name__] = fn
            return fn
        return reg


class _Bridge:
    def __init__(self, reply):
        self.sent = []
        self.reply = reply

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return self.reply


def _run(monkeypatch, reply, **kwargs):
    bridge = _Bridge(reply)
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools["pcb_plane_connections"](**kwargs)), bridge.sent


def test_free_vias_are_asked_for_with_net_and_limit(monkeypatch):
    reply = {"vias": [{"rule_style": 1}, {"rule_style": 2}]}
    result, sent = _run(monkeypatch, reply, kind="Via", net="GND", limit=20)
    assert sent == [("pcb.plane_connections", {"kind": "via", "net": "GND", "limit": "20"})]
    assert [v["connection"] for v in result["vias"]] == ["direct", "none"]


def test_a_part_still_goes_by_designator(monkeypatch):
    _, sent = _run(monkeypatch, {"pads": []}, designator="U1", kind="via")
    assert sent == [("pcb.plane_connections", {"designator": "U1"})]


def test_nothing_to_read_is_refused_before_the_bridge(monkeypatch):
    result, sent = _run(monkeypatch, {}, kind="track")
    assert sent == [] and result["ok"] is False


def test_contacts_follow_the_planes_an_object_crosses():
    body = _units()["PlaneConnItem"]
    # A hole spans every plane; a via only the layers it intersects.
    assert re.search(r"Crosses\s*:=\s*Pad\.HoleSize\s*>\s*0", body)
    assert re.search(r"Crosses\s*:=\s*Via\.IntersectLayer\(Lyr\)", body)
    assert body.index("Pad.HoleSize > 0") < body.index("Via.IntersectLayer(Lyr)")
    # The split region under the object decides the plane's net.
    assert "PlaneNet := PlaneNetAt(Board, Lyr, Pad.X, Pad.Y);" in body
    assert re.search(r"JsonBool\('joins',\s*\(NetName <> ''\)\s*And\s*\(PlaneNet = NetName\)\)", body)


def test_rules_and_manual_reliefs_are_reported():
    body = _units()["PlaneConnItem"]
    assert "FindDominantRuleForObject(Pad, eRule_PowerPlaneConnectStyle)" in body
    assert "FindDominantRuleForObject(Pad, eRule_PowerPlaneClearance)" in body
    assert "Manual := Pad.ManualThermalReliefsCount" in body
    assert "JsonInt('manual_reliefs', Manual)" in body


def test_free_objects_walk_only_free_vias_or_pads_and_stop_at_the_limit():
    body = _units()["PCB_PlaneConnections"]
    assert "Iter.AddFilter_ObjectSet(MkSet(eViaObject))" in body
    assert "Iter.AddFilter_Method(eProcessFree)" in body
    # Both walks, a part's pads and the free objects, stop at the limit.
    assert body.count("If Count < Limit Then") == 2
    assert body.count("Else Truncated := True;") == 2
    assert "JsonRaw('planes', '[' + Planes + ']')" in body


def test_a_split_plane_answers_with_the_region_under_the_point():
    body = _units()["PlaneNetAt"]
    assert "Iter.AddFilter_ObjectSet(MkSet(eSplitPlaneObject))" in body
    assert re.search(r"If Split\.PointInPolygon\(X, Y\) Then", body)
    # The layer's own net only when no region holds the point; "(No Net)" is none.
    assert body.index("PointInPolygon") < body.index("InternalPlaneNetName")
    assert re.search(r"If Not Found Then", body)
    assert "If Result = '(No Net)' Then Result := '';" in body
