# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A template can place the parts of a multi-part symbol, and the sheet's
pins are read one part at a time.

Two sessions hit this on 2026-10-23: an i.MX 95 split over sheets and an
STM32H743 in 3 parts. A template could only place part 1, the same
designator twice was refused, and with the parts placed by hand the
sheet's pin read returned every pin of the symbol on every instance (the
other parts' pins hidden at that instance's positions), so the wiring check
saw each U2 pin three times and reported 15 false shorts.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eda_agent.design import sch_template as st
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: A two-part symbol: part 1 has pin 1 (left), part 2 has pin 2 (right),
#: and a Part Zero pin 3 goes with both.
MCU = [
    {"designator": "1", "name": "PA0", "x": -100, "y": 0, "orientation": 2,
     "length": 100, "owner_part_id": 1},
    {"designator": "2", "name": "PB0", "x": 100, "y": 0, "orientation": 0,
     "length": 100, "owner_part_id": 2},
    {"designator": "3", "name": "VSS", "x": 0, "y": -100, "orientation": 3,
     "length": 100, "owner_part_id": 0},
]


def test_only_the_placed_part_and_part_zero_are_read_from_the_symbol():
    one = {p.number for p in st.place_symbol_pins("U2", MCU, 0, 0, 0, part=1)}
    two = {p.number for p in st.place_symbol_pins("U2", MCU, 0, 0, 0, part=2)}
    assert one == {"1", "3"} and two == {"2", "3"}
    assert all(p.part == 2 for p in st.place_symbol_pins("U2", MCU, 0, 0, 0, part=2))


def test_the_sheet_read_keeps_each_instances_part():
    rows = [{"refdes": "U2", "pin_number": "2", "x_mils": 100, "y_mils": 0,
             "orientation": 0, "pin_length_mils": 100, "part_id": 2}]
    assert st.pins_from_sheet(rows)["U2"][0].part == 2
    rows[0].pop("part_id")
    assert st.pins_from_sheet(rows)["U2"][0].part == 1, "an old reply reads as part 1"


def _pins(parts):
    out = {}
    for p in parts:
        out.setdefault(p["designator"], []).extend(st.place_symbol_pins(
            p["designator"], MCU, p["x"], p["y"], 0, st.part_number(p)))
    return out


def test_two_parts_get_two_bodies_and_anchors_find_the_right_part():
    parts = [{"designator": "U2", "lib_reference": "MCU", "x": 1000, "y": 1000},
             {"designator": "U2", "lib_reference": "MCU", "x": 3000, "y": 1000, "part": 2}]
    t = {"parts": parts,
         "wires": [{"net": "A", "path": ["U2.1", [600, 1000]]},
                   {"net": "B", "path": ["U2.2", [3300, 1000]]}]}
    d = st.build(t, _pins(parts))
    assert set(d.bodies) == {"U2", "U2/2"}
    assert d.bodies["U2"][2] < d.bodies["U2/2"][0], "the parts' bodies stay apart"
    # U2.2 lives on part 2, at x = 3000 + 200.
    seg = [s for s in d.segments if s.net == "B"][0]
    assert {seg.x1, seg.x2} == {3200, 3300}


def test_pins_of_parts_drawn_elsewhere_are_warnings_only_for_split_symbols():
    parts = [{"designator": "U2", "lib_reference": "MCU", "x": 1000, "y": 1000, "part": 2},
             {"designator": "R1", "lib_reference": "MCU", "x": 5000, "y": 1000}]
    t = {"parts": parts, "wires": [],
         "nets": {"N1": ["U2.1"], "N2": ["R1.9"]}}
    d = st.build(t, _pins(parts))
    assert any("U2.1" in w["detail"] for w in d.warnings)
    assert not any("U2.1" in e["detail"] for e in d.errors)
    assert any("R1.9" in e["detail"] for e in d.errors), "a plain part's missing pin is a mistake"


# ---- the tools --------------------------------------------------------------

