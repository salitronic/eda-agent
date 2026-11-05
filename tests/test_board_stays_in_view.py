# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""With EDA_AGENT_KEEP_BOARD_IN_VIEW set, an edit is followed by Fit Board.

A demo video of a round LED board had the board half out of view for
most of its layout: the agent placed and routed without ever zooming.
"""
from __future__ import annotations

from pathlib import Path

from eda_agent.bridge import altium_bridge as ab

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _fresh(monkeypatch, on=True):
    if on:
        monkeypatch.setenv(ab.KEEP_IN_VIEW_ENV, "1")
    else:
        monkeypatch.delenv(ab.KEEP_IN_VIEW_ENV, raising=False)
    monkeypatch.setattr(ab, "_last_fit", [0.0])


def test_off_unless_asked(monkeypatch):
    _fresh(monkeypatch, on=False)
    assert not ab._wants_board_in_view("pcb.batch_move_components", now=100.0)


def test_an_edit_is_followed_by_a_fit(monkeypatch):
    _fresh(monkeypatch)
    assert ab._wants_board_in_view("pcb.batch_move_components", now=100.0)
    assert ab._wants_board_in_view("generic.place_wires", now=110.0)


def test_reads_and_other_documents_are_not(monkeypatch):
    _fresh(monkeypatch)
    for cmd in ("pcb.get_components", "generic.query_objects", "generic.zoom",
                "pcb.run_drc", "library.add_footprint_pads",
                "project.get_nets", "application.ping", "generic.switch_view"):
        assert not ab._wants_board_in_view(cmd, now=100.0), cmd


def test_at_most_once_every_two_seconds(monkeypatch):
    _fresh(monkeypatch)
    assert ab._wants_board_in_view("pcb.place_tracks", now=100.0)
    assert not ab._wants_board_in_view("pcb.place_vias", now=101.0)
    assert ab._wants_board_in_view("pcb.place_vias", now=102.5)


def test_zoom_board_is_fit_board_on_a_pcb():
    pas = _GENERIC.read_text(encoding="utf-8")
    body = pas[pas.index("Function Gen_Zoom("):]
    body = body[:body.index("\nFunction ", 10)]
    assert "(Action = 'board') And (FocusedKind = 'PCB')" in body
    assert "AddStringParameter('Target', 'Board')" in body
