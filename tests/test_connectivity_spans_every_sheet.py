# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A multi-part component's pins take their nets from every sheet it is on.

In the compiled model each sheet holding a part of the component carries a
component of its own, and answers a net only for that part's pins ("?" for
the rest). Connectivity used to read the first sheet only, so every pin of
a part placed elsewhere came back "?", wired or not.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import project as project_tools

from tests.pascal_source import load

_PROJECT = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Project.pas"


def _units():
    return load(_PROJECT, minimum=5)


def test_both_readers_share_one_walk():
    units = _units()
    assert "ConnectivityEntries(Project, Wanted, Matched)" in units["Proj_GetConnectivity"]
    assert "ConnectivityEntries(Project, Wanted, MatchedDesigs)" in units["Proj_GetConnectivityBatch"]
    for name in ("Proj_GetConnectivity", "Proj_GetConnectivityBatch"):
        assert "DM_FlattenedNetName" not in units[name], name


def test_an_unresolved_pin_takes_the_net_another_sheet_resolved():
    body = _units()["ConnectivityEntries"]
    collect = body.index("If StrToIntDef(Counts.Values[Desig], 0) < 2 Then Continue;")
    write = body.index("Matched.Add(Desig);")
    assert collect < write
    assert "If (Net = '') Or (Net = '?') Or (Pos('=', PinNum) > 0) Then Continue;" in body
    fill = body[write:]
    assert "If Multi And ((Net = '') Or (Net = '?'))" in fill
    assert "Other := Nets.Values[Desig + '|' + PinNum];" in fill


def test_the_reply_lists_every_sheet():
    body = _units()["ConnectivityEntries"]
    assert "',\"sheets\":[' + SheetsBy.Values[Desig] + ']'" in body
    assert "',\"sheet\":\"' + EscapeJsonString(Doc.DM_FileName)" in body


def test_the_entries_routine_is_defined_before_its_callers():
    raw = _PROJECT.read_text(encoding="utf-8-sig")
    at = raw.index("Function ConnectivityEntries(")
    assert at < raw.index("Function Proj_GetConnectivity(")
    assert at < raw.index("Function Proj_GetConnectivityBatch(")


# --- the filters a large module needs ----------------------------------------

_PINS = [{"pin_number": "D10", "pin_name": "USB_C_D_P", "net": "EXT_USB1_D_P"},
         {"pin_number": "D11", "pin_name": "USB_C_D_N", "net": "?"},
         {"pin_number": "A1", "pin_name": "GND", "net": "GND"}]


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    async def send_command_async(self, command, params=None, timeout=None):
        entry = {"designator": "U20", "comment": "", "sheet": "a", "sheets": ["a", "b"],
                 "pin_count": 3, "pins": [dict(p) for p in _PINS]}
        if command == "project.get_connectivity_batch":
            return {"components": [entry], "matched": 1, "requested": 1, "not_found": []}
        return entry


def _tool(monkeypatch, name, **kw):
    monkeypatch.setattr(project_tools, "get_bridge", lambda: _Bridge())
    mcp = _Mcp()
    project_tools.register_project_tools(mcp)
    return asyncio.run(mcp.tools[name](**kw))


def test_pins_by_number_or_name(monkeypatch):
    out = _tool(monkeypatch, "proj_get_connectivity", designator="U20", pins=["d10", "GND"])
    assert [p["pin_number"] for p in out["pins"]] == ["D10", "A1"]
    assert out["pins_left_out"] == 1


def test_connected_only_drops_pins_on_no_net(monkeypatch):
    out = _tool(monkeypatch, "proj_get_connectivity_many", designators=["U20"],
                connected_only=True)
    (comp,) = out["components"]
    assert [p["pin_number"] for p in comp["pins"]] == ["D10", "A1"]
    assert comp["pins_left_out"] == 1


def test_no_filter_leaves_the_reply_whole(monkeypatch):
    out = _tool(monkeypatch, "proj_get_connectivity", designator="U20")
    assert len(out["pins"]) == 3 and "pins_left_out" not in out
