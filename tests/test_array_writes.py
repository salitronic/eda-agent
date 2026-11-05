# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Many board objects of one kind in one call, at the cost of the objects.

Measured on AD 25.8 (testboard, 1000 objects per call): a track or via
costs ~5 ms to create, but every row's layer name went through
ResolveLayerId, which matches user names first (the stack, then every
mechanical layer to 1024). A canonical token whose layer carries another
user name ("TopLayer" for a top copper named "L1 (TOP)") took ~0.7 s per
row; 1000 such tracks held Altium for 12 minutes. A named net walked every
net on the board per row. Arcs and free pads had no list handler at all:
one round trip each, ~156 and ~200 ms. So the resolver tries the token
before the walk, the list handlers look each layer and net up once per
call, and pcb.place_arcs / pcb.place_pads place a list in one edit; a pad
list is one template, moved, turned and named. A row with no layer on
those two (or "current" anywhere) takes the editor's current layer, read
once per call and named in the reply.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import bulk
from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_PCB = _ALTIUM / "PCB.pas"
_UTILS = _ALTIUM / "Utils.pas"

_LIST_HANDLERS = ("PCB_PlaceTracks", "PCB_PlaceVias", "PCB_PlaceArcs",
                  "PCB_PlacePads")


def _pcb():
    return load(_PCB)


def _loop(body):
    """The per-row part of a list handler: from its edit to its end."""
    start = body.index("PcbBeginEdit(0);")
    end = body.index("PcbEndEdit(0);")
    return body[start:end]


# ------------------------------------------------------------- the cache

def test_the_cache_answers_a_name_it_has_seen_without_the_resolver():
    body = load(_UTILS)["ResolveLayerIdCached"]
    resolve = body.index("Result := ResolveLayerId(Board, LayerName);")
    raw = body.index("Hit := CachedLayerAt(Cache, LayerName);")
    assert body.index("Exit;", raw) < resolve
    assert body.index("Hit := CachedLayerAt(Cache, Key);") < resolve
    assert "Cache := Cache + Chr(1) + Key + Chr(2) + IntToStr(Result) + Chr(1);" in body


def test_the_name_as_given_is_tried_before_it_is_normalised():
    """Normalising walks the name a character at a time: ~7 ms a row."""
    body = load(_UTILS)["ResolveLayerIdCached"]
    assert body.index("CachedLayerAt(Cache, LayerName)") < body.index(
        "Key := NormalizeLayerName(LayerName);")
    assert ("If LayerName <> Key Then\n        Cache := Cache + Chr(1) + LayerName"
            in body.replace(chr(13), ""))


def test_the_cache_keys_by_the_resolvers_own_normalised_name():
    """"Top Layer" and "toplayer" are one name to the resolver, so one key."""
    body = load(_UTILS)["ResolveLayerIdCached"]
    assert "Key := NormalizeLayerName(LayerName);" in body
    assert "Pos(Chr(1) + Key + Chr(2), Cache)" in load(_UTILS)["CachedLayerAt"]


def test_the_resolver_tries_the_token_before_walking_the_layers():
    body = load(_UTILS)["ResolveLayerIdInStack"]
    walk = body.index("LayerStack.FirstLayer")
    assert body.index("Candidate := GetLayerFromString(Stripped);") < walk
    assert body.index("MechNum := ParseMechLayerNumber(Stripped);") < walk
    assert body.index("For MechN := 1 To 1024 Do") > walk


def test_a_token_still_needs_its_round_trip():
    """GetLayerFromString answers eTopLayer for any typo: only a token that
    reads back as itself is taken."""
    body = load(_UTILS)["ResolveLayerIdInStack"]
    take = body.index("If UpperCase(GetLayerString(Candidate)) = Wanted Then")
    assert take < body.index("Result := Candidate;")


@pytest.mark.parametrize("name", _LIST_HANDLERS)
def test_list_handlers_resolve_rows_through_the_cache(name):
    body = _pcb()[name]
    rows = _loop(body)
    assert "ResolveLayerIdCached(" in rows or "ListRowLayer(" in rows
    assert not re.search(r"\bResolveLayerId\(", rows), (
        f"{name} resolves a row's layer uncached: up to 0.7 s per row")
    assert body.index("LayerCache := '';") < body.index("PcbBeginEdit(0);")


def test_a_row_layer_is_current_empty_or_a_cached_name():
    body = _pcb()["ListRowLayer"]
    assert "If LayerStr = '' Then Result := Empty" in body
    assert "Else If LowerCase(Trim(LayerStr)) = 'current' Then Result := CurLayer" in body
    assert "Else Result := ResolveLayerIdCached(Board, LayerStr, Cache);" in body


@pytest.mark.parametrize("name", ["PCB_PlaceTracks", "PCB_PlaceArcs",
                                  "PCB_PlacePads"])
