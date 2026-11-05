# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Anything drawn on the Keep-Out layer is a keep-out to the router.

Measured on a demo board: a ring drawn round a mounting hole on the
Keep-Out layer with pcb_place_arc carried no keep-out flag, so the reader
took it for copper on a layer the router does not route and a signal
ran straight through it.
"""
from __future__ import annotations

from eda_agent.layout.model import LayoutBoard
from eda_agent.layout.read_altium import _copper

_C = 10000


def test_an_unflagged_arc_and_track_on_the_keepout_layer_are_keepouts():
    board = LayoutBoard(name="b")
    _copper([
        {"k": "arc", "layer": "KeepOutLayer", "v": [1000 * _C, 1000 * _C, 145 * _C, 0, 360, 10 * _C]},
        {"k": "track", "layer": "KeepOutLayer", "v": [0, 0, 100 * _C, 0, 10 * _C]},
        {"k": "track", "layer": "TopLayer", "v": [0, 0, 100 * _C, 0, 10 * _C], "net": "A"},
    ], board)
    assert board.arcs[0].keepout is True
    assert [t.keepout for t in board.tracks] == [True, False]
