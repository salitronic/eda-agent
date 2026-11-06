# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A symbol's display modes can be named.

Altium shows "Alternate 1, 2 ..." for a symbol's alternate drawings unless
the mode has a custom name. MEASURED on AD 25.8.1: GetState_ /
SetState_CustomDisplayModeName on the component read and write those names
(Normal, mode 0, takes one too), they survive save and reopen, and a written
name shows in Altium's UI. DisplayModeNames is read and written as one
';'-separated list, one name per existing mode.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"
_DOCS = Path(__file__).resolve().parents[1] / "src" / "eda_agent" / "tools" / "generic.py"


def _units():
    return load(_GENERIC, minimum=20)


def test_the_names_are_read_per_mode_from_normal():
    body = _units()["ReadDisplayModeNames"]
    assert "For I := 0 To N - 1 Do" in body
    assert "Comp.GetState_CustomDisplayModeName(I)" in body
    assert "Result := Result + ';'" in body


def test_a_list_of_the_wrong_length_is_refused_before_anything_is_written():
    body = _units()["WriteDisplayModeNames"]
    guard = body.index("<> N - 1 Then Exit;")
    assert guard < body.index("Comp.SetState_CustomDisplayModeName(I, Item)")
    assert "If N < 1 Then Exit;" in body


def test_the_write_is_read_back():
    body = _units()["WriteDisplayModeNames"]
    assert "Result := ReadDisplayModeNames(Comp) = Value;" in body


def test_the_property_is_wired_into_get_and_set_on_components_only():
    get = _units()["GetSchProperty"]
    assert "Else If PropName = 'DisplayModeNames' Then Result := ReadDisplayModeNames(Comp)" in get
    put = _units()["SetSchProperty"]
    branch = put[put.index("Or (PropName = 'DisplayModeNames') Then"):]
    assert branch.index("If Obj.ObjectId <> eSchComponent Then") < branch.index("WriteDisplayModeNames(Comp, Value)")
    assert "If Not WriteDisplayModeNames(Comp, Value) Then WroteOK := False;" in branch


def test_helpers_come_before_their_callers_and_take_parameters():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    for name in ("ReadDisplayModeNames(", "WriteDisplayModeNames("):
        assert raw.index("Function " + name) < raw.index("Function GetSchProperty(")


def test_the_tool_documents_it():
    assert '"DisplayModeNames"' in _DOCS.read_text(encoding="utf-8")
