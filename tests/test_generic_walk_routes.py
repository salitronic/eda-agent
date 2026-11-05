# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The generic PCB query, modify and delete look only where a match can be.

A filter that names a net, a component or a position still walked every
object of its type on the board and asked the filter of each one. Three
routes hand the filter only the objects that could match:

- Net=<name>: the net's group, plus the copper of every pour on that net
  (poured copper is not in the net's group, while the Net getter answers
  the pour's net for it);
- Component=<designator>: every component of that name, its group (not for
  texts: a component's designator and comment are not in its group, while
  the Component getter names their component);
- a Bounds* / X1 / X2 / Y1 / Y2 value: a spatial search round that point,
  on every layer (narrowed by layer it left a layer's SMD pads out).

MatchesFilterPCB still decides every object any route hands out, so a
route can only leave out what the filter rejects. MEASURED on an 8-layer
board, each routed query equal to the full read it replaces (84 cases):
a net's tracks 0.5-1.7 s, a component's 0.3-2.2 s, a spatial lookup
0.04-0.27 s, where the walk took seconds per call.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic(name):
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)[name]


def _routes():
    return _generic("CollectPCBMatches")


def _route(body, start_marker, end_marker="Exit;"):
    start = body.index(start_marker)
    return body[start:body.index(end_marker, start)]


def test_each_route_ends_the_walk_and_the_board_walk_is_last():
    body = _routes()
    net = body.index("FilterExactValue(FilterStr, 'Net', Wanted)")
    comp = body.index("FilterExactValue(FilterStr, 'Component', Wanted)")
    spatial = body.index("Spatial := Board.SpatialIterator_Create;")
    walk = body.rindex("Iterator := Board.BoardIterator_Create;")
    assert net < comp < spatial < walk
    assert body.count("Exit;") == 3


def test_a_route_is_only_taken_for_an_exact_condition():
    body = _generic("FilterExactValue")
    assert "If (EqPos > 1) And (FilterOpText(Condition) = '=') Then" in body
    assert "If Copy(Condition, 1, EqPos - 1) = Prop Then" in body


def test_every_object_a_route_hands_out_is_checked_by_the_filter():
    keep = _generic("PCBKeepMatch")
    assert keep.index("If Not MatchesFilterPCB(Obj, FilterStr) Then Exit;") \
        < keep.index("Matches.Add(Obj);")
    body = _routes()
    for marker in ("FilterExactValue(FilterStr, 'Net', Wanted)",
                   "FilterExactValue(FilterStr, 'Component', Wanted)",
                   "Spatial := Board.SpatialIterator_Create;"):
        route = _route(body, marker)
        assert "Matches.Add(" not in route
        assert "PCBKeepMatch(Obj, FilterStr, Matches, Seen);" in route


def test_an_object_met_twice_is_kept_once():
    keep = _generic("PCBKeepMatch")
    assert "Key := IntToStr(Obj.I_ObjectAddress);" in keep
    assert "If Seen.IndexOf(Key) >= 0 Then Exit;" in keep
    assert "Seen.Sorted := True;" in _routes()


def test_the_net_route_takes_the_net_and_the_pours_on_it():
    route = _route(_routes(), "FilterExactValue(FilterStr, 'Net', Wanted)")
    assert "FilterExactValue(FilterStr, 'Net.Name', Wanted)" in route
    assert "And (ObjTypeInt <> eTextObject)" in route
    assert "If Net.Name = Wanted Then" in route
    assert "Group := Net.GroupIterator_Create;" in route
    assert "Group.AddFilter_AllLayers;" in route
    # Poured copper: the children of every pour on that net.
    assert "Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));" in route
    assert "If Value = Wanted Then" in route
    assert "Group := Owner.GroupIterator_Create;" in route


def test_the_component_route_leaves_texts_to_the_walk():
    route = _route(_routes(), "FilterExactValue(FilterStr, 'Component', Wanted)")
    assert "And (ObjTypeInt <> eComponentObject) And (ObjTypeInt <> eTextObject)" in route
    assert "If Comp.Name.Text = Wanted Then" in route
    assert "Group := Comp.GroupIterator_Create;" in route


def test_the_spatial_route_is_primitives_only_on_every_layer():
    body = _routes()
    assert re.search(r"HasX := False; HasY := False; X := 0; Y := 0;\s+"
                     r"If IsPrimitive Then", body)
    # Exactly these: a spatial iterator hands out no group object
    # (component, polygon, dimension).
    primitive = body[body.index("IsPrimitive := "):]
    primitive = primitive[:primitive.index(";")]
    assert set(re.findall(r"\(ObjTypeInt = (\w+)\)", primitive)) == {
        "eTrackObject", "eArcObject", "eViaObject", "ePadObject",
        "eFillObject", "eRegionObject", "eTextObject"}
    route = _route(body, "Spatial := Board.SpatialIterator_Create;")
    assert "Spatial.AddFilter_ObjectSet(MkSet(ObjTypeInt));" in route
    assert "Spatial.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);" in route
    assert "AddFilter_LayerSet(MkSet" not in route
    for prop in ("BoundsLeft", "BoundsRight", "X1", "X2"):
        assert f"FilterExactValue(FilterStr, '{prop}', Value)" in body
    for prop in ("BoundsBottom", "BoundsTop", "Y1", "Y2"):
        assert f"FilterExactValue(FilterStr, '{prop}', Value)" in body
    assert "Spatial.AddFilter_Area(X - MilsToCoordF(1), Y - MilsToCoordF(1)," in route


def test_every_walk_stops_at_the_limit_and_query_counts_what_is_left():
    body = _routes()
    loops = re.findall(r"While \(\w+ <> Nil\) And PCBStillCollecting\(Matches, Limit\) Do",
                       body)
    # The nets, a net's group, the pours, a pour's group, the components,
    # a component's group, the spatial search and the board walk.
    assert len(loops) == 8
    assert "Result := (Limit <= 0) Or (Matches.Count < Limit);" in \
        _generic("PCBStillCollecting")
    query = _generic("ProcessPCBBoardObjects")
    assert "If Limit > 0 Then Remaining := Limit - TotalMatched;" in query
    assert "CollectPCBMatches(Board, ObjTypeInt, FilterStr, Remaining, Victims);" in query
    assert "CollectPCBMatches(Board, ObjTypeInt, FilterStr, 0, Victims);" in query
    assert "CollectPCBMatches(Board, ObjTypeInt, FilterStr, Limit, Victims);" in query


def test_every_iterator_is_destroyed():
    body = _routes()
    assert body.count("Iterator := Board.BoardIterator_Create;") \
        == body.count("Board.BoardIterator_Destroy(Iterator);") == 4
    assert body.count("Group := ") == 3
    for owner in ("Net", "Owner", "Comp"):
        assert f"{owner}.GroupIterator_Destroy(Group);" in body
    assert "Board.SpatialIterator_Destroy(Spatial);" in body
