# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A placed component reports both its heights and chooses neither.

pcb_get_components reported only the footprint's Height field, a number
typed by hand that is often 0 or out of step with the model, and a body's
height was reachable only by querying every body. Either number can be the
wrong one for a part, so both are given: height_mils, the Height field as
entered, and model_height_mils, the tallest 3D body (STEP or extruded),
with a mismatch flag. The generic properties are Height and ModelHeight.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_PCB = _ALTIUM / "PCB.pas"
_GENERIC = _ALTIUM / "PCBGeneric.pas"


def _generic():
    return load(_GENERIC, minimum=20)


def test_the_scan_walks_only_bodies_and_keeps_the_tallest():
    body = _generic()["ComponentBodyScan"]
    assert "GIter.AddFilter_ObjectSet(MkSet(eComponentBodyObject));" in body
    assert "If (Count = 0) Or (Overall > MaxOverall) Then MaxOverall := Overall;" in body
    assert "Comp.GroupIterator_Destroy(GIter);" in body


def test_a_listed_body_names_its_kind_and_heights():
    body = _generic()["ComponentBodyScan"]
    for key in ('"identifier":"', '","kind":"', '","layer":"',
                '","standoff_mils":', ',"overall_mils":'):
        assert key in body


def test_step_and_extruded_bodies_are_told_apart():
    body = _generic()["ComponentBodyKind"]
    assert "If Kind = 0 Then Result := 'extruded'" in body
    assert "Else If Kind = 1 Then Result := 'step'" in body


def test_the_model_height_is_the_tallest_body_or_nothing():
    body = _generic()["ComponentModelHeightJson"]
    assert "ComponentBodyScan(Comp, False, MaxOverall, Count, StepCount);" in body
    assert "If Count > 0 Then Result := FloatToJsonStr(CoordToMilsF(MaxOverall))" in body
    assert "Else Result := '';" in body
    assert "Comp.Height" not in body


def test_the_generic_height_is_the_field_and_model_height_the_body():
    body = _generic()["GetPCBProperty"]
    at = body.index("Else If PropName = 'Height' Then")
    assert "Result := FloatToJsonStr(CoordToMilsF(Comp.Height));" in body[at:at + 500]
    model = body.index("Else If PropName = 'ModelHeight' Then")
    assert "ComponentModelHeightJson(Comp)" in body[model:model + 200]


def test_model_height_is_advertised_and_a_length():
    gen = _generic()
    assert "ModelHeight" in gen["KnownPCBPropertyList"]
    assert "(PropName = 'ModelHeight')" in gen["IsLengthProperty"]
    assert "ComponentHeight" not in _GENERIC.read_text(encoding="utf-8-sig")


def test_get_components_reports_both_heights_and_picks_neither():
    body = load(_PCB)["PCB_GetComponents"]
    assert "ComponentBodyScan(Comp, WantList, MaxOverall, BodyCount, StepCount)" in body
    assert "HeightMils := ParamMils;" in body
    assert "HeightMils := MaxMils" not in body
    for key in ('"model_height_mils":', '"model_height_mils":null', '"body_count":',
                '"height_mismatch":'):
        assert key in body
    for gone in ("height_source", "component_height_mils", "max_body_height_mils"):
        assert gone not in body
    assert "If WantList Then HeightJson := HeightJson + '\"bodies\":['" in body


def test_an_unknown_mode_is_refused():
    body = load(_PCB)["PCB_GetComponents"]
    assert "'BAD_BODY_HEIGHTS'" in body
    assert "If BodyMode = '' Then BodyMode := 'max';" in body


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
        return {"success": True, "components": [], "count": 0}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_get_components"](**kw))
    return out, bridge.sent


def test_max_is_the_default(monkeypatch):
    _, sent = _call(monkeypatch)
    assert sent == [("pcb.get_components", {"body_heights": "max"})]


def test_all_reaches_the_script(monkeypatch):
    _, sent = _call(monkeypatch, body_heights=" ALL ")
    assert sent == [("pcb.get_components", {"body_heights": "all"})]


def test_an_unknown_mode_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, body_heights="tallest")
    assert out["ok"] is False and not sent
