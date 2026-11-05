# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A board primitive's Net can be written through the generic layer (#126).

Net was read-only: GetPCBProperty answered it, SetPCBProperty fell through
to "unknown". Exploding a polygon leaves its net on every region it made,
and nothing generic could take it off again. Now '' leaves the primitive on
no net, a name joins that existing net, and an unknown name is refused
rather than ignored. The net's own collection is kept in step both ways,
as BindPrimitiveToNet does, because connectivity and pours walk it.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

_COPPER = {"eTrackObject", "eArcObject", "eViaObject", "ePadObject",
           "eFillObject", "eRegionObject", "ePolyObject"}


def _generic():
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)


def _helper():
    return _generic()["PCBSetNet"].replace("\r\n", "\n")


def _net_branch():
    body = _generic()["SetPCBProperty"].replace("\r\n", "\n")
    start = body.index("Else If (PropName = 'Net') Or (PropName = 'Net.Name') Then")
    return body[start:body.index("Else If (PropName", start + 10)]


def test_an_unknown_net_is_refused_not_ignored():
    body = _helper()
    lookup = body.index("If NetObj.Name = Value Then Break;")
    refuse = body.index("If NetObj = Nil Then Exit;")
    write = body.index("Obj.Net := NetObj;")
    assert lookup < refuse < write
    assert body.index("Result := False;") < lookup
    assert body.rindex("Result := True;") > write
    branch = _net_branch()
    assert "If Not PCBSetNet(Obj, Value) Then Refused := True;" in branch


def test_an_empty_name_clears_the_net_without_a_lookup():
    body = _helper()
    assert "NetObj := Nil;" in body[:body.index("If Value <> '' Then")]
    guarded = body[body.index("If Value <> '' Then"):body.index("OldNet := Obj.Net;")]
    assert "BoardIterator_Create" in guarded, "the lookup runs only for a name"
    assert "If NetObj = Nil Then Exit;" in guarded, "a nil net refuses only a name"


def test_membership_is_kept_both_ways():
    body = _helper()
    remove = body.index("OldNet.RemovePCBObject(Obj);")
    assign = body.index("Obj.Net := NetObj;")
    add = body.index("NetObj.AddPCBObject(Obj);")
    assert remove < assign < add
    assert "If OldNet <> Nil Then" in body[:remove]
    assert "If NetObj <> Nil Then" in body[assign:add]


def test_the_net_walk_uses_the_full_layer_set():
    body = _helper()
    assert "AddFilter_ObjectSet(MkSet(eNetObject));" in body
    assert "AddFilter_IPCB_LayerSet(LayerSet.AllLayers);" in body
    assert body.index("Board.BoardIterator_Destroy(Iter);") > body.index("Break;")


def test_only_copper_types_take_a_net_and_the_rest_are_not_matched():
    branch = _net_branch()
    write = branch.index("PCBSetNet(Obj, Value)")
    types = set(re.findall(r"Oid = (e\w+)", branch[:write]))
    assert types == _COPPER
    assert branch.count("Oid =") == len(_COPPER)
    assert "Else Matched := False;" in branch[write:]


def test_both_spellings_are_known_properties():
    known = _generic()["IsKnownPCBProperty"]
    assert "(PropName = 'Net')" in known
    assert "(PropName = 'Net.Name')" in known


def test_the_helper_is_defined_before_the_setter_that_calls_it():
    src = (_ALTIUM / "PCBGeneric.pas").read_text(encoding="ascii")
    assert src.index("Function PCBSetNet(") < src.index("Function SetPCBProperty(")
