# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A board text's font, kind, style and justification, on create and after.

pcb_place_text placed a stroke text in Altium's default font and nothing
else: no stroke font, no TrueType, no bold or italic, and no way to anchor
a text on a point other than its bottom left. Altium justifies a text only
when it is multiline (inside its frame); a single-line text ignores the
setting. Measured on AD 25: the frame's corner is the text's location, and
Altium grows a frame smaller than the text plus margin when the edit is
closed, so the text is placed again from the frame it really got. On an
inverted (label) text the filled box is the frame; a size written to the
inverted rectangle itself is ignored, so that size is read only.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_PCB = _ALTIUM / "PCB.pas"
_GENERIC = _ALTIUM / "PCBGeneric.pas"


def _place():
    return load(_PCB)["PCB_PlaceText"]


def _generic(name):
    return load(_GENERIC, minimum=20)[name]


# ---------------------------------------------------------------- generic

def test_new_text_properties_belong_to_a_text():
    body = _generic("ExtraPCBPropertyOwner")
    for name in ("TextKind", "FontID", "Bold", "Italic", "InvRectWidth", "InvRectHeight"):
        assert "(PropName = '%s')" % name in body
    owner = body[body.index("(PropName = 'FontName')"):]
    assert owner.index("Then Result := 3") < owner.index("Then Result := 4")


def test_new_text_properties_are_read():
    body = _generic("PCBGetExtra")
    assert "PropName = 'TextKind' Then Result := IntToStr(Txt.TextKind)" in body
    assert "PropName = 'FontID' Then Result := IntToStr(Txt.FontID)" in body
    assert "PropName = 'Bold' Then Result := BoolToJsonStr(Txt.Bold)" in body
    assert "PropName = 'Italic' Then Result := BoolToJsonStr(Txt.Italic)" in body
    assert "PropName = 'InvRectWidth' Then Result := FloatToJsonStr(CoordToMilsF(Txt.InvRectWidth))" in body
    assert "PropName = 'InvRectHeight' Then Result := FloatToJsonStr(CoordToMilsF(Txt.InvRectHeight))" in body


def test_kind_font_and_style_are_written_and_checked():
    body = _generic("PCBSetExtra")
    kind = body[body.index("Else If PropName = 'TextKind' Then"):]
    kind = kind[:kind.index("End")]
    assert "If N > 2 Then Exit;" in kind
    assert "Txt.TextKind := N;" in kind and "Txt.UseTTFonts := N = 1;" in kind
    font = body[body.index("Else If PropName = 'FontID' Then"):]
    font = font[:font.index("End")]
    assert "If (N < 1) Or (N > 3) Then Exit;" in font and "Txt.FontID := N;" in font
    name = body[body.index("Else If PropName = 'FontName' Then"):]
    name = name[:name.index("End")]
    assert "If Trim(Value) = '' Then Exit;" in name and "Txt.FontName := Value;" in name
    assert "Else If PropName = 'Bold' Then Txt.Bold := B" in body
    assert "Else If PropName = 'Italic' Then Txt.Italic := B" in body


def test_value_types_are_parsed_as_their_kind():
    body = _generic("PCBSetExtra")
    is_bool = body[body.index("IsBool :="):body.index("IsText :=")]
    assert "(PropName = 'Bold') Or (PropName = 'Italic')" in is_bool
    is_text = body[body.index("IsText :="):body.index("IsInt :=")]
    assert "(PropName = 'FontName')" in is_text
    is_int = body[body.index("IsInt :="):body.index("B := False;")]
    assert "(PropName = 'TextKind') Or" in is_int and "(PropName = 'FontID')" in is_int


def test_the_inverted_box_size_is_read_only():
    body = _generic("PCBSetExtra")
    read_only = body[:body.index("Owner := ExtraPCBPropertyOwner(PropName);")]
    assert "(PropName = 'InvRectWidth') Or (PropName = 'InvRectHeight') Then Exit;" in read_only
    assert "Txt.InvRectWidth :=" not in body and "Txt.InvRectHeight :=" not in body


def test_new_properties_are_advertised():
    text = _GENERIC.read_text(encoding="utf-8")
    assert "+ 'TextKind, FontID, Bold, Italic, InvRectWidth, InvRectHeight, '" in text


# ---------------------------------------------------------------- create

def test_justification_names_map_to_altiums_order():
    body = load(_PCB)["TextJustifyCode"]
    order = ["top_left", "center_left", "bottom_left", "top_center", "center",
             "bottom_center", "top_right", "center_right", "bottom_right"]
    for code, name in enumerate(order, start=1):
        assert "Name = '%s' Then Result := %d" % (name, code) in body
    assert "Else Result := -1;" in body


def test_the_longest_line_is_measured_line_by_line():
    body = load(_PCB)["TextLongestLineMils"]
    # one line at a time: a single-line text draws CR LF on one row
    assert "P := Pos(#10, Rest);" in body
    assert "If Copy(Line, Length(Line), 1) = #13 Then" in body
    one = body.index("TextObj.Text := Line;")
    assert one < body.index("Box := TextObj.BoundingRectangle;")
    assert "W := CoordToMilsF(Box.Right - Box.Left);" in body
    assert "If W > Result Then Result := W;" in body
    # the full text put back after the walk
    assert body.index("TextObj.Text := S;") > body.index("If W > Result Then Result := W;")


