# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The ERC list reaches every violation and says when it stops short.

The script read a ``limit`` (default 100, 0 for all) but the tools always
sent an empty request, so a project with 240 violations answered
violation_count 240 with 100 entries and nothing to say the rest were
missing (issue #93). The tools now pass ``limit`` and ``offset``, and the
reply carries ``returned``, ``truncated`` and ``next_offset``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic as generic_tools

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def test_the_reader_pages_and_reports_the_page():
    body = load(_GENERIC)["Gen_GetErcViolations"]
    assert "ExtractJsonValue(Params, 'offset')" in body
    assert "For I := Offset To VCount - 1 Do" in body
    stop = body.index("Truncated := True;")
    assert body.index("NextOffset := I;") > stop
    assert '"truncated":true,"next_offset":' in body
    assert '"truncated":false' in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self, reply):
        self.reply = reply
        self.params = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.params.append((command, dict(params or {})))
        return dict(self.reply)


def _tools(monkeypatch, bridge):
    monkeypatch.setattr(generic_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    generic_tools.register_generic_tools(mcp)
    return mcp.tools


def test_the_tools_send_limit_and_offset(monkeypatch):
    bridge = _Bridge({"violation_count": 0, "violations": [],
                      "returned": 0, "truncated": False})
    tools = _tools(monkeypatch, bridge)
    asyncio.run(tools["proj_get_erc_violations"](limit=0, offset=100))
    asyncio.run(tools["proj_run_erc"](limit=0))
    assert bridge.params[0] == ("generic.get_erc_violations",
                                {"limit": "0", "offset": "100",
                                 "include_suppressed": "false"})
    assert bridge.params[1] == ("generic.run_erc", {"limit": "0",
                                                    "include_suppressed": "false"})


def test_a_partial_page_carries_a_warning(monkeypatch):
    bridge = _Bridge({"violation_count": 240, "violations": [{}] * 100,
                      "returned": 100, "truncated": True, "next_offset": 100})
    out = asyncio.run(_tools(monkeypatch, bridge)["proj_get_erc_violations"]())
    assert "100 of 240" in out["warning"]


def test_an_old_script_reply_is_marked_partial(monkeypatch):
    """A script from before the change sends no ``returned``; the cap
    must still show on the reply."""
    bridge = _Bridge({"violation_count": 240, "violations": [{}] * 100})
    out = asyncio.run(_tools(monkeypatch, bridge)["proj_get_erc_violations"]())
    assert out["returned"] == 100
    assert out["truncated"] is True
    assert "warning" in out


def test_a_complete_list_has_no_warning(monkeypatch):
    bridge = _Bridge({"violation_count": 2, "violations": [{}, {}]})
    out = asyncio.run(_tools(monkeypatch, bridge)["proj_get_erc_violations"]())
    assert out["truncated"] is False
    assert "warning" not in out
