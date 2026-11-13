# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A dimension's text written through the generic layer is read back.

Altium keeps its own diameter sign as the prefix of a radial- or
linear-diameter dimension: the assignment is taken without a word and the
prefix does not change. PCBSetExtra returned 1 all the same, so
obj_modify answered matched 1 for a write that never happened. The three
text members are now read back, and a value Altium did not keep is
refused (-1), which the modify reports as failed.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GEN = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"
_TEXTS = ("TextFormat", "TextPrefix", "TextSuffix")


def _dimension_branch():
    body = load(_GEN, minimum=20)["PCBSetExtra"]
    start = body.index("Owner = 4 Then")
    return body[start:body.index("Owner = 5 Then", start)]


def _member(member):
    branch = _dimension_branch()
    m = re.search(r"PropName = '%s' Then\s*Begin(.*?)End\s*Else" % member, branch, re.S)
    assert m, "%s is not written in a Begin ... End block of its own" % member
    return m.group(1)


def test_each_text_is_written_then_read_back():
    for member in _TEXTS:
        block = _member(member)
        write = block.index("Dim.%s := Value;" % member)
        check = block.index("If Dim.%s <> Value Then" % member)
        assert write < check, member


def test_a_value_altium_did_not_keep_is_refused():
    for member in _TEXTS:
        block = _member(member)
        after = block[block.index("If Dim.%s <> Value Then" % member):]
        assert re.search(r"Result := -1;\s*Exit;", after), member


def test_the_numbers_are_still_written_as_before():
    branch = _dimension_branch()
    assert "If PropName = 'TextHeight' Then Dim.TextHeight := MilsToCoordF(F)" in branch
    assert "Else If PropName = 'ArrowSize' Then Dim.ArrowSize := MilsToCoordF(F)" in branch
