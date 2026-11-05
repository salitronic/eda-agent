# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The copper physically joined to a seed object, found by geometry.

Altium's Tab-select (PCB:SelectNext) grows from the editor's own last
clicked object and cannot be seeded from a script, so there was no way to
ask what copper touches a pad. pcb.connected_copper walks it breadth first:
each object found is searched round its bounding box, and copper joins when
the two pieces share a layer (or one spans every layer) and
Board.PrimPrimDistance is 0.

MEASURED (AD 25.8, an 8-layer board): PGND from C60.1, 691 objects in 33 s;
SGND from C60.2, 427 objects in 15 s; each walk reported only its own net
plus "(no net)" pour pieces. Without the region-kind check the walk leaked
through no-net cutout regions into other nets.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import pcb

REPO = Path(__file__).resolve().parents[1]
ALTIUM = REPO / "scripts" / "altium"


def _routine(name: str) -> str:
    text = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    at = text.index(f"Function {name}(")
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
    return mcp.tools["pcb_connected_copper"], bridge


# -- the MCP tool ------------------------------------------------------------

def test_the_pad_shorthand_becomes_a_component_and_pad_name_filter(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(pad=" C60.1 "))
    assert bridge.sent == [("pcb.connected_copper", {
        "object_type": "pad", "filter": "Component=C60|Name=1", "max": "20000"})]


def test_the_pad_name_is_everything_after_the_first_dot(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(pad="U1.A.1"))
    assert bridge.sent[-1][1]["filter"] == "Component=U1|Name=A.1"


def test_object_type_filter_and_options_are_passed(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(object_type=" track ", filter="Net=GND|Layer=TopLayer",
                     limit=500, include_objects=True, select=True,
                     clear_selection=True))
    assert bridge.sent == [("pcb.connected_copper", {
        "object_type": "track", "filter": "Net=GND|Layer=TopLayer",
        "max": "500", "include_objects": "true", "select": "true",
        "clear_selection": "true"})]


def test_clear_selection_alone_selects_nothing(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    asyncio.run(tool(object_type="via", filter="X=100", clear_selection=True))
    ask = bridge.sent[-1][1]
    assert "select" not in ask and "clear_selection" not in ask


def test_bad_seeds_and_limits_are_refused_before_altium_is_asked(monkeypatch):
    tool, bridge = _tool(monkeypatch)
    for bad in ({"pad": "U1"}, {"pad": ".3"}, {"pad": "U1."},
                {"pad": "U1.3|Net=GND"}, {}, {"object_type": " "},
                {"object_type": "pad", "limit": 0},
                {"object_type": "pad", "limit": True}):
        assert asyncio.run(tool(**bad))["ok"] is False, bad
    assert bridge.sent == []


# -- the Pascal --------------------------------------------------------------

def test_the_command_is_dispatched():
    dispatch = _routine("HandlePCBCommand")
    assert "'connected_copper':" in dispatch
    assert "PCB_ConnectedCopper(Params, RequestId)" in dispatch


def test_the_seed_is_the_first_generic_filter_match():
    body = _routine("PCB_ConnectedCopper")
    assert "CollectPCBMatches(Board, T, FilterStr, 1, Matches);" in body
    assert "'NOT_FOUND'" in body
    assert "If Not ConnectedCopperIsCopper(Seed) Then" in body


def test_a_region_is_copper_only_when_its_kind_is_copper():
    body = _routine("ConnectedCopperIsCopper")
    assert "Result := PcbCheckIsCopper(Prim);" in body
    assert "If Prim.ObjectId = eRegionObject Then" in body
    assert "Result := Rgn.Kind = eRegionKind_Copper;" in body
    walk = _routine("PCB_ConnectedCopper")
    assert "If ConnectedCopperIsCopper(Q) Then" in walk


def test_touching_needs_a_shared_or_spanning_layer_and_no_gap():
    body = _routine("ConnectedCopperTouch")
    assert ("If Not (PcbCheckSpansLayers(A) Or PcbCheckSpansLayers(B) "
            "Or (A.Layer = B.Layer)) Then Exit;") in body
    assert "Board.PrimPrimDistance(A, B) = 0" in body
    walk = _routine("PCB_ConnectedCopper")
    assert "If ConnectedCopperTouch(Board, P, Q) Then" in walk


def test_the_helpers_it_uses_are_built_before_it():
    # Library.pas comes before PCB.pas in the concatenation.
    lib = (ALTIUM / "Library.pas").read_text(encoding="utf-8-sig")
    assert "Function PcbCheckIsCopper(Prim : IPCB_Primitive) : Boolean;" in lib
    assert "Function PcbCheckSpansLayers(Prim : IPCB_Primitive) : Boolean;" in lib
    text = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    walk = text.index("Function PCB_ConnectedCopper(")
    for helper in ("ConnectedCopperIsCopper", "ConnectedCopperTouch",
                   "ConnectedCopperKind"):
        assert text.index(f"Function {helper}(") < walk


def test_the_spatial_search_is_filtered_and_destroyed():
    body = _routine("PCB_ConnectedCopper")
    assert ("Sp.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, "
            "ePadObject, eFillObject, eRegionObject));") in body
    assert "Sp.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);" in body
    assert "Sp.AddFilter_Area(R.Left - Gap, R.Bottom - Gap, R.Right + Gap, R.Top + Gap);" in body
    assert re.search(r"Finally\s+Board\.SpatialIterator_Destroy\(Sp\);", body)


def test_the_walk_stops_at_max_and_says_so():
    body = _routine("PCB_ConnectedCopper")
    assert "While (Head < Queue.Count) And Not Capped Do" in body
    assert re.search(r"If Queue\.Count >= MaxN Then\s+Capped := True", body)
    assert "JsonBool('capped', Capped)" in body


def test_the_selection_filter_is_bypassed_and_restored_in_finally():
    body = _routine("PCB_ConnectedCopper")
    assert "If DoSelect Then" in body
    sel = body[body.index("If DoSelect Then"):]
    assert "WasOff := PcbSelectFilterOff(Board);" in sel
    assert re.search(r"Finally\s+PcbSelectFilterRestore\(Board, WasOff\);", sel)
    assert sel.index("Board.SelectedObjects_BeginUpdate;") < sel.index("Q.Selected := True;")
    assert sel.index("Q.Selected := True;") < sel.index("Board.SelectedObjects_EndUpdate;")
    assert "If ExtractJsonValue(Params, 'clear_selection') = 'true' Then" in sel


def test_nothing_is_selected_unless_asked():
    body = _routine("PCB_ConnectedCopper")
    assert "DoSelect := ExtractJsonValue(Params, 'select') = 'true';" in body
    before = body[:body.index("If DoSelect Then")]
    assert ".Selected := True" not in before
    assert "SelectedObjects_Clear" not in before


def test_copper_with_no_net_is_named_no_net():
    body = _routine("PCB_ConnectedCopper")
    assert "If NetName = '' Then NetName := '(no net)';" in body
    assert "Nets.Duplicates := dupIgnore;" in body and "Nets.Sorted := True;" in body
