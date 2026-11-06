# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb.delete_invalid_objects removes FREE degenerate primitives only.

Its walk is eProcessAll, so it also hands out the primitives of placed
components, polygon pours and dimensions. A zero-length track in a
footprint is a round dot (often a pin-1 mark); deleting it made the part
differ from its library footprint, live on AD 25.8.1. A primitive with an
owner is now never a candidate, and one whose owner cannot be read is
skipped as well.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_DeleteInvalidObjects"]


def test_the_owner_is_read_for_every_object_before_it_is_judged():
    body = _body()
    first = body.index("Obj := Iter.FirstPCBObject;")
    default = body.index("Owned := True;", first)
    read = body.index(
        "Try Owned := Obj.InComponent Or Obj.InPolygon Or Obj.InDimension; Except End;",
        default)
    track = body.index("eTrackObject) Then", read)
    assert first < default < read < track


def test_neither_kind_is_a_candidate_when_owned():
    body = _body()
    assert "If (Not Owned) And (Obj.ObjectId = eTrackObject) Then" in body
    assert "Else If (Not Owned) And (Obj.ObjectId = eRegionObject) Then" in body
    # no other route to a victim
    assert body.count("Victim := Obj;") == 2


def test_the_walk_still_restarts_after_each_removal():
    """The guard does not change the find-one-remove-restart shape."""
    body = _body()
    loop = body.index("While FoundOne And (Guard < 100000) Do")
    create = body.index("Iter := Board.BoardIterator_Create;", loop)
    destroy = body.index("Board.BoardIterator_Destroy(Iter);", create)
    remove = body.index("Board.RemovePCBObject(Victim);", destroy)
    assert loop < create < destroy < remove
