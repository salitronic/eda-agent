# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The EDA-agnostic engineering calculators exposed to the KiCad backend.
Deterministic physics, no live EDA."""

from __future__ import annotations

import asyncio
import json

import pytest


def _payload(result):
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


@pytest.fixture(scope="module")
def calc_mcp():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_calc_tools
    m = FastMCP("test")
    register_calc_tools(m)
    return m


def _call(mcp, name, **kw):
    return _payload(asyncio.run(mcp.call_tool(name, kw)))


def test_kicad_backend_registers_calculators():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend
    m = FastMCP("test")
    register_backend(m, "kicad")
    # The calculators are kinds of pcb_calc, kept by their own names.
    assert "pcb_calc" in {t.name for t in asyncio.run(m.list_tools())}
    names = set(m.hidden)
    assert {"pcb_calc_trace_width_for_current", "pcb_calc_termination",
            "pcb_calc_thermal_vias", "pcb_calc_trace_width_for_impedance",
            "pcb_calc_length_match",
            "pcb_calc_track_current_capacity"} <= names


def test_both_backend_does_not_double_register_calculators():
    # Altium's register_all_tools already defines pcb_calc_*, so "both" must not
    # add them again (that would be a duplicate-tool registration error).
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend
    m = FastMCP("test")
    # Would raise on a duplicate tool name if calc were double-registered.
    register_backend(m, "both")
    names = [t.name for t in asyncio.run(m.list_tools())]
    assert names.count("pcb_calc") == 1
    assert "pcb_calc_thermal_vias" in m.hidden


def test_trace_width_for_current_and_roundtrip(calc_mcp):
    w = _call(calc_mcp, "pcb_calc_trace_width_for_current",
              current_a=2.0, delta_t_c=10.0, copper_oz=1.0, layer="external")
    assert w["ok"] is True
    width = w["recommended_width_mils"]
    assert width > w["min_width_mils"] > 0
    cap = _call(calc_mcp, "pcb_calc_track_current_capacity", width_mils=width)
    # A track sized for 2 A (with margin) sustains at least 2 A.
    assert cap["ok"] is True and cap["current_a"] >= 2.0


def test_thermal_vias_solves_count(calc_mcp):
    t = _call(calc_mcp, "pcb_calc_thermal_vias", drill_mm=0.3, plating_um=25.0,
              length_mm=1.6, power_w=2.0, delta_t_c=20.0)
    assert t["ok"] is True
    assert t["via_count"] >= 1
    assert t["target_k_per_w"] == pytest.approx(10.0, rel=0.01)  # 20C / 2W


def test_impedance_width_and_bad_geometry(calc_mcp):
    r = _call(calc_mcp, "pcb_calc_trace_width_for_impedance",
              target_ohms=50.0, geometry="microstrip",
              dielectric_height_mils=6.0)
    assert r["ok"] is True and r["width_mils"] > 0
    bad = _call(calc_mcp, "pcb_calc_trace_width_for_impedance",
                target_ohms=50.0, geometry="not_a_geometry",
                dielectric_height_mils=6.0)
    assert bad["ok"] is False and "geometry" in bad["reason"]
