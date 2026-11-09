# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Lands placed one by one into a new footprint need the comparison.

When the drawing comparison could not run, the agent made an empty
footprint with lib_create_footprint and placed its lands with
lib_add_footprint_pads, which checks nothing: three parts went unchecked.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design.landpattern import compare_figure
from eda_agent.tools import library
from tests.test_link_footprint_reads_the_link_back import _tools


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == "library.create_footprint":
            return {"success": True, "name": params["name"]}
        if command in ("library.add_footprint_pads", "library.add_footprint_pad"):
            return {"success": True, "added": 1}
        raise AssertionError(command)


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(library, "_NEW_FOOTPRINTS", set())
    monkeypatch.setattr(library, "_LAST_NEW", [])
    monkeypatch.setattr(compare_figure, "ISSUED", {})
    bridge = _Bridge()
    return _tools(monkeypatch, bridge), bridge


_PADS = [{"designator": "1", "x": 0, "y": 0}, {"designator": "2", "x": 1, "y": 0}]


@pytest.mark.parametrize("name", ["LGA16", ""])
def test_pads_into_a_new_footprint_are_refused_unchecked(tools, name):
    t, bridge = tools
    asyncio.run(t["lib_create_footprint"](name="LGA16"))
    out = asyncio.run(t["lib_add_footprint_pads"](pads=_PADS, footprint_name=name))
    assert not out["ok"] and "lib_create_footprint_from_package" in out["reason"]
    assert "do not build the lands another way" in out["reason"]
    one = asyncio.run(t["lib_add_footprint_pad"](designator="1", x=0, y=0,
                                                 footprint_name=name))
    assert not one["ok"]
    assert bridge.calls == ["library.create_footprint"]


def test_a_compared_id_lets_the_lands_in(tools):
    t, bridge = tools
    asyncio.run(t["lib_create_footprint"](name="LGA16"))
    bad = asyncio.run(t["lib_add_footprint_pads"](pads=_PADS, compared="made_up"))
    assert not bad["ok"] and "not one it returned" in bad["reason"]
    compare_figure.ISSUED["abc"] = 2
    ok = asyncio.run(t["lib_add_footprint_pads"](pads=_PADS, compared="abc"))
    assert ok["success"] and bridge.calls[-1] == "library.add_footprint_pads"
    more = asyncio.run(t["lib_add_footprint_pad"](designator="3", x=2, y=0))
    assert more["success"]


def test_an_existing_footprint_takes_pads_as_before(tools):
    t, bridge = tools
    out = asyncio.run(t["lib_add_footprint_pads"](pads=_PADS, footprint_name="OLD"))
    assert out["success"] and bridge.calls == ["library.add_footprint_pads"]


def test_a_failed_comparison_says_to_stop(monkeypatch, tmp_path):
    from tests.test_compare_with_drawing import _tools as fp_tools
    t = fp_tools(monkeypatch, tmp_path)
    pkg = {"family": "custom", "name": "X", "body_x": 1, "body_y": 1,
           "source": {"document": "ds", "page": "1", "path": str(tmp_path / "none.pdf")},
           "custom_pads": [{"number": "1", "x": 0, "y": 0, "width": 0.3, "height": 0.3}]}
    out = asyncio.run(t["footprint_compare_drawing"](pkg, [0, 0, 1, 1]))
    assert not out["ok"] and "stop and report" in out["next"]


def test_the_panel_can_let_unchecked_lands_in_and_says_so(tools, monkeypatch):
    from eda_agent import preferences
    t, bridge = tools
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": "footprint_drawing"})
    asyncio.run(t["lib_create_footprint"](name="LGA16"))
    out = asyncio.run(t["lib_add_footprint_pads"](pads=_PADS))
    assert out["success"] and "switched off" in out["unchecked"]["skipped"]
    one = asyncio.run(t["lib_add_footprint_pad"](designator="3", x=2, y=0))
    assert one["success"] and one["unchecked"]["check"] == "footprint_drawing"