def test_a_row_with_no_layer_goes_on_the_top_layer(name):
    """Every list puts a row with no layer on the top layer, as the track and
    via lists always have; the current layer is asked for by name."""
    rows = _loop(_pcb()[name])
    assert "ListRowLayer(Board, LayerStr, eTopLayer, CurLayer, LayerCache)" in rows
    # only a row that named "current" counts as placed there
    assert "(LayerStr = '')" not in rows[rows.index("Inc(Placed)"):]


@pytest.mark.parametrize("name", ("PCB_PlaceTracks", "PCB_PlaceArcs", "PCB_PlacePads"))
def test_the_current_layer_is_read_once_and_named_in_the_reply(name):
    body = _pcb()[name]
    read = body.index("Try CurLayer := Board.CurrentLayer; Except CurLayer := eNoLayer; End;")
    assert read < body.index("PcbBeginEdit(0);")
    assert body.count("Board.CurrentLayer") == 1
    assert "CurrentLayerJson(CurLayer, OnCurrent)" in body[body.index("PcbEndEdit(0);"):]


def test_the_reply_names_the_layer_only_when_one_went_there():
    body = _pcb()["CurrentLayerJson"]
    assert body.index("If OnCurrent <= 0 Then Exit;") < body.index("LayerDisplayName(CurLayer)")


@pytest.mark.parametrize("name", _LIST_HANDLERS)
def test_list_handlers_search_a_net_only_when_the_name_changes(name):
    rows = _loop(_pcb()[name])
    find = rows.index("LastNet := FindNetByName(Board,")
    guard = rows.rfind("<> LastNetName Then", 0, find)
    assert guard != -1 and find - guard < 120
    assert rows.count("FindNetByName(") == 1


@pytest.mark.parametrize("name", ("PCB_PlaceArcs", "PCB_PlacePads"))
def test_one_edit_and_one_broadcast_per_call(name):
    body = _pcb()[name]
    assert body.count("PcbBeginEdit(0);") == 1
    assert body.count("SendMessageToRobots(") == 1
    loop_end = body.index("PCBServer.SendMessageToRobots(")
    assert body.rindex("Inc(Placed);") < loop_end
    assert "c_NoEventData" in body[loop_end:]


# ------------------------------------------------------------- arcs

def test_an_arc_row_falls_back_to_the_calls_own_defaults():
    rows = _loop(_pcb()["PCB_PlaceArcs"])
    assert "If F5 = '' Then F5 := DefWidth;" in rows
    assert "If LayerStr = '' Then LayerStr := DefLayer;" in rows
    assert "If NetStr = '' Then NetStr := DefNet;" in rows
    assert "If KeepStr = '' Then KeepStr := DefKeep;" in rows


def test_an_arc_keeps_place_arcs_keepout_rule():
    rows = _loop(_pcb()["PCB_PlaceArcs"])
    assert ("If (KeepStr = '1') Or ((KeepStr = '') And (ArcLayer = eKeepOutLayer)) Then"
            in rows)


def test_an_arc_with_no_radius_is_counted_not_placed():
    rows = _loop(_pcb()["PCB_PlaceArcs"])
    bad = rows.index("If (ARad <= 0) Or (AWidth < 0) Then")
    assert rows.index("Inc(BadGeometry);", bad) < rows.index("PCBObjectFactory(")


# ------------------------------------------------------------- pads

def test_the_pad_template_is_read_once_before_the_rows():
    body = _pcb()["PCB_PlacePads"]
    rows = _loop(body)
    assert "ExtractJsonValue(" not in rows
    for key in ("shape", "x_size", "y_size", "corner_radius", "hole_size",
                "hole_length", "mask_expansion", "paste_expansion"):
        assert f"ExtractJsonValue(Params, '{key}')" in body


def test_a_drilled_pad_goes_on_multilayer():
    rows = _loop(_pcb()["PCB_PlacePads"])
    assert "If HoleSize > 0 Then PadLayer := eMultiLayer" in rows


def test_the_layer_is_set_before_the_roundrect_shape():
    """The roundrect setters are per layer: a shape before the layer is lost."""
    rows = _loop(_pcb()["PCB_PlacePads"])
    assert rows.index("Pad.Layer := PadLayer;") < rows.index("SetPadRoundRect(Pad, CornerPct)")


def test_a_roundrect_pad_off_the_top_layer_gets_the_top_shape_too():
    """A simple pad draws its TOP shape everywhere: a bottom roundrect read
    back round live until the top layer's shape was set as well."""
    rows = _loop(_pcb()["PCB_PlacePads"])
    guard = rows.index("If (PadLayer <> eTopLayer) And (PadLayer <> eMultiLayer) Then")
    assert rows.index("SetPadRoundRect(Pad, CornerPct);") < guard
    assert rows.index("Pad.SetState_StackShapeOnLayer(eTopLayer, eRoundedRectangular);", guard)
    assert rows.index("Pad.SetState_StackCRPctOnLayer(eTopLayer, CornerPct);", guard)


