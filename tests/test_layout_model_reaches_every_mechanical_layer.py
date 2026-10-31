# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The layout model's copper read reaches Mechanical17 and up.

The read walked the legacy AllLayers set, which stops at Mechanical16, so a
polygon on Mechanical17 or higher was found by generic.query_objects and
never by get_layout_model; GetLayerString named such a layer 'Unknown', in
the copper read and in place_polygon_rect's reply (issue #149). The generic
dump walk was fixed the same way before: the V7 layer set, and
LayerDisplayName for the name.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _pcb(name):
    return load(_ALTIUM / "PCB.pas")[name]


def test_the_unnarrowed_read_walks_the_v7_layer_set():
    section = _pcb("LmCopperSection")
    assert "Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);" in section
    assert "AddFilter_LayerSet(AllLayers)" not in section


def test_a_layer_the_legacy_set_cannot_hold_is_matched_by_name():
    section = _pcb("LmCopperSection")
    wide = section.index("Wide := (OnLayer <> eNoLayer) And (GetLayerString(OnLayer) = 'Unknown');")
    narrow = section.index("If (OnLayer <> eNoLayer) And Not Wide Then")
    assert wide < narrow < section.index("Iter.FirstPCBObject")
    # the match sits with the selection test, before anything is counted
    match = section.index("Wanted := LayerDisplayName(Obj.Layer) = LayerDisplayName(OnLayer);")
    assert section.index("If Wanted And Wide Then") < match < section.index("Inc(Idx);")


def test_layers_are_named_past_mechanical16():
    section = _pcb("LmCopperSection")
    assert "Common := ',\"layer\":\"' + LayerDisplayName(Obj.Layer)" in section
    placer = _pcb("PCB_PlacePolygonRect")
    assert "EscapeJsonString(LayerDisplayName(Polygon.Layer))" in placer
    assert "GetLayerString(Polygon.Layer)" not in placer
