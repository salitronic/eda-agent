# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Which schematic object types carry which type-specific member.

DelphiScript resolves a member on the object at run time. Asked of an object
whose interface lacks it, the engine raises "Undeclared identifier" in a
modal no Try catches, and the polling loop stops. The generic getter and
setter (Generic.pas GetSchProperty / SetSchProperty) therefore ask
SchMemberGate first, and that function is generated from this table by
scripts/altium/gen_sch_members.py. Nothing else decides it.

A type belongs in a row only when its interface declares the member in the
SDK (``sdk``: the interfaces, ancestors included) and a script has been
seen to read it on that type (``measured``: the Altium versions). A type
left out answers blank and "unreadable" from the getter and "unknown" from
the setter; the loop keeps running. A type put in wrongly stops the loop,
so a row is widened only with both kinds of evidence.

``min_version`` (Altium's version code, 2508 for 25.8) limits a row to the
versions it was measured on, where an older one is known to lack it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MemberRow:
    members: tuple[str, ...]
    types: tuple[str, ...]
    sdk: tuple[str, ...]
    measured: tuple[str, ...]
    note: str = ""
    min_version: Optional[int] = None
    #: Types the SDK gives the member that are left out on purpose.
    excluded: dict[str, str] = field(default_factory=dict)


ROWS: tuple[MemberRow, ...] = (
    MemberRow(
        members=("LibReference", "SourceLibraryName", "DesignItemId",
                 "ComponentDescription", "CurrentPartID", "PartCount"),
        types=("eSchComponent", "eHarnessComponent"),
        sdk=("ISch_Component", "ISch_HarnessComponent"),
        measured=("25.8", "26.11"),
        note="a placed part's identity and parts",
        excluded={"eSheetSymbol": "declares SourceLibraryName and DesignItemId "
                                  "only; one gate covers the six members"},
    ),
    MemberRow(
        members=("Electrical", "PinLength"),
        types=("ePin", "eHarnessPin"),
        sdk=("ISch_Pin", "ISch_HarnessPin"),
        measured=("25.8", "26.11"),
    ),
    MemberRow(
        members=("IOType",),
        types=("ePort", "eSheetEntry"),
        sdk=("ISch_Port", "ISch_SheetEntry"),
        measured=("25.8", "26.11"),
    ),
    MemberRow(
        members=("Alignment",),
        types=("ePort", "eTextFrame", "eNote"),
        sdk=("ISch_Port", "ISch_TextFrame", "ISch_Note"),
        measured=("25.8", "26.11"),
    ),
    MemberRow(
        members=("Style",),
        types=("ePort", "ePowerObject", "eCrossSheetConnector", "eSheetEntry",
               "eParameterSet", "eProbe"),
        sdk=("ISch_Port", "ISch_PowerObject", "ISch_CrossSheetConnector",
             "ISch_SheetEntry", "ISch_ParameterSet", "ISch_Probe"),
        measured=("25.8", "26.11"),
        note="a probe is a parameter set; its Style is measured on 26.11 only",
    ),
    MemberRow(
        members=("Width",),
        types=("ePort",),
        sdk=("ISch_Port",),
        measured=("25.8", "26.11"),
        note="a length in mils",
        excluded={"ePin": "declared, but reads 0.0001 mil: not a length, so "
                          "a length written there would be nonsense",
                  "eHarnessPin": "as ePin"},
    ),
    MemberRow(
        members=("LineWidth",),
        types=("eLine", "ePolyline", "ePolygon", "eRectangle", "eRoundRectangle",
               "eArc", "eEllipticalArc", "eEllipse", "eBezier", "eWire", "eBus",
               "eBusEntry", "eSignalHarness", "eHarnessWire", "eBlanket",
               "eCompileMask", "eImage", "eSheetSymbol", "eHarnessConnector",
               "eTextFrame", "eNote"),
        sdk=("ISch_Line", "ISch_Polyline", "ISch_Polygon", "ISch_Rectangle",
             "ISch_RoundRectangle", "ISch_Arc", "ISch_EllipticalArc",
             "ISch_Ellipse", "ISch_Bezier", "ISch_Wire", "ISch_Bus",
             "ISch_BusEntry", "ISch_HarnessWire", "ISch_Blanket",
             "ISch_CompileMask", "ISch_Image", "ISch_SheetSymbol",
             "ISch_HarnessConnector", "ISch_TextFrame", "ISch_Note"),
        measured=("25.8", "26.11"),
        note="the SDK index lists no members for ISch_SignalHarness; its "
             "LineWidth is measured (26.11)",
    ),
)


def _index() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for row in ROWS:
        for m in row.members:
            if m in out:
                raise ValueError("member in two rows: " + m)
            out[m] = row.types
    return out


#: member -> the object types that carry it
TYPES_BY_MEMBER: dict[str, tuple[str, ...]] = _index()

#: The schematic object types a refusal is judged for. A board type
#: ("eTrackObject" has a Width of its own) or an unknown name is not.
SCH_TYPES: frozenset[str] = frozenset(
    t for row in ROWS for t in row.types) | frozenset((
        "eNetLabel", "eParameter", "eJunction", "eLabel", "eDesignator",
        "eNoERC", "eHarnessEntry", "eHarnessSplice", "eSheetName",
        "eSheetFileName"))


def member_refusal(object_type: str, member: str) -> Optional[str]:
    """Why ``member`` cannot be read or written on ``object_type``, or None.

    None also for a member the table does not govern (the base interface
    has it, or another check in the script answers) and for any type not
    in SCH_TYPES (a board type, or a name given loosely)."""
    types = TYPES_BY_MEMBER.get(member)
    ot = (object_type or "").strip()
    if types is None or ot not in SCH_TYPES or ot in types:
        return None
    return "%s has no %s; it is read and written on %s" % (ot, member, ", ".join(types))


def member_refusals(object_type: str, members: list[str]) -> dict[str, str]:
    """member_refusal for each name given, keeping only the refused ones."""
    out: dict[str, str] = {}
    for m in members:
        why = member_refusal(object_type, m.strip())
        if why:
            out[m.strip()] = why
    return out
