# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the design_layout_schematic MCP tool.

No Altium: the layout engine is pure Python, so the tool computes its
result entirely offline. These lock down the tool's contract (data
shape, validation rejection) and that it stays Altium-free.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import design as design_module


def _capture_tools():
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    design_module.register_design_tools(DummyMcp())
    return captured


def _plan_dict() -> dict:
    return {
        "spec": "regulator",
        "summary": "ic plus local passives and rails",
        "sheets": [{"name": "main"}],
        "zones": [{"name": "reg", "sheet": "main", "role": "mcu"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "REG_IC", "zone": "reg"},
            {"refdes": "C1", "lib_ref": "CAP", "value": "10uF", "zone": "reg"},
            {"refdes": "R1", "lib_ref": "RES", "value": "10k", "zone": "reg"},
            {"refdes": "J1", "lib_ref": "CONN", "role": "input"},
        ],
        "nets": [
            {"name": "VIN", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"},
                {"refdes": "U1", "pin": "1"},
                {"refdes": "C1", "pin": "1"},
            ]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"},
                {"refdes": "U1", "pin": "2"},
                {"refdes": "C1", "pin": "2"},
                {"refdes": "R1", "pin": "2"},
            ]},
            {"name": "FB", "pins": [
                {"refdes": "U1", "pin": "3"},
                {"refdes": "R1", "pin": "1"},
            ]},
        ],
    }


@pytest.mark.asyncio
async def test_layout_returns_positions_and_net_representation():
    tool = _capture_tools()["design_layout_schematic"]
    out = await tool(plan_dict := _plan_dict())

    assert out["ok"] is True
    assert out["sheet"] == "main"
    placed = {p["designator"] for p in out["placements"]}
    assert placed == {"U1", "C1", "R1", "J1"}
    for p in out["placements"]:
        # NOT asserted on the 100-mil grid, and the reason is the point
        # of the engine swap: this coordinate is the symbol's BODY
        # CENTRE, and the canvas pipeline grid-aligns a symbol by its
        # PINS instead (an origin snap leaves pins off-grid on a library
        # whose pin coordinates carry odd 50s, and an off-grid pin does
        # not bond to a wire in Altium). The retired neat engine snapped
        # centres, so this used to hold by accident of which engine ran.
        assert isinstance(p["x"], int) and isinstance(p["y"], int)
        assert p["rotation"] in (0, 90, 180, 270)

    rep = out["net_representation"]
    # Power / ground go to port glyphs.
    assert rep.get("VIN") == "power_port"
    assert rep.get("GND") == "power_port"
    # Every decided net is one of the three kinds.
    assert set(rep.values()) <= {"wire", "net_label", "power_port"}
    assert "total" in out["score"]
    # One-line verdict synthesising the score + representation.
    s = out["summary"].lower()
    assert isinstance(out["summary"], str) and s
    assert "crossing" in s and "part(s)" in s and "nets as" in s


@pytest.mark.asyncio
async def test_layout_is_deterministic():
    tool = _capture_tools()["design_layout_schematic"]
    a = await tool(_plan_dict())
    b = await tool(_plan_dict())
    assert a == b


@pytest.mark.asyncio
async def test_layout_accepts_json_string():
    import json
    tool = _capture_tools()["design_layout_schematic"]
    out = await tool(json.dumps(_plan_dict()))
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_layout_rejects_bad_plan():
    tool = _capture_tools()["design_layout_schematic"]
    out = await tool({"spec": "x"})  # missing required fields
    assert out["ok"] is False
    assert out["errors"]


@pytest.mark.asyncio
async def test_layout_honours_placement_hints():
    tool = _capture_tools()["design_layout_schematic"]
    out = await tool(
        _plan_dict(),
        placement_hints={"U1": {"x": 5000, "y": 5000, "rotation": 90}},
    )
    u1 = next(p for p in out["placements"] if p["designator"] == "U1")
    assert u1["rotation"] == 90


@pytest.mark.asyncio
async def test_layout_render_png_writes_preview(tmp_path):
    tool = _capture_tools()["design_layout_schematic"]
    out_png = tmp_path / "preview.png"
    out = await tool(_plan_dict(), render_png=str(out_png))
    assert out["ok"] is True
    # The data result is unchanged; the preview path is added on success.
    assert out.get("preview_png") == str(out_png)
    assert "preview_error" not in out
    from PIL import Image
    assert out_png.exists() and out_png.stat().st_size > 1000
    with Image.open(out_png) as im:
        im.verify()


@pytest.mark.asyncio
async def test_layout_render_png_failure_does_not_break_data(tmp_path):
    tool = _capture_tools()["design_layout_schematic"]
    # An impossible path triggers a render error but the data must survive.
    bad = tmp_path / "no\x00such" / "x.png"  # NUL in path -> OSError on write
    out = await tool(_plan_dict(), render_png=str(bad))
    assert out["ok"] is True
    assert "placements" in out and "score" in out
    assert "preview_error" in out and "preview_png" not in out
