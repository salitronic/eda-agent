# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A model's terminals are placed from the footprint's lands.

Three times in one take a row of terminals was modelled as one long
strip under the body, where the part has a separate terminal on every
land. The model tool now places one terminal per land from the
footprint, and refuses a flat terminal-coloured box that spans lands of
two names.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design import models3d
from eda_agent.tools import library as lib_tools

_LANDS = [{"name": "1", "x": -1.0, "y": 0.0, "w": 0.3, "h": 0.8, "shape": "rect", "rotation": 0},
          {"name": "2", "x": 0.0, "y": 0.0, "w": 0.3, "h": 0.8, "shape": "rect", "rotation": 90},
          {"name": "3", "x": 1.0, "y": 0.0, "w": 0.4, "h": 0.4, "shape": "circle", "rotation": 0}]
_BODY = {"kind": "box", "role": "body", "x0": -1.5, "y0": -1, "z0": 0.05,
         "x1": 1.5, "y1": 1, "z1": 0.9}


def test_a_plate_on_every_land_where_the_land_is():
    solids, problems = models3d.terminal_solids(_LANDS, "plate", 0.05)
    assert problems == [] and len(solids) == 3
    a, b, c = solids
    assert (a.x0, a.x1, a.y0, a.y1) == pytest.approx((-1.15, -0.85, -0.4, 0.4))
    assert (b.x1 - b.x0, b.y1 - b.y0) == pytest.approx((0.8, 0.3))  # turned 90
    assert c.kind == "cylinder" and c.r == pytest.approx(0.2)


def test_balls_sit_with_their_top_at_the_height():
    solids, _ = models3d.terminal_solids(_LANDS, "ball", 0.25, diameter=0.3)
    assert all(s.kind == "sphere" and s.cz + s.r == pytest.approx(0.25) for s in solids)
    assert models3d.terminal_solids(_LANDS, "ball", 0.25)[1]  # no diameter


def test_a_strip_across_lands_is_named_but_a_can_is_not():
    strip = models3d.Solid("box", "gold", x0=-1.2, y0=-0.4, z0=0, x1=1.2, y1=0.4, z1=0.05)
    can = models3d.Solid("box", "metal", x0=-1.5, y0=-1, z0=0, x1=1.5, y1=1, z1=1.2)
    one = models3d.Solid("box", "gold", x0=-1.15, y0=-0.4, z0=0, x1=-0.85, y1=0.4, z1=0.05)
    problems = models3d.strip_terminals([strip, can, one], _LANDS)
    assert len(problems) == 1 and "solid 0" in problems[0] and "1, 2, 3" in problems[0]


def _tool(monkeypatch, tmp_path, lands):
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
            if command == "library.get_pad_geometry":
                return {"pads": [{"name": l["name"], "x_mm": l["x"], "y_mm": l["y"],
                                  "w_mm": l["w"], "h_mm": l["h"], "shape": l["shape"],
                                  "rotation": l["rotation"]} for l in lands]}
            return {"success": True}

    written = []

    def _write(plan, path, name="part"):
        written.append(plan)
        return {"ok": True, "path": str(path), "writer": "fake"}

    monkeypatch.setattr(lib_tools, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(models3d, "write_step", _write)
    lib_tools.register_library_tools(_Mcp())
    return tools["lib_create_3d_model"], sent, written


def test_the_tool_places_terminals_from_the_footprint(monkeypatch, tmp_path):
    run, sent, written = _tool(monkeypatch, tmp_path, _LANDS)
    out = asyncio.run(run("LGA3", [_BODY], "drawing p.1",
                          terminals={"kind": "plate", "height": 0.05},
                          drawing={"body_x": 3.0, "body_y": 2.0, "height": 0.9}))
    assert out["ok"] and out["terminals"] == 3
    assert all(r["ok"] for r in out["drawing_dims"])
    assert [c for c, _ in sent].count("library.get_pad_geometry") == 1
    assert sum(s.role == "gold" for s in written[0].solids) == 3


def test_the_tool_refuses_a_strip(monkeypatch, tmp_path):
    run, sent, written = _tool(monkeypatch, tmp_path, _LANDS)
    strip = {"kind": "box", "role": "gold", "x0": -1.2, "y0": -0.4, "z0": 0,
             "x1": 1.2, "y1": 0.4, "z1": 0.05}
    out = asyncio.run(run("LGA3", [_BODY, strip], "drawing p.1"))
    assert not out["ok"] and "terminals" in out["problems"][0]
    assert written == [] and not any(c == "library.link_3d_model" for c, _ in sent)
