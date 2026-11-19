# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Where a sheet counts its zones from, its document font and its colours
can be set through sheet options.

sch_set_sheet_options writes ISch_Document's ReferenceZoneStyle (0 from
the upper left, 1 from the bottom right), SystemFont (a font id),
AreaColor (the sheet colour) and Color (the border and zone frame
colour) in the same edit as the other options, and reads all four back
with them, so one that did not take is in not_applied. A font id that
names no font is refused before anything is written. Colours go in and
come back as "#RRGGBB", with Altium's BGR integer beside them.

The small routines that turn the words and numbers into Altium's values
are lifted out of Generic.pas and run under Free Pascal; the order of
the checks and writes is read from the source.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import generic

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _routines() -> dict[str, str]:
    return load(_GENERIC)


def _handler() -> str:
    return _flat(_routines()["Gen_SheetOptions"])


def _options() -> str:
    return _flat(_routines()["SheetOptionsJson"])


# -- the conversions, compiled by Free Pascal --------------------------------

_WHOLE = [  # (text, low, high) -> SheetWholeInRange
    ("0", 0, 16777215), ("16777215", 0, 16777215), ("16317695", 0, 16777215),
    ("16777216", 0, 16777215), ("-1", 0, 16777215), ("", 0, 16777215),
    ("12a", 0, 16777215), ("$FF", 0, 16777215), ("+5", 0, 16777215),
    ("000000001", 0, 16777215), ("99999999999", 0, 16777215),
    ("1", 1, 99999999), ("0", 1, 99999999), ("99999999", 1, 99999999),
]


def _run(tmp_path) -> dict[str, str]:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    code = _routines()
    lines = []
    for word in ("upper_left", "bottom_right", "top_left", "Upper_Left", ""):
        lines.append(f"  Writeln('style[{word}]|' + IntToStr(SheetZoneStyleOf('{word}')));")
    for style in (0, 1, -1, 2, 7):
        lines.append(f"  Writeln('name[{style}]|' + SheetZoneOriginName({style}));")
    for text, low, high in _WHOLE:
        lines.append(f"  Writeln('whole[{text},{low},{high}]|' + "
                     f"BoolToStr(SheetWholeInRange('{text}', {low}, {high}), True));")
    program = "\n".join([
        "program sheetoptions;", "{$mode delphi}", "uses SysUtils;",
        code["SheetZoneStyleOf"], code["SheetZoneOriginName"],
        code["SheetWholeInRange"],
        "begin", *lines, "end.",
    ])
    path = tmp_path / "sheetoptions.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True,
                           text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("sheetoptions.exe" if os.name == "nt" else "sheetoptions")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    out = {}
    for line in ran.stdout.splitlines():
        key, value = line.split("|", 1)
        out[key] = value
    assert len(out) == 5 + 5 + len(_WHOLE), out
    return out


def test_the_two_zone_origins_map_to_altiums_styles_and_back(tmp_path):
    out = _run(tmp_path)
    assert out["style[upper_left]"] == "0"
    assert out["style[bottom_right]"] == "1"
    # The handler lower-cases the word first; anything else is refused.
    for word in ("top_left", "Upper_Left", ""):
        assert out[f"style[{word}]"] == "-1", word
    assert out["name[0]"] == "upper_left"
    assert out["name[1]"] == "bottom_right"
    assert out["name[-1]"] == ""          # not read
    assert out["name[2]"] == "2"          # a style this does not know
    assert out["name[7]"] == "7"


def test_colours_and_font_ids_are_checked_by_their_characters_first(tmp_path):
    out = _run(tmp_path)
    taken = {(t, lo, hi) for t, lo, hi in _WHOLE
             if out[f"whole[{t},{lo},{hi}]"] == "True"}
    assert taken == {("0", 0, 16777215), ("16777215", 0, 16777215),
                     ("16317695", 0, 16777215), ("1", 1, 99999999),
                     ("99999999", 1, 99999999)}


# -- the handler, read from the source ----------------------------------------

def test_the_four_values_are_taken_from_the_request():
    body = _handler()
    for line in (
            "ZoneOrigin := LowerCase(Trim(ExtractJsonValue(Params, 'zone_origin')));",
            "DocFont := Trim(ExtractJsonValue(Params, 'document_font_id'));",
            "SheetColour := Trim(ExtractJsonValue(Params, 'sheet_color'));",
            "BorderColour := Trim(ExtractJsonValue(Params, 'border_color'));"):
        assert line in body, line
    assert ("Or (ZoneOrigin <> '') Or (DocFont <> '') Or (SheetColour <> '') "
            "Or (BorderColour <> '');") in body


def test_a_bad_value_is_refused_before_anything_is_written():
    body = _handler()
    begin = body.index("SchBeginEdit(SchDoc, '');")
    checks = [
        "If (ZoneOrigin <> '') And (SheetZoneStyleOf(ZoneOrigin) < 0) Then",
        "(Not SheetWholeInRange(SheetColour, 0, 16777215))",
        "(Not SheetWholeInRange(BorderColour, 0, 16777215))",
        "If (DocFont <> '') And (Not SheetWholeInRange(DocFont, 1, 99999999)) Then",
        "If (DocFont <> '') And (SheetFontJson(StrToIntDef(DocFont, 0)) = 'null') Then",
    ]
    for check in checks:
        at = body.index(check)
        assert at < begin, check
        assert "BuildErrorResponse(RequestId, 'INVALID_VALUE'" in body[at:at + 260], check
        assert "Exit;" in body[at:at + 400], check
    # the range is checked before the font table is asked
    assert body.index(checks[3]) < body.index(checks[4])


