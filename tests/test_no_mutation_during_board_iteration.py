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

Rewriting that handler to collect first was not enough: the write faults
on AD 26.10.1.6 however it is spelled, so it refuses instead, and what
is guarded here is that it stays refused.

A note in one handler does not stop the next one being written the same
way, which is what the rest of this checks.
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


#: The handlers that walk the board and then write to what they found.
#: PCB_SetTrackWidth is where the pattern was worked out; the other one
#: had the defect and nobody had reported it.
_COLLECTORS = ("PCB_SetTrackWidth", "PCB_MoveTracksToLayer")


def _body(source: str, name: str) -> str:
    assert f"Function {name}" in source, f"{name} is gone from PCB.pas"
    return source.split(f"Function {name}", 1)[1].split(chr(10) + "End;", 1)[0]


def _list_name(body: str) -> str:
    m = re.search(r"(\w+)\s*:=\s*CreateObject\(TInterfaceList\)", body)
    assert m, "no TInterfaceList here, so it walks and writes in one pass"
    return m.group(1)


@pytest.mark.parametrize("name", _COLLECTORS)
def test_a_collecting_handler_fills_its_list_before_it_reads_it_back(
        source: str, name: str):
    """Collect during the walk, modify after it.

    Not "before PreProcess": one of these two opens the transaction
    before the walk and the other after, and both are correct. What has
    to hold is the order of the two list operations.
    """
    body = _body(source, name)
    listname = _list_name(body)
    added = body.find(f"{listname}.Add(")
    read_back = body.find(f"{listname}.Items[")
    assert added >= 0, f"{name} never adds to {listname}"
    assert read_back >= 0, f"{name} never reads {listname} back"

    # Against the end of the walk that FILLS the list, not against the
    # Add itself and not against the last walk in the function. Ordering
    # the two list calls is not enough: a read-back on the line after the
    # Add is still inside the loop, which is the shape of the original
    # defect. And PCB_MoveTracksToLayer runs a second, unrelated walk
    # after the writes, so the last iterator in the function is the
    # wrong landmark too.
    walk_ends = body.find("NextPCBObject", added)
    assert walk_ends >= 0, (
        f"{name} adds to {listname} outside any iterator walk; this guard "
        f"has lost track of what it is measuring")
    assert read_back > walk_ends, (
        f"{name} reads {listname} back while the BoardIterator that filled "
        f"it is still walking; the whole point of the list is to defer the "
        f"writes until after it finishes")


@pytest.mark.parametrize("name", _COLLECTORS)
def test_the_collected_item_is_narrowed_after_retrieval(source: str, name: str):
    """A TInterfaceList holds untyped IInterface.

    Assigning an item straight to a derived local skips QueryInterface and
    leaves a mistyped pointer, and the fault surfaces as a read of
    FFFFFFFF inside oleaut32 rather than anywhere near this code. So the
    local it lands in has to be the base IPCB_Primitive.
    """
    body = _body(source, name)
    listname = _list_name(body)
    targets = re.findall(r"(\w+)\s*:=\s*%s\.Items\[" % re.escape(listname),
                         body)
    assert targets, f"{name} never retrieves from {listname}"

    decls = body.split("Begin", 1)[0]
    for target in set(targets):
        line = next((ln for ln in decls.splitlines()
                     if re.search(r"\b%s\b\s*[,:]" % re.escape(target), ln)),
                    "")
        assert "IPCB_Primitive" in line, (
            f"{name} assigns {listname}.Items straight to {target}, declared "
            f"as {line.strip() or 'nothing found'}; a derived interface here "
            f"skips QueryInterface and faults in oleaut32")


@pytest.mark.parametrize("name", _COLLECTORS)
def test_the_list_of_primitives_is_not_freed(source: str, name: str):
    """Releasing board-primitive refs through the COM marshaller faults."""
    body = _body(source, name)
    assert f"{_list_name(body)}.Free" not in body


def test_the_via_relief_does_not_walk_the_board_at_all(source: str):
    """The handler that hit this refuses now, so it cannot hit it again.

    Setting a via's soldermask expansion faults inside
    ScriptingSystem.DLL on AD 26.10.1.6 however it is written, so the
    collect-then-modify rewrite was not enough and the handler answers
    NOT_SCRIPTABLE without touching the board. Guarded here rather than
    only where the refusal lives, because restoring the write is exactly
    the change that would bring the original crash back.
    """
    body = _body(source, "PCB_SetViaSoldermaskRelief")
    assert "NOT_SCRIPTABLE" in body, (
        "PCB_SetViaSoldermaskRelief no longer refuses; if the write is "
        "back it needs the collect-then-modify guards above and a live "
        "re-measurement on the Altium build that faulted")
    assert "BoardIterator_Create" not in body
    assert "PCBServer.PreProcess" not in body
    assert "SolderMaskExpansion" not in body
