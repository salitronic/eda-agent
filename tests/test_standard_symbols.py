# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Standard schematic symbols are the default, not rectangles.

An op-amp, comparator, current-sense or instrumentation amplifier is a
triangle on every schematic a reader will have seen, and a discrete part
has its conventional glyph. lib_create_ic_symbol used to draw every IC as
a rectangle, so an INA293 came out as a box that had to be rebuilt by hand
from a triangle elsewhere in the library.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.design.symbol_gen import (
    PASSIVE_KINDS, classify_amplifier_pins, generate_amplifier_symbol,
    generate_passive_symbol, generate_transistor_symbol, looks_like_amplifier)
from tests.pascal_source import load


@pytest.fixture(autouse=True)
def _no_pin_table(monkeypatch):
    """These build symbols to check their layout; the pin table check has
    its own tests (test_symbol_pins_against_the_pin_table.py)."""
    from eda_agent import preferences
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": "symbol_pins"})


def _pins(names):
    return [{"designator": str(i), "name": n} for i, n in enumerate(names, 1)]


INA = _pins(["OUT", "GND", "IN+", "IN-", "VS"])
DUAL = _pins(["OUTA", "-INA", "+INA", "V-", "+INB", "-INB", "OUTB", "V+"])
QUAD = _pins(["OUT1", "IN1-", "IN1+", "VCC", "IN2+", "IN2-", "OUT2", "OUT3",
              "IN3-", "IN3+", "GND", "IN4+", "IN4-", "OUT4"])
LDO = _pins(["VIN", "GND", "EN", "NC", "VOUT"])


def _on_grid(v):
    return v % 100 == 0


def test_a_single_amplifier_is_one_triangle_in_the_convention():
    amp = generate_amplifier_symbol(INA)
    assert amp.part_count == 1
    part = amp.parts[0]
    (tri,) = part["polygons"]
    (bx1, by1), (bx2, by2), (ax, ay) = tri["points"]
    assert bx1 == bx2 == 200 and ax > bx1 and by2 < ay < by1
    by_name = {p["name"]: p for p in part["pins"]}
    # Inputs on the left pointing left, output at the apex pointing right.
    assert by_name["IN+"]["rotation"] == by_name["IN-"]["rotation"] == 180
    assert (by_name["OUT"]["x"], by_name["OUT"]["y"]) == (ax, ay)
    assert by_name["OUT"]["rotation"] == 0
    # Supplies leave the top and bottom.
    assert by_name["VS"]["rotation"] == 90 and by_name["GND"]["rotation"] == 270
    # Rule 14: the top-left input's wire end is the origin.
    first = part["pins"][0]
    assert (first["x"] - first["length"], first["y"]) == (0, 0)
    # Rule 15: every pin on the 100-mil grid, wire ends included.
    for p in part["pins"]:
        assert _on_grid(p["x"]) and _on_grid(p["y"]), p
    assert {t["text"] for t in part["texts"]} == {"+", "-"}
    assert by_name["IN+"]["electrical_type"] == "input"
    assert by_name["OUT"]["electrical_type"] == "output"
    assert by_name["VS"]["electrical_type"] == "power"


def test_a_supply_pin_sits_exactly_on_the_slanted_edge():
    part = generate_amplifier_symbol(INA).parts[0]
    (_, top), (_, bot), (ax, ay) = part["polygons"][0]["points"]
    for p in part["pins"]:
        if p["rotation"] in (90, 270):
            edge_y = top if p["rotation"] == 90 else bot
            expected = edge_y + (ay - edge_y) * (p["x"] - 200) / (ax - 200)
            assert p["y"] == expected, p


