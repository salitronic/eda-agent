# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_set_rule_properties sets polygon/plane connect styles and exact widths.

Asked for on a 6-layer board: Polygon Connect had to become Direct Connect
and no tool could set it, and 0.09 mm (3.543 mil) widths could not be said
in the integer mils the width fields took.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body():
    return load(_ALTIUM / "PCB.pas")["PCB_SetRuleProperties"]


def test_widths_take_mm_exactly_and_mils_with_decimals():
    body = _body()
    for var in ("MinW", "MaxW", "FavW"):
        assert f"If {var}MM <> '' Then WCoord := MMToCoord(StrToFloatDef({var}MM, 0))" in body
        assert f"Else WCoord := MilsToCoordF(StrToFloatDef({var}Str, 0));" in body
    assert "Or (MinWMM <> '') Or (MaxWMM <> '') Or (FavWMM <> '')) Then" in body


def test_connect_settings_go_through_the_sdk_setters():
    body = _body()
    for call in ("RulePolyConn.SetState_ConnectStyle(StyleNo);",
                 "RulePolyConn.SetState_ReliefAirGap(",
                 "RulePolyConn.SetState_ReliefConductorWidth(",
                 "RulePolyConn.SetState_ReliefEntries(",
                 "RulePolyConn.SetState_PolygonReliefAngle(",
                 "RulePlaneConn.SetState_PlaneConnectStyle(StyleNo);",
                 "RulePlaneConn.SetState_ReliefExpansion("):
        assert call in body, call
    # Typed iterator locals, assigned straight from the iterator.
    assert "RulePolyConn := Iter.FirstPCBObject;" in body
    assert "RulePlaneConn := Iter.FirstPCBObject;" in body


def test_the_style_is_confirmed_from_the_descriptor_not_a_property():
    body = _body()
    assert "Desc := Rule.Descriptor;" in body
    assert "connect_style_confirmed" in body
    # No read through the property spellings DelphiScript did not know.
    assert ".ConnectStyle" not in body.replace("SetState_ConnectStyle", "")


def test_settings_on_the_wrong_rule_are_refused_with_a_reason():
    body = _body()
    assert "connect settings apply to a Polygon Connect (kind 20)" in body
    assert "Nothing was written." in body


def test_the_tool_forwards_the_new_fields(monkeypatch):
    from eda_agent.tools import pcb as pcb_tools
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, dict(params or {})))
            return {}

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    pcb_tools.register_pcb_tools(_Mcp())
    asyncio.run(tools["pcb_set_rule_properties"](
        "PolygonConnect", connect_style="direct", air_gap_mm=0.25, entries=4))
    asyncio.run(tools["pcb_set_rule_properties"]("Width", min_width_mm=0.09))
    assert sent[0][1]["connect_style"] == "direct"
    assert sent[0][1]["air_gap_mm"] == "0.25" and sent[0][1]["entries"] == "4"
    assert sent[1][1]["min_width_mm"] == "0.09"


def test_a_differential_pair_rule_takes_widths_and_gaps_on_every_layer():
    body = _body()
    for setter in ("SetState_MinWidth", "SetState_MaxWidth", "SetState_PreferedWidth",
                   "SetState_MinGap", "SetState_MaxGap", "SetState_PreferedGap"):
        assert f"RuleDiff.{setter}(L, WCoord);" in body, setter
    assert "RuleDiff := Iter.FirstPCBObject;" in body
    assert "If (RuleDiff.RuleKind = 51) And (RuleDiff.Name = RuleName) Then" in body
    assert "diff_pair_descriptor" in body


def test_a_connect_rule_can_be_created_with_its_style():
    body = load(_ALTIUM / "PCB.pas")["PCB_CreateDesignRule"]
    assert "RulePolyC := PCBServer.PCBRuleFactory(20);" in body
    assert "RulePolyC.SetState_ConnectStyle(StyleNo);" in body
    assert "RulePlaneC := PCBServer.PCBRuleFactory(6);" in body
    assert "RulePlaneC.SetState_PlaneConnectStyle(StyleNo);" in body
    # Direct unless asked otherwise: the common case is vias, never relieved.
    assert "StyleNo := 1;" in body
    assert "eRule_PolygonConnect" not in body, "an unproven enum name halts the loop"