def test_each_pad_is_turned_and_named_by_its_row():
    rows = _loop(_pcb()["PCB_PlacePads"])
    assert "Pad.Rotation := Rot;" in rows
    assert "If F3 <> '' Then Pad.Name := F3;" in rows


def test_masks_go_through_the_pad_cache_as_manual():
    rows = _loop(_pcb()["PCB_PlacePads"])
    assert "Cache.SolderMaskExpansionValid := eCacheManual;" in rows
    assert "Cache.PasteMaskExpansionValid := eCacheManual;" in rows
    assert "Pad.SetState_Cache := Cache;" in rows


def test_a_bad_template_is_refused_before_anything_is_placed():
    body = _pcb()["PCB_PlacePads"]
    edit = body.index("PcbBeginEdit(0);")
    for code in ("'BAD_SHAPE'", "'BAD_CORNER'", "'BAD_SIZE'", "'BAD_UNITS'"):
        assert body.index(code) < edit


def test_both_commands_are_routed():
    text = _PCB.read_text(encoding="utf-8")
    assert "'place_arcs':              Result := PCB_PlaceArcs(Params, RequestId);" in text
    assert "'place_pads':              Result := PCB_PlacePads(Params, RequestId);" in text


# ------------------------------------------------------------- the tools

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, tool, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools[tool](**kw))
    return out, bridge.sent


def test_arcs_go_as_rows_with_the_shared_values_sent_once(monkeypatch):
    _, sent = _call(monkeypatch, "pcb_place_arcs", arcs=[
        {"x_center": 100, "y_center": 200.5, "radius": 20},
        {"x_center": 300, "y_center": 400, "radius": 25, "start_angle": 90,
         "end_angle": 180, "width": 6.25, "layer": "Mechanical3",
         "net": "GND", "keepout": True},
    ], width=4, layer="L1 (TOP)")
    assert sent == [("pcb.place_arcs", {
        "arcs": "100,200.5,20,0,0,,,,|300,400,25,90,180,6.25,Mechanical3,GND,1",
        "width": "4", "layer": "L1 (TOP)"})]


def test_pads_go_as_one_template_and_rows(monkeypatch):
    _, sent = _call(monkeypatch, "pcb_place_pads", pads=[
        {"x": -7.5, "y": 3.1, "name": "1"},
        {"x": 0.2, "y": -7.5, "rotation": 90, "name": "33", "net": "GND"},
    ], shape="roundrect", x_size=1.5, y_size=0.25, corner_radius=50,
        mask_expansion=0.05, paste_expansion=0, units="mm")
    assert sent == [("pcb.place_pads", {
        "pads": "-7.5,3.1,0,1,,|0.2,-7.5,90,33,,GND",
        "shape": "roundrect", "x_size": "1.5", "y_size": "0.25",
        "corner_radius": "50", "hole_size": "0",
        "mask_expansion": "0.05", "paste_expansion": "0", "units": "mm"})]


def test_a_slot_and_an_unplated_hole_are_sent_only_when_asked(monkeypatch):
    _, sent = _call(monkeypatch, "pcb_place_pads", pads=[{"x": 0, "y": 0}],
                    hole_size=40, hole_length=80, hole_rotation=90,
                    plated=False)
    params = sent[0][1]
    assert params["hole_length"] == "80" and params["hole_rotation"] == "90"
    assert params["plated"] == "false"
    _, sent = _call(monkeypatch, "pcb_place_pads", pads=[{"x": 0, "y": 0}])
    assert not {"hole_length", "plated", "mask_expansion",
                "paste_expansion"} & set(sent[0][1])


def test_a_name_holding_a_separator_is_refused(monkeypatch):
    with pytest.raises(ValueError):
        _call(monkeypatch, "pcb_place_pads", pads=[{"x": 0, "y": 0, "name": "A,1"}])
    with pytest.raises(ValueError):
        _call(monkeypatch, "pcb_place_arcs",
              arcs=[{"x_center": 0, "y_center": 0, "radius": 5, "net": "A|B"}])


def test_pad_sizes_default_to_60_mil_in_the_callers_units(monkeypatch):
    _, sent = _call(monkeypatch, "pcb_place_pads", pads=[{"x": 0, "y": 0}],
                    units="mm")
    assert sent[0][1]["x_size"] == sent[0][1]["y_size"] == "1.524"


def test_the_list_tools_take_no_second_list():
    assert {"pcb_place_arcs", "pcb_place_pads"} <= bulk.LIST_NATIVE