@pytest.mark.parametrize("pins,units", [(DUAL, 2), (QUAD, 4)])
def test_a_multi_unit_part_is_a_triangle_per_part_plus_power(pins, units):
    amp = generate_amplifier_symbol(pins)
    assert amp.part_count == units + 1
    for i, part in enumerate(amp.parts[:units], 1):
        assert len(part["polygons"]) == 1
        assert {p["owner_part_id"] for p in part["pins"]} == {i}
    power = amp.parts[-1]
    assert power["rectangles"] and not power["polygons"]
    seen = [p["designator"] for part in amp.parts for p in part["pins"]]
    assert sorted(seen) == sorted(p["designator"] for p in pins)


def test_amplifier_detection():
    assert looks_like_amplifier(INA)
    assert looks_like_amplifier(DUAL, "Dual op-amp")
    assert not looks_like_amplifier(LDO, "LDO regulator")
    assert classify_amplifier_pins(LDO) is None


@pytest.mark.parametrize("kind", PASSIVE_KINDS)
def test_every_discrete_kind_draws_on_the_pin_grid(kind):
    g = generate_passive_symbol(kind)
    assert g.lines or g.rectangles or g.polygons or g.arcs
    first = g.pins[0]
    assert (first["x"] - first["length"], first["y"]) == (0, 0)
    for p in g.pins:
        assert _on_grid(p["x"]) and _on_grid(p["y"]), p


@pytest.mark.parametrize("kind", ["resistor", "fuse"])
def test_a_resistor_body_is_long_and_narrow(kind):
    # A 200 x 200 square read as a box, not a resistor.
    (body,) = generate_passive_symbol(kind).rectangles
    length = abs(body["x2"] - body["x1"])
    height = abs(body["y2"] - body["y1"])
    assert length >= 2 * height, body
    assert body["y1"] == -body["y2"]  # centred on the lead


def test_inductor_is_arcs_and_capacitor_plates_are_close():
    """Asked for: the inductor as humps, not a box, and the capacitor
    plates close together rather than at the body edges."""
    ind = generate_passive_symbol("inductor")
    assert len(ind.arcs) >= 3 and not ind.rectangles
    cap = generate_passive_symbol("capacitor")
    plates = sorted(l["x1"] for l in cap.lines if l["x1"] == l["x2"])
    assert len(plates) == 2 and plates[1] - plates[0] <= 50


def test_transistor_pin_map_overrides_the_default():
    g = generate_transistor_symbol("nmos", {"G": "3", "S": "1", "D": "2"})
    assert {p["name"]: p["designator"] for p in g.pins} == {
        "G": "3", "S": "1", "D": "2"}
    with pytest.raises(ValueError):
        generate_transistor_symbol("npn", {"G": "1"})


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, cmd, params=None, **kw):
        self.sent.append((cmd, params or {}))
        return {"success": True}


def _tools(monkeypatch):
    import eda_agent.tools.library as lib

    bridge = _Bridge()
    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    lib.register_library_tools(_Mcp())
    return tools, bridge


def test_ic_tool_draws_a_triangle_for_an_amplifier(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["lib_create_ic_symbol"](
        name="INA293", left_pins=INA[2:4], right_pins=[INA[0], INA[1], INA[4]],
        description="current sense amplifier"))
    assert out["body"] == "amplifier"
    cmds = [c for c, _ in bridge.sent]
    assert "library.add_symbol_polygon" in cmds
    assert "library.add_symbol_rectangle" not in cmds
    poly = next(p for c, p in bridge.sent if c == "library.add_symbol_polygon")
    assert poly["fill_color"] == 8454143
    # Body before pins, or the solid fill paints over them.
    assert cmds.index("library.add_symbol_polygon") < cmds.index(
        "library.add_pins")


def test_ic_tool_keeps_a_rectangle_for_a_plain_ic(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["lib_create_ic_symbol"](
        name="LDO", left_pins=LDO[:3], right_pins=LDO[3:]))
    assert "body" not in out or out["body"] != "amplifier"
    assert any(c == "library.add_symbol_rectangle" for c, _ in bridge.sent)


def test_ic_tool_rectangle_can_still_be_forced(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["lib_create_ic_symbol"](
        name="X", left_pins=INA[2:4], right_pins=[INA[0]], body="rectangle"))
    assert not any(c == "library.add_symbol_polygon" for c, _ in bridge.sent)


