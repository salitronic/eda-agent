# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A schematic write has to mark its document dirty.

SmartCompile skips DM_Compile while the project looks clean and nothing
is inside its TTL, so a write that leaves the document unmarked is
invisible to everything compiled: a later ERC or netlist read answers
from the model as it stood BEFORE the write. Reported from a live board
as NoERC markers that were in the file and still listed as violations
until the project was reopened, which is exactly what forcing a
recompile does.

The same flag is what a deferred save flushes on, so an unmarked write
is also a write that may never reach disk.

Three handlers did it correctly (place_net_label, place_power_port,
place_sch_component_from_library) and twenty-eight did not, including
the bulk placers the layout engine itself emits through.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"

#: Not a handler: a helper called by handlers that mark for themselves.
_HELPERS = {"SetCompParamText"}


@pytest.fixture(scope="module")
def functions() -> dict[str, str]:
    text = strip_comments(_PAS.read_text(encoding="utf-8", errors="replace"))
    found = {}
    for part in re.split(r"(?m)^(?=Function\s+\w+)", text):
        m = re.match(r"Function\s+(\w+)", part)
        if m and len(part) > 40:
            found[m.group(1)] = part
    assert len(found) > 80, "the function splitter found almost nothing"
    return found


def _writers(functions: dict[str, str]) -> dict[str, str]:
    """Handlers that add a schematic object to a document."""
    return {
        name: body for name, body in functions.items()
        if name not in _HELPERS
        and re.search(r"RegisterSchObjectInContainer|SchRegisterObject\(", body)
    }


def test_the_sweep_finds_the_writers_it_is_meant_to_check(functions):
    writers = _writers(functions)
    assert len(writers) >= 25, (
        f"only {len(writers)} schematic writers found; the detector has "
        f"stopped matching and this guard would pass over nothing")


def test_every_schematic_write_marks_its_document(functions):
    offenders = [
        name for name, body in _writers(functions).items()
        if "MarkDocDirtyByPath" not in body and "SetModified" not in body
    ]
    assert not offenders, (
        "these write to a schematic and leave the document clean, so a "
        "later compile answers from the model as it was before the write: "
        + ", ".join(sorted(offenders)))


@pytest.mark.parametrize("name", [
    "Gen_PlaceNoERC",                       # the reported one
    "Gen_PlaceDirective",                   # same family: changes ERC
    "Gen_PlaceCompileMask",
    "Gen_PlaceWires",                       # the engine emits through these
    "Gen_PlaceNetLabels",
    "Gen_PlacePowerPorts",
    "Gen_PlaceSchComponentsFromLibrary",
])
def test_the_handlers_that_matter_most_are_named(functions, name):
    """Named individually so a rename cannot drop them from the sweep."""
    assert "MarkDocDirtyByPath" in functions[name]


def test_the_mark_is_guarded_against_a_nil_document(functions):
    """Reading DocumentName off a Nil interface raises, and this runs on
    the success path where a raise would be the whole call."""
    for name, body in _writers(functions).items():
        if "MarkDocDirtyByPath" not in body:
            continue
        for line in body.splitlines():
            if "MarkDocDirtyByPath" in line:
                assert "<> Nil" in line or "SrvDoc" in line, (
                    f"{name} marks without checking the document first: "
                    f"{line.strip()}")


def test_a_parameter_write_marks_too(functions):
    """The spice attach paths write through a helper rather than placing
    an object, so the object-placing sweep above does not see them."""
    for name in ("Gen_AttachSpicePrimitive", "Gen_AttachSpiceModel",
                 "Gen_AttachSpicePrimitivesBatch"):
        assert "MarkDocDirtyByPath" in functions[name], (
            f"{name} changes a parameter and leaves the document clean")
