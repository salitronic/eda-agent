# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""generic.clear_highlights clears a schematic selection through the process.

It used to walk wires, labels, ports, power objects, PINS and SHEET
ENTRIES and read / write ``Selection`` on each, inside SchBeginEdit.
SchDeselectAllObjects records that Selection faults on sub-objects in a
modal Try/Except cannot catch, and Gen_HighlightNet's own note forbids
"Selection=False inside the iterator". The schematic branch now uses
SchDeselectAllObjects, as Gen_DeselectAll does.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_GEN = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body():
    return load(_GEN)["Gen_ClearHighlights"]


def _sch_branch():
    body = _body()
    return body[body.index("If SchDoc <> Nil Then"):]


def test_the_schematic_branch_uses_the_process():
    assert "SchDeselectAllObjects(SchDoc);" in _sch_branch()


def test_no_object_is_asked_for_selection():
    assert ".Selection" not in _body()
    assert "SchIterator_Create" not in _body()


def test_no_edit_bracket_round_a_selection_change():
    # A modal inside SchBeginEdit deadlocks Altium (Gen_HighlightNet's note).
    assert "SchBeginEdit" not in _sch_branch()


def test_the_reply_does_not_claim_a_count_it_cannot_read():
    branch = _sch_branch()
    assert '"deselected":"all"' in branch
    assert "cleared" not in branch.split("SchDeselectAllObjects", 1)[1]


def test_the_helper_is_defined_before_the_handler():
    raw = _GEN.read_text(encoding="utf-8-sig")
    assert raw.index("Procedure SchDeselectAllObjects(") < raw.index("Function Gen_ClearHighlights(")
