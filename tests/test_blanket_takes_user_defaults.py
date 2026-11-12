# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A placed blanket takes the user's blanket defaults, not an opaque black fill.

MEASURED 2026-11-11 on AD 25.8.1, on a scratch sheet: sch_place_blanket made
its blanket with eCreate_Default and read back Color 0, AreaColor 0,
Transparent false, LineStyle 0. On screen it was a solid black block over
every wire, label and pin name it enclosed, because a new object is drawn
above what is already on the sheet. A blanket drawn in Altium's editor on
the same sheet read Color 255, AreaColor 16777215, Transparent true,
LineStyle 1, and everything inside it stayed visible; writing those values
onto the placed blanket made it look the same. An opaque blanket fill reads
back opaque but comes back transparent after save and reopen, so it is not
offered. The placer now creates the blanket with eCreate_GlobalCopy (the
user's Preferences defaults, as the editor's own Place > Blanket), always
writes Transparent, takes color / area_color, and reads the style back.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import generic

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _placer() -> str:
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(r"(?m)^Function Gen_PlaceBlanket\b", code)
    assert start, "missing Gen_PlaceBlanket"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_blanket_is_made_from_the_users_defaults():
    body = _placer()
    assert "SchObjectFactory(eBlanket, eCreate_GlobalCopy)" in body
    assert "eCreate_Default" not in body


def test_style_is_written_only_when_asked_and_validated_first():
    body = _placer()
    for var, member in (("ColorStr", "Color"), ("AreaStr", "AreaColor")):
        write = f"Blanket.{member} :="
        assert write in body, member
        guard = body[:body.index(write)].rsplit("If ", 1)[1]
        assert guard.startswith(f"{var} <> ''"), member
    refusals = body[:body.index("SchObjectFactory(eBlanket")]
    assert "Not IsIntStr(ColorStr)" in refusals
    assert "Not IsIntStr(AreaStr)" in refusals


def test_the_fill_is_always_written_transparent():
    body = _placer()
    write = body.index("Blanket.Transparent := True")
    assert "If " not in body[body.rindex(";", 0, write):write]
    assert "transparent" not in body[:body.index("SchObjectFactory(eBlanket")].lower().replace(
        "transparent.", "")


def test_the_reply_reads_the_style_back():
    body = _placer()
    reply = body[body.index("BuildSuccessResponse"):]
    for key, read in (('"color":', "Blanket.Color"),
                      ('"area_color":', "Blanket.AreaColor"),
                      ('"transparent":', "Blanket.Transparent"),
                      ('"line_style":', "Blanket.LineStyle")):
        assert key in reply and read in reply, key


class _Bridge:
    def __init__(self, log):
        self.log = log

    async def send_command_async(self, command, params=None, timeout=None):
        self.log.append((command, dict(params or {})))
        return {"success": True}


def _tool(monkeypatch, log):
    monkeypatch.setattr(generic, "get_bridge", lambda: _Bridge(log))
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return tools["sch_place_blanket"]


SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]


def test_style_is_sent_only_when_given(monkeypatch):
    log = []
    tool = _tool(monkeypatch, log)
    asyncio.run(tool(vertices=SQUARE))
    asyncio.run(tool(vertices=SQUARE, color=255, area_color=16777215))
    plain, styled = log[0][1], log[1][1]
    assert not {"color", "area_color", "transparent"} & set(plain)
    assert styled["color"] == "255"
    assert styled["area_color"] == "16777215"
    assert "transparent" not in styled
