# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sch_draw_template: symbols, placement, read-back, then wiring, in order,
and nothing wired when a check fails."""

from __future__ import annotations

import asyncio

import pytest

PASSIVE = [
    {"designator": "1", "name": "1", "x": -100, "y": 0, "orientation": 2, "length": 100},
    {"designator": "2", "name": "2", "x": 100, "y": 0, "orientation": 0, "length": 100},
]


def _sheet_rows(placements: str) -> list[dict]:
    """What get_sch_doc_pins reads after the placement payload lands."""
    rows = []
    for op in placements.split("~~"):
        f = dict(kv.split("=", 1) for kv in op.split(";"))
        x, y, rot = int(f["x"]), int(f["y"]), int(f["rotation"])
        from eda_agent.design.sch_template import place_symbol_pins
        for p in place_symbol_pins(f["designator"], PASSIVE, x, y, rot):
            rows.append({"refdes": p.designator, "pin_number": p.number,
                         "pin_name": p.name, "x_mils": p.root_x, "y_mils": p.root_y,
                         "orientation": p.orientation, "pin_length_mils": 100})
    return rows


class _Bridge:
    def __init__(self, shift: int = 0, existing: tuple = ()):
        self.calls: list[tuple[str, dict]] = []
        self.rows: list[dict] = [{"refdes": r, "pin_number": "1", "x_mils": 0,
                                  "y_mils": 0, "orientation": 0,
                                  "pin_length_mils": 100} for r in existing]
        self.shift = shift

    async def send_command_async(self, command, params=None, **kw):
        params = dict(params or {})
        self.calls.append((command, params))
        if command == "library.get_pin_list":
            return {"pins": PASSIVE}
        if command == "generic.get_sch_doc_pins":
            return {"pins": self.rows}
        if command == "generic.place_sch_components_from_library":
            rows = _sheet_rows(params["placements"])
            for r in rows:
                r["x_mils"] += self.shift
            self.rows = self.rows + rows
            return {"placed": len(rows) // 2}
        return {"success": True}


@pytest.fixture
def tools(monkeypatch, tmp_path):
    from eda_agent.tools import sch_template as mod
    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    mod.register_sch_template_tools(_Capture())
    return captured, mod, monkeypatch, tmp_path


TEMPLATE = {
    "parts": [{"designator": "R1", "lib_reference": "RES", "library_path": "L.SchLib",
               "x": 1000, "y": 1000, "value": "10k"},
              {"designator": "R2", "lib_reference": "RES", "library_path": "L.SchLib",
               "x": 1000, "y": 600, "value": "4k7"}],
    "wires": [{"net": "MID", "path": ["R1.1", [800, 600], "R2.1"]}],
    "ports": [{"net": "VCC", "at": "R1.2"}, {"net": "GND", "at": "R2.2"}],
    "nets": {"MID": ["R1.1", "R2.1"]},
}


def _commands(bridge):
    return [c for c, _ in bridge.calls]


def test_a_clean_template_is_drawn_in_order(tools):
    fns, mod, mp, tmp = tools
    bridge = _Bridge()
    mp.setattr(mod, "get_bridge", lambda: bridge)
    out = asyncio.run(fns["sch_draw_template"](TEMPLATE, "S.SchDoc",
                                               png_path=str(tmp / "p.png")))
    assert out["stage"] == "done" and out["ok"], out
    cmds = _commands(bridge)
    order = [cmds.index(c) for c in (
        "library.get_pin_list", "generic.place_sch_components_from_library",
        "generic.place_wires", "generic.place_power_ports",
        "generic.set_sch_components_parameters")]
    assert order == sorted(order)
    stamps = dict(bridge.calls)["generic.set_sch_components_parameters"]["stamps"]
    # Value writes the Comment and the part's own Value parameter.
    assert "designator=R1;Value=10k" in stamps
    ports = dict(bridge.calls)["generic.place_power_ports"]["ports"]
    assert "text=GND" in ports and "style=gnd_power;orientation=3" in ports
    assert "style=bar;orientation=1" in ports


def test_parts_that_land_elsewhere_are_not_wired(tools):
    fns, mod, mp, tmp = tools
    bridge = _Bridge(shift=50)
    mp.setattr(mod, "get_bridge", lambda: bridge)
    out = asyncio.run(fns["sch_draw_template"](TEMPLATE, "S.SchDoc"))
    assert out["stage"] == "placement" and not out["ok"]
    assert "generic.place_wires" not in _commands(bridge)


def test_a_designator_already_on_the_sheet_is_refused_before_placing(tools):
    fns, mod, mp, tmp = tools
    bridge = _Bridge(existing=("R1",))
    mp.setattr(mod, "get_bridge", lambda: bridge)
    out = asyncio.run(fns["sch_draw_template"](TEMPLATE, "S.SchDoc"))
    assert out["stage"] == "sheet"
    assert "generic.place_sch_components_from_library" not in _commands(bridge)


def test_a_short_stops_before_anything_is_placed(tools):
    fns, mod, mp, tmp = tools
    bridge = _Bridge()
    mp.setattr(mod, "get_bridge", lambda: bridge)
    bad = dict(TEMPLATE, wires=TEMPLATE["wires"] + [
        {"net": "GND", "path": [[800, 600], [800, 400]]}])
    out = asyncio.run(fns["sch_draw_template"](bad, "S.SchDoc",
                                               png_path=str(tmp / "p.png")))
    assert out["stage"] == "template" and not out["ok"]
    assert "generic.place_sch_components_from_library" not in _commands(bridge)


def test_unknown_part_keys_are_refused(tools):
    fns, mod, mp, tmp = tools
    bad = {"parts": [{"designator": "R1", "lib_reference": "RES", "rot": 90}]}
    out = asyncio.run(fns["sch_preview_template"](bad))
    assert not out["ok"] and "unknown keys" in out["errors"][0]


def _plan(**over):
    import json
    plan = {
        "spec": "divider", "summary": "two resistors",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": "L.SchLib", "value": "10k",
             "manufacturer": "Yageo", "mpn": "RC0603FR-0710KL", "footprint": "R0603"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": "L.SchLib", "value": "4k7"},
            {"refdes": "R3", "lib_ref": "RES", "lib_path": "L.SchLib", "value": "1k"},
        ],
        "nets": [
            {"name": "MID", "pins": [{"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
            {"name": "VCC", "pins": [{"refdes": "R1", "pin": "2"}, {"refdes": "R3", "pin": "2"}]},
        ],
    }
    plan.update(over)
    return json.dumps(plan)


# Only positions: the plan supplies symbol, library, value and the nets.
POSITIONS = {
    "parts": [{"designator": "R1", "x": 1000, "y": 1000},
              {"designator": "R2", "x": 1000, "y": 600}],
    "wires": [{"net": "MID", "path": ["R1.1", [800, 600], "R2.1"]}],
    "ports": [{"net": "VCC", "at": "R1.2"}, {"net": "GND", "at": "R2.2"}],
}


def test_the_plan_fills_the_parts_and_stamps_its_part_data(tools):
    fns, mod, mp, tmp = tools
    bridge = _Bridge()
    mp.setattr(mod, "get_bridge", lambda: bridge)
    out = asyncio.run(fns["sch_draw_template"](POSITIONS, "S.SchDoc",
                                               png_path=str(tmp / "p.png"),
                                               plan_json=_plan(), sheet="main"))
    assert out["stage"] == "done" and out["ok"], out
    stamps = dict(bridge.calls)["generic.set_sch_components_parameters"]["stamps"]
    r1 = next(s for s in stamps.split("~~") if s.startswith("designator=R1;"))
    for field in ("Manufacturer=Yageo", "Manufacturer Part Number=RC0603FR-0710KL",
                  "Footprint=R0603", "Value=10k"):
        assert field in r1
    # R3 is on the plan's sheet and not drawn: said, not refused.
    assert any("R3" in n["where"] for n in out["plan_notes"])


def test_a_drawing_that_differs_from_the_plan_is_not_wired(tools):
    fns, mod, mp, tmp = tools
    bridge = _Bridge()
    mp.setattr(mod, "get_bridge", lambda: bridge)
    wrong = dict(POSITIONS, ports=[{"net": "VCC", "at": "R2.2"},
                                   {"net": "GND", "at": "R1.2"}])
    out = asyncio.run(fns["sch_draw_template"](wrong, "S.SchDoc",
                                               png_path=str(tmp / "p.png"),
                                               plan_json=_plan()))
    assert out["stage"] == "template" and not out["ok"]
    assert any(e["check"] == "netlist" and e["where"] == "VCC" for e in out["errors"])
    assert "generic.place_sch_components_from_library" not in _commands(bridge)


def test_a_part_the_plan_lacks_is_refused(tools):
    fns, mod, mp, tmp = tools
    extra = dict(POSITIONS, parts=POSITIONS["parts"] + [
        {"designator": "C9", "lib_reference": "CAP", "x": 0, "y": 0}])
    out = asyncio.run(fns["sch_preview_template"](extra, plan_json=_plan()))
    assert not out["ok"] and "C9 is not a part of the plan" in out["errors"][0]


def test_a_redraw_keeps_each_parts_unique_id(tools):
    """A follow-up take redrew two sheets and set the ids back one call at
    a time afterwards; 7 parts still came back unlinked from the board."""
    fns, mod, mp, tmp = tools
    ids = {"R1": "AAAAAAAA", "R2": "BBBBBBBB"}

    class _IdBridge(_Bridge):
        def __init__(self):
            super().__init__()
            self.set: dict[str, str] = {}

        async def send_command_async(self, command, params=None, **kw):
            if command == "generic.set_component_unique_id":
                # R2's write does not take.
                if params["designator"] != "R2":
                    self.set[params["designator"]] = params["unique_id"]
            if command == "generic.query_objects":
                self.calls.append((command, dict(params)))
                return {"objects": [{"Designator.Text": r, "UniqueId": self.set.get(r, "NEW")}
                                    for r in ("R1", "R2")]}
            return await super().send_command_async(command, params, **kw)

    bridge = _IdBridge()
    mp.setattr(mod, "get_bridge", lambda: bridge)
    template = {**TEMPLATE, "parts": [dict(p, unique_id=ids[p["designator"]])
                                      for p in TEMPLATE["parts"]]}
    out = asyncio.run(fns["sch_draw_template"](template, "S.SchDoc",
                                               png_path=str(tmp / "p.png")))
    assert out["unique_ids"] == {"set": 1, "failed": 1, "failed_refdes": ["R2"]}
    assert out["ok"] is False, "a part left unlinked is a failed redraw"
