# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Basic 3D models are reported unless the Config tab allows them.

A basic 3D model is a footprint whose bodies are only extruded boxes,
cylinders or spheres, with no STEP model among them. With "Basic 3D models"
at Not allowed, the default, the footprint checks and the board's component
list name each one and tell the AI to ask the user before replacing it, and
a new footprint gets no extruded box in place of a STEP model it could not
build.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

import eda_agent.tools.library as lib
from eda_agent import preferences
from eda_agent.tools import pcb as pcb_tools
from eda_agent.tools import register_all_tools

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _allow(monkeypatch, allowed: bool) -> None:
    monkeypatch.setattr(preferences, "basic_3d_models_allowed",
                        lambda workspace=None: allowed)


_FOOTPRINTS = [
    {"name": "BOXED", "pads": [], "primitives": [], "bodies": 2, "step_bodies": 0},
    {"name": "MODELLED", "pads": [], "primitives": [], "bodies": 1, "step_bodies": 1},
    {"name": "BARE", "pads": [], "primitives": [], "bodies": 0, "step_bodies": 0},
]


class _LibBridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == "library.get_library_geometry":
            return {"library_path": "Demo.PcbLib", "total": len(_FOOTPRINTS),
                    "offset": 0, "count": len(_FOOTPRINTS),
                    "footprints": [dict(f) for f in _FOOTPRINTS]}
        if command == "library.check_pcblib":
            return {"footprints_checked": 3, "footprints_with_problems": 0,
                    "footprints": []}
        return {}


def _call(tool, args, monkeypatch):
    bridge = _LibBridge()
    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool(tool, args))
    c = r[0] if isinstance(r, tuple) else r
    return json.loads(c[0].text), bridge


def test_the_library_read_counts_step_bodies():
    body = load(_ALTIUM / "Library.pas")["Lib_GetLibraryGeometry"]
    assert "If ModelKind = 1 Then Inc(StepCount);" in body
    assert "',\"step_bodies\":' + IntToStr(StepCount)" in body


def test_the_board_read_counts_step_bodies():
    scan = load(_ALTIUM / "PCBGeneric.pas", minimum=20)["ComponentBodyScan"]
    assert "If Kind = 'step' Then Inc(StepCount);" in scan
    body = load(_ALTIUM / "PCB.pas")["PCB_GetComponents"]
    assert "'\"step_body_count\":' + IntToStr(StepCount)" in body


def test_the_audit_names_only_footprints_without_a_step_model(monkeypatch):
    _allow(monkeypatch, False)
    rep, _ = _call("lib_audit_footprint_policies", {}, monkeypatch)
    assert rep["basic_3d_models"]["footprints"] == ["BOXED"]
    assert "ask" in rep["basic_3d_models"]["ask_the_user"]


def test_the_audit_says_nothing_when_basic_models_are_allowed(monkeypatch):
    _allow(monkeypatch, True)
    rep, _ = _call("lib_audit_footprint_policies", {}, monkeypatch)
    assert "basic_3d_models" not in rep


def test_the_library_check_names_them(monkeypatch):
    _allow(monkeypatch, False)
    rep, bridge = _call("lib_check_pcblib", {}, monkeypatch)
    assert rep["footprints_checked"] == 3
    assert rep["basic_3d_models"]["footprints"] == ["BOXED"]
    rep, _ = _call("lib_check_pcblib", {"footprint_name": "MODELLED"}, monkeypatch)
    assert "basic_3d_models" not in rep


def test_the_library_check_reads_no_more_when_allowed(monkeypatch):
    _allow(monkeypatch, True)
    rep, bridge = _call("lib_check_pcblib", {}, monkeypatch)
    assert "basic_3d_models" not in rep
    assert bridge.calls == ["library.check_pcblib"]


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _BoardBridge:
    async def send_command_async(self, command, params=None, timeout=None):
        return {"success": True, "count": 3, "components": [
            {"designator": "U2", "footprint": "SOIC8", "body_count": 1,
             "step_body_count": 0},
            {"designator": "U1", "footprint": "SOIC8", "body_count": 1,
             "step_body_count": 0},
            {"designator": "C1", "footprint": "C0402", "body_count": 1,
             "step_body_count": 1},
        ]}


def _components(monkeypatch):
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _BoardBridge())
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools["pcb_get_components"]())


def test_a_board_names_the_footprints_and_their_parts(monkeypatch):
    _allow(monkeypatch, False)
    flag = _components(monkeypatch)["basic_3d_models"]
    assert flag["footprints"] == ["SOIC8"]
    assert flag["parts"] == {"SOIC8": ["U1", "U2"]}
    assert "pcb_update_footprints_from_library" in flag["ask_the_user"]


def test_a_board_says_nothing_when_allowed(monkeypatch):
    _allow(monkeypatch, True)
    assert "basic_3d_models" not in _components(monkeypatch)


class _BodyBridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append(command)
        return {"added": 1}


async def _step(_name, coro):
    return await coro


def _body(monkeypatch, allowed):
    _allow(monkeypatch, allowed)
    bridge = _BodyBridge()
    model = SimpleNamespace(body=SimpleNamespace(x0=-1, y0=-1, x1=1, y1=1,
                                                 z0=0, z1=1))
    out = asyncio.run(lib._footprint_3d_body(
        bridge, _step, SimpleNamespace(family="sot"), model, "SOT23", "extruded"))
    return out, bridge.sent


def test_a_new_footprint_gets_no_box_when_basic_models_are_not_allowed(monkeypatch):
    out, sent = _body(monkeypatch, False)
    assert out["kind"] == "none" and not sent
    assert "Basic 3D models" in out["note"] and "lib_create_3d_model" in out["note"]


def test_a_new_footprint_gets_its_box_when_allowed(monkeypatch):
    out, sent = _body(monkeypatch, True)
    assert out["kind"] == "extruded"
    assert sent == ["library.add_footprint_bodies"]


def test_tool_guide_answers_how_to_replace_them():
    from eda_agent.tools.guidance import guidance_for
    for question in ("replace the basic 3D models with real ones",
                     "footprint has an extruded box as its model"):
        top = guidance_for(question)["recipes"][0]
        assert top["task"].startswith("Find and replace basic 3D models"), question
        assert "lib_create_3d_model" in top["use"]
