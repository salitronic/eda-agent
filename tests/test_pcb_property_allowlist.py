# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every property the board getter answers is one obj_query accepts.

obj_query refuses a board property IsKnownPCBProperty does not list, so a
name the getter answers but the list lacks is reported as not existing.
Kind, the pour options and the body heights sat in exactly that gap: the
getter read them, the allow-list refused them. KnownPCBPropertyList is the
text the refusal shows, so it has to name them too.
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"


def _body(text: str, header: str) -> str:
    start = text.index(header)
    end = re.compile(r"^End;", re.M).search(text, start).end()
    return text[start:end]


def _names(body: str) -> set[str]:
    return set(re.findall(r"PropName\s*=\s*'([A-Za-z.]+)'", body))


def test_the_allow_list_and_the_refusal_text_cover_the_getter():
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    answered = _names(_body(text, "Function GetPCBProperty("))
    allowed = _names(_body(text, "Function IsKnownPCBProperty("))
    listed = set(re.findall(r"[A-Za-z]+", _body(text, "Function KnownPCBPropertyList(")
                            .split("Begin", 1)[1]))
    assert answered, "the getter's branches were not found; update this test"
    assert answered - allowed == set(), "answered by the getter, refused by obj_query"
    # The refusal text lists bare names; dotted and alias spellings are extra.
    bare = {n for n in answered if "." not in n} - {"RegionKind"}
    assert bare - listed == set(), "missing from the list the refusal shows"


def test_free_copper_can_be_told_from_a_footprints():
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    answered = _names(_body(text, "Function GetPCBProperty("))
    assert {"InComponent", "InPolygon", "Component", "Locked", "IsKeepout"} <= answered


def test_every_property_the_setter_writes_is_one_the_getter_reads():
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    written = _names(_body(text, "Function SetPCBProperty("))
    answered = _names(_body(text, "Function GetPCBProperty("))
    assert written, "the setter's branches were not found; update this test"
    assert written - answered == set(), "written by obj_modify, unreadable by obj_query"


def test_a_texts_height_and_stroke_can_be_read_and_written():
    # Silkscreen text size is a fabrication requirement; only the create
    # path could set it, and "Height" was refused as not a property.
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    answered = _names(_body(text, "Function GetPCBProperty("))
    written = _names(_body(text, "Function SetPCBProperty("))
    assert {"Height", "StrokeWidth", "IsDesignator", "IsComment"} <= answered
    assert {"Height", "StrokeWidth"} <= written


def test_a_length_is_written_as_decimal_mils_and_a_non_number_is_refused():
    # StrToIntDef read 3.5 as 0: a fractional width wrote zero and a
    # fractional X moved the object to the origin.
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    setter = _body(text, "Function SetPCBProperty(")
    lengths = _names(_body(text, "Function IsLengthProperty("))
    assert "StrToIntDef(Value" not in setter
    assert {"X", "Y", "Width", "Height", "StrokeWidth"} <= lengths
    assert re.search(r"Refused\s*:=\s*IsLengthProperty\(PropName\)\s*And\s*\(Not IsFloatStr\(Value\)\)",
                     setter)
    assert re.search(r"If Refused Then\s*Begin\s*Result\s*:=\s*-1", setter)


def test_modify_collects_before_it_changes():
    # An object changed while the board iterator walks can move in the
    # spatial index under it. Delete already collected first; modify did not.
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    walk = _body(text, "Function ProcessPCBBoardObjects(")
    start = walk.index("If Mode = 'modify' Then")
    arm = walk[start:walk.index("Exit;", start)]
    first_apply = arm.index("ApplySetPropertiesPCB(")
    # The walk is CollectPCBMatches, which hands back the whole list with
    # every iterator it used destroyed; nothing is applied inside it.
    collect = arm.index("CollectPCBMatches(")
    assert collect < first_apply, "a property is applied before the walk ends"
    routes = _body(text, "Procedure CollectPCBMatches(")
    assert "ApplySetPropertiesPCB(" not in routes
    assert routes.count("_Create;") == routes.count("_Destroy(")
