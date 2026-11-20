# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Arguments are taken in the shapes a caller reaches for first.

A list of property names, a dict of assignments, a list of nets: each is
sent as the wire's own text. A filter that writes a pattern with = says
how a pattern is written, a binding without the keys a binding needs says
which keys were given, and a net name with * or ? matches as a pattern.
"""
from __future__ import annotations

import asyncio
import inspect
import typing

from eda_agent.tools import generic, pcb, project
from eda_agent.tools.generic import _assignments, _literal_wildcard_note, _names


class _Bridge:
    def __init__(self, reply=None):
        self.sent, self.reply = [], reply or {"success": True}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, dict(params or {})))
        return self.reply


def _tools(monkeypatch, module, register, reply=None):
    bridge = _Bridge(reply)
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    register(_Mcp())
    return captured, bridge


def test_assignments_and_names_in_any_shape():
    assert _assignments({"Locked": True, "Height": 50}) == "Locked=true|Height=50"
    assert _assignments(["A=1", "B=2"]) == "A=1|B=2"
    assert _assignments("Locked=true") == "Locked=true"
    assert _names(["X", " Y"]) == "X,Y"


def test_use_tt_fonts_goes_to_altium_as_written(monkeypatch):
    # The script writes it from each text's own kind, which
    # no rewrite here can see, whatever the type is called.
    assert _assignments({"Height": 49, "UseTTFonts": False}) == "Height=49|UseTTFonts=false"
    tools, bridge = _tools(monkeypatch, generic, generic.register_generic_tools)
    for spelling in ("eTextObject", "text", "TextObject"):
        asyncio.run(tools["obj_modify"](object_type=spelling, filter="Layer=TopOverlay",
                                        set="UseTTFonts=true|FontName=Consolas"))
        assert bridge.sent[-1][1]["set"] == "UseTTFonts=true|FontName=Consolas"
    asyncio.run(tools["obj_modify"](items=[{"object_type": "text", "filter": "Layer=TopOverlay",
                                            "set": {"UseTTFonts": False}}]))
    assert "UseTTFonts=false" in str(bridge.sent[-1][1])


def test_obj_modify_sends_a_dict_and_each_items_set_as_text(monkeypatch):
    tools, bridge = _tools(monkeypatch, generic, generic.register_generic_tools)
    asyncio.run(tools["obj_modify"](object_type="eComponentObject",
                                    filter="Designator=U1", set={"Locked": True}))
    assert bridge.sent[-1][1]["set"] == "Locked=true"


def test_a_pattern_written_with_equals_is_named(monkeypatch):
    assert "Name~*SD*" in _literal_wildcard_note("Name=*SD*")
    assert _literal_wildcard_note("Name=SDIO_CLK") == ""
    tools, _ = _tools(monkeypatch, generic, generic.register_generic_tools,
                      {"success": True, "objects": [], "count": 0})
    got = asyncio.run(tools["obj_query"](object_type="ePin", properties=["Name"],
                                         filter="Name=*SD*"))
    assert "Name~*SD*" in got["note"]


def test_a_net_class_takes_its_nets_as_a_list(monkeypatch):
    tools, bridge = _tools(monkeypatch, pcb, pcb.register_pcb_tools)
    asyncio.run(tools["pcb_create_net_class"](name="SD", nets=["SD_CLK", "SD_CMD"]))
    assert bridge.sent[-1] == ("pcb.create_net_class",
                               {"name": "SD", "nets": "SD_CLK,SD_CMD"})


def test_a_binding_without_its_keys_says_which_were_given(monkeypatch):
    tools, bridge = _tools(monkeypatch, pcb, pcb.register_pcb_tools)
    got = asyncio.run(tools["pcb_bind_pad_nets"](
        bindings=[{"component": "U1", "pad": "1", "net": "A"}]))
    assert "designator, pin and net" in got["error"]
    assert "component, net, pad" in got["error"]
    assert bridge.sent == []


def test_a_preferred_rule_value_may_be_a_fraction_of_a_mil(monkeypatch):
    tools, _ = _tools(monkeypatch, pcb, pcb.register_pcb_tools)
    hints = typing.get_type_hints(inspect.unwrap(tools["pcb_create_design_rule"]))
    assert float in typing.get_args(hints["favored_value"])


def test_a_net_name_with_a_wildcard_is_a_pattern(monkeypatch):
    pins = [{"component": "U1", "pin": str(i), "net": n}
            for i, n in enumerate(["SD_CLK", "SD_CMD", "GND", "sd_d0"])]
    tools, bridge = _tools(monkeypatch, project, project.register_project_tools,
                           {"pins": pins, "count": 4, "limit_hit": False})
    got = asyncio.run(tools["proj_get_nets"](net_name="SD*", limit=2))
    assert bridge.sent[-1][1].get("net_name") is None
    assert bridge.sent[-1][1]["limit"] == "0"
    assert [p["net"] for p in got["pins"]] == ["SD_CLK", "SD_CMD"]
    assert got["limit_hit"] is True and got["count"] == 2
    got = asyncio.run(tools["proj_get_nets"](net_name="GND"))
    assert bridge.sent[-1][1]["net_name"] == "GND"


def test_a_board_property_asked_for_in_mm_is_read_in_mm(monkeypatch):
    tools, bridge = _tools(monkeypatch, generic, generic.register_generic_tools,
                           {"success": True, "count": 1,
                            "objects": [{"X": 1.9685, "Width": 10, "Net": "A"}]})
    got = asyncio.run(tools["obj_query"](object_type="eTrackObject",
                                         properties="X_mm,Width_mm,Width,Net"))
    assert bridge.sent[-1][1]["properties"] == "X,Width,Net"
    assert got["objects"] == [{"Width": 10, "Net": "A", "X_mm": 0.05, "Width_mm": 0.254}]
    # A schematic object's lengths are not board mils: left as asked.
    asyncio.run(tools["obj_query"](object_type="ePowerObject", properties="X_mm"))
    assert bridge.sent[-1][1]["properties"] == "X_mm"
