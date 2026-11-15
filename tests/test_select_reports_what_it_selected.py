# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""obj_select reports matched and selected, never another request's notes.

Gen_SelectObjects handed back the modify reply, whose tail is the property
notes, and never reset them. An obj_modify with a property this build does
not write, followed by an unrelated obj_select, answered with the earlier
call's property name and success false. A select on 3D bodies after a
query of their X and Y said X and Y were unreadable, although the
selection had worked.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import strip_comments

GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _routine(name: str) -> str:
    code = strip_comments(GENERIC.read_text(encoding="utf-8", errors="replace"))
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_notes_are_reset_before_anything_runs():
    body = _routine("Gen_SelectObjects")
    reset = body.find("ResetPropertyDiag(0)")
    first = min(i for i in (body.find("ProcessActiveDoc("),
                            body.find("ProcessActivePCBDoc(")) if i >= 0)
    assert 0 <= reset < first, (
        "Gen_SelectObjects must reset the property notes before it selects")


def test_the_modify_reply_is_not_the_answer():
    body = _routine("Gen_SelectObjects")
    assert not re.search(r"Result\s*:=\s*ProcessActive(PCB)?Doc\(", body), (
        "a select returned the modify reply, property notes and all")
    assert "ModifyOutcomeJson" not in body and "RenderPropertyDiagJson" not in body
    # The modify reply goes back only when it is a refusal.
    handed_back = re.findall(r"Result\s*:=\s*(\w+)\s*;", body)
    for name in set(handed_back):
        guarded = re.findall(
            rf"If\s+ExtractJsonValue\({name},\s*'success'\)\s*<>\s*'true'\s*Then"
            rf"\s*Begin\s*Result\s*:=\s*{name}\s*;", body)
        assert len(guarded) == handed_back.count(name), (
            f"Result := {name} is returned without checking it is a refusal")
    replies = re.findall(r"BuildSuccessResponse\(RequestId,(.*?)\);", body, re.S)
    assert replies, "no success reply found"
    for reply in replies:
        assert '"matched"' in reply and '"selected"' in reply, reply
        assert "properties" not in reply, reply


def test_the_selection_is_read_back():
    body = _routine("Gen_SelectObjects")
    assert re.search(r"ProcessActivePCBDoc\([^;]*'query'", body)
    assert re.search(r"ProcessActiveDoc\([^;]*'query'", body)
    # The schematic read-back filters on Selection, so the getter has to
    # answer it or every schematic select would read back as none.
    getter = _routine("GetSchPropertyGated")
    assert re.search(r"PropName\s*=\s*'Selection'\s*Then\s*Result\s*:=", getter)
