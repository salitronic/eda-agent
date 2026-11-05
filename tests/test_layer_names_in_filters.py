# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A layer in a generic PCB filter means a layer, whatever it is called.

The Layer getter answers the internal name (TopLayer, MidLayer3,
Mechanical21) and the filter compared that text with the value given,
exactly. A user's layer name ("FluidGon", "L2 (GND)") or a case variant
matched nothing and reported success; ``Layer!=<user name>`` matched every
object, the objects on that layer included, so a delete meant to spare a
layer removed it as well; ``Layer~Fluid*`` never met a user name; and a
value naming no layer was never refused. The filter's layer conditions are
now resolved once per call through ResolveLayerId, the same resolver a
``set=`` layer goes through, and a name that is no layer is refused.

ResolveLayerId also takes an internal name in any case (``midlayer3``,
``multilayer``): GetLayerFromString is case-sensitive and missed them.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic(name):
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)[name]


def _utils(name):
    return load(_ALTIUM / "Utils.pas")[name]


def test_equal_and_not_equal_layers_are_resolved_to_the_internal_name():
    body = _generic("CanonicalLayerFilter")
    assert "If ((PropName = 'Layer') Or (PropName = 'Layer1') Or (PropName = 'Layer2'))" in body
    assert "If (Op = '=') Or (Op = '!=') Then" in body
    assert "L := ResolveLayerId(Board, Value);" in body
    assert "Condition := PropName + Op + LayerDisplayName(L);" in body


def test_a_value_that_names_no_layer_is_handed_back_and_left_alone():
    body = _generic("CanonicalLayerFilter")
    assert "If L = eNoLayer Then" in body
    assert "If Bad = '' Then Bad := Value;" in body
    assert body.index("Bad := '';") < body.index("While Remaining <> '' Do")


def test_a_wildcard_layer_becomes_the_set_of_layers_it_matches():
    body = _generic("CanonicalLayerFilter")
    assert "Value := LayersMatchingPattern(Board, Value);" in body
    assert "If Op = '~' Then Op := '=' Else Op := '!=';" in body
    assert "Condition := PropName + '.In' + Op + Value;" in body


def test_a_wildcard_that_names_one_layer_becomes_that_layer():
    # Then NarrowPCBWalk narrows it as it does an exact name.
    body = _generic("CanonicalLayerFilter")
    assert "If (Value <> '') And (Pos(';', Value) = Length(Value)) Then" in body
    assert "Condition := PropName + Op + Copy(Value, 1, Length(Value) - 1)" in body


def test_a_pattern_meets_the_internal_and_the_user_name_of_every_layer():
    body = _generic("LayersMatchingPattern")
    assert "If FilterWildMatch(Internal, Pattern)" in body
    assert "Or ((User <> '') And FilterWildMatch(User, Pattern)) Then" in body
    assert "Try Obj := Stack.LayerObject_V7[L]; Except Obj := Nil; End;" in body
    # The legacy ids, then Mechanical17 to 1024, named as the getter does.
    assert "For I := 1 To 1104 Do" in body
    assert "L := MechLayerFromNumber(I - 80);" in body
    assert "Internal := 'Mechanical' + IntToStr(I - 80);" in body
    assert "Internal := LayerDisplayName(L);" in body


def test_the_filter_compares_a_layer_set_by_membership():
    body = _generic("MatchesFilterPCB")
    assert "If (PropName = 'Layer.In') Or (PropName = 'Layer1.In')" in body
    assert "Actual := GetPCBProperty(Obj, Copy(PropName, 1, Length(PropName) - 3));" in body
    assert "Pos(';' + Actual + ';', ';' + Expected + ';') > 0) Then Continue;" in body
    assert body.index("'Layer.In'") < body.index("Actual := GetPCBProperty(Obj, PropName);")


def test_every_walk_route_sees_internal_names():
    body = _generic("CollectPCBMatches")
    first = body.index("FilterStr := CanonicalLayerFilter(Board, FilterStr, BadLayer);")
    assert first < body.index("Seen := TStringList.Create;")
    # NarrowPCBWalk narrows on the internal name, so a user name narrows too.
    assert first < body.index("NarrowPCBWalk(Board, Iterator, FilterStr);")


def test_an_unknown_filter_layer_is_refused_before_anything_is_matched():
    body = _generic("ProcessActivePCBDoc")
    canon = body.index("Canon := CanonicalLayerFilter(Board, FilterStr, BadLayer);")
    refuse = body.index("'Unknown layer name in the filter: ' + BadLayer")
    assert canon < refuse < body.index("ProcessPCBBoardObjects(Board, ObjTypeInt,")
    assert "Canon, PropsStr, SetStr, Mode, TotalMatched, Limit, IndexJson);" in body


def test_a_case_variant_of_an_internal_name_resolves_before_user_names():
    body = _utils("ResolveLayerIdInStack")
    canonical = body.index("Candidate := CanonicalLayerByName(Wanted);")
    stack_walk = body.index("Try Obj := LayerStack.FirstLayer; Except Obj := Nil; End;")
    assert canonical < stack_walk


def test_internal_names_resolve_in_any_case():
    body = _utils("CanonicalLayerByName")
    assert "If UpperCase(GetLayerString(I)) = Wanted Then" in body
    assert "For I := 1 To 80 Do" in body
    # GetLayerString says 'Unknown' for an unnamed id: never a match.
    assert "If (Wanted = '') Or (Wanted = 'UNKNOWN') Then Exit;" in body


def test_a_set_of_layers_narrows_the_walk_to_those_layers():
    body = _generic("NarrowPCBWalk")
    assert "Else If (PropName = 'Layer.In') And (Not HaveSet) And (Expected <> '') Then" in body
    assert "If (Got <> eNoLayer) And (LayerDisplayName(Got) = Name) Then" in body
    assert "Several.Include(Got);" in body
    # A name that does not round-trip gives up the narrowing altogether.
    assert "InSet := 0;\n                        Break;" in body.replace("\r\n", "\n")
    assert "If (OnLayer = eNoLayer) And (InSet > 0) Then" in body
    assert "Iter.AddFilter_IPCB_LayerSet(Several)" in body
