# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Board vias can be tented, the way library vias already could.

Only lib_set_via_properties set tenting, so a via placed on a board with
pcb_place_via could not be tented at all; the workaround was a solder
mask rule, which changes every via. Measured on AD 26.11, the tenting
flag is kept only while the via's mask expansion is manual, so the tool
switches a rule-driven via to manual first, through the pad cache, and
reads the flags back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_SetViaTenting"]


def test_the_mask_goes_manual_before_the_flag_is_written():
    body = _body()
    manual = body.index("Cache.SolderMaskExpansionValid := eCacheManual;")
    assert manual < body.index("Via.SetState_IsTenting_Top(")
    assert manual < body.index("Via.SetState_IsTenting_Bottom(")
    assert "Via.SetState_Cache := Cache;" in body


def test_each_flag_is_read_back():
    body = _body()
    assert "If Via.GetState_IsTenting_Top <> (TopStr = 'true') Then Ok := False;" in body
    assert "If Via.GetState_IsTenting_Bottom <> (BotStr = 'true') Then Ok := False;" in body


def test_nothing_is_written_during_the_board_walk():
    body = _body()
    walk_end = body.index("Board.BoardIterator_Destroy(Iterator);")
    assert body.index("PcbBeginEdit(0);") > walk_end
    assert "SetState_IsTenting" not in body[:walk_end]


def test_the_mask_value_is_never_assigned_on_the_via():
    """Assigning SolderMaskExpansion on an IPCB_Via faults the engine."""
    assert "Via.SolderMaskExpansion :=" not in _body()


def test_footprint_vias_are_left_to_the_library_tool():
    body = _body()
    assert "If Via.InComponent Then" in body
    assert '"footprint_vias_skipped":' in body


def test_the_command_is_routed():
    assert "'set_via_tenting': Result := PCB_SetViaTenting(Params, RequestId);" in \
        _PCB.read_text(encoding="utf-8")


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
        return {"success": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_set_via_tenting"](**kw))
    return out, bridge.sent


def test_points_and_sides_reach_the_script(monkeypatch):
    _, sent = _call(monkeypatch, points=[{"x": 14710, "y": 5783.2256}],
                    top=True, bottom=True)
    assert sent == [("pcb.set_via_tenting", {
        "points": "14710.0,5783.2256", "top": "true", "bottom": "true"})]


def test_a_side_left_out_is_not_sent(monkeypatch):
    _, sent = _call(monkeypatch, net="GND", bottom=False)
    assert sent[0][1] == {"net": "GND", "bottom": "false"}


def test_nothing_to_set_or_no_vias_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, net="GND")
    assert out["ok"] is False and not sent
    out, sent = _call(monkeypatch, top=True)
    assert out["ok"] is False and not sent
