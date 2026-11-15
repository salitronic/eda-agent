# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A board iterator over components looks on every layer.

A rigid-flex board can place parts on an inner signal layer. An iterator
filtered to the top and bottom layers misses them: the layout replicator
then found no internal net for a channel whose parts sat on an inner layer,
and could take a shared net for an internal one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_UNITS = sorted(p for p in _DIR.glob("*.pas") if p.name != "Altium_MCP.pas")


@pytest.mark.parametrize("unit", _UNITS, ids=lambda p: p.name)
def test_no_component_iterator_is_held_to_the_outer_layers(unit):
    text = unit.read_text(encoding="utf-8-sig")
    for m in re.finditer(r"AddFilter_ObjectSet\(MkSet\(eComponentObject\)\);", text):
        nxt = text[m.end():m.end() + 200]
        assert "AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer))" not in nxt, (
            unit.name, text.count("\n", 0, m.start()) + 1)


def test_the_replicator_classifies_nets_on_every_layer():
    text = (_DIR / "PCB.pas").read_text(encoding="utf-8-sig")
    body = text[text.index("Function PCB_ReplicateLayout("):]
    body = body[:body.index("{ 5. Replicate")]
    at = body.index("AddFilter_ObjectSet(MkSet(eComponentObject));")
    assert "AddFilter_IPCB_LayerSet(LayerSet.AllLayers);" in body[at:at + 300]