def test_passive_tool_emits_inductor_arcs(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["lib_create_passive_symbol"](name="L1", kind="inductor"))
    assert sum(c == "library.add_symbol_arc" for c, _ in bridge.sent) == 4


@pytest.mark.parametrize("kind", ["capacitor", "polarized_capacitor", "diode",
                                  "led", "crystal", "fuse"])
def test_passive_glyph_lines_use_a_line_size_code_not_mils(monkeypatch, kind):
    # Altium reads a symbol line's width as its size code (0..3). The tool
    # once sent 10, and every capacitor came out as a solid blob.
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["lib_create_passive_symbol"](name="X1", kind=kind))
    lines = [p["lines"] for c, p in bridge.sent if c == "library.add_symbol_lines"]
    assert lines
    widths = {int(op.split("width=")[1].split(";")[0])
              for op in lines[0].split("~~")}
    assert widths <= {0, 1, 2, 3}, widths


def test_a_discrete_hides_its_pin_names_and_numbers(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["lib_create_passive_symbol"](name="C1", kind="capacitor"))
    pins = next(p["pins"] for c, p in bridge.sent if c == "library.add_pins")
    for op in pins.split("~~"):
        assert "show_name=false" in op and "show_designator=false" in op, op


def test_body_primitives_can_target_a_part():
    lib = load(Path(__file__).resolve().parents[1] / "scripts" / "altium"
               / "Library.pas")
    for handler in ("Lib_AddSymbolPolygon", "Lib_AddSymbolLines",
                    "Lib_AddSymbolRectangle"):
        assert "SetOwnerPartFrom(" in lib[handler], handler
    assert "AreaColor" in lib["Lib_AddSymbolPolygon"]


def test_audit_flags_an_amplifier_drawn_as_a_box():
    from eda_agent.tools.library import _standard_symbol_warnings

    comps = [
        {"name": "INA293", "body": {"rectangles": 1, "polygons": 0},
         "pins": INA, "description": "current sense amplifier"},
        {"name": "INA180", "body": {"rectangles": 0, "polygons": 1},
         "pins": INA},
        {"name": "LDO", "body": {"rectangles": 1, "polygons": 0},
         "pins": LDO},
    ]
    assert [w["name"] for w in _standard_symbol_warnings(comps)] == ["INA293"]


def test_audit_reports_what_the_body_is_drawn_with():
    lib = load(Path(__file__).resolve().parents[1] / "scripts" / "altium"
               / "Library.pas")
    body = lib["Lib_AuditStyles"]
    assert '"body":{"rectangles":' in body and '"description":"' in body


def _easyeda(monkeypatch):
    import eda_agent.tools.easyeda as ez

    sent = []

    def fake_call(command, params=None, **kw):
        sent.append((command, params or {}))
        if command.endswith("create_symbol") or "create" in command:
            return {"ok": True, "uuid": "u1"}
        return {"ok": True}

    monkeypatch.setattr(ez, "_call", fake_call)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    ez.register_easyeda_tools(_Mcp())
    return tools, sent


def test_easyeda_twin_draws_the_triangle_and_the_inductor_arcs(monkeypatch):
    """A fix usually has a twin: the EasyEDA tools share symbol_gen."""
    tools, sent = _easyeda(monkeypatch)
    out = asyncio.run(tools["easyeda_create_ic_symbol"](
        library_uuid="L", name="INA", left_pins=INA[2:4],
        right_pins=[INA[0], INA[1], INA[4]]))
    assert out.get("body") == "amplifier", out
    assert any("polygon" in c for c, _ in sent)
    sent.clear()
    out = asyncio.run(tools["easyeda_create_passive_symbol"](
        library_uuid="L", name="L1", kind="inductor"))
    assert out.get("ok"), out
    assert sum("arc" in c for c, _ in sent) == 4
