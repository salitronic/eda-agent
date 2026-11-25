# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_create_electrode: sensor electrodes as custom-shaped pads.

The geometry is plain numbers and is checked here without Altium: every
outline runs without crossing itself, neighbouring electrodes keep the gap
asked for (measured on the drawn copper, arcs cut into short chords), and
the tool refuses a pattern whose sizes break either before anything is
sent. The interdigitated estimate is checked against the conformal-mapping
limits it rests on.
"""
from __future__ import annotations

import asyncio
import math

import pytest

from eda_agent.design import electrode as e
from eda_agent.tools import library as lib_mod
from eda_agent.tools.registry import ToolRegistry


@pytest.mark.parametrize("pattern, kw", [
    ("interdigitated", {}),
    ("interdigitated", {"fingers": 12, "finger_width": 0.1, "gap": 0.1, "round_tips": False}),
    ("slider", {}),
    ("slider", {"segments": 7, "teeth": 3, "gap": 0.3}),
    ("slider", {"teeth": 1}),
    ("wheel", {}),
    ("wheel", {"segments": 5, "gap": 0.8}),
])
def test_every_pattern_keeps_its_gap_and_never_crosses_itself(pattern, kw):
    geom = e.build(pattern, **kw)
    got = e.check(geom)
    assert got["self_crossing"] == []
    assert got["measured_min_gap"] == pytest.approx(geom["min_gap"], abs=1e-6)


def test_interdigitated_fingers_interleave_one_gap_apart():
    geom = e.interdigitated(fingers=3, finger_width=0.2, gap=0.2, finger_length=2, bus_width=0.5)
    assert [x["designator"] for x in geom["electrodes"]] == ["1", "2"]
    assert geom["size"] == pytest.approx([2 * 0.8 + 2 * 0.2 + 0.2, 0.5 * 2 + 2 + 0.2])
    assert geom["gaps_between_fingers"] == 5


def test_round_tips_are_half_circles():
    pts = e.interdigitated(fingers=2)["electrodes"][0]["outline"]
    assert sum(1 for p in pts if len(p) > 2 and p[2] == 180.0) == 2


def test_the_wheel_sectors_are_arcs_on_both_rims():
    out = e.wheel(segments=4)["electrodes"][0]["outline"]
    assert out[1][2] > 0 and out[3][2] < 0


def test_the_meander_runs_between_its_two_pads():
    geom = e.meander(turns=4, run_length=10, trace_width=0.3, gap=0.3)
    assert len(geom["tracks"]) == 4 + 3
    ends = (geom["tracks"][0]["x1"], geom["tracks"][-1]["x2"])
    assert ends[0] == pytest.approx(-5) and ends[1] == pytest.approx(-5)
    assert geom["squares"] == pytest.approx((4 * 10 + 3 * 0.6) / 0.3)


def test_the_capacitance_estimate_follows_the_conformal_map():
    # Equal fingers and gaps: K(k) = K(k'), so each gap gives eps0 (er + 1) / 2
    # per metre of overlap.
    c = e.idc_capacitance(2, 1.0, 1e-4, 1e-4, 4.0)
    assert c == pytest.approx(e.EPS0 * 2.5, rel=1e-9)
    assert e.idc_capacitance(9, 1.0, 1e-4, 1e-4, 4.0) == pytest.approx(8 * c, rel=1e-9)
    assert e.idc_capacitance(2, 1.0, 1e-4, 5e-5, 4.0) > c


def test_the_elliptic_integral_matches_known_values():
    assert e._ellipk(0.0) == pytest.approx(math.pi / 2)
    assert e._ellipk(math.sqrt(0.5)) == pytest.approx(1.8540746773013719, rel=1e-12)


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params or {}))
        if command == "library.create_footprint":
            return {"success": True}
        return {"added": 1}


@pytest.fixture()
def tool(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(lib_mod, "get_bridge", lambda: rec)
    reg = ToolRegistry()
    lib_mod.register_library_tools(reg)
    found = {t.name: t.fn for t in asyncio.run(reg.list_tools())}
    return lambda **kw: asyncio.run(found["lib_create_electrode"](**kw)), rec


def test_a_comb_is_made_as_two_custom_pads_with_paste_off(tool):
    run, rec = tool
    out = run(name="IDE", pattern="interdigitated", units="mm")
    commands = [c for c, _ in rec.sent]
    assert commands[:2] == ["library.create_footprint", "library.add_footprint_custom_pads"]
    assert commands.count("library.set_pad_properties") == 2
    assert all(p["paste_top_enabled"] == "false" for c, p in rec.sent
               if c == "library.set_pad_properties")
    assert out["pads"] == ["1", "2"] and out["capacitance_pf"] > 0


def test_lengths_in_mils_are_taken_to_mm(tool):
    run, rec = tool
    out = run(name="IDE", gap=8, finger_width=8, units="mil")
    assert out["min_gap"] == pytest.approx(8)
    assert out["length_units"] == "mil"


@pytest.mark.parametrize("kw, says", [
    ({"pattern": "spiral"}, "pattern is one of"),
    ({"gap": -1}, "more than 0"),
    ({"name": " "}, "name is required"),
])
def test_a_bad_request_sends_nothing(tool, kw, says):
    run, rec = tool
    out = run(**{"name": "X", "units": "mm", **kw})
    assert out["ok"] is False and says in out["reason"] and rec.sent == []
