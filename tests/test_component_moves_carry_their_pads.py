# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Moving a placed component must move its pads with it.

A component owns its pads. Assigning ``Comp.x`` moves the component
record and leaves every child pad where it was in the board's own
structures, so the polygon engine keeps clearing the hole the part used
to occupy and the DRC keeps measuring the old footprint. Reported from a
live board: a part was moved through the API, repoured, and the copper
still avoided the old pad while colliding with the new one. Repouring
from the menu did not help either, because nothing was wrong with the
repour: the board still believed the pad had not moved.

MoveByXY is inherited from IPCB_Primitive and is what this codebase
already uses for bodies and replicated primitives, and what four
published scripts use to move a component.

PLACING is not moving. A component from PCBObjectFactory has not been
added to the board yet, its pads come from LoadFromLibrary relative to
its origin, and setting that origin before AddPCBObject is the ordinary
placement sequence. Those functions are exempt, and the exemption is
keyed on the factory call rather than on a list of names.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: Assignment to a component's own position.
_ASSIGN = re.compile(r"\b(Comp|Component|PcbComp)\w*\.(x|y)\s*:=", re.I)


def _functions(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^(?=(?:Function|Procedure)\s+\w+)", text)
    out = []
    for part in parts:
        head = part.split(chr(10), 1)[0]
        name = re.match(r"(?:Function|Procedure)\s+(\w+)", head)
        if name and len(part) > 40:
            out.append((name.group(1), part))
    return out


@pytest.fixture(scope="module")
def functions() -> list[tuple[str, str]]:
    text = strip_comments(_PAS.read_text(encoding="utf-8", errors="replace"))
    found = _functions(text)
    assert len(found) > 100, "the function splitter found almost nothing"
    return found


def test_the_movers_this_was_reported_against_use_a_move(functions):
    """The five handlers that moved an existing component by assignment."""
    wanted = {"PCB_MoveComponent", "PCB_BatchMoveComponents",
              "PCB_AlignComponents", "PCB_SnapToGrid",
              "PCB_DistributeComponents"}
    seen = {name for name, _ in functions} & wanted
    assert seen == wanted, f"missing handlers: {sorted(wanted - seen)}"
    for name, body in functions:
        if name in wanted:
            assert "MoveByXY" in body, (
                f"{name} no longer moves the component, so its pads stay "
                f"behind and the pour clears the wrong copper")


def test_no_placed_component_is_repositioned_by_assignment(functions):
    offenders = []
    for name, body in _placement_exempt(functions):
        for hit in _ASSIGN.finditer(body):
            offenders.append(f"{name}: {hit.group(0)}")
    assert not offenders, (
        "these move an existing component by assigning its position, which "
        "leaves its pads behind in the board's structures: "
        + "; ".join(offenders))


def _placement_exempt(functions):
    """Every function except the ones that CREATE a component."""
    for name, body in functions:
        if "PCBObjectFactory(eComponentObject" in body:
            continue
        yield name, body


def test_the_exemption_covers_the_placers_and_nothing_more(functions):
    """A blanket exemption would hide the next mover written this way."""
    exempt = [name for name, body in functions
              if "PCBObjectFactory(eComponentObject" in body]
    assert exempt, "the factory call moved; every placer is now being checked"
    assert len(exempt) <= 6, (
        f"{len(exempt)} functions claim to create a component; the "
        f"exemption is meant to be narrow: {exempt}")
    for name, body in functions:
        if name in exempt:
            assert "AddPCBObject" in body, (
                f"{name} is exempt as a placer but never adds the component "
                f"to the board, so it is not placing one")


def test_a_move_is_computed_from_where_the_component_actually_is(functions):
    """The delta cannot be assumed from the caller's numbers alone."""
    for name, body in functions:
        if name not in ("PCB_MoveComponent", "PCB_BatchMoveComponents"):
            continue
        move = body.split("MoveByXY", 1)[0]
        assert "Comp.x" in move and "Comp.y" in move, (
            f"{name} calls MoveByXY without reading the current position")
