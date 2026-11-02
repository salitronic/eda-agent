# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Mechanical17 and up are reached, and named, by every handler.

Altium 25 has up to 1024 mechanical layers. The legacy layer range, the
legacy AllLayers set and GetLayerString stop at Mechanical16, so on a
board with higher mechanical layers (measured on Mechanical21 and 33):
get_polygons listed 113 of 126 pours, and get_board_statistics,
calc_polygon_area, repour_polygons, pours_at, pour_outline and
pour_copper could not see a pour there; delete_object layer= found
nothing; get_layer_display and get_pcb_geometry left the layers out; a
user name such as "FluidGon" resolved to no layer; and every place_*
reply, set_layer_visibility and copy_designators_to_mech named the layer
'Unknown'. #149 and #155 fixed the layout model and the generic walk one
at a time; this closes the pattern and keeps it closed.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
# Altium_MCP.pas is the built bundle (build.py), not a source module: it
# repeats Utils and SelfTest, whose legacy-range calls are kept on purpose
_UNITS = sorted(u for u in _ALTIUM.glob("*.pas") if u.name != "Altium_MCP.pas")
# a GetLayerString kept on purpose: the legacy-range test ('Unknown' = the
# legacy table has no name for it), compared on the same line
_LEGACY_TEST = re.compile(r"GetLayerString\([^()]*(\([^()]*\))?[^()]*\)\s*(=|<>)\s*'Unknown'")


def _text(unit):
    return unit.read_text(encoding="latin-1")


def test_no_walk_filters_by_the_legacy_all_layers_set():
    hits = [f"{u.name}:{n}" for u in _UNITS
            for n, line in enumerate(_text(u).splitlines(), 1)
            if re.search(r"AddFilter_LayerSet\(\s*AllLayers\s*\)", line)]
    assert not hits, "walk with AddFilter_IPCB_LayerSet(LayerSet.AllLayers): " + ", ".join(hits)


def test_no_loop_stops_at_mechanical16():
    hits = [f"{u.name}:{n}" for u in _UNITS
            for n, line in enumerate(_text(u).splitlines(), 1)
            if re.search(r"To\s+eMechanical16\b", line)]
    assert not hits, "loop with MechLayerFromNumber(1..1024): " + ", ".join(hits)


def test_layers_are_named_with_layer_display_name():
    hits = []
    for u in _UNITS:
        if u.name in ("Utils.pas", "SelfTest.pas"):
            continue
        for n, line in enumerate(_text(u).splitlines(), 1):
            if "GetLayerString(" in line and not _LEGACY_TEST.search(line):
                hits.append(f"{u.name}:{n}")
    assert not hits, "name a layer with LayerDisplayName: " + ", ".join(hits)


def test_a_user_name_past_mechanical16_resolves():
    resolver = load(_ALTIUM / "Utils.pas")["ResolveLayerIdInStack"]
    loop = resolver.index("For MechN := 1 To 1024 Do")
    layer = resolver.index("Lyr := MechLayerFromNumber(MechN);", loop)
    stop = resolver.index("If Lyr = eNoLayer Then Break;", layer)
    assert stop < resolver.index("Obj := LayerStack.LayerObject_V7[Lyr];", stop)


def _lists_high_mechanical_layers(body, counter):
    loop = body.index(f"For {counter} := 17 To 1024 Do")
    layer = body.index(f"Lyr := MechLayerFromNumber({counter});", loop)
    stop = body.index("If Lyr = eNoLayer Then Break;", layer)
    assert body.index("MechLayerEnabled(Board, Lyr)", stop) > stop


def test_get_layer_display_lists_enabled_high_mechanical_layers():
    body = load(_ALTIUM / "PCB.pas")["PCB_GetLayerDisplay"]
    _lists_high_mechanical_layers(body, "N")
    assert "Ids.Add(IntToStr(Lyr))" in body
    assert "MechLayerEnabled" in load(_ALTIUM / "PCB.pas")


def test_get_pcb_geometry_lists_enabled_high_mechanical_layers():
    body = load(_ALTIUM / "Generic.pas")["Gen_GetPcbGeometry"]
    _lists_high_mechanical_layers(body, "MechN")
    assert "EscapeJsonString(LayerDisplayName(Lyr)) + '\":{\"color\":'" in body


def test_delete_object_finds_an_object_past_mechanical16():
    body = load(_ALTIUM / "PCB.pas")["PCB_DeleteObject"]
    wide = body.index("Else If ParseMechLayerNumber(LayerDisplayName(TargetLayer)) > 16 Then")
    own = body.index("OneLayer := LayerSet.CreateLayerSet;", wide)
    include = body.index("OneLayer.Include(TargetLayer);", own)
    assert body.index("Iterator.AddFilter_IPCB_LayerSet(OneLayer);", include) < body.index(
        "Iterator.FirstPCBObject")
