# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb.layer_view acts on the library's own board when a PcbLib is in front.

It used to take its board from GetPCBBoardAnywhere, which with a PcbLib
focused either refuses (several boards open) or opens the one board of
the open projects and answers from it. Reading the view of a footprint
then reported another document's layers, and a write hid layers or moved
the current layer of a board the user was not looking at. A library's
editing surface is its own board, PcbLib.Board; current layer and layer
visibility were read and set on it live (AD 25.8.1).
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _fn(name):
    return load(_PCB)[name]


def test_a_pcblib_in_front_gives_its_own_board():
    body = _fn("PCB_LayerView")
    lib = body.index("If LvPcbLibInFront(0) Then")
    get = body.index("PcbLib := PCBServer.GetCurrentPCBLibrary;", lib)
    own = body.index("Board := PcbLib.Board;", get)
    assert lib < get < own


def test_a_pcblib_in_front_never_falls_back_to_another_board():
    """The board lookup is only the ELSE of the library test, so a library
    that cannot be had gives no board: no board is better than the wrong
    one."""
    body = _fn("PCB_LayerView")
    assert body.count("GetPCBBoardAnywhere") == 1
    lib = body.index("If LvPcbLibInFront(0) Then")
    other = body.index("Else", lib)
    anywhere = body.index("Board := GetPCBBoardAnywhere(0);")
    assert lib < body.index("Board := PcbLib.Board;") < other < anywhere
    # nothing between the Else and the lookup: it is that branch's only statement
    assert body[other + len("Else"):anywhere].strip() == ""
    # and the refusal follows straight on, for both branches
    assert body.index("If Board = Nil Then", anywhere) > anywhere


def test_the_kind_comes_from_the_focused_document():
    """PCBServer is not named before a PcbLib is known to be in front:
    with no PCB server loaded, naming it raises an uncatchable modal."""
    body = _fn("LvPcbLibInFront")
    kind = body.index("Kind := UpperCase(Doc.DM_DocumentKind);")
    assert "Doc := Workspace.DM_FocusedDocument;" in body[:kind]
    assert "Result := Kind = 'PCBLIB';" in body
    assert "PCBServer" not in body
    handler = _fn("PCB_LayerView")
    assert handler.index("If LvPcbLibInFront(0) Then") < handler.index("PCBServer.")


def test_a_change_refreshes_the_library_view():
    body = _fn("PCB_LayerView")
    changed = body.index("If Changed Then")
    tabs = body.index("Try Board.ViewManager_UpdateLayerTabs; Except End;", changed)
    refresh = body.index("Try PcbLib.RefreshView; Except End;", tabs)
    guard = body.index("If PcbLib <> Nil Then", tabs)
    assert changed < tabs < guard < refresh
    # inside the change block: a plain read leaves the library view alone
    full = body.index("Try Board.ViewManager_FullUpdate; Except End;", changed)
    after = full + len("Try Board.ViewManager_FullUpdate; Except End;")
    assert re.search(r"\n\s*End;", body[after:refresh]) is None
