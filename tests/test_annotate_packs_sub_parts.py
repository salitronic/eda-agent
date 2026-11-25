# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_annotate gives the sub-parts of one device one designator.

A part with more than one sub-part (PartCount counts the shared part zero,
so above 2) is keyed by its library reference and design item. A sub-part
that arrived with a designator shares the new one with the sub-parts that
shared its old one; a sub-part at "<prefix>?" joins the first device of its
key numbered so far that lacks its sub-part, and takes a new number only
when none has room. A device never holds the same sub-part twice.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import load

_PROJECT_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Project.pas"


@pytest.fixture(scope="module")
def body() -> str:
    return load(_PROJECT_PAS, minimum=10)["Proj_Annotate"]


def test_a_device_is_keyed_by_library_reference_and_design_item(body):
    assert "If PartN > 2 Then PartKey := Comp.LibReference + '/' + Comp.DesignItemId;" in body
    assert "PartIds.Add(PartId);" in body and "OldDes.Add(Comp.Designator.Text);" in body


def test_the_key_part_and_old_designator_move_with_the_sort(body):
    flat = " ".join(body.split())
    for name in ("PartKeys", "PartIds", "OldDes"):
        assert f"TmpStr := {name}[J]; {name}[J] := {name}[J+1]; {name}[J+1] := TmpStr;" in flat, name


def test_a_sub_part_joins_its_device_before_a_number_is_taken(body):
    join = body.index("If Hit <> '' Then")
    counter = body.index("PrefixIdx := -1;", join)
    assert join < counter
    assert "Comp.Designator.Text := Hit;" in body[join:counter]


def test_a_device_never_takes_the_same_sub_part_twice(body):
    assert body.count("Pos(',' + PartId + ',',") == 2


def test_a_new_device_is_recorded_for_the_sub_parts_after_it(body):
    new = body.index("NewDesText := TmpPrefix + IntToStr(CounterVal);")
    after = body[new:new + 600]
    assert "Groups.Add(PartKey + '|' + NewDesText + '|,' + PartId + ',');" in after
    assert "OldMap.Add(PartKey + '|' + OldText + '=' + NewDesText);" in after
