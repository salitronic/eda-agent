# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Placed copper has to join the net, not just point at it.

``Prim.Net := N`` sets a reference and nothing else. The net keeps its
own collection, and connectivity, the ratsnest and the polygon engine
all walk THAT. Copper placed with only the assignment has a net, reports
that net when queried, and is invisible to everything that matters: no
thermal relief where a pour meets it, no connection in the DRC's view,
and an un-routed net reported for copper plainly on the board.

Reported from a live board twice over, as "vias get no reliefs or
connectivity" and "placed pads aren't registered". Sixteen handlers had
it; the two that did not (PCB_TuneLength, PCB_ReplicateLayout) are the
ones whose copper always connected.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: The pour regenerates a polygon's own primitives from its outline and
#: net, and the pours on the reported board filled correctly, so this
#: one is left as it is rather than changed on a guess.
_EXEMPT = {"PCB_PlacePolygonRect"}


@pytest.fixture(scope="module")
def functions() -> dict[str, str]:
    text = strip_comments(_PAS.read_text(encoding="utf-8", errors="replace"))
    out = {}
    for part in re.split(r"(?m)^(?=Function\s+\w+)", text):
        m = re.match(r"Function\s+(\w+)", part)
        if m and len(part) > 40:
            out[m.group(1)] = part
    assert len(out) > 100, "the function splitter found almost nothing"
    return out


def test_the_binder_exists_and_does_both_halves(functions):
    body = functions["BindPrimitiveToNet"]
    assert "Prim.Net := NetObj;" in body
    assert "NetObj.AddPCBObject(Prim);" in body, (
        "the half that makes the copper visible to connectivity is gone")


def test_the_binder_cannot_end_the_call(functions):
    """A type that will not take it degrades to the old behaviour."""
    body = functions["BindPrimitiveToNet"]
    assert "Try" in body and "Except" in body
    assert "If (NetObj = Nil) Or (Prim = Nil) Then Exit;" in body


def test_it_is_defined_before_anything_calls_it():
    """DelphiScript has no forward declarations: a call above the
    definition is an undeclared identifier at runtime, which is a modal
    the polling loop cannot catch. The linter caught this once already.
    """
    text = strip_comments(_PAS.read_text(encoding="utf-8", errors="replace"))
    definition = text.index("Function BindPrimitiveToNet(")
    first_call = min(
        (m.start() for m in re.finditer(r"BindPrimitiveToNet\(", text)
         if m.start() != definition + len("Function ")), default=None)
    assert first_call is not None
    assert definition < first_call


def test_no_handler_assigns_a_net_without_joining_it(functions):
    offenders = []
    for name, body in functions.items():
        if name in _EXEMPT or name == "BindPrimitiveToNet":
            continue
        if "AddPCBObject" not in body:
            continue
        assigns = re.findall(r"\b\w+\.Net\s*:=", body)
        binds = re.findall(r"\b\w*[Nn]et\w*\.AddPCBObject\(|"
                           r"BindPrimitiveToNet\(", body)
        if assigns and not binds:
            offenders.append(name)
    assert not offenders, (
        "these place copper that points at a net the net does not know "
        "about, so it carries no connectivity: " + ", ".join(offenders))


def test_the_handlers_that_were_reported_are_bound(functions):
    """The two the report named, by name, so a rename cannot quietly
    drop them from the sweep above."""
    for name in ("PCB_PlaceVia", "PCB_PlaceComponent"):
        assert "BindPrimitiveToNet(" in functions[name], (
            f"{name} is back to assigning the net without joining it")


def test_the_exemption_is_one_named_handler(functions):
    """A growing exemption list is how this defect comes back."""
    assert _EXEMPT == {"PCB_PlacePolygonRect"}
    assert all(name in functions for name in _EXEMPT)
