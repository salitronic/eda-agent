# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The generic PCB query, modify and delete narrow their walk by the filter.

Every call walked every object of its type on every layer and asked the
filter of each one, so ``Layer=TopLayer`` cost as much as no filter at
all: a track delete that matched nothing took 5.3 s on a board of 8719
tracks. An exact ``Layer=`` (in the name the getter answers with) now
narrows the iterator to that layer. The filter still checks every object
handed out, so a narrowing can only drop what it would reject. A call
that matched nothing no longer redraws the board or marks it modified.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic(name):
    return load(_ALTIUM / "PCBGeneric.pas", minimum=20)[name]


def _walks(body):
    """Each iterator walk: from its creation to its first object."""
    out = []
    for m in re.finditer(r"Iterator := Board\.BoardIterator_Create;", body):
        out.append(body[m.end():body.index("Iterator.FirstPCBObject", m.end())])
    return out


def test_query_modify_and_delete_all_narrow_their_walk():
    body = _generic("ProcessPCBBoardObjects")
    walks = _walks(body)
    assert len(walks) == 3
    for walk in walks:
        obj = walk.index("Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));")
        narrow = walk.index("NarrowPCBWalk(Board, Iterator, FilterStr);")
        assert obj < narrow
        assert "AddFilter_Method" not in walk
        assert "AddFilter_IPCB_LayerSet" not in walk


def test_the_filter_still_checks_every_object_the_walk_hands_out():
    body = _generic("ProcessPCBBoardObjects")
    assert body.count("MatchesFilterPCB(Obj, FilterStr)") == 3


def test_only_an_exact_condition_narrows():
    body = _generic("NarrowPCBWalk")
    assert "If (EqPos > 1) And (FilterOpText(Condition) = '=') Then" in body


def test_a_layer_narrows_only_when_the_getter_would_answer_that_name():
    body = _generic("NarrowPCBWalk")
    assert "If (PropName = 'Layer') And (OnLayer = eNoLayer) Then" in body
    assert "If (Got <> eNoLayer) And (LayerDisplayName(Got) = Expected) Then" in body
    # The getter answers Layer with LayerDisplayName, so a user name such
    # as "Fluidgon" (which the filter never matches) keeps the full walk.
    getter = _generic("GetPCBProperty")
    assert "Then Result := LayerDisplayName(Obj.Layer)" in getter


def test_one_layer_past_mechanical16_gets_a_v7_set_of_its_own():
    body = _generic("NarrowPCBWalk")
    wide = body.index("Else If GetLayerString(OnLayer) = 'Unknown' Then")
    assert body.index("OneLayer := LayerSet.CreateLayerSet;", wide) > wide
    assert "OneLayer.Include(OnLayer);" in body
    assert "Iter.AddFilter_IPCB_LayerSet(OneLayer);" in body
    assert "Iter.AddFilter_LayerSet(MkSet(OnLayer));" in body


def test_no_narrowing_walks_every_layer_and_every_object_as_before():
    body = _generic("NarrowPCBWalk")
    assert re.search(r"If OnLayer = eNoLayer Then\s+"
                     r"Iter\.AddFilter_IPCB_LayerSet\(LayerSet\.AllLayers\)", body)


def test_free_primitives_are_not_narrowed_by_the_walk():
    # eProcessFree also leaves out the copper a polygon pour owns, which
    # InComponent=false matches: measured 654 of 794 free regions.
    body = _generic("NarrowPCBWalk")
    assert "eProcessFree" not in body
    assert body.count("Iter.AddFilter_Method(eProcessAll);") == 1


def test_a_call_that_matched_nothing_does_not_redraw_or_mark_the_board():
    body = _generic("ProcessActivePCBDoc")
    guard = body.index("And (TotalMatched > 0) Then")
    assert guard < body.index("Board.GraphicalView_ZoomRedraw;") \
        < body.index("MarkDocDirtyByPath(Board.FileName);")
