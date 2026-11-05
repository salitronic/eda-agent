# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_create_3d_model: a STEP model from solids read off the drawing.

No package family covers a USB-C receptacle or a LED with a lens, so a
demo run gave those parts extruded boxes. The tool takes the solids the
drawing gives, refuses a model that names no drawing or a malformed
solid, writes the STEP beside the library and links it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.design import models3d
from eda_agent.tools import library as lib_tools

_SHELL = {"kind": "box", "role": "metal", "x0": -4.47, "y0": -3.7, "z0": 0,
          "x1": 4.47, "y1": 3.65, "z1": 3.26}
_LENS = {"kind": "cylinder", "role": "lens", "cx": 0, "cy": 0, "r": 0.7,
         "z0": 0.28, "z1": 0.84}


def test_a_cited_solid_list_plans_a_model():
    plan, problems = models3d.plan_from_solids([_SHELL, _LENS], "HRO TYPE-C-31-M-12 p.1")
    assert problems == [] and plan.from_drawing
    assert plan.height == pytest.approx(3.26)


def test_no_drawing_no_model(tmp_path):
    plan, _ = models3d.plan_from_solids([_SHELL], "")
    out = models3d.write_step(plan, tmp_path / "x.step")
    assert out["ok"] is False and "drawing" in out["reason"]


@pytest.mark.parametrize("bad, word", [
    ({"kind": "cone"}, "kind"),
    ({"kind": "box", "x0": 0, "y0": 0, "z0": 0, "x1": 1, "y1": 1}, "missing z1"),
    (dict(_SHELL, x1=-5), "far edge"),
    (dict(_LENS, role="purple"), "role"),
    ({"kind": "cylinder", "r": 1, "cx": 0, "cy": 0}, "missing z0"),
])
def test_a_malformed_solid_is_named(bad, word):
    plan, problems = models3d.plan_from_solids([_SHELL, bad], "drawing")
    assert plan is None and any(word in p for p in problems)


@pytest.mark.skipif("cadquery" not in models3d.available_writers(),
                    reason="needs CadQuery")
def test_it_writes_a_real_step(tmp_path):
    plan, _ = models3d.plan_from_solids([_SHELL, _LENS], "drawing p.1")
    out = models3d.write_step(plan, tmp_path / "usb.step")
    assert out["ok"], out
    assert Path(out["path"]).read_bytes().startswith(b"ISO-10303-21")


def test_the_tool_writes_beside_the_library_and_links(monkeypatch, tmp_path):
    tools, sent = {}, []

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            if command == "application.get_active_document":
                return {"file_path": str(tmp_path / "Parts.PcbLib")}
            return {"success": True}

    def _write(plan, path, name="part"):
        return {"ok": True, "path": str(path), "writer": "fake"}

    monkeypatch.setattr(lib_tools, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(models3d, "write_step", _write)
    lib_tools.register_library_tools(_Mcp())
    out = asyncio.run(tools["lib_create_3d_model"](
        "USB_C", [_SHELL], "HRO drawing p.1"))
    assert out["ok"] is True
    assert Path(out["path"]) == tmp_path / "3D" / "USB_C.step"
    link = [p for c, p in sent if c == "library.link_3d_model"]
    assert link and link[0]["component_name"] == "USB_C"
