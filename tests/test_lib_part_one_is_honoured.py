# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An explicit ``@1`` must reach part 1, and a plain lookup must not move.

A SchLib iterator only yields the DISPLAYED part, so ``lib_component:X@1``
answers about part 1 only if the editor is on part 1.  Reported in GH #11
on 2026-09-22: with the editor on part 3, ``@1`` returned part 3's pins,
while ``@2`` upwards worked.

The cause was that the scope parser defaulted to ``PartId := 1`` when no
suffix was written, so an explicit ``@1`` and a plain ``lib_component:X``
arrived as the same value and could not be told apart.  The plain lookup
is the one every lib_ tool reaches through ``SelectLibComponent``, and it
must not start verifying or moving anything: when a step-and-verify once
ran on every lookup, lib_link_footprint and lib_batch_rename refused
components that demonstrably existed.

So there are two obligations pulling in opposite directions, and both are
pinned here:

* ``PartId 0`` means no suffix, and takes the historical path unchanged.
* ``PartId 1`` means ``@1`` was written, and is reached and verified.

Part 1 cannot be reached by stepping (the step clamps, and there is no
previous-part command), so it is reached by selecting a DIFFERENT
component and reselecting this one, which was measured to reset the
display.  A library holding only the one component has nothing to
reselect from, and must say so rather than answer about whichever part
is showing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PAS = REPO_ROOT / "scripts" / "altium" / "Library.pas"
GENERIC_PAS = REPO_ROOT / "scripts" / "altium" / "Generic.pas"


@pytest.fixture(scope="module")
def lib() -> dict:
    return load(LIBRARY_PAS)


@pytest.fixture(scope="module")
def gen() -> dict:
    return load(GENERIC_PAS)


# --------------------------------------------------------------------------
# no suffix and @1 are different values
# --------------------------------------------------------------------------

def test_the_parser_defaults_to_no_suffix_not_part_one(gen: dict) -> None:
    """The default must be 0. A default of 1 is the whole defect."""
    body = gen["ApplyLibComponentScope"]
    first = re.search(r"PartId\s*:=\s*(-?\d+)\s*;", body)
    assert first is not None, "ApplyLibComponentScope never initialises PartId"
    assert first.group(1) == "0", (
        f"the scope parser initialises PartId to {first.group(1)}. It must "
        f"be 0 for no suffix; with 1, an explicit @1 and a plain lookup are "
        f"the same value and @1 is answered about whatever part is displayed")


def test_the_plain_wrapper_passes_no_suffix(lib: dict) -> None:
    """Every lib_ tool reaches the selector through SelectLibComponent.

    If this passed 1, every plain lookup would start bouncing between
    components and verifying part 1, which is the regression that once
    made two tools refuse components that existed.
    """
    body = lib["SelectLibComponent"]
    m = re.search(r"SelectLibComponentPart\(\s*Name\s*,\s*(-?\d+)\s*\)", body)
    assert m is not None, "SelectLibComponent no longer delegates"
    assert m.group(1) == "0", (
        f"SelectLibComponent passes {m.group(1)}. It must pass 0 so the "
        f"plain lookup takes the unchanged path and never bounces")


# --------------------------------------------------------------------------
# @1 is reached, and only @1
# --------------------------------------------------------------------------

def test_explicit_part_one_is_reached_and_verified(lib: dict) -> None:
    body = lib["SelectLibComponentPart"]
    arm = re.search(r"If\s+PartId\s*=\s*1\s+Then\s+Begin(.*?)End;", body,
                    re.DOTALL)
    assert arm is not None, (
        "SelectLibComponentPart has no branch for PartId = 1, so an "
        "explicit @1 is never honoured")
    assert "ReachLibPartOne" in arm.group(1), (
        "the PartId = 1 branch does not call ReachLibPartOne")
    assert re.search(r"Result\s*:=\s*Nil", arm.group(1)), (
        "the PartId = 1 branch must return Nil when part 1 is not reached, "
        "so the scope resolves to NOT_FOUND instead of a wrong answer")


