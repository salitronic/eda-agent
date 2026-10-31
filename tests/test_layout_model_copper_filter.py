# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The layout model's copper read can be narrowed to kinds, a layer and the
selection.

get_layout_model section=copper is the only reader of a polygon's
segments (arcs included), and it walked every track, arc, via, region,
fill and polygon of the board. A caller that wanted a few pours read
everything: on a board of 10,500 copper objects Altium handed the
polygons out LAST, so 140 outlines cost about 45 seconds. ``kinds``,
``layer`` and ``selected`` narrow the iterator itself. Without them the
read is the one it was: one pass, every kind, the same order and paging.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_KINDS = {"track": "eTrackObject", "arc": "eArcObject", "via": "eViaObject",
          "region": "eRegionObject", "fill": "eFillObject", "polygon": "ePolyObject"}


def _pcb(name):
    return load(_ALTIUM / "PCB.pas")[name]


def test_each_kind_filters_the_iterator_to_its_own_object_type():
    body = _pcb("LmCopperSection")
    for kind, obj in _KINDS.items():
        assert re.search(r"If Kind = '%s' Then Iter\.AddFilter_ObjectSet\(MkSet\(%s\)\)"
                         % (kind, obj), body), kind


def test_no_kinds_is_one_pass_over_every_kind_as_before():
    section = _pcb("LmCopperSection")
    chain = section[section.index("Kind := Trim(Passes[P]);"):
                    section.index("Wide := (OnLayer <> eNoLayer)")]
    fallback = chain[chain.rindex("Else"):]
    for obj in _KINDS.values():
        assert obj in fallback
    assert "If Passes.Count = 0 Then Passes.Add('all');" in section


def test_the_kinds_accepted_are_the_kinds_filtered():
    known = _pcb("LmCopperKindKnown")
    assert set(re.findall(r"Kind = '(\w+)'", known)) == set(_KINDS)


def test_a_layer_narrows_the_iterator_and_none_keeps_every_layer():
    section = _pcb("LmCopperSection")
    gate = section.index("If (OnLayer <> eNoLayer) And Not Wide Then")
    one = section.index("Iter.AddFilter_LayerSet(MkSet(OnLayer))")
    every = section.index("Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);")
    assert gate < one < every < section.index("Iter.FirstPCBObject")


def test_selected_only_skips_and_does_not_count_an_unselected_object():
    section = _pcb("LmCopperSection")
    gate = section.index("If Wanted Then")
    count = section.index("Inc(Idx);")
    step = section.index("Obj := Iter.NextPCBObject;")
    # the offset counter is inside the gate, so paging walks the selection;
    # the iterator steps outside it, so an unselected object is passed over
    assert section.index("Try Wanted := Obj.Selected;") < gate < count < step
    between = section[count:step]
    assert between.count("End;") >= 1


def test_a_bad_kind_or_layer_is_refused_before_anything_is_read():
    handler = _pcb("PCB_GetLayoutModel")
    read = handler.index("Body := LmCopperSection(")
    for gate, code in (("If Bad <> '' Then", "'BAD_KINDS'"),
                       ("If OnLayer = eNoLayer Then", "'UNKNOWN_LAYER'")):
        assert handler.index(gate) < handler.index(code) < read, code
    assert "Bad := Bad + ' ' + KindOne;" in handler
    assert "If LmCopperKindKnown(KindOne) Then" in handler
    assert "OnLayer := ResolveLayerId(Board, LayerStr);" in handler
    assert ("LowerCase(ExtractJsonValue(Params, 'selected')) = 'true');"
            in handler[read:])
