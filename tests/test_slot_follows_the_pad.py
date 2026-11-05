# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A slotted pad's hole runs the way the pad does.

Altium runs a slot along the pad's X. A demo board's USB-C shell pads
(1.1 x 1.9 mm, slot 1.4 mm along Y) came out with the slot across the
pad, drilled through its sides. And its two unplated locating holes,
given no designator as a land pattern draws them, were dropped as
skipped_invalid.
"""
from __future__ import annotations

from eda_agent.tools import library as lib


def _fields(payload):
    return [dict(f.split("=", 1) for f in op.split(";")) for op in payload.split("~~")]


def test_a_slot_in_a_tall_pad_is_turned_to_run_along_it():
    payload, _ = lib._pads_payload([
        {"designator": "13", "x": -4.32, "y": 1.83, "x_size": 1.1, "y_size": 1.9,
         "shape": "round", "hole_size": 0.6, "hole_length": 1.4},
        # Measured live: a slot exactly as long as the pad is wide was left
        # across the pad by a rule that turned only a slot too long for X.
        {"designator": "13", "x": -4.32, "y": -2.39, "x_size": 1.1, "y_size": 1.6,
         "shape": "round", "hole_size": 0.6, "hole_length": 1.1}], "mm")
    assert [f["hole_rotation"] for f in _fields(payload)] == ["90", "90"]


def test_a_slot_along_the_pad_width_stays_put():
    payload, _ = lib._pads_payload([
        {"designator": "1", "x_size": 2.4, "y_size": 1.4,
         "hole_size": 0.8, "hole_length": 1.6}], "mm")
    assert _fields(payload)[0]["hole_rotation"] == "0"


def test_an_explicit_turn_is_kept():
    payload, _ = lib._pads_payload([
        {"designator": "1", "x_size": 1.1, "y_size": 1.9,
         "hole_size": 0.6, "hole_length": 1.4, "hole_rotation": 0}], "mm")
    assert _fields(payload)[0]["hole_rotation"] == "0"


def test_a_round_hole_carries_no_turn():
    payload, _ = lib._pads_payload([
        {"designator": "1", "x_size": 1.6, "y_size": 1.6, "hole_size": 1.0}], "mm")
    assert "hole_rotation" not in _fields(payload)[0]


def test_an_unplated_hole_needs_no_designator():
    payload, skipped = lib._pads_payload([
        {"designator": "", "x": -2.9, "y": 1.3, "x_size": 0.65, "y_size": 0.65,
         "hole_size": 0.65, "plated": False},
        {"designator": "", "x": 0, "y": 0}], "mm")
    assert skipped == 1
    assert len(payload.split("~~")) == 1
    assert _fields(payload)[0]["plated"] == "false"


def test_the_handler_reads_the_turn():
    import pathlib
    pas = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "altium" / "Library.pas").read_text(encoding="utf-8")
    body = pas[pas.index("Function Lib_AddFootprintPads"):]
    body = body[:body.index("\nFunction ", 10)]
    assert "GetBatchField(Op, 'hole_rotation')" in body
    assert "Pad.HoleRotation :=" in body