def test_the_plain_lookup_is_neither_stepped_nor_bounced(lib: dict) -> None:
    """PartId 0 must fall through every guarded branch untouched."""
    body = lib["SelectLibComponentPart"]
    for gate in re.findall(r"If\s+(PartId[^T]*?)\s+Then", body):
        g = gate.replace(" ", "")
        assert g in ("PartId>1", "PartId=1"), (
            f"SelectLibComponentPart branches on 'If {gate}', which a "
            f"PartId of 0 may enter. The plain lookup must reach no branch "
            f"that steps or bounces.")


# --------------------------------------------------------------------------
# the bounce, and the case where it is impossible
# --------------------------------------------------------------------------

def test_part_one_is_reached_by_reselecting_a_different_component(lib: dict):
    """Two CurrentSchComponent assignments, the first to another component.

    Reassigning the SAME component was measured to leave the display
    where it was, so a single assignment would look like a reset and do
    nothing.
    """
    body = lib["ReachLibPartOne"]
    assigns = re.findall(r"CurrentSchComponent\s*:=\s*(\w+)", body)
    assert assigns[:2] == ["Other", "Component"], (
        f"ReachLibPartOne assigns CurrentSchComponent to {assigns}. It must "
        f"select another component first and then this one; reselecting "
        f"the same component does not reset the display")


def test_a_single_component_library_reports_rather_than_guessing(lib: dict):
    """With nothing to bounce off, the answer is 'cannot', not a part."""
    body = lib["ReachLibPartOne"]
    arm = re.search(r"If\s+OtherName\s*=\s*''\s+Then\s+Begin(.*?)Exit;\s*End;",
                    body, re.DOTALL)
    assert arm is not None, (
        "ReachLibPartOne does not handle a library with no other component")
    assert "NoteNextStep" in arm.group(1), (
        "the no-other-component case must say why part 1 was not reached")
    assert not re.search(r"Result\s*:=\s*True", arm.group(1)), (
        "the no-other-component case must not report success: the display "
        "is on another part and nothing moved it")


def test_success_after_the_bounce_excludes_another_parts_pins(lib: dict):
    """The property that matters is 'no pin from another part is visible'.

    PartOneEvidence returns -1 when another part's pins are seen, so the
    post-bounce test must reject exactly that value.
    """
    body = lib["ReachLibPartOne"]
    tail = body[body.rfind("CurrentSchComponent"):]
    assert re.search(r"Result\s*:=\s*\(\s*Evidence\s*>=\s*0\s*\)", tail), (
        "after the bounce, ReachLibPartOne must fail when the iterator "
        "still shows another part (PartOneEvidence = -1)")


def test_verification_uses_the_iterator_the_query_answers_from(lib: dict):
    """Not a proxy. The query iterates SchIterator_Create + ePin."""
    body = lib["DisplayedPartByPins"]
    assert "SchIterator_Create" in body
    assert re.search(r"MkSet\(\s*ePin\s*\)", body)
    assert "OwnerPartId" in body


def test_the_evidence_is_pins_not_the_part_id_readback(lib: dict):
    """Live on AD 26.10.1.6: @1 was accepted while part 3 was on screen.

    ReachLibPartOne assigns CurrentPartID := 1 just before it checks, so
    any readback of the part id can answer 1 whatever the editor shows.
    Only the pins the query iterator yields are evidence.
    """
    body = lib["PartOneEvidence"]
    assert "DisplayedPartByPins" in body
    for readback in ("CurrentLibPartId", "GetState_CurrentSchComponentPartId",
                     "CurrentPartID"):
        assert readback not in body, (
            f"PartOneEvidence consults {readback}, which echoes the part id "
            f"ReachLibPartOne has just assigned")


def test_the_editor_is_let_act_between_the_two_selections(lib: dict):
    """Live: back to back inside one handler, the display stayed on part 3.

    The measured reset was three separate calls with the UI running
    between them. Messages must be processed after EACH selection.
    """
    body = lib["ReachLibPartOne"]
    first = body.index("SchLib.CurrentSchComponent := Other")
    second = body.index("SchLib.CurrentSchComponent := Component")
    check = body.index("Evidence := PartOneEvidence(SchLib)")
    assert "Application.ProcessMessages" in body[first:second], (
        "no message processing between selecting the other component and "
        "reselecting this one")
    assert "Application.ProcessMessages" in body[second:check], (
        "no message processing between reselecting and checking the pins")