def test_the_four_values_are_written_inside_the_edit():
    body = _handler()
    begin = body.index("SchBeginEdit(SchDoc, '');")
    end = body.index("SchEndEdit(SchDoc, 'Edit');")
    for write in (
            "If ZoneOrigin <> '' Then SchDoc.SetState_ReferenceZoneStyle(SheetZoneStyleOf(ZoneOrigin));",
            "If DocFont <> '' Then SchDoc.SetState_SystemFont(StrToIntDef(DocFont, 1));",
            "If SheetColour <> '' Then SchDoc.AreaColor := StrToIntDef(SheetColour, 0);",
            "If BorderColour <> '' Then SchDoc.Color := StrToIntDef(BorderColour, 0);"):
        at = body.index(write)
        assert begin < at < end, write
    assert body.index("SchDoc.UpdateDocumentProperties;") > end


def test_the_four_values_are_read_back_with_the_options():
    body = _options()
    for line in ("Try ZoneStyle := SchDoc.GetState_ReferenceZoneStyle; Except End;",
                 "Try DocFont := SchDoc.GetState_SystemFont; Except End;",
                 "Try SheetColour := SchDoc.AreaColor; Except End;",
                 "Try BorderColour := SchDoc.Color; Except End;",
                 "JsonStr('zone_origin', SheetZoneOriginName(ZoneStyle))",
                 "JsonInt('document_font_id', DocFont)",
                 "JsonRaw('document_font', SheetFontJson(DocFont))",
                 "JsonInt('sheet_color', SheetColour)",
                 "JsonInt('border_color', BorderColour)"):
        assert line in body, line


def test_the_font_is_described_from_the_font_table_or_null():
    body = _flat(_routines()["SheetFontJson"])
    assert body.index("Result := 'null';") < body.index("GetFontSpec(")
    assert ("FontMgr.GetFontSpec(FontId, Size, Rotation, Underline, Italic, Bold, "
            "StrikeOut, FontName);") in body
    assert body.index("If FontName = '' Then Exit;") < body.index("Result := JsonObj(")
    for key in ("font_name", "size", "rotation", "bold", "italic", "underline",
                "strikeout"):
        assert f"'{key}'" in body, key


# -- the tool, with a stand-in bridge ------------------------------------------

class _Bridge:
    def __init__(self, options):
        self.sent = []
        self.options = options

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True, "changed": bool(params), "options": dict(self.options)}


def _call(monkeypatch, options, **kwargs):
    bridge = _Bridge(options)
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    reply = asyncio.run(captured["sch_set_sheet_options"](**kwargs))
    return reply, bridge.sent


_READ = {"zone_origin": "upper_left", "document_font_id": 3,
         "document_font": {"font_name": "Arial", "size": 10},
         "sheet_color": 16317695, "border_color": 0}


def test_the_tool_sends_each_value_only_when_given(monkeypatch):
    _, sent = _call(monkeypatch, _READ, zone_origin="Bottom Right")
    assert sent == [("generic.sheet_options", {"zone_origin": "bottom_right"})]
    _, sent = _call(monkeypatch, _READ, document_font_id=3)
    assert sent == [("generic.sheet_options", {"document_font_id": "3"})]
    _, sent = _call(monkeypatch, _READ, sheet_color="#FF0000", border_color="#0000ff")
    assert sent == [("generic.sheet_options",
                     {"sheet_color": "255", "border_color": "16711680"})]
    _, sent = _call(monkeypatch, _READ, sheet_color=" ", zone_origin="")
    assert sent == [("generic.sheet_options", {})]
    _, sent = _call(monkeypatch, _READ)
    assert sent == [("generic.sheet_options", {})]


@pytest.mark.parametrize("kwargs", [
    {"zone_origin": "top_left"},
    {"zone_origin": "centre"},
    {"document_font_id": 0},
    {"document_font_id": 100_000_000},
    {"sheet_color": "FFFFF"},
    {"sheet_color": "#GG0000"},
    {"border_color": "16317695"},
])
def test_a_bad_value_is_refused_and_nothing_is_sent(monkeypatch, kwargs):
    reply, sent = _call(monkeypatch, _READ, **kwargs)
    assert sent == []
    assert reply["ok"] is False
    assert next(iter(kwargs)) in reply["reason"]


def test_a_value_that_did_not_take_is_named(monkeypatch):
    reply, _ = _call(monkeypatch, _READ, zone_origin="bottom_right",
                     document_font_id=3, sheet_color="#FFFFFF",
                     border_color="#000000")
    assert reply["not_applied"] == ["zone_origin", "sheet_color"]
    taken = dict(_READ, zone_origin="bottom_right", sheet_color=16777215)
    reply, _ = _call(monkeypatch, taken, zone_origin="bottom_right",
                     document_font_id=3, sheet_color="#FFFFFF",
                     border_color="#000000")
    assert reply["not_applied"] == []
    reply, _ = _call(monkeypatch, _READ, document_font_id=4, border_color="#010000")
    assert reply["not_applied"] == ["document_font_id", "border_color"]


def test_colours_come_back_as_rrggbb_with_the_bgr_integer(monkeypatch):
    reply, _ = _call(monkeypatch, _READ)
    got = reply["options"]
    assert got["sheet_color"] == "#FFFCF8" and got["sheet_color_bgr"] == 16317695
    assert got["border_color"] == "#000000" and got["border_color_bgr"] == 0
    assert got["document_font"] == {"font_name": "Arial", "size": 10}
    assert "not_applied" not in reply