@pytest.fixture
def mod():
    from eda_agent.tools import sch_template as m
    return m


@pytest.mark.parametrize("parts,needle", [
    ([{"designator": "U2", "lib_reference": "MCU"},
      {"designator": "U2", "lib_reference": "MCU"}], "part 1 is placed twice"),
    ([{"designator": "U2", "lib_reference": "MCU"},
      {"designator": "U2", "lib_reference": "OTHER", "part": 2}], "same symbol"),
    ([{"designator": "U2", "lib_reference": "MCU", "part": 0}], "whole number"),
])
def test_bad_parts_are_refused(mod, parts, needle):
    assert any(needle in p for p in mod._part_problems({"parts": parts}))


def test_one_designator_once_per_part_is_fine(mod):
    parts = [{"designator": "U2", "lib_reference": "MCU", "library_path": "L"},
             {"designator": "U2", "lib_reference": "MCU", "library_path": "L", "part": 2},
             {"designator": "U2", "lib_reference": "MCU", "library_path": "L", "part": 3}]
    assert mod._part_problems({"parts": parts}) == []


def test_the_template_can_come_from_a_file(mod, tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"parts": []}), encoding="utf-8")
    assert mod._load_template(None, str(f)) == ({"parts": []}, "")
    assert "not both" in mod._load_template({}, str(f))[1]
    assert "give template" in mod._load_template(None, "")[1]
    assert "could not read" in mod._load_template(None, str(tmp_path / "no.json"))[1]


class _Bridge:
    """Altium for a draw: placements land, and the sheet's pins are read per
    instance, as Gen_GetSchDocPins now does."""

    def __init__(self, refuse_part: bool = False):
        self.calls = []
        self.rows = []
        self.refuse_part = refuse_part

    async def send_command_async(self, command, params=None, **kw):
        params = dict(params or {})
        self.calls.append((command, params))
        if command == "library.get_pin_list":
            return {"pins": MCU}
        if command == "generic.get_sch_doc_pins":
            return {"pins": self.rows}
        if command == "generic.place_sch_components_from_library":
            failed = []
            for op in params["placements"].split("~~"):
                f = dict(kv.split("=", 1) for kv in op.split(";"))
                part = int(f.get("part_id", 1))
                if self.refuse_part and part > 1:
                    failed.append(f"{f['designator']}:PART_REFUSED")
                    continue
                for p in st.place_symbol_pins(f["designator"], MCU, int(f["x"]),
                                              int(f["y"]), int(f["rotation"]), part):
                    self.rows.append({"refdes": p.designator, "pin_number": p.number,
                                      "pin_name": p.name, "x_mils": p.root_x,
                                      "y_mils": p.root_y, "orientation": p.orientation,
                                      "pin_length_mils": 100, "part_id": part})
            out = {"placed": 2 - len(failed), "failed": len(failed)}
            if failed:
                out["failed_refdes"] = ",".join(failed)
            return out
        return {"success": True}


def _draw(mod, monkeypatch, bridge, tmp_path):
    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    monkeypatch.setattr(mod, "get_bridge", lambda: bridge)
    monkeypatch.setattr(mod, "_render", lambda d, p, t: {})
    mod.register_sch_template_tools(_Capture())
    path = tmp_path / "t.json"
    path.write_text(json.dumps({
        "parts": [{"designator": "U2", "lib_reference": "MCU", "library_path": "L",
                   "x": 1000, "y": 1000},
                  {"designator": "U2", "lib_reference": "MCU", "library_path": "L",
                   "x": 3000, "y": 1000, "part": 2}],
        "wires": [{"net": "A", "path": ["U2.1", [600, 1000]]},
                  {"net": "B", "path": ["U2.2", [3300, 1000]]}],
        "nets": {"A": ["U2.1"], "B": ["U2.2"]}}), encoding="utf-8")
    return asyncio.run(captured["sch_draw_template"](
        document_path="S.SchDoc", template_path=str(path)))


