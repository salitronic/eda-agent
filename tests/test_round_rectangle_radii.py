# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A round rectangle's corner radii, and a name the reader does not know.

A round rectangle placed on a sheet read and wrote its position, colours
and fill, but not its corner radii: the generic getter had no branch for
them (a read came back blank), and the setter reported them unknown. They
live on ISch_RoundRectangle only, so both sides narrow to that interface
first, as Corner does; any other type answers unreadable / not written.

A name no branch of the getter reads also came back as a silent blank, the
same answer as an empty value. It is now recorded as unknown.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _units():
    return load(_GENERIC, minimum=20)


def _branch(body: str) -> str:
    at = body.index("(PropName = 'CornerXRadius') Or (PropName = 'CornerYRadius')")
    end = re.compile(r"\n        End\r?\n").search(body, at).end()
    return body[at:end]


def test_the_getter_reads_both_radii_through_the_typed_interface():
    branch = _branch(_units()["GetSchProperty"])
    assert "If Obj.ObjectId = eRoundRectangle Then" in branch
    assert branch.index("RR := Obj;") < branch.index("RR.CornerXRadius")
    assert "CoordToMilsF(RR.CornerXRadius)" in branch
    assert "CoordToMilsF(RR.CornerYRadius)" in branch
    assert "NotePropertyDiag('unreadable', PropName);" in branch


def test_the_setter_writes_both_radii_through_the_typed_interface():
    branch = _branch(_units()["SetSchProperty"])
    assert "If Obj.ObjectId = eRoundRectangle Then" in branch
    assert branch.index("RR := Obj;") < branch.index("RR.CornerXRadius :=")
    assert "RR.CornerXRadius := MilsToCoordF(" in branch
    assert "RR.CornerYRadius := MilsToCoordF(" in branch
    assert "Matched := False;" in branch


def test_the_typed_local_is_a_round_rectangle_in_both():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    for name in ("GetSchProperty", "SetSchProperty"):
        at = raw.index("Function %s(" % name)
        head = raw[at:raw.index("Begin", at)]
        assert re.search(r"RR\s*:\s*ISch_RoundRectangle;", head), name


def test_no_radius_is_asked_of_the_base_interface():
    for name in ("GetSchProperty", "SetSchProperty"):
        assert not re.search(r"Obj\.Corner[XY]Radius", _units()[name]), name


def test_an_unknown_name_is_recorded_not_left_blank():
    body = _units()["GetSchProperty"]
    tail = body[body.index("PropName = 'Selection'"):]
    assert re.search(r"Else\s+NotePropertyDiag\('unknown', PropName\);", tail)
