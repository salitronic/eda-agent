# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The SchLib part stepper must not report success without stepping.

A SchLib iterator only ever yields the CURRENTLY DISPLAYED part, so
``obj_query`` / ``obj_modify`` scoped ``lib_component:NAME@3`` answer
about part 3 only if the editor was actually moved there first.

The first version of ``StepLibComponentPartTo`` opened with
``Result := True`` and then walked ``SCH:NextComponentPart`` until the
DOCUMENT reported the target part.  The walk was gated on that readback:

    Result := True;
    Seen := CurrentLibPartId(SchLib);
    If Seen < 0 Then Exit;          <-- returns True, having stepped nothing

``GetState_CurrentSchComponentPartId`` is declared on AD 26.8.1.31 but
returns -1 at runtime, so that branch is the one that fires in the
field.  Reported in GH #11 against a 4-part TPS23881B: a query scoped to
part 3 came back with part 1's pins, reporting success, and nothing said
so.  A guard that passes in exactly the case it exists to catch is worse
than no guard, because the caller stops checking.

These tests pin the two properties that make the silent answer
impossible, and both fail if the old shape comes back:

  * the function starts out FALSE, so every path that does not reach a
    verified comparison returns "not reached";
  * success is only ever a comparison against ``Target``, never a bare
    ``Result := True`` on an unverified path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load


REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PAS = REPO_ROOT / "scripts" / "altium" / "Library.pas"

STEPPER = "StepLibComponentPartTo"


@pytest.fixture(scope="module")
def stepper() -> str:
    """The comment-free body of the stepper.

    Comments are stripped by the loader, so the prose above the function
    describing the defect cannot satisfy a check that forbids it.
    """
    found = load(LIBRARY_PAS)
    assert STEPPER in found, (
        f"{STEPPER} is gone from Library.pas. If it was renamed, this "
        f"guard must follow it: the behaviour it pins is that a part "
        f"that was never reached is never reported as reached.")
    return found[STEPPER]


def test_stepper_starts_out_false(stepper: str) -> None:
    """The first thing assigned to Result is False, not True.

    This is the whole defect in one line.  Opening with True makes every
    early Exit an unearned success; opening with False makes an
    unverified path a refusal, which resolves to NOT_FOUND and tells the
    caller something it can act on.
    """
    first = re.search(r"Result\s*:=\s*(True|False)\s*;", stepper, re.IGNORECASE)
    assert first is not None, (
        f"{STEPPER} never assigns Result a literal. It must open with "
        f"Result := False so an unverified path is a refusal.")
    assert first.group(1).lower() == "false", (
        f"{STEPPER} opens with Result := {first.group(1)}. It must open "
        f"with False. Opening with True is the GH #11 defect: the "
        f"readback it then guards on can be unavailable, and the "
        f"function returns success without moving the editor at all.")


def test_success_is_only_ever_a_verified_comparison(stepper: str) -> None:
    """No bare ``Result := True`` except the single-part early-out.

    Every other route to success must be ``Result := (<something> =
    Target)``, so success means a part id was read back and matched.  The
    one exception is a symbol with a single part, where there is nowhere
    to move and nothing to verify.
    """
    # Anchored on the park, not on "somewhere earlier in the function".
    # Searching the whole prefix for the Count <= 1 guard is satisfied by
    # the single-part early-out for EVERY later Result := True, which let
    # a bare success on the fallback path through when this was first
    # written. Everything from the park onwards is the stepping path, and
    # nothing there may claim success without a comparison.
    park = re.search(r"CurrentPartID\s*:=", stepper, re.IGNORECASE)
    assert park is not None, (
        f"{STEPPER} does not park CurrentPartID; see the companion test.")

    stepping_path = stepper[park.start():]
    stray = re.search(r"Result\s*:=\s*True\s*;", stepping_path, re.IGNORECASE)
    assert stray is None, (
        f"{STEPPER} sets Result := True after the step has been issued. "
        f"Past that point success means 'the editor is showing the part "
        f"that was asked for', which only a comparison against Target can "
        f"establish. A bare True here is the GH #11 silent answer with an "
        f"extra step in front of it.")

    before_park = stepper[: park.start()]
    early = re.findall(r"Result\s*:=\s*True\s*;", before_park, re.IGNORECASE)
    assert len(early) <= 1, (
        f"{STEPPER} has {len(early)} bare Result := True before the step. "
        f"The only one that is earned is the single-part early-out.")
    if early:
        assert re.search(r"Count\s*<=\s*1", before_park, re.IGNORECASE), (
            f"{STEPPER} claims success before stepping without the "
            f"single-part guard that would justify it.")

    # The function must END on a comparison: that is the answer it returns
    # on the path where the document could not be read.
    last = None
    for m in re.finditer(r"Result\s*:=\s*([^;]+);", stepper, re.IGNORECASE):
        last = m
    assert last is not None, f"{STEPPER} never assigns Result."
    assert re.search(r"=\s*Target", last.group(1), re.IGNORECASE), (
        f"{STEPPER} ends on 'Result := {last.group(1).strip()}'. The last "
        f"word has to be a comparison against Target, because that is the "
        f"fallback path taken when the document readback is unavailable, "
        f"which is the case that fires in the field.")


def test_the_target_is_parked_before_the_step(stepper: str) -> None:
    """CurrentPartID is set BEFORE NextComponentPart is issued.

    The command moves the display to ``CurrentPartID + 1``, so the
    assignment is what chooses the destination.  Issuing the step first,
    or dropping the assignment, lands on whatever happened to be
    displayed and the function becomes a coin toss that still verifies
    afterwards (and so mostly returns False).
    """
    park = re.search(r"CurrentPartID\s*:=", stepper, re.IGNORECASE)
    step = re.search(r"RunProcess\s*\(\s*'SCH:NextComponentPart'", stepper,
                     re.IGNORECASE)
    assert park is not None, (
        f"{STEPPER} does not set CurrentPartID. The step moves the "
        f"display to CurrentPartID + 1, so without the assignment the "
        f"destination is undefined.")
    assert step is not None, (
        f"{STEPPER} does not issue SCH:NextComponentPart. Assigning "
        f"CurrentPartID alone sets a property and does not move the "
        f"displayed part, which is the original GH #11 report.")
    assert park.start() < step.start(), (
        f"{STEPPER} issues SCH:NextComponentPart before parking "
        f"CurrentPartID. The assignment chooses where the step lands, so "
        f"it has to come first.")


def test_the_unreadable_document_path_still_verifies(stepper: str) -> None:
    """When the document cannot answer, the property is read back.

    This is the path that used to return True having done nothing.  It
    must now fall through to a second readback and compare that against
    Target, because the property was parked at Target - 1 by this
    function: still Target - 1 means the command did not run.
    """
    readbacks = re.findall(r"CurrentPartID\s*;", stepper, re.IGNORECASE)
    assert readbacks, (
        f"{STEPPER} never reads CurrentPartID back. On a build where "
        f"GetState_CurrentSchComponentPartId returns -1 there is then no "
        f"way to tell a step that worked from one that did not, and GH "
        f"#11 is back.")

    tail = stepper[stepper.rfind("CurrentLibPartId"):]
    assert re.search(r"=\s*Target", tail, re.IGNORECASE), (
        f"{STEPPER} does not compare anything against Target after the "
        f"document readback fails. That path must end in a comparison, "
        f"not an Exit.")
