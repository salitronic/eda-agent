# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The board route reads and writes a via's and pad's mask, tenting and stack.

On a PcbDoc, generic.query_objects / modify_objects knew a via's Size and
HoleSize and nothing else. These checks hold the rules the new properties
rest on: the via mask goes through the pad cache (a direct write on the via
faults the engine), the testpoint flags are read and never written, a
component's union is set on every one of its parts, and the box and a
body's Identifier are read only.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"


def _fns():
    return load(_GENERIC, minimum=20)


def test_the_via_mask_goes_through_the_pad_cache_only():
    fns = _fns()
    for name in ("PCBGetMask", "PCBSetMask", "SetPCBProperty", "GetPCBProperty"):
        assert not re.search(r"\bVia\.SolderMask\w*\s*:=", fns[name]), name
    setter = fns["PCBSetMask"]
    assert "Cache := AsPrim.GetState_Cache;" in setter
    assert "AsPrim.SetState_Cache := Cache;" in setter
    assert setter.index("Cache := AsPrim.GetState_Cache;") < \
        setter.index("AsPrim.SetState_Cache := Cache;")


def test_the_manual_flag_maps_to_the_cache_state_both_ways():
    setter = _fns()["PCBSetMask"]
    assert "Cache.SolderMaskExpansionValid := eCacheManual" in setter
    assert "Cache.SolderMaskExpansionValid := eCacheValid" in setter
    getter = _fns()["PCBGetMask"]
    assert "Cache.SolderMaskExpansionValid = eCacheManual" in getter


def test_the_testpoint_flags_the_box_and_identifier_are_never_written():
    setter = _fns()["SetPCBProperty"]
    for prop in ("IsTestpoint_Top", "IsTestpoint_Bottom", "IsAssyTestpoint_Top",
                 "IsAssyTestpoint_Bottom", "BoundsLeft", "BoundsTop",
                 "Identifier"):
        assert f"'{prop}'" not in setter, prop
    assert not re.search(r"\.Is(?:Assy)?Testpoint_\w+\s*:=", setter)


def test_tenting_is_written_on_vias_and_refused_on_anything_else():
    setter = _fns()["SetPCBProperty"]
    for side in ("Top", "Bottom"):
        start = setter.index(f"Else If PropName = 'IsTenting_{side}' Then")
        branch = setter[start:setter.index("Else If", start + 10)]
        assert f"Via.IsTenting_{side} := StrToBool(Value);" in branch
        assert "Else Matched := False;" in setter[start:start + 300]


def test_a_component_union_reaches_every_part_and_a_negative_one_is_refused():
    fns = _fns()
    union = fns["PCBSetComponentUnion"]
    assert "Comp.SetState_UnionIndex := UIndex" in union
    assert "GIter := Comp.GroupIterator_Create;" in union
    assert "Prim.SetState_UnionIndex := UIndex" in union
    assert "Comp.GroupIterator_Destroy(GIter);" in union
    assert "PCBSetComponentUnion(Comp, UIndex);" in fns["PCBSetUnion"]
    setter = fns["SetPCBProperty"]
    start = setter.index("Else If PropName = 'UnionIndex' Then")
    branch = setter[start:start + 300]
    assert "(StrToFloatDef(Value, -1) < 0) Then" in branch
    assert "Refused := True" in branch


def test_the_stack_names_read_and_write_alike_and_an_unknown_one_is_refused():
    fns = _fns()
    names = re.findall(r"Result := '([a-z-]+)'", fns["PCBStackName"])
    assert set(names) >= {"simple", "top-middle-bottom", "full-stack"}
    setter = fns["PCBSetStack"]
    for name in ("simple", "top-middle-bottom", "full-stack"):
        assert f"Value = '{name}'" in setter
    assert "Else Begin Result := -1; Exit; End;" in setter
    assert "Else If PosVal < 0 Then Refused := True;" in fns["SetPCBProperty"]


def test_every_new_property_is_known_and_listed():
    fns = _fns()
    known = fns["IsKnownPCBProperty"]
    listed = fns["KnownPCBPropertyList"]
    new = ("TopShapeName BotXSize BotYSize Plated HoleType HoleWidth HoleRotation "
           "SizeTop SizeMid SizeBottom SolderMaskExpansion SolderMaskBottomExpansion "
           "SolderMaskSeparate SolderMaskManual SolderMaskFromHoleEdge IsTenting_Top "
           "IsTenting_Bottom IsTestpoint_Top IsTestpoint_Bottom IsAssyTestpoint_Top "
           "IsAssyTestpoint_Bottom BoundsLeft BoundsBottom BoundsRight BoundsTop "
           "UnionIndex Identifier").split()
    assert len(new) == 27
    for prop in new:
        assert f"(PropName = '{prop}')" in known, prop
        assert re.search(rf"\b{prop}\b", listed), prop
