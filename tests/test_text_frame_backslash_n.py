# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A backslash followed by n in a text frame's text is text, unless typed
for a line break.

The script used to turn every backslash-n into a line break, so a frame
holding a Windows path (C:\\new\\board), a net such as \\net or a regex was
split into lines and lost the backslash. The bridge carries real newlines
(JSON), so the script now keeps backslash-n as sent and only
sch_place_text_frame turns a TYPED one into a newline: for a text that has
no real newline, and not when typed_breaks is False.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eda_agent.tools import generic
from eda_agent.tools.generic import typed_line_breaks

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, name: str) -> str:
    text = (ALTIUM / unit).read_text(encoding="utf-8-sig")
    at = text.index(f"Function {name}(")
    return text[at:text.index("\nEnd;", at)]


def _code(body: str) -> str:
    """The routine without its { } comments, which may quote a path."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif not depth:
            out.append(ch)
    return "".join(out)


def test_the_script_keeps_a_backslash_n_as_text():
    code = _code(_routine("Generic.pas", "Gen_PlaceTextFrame"))
    assert "'\\' + 'n'" not in code
    assert "'\\n'" not in code
    assert "StringReplace(TextStr, #10, #13 + #10" in code


@pytest.mark.parametrize("text, wanted", [
    ("Line one\\nLine two", "Line one\nLine two"),            # typed break
    ("Line one\nLine two", "Line one\nLine two"),             # real break
    ("See C:\\new\\board\nRev A", "See C:\\new\\board\nRev A"),  # path + real break
    ("See C:\\new\\board\r\nRev A", "See C:\\new\\board\r\nRev A"),
    ("See C:\\new\\board\rRev A", "See C:\\new\\board\rRev A"),
    ("no break", "no break"),
])
def test_only_a_text_without_newlines_takes_typed_breaks(text, wanted):
    assert typed_line_breaks(text) == wanted


def test_typed_breaks_false_keeps_a_one_line_path():
    assert typed_line_breaks("C:\\new\\board", False) == "C:\\new\\board"
    assert typed_line_breaks("A\\nB", False) == "A\\nB"
    # The documented default: in a one-line text a typed \n is a break.
    assert typed_line_breaks("C:\\new\\board") == "C:" + "\n" + "ew\\board"


class _Bridge:
    def __init__(self):
        self.sent = {}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent[command] = params
        return {"success": True}


def _tool(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

    generic.register_generic_tools(_Server())
    return tools["sch_place_text_frame"], bridge


def _sent_text(monkeypatch, **kwargs):
    place, bridge = _tool(monkeypatch)
    asyncio.run(place(x1=0, y1=0, x2=1000, y2=500, **kwargs))
    return bridge.sent["generic.place_text_frame"]["text"]


def test_the_tool_sends_a_path_as_typed_when_asked(monkeypatch):
    sent = _sent_text(monkeypatch, text="C:\\new\\board", typed_breaks=False)
    assert sent == "C:\\new\\board"
    # On the wire the backslash is escaped, so the script's JSON unescape
    # gives the same two characters back, never a newline.
    assert json.dumps(sent) == '"C:\\\\new\\\\board"'


def test_the_tool_keeps_a_path_in_a_text_with_newlines(monkeypatch):
    text = "Board: C:\\new\\board\nRev A"
    assert _sent_text(monkeypatch, text=text) == text


def test_the_tool_still_turns_a_typed_break_into_a_newline(monkeypatch):
    assert _sent_text(monkeypatch, text="A\\nB") == "A\nB"
