# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A SPICE model file is checked before it is attached to a part."""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.sim.model_file import check_model_file

_VENDOR = ("* LMV321 operational amplifier macromodel\n"
           "* Copyright 2019 Texas Instruments Incorporated\n")


def _file(tmp_path, body, header=_VENDOR, name="m.lib"):
    f = tmp_path / name
    f.write_text(header + body)
    return str(f)


def test_a_vendor_subcircuit_with_matching_nodes_passes(tmp_path):
    f = _file(tmp_path, ".SUBCKT LMV321 IN+ IN- VCC VEE OUT\n"
                        "+ PARAMS: GAIN=1\nR1 IN+ IN- 1MEG\n.ENDS LMV321\n")
    out = check_model_file(f, "lmv321", "X", pin_count=5)
    assert out["ok"], out
    assert out["nodes"] == ["IN+", "IN-", "VCC", "VEE", "OUT"]
    assert "Texas Instruments" in out["publisher"]


def test_a_name_not_declared_lists_what_is(tmp_path):
    f = _file(tmp_path, ".SUBCKT OPA1 1 2 3\n.ENDS\n.MODEL D1N4148 D(IS=1n)\n")
    out = check_model_file(f, "LMV321")
    assert not out["ok"] and ".SUBCKT OPA1" in out["reason"]
    assert ".MODEL D1N4148" in out["reason"]


def test_a_file_naming_no_publisher_is_refused(tmp_path):
    f = _file(tmp_path, ".SUBCKT X1 1 2\n.ENDS\n", header="* op amp model\n")
    out = check_model_file(f, "X1", pin_count=2)
    assert not out["ok"] and "names no publisher" in out["reason"]


def test_more_nodes_than_pins_is_refused_and_fewer_is_a_note(tmp_path):
    f = _file(tmp_path, ".SUBCKT U 1 2 3 4 5 6\n.ENDS\n")
    assert "unconnected" in check_model_file(f, "U", pin_count=5)["reason"]
    fewer = check_model_file(f, "U", pin_count=8)
    assert fewer["ok"] and "2 pin(s)" in fewer["note"]


def test_a_subcircuit_cut_short_is_refused(tmp_path):
    f = _file(tmp_path, ".SUBCKT U 1 2\nR1 1 2 1k\n")
    assert "no .ENDS" in check_model_file(f, "U")["reason"]


@pytest.mark.parametrize("prim,ok", [("D", True), ("Q", False), ("X", False)])
def test_a_model_takes_its_own_element_letter(tmp_path, prim, ok):
    f = _file(tmp_path, ".MODEL BAT54 D(IS=2u N=1.1)\n",
              header="* BAT54 Schottky, Nexperia\n")
    assert check_model_file(f, "BAT54", prim)["ok"] is ok


def test_a_missing_file_is_refused(tmp_path):
    assert not check_model_file(str(tmp_path / "none.lib"), "X")["ok"]


def _attach(monkeypatch, pins=5, checks_off=""):
    from eda_agent import preferences
    from eda_agent.tools import sim as sim_mod
    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append(command)
            if command == "project.get_component_info":
                return {"pins": [{"pin": str(n)} for n in range(1, pins + 1)]}
            return {"success": True}

    monkeypatch.setattr(sim_mod, "get_bridge", lambda: Bridge())
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": checks_off})
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    sim_mod.register_sim_tools(Mcp())
    return tools["sim_attach_model"], sent


def test_the_tool_refuses_before_attaching_and_reports_the_check(monkeypatch, tmp_path):
    f = _file(tmp_path, ".SUBCKT U 1 2 3 4 5 6\n.ENDS\n")
    attach, sent = _attach(monkeypatch, pins=5)
    out = asyncio.run(attach(designator="U1", file_path=f, model_name="U"))
    assert not out["success"] and out["check"] == "spice_model"
    assert "generic.attach_spice_model" not in sent
    good = _file(tmp_path, ".SUBCKT U 1 2 3 4 5\n.ENDS\n", name="g.lib")
    out = asyncio.run(attach(designator="U1", file_path=good, model_name="U"))
    assert out["success"] and out["model_check"]["nodes"] == ["1", "2", "3", "4", "5"]


def test_the_panel_can_switch_the_model_check_off(monkeypatch, tmp_path):
    f = _file(tmp_path, ".SUBCKT U 1 2\n.ENDS\n", header="")
    attach, sent = _attach(monkeypatch, checks_off="spice_model")
    out = asyncio.run(attach(designator="U1", file_path=f, model_name="U"))
    assert out["success"] and out["unchecked"]["check"] == "spice_model"
    assert sent == ["generic.attach_spice_model"]
