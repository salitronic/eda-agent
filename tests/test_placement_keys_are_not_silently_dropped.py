# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A mistyped placement key must be reported, not ignored.

``sch_place_components`` takes ``list[dict]``, so the schema cannot check
what is inside each dict.  An unrecognised key was therefore dropped in
silence, and the consequences landed somewhere else entirely:

    {"lib_reference": "...", "source_library": "...\\X.SchLib"}
                              ^^^^^^^^^^^^^^ not read

``library_path`` is then empty, the Pascal calls
``LoadComponentFromLibrary(LibRef, '')``, that returns Nil, and the
result is reported as ``LOAD_FAILED`` -- an error naming the LIBRARY,
for a call in which no library was ever named.

Measured cost, 2026-09-21: most of a working day.  Four spellings of the
library path, a project sheet and a free sheet, an extracted .SchLib, the
IntLib-versus-SchLib question, ``proj_replace_component``,
``sch_replicate_component`` and finally Altium's own Place Part dialog
driven through UI automation, which crashed Altium.  All of it against an
error whose real cause was the key name.

Two layers have to hold for that to be impossible:

1. the tool rejects keys it does not read, and suggests the right one;
2. the Pascal distinguishes "no path given" from "the load failed".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load

from eda_agent.tools.generic import PLACEMENT_KEYS, unknown_placement_keys


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERIC_PAS = REPO_ROOT / "scripts" / "altium" / "Generic.pas"


# --------------------------------------------------------------------------
# layer 1: the tool refuses what it cannot read
# --------------------------------------------------------------------------

def test_the_key_that_actually_caused_it_is_rejected():
    """``source_library`` is the real mistake, not a hypothetical one."""
    bad = unknown_placement_keys([{
        "lib_reference": "LDO_3V3",
        "source_library": "C:\\Lib\\Power.SchLib",
        "designator": "U5",
        "x": 100, "y": 200,
    }])
    assert "source_library" in bad, (
        "the key that cost a day in the field must be rejected")
    assert bad["source_library"] == "library_path", (
        "rejecting it is only half the value; the caller needs to be told "
        "which key they meant, or they go on to suspect the library")


def test_the_other_observed_mistake_is_rejected():
    """``lib_ref`` for ``lib_reference`` was tried in the same session."""
    bad = unknown_placement_keys([{"lib_ref": "X", "x": 0, "y": 0}])
    assert bad.get("lib_ref") == "lib_reference"


def test_every_documented_key_is_accepted():
    """The rejection must not fire on the tool's own documented fields.

    A guard that rejects valid input is worse than none: it would make
    the working call fail and send the caller hunting again.
    """
    good = {
        "library_path": "C:\\Lib\\X.SchLib",
        "lib_reference": "PART",
        "x": 1, "y": 2,
        "rotation": 90,
        "designator": "U1",
        "footprint": "SOT23-5",
    }
    assert unknown_placement_keys([good]) == {}
    assert set(good) <= set(PLACEMENT_KEYS)


def test_unknown_keys_are_collected_across_all_placements():
    """One bad dict in fifty must still be caught.

    Checking only the first placement would pass a batch where the typo
    is in the tenth, which is the shape these calls actually have.
    """
    placements = [{"library_path": "p", "lib_reference": "a", "x": 0, "y": 0}] * 9
    placements = list(placements) + [{
        "lib_reference": "b", "x": 0, "y": 0, "sourceLibrary": "p"}]
    bad = unknown_placement_keys(placements)
    assert "sourceLibrary" in bad


def test_a_key_with_no_close_match_still_reports():
    """Unknown with no suggestion is still unknown, not accepted."""
    bad = unknown_placement_keys([{"zzz_nonsense": 1}])
    assert "zzz_nonsense" in bad
    assert bad["zzz_nonsense"] is None


# --------------------------------------------------------------------------
# layer 2: the Pascal says which failure it was
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def placement_handler() -> str:
    funcs = load(GENERIC_PAS)
    name = "Gen_PlaceSchComponentsFromLibrary"
    assert name in funcs, f"{name} is gone; this guard must follow it"
    return funcs[name]


def test_empty_path_is_not_reported_as_a_failed_load(placement_handler: str):
    """An absent library path gets its own token.

    ``LOAD_FAILED`` points at the library. When no library was named,
    that sends the caller to debug the wrong thing, which is exactly what
    happened.
    """
    assert "NO_LIBRARY_PATH" in placement_handler, (
        "an empty library_path must report NO_LIBRARY_PATH, not "
        "LOAD_FAILED: the second one blames a library that was never "
        "named and costs the caller hours")


def test_the_empty_check_precedes_the_load(placement_handler: str):
    """Order matters: check before calling, or the token never fires."""
    guard = placement_handler.find("NO_LIBRARY_PATH")
    loadcall = placement_handler.find("LoadComponentFromLibrary")
    assert guard != -1 and loadcall != -1
    assert guard < loadcall, (
        "the empty-path check must come before LoadComponentFromLibrary; "
        "after it, the Nil return has already been labelled LOAD_FAILED")


def test_load_failed_still_exists_for_real_load_failures(placement_handler: str):
    """Splitting the two must not delete the original.

    A genuinely bad path, or a part absent from a real library, is still
    a load failure and must still say so.
    """
    assert "LOAD_FAILED" in placement_handler
