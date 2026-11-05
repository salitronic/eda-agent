# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An unknown layer's hint names the enabled mechanical layers past 16.

The hint walked eMechanical1 to eMechanical16, so with Mechanical17
enabled a caller told "Unknown layer" was never offered it.

MEASURED 2026-10-31 on AD 26.11 (scratch board, discarded): Mechanical17
and Mechanical40 enabled, pcb_get_copper layer=Nowhere listed both, and
layer="Mechanical 40", the spelling the hint prints, was accepted.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_UTILS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Utils.pas"


def test_the_hint_walks_every_mechanical_number():
    fns = load(_UTILS, minimum=20)
    body = fns["BoardLayerNamesHint"]
    assert "For Num := 1 To 1024 Do" in body
    assert "Lyr := MechLayerFromNumber(Num);" in body
    assert "eMechanical16" not in body


def test_past_16_only_enabled_layers_are_named():
    body = load(_UTILS, minimum=20)["BoardLayerNamesHint"]
    assert "Enabled := Num <= 16;" in body
    assert "Enabled := Obj.MechanicalLayerEnabled;" in body
    assert "If (Obj <> Nil) And Enabled Then" in body
    assert "If Count >= 64 Then Break;" in body


def test_the_helper_comes_first_in_the_unit():
    text = _UTILS.read_text(encoding="utf-8")
    assert text.index("Function MechLayerFromNumber") < text.index("Function BoardLayerNamesHint")
