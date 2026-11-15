# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_set_via_soldermask_relief writes through the via's pad cache.

Measured on AD 26.10.1.6, one via on a scratch board, each write in its
own call: assigning SolderMaskExpansion straight onto the IPCB_Via crashed
the scripting engine, as it had twice in 2026-09. SolderMaskExpansionFromHoleEdge assigned on the via
took, and the expansion written into the via's TPadCache (GetState_Cache,
change, SetState_Cache) took on both sides and read back the same through
the via and through a pad-typed variable. The tool had been refusing
since the first crash; it works again by the route that does not fault.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.metadata import tool_metadata
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"vias_matched": 1, "vias_verified": 1}


@pytest.fixture()
def tool_and_bridge(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: bridge)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    tools = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    return tools["pcb_set_via_soldermask_relief"], bridge


def test_the_tool_sends_the_value_and_the_net(tool_and_bridge):
    tool, bridge = tool_and_bridge
    asyncio.run(tool(expansion_mils=3.5, net=" GND "))
    assert bridge.sent == [("pcb.set_via_soldermask_relief",
                            {"expansion_mils": "3.5", "net": "GND"})]


def test_it_is_advertised_as_changing_the_board():
    assert tool_metadata("pcb_set_via_soldermask_relief")["interaction"] == "silent"


def test_no_handler_assigns_the_expansion_onto_a_via():
    """The write that faults, anywhere it could come back."""
    offenders = []
    for pas in ALTIUM.glob("*.pas"):
        if pas.name == "Altium_MCP.pas":
            continue
        text = pas.read_text(encoding="utf-8-sig", errors="replace")
        for m in re.finditer(r"\b(Via\w*)\.SolderMaskExpansion\s*:=", text):
            offenders.append(f"{pas.name}: {m.group(0)}")
    assert not offenders, offenders


def test_both_sides_are_written_and_read_back():
    body = load(ALTIUM / "PCB.pas", minimum=100)["PCB_SetViaSoldermaskRelief"]
    assert "Cache.SolderMaskBottomExpansion := Exp;" in body
    assert "Cache.SolderMaskExpansionValid := eCacheManual;" in body
    tail = body[body.index("Via.SetState_Cache := Cache;"):]
    assert "Cache := Via.GetState_Cache;" in tail
    assert "Verified := Verified + 1" in tail