def test_justification_fractions():
    fx = load(_PCB)["TextJustifyFracX"]
    assert "If Code >= 7 Then Result := 1" in fx and "Else If Code >= 4 Then Result := 0.5" in fx
    fy = load(_PCB)["TextJustifyFracY"]
    assert "Row := (Code - 1) Mod 3;" in fy
    assert "If Row = 0 Then Result := 1" in fy and "Else If Row = 1 Then Result := 0.5" in fy


def test_a_justified_text_is_framed_and_placed_from_the_frame_it_got():
    body = _place()
    frame = body.index("TextObj.Multiline := True;")
    # no width given: as wide as the longest line, measured (+ 1 mil); a
    # 1 mil frame made Altium wrap every line at its spaces
    measure = body.index("FrameW := TextLongestLineMils(TextObj, TextStr);")
    assert body.index("If FrameW <= 0 Then", 0, measure) < measure < frame
    assert body.index("If FrameW > 0 Then FrameW := FrameW + 1 Else FrameW := 1;") < frame
    assert body.index("If FrameH <= 0 Then FrameH := 1;") < frame
    assert "BoxW" not in body
    assert body.index("TextObj.MultilineTextAutoPosition := Just;") > frame
    end = body.index("TextObj.EndModify;", frame)
    regrow = body.index("If (TextObj.MultilineTextWidth <> MilsToCoordF(FrameW)) Or", end)
    again = body[regrow:body.index("TextObj.GraphicallyInvalidate;", regrow)]
    assert "FrameW := CoordToMilsF(TextObj.MultilineTextWidth);" in again
    assert "TextObj.XLocation := MilsToCoordF(TX - (FX * Cos(Ang) - FY * Sin(Ang)));" in again
    assert "TextObj.YLocation := MilsToCoordF(TY - (FX * Sin(Ang) + FY * Cos(Ang)));" in again


def test_stroke_font_and_kind_map_and_refuse():
    body = _place()
    assert "Else If StrokeFontStr = 'sans_serif' Then StrokeFontNo := 2" in body
    assert "Else If StrokeFontStr = 'serif' Then StrokeFontNo := 3" in body
    assert "Else If KindStr = 'truetype' Then Kind := 1" in body
    assert "If StrokeFontNo > 0 Then TextObj.FontID := StrokeFontNo;" in body
    assert "TextObj.UseTTFonts := Kind = 1;" in body


def test_truetype_only_settings_are_refused_on_a_stroke_text():
    body = _place()
    assert ("If (Kind = 0) And ((BoldF = 1) Or (ItalicF = 1) Or (InvF = 1) Or\n"
            "       (FontNameStr <> '')) Then") in body
    assert "If (Kind = 1) And (StrokeFontNo > 0) Then" in body
    assert "If ((FrameWStr <> '') Or (FrameHStr <> '')) And (Just = 0) Then" in body


def test_refusals_come_before_anything_is_made():
    body = _place()
    make = body.index("PCBServer.PCBObjectFactory(eTextObject")
    for err in ("'stroke_font is for a stroke text, not kind truetype'",
                "'a frame needs a justification: it is where the text sits in it'",
                "'frame_width, frame_height and margin are mils, not below zero'"):
        assert body.index(err) < make


def test_the_reply_is_read_off_the_text():
    body = _place()
    for field in ('"kind":\' + IntToStr(TextObj.TextKind)',
                  '"font_id":\' + IntToStr(TextObj.FontID)',
                  '"multiline":\' + BoolToJsonStr(TextObj.Multiline)',
                  '"justification":\' + IntToStr(TextObj.MultilineTextAutoPosition)',
                  '"frame_width":\' + FloatToJsonStr(CoordToMilsF(TextObj.MultilineTextWidth))',
                  '"margin":\' + FloatToJsonStr(CoordToMilsF(TextObj.InvertedTTTextBorder))'):
        assert field in body


# ---------------------------------------------------------------- the tool

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"placed": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    asyncio.run(mcp.tools["pcb_place_text"](**kw))
    return bridge.sent


def test_a_plain_text_sends_what_it_always_sent(monkeypatch):
    sent = _call(monkeypatch, text="A", x=1, y=2)
    assert sent == [("pcb.place_text", {"text": "A", "x": "1", "y": "2", "layer": "TopOverlay",
                                        "height": "60", "rotation": "0"})]


def test_style_and_justification_reach_the_script(monkeypatch):
    sent = _call(monkeypatch, text="L", x=0, y=0, kind="truetype", font_name="Arial",
                 bold=True, italic=False, justification="center", frame_width=600,
                 frame_height=150, inverted=True, margin=10)
    p = sent[0][1]
    assert p["kind"] == "truetype" and p["font_name"] == "Arial"
    assert p["bold"] == "true" and p["italic"] == "false" and p["inverted"] == "true"
    assert p["justification"] == "center"
    assert p["frame_width"] == "600" and p["frame_height"] == "150" and p["margin"] == "10"


def test_a_stroke_font_and_a_zero_margin_are_sent(monkeypatch):
    p = _call(monkeypatch, text="S", x=0, y=0, stroke_font="sans_serif", margin=0)[0][1]
    assert p["stroke_font"] == "sans_serif" and p["margin"] == "0"
    assert "bold" not in p and "frame_width" not in p
