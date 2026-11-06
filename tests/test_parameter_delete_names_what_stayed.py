# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A parameter delete that cannot take says which parameter stayed, and why.

MEASURED on AD 25.8.1: deleting a library symbol's Comment parameter never
takes (Altium keeps a symbol's own Comment), and the reply only said "a
parameter stayed on the symbol after it was removed" -- with several names
in one filter the caller could not tell which. The fault now names the
parameter, and for Comment says what to do instead.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body():
    return load(_GENERIC, minimum=20)["RemoveMatchingParamsFromComponent"]


def _fault(body: str) -> str:
    at = body.index("If (SameTries > 1) Or (Lib = Nil) Then")
    return body[at:body.index("Break;", at)]


def test_the_stuck_parameter_is_named():
    fault = _fault(_body())
    assert "Try StuckName := StuckParam.Name; Except StuckName := ''; End;" in fault
    assert "'the parameter \"' + StuckName" in fault


def test_comment_says_what_to_do_instead():
    fault = _fault(_body())
    assert "If SameText(StuckName, 'Comment') Then" in fault
    assert "keeps a symbol''s own Comment" in fault
    assert "clear its text or hide it instead" in fault


def test_the_name_is_read_through_a_parameter_view():
    # Name is guarded per type in this unit: read it off ISch_Parameter.
    body = _body()
    assert "StuckParam := FoundParam;" in _fault(body)
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    at = raw.index("Procedure RemoveMatchingParamsFromComponent(")
    assert "StuckParam : ISch_Parameter;" in raw[at:at + 600]
