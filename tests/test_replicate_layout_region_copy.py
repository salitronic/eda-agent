# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""pcb.replicate_layout copies a region with Replicate, not ReplicateWithChildren.

On AD 25.8.1 a region has no ReplicateWithChildren: asking for it raised
"Undeclared identifier: ReplicateWithChildren" past the Try, and the
script stopped as soon as an internal net carried a region (every solid
pour's copper is regions). Only a polygon takes ReplicateWithChildren, and
copper its pour made comes along with it, so that copper is not copied a
second time on its own.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _fns():
    return load(_PCB)


def test_only_a_polygon_is_replicated_with_children():
    body = _fns()["ReplicateCopy"]
    head, tail = body.split("Else", 1)
    assert "Prim.ObjectId = ePolyObject" in head
    assert "eRegionObject" not in body
    assert "ReplicateWithChildren" in head
    assert "ReplicateWithChildren" not in tail
    assert "Prim.Replicate;" in tail


def test_the_handler_copies_through_the_helper():
    body = _fns()["PCB_ReplicateLayout"]
    assert "ReplicateWithChildren" not in body
    assert "NewPrim := ReplicateCopy(Prim);" in body


def test_pour_copper_is_left_to_its_polygon():
    body = _fns()["PCB_ReplicateLayout"]
    assert "And (Not PourOwnsPrim(Prim))" in body
    helper = _fns()["PourOwnsPrim"]
    assert "If Prim.ObjectId = ePolyObject Then Exit;" in helper
    assert "Result := Prim.InPolygon;" in helper


def test_the_helpers_come_before_the_handler():
    raw = _PCB.read_text(encoding="utf-8-sig")
    handler = raw.index("Function PCB_ReplicateLayout(")
    assert raw.index("Function ReplicateCopy(") < handler
    assert raw.index("Function PourOwnsPrim(") < handler
