# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A sheet's title block and border can be switched through sheet options.

sch_set_sheet_options writes TitleBlockOn and BorderOn with
ISch_Document's SetState_TitleBlockOn and SetState_BorderOn, in the same
edit as the other options, and reads both back with them, so one that
did not take is in not_applied.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import generic

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _handler() -> str:
    return _flat(load(_GENERIC)["Gen_SheetOptions"])


def _options() -> str:
    return _flat(load(_GENERIC)["SheetOptionsJson"])


def test_both_flags_are_taken_from_the_request():
    body = _handler()
    assert "TitleBlock := LowerCase(ExtractJsonValue(Params, 'title_block_on'));" in body
    assert "SheetBorder := LowerCase(ExtractJsonValue(Params, 'border_on'));" in body
    assert "Or (TitleBlock <> '') Or (SheetBorder <> '')" in body


def test_both_flags_are_written_inside_the_edit():
    body = _handler()
    begin = body.index("SchBeginEdit(SchDoc, '');")
    end = body.index("SchEndEdit(SchDoc, 'Edit');")
    title = body.index("If TitleBlock <> '' Then SchDoc.SetState_TitleBlockOn(TitleBlock = 'true');")
    border = body.index("If SheetBorder <> '' Then SchDoc.SetState_BorderOn(SheetBorder = 'true');")
    assert begin < title < end
    assert begin < border < end


def test_both_flags_are_read_back_with_the_options():
    body = _options()
    assert "Try TitleBlock := SchDoc.TitleBlockOn; Except End;" in body
    assert "Try SheetBorder := SchDoc.BorderOn; Except End;" in body
    assert "JsonBool('title_block_on', TitleBlock)" in body
    assert "JsonBool('border_on', SheetBorder)" in body


class _Bridge:
    def __init__(self, options):
        self.sent = []
        self.options = options

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True, "changed": bool(params), "options": self.options}


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


def test_the_tool_sends_the_flags_only_when_given(monkeypatch):
    _, sent = _call(monkeypatch, {"title_block_on": False, "border_on": True},
                    title_block_on=False, border_on=True)
    assert sent == [("generic.sheet_options",
                     {"title_block_on": "false", "border_on": "true"})]
    _, sent = _call(monkeypatch, {"title_block_on": True},
                    title_block_on=True)
    assert sent == [("generic.sheet_options", {"title_block_on": "true"})]
    _, sent = _call(monkeypatch, {}, zones_x=6)
    assert sent == [("generic.sheet_options", {"zones_x": "6"})]


def test_a_flag_that_did_not_take_is_named(monkeypatch):
    reply, _ = _call(monkeypatch, {"title_block_on": True, "border_on": True},
                     title_block_on=False, border_on=True)
    assert reply["not_applied"] == ["title_block_on"]
    reply, _ = _call(monkeypatch, {"title_block_on": False, "border_on": False},
                     title_block_on=False, border_on=False)
    assert reply["not_applied"] == []
