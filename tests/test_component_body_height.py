# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A placed component's height is its tallest 3D body.

pcb_get_components reported only the footprint's Height parameter, a
number typed by hand that is often 0 or out of step with the model, and
the bodies' heights were reachable only by querying every body and
grouping them by part. Now every reader of a component's height takes
the tallest body, STEP or extruded, when the part has one, and the
parameter only when it has none; the parameter is still reported as
typed, beside the source and a mismatch flag.
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


def test_the_guard_prefers_bodies_over_the_parameter():
    body = _generic()["ComponentGuardedHeight"]
    bodies = body.index("If Count > 0 Then Result := MaxOverall")
    assert bodies < body.index("Result := Comp.Height;")


def test_the_generic_height_of_a_component_is_guarded():
    body = _generic()["GetPCBProperty"]
    assert "ComponentGuardedHeight(Comp)" in body
    raw = body.index("Else If PropName = 'ComponentHeight' Then")
    assert "Comp.Height" in body[raw:raw + 200]


def test_component_height_is_advertised_and_a_length():
    gen = _generic()
    assert "ComponentHeight" in gen["KnownPCBPropertyList"]
    assert "(PropName = 'ComponentHeight')" in gen["IsLengthProperty"]


def test_get_components_reports_the_guarded_height():
    body = load(_PCB)["PCB_GetComponents"]
    assert "ComponentBodyScan(Comp, WantList, MaxOverall, BodyCount)" in body
    guarded = body.index("If BodyCount > 0 Then")
    assert body.index("HeightMils := MaxMils;") > guarded
    for key in ('"height_source":"3d_bodies",', '"height_source":"component",',
                '"component_height_mils":', '"body_count":', '"height_mismatch":',
                '"max_body_height_mils":'):
        assert key in body
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
