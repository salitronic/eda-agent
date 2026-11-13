# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Justification is read and written only on the label types that have it.

Issue #328.

MEASURED on AD 25.8.1: a generic query of a schematic text frame asking
for Justification raised "Undeclared identifier: Justification" in an
error box no Try catches, and the polling loop stopped. A text frame has
Alignment, not Justification. Both the getter and the setter now ask an
allowlist first, like IsSolid beside them; any other type answers
unreadable / not written.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"
_LABELS = ("eLabel", "eNetLabel", "eParameter", "eDesignator", "eSheetName",
           "eSheetFileName", "ePowerObject", "eCrossSheetConnector")


def _units():
    return load(_GENERIC, minimum=20)


def _branch(body: str) -> str:
    at = body.index("PropName = 'Justification'")
    end = body.index("End", at)
    return body[at:end]


def test_the_allowlist_holds_the_labels_and_no_text_frame():
    gate = _units()["SchObjectHasJustification"]
    for t in _LABELS:
        assert "(Obj.ObjectId = %s)" % t in gate, t
    for t in ("eTextFrame", "eNote", "ePort", "ePin", "eLine", "eWire",
              "eRectangle", "eSheetEntry"):
        assert "(Obj.ObjectId = %s)" % t not in gate, t


def test_the_getter_asks_first():
    branch = _branch(_units()["GetSchProperty"])
    assert (branch.index("If SchObjectHasJustification(Obj) Then")
            < branch.index("Obj.Justification"))
    assert "NotePropertyDiag('unreadable', PropName);" in branch


def test_the_setter_asks_first():
    branch = _branch(_units()["SetSchProperty"])
    assert (branch.index("If SchObjectHasJustification(Obj) Then")
            < branch.index("Obj.Justification :="))
    assert "Matched := False;" in branch


def test_no_unguarded_justification_is_left_in_the_generic_property_code():
    for name in ("GetSchProperty", "SetSchProperty"):
        body = _units()[name]
        for m in re.finditer(r"Obj\.Justification", body):
            before = body[max(0, m.start() - 160):m.start()]
            assert "SchObjectHasJustification(Obj)" in before, name


def test_the_gate_is_defined_before_its_callers():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    assert (raw.index("Function SchObjectHasJustification(")
            < raw.index("Function GetSchProperty("))
