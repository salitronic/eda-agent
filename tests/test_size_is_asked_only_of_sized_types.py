# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""XSize and YSize are read and written only on the types that have them.

MEASURED on AD 25.8.1: a generic query of a schematic image asking for
XSize raised "Undeclared identifier: XSize" in an error box no Try
catches, and the polling loop stopped. An image has no XSize. Both the
getter and the setter now ask an allowlist first, like IsSolid beside
them; any other type answers unreadable / not written.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _units():
    return load(_GENERIC, minimum=20)


def _branch(body: str) -> str:
    at = body.index("(PropName = 'XSize') Or (PropName = 'YSize')")
    end = body.index("End", at)
    return body[at:end]


def test_the_allowlist_holds_the_sized_types_and_no_image():
    gate = _units()["SchObjectHasSize"]
    for t in ("eSheetSymbol", "eHarnessConnector"):
        assert "(Obj.ObjectId = %s)" % t in gate, t
    for t in ("eImage", "eRectangle", "eLabel", "ePin", "eTextFrame", "eNote"):
        assert "(Obj.ObjectId = %s)" % t not in gate, t


def test_the_getter_asks_first():
    branch = _branch(_units()["GetSchProperty"])
    assert branch.index("If Not SchObjectHasSize(Obj) Then") < branch.index("Obj.XSize")
    assert branch.index("If Not SchObjectHasSize(Obj) Then") < branch.index("Obj.YSize")
    assert "NotePropertyDiag('unreadable', PropName)" in branch


def test_the_setter_asks_first():
    branch = _branch(_units()["SetSchProperty"])
    assert branch.index("If Not SchObjectHasSize(Obj) Then") < branch.index("Obj.XSize :=")
    assert branch.index("If Not SchObjectHasSize(Obj) Then") < branch.index("Obj.YSize :=")
    assert "Matched := False" in branch


def test_no_unguarded_size_access_is_left_in_the_generic_property_code():
    for name in ("GetSchProperty", "SetSchProperty"):
        body = _units()[name]
        for m in re.finditer(r"Obj\.[XY]Size", body):
            before = body[max(0, m.start() - 320):m.start()]
            assert "SchObjectHasSize(Obj)" in before, name


def test_the_gate_is_defined_before_its_callers():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    assert raw.index("Function SchObjectHasSize(") < raw.index("Function GetSchProperty(")
