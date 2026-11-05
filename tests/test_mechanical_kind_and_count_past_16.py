# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""set_mech_layer_kind and the per-layer primitive count reach Mechanical17 and up.

pcb_set_mech_layer_kind refused every layer past Mechanical16 as "not a
mechanical layer" (its test compared the id with eMechanical1..16, and
the ids of the higher layers lie past that), and the loop that clears a
kind from the layer holding it walked only Mechanical1-16.
pcb_get_mech_layer_names reported 0 primitives on every layer past
Mechanical16: the counter filtered by a legacy MkSet, which cannot hold
those layers. Same cause as the layout model's read (#149).
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _pcb(name):
    return load(_ALTIUM / "PCB.pas")[name]


def test_the_counter_walks_a_high_mechanical_layer_through_the_v7_set():
    counter = _pcb("PCB_CountPrimitivesOnLayer")
    wide = counter.index("Wide := ParseMechLayerNumber(LayerDisplayName(Lyr)) > 16;")
    own = counter.index("OneLayer := LayerSet.CreateLayerSet;", wide)
    include = counter.index("OneLayer.Include(Lyr);", own)
    used = counter.index("Iterator.AddFilter_IPCB_LayerSet(OneLayer);", include)
    legacy = counter.index("Iterator.AddFilter_LayerSet(MkSet(Lyr));", used)
    assert legacy < counter.index("Iterator.FirstPCBObject")
    assert "OneLayer : IPCB_LayerSet;" in counter


def test_the_counter_counts_only_the_asked_layer():
    counter = _pcb("PCB_CountPrimitivesOnLayer")
    walk = counter.index("While Prim <> Nil Do")
    guard = counter.index("If (Not Wide) Or (Prim.Layer = Lyr) Then Result := Result + 1;", walk)
    assert guard < counter.index("Prim := Iterator.NextPCBObject;", walk)


def test_a_high_mechanical_layer_takes_a_kind():
    setter = _pcb("PCB_SetMechLayerKind")
    assert "eMechanical16" not in setter
    refuse = setter.index("'NOT_MECHANICAL'")
    assert setter.index("If Not LvIsMech(TargetLayer) Then") < refuse


def test_the_kind_is_cleared_from_every_mechanical_layer():
    setter = _pcb("PCB_SetMechLayerKind")
    loop = setter.index("For N := 1 To 1024 Do")
    layer = setter.index("Lyr := MechLayerFromNumber(N);", loop)
    stop = setter.index("If Lyr = eNoLayer Then Break;", layer)
    assert stop < setter.index("OtherObj := LayerStack.LayerObject_V7[Lyr];", stop)
    assert "EscapeJsonString(LayerDisplayName(Lyr))" in setter
    assert "GetLayerString(Lyr)" not in setter


def test_the_reply_names_a_high_mechanical_layer():
    # GetLayerString answers 'Unknown' past Mechanical16 (measured on
    # Mechanical33: the reply said "layer":"Unknown")
    setter = _pcb("PCB_SetMechLayerKind")
    assert '"layer":"\' + EscapeJsonString(LayerDisplayName(TargetLayer))' in setter
    assert "GetLayerString(" not in setter
