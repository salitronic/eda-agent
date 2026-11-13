# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A text frame can be placed already styled.

generic.place_text_frame always made an unfilled frame with the default
border colour and margin. Drawing a table of filled cells meant placing
every cell, finding the ones just placed by a filter that a user's own
frame could also match, and restyling them. The fill, border and text
colours, the border switch, word wrap, clip and text margin are now
optional arguments, written before the frame is registered and read back
off the frame in the reply. Left out, the frame comes out as before.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import generic

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _routines():
    return load(_GENERIC, minimum=20)


def _body():
    return _routines()["Gen_PlaceTextFrame"]


# --- the Pascal handler ----------------------------------------------------------

def test_the_style_is_written_before_the_frame_is_registered():
    body = _body()
    register = body.index("SchDoc.RegisterSchObjectInContainer(TF)")
    for write in ("TF.AreaColor := WantFill", "TF.Color := WantBorderColor",
                  "TF.TextColor := WantTextColor",
                  "TF.ShowBorder := (WantShowBorder = 1)",
                  "TF.WordWrap := (WantWrap = 1)",
                  "TF.ClipToRect := (WantClip = 1)",
                  "TF.TextMargin := MilsToCoordF(WantMargin)"):
        assert write in body, write
        assert body.index(write) < register, write


def test_the_asked_style_comes_after_the_defaults_so_it_wins():
    body = _body()
    for default, asked in (("TF.IsSolid := False", "TF.IsSolid := True"),
                           ("TF.ShowBorder := True", "TF.ShowBorder := (WantShowBorder = 1)"),
                           ("TF.WordWrap := True", "TF.WordWrap := (WantWrap = 1)"),
                           ("TF.ClipToRect := True", "TF.ClipToRect := (WantClip = 1)")):
        assert body.index(default) < body.index(asked), asked


def test_a_fill_makes_the_frame_solid_and_only_when_asked():
    body = _body()
    guard = body.index("If WantFill >= 0 Then")
    assert guard < body.index("TF.IsSolid := True") < body.index("TF.AreaColor := WantFill")


def test_each_style_write_is_guarded_by_its_own_argument():
    body = _body()
    for guard, write in (("If WantBorderColor >= 0 Then", "TF.Color := WantBorderColor"),
                         ("If WantTextColor >= 0 Then", "TF.TextColor := WantTextColor"),
                         ("If WantShowBorder >= 0 Then", "TF.ShowBorder := (WantShowBorder = 1)"),
                         ("If WantWrap >= 0 Then", "TF.WordWrap := (WantWrap = 1)"),
                         ("If WantClip >= 0 Then", "TF.ClipToRect := (WantClip = 1)"),
                         ("If WantMargin >= 0 Then", "TF.TextMargin := MilsToCoordF(WantMargin)")):
        at = body.index(guard)
        assert body.index(write, at) - at < 120, write


def test_bad_values_are_refused_before_anything_is_made():
    body = _body()
    made = body.index("SchServer.SchObjectFactory(eTextFrame")
    for code in ("'BAD_COLOR'", "'BAD_FLAG'", "'BAD_MARGIN'"):
        assert body.index(code) < made, code


def test_every_style_argument_is_checked_by_its_refusal():
    body = _body()
    for code, names in (("'BAD_COLOR'", ("WantFill", "WantBorderColor", "WantTextColor")),
                        ("'BAD_FLAG'", ("WantShowBorder", "WantWrap", "WantClip"))):
        at = body.index(code)
        condition = body[body.rindex("If (", 0, at):at]
        for name in names:
            assert f"({name} = -2)" in condition, (code, name)


def test_the_reply_reads_the_style_back_off_the_frame():
    body = _body()
    assert "TextFrameStyleJson(TF)" in body
    assert body.index("SchEndEdit(SchDoc, 'Edit')") < body.index("TextFrameStyleJson(TF)")
    reader = _routines()["TextFrameStyleJson"]
    for read in ("TF.IsSolid", "TF.AreaColor", "TF.Color)", "TF.TextColor",
                 "TF.ShowBorder", "TF.WordWrap", "TF.ClipToRect",
                 "CoordToMilsF(TF.TextMargin)"):
        assert read in reader, read


def test_the_style_stays_apart_from_the_line_break_conversion():
    # Another change rewrites the "\n" handling; the style must not lean on it.
    body = _body()
    last_break = body.index("StringReplace(TextStr, #10, #13 + #10")
    assert body.index("TextFrameColorArg(Params, 'area_color')") > last_break


# --- the two argument readers, compiled and run under Free Pascal ----------------

