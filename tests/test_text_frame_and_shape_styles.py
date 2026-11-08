# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Notes, text frames and drawn shapes take their style through obj_modify.

MEASURED 2026-11-07 on AD 25.8.1, on a scratch sheet: a note's and a text
frame's font, border and fill colour already read and wrote, but TextColor,
ShowBorder, WordWrap (and ClipToRect, TextMargin, a note's Author) answered
unknown; Transparent answered unknown on a rectangle and ellipse, LineStyle
on a line and a rectangle. Only what Altium SAVES is offered: written, saved
and reopened, a round rectangle, text frame and note lost Transparent, a
frame and note lost LineStyle, and a note refused ShowBorder. And a schematic FontId of
'0' was written as font 0, 'abc' as font 1, with no refusal.

Every member is asked through the interface that carries it (an absent
member halts the script), and only of the types on an allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _source() -> str:
    return (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _types(body: str) -> set[str]:
    return set(re.findall(r"Obj\.ObjectId = (e\w+)", body))


def test_frame_extras_belong_to_frames_and_notes_and_author_to_notes():
    code = _source()
    owner = _routine(code, "SchExtraOwner")
    five = owner.split("Then Result := 4", 1)[1].split("Then Result := 5", 1)[0]
    assert set(re.findall(r"PropName = '(\w+)'", five)) == {
        "WordWrap", "ClipToRect", "TextMargin"}
    assert "Else If PropName = 'Author' Then Result := 6" in owner
    assert "Else If PropName = 'ShowBorder' Then Result := 7" in owner
    fits = _routine(code, "SchExtraFits")
    assert "((Owner = 5) And ((Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote)))" in fits
    assert "((Owner = 6) And (Obj.ObjectId = eNote))" in fits
    # A note refuses ShowBorder (reads back true): text frames only.
    assert "((Owner = 7) And (Obj.ObjectId = eTextFrame))" in fits
    # TextMargin is a length: decimal mils, neither a whole number nor a switch.
    assert "TextMargin" not in _routine(code, "SchExtraIsInt")
    isbool = _routine(code, "SchExtraIsBool")
    assert "((SchExtraOwner(PropName) = 5) And (PropName <> 'TextMargin'))" in isbool
    assert "(PropName = 'ShowBorder')" in isbool


def test_frame_extras_are_read_and_written_through_typed_interfaces():
    code = _source()
    for name, verb in (("SchGetExtra", "read"), ("SchSetExtra", "write")):
        body = _routine(code, name)
        assert "TF : ISch_TextFrame;" in body
        assert "NoteObj : ISch_Note;" in body
        branch = body.split("Else If (Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote) Then", 1)[1]
        branch = branch.split("    Else\r\n", 1)[0] if "\r\n" in branch else branch.split("    Else\n", 1)[0]
        assert branch.index("TF := Obj") < branch.index("TF.ShowBorder")
        for member in ("ShowBorder", "WordWrap", "ClipToRect", "TextMargin"):
            assert f"TF.{member}" in branch, (verb, member)
        assert branch.index("NoteObj := Obj") < branch.index("NoteObj.Author")
        assert not re.search(r"\bObj\.(ShowBorder|WordWrap|ClipToRect|TextMargin|Author)\b", branch)
    getter = _routine(code, "SchGetExtra")
    assert "FloatToJsonStr(CoordToMilsF(TF.TextMargin))" in getter
    setter = _routine(code, "SchSetExtra")
    assert "TF.TextMargin := MilsToCoordF(Mils)" in setter
    check = setter.index("If SchExtraIsMils(PropName) Then")
    # Refused before anything is written: not a number, or below 0.
    assert check < setter.index("If Not IsFloatStr(Value) Then Exit;") < setter.index("If Mils < 0 Then Exit;") < setter.index("TF := Obj")
    assert "Result := SchGetExtra(Obj, PropName) = FloatToJsonStr(Mils)" in setter
    assert "TextMargin" in _routine(code, "SchExtraIsMils")


def test_transparent_and_line_style_are_asked_only_of_the_allowed_types():
    code = _source()
    # Only where Altium SAVES it: a round rectangle, text frame and note
    # read Transparent back and lose it on save; frames and notes lose
    # LineStyle the same way (measured on AD 25.8.1).
    assert _types(_routine(code, "SchObjectHasTransparent")) == {
        "eBlanket", "ePolygon", "eRectangle", "eEllipse"}
    assert _types(_routine(code, "SchObjectHasLineStyle")) == {
        "eBlanket", "eLine", "eRectangle"}
    for routine in ("GetSchProperty", "SetSchProperty"):
        body = _routine(code, routine)
        for prop, guard in (("Transparent", "SchObjectHasTransparent(Obj)"),
                            ("LineStyle", "SchObjectHasLineStyle(Obj)")):
            branch = body.split(f"Else If PropName = '{prop}' Then", 1)[1]
            branch = re.split(r"\bElse If PropName\b", branch, maxsplit=1)[0]
            assert branch.index(guard) < branch.index(f"Obj.{prop}"), (routine, prop)


def test_a_bad_font_id_is_refused_not_written_as_font_0_or_1():
    body = _routine(_source(), "SetSchProperty")
    branch = body.split("Else If PropName = 'FontId' Then", 1)[1]
    branch = re.split(r"\bElse If PropName\b", branch, maxsplit=1)[0]
    assert "StrToIntDef(Value, 1)" not in branch
    refuse = branch.index("Else If StrToIntDef(Value, 0) < 1 Then")
    assert refuse < branch.index("WroteOK := False") < branch.index("Obj.FontId := StrToIntDef(Value, 0)")
    assert branch.index("If Not SchObjectHasFontId(Obj) Then") < refuse
