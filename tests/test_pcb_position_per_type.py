# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A PCB primitive's position is not one member, and reading the wrong
one stops the polling loop.

GetPCBProperty read ``Obj.x`` off the declared IPCB_Primitive. For the
types that do not publish it that raises "Undeclared identifier: x",
which the script engine shows as a modal before any Try/Except runs, so
the session hangs until somebody restarts it by hand. Reported from a
live board by an obj_query for X on an eTextObject.

The schematic side has the same rule and the same shape already
(SchObjectHasText / SchObjectHasOrientation): a type that lacks the
member is told so, and the caller gets a reply.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"


@pytest.fixture(scope="module")
def source() -> str:
    stripped = strip_comments(
        _PAS.read_text(encoding="utf-8", errors="replace"))
    assert len(stripped) > 5000, (
        "the comment stripper ate the file; every check below would pass "
        "against nothing")
    assert "Function SetPCBProperty(" in stripped
    return stripped


@pytest.fixture(scope="module")
def resolver(source: str) -> str:
    """The body of the one function that answers where a primitive is."""
    assert "Function PCBPrimitivePos(" in source, (
        "the per-type position resolver is gone; X and Y are back to being "
        "read off whichever member the base interface happens to publish")
    return source.split("Function PCBPrimitivePos(", 1)[1].split(
        "Function GetPCBProperty(", 1)[0]


def test_the_base_interface_is_never_asked_for_a_position(source: str):
    """The defect itself: Obj.x on the declared IPCB_Primitive."""
    for member in ("Obj.x", "Obj.y"):
        assert member not in source, (
            f"{member} is read off the base primitive again, which raises "
            f"an uncatchable modal on every type that does not declare it")


@pytest.mark.parametrize("oid,member", [
    ("ePadObject", "Pad."),
    ("eViaObject", "Via."),
    ("eComponentObject", "Comp."),
    ("eComponentBodyObject", "Body."),
    ("eTextObject", "Txt."),
    ("eArcObject", "Arc."),
    ("eFillObject", "Fill."),
])
def test_each_type_is_read_through_a_local_of_its_own_type(
        resolver: str, oid: str, member: str):
    """DelphiScript resolves members against the DECLARED type, so the
    narrowing is what makes the read legal at all."""
    block = resolver.split(f"Oid = {oid} Then", 1)
    assert len(block) == 2, f"{oid} has no branch"
    body = block[1].split("End", 1)[0]
    assert member in body, f"{oid} is not read through a {member} local"


@pytest.mark.parametrize("oid,x_member,y_member", [
    ("eTextObject", "XLocation", "YLocation"),
    ("eArcObject", "XCenter", "YCenter"),
    ("eFillObject", "X1Location", "Y1Location"),
])
def test_the_types_that_spell_it_differently(resolver: str, oid: str,
                                             x_member: str, y_member: str):
    """These three are the reason the bug existed.

    Every member named here is one this build already exercises: text in
    Generic.pas and Library.pas, arc centres and fill corners in PCB.pas.
    """
    body = resolver.split(f"Oid = {oid} Then", 1)[1].split("End", 1)[0]
    assert x_member in body and y_member in body


def test_a_shape_with_no_single_position_is_refused_not_guessed(
        resolver: str, source: str):
    """A track has two ends and a region has an outline.

    Answering with one of them returns a coordinate the caller then acts
    on, which is worse than saying the property is not on this type.
    """
    for oid in ("eTrackObject", "eRegionObject", "ePolyObject"):
        assert oid not in resolver, (
            f"{oid} was given a position; it does not have one")
    assert "Found := False" in resolver
    assert "NotePropertyDiag('unreadable'" in source


def test_the_getter_returns_a_coordinate_only_when_it_found_one(source: str):
    """Reporting 0 mils for a type with no position is the worst answer
    available: it looks like the corner of the board and reads as data."""
    getter = source.split("Function GetPCBProperty(", 1)[1].split(
        "Function SetPCBProperty(", 1)[0]
    branch = getter.split("If (PropName = 'X') Or (PropName = 'Y') Then", 1)[1]
    body = branch.split("Else If PropName = 'Layer'", 1)[0]
    assert "If PosFound Then" in body, (
        "the coordinate is returned without checking that the type had one")
    refusal = body.split("Else", 1)[1]
    assert "NotePropertyDiag('unreadable'" in refusal
    assert "Result := '';" in refusal


def test_the_refusal_is_not_reported_as_a_bad_property_name(source: str):
    """The tail of the setter turns 0 into "unknown" and -1 into
    "failed". X on a track is neither: the name is right, the type is
    wrong, and saying otherwise sends the caller hunting a typo."""
    setter = source.split("Function SetPCBProperty(", 1)[1]
    branch = setter.split("If (PropName = 'X') Or (PropName = 'Y') Then", 1)[1]
    refusal = branch.split("End;", 1)[0]
    assert "NotePropertyDiag('unreadable'" in refusal
    assert "Result := 1;" in refusal


def test_a_position_is_written_as_a_move_not_an_assignment(source: str):
    """Assigning x killed the PCB engine once with an access violation,
    and was not even reachable on the types that do not declare it."""
    setter = source.split("Function SetPCBProperty(", 1)[1]
    branch = setter.split("If (PropName = 'X') Or (PropName = 'Y') Then", 1)[1]
    body = branch.split("Else If PropName = 'Layer'", 1)[0]
    assert "MoveByXY" in body
    assert "Obj.x :=" not in body and "Obj.y :=" not in body


def test_the_resolver_cannot_raise_out_of_its_own_try(resolver: str):
    """Found stays False on the way out, so a member that turns out to
    be absent on some build degrades to a refusal rather than a stall."""
    assert "Except" in resolver
    tail = resolver.split("Except", 1)[1]
    assert "Found := False" in tail