def _fpc():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def _run(tmp_path: Path, calls: list[str]) -> list[str]:
    fpc = _fpc()
    text = _GENERIC.read_text(encoding="utf-8-sig")
    sources = []
    for name in ("TextFrameColorArg", "TextFrameFlagArg"):
        at = text.index(f"Function {name}(")
        sources.append(text[at:text.index("\nEnd;", at) + len("\nEnd;")])
    program = "\n".join([
        "program tfargs;", "{$mode delphi}{$H+}", "uses SysUtils;",
        # The real ExtractJsonValue reads a JSON object; the stand-in reads
        # "key=value" lines, which is all these two routines ask of it.
        "Function ExtractJsonValue(Params, Key : String) : String;",
        "Var P, E : Integer;",
        "Begin",
        "    Result := '';",
        "    P := Pos(#10 + Key + '=', #10 + Params);",
        "    If P = 0 Then Exit;",
        "    Result := Copy(Params, P + Length(Key) + 1, MaxInt);",
        "    E := Pos(#10, Result);",
        "    If E > 0 Then Result := Copy(Result, 1, E - 1);",
        "End;",
        *sources,
        "Begin",
        *calls,
        "End.",
    ])
    path = tmp_path / "tfargs.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, "-O1", str(path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("tfargs.exe" if (tmp_path / "tfargs.exe").exists() else "tfargs")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout.split()


@pytest.mark.parametrize("given, expected", [
    (None, -1), ("", -1), ("0", 0), ("13421772", 13421772), ("16777215", 16777215),
    ("16777216", -2), ("-1", -2), ("#CCCCCC", -2), ("12ab", -2),
    ("99999999999", -2), ("123456789", -2), (" 255 ", 255),
])
def test_a_colour_argument_is_a_packed_colour_or_refused(tmp_path, given, expected):
    params = "x1=0" if given is None else f"x1=0\ncolor={given}"
    out = _run(tmp_path, [f"WriteLn(TextFrameColorArg('{params}', 'color'));"
                          .replace("\n", "' + #10 + '")])
    assert out == [str(expected)]


@pytest.mark.parametrize("given, expected", [
    (None, -1), ("true", 1), ("TRUE", 1), ("1", 1), ("false", 0), ("0", 0),
    ("yes", -2), ("2", -2),
])
def test_a_switch_argument_is_true_false_or_refused(tmp_path, given, expected):
    params = "x1=0" if given is None else f"x1=0\nshow_border={given}"
    out = _run(tmp_path, [f"WriteLn(TextFrameFlagArg('{params}', 'show_border'));"
                          .replace("\n", "' + #10 + '")])
    assert out == [str(expected)]


# --- the MCP tool -----------------------------------------------------------------

class _Bridge:
    def __init__(self, reply=None):
        self.sent: list[tuple[str, dict]] = []
        self.reply = reply if reply is not None else {"placed": True}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, dict(params or {})))
        return self.reply


def _tool(monkeypatch, reply=None):
    bridge = _Bridge(reply)
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return tools["sch_place_text_frame"], bridge


def test_without_style_arguments_nothing_new_is_sent(monkeypatch):
    place, bridge = _tool(monkeypatch)
    asyncio.run(place(x1=0, y1=0, x2=1000, y2=500, text="a"))
    (command, params), = bridge.sent
    assert command == "generic.place_text_frame"
    assert set(params) == {"x1", "y1", "x2", "y2", "text", "align", "font_id"}


def test_the_style_goes_to_the_bridge_as_altiums_values(monkeypatch):
    place, bridge = _tool(monkeypatch)
    asyncio.run(place(x1=0, y1=0, x2=1000, y2=350, text="Rev", fill="#CCCCCC",
                      border_color="#000001", text_color="#102030",
                      show_border=True, word_wrap=False, clip_to_rect=False,
                      text_margin=25))
    (_, params), = bridge.sent
    assert params["area_color"] == str(0xCCCCCC)
    assert params["color"] == str(0x010000)          # #000001: blue in the top byte
    assert params["text_color"] == str(0x302010)
    assert params["show_border"] == "true"
    assert params["word_wrap"] == "false"
    assert params["clip_to_rect"] == "false"
    assert params["text_margin"] == "25"


@pytest.mark.parametrize("kwargs", [
    {"fill": "CCCCCC0"}, {"border_color": "#GGGGGG"}, {"text_color": "red"},
    {"text_margin": -1},
])
def test_a_bad_style_value_is_refused_before_the_bridge(monkeypatch, kwargs):
    place, bridge = _tool(monkeypatch)
    reply = asyncio.run(place(x1=0, y1=0, x2=10, y2=10, text="a", **kwargs))
    assert reply["ok"] is False
    assert bridge.sent == []


def test_the_read_back_colours_come_back_as_rgb(monkeypatch):
    place, _ = _tool(monkeypatch, {"placed": True, "style": {
        "is_solid": True, "area_color": 0xCCCCCC, "color": 0x010000,
        "text_color": 0x302010, "text_margin": 25.0}})
    reply = asyncio.run(place(x1=0, y1=0, x2=10, y2=10, text="a", fill="#CCCCCC"))
    style = reply["style"]
    assert style["area_color"] == "#CCCCCC" and style["area_color_bgr"] == 0xCCCCCC
    assert style["color"] == "#000001" and style["color_bgr"] == 0x010000
    assert style["text_color"] == "#102030"
    assert style["text_margin"] == 25.0
