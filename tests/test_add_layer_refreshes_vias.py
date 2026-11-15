# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A layer added by pcb.add_layer is seen by the vias already on the board.

Measured on AD 25.8.1: after a mid layer is inserted, a pour on it with
RemoveDead set loses all its copper as dead although through vias of its
net cross the layer; with RemoveDead off it pours and connects to them.
Only reopening the board, or taking each via off the board and adding it
back, makes the vias count on the new layer. A modify bracket, a size put
back in the same bracket, the via's own layer span written back and the
board registration message alone all left it as it was.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _fns():
    return load(_PCB)


def test_add_layer_refreshes_the_vias_after_the_insert():
    body = _fns()["PCB_AddLayer"]
    refresh = body.index("RefreshBoardVias(Board")
    assert body.index("InsertLayer(") < refresh
    assert body.index("InsertInStackBelow(") < refresh
    # inside the edit, so the vias are re-added before PostProcess
    assert refresh < body.index("PcbEndEdit(")


def test_add_layer_reports_the_vias_it_refreshed():
    body = _fns()["PCB_AddLayer"]
    assert '"vias_refreshed":' in body
    assert '"vias_in_components":' in body


def test_vias_are_taken_off_and_added_back():
    body = _fns()["RefreshBoardVias"]
    remove = body.index("Board.RemovePCBObject(Prim)")
    add = body.index("Board.AddPCBObject(Prim)")
    assert remove < add < body.index("PCBM_BoardRegisteration", add)


def test_the_walk_is_filtered_to_vias_and_finished_before_any_change():
    body = _fns()["RefreshBoardVias"]
    assert "AddFilter_ObjectSet(MkSet(eViaObject))" in body
    destroy = body.index("BoardIterator_Destroy(Iter)")
    assert destroy < body.index("RemovePCBObject(")


def test_a_component_via_is_left_alone_and_counted():
    body = _fns()["RefreshBoardVias"]
    guard = body.index("If Prim.InComponent Then Inc(Skipped)")
    assert guard < body.index("Vias.Add(Prim)")


def test_the_list_of_altium_references_is_not_freed():
    body = _fns()["RefreshBoardVias"]
    assert "Vias.Free" not in body
