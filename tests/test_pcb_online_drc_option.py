# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Online DRC through pcb_edit_options.

Bulk board edits are slowed a lot by online DRC checking every object as
it is placed, so a script switches it off for the run and puts the user's
setting back afterwards. That needs it read and set by name: the tool
maps ``online_drc`` to Altium's ``DoOnlineDRC``, and the script knows it
as a switch, reads it and writes it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(name: str) -> str:
    text = (ALTIUM / "PCB.pas").read_text(encoding="latin-1")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True, "options": {"DoOnlineDRC": False},
                "not_applied": [], "refused": []}


def _edit_options(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    monkeypatch.setattr("eda_agent.tools.editor_options.get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Mcp())
    return captured["pcb_edit_options"], bridge


def test_online_drc_is_named_doonlinedrc():
    assert pcb._PCB_EDIT_OPTIONS["online_drc"] == "DoOnlineDRC"


def test_script_knows_online_drc_as_a_switch_it_reads_and_writes():
    kind = _body("PcbOptionKind")
    # Listed in the switch group, the one that ends in Result := 'b'.
    switches = kind[:kind.index("Then Result := 'b'")]
    assert "(Name = 'DoOnlineDRC')" in switches
    assert ("Else If Name = 'DoOnlineDRC' Then Result := BoolToJsonStr(Opts.DoOnlineDRC)"
            in _body("PcbOptionGet"))
    assert ("Else If Name = 'DoOnlineDRC' Then Opts.DoOnlineDRC := B"
            in _body("PcbOptionSet"))


def test_switching_online_drc_off_sends_its_altium_name(monkeypatch):
    tool, bridge = _edit_options(monkeypatch)
    result = asyncio.run(tool(online_drc=False))
    command, params = bridge.sent[0]
    assert command == "pcb.editor_options"
    assert params["set"] == "DoOnlineDRC=false"
    assert "DoOnlineDRC" in params["names"].split(",")
    assert result["options"] == {"online_drc": False}


def test_reading_asks_for_online_drc_and_sets_nothing(monkeypatch):
    tool, bridge = _edit_options(monkeypatch)
    asyncio.run(tool())
    _command, params = bridge.sent[0]
    assert "set" not in params
    assert "DoOnlineDRC" in params["names"].split(",")
