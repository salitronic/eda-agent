# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Never mutate a board primitive while the BoardIterator is walking.

PCB_SetTrackWidth learned this and wrote it down: changing a primitive
mid-walk corrupts the iterator, and assigning a collected item straight
to a DERIVED interface skips QueryInterface and faults in oleaut32 on
the first vtable call. PCB_SetViaSoldermaskRelief did both anyway, and on
a live board it took the scripting engine down with an access violation.
Because the fault landed between PreProcess and PostProcess it also left
an open transaction in the PCB server, so the next edit misbehaved too.

A note in one handler does not stop the next one being written the same
way, which is what this checks.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: Members whose assignment changes a primitive. Reading during the walk
#: is what collecting is for; writing is the hazard.
#:
#: Selected is deliberately absent. It is the one write the published
#: scripts do perform mid-walk (39 occurrences across the reference
#: corpus), it moves nothing and re-indexes nothing, and claiming it
#: would make this guard cry wolf on the idiom everybody uses.
_MUTATIONS = re.compile(
    r"\b\w+\.(Width|SolderMaskExpansion|SolderMaskExpansionFromHoleEdge|"
    r"HoleSize|Size|Rotation|Layer|Net|X|Y)\s*:=")


@pytest.fixture(scope="module")
def source() -> str:
    text = strip_comments(_PAS.read_text(encoding="utf-8", errors="replace"))
    assert "Function PCB_SetViaSoldermaskRelief" in text
    assert len(text) > 100000, "the stripper ate the file"
    return text


def _functions(text: str) -> list[str]:
    """One string per Function/Procedure body.

    PER FUNCTION, because a fixed window does not stop at the end of one.
    The first version of this scanned 4000 characters past each loop and
    reported a handler that BUILDS a pad (Pad.X, then AddPCBObject) as
    mutating an iterated one, on the strength of an iterator declared in
    the function above it.
    """
    parts = re.split(r"(?m)^(?=(?:Function|Procedure)\s+\w+)", text)
    return [p for p in parts if len(p) > 40]


def _walk_bodies(text: str) -> list[tuple[str, str]]:
    """Every BoardIterator walk, with its body.

    Scoped to BoardIterator on purpose. A component's GroupIterator walks
    that component's own children and the recorded hazard is not about
    it, so claiming those would be asserting something unmeasured.
    """
    bodies = []
    for func in _functions(text):
        if "BoardIterator_Create" not in func:
            continue
        for match in re.finditer(r"While\s+(\w+)\s*<>\s*Nil\s+Do", func):
            cursor = match.group(1)
            body = func[match.end():]
            end = body.find("NextPCBObject")
            if end < 0:
                continue
            if f"{cursor} := " not in body[:end + 40]:
                continue
            bodies.append((cursor, body[:end]))
    return bodies


def test_the_sweep_finds_the_walks_it_is_meant_to_check(source: str):
    """A discovery guard that discovers nothing passes vacuously."""
    assert len(_walk_bodies(source)) >= 5


def _aliases(cursor: str, body: str) -> set[str]:
    """The cursor plus every local narrowed from it.

    ``Track := Prim`` makes Track the same object under a typed name, so
    a write through it is a write to what the iterator is holding. A
    freshly built object (``Comp.Name.Replicate``) is not an alias, which
    is why this cannot be a plain search for the member name: two of the
    first hits were writes to a new primitive about to be added.
    """
    found = {cursor}
    for _ in range(3):                      # aliases of aliases
        for name in list(found):
            for hit in re.finditer(r"\b(\w+)\s*:=\s*%s\s*;" % re.escape(name),
                                   body):
                found.add(hit.group(1))
    return found


def test_no_primitive_is_written_inside_a_board_walk(source: str):
    offenders = []
    for cursor, body in _walk_bodies(source):
        owned = _aliases(cursor, body)
        for hit in _MUTATIONS.finditer(body):
            target = hit.group(0).split(".", 1)[0].strip()
            if target in owned:
                offenders.append(f"{cursor}: {hit.group(0)}")
    assert not offenders, (
        "these writes happen while a BoardIterator is still walking, which "
        "corrupts the iterator and has taken the engine down with an access "
        "violation: " + "; ".join(offenders))


def test_the_via_relief_collects_before_it_modifies(source: str):
    """The handler that hit it, specifically."""
    body = source.split("Function PCB_SetViaSoldermaskRelief", 1)[1].split(
        chr(10) + "End;", 1)[0]
    assert "TInterfaceList" in body, "it walks and writes in one pass again"
    collect = body.split("PCBServer.PreProcess", 1)[0]
    assert "Matches.Add" in collect
    assert "SolderMaskExpansion :=" not in collect


def test_the_collected_item_is_narrowed_after_retrieval(source: str):
    """A TInterfaceList holds untyped IInterface.

    Assigning an item straight to a derived local skips QueryInterface and
    leaves a mistyped pointer, and the fault surfaces as a read of
    FFFFFFFF inside oleaut32 rather than anywhere near this code.
    """
    body = source.split("Function PCB_SetViaSoldermaskRelief", 1)[1].split(
        chr(10) + "End;", 1)[0]
    assert "Prim := Matches.Items[I];" in body
    assert "Via := Matches.Items" not in body


def test_the_list_of_primitives_is_not_freed(source: str):
    """Releasing board-primitive refs through the COM marshaller faults."""
    body = source.split("Function PCB_SetViaSoldermaskRelief", 1)[1].split(
        chr(10) + "End;", 1)[0]
    assert "Matches.Free" not in body
