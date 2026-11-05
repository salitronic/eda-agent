# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Linking a 3D model replaces the footprint's old body by default.

A follow-up take rebuilt the model of every footprint; each kept its old
plain box beside the new model, and the agent removed them by hand.
"""

from __future__ import annotations

import asyncio

from eda_agent.tools import library


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command == "library.delete_footprint_primitives":
            return {"removed": 1, "leftover": 0}
        return {"success": True}


def _tools(bridge, monkeypatch):
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    library.register_library_tools(_Mcp())
    return tools


def test_the_old_body_goes_before_the_new_one_is_linked(monkeypatch):
    bridge = _Bridge()
    out = asyncio.run(_tools(bridge, monkeypatch)["lib_link_3d_model"](
        "LED_2020", "C:/m/led.step"))
    commands = [c for c, _ in bridge.calls]
    assert commands == ["library.delete_footprint_primitives",
                        "library.link_3d_model"]
    delete = bridge.calls[0][1]
    assert delete["footprint_name"] == "LED_2020"
    assert delete["object_type"] == "component_body" and delete["confirm"] == "true"
    assert out["replaced_bodies"] == {"removed": 1, "leftover": 0}


def test_a_second_model_can_be_added_beside(monkeypatch):
    bridge = _Bridge()
    asyncio.run(_tools(bridge, monkeypatch)["lib_link_3d_model"](
        "LED_2020", "C:/m/lens.step", replace_existing=False))
    assert [c for c, _ in bridge.calls] == ["library.link_3d_model"]


def test_the_current_footprint_is_not_guessed_at(monkeypatch):
    bridge = _Bridge()
    out = asyncio.run(_tools(bridge, monkeypatch)["lib_link_3d_model"](
        "", "C:/m/x.step"))
    assert [c for c, _ in bridge.calls] == ["library.link_3d_model"]
    assert "skipped" in out["replaced_bodies"]


def test_a_model_reports_its_solids_not_just_one_body(monkeypatch, tmp_path):
    """A take read "bodies": 1 as "single plain blocks" and rebuilt eleven
    models that already had terminations and leads."""
    step = tmp_path / "chip.step"
    step.write_text("#1=MANIFOLD_SOLID_BREP('',#2);\n#3=MANIFOLD_SOLID_BREP('',#4);\n"
                    "#5=MANIFOLD_SOLID_BREP('',#6);\n", encoding="latin-1")

    class _B:
        async def send_command_async(self, command, params=None, timeout=None):
            return {"bodies": 1, "body_models": [
                {"model_file": str(step)}, {"model_file": str(tmp_path / "gone.step")}]}

    out = asyncio.run(_tools(_B(), monkeypatch)["lib_get_footprint_primitives"]("C0402"))
    assert [m["step_solids"] for m in out["body_models"]] == [3, None]
