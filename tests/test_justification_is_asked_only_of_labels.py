# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Justification is read and written only on the label types that have it.

MEASURED on AD 25.8.1: a generic query of a schematic text frame asking
for Justification raised "Undeclared identifier: Justification" in an
error box no Try catches, and the polling loop stopped. A text frame has
Alignment, not Justification. The member table (eda_agent.sch_members)
holds the types that have it, and the generated SchMemberGate refuses the
rest before the getter or setter touches the object.
"""
from __future__ import annotations

from pathlib import Path

from eda_agent import sch_members as sm

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"
_LABELS = ("eLabel", "eNetLabel", "eParameter", "eDesignator", "eSheetName",
           "eSheetFileName", "ePowerObject", "eCrossSheetConnector")


def test_the_table_holds_the_labels_and_no_text_frame():
    types = sm.TYPES_BY_MEMBER["Justification"]
    assert set(types) == set(_LABELS)
    for t in ("eTextFrame", "eNote", "ePort", "ePin", "eLine", "eWire"):
        assert t not in types, t


def test_the_generated_gate_carries_it():
    text = _GENERIC.read_text(encoding="utf-8-sig")
    gate = text[text.index("Function SchMemberGate("):]
    gate = gate[:gate.index("{ END GENERATED SchMemberGate }")]
    at = gate.index("(PropName = 'Justification')")
    branch = gate[at:gate.index("Result := 1;", at)]
    for t in _LABELS:
        assert "(Id = %s)" % t in branch, t


def test_a_text_frame_is_told_to_use_alignment():
    why = sm.member_refusal("eTextFrame", "Justification")
    assert why and "eLabel" in why
    assert sm.member_refusal("eNetLabel", "Justification") is None
