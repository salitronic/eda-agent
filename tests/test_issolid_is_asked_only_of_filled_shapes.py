# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""IsSolid is read and written only on the shapes that have it.

MEASURED on AD 25.8.1: a generic query of a schematic line asking for
IsSolid raised "Undeclared identifier: IsSolid" in an error box no Try
catches, and the polling loop stopped. A line has no IsSolid. Both the
getter and the setter now ask an allowlist first, like IsHidden beside
them; any other type answers unreadable / not written.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"
_FILLED = ("eRectangle", "eRoundRectangle", "ePolygon", "ePolyline", "eEllipse",
           "eTextFrame", "eNote", "eBlanket")


def _units():
    return load(_GENERIC, minimum=20)


def _branch(body: str) -> str:
    at = body.index("PropName = 'IsSolid'")
    end = body.index("End", at)
    return body[at:end]


def test_the_allowlist_holds_the_filled_shapes_and_no_line():
    gate = _units()["SchObjectHasIsSolid"]
    for t in _FILLED:
        assert "(Obj.ObjectId = %s)" % t in gate, t
    for t in ("eLine", "eArc", "eLabel", "ePin", "eWire", "eEllipticalArc"):
        assert "(Obj.ObjectId = %s)" % t not in gate, t


def test_the_getter_asks_first():
    branch = _branch(_units()["GetSchProperty"])
    assert branch.index("If SchObjectHasIsSolid(Obj) Then") < branch.index("Obj.IsSolid")
    assert "NotePropertyDiag('unreadable', PropName);" in branch


def test_the_setter_asks_first():
    branch = _branch(_units()["SetSchProperty"])
    assert branch.index("If SchObjectHasIsSolid(Obj) Then") < branch.index("Obj.IsSolid :=")
    assert "Matched := False;" in branch


def test_no_unguarded_issolid_read_is_left_in_the_generic_property_code():
    for name in ("GetSchProperty", "SetSchProperty"):
        body = _units()[name]
        for m in re.finditer(r"Obj\.IsSolid", body):
            before = body[max(0, m.start() - 160):m.start()]
            assert "SchObjectHasIsSolid(Obj)" in before, name


def test_the_gate_is_defined_before_its_callers():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    assert raw.index("Function SchObjectHasIsSolid(") < raw.index("Function GetSchProperty(")