def test_a_multi_part_symbol_is_drawn_with_each_part_set(mod, monkeypatch, tmp_path):
    bridge = _Bridge()
    out = _draw(mod, monkeypatch, bridge, tmp_path)
    assert out["stage"] == "done", out
    placements = [p for c, p in bridge.calls
                  if c == "generic.place_sch_components_from_library"][0]["placements"]
    ops = placements.split("~~")
    assert ops[0].endswith("part_id=1") and ops[1].endswith("part_id=2")
    stamps = [p for c, p in bridge.calls if c == "generic.set_sch_components_parameters"]
    assert all(s["stamps"].count("designator=U2") <= 1 for s in stamps)


def test_a_refused_part_stops_before_any_wire(mod, monkeypatch, tmp_path):
    bridge = _Bridge(refuse_part=True)
    out = _draw(mod, monkeypatch, bridge, tmp_path)
    assert out["stage"] == "placement" and "PART_REFUSED" in out["errors"][0]
    assert not any(c == "generic.place_wires" for c, _ in bridge.calls)


# ---- the Pascal --------------------------------------------------------------

def test_the_sheet_pin_read_keeps_only_each_instances_part():
    body = load(_ALTIUM / "Generic.pas")["Gen_GetSchDocPins"]
    assert "If (Owner = 0) Or (Owner = PartId) Then" in body
    assert "',\"part_id\":' + IntToStr(PartId)" in body


@pytest.mark.parametrize("func", ["Gen_GetSchComponentPins", "Gen_GetSchGeometry",
                                  "Gen_StubPins", "Gen_ExplainPin"])
def test_the_other_sheet_pin_readers_filter_the_same_way(func):
    assert "PinShownOn(Comp, Pin)" in load(_ALTIUM / "Generic.pas")[func]


def test_the_helper_is_the_same_rule():
    body = load(_ALTIUM / "Utils.pas")["PinShownOn"]
    assert "Result := (Owner = 0) Or (Owner = PartId);" in body


def test_the_placer_sets_the_part_and_reads_it_back():
    body = load(_ALTIUM / "Generic.pas")["Gen_PlaceSchComponentsFromLibrary"]
    put = body.index("PutLoadedComponent(SchDoc, Comp, X, Y, Rotation, Desig);")
    # Set before the add, through both setters, part 1 included.
    assert body.index("Comp.SetState_CurrentPartID(PartId);") < put
    assert body.index("Comp.CurrentPartID := PartId;") < put
    assert body.count("If PartId >= 1 Then") == 2
    # Defaulted to part 1, so a caller that sends no part_id (an older
    # server) still gets its part written and read back.
    assert "PartId := StrToIntDef(GetBatchField(Op, 'part_id'), 1);" in body
    assert "If PartId < 1 Then PartId := 1;" in body
    assert "If PartAfter <> PartId Then" in body
    assert "':PART_REFUSED'" in body


def test_a_part_is_set_inside_the_modify_bracket_the_compiler_listens_to():
    """Written without SCHM_Begin/EndModify the part read back right and the
    compiled netlist still listed every pin of the symbol on every part."""
    gen = load(_ALTIUM / "Generic.pas")
    helper = gen["SetSchPartId"]
    begin = helper.index("SchBeginModify(Comp);")
    end = helper.index("SchEndModify(Comp);")
    assert begin < helper.index("Comp.SetState_CurrentPartID(PartId);") < end
    assert begin < helper.index("Comp.CurrentPartID := PartId;") < end
    assert end < helper.index("RefreshSchObjectRender(Comp);")
    placer = gen["Gen_PlaceSchComponentsFromLibrary"]
    put = placer.index("PutLoadedComponent(SchDoc, Comp, X, Y, Rotation, Desig);")
    assert placer.index("SetSchPartId(Comp, PartId);") > put
    assert "SetSchPartId(Comp, PartId);" in gen["Gen_SetComponentPartId"]
