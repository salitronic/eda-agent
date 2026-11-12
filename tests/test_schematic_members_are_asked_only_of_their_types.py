# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""The generic schematic getter and setter ask a member only of the types
that have it.

DelphiScript resolves a member on the object at run time. Asked of an
object whose interface lacks it, the engine raises "Undeclared identifier"
in a modal no Try catches, and the polling loop stops (issues #253, #267,
#328, #330). GetSchProperty and SetSchProperty read and wrote LineWidth, Style,
IOType, Alignment, Electrical, Width, PinLength, LibReference,
SourceLibraryName, DesignItemId, ComponentDescription, CurrentPartID and
PartCount on the base ISch_GraphicalObject with no type check, so a query
of a pin for LineWidth or of a wire for IOType took the bridge down. Each
now asks an allowlist first; any other type answers unreadable / not
written.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"

# guard -> (types it must keep, types it must refuse)
_GATES = {
    "SchObjectIsPart": (("eSchComponent", "eHarnessComponent"),
                        ("ePin", "eWire", "eSheetSymbol", "eParameter")),
    "SchObjectIsPinLike": (("ePin", "eHarnessPin"),
                           ("eSchComponent", "ePort", "eSheetEntry", "eWire")),
    "SchObjectHasIOType": (("ePort", "eSheetEntry"),
                           ("ePin", "eWire", "eNetLabel", "ePowerObject")),
    "SchObjectHasAlignment": (("ePort", "eTextFrame", "eNote"),
                              ("ePin", "eWire", "eLabel")),
    "SchObjectHasStyle": (("ePort", "ePowerObject", "eCrossSheetConnector",
                           "eSheetEntry", "eParameterSet"),
                          ("ePin", "eWire", "eLine", "eLabel", "eNetLabel")),
    "SchObjectHasWidth": (("ePort",),
                          ("ePin", "eWire", "eLine", "eSheetEntry")),
    "SchObjectHasLineWidth": (("eLine", "ePolyline", "ePolygon", "eRectangle",
                               "eRoundRectangle", "eArc", "eEllipticalArc",
                               "eEllipse", "eBezier", "eWire", "eBus",
                               "eBusEntry", "eBlanket", "eSheetSymbol",
                               "eHarnessConnector", "eTextFrame", "eNote"),
                              ("ePin", "ePort", "eLabel", "eNetLabel",
                               "ePowerObject", "eParameter", "eSchComponent",
                               "eSheetEntry", "eJunction")),
}

# property -> (guard, the member it reaches)
_PROPS = {
    "LineWidth": "SchObjectHasLineWidth",
    "Style": "SchObjectHasStyle",
    "IOType": "SchObjectHasIOType",
    "Alignment": "SchObjectHasAlignment",
    "Electrical": "SchObjectIsPinLike",
    "Width": "SchObjectHasWidth",
    "PinLength": "SchObjectIsPinLike",
    "LibReference": "SchObjectIsPart",
    "SourceLibraryName": "SchObjectIsPart",
    "DesignItemId": "SchObjectIsPart",
}


def _units():
    return load(_GENERIC, minimum=20)


def _decommented(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


@pytest.mark.parametrize("gate", sorted(_GATES))
def test_each_allowlist_keeps_its_types_and_refuses_the_rest(gate):
    body = _units()[gate]
    keep, refuse = _GATES[gate]
    for t in keep:
        assert re.search(r"(?:Obj\.ObjectId|Id) = %s\b" % t, body), (gate, t)
    for t in refuse:
        assert not re.search(r"\b%s\b" % t, body), (gate, t)
    assert "Result := False;" in body.split("If Obj = Nil")[0]


@pytest.mark.parametrize("routine", ["GetSchProperty", "SetSchProperty"])
@pytest.mark.parametrize("prop", sorted(_PROPS))
def test_every_member_access_is_gated(routine, prop):
    code = _decommented(_units()[routine])
    hits = list(re.finditer(r"\bObj\.%s\b" % prop, code))
    assert hits, (routine, prop)
    for m in hits:
        before = code[max(0, m.start() - 600):m.start()]
        # ConnectionX / ConnectionY read PinLength after their own pin check.
        pin_checked = "Obj.ObjectId <> ePin" in before and "Connection" in before
        assert _PROPS[prop] + "(Obj)" in before or pin_checked, (
            routine, prop, m.start())


@pytest.mark.parametrize("routine", ["GetSchProperty", "SetSchProperty"])
def test_part_only_members_are_gated(routine):
    code = _decommented(_units()[routine])
    for prop in ("CurrentPartID", "PartCount"):
        at = code.index("PropName = '%s'" % prop)
        member = code.index("Comp.", at)
        assert "SchObjectIsPart(Obj)" in code[at - 80:member], (routine, prop)


def test_component_description_read_is_gated():
    code = _decommented(_units()["GetSchProperty"])
    m = re.search(r"\bObj\.ComponentDescription\b", code)
    assert m
    assert "SchObjectIsPart(Obj)" in code[max(0, m.start() - 200):m.start()]


def test_a_refused_type_says_so():
    def branch(body, prop):
        at = body.index("'%s'" % prop)
        tail = body[at:]
        return re.split(r"\bElse If \(?PropName = '(?!%s')" % prop, tail, maxsplit=1)[0]

    get = _decommented(_units()["GetSchProperty"])
    for prop in _PROPS:
        assert "NotePropertyDiag('unreadable', PropName)" in branch(get, prop), prop
    put = _decommented(_units()["SetSchProperty"])
    for prop in _PROPS:
        assert "Matched := False" in branch(put, prop), prop


def test_the_gates_are_defined_before_their_callers():
    raw = _GENERIC.read_text(encoding="utf-8-sig")
    first_use = raw.index("Function GetSchProperty(")
    for gate in _GATES:
        assert raw.index("Function %s(" % gate) < first_use, gate
