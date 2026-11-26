# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sch_draw_template stops when a part's footprint was not chosen.

A template named a footprint its symbol had no model for. The placement
keeps such a part on the library's model and lists it in
``footprint_issues``; the draw went on, wired the sheet, saved it and
answered ``ok: true`` with the list three levels down in ``placed``. The
sheet on disk then carried a part with another footprint than the one
the template asked for. The draw now stops there, before the first wire
and before any save, and names the part, the model asked for, the model
it stayed on and the models the symbol has.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design.sch_template import place_symbol_pins
from eda_agent.tools import sch_template as mod

PASSIVE = [
    {"designator": "1", "name": "1", "x": -100, "y": 0, "orientation": 2, "length": 100},
    {"designator": "2", "name": "2", "x": 100, "y": 0, "orientation": 0, "length": 100},
]

TEMPLATE = {
    "parts": [{"designator": "R1", "lib_reference": "RES", "library_path": "L.SchLib",
               "x": 1000, "y": 1000, "value": "10k", "footprint": "0402"},
              {"designator": "R2", "lib_reference": "RES", "library_path": "L.SchLib",
               "x": 1000, "y": 600, "value": "4k7", "footprint": "0603"}],
    "wires": [{"net": "MID", "path": ["R1.1", [800, 600], "R2.1"]}],
    "ports": [{"net": "VCC", "at": "R1.2"}, {"net": "GND", "at": "R2.2"}],
    "nets": {"MID": ["R1.1", "R2.1"]},
}


class _Bridge:
    """The placement as the script answers it: every part placed, and
    ``issues`` beside the counts when a footprint was not chosen."""

    def __init__(self, issues=None):
        self.issues = issues
        self.calls: list[tuple[str, dict]] = []
        self.rows: list[dict] = []

    async def send_command_async(self, command, params=None, **kw):
        params = dict(params or {})
        self.calls.append((command, params))
        if command == "library.get_pin_list":
            return {"pins": PASSIVE}
        if command == "generic.get_sch_doc_pins":
            return {"pins": self.rows}
        if command == "generic.place_sch_components_from_library":
            for op in params["placements"].split("~~"):
                f = dict(kv.split("=", 1) for kv in op.split(";"))
                for q in place_symbol_pins(f["designator"], PASSIVE, int(f["x"]),
                                           int(f["y"]), int(f["rotation"])):
                    self.rows.append({
                        "refdes": q.designator, "pin_number": q.number,
                        "pin_name": q.name, "x_mils": q.root_x, "y_mils": q.root_y,
                        "orientation": q.orientation, "pin_length_mils": 100})
            out = {"placed": 2, "failed": 0, "total": 2,
                   "footprints_chosen": 2 - len(self.issues or [])}
            if self.issues:
                out["footprint_issues"] = self.issues
            return out
        return {"success": True}

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.fixture
def draw(monkeypatch):
    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    mod.register_sch_template_tools(_Capture())

    def run(bridge, **kw):
        monkeypatch.setattr(mod, "get_bridge", lambda: bridge)
        return asyncio.run(captured["sch_draw_template"](
            TEMPLATE, "S.SchDoc", **kw))
    return run


_AFTER_PLACEMENT = ("generic.place_wires", "generic.place_power_ports",
                    "generic.place_net_labels",
                    "generic.set_sch_components_parameters",
                    "application.save_document", "application.close_document")


def test_a_footprint_the_symbol_lacks_stops_the_draw_before_any_wire(draw):
    bridge = _Bridge([{"designator": "R2", "issue": "FOOTPRINT_NOT_ON_SYMBOL",
                       "kept": "0402", "models": "0402, 0805"}])
    out = draw(bridge)
    assert out["ok"] is False and out["stage"] == "placement", out
    assert out["placed"]["footprint_issues"]          # the raw reply stays
    line = out["errors"][0]
    assert line.startswith("R2:")
    assert "'0603'" in line and "0402, 0805" in line and "stayed on 0402" in line
    assert "nothing was wired or saved" in out["errors"][-1]
    assert "reopened" not in out
    for command in _AFTER_PLACEMENT:
        assert command not in bridge.commands()


def test_a_choice_that_did_not_take_stops_it_too(draw):
    bridge = _Bridge([{"designator": "R1", "issue": "FOOTPRINT_NOT_APPLIED",
                       "kept": "0805"}])
    out = draw(bridge)
    assert out["ok"] is False and out["stage"] == "placement"
    line = out["errors"][0]
    assert "R1" in line and "'0402'" in line
    assert "FOOTPRINT_NOT_APPLIED" in line and "stayed on 0805" in line
    assert "generic.place_wires" not in bridge.commands()


def test_every_part_is_named(draw):
    bridge = _Bridge([
        {"designator": "R1", "issue": "FOOTPRINT_NOT_ON_SYMBOL", "kept": "",
         "models": ""},
        {"designator": "R2", "issue": "FOOTPRINT_NOT_APPLIED", "kept": "0402"}])
    out = draw(bridge)
    assert [e.split(":")[0] for e in out["errors"][:2]] == ["R1", "R2"]
    assert "it has: none" in out["errors"][0] and "stayed on no model" in out["errors"][0]


def test_chosen_footprints_draw_as_before(draw):
    bridge = _Bridge()
    out = draw(bridge, reopen=False)
    assert out["ok"] and out["stage"] == "done", out
    assert "generic.place_wires" in bridge.commands()
    assert out["placed"]["footprints_chosen"] == 2


@pytest.mark.parametrize("issues", [None, [], "FOOTPRINT_NOT_ON_SYMBOL", [7]])
def test_no_list_of_issues_is_no_error(issues):
    assert mod._footprint_errors(issues, TEMPLATE["parts"]) == []
