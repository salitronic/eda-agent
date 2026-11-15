# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Importing a KiCad project into Altium: read, plan, and the checks."""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

from eda_agent.convert.kicad_altium import plan as P
from eda_agent.convert.kicad_altium.read import read_board, read_project
from eda_agent.design import sch_template as st

_RES = """
		(symbol "Lib:R"
			(pin_numbers (hide yes))
			(property "Reference" "R" (at 2 0 90))
			(property "Value" "R" (at 0 0 90))
			(symbol "R_0_1"
				(rectangle (start -1.016 -2.54) (end 1.016 2.54)
					(stroke (width 0.254) (type default)) (fill (type none)))
			)
			(symbol "R_1_1"
				(pin passive line (at 0 3.81 270) (length 1.27)
					(name "~" (effects (font (size 1.27 1.27))))
					(number "1" (effects (font (size 1.27 1.27)))))
				(pin passive line (at 0 -3.81 90) (length 1.27)
					(name "~" (effects (font (size 1.27 1.27))))
					(number "2" (effects (font (size 1.27 1.27)))))
			)
		)
		(symbol "power:GND"
			(power)
			(property "Reference" "#PWR" (at 0 -6.35 0))
			(property "Value" "GND" (at 0 -3.81 0))
			(symbol "GND_1_1"
				(pin power_in line (at 0 0 270) (length 0) (hide yes)
					(name "GND" (effects (font (size 1.27 1.27))))
					(number "1" (effects (font (size 1.27 1.27)))))
			)
		)
		(symbol "power:PWR_FLAG"
			(power)
			(property "Reference" "#FLG" (at 0 1.905 0))
			(property "Value" "PWR_FLAG" (at 0 3.81 0))
			(symbol "PWR_FLAG_1_1"
				(pin power_out line (at 0 0 90) (length 0)
					(name "pwr" (effects (font (size 1.27 1.27))))
					(number "1" (effects (font (size 1.27 1.27)))))
			)
		)
"""


def _symbol(lib_id: str, ref: str, value: str, x: float, y: float, angle: int = 0,
            mirror: str = "", uid: str = "u") -> str:
    m = f"(mirror {mirror})" if mirror else ""
    return f"""
	(symbol (lib_id "{lib_id}") (at {x} {y} {angle}) {m} (unit 1)
		(in_bom yes) (on_board yes) (dnp no)
		(uuid "{uid}")
		(property "Reference" "{ref}" (at 0 0 0))
		(property "Value" "{value}" (at 0 0 0))
		(property "Footprint" "" (at 0 0 0))
		(property "MPN" "{'RC0603' if ref.startswith('R') else ''}" (at 0 0 0))
		(instances (project "t" (path "/root" (reference "{ref}") (unit 1))))
	)"""


def _sheet_text() -> str:
    # R1 stands upright at (50.8, 50.8): pin 1 at y 46.99, pin 2 at 54.61.
    # R2 is turned and mirrored; GND hangs from R1 pin 2 by a wire.
    return f"""(kicad_sch (version 20250114) (generator "eeschema")
	(uuid "root")
	(paper "A4")
	(lib_symbols {_RES})
	{_symbol("Lib:R", "R1", "10k", 50.8, 50.8, uid="a")}
	{_symbol("Lib:R", "R2", "4k7", 76.2, 46.99, angle=90, mirror="y", uid="b")}
	{_symbol("power:GND", "#PWR01", "GND", 50.8, 60.96, uid="c")}
	{_symbol("power:PWR_FLAG", "#FLG01", "PWR_FLAG", 50.8, 60.96, uid="d")}
	(wire (pts (xy 50.8 54.61) (xy 50.8 60.96)) (uuid "w1"))
	(wire (pts (xy 50.8 46.99) (xy 72.39 46.99)) (uuid "w2"))
	(label "MID" (at 60.96 46.99 0) (uuid "l1"))
	(no_connect (at 80.01 46.99) (uuid "n1"))
)
"""


_BOARD = """(kicad_pcb (version 20260206) (generator "pcbnew")
	(general (thickness 1.6))
	(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
	(footprint "Lib:R_0603" (layer "B.Cu") (at 100 50 90)
		(property "Reference" "R1" (at 0 0 0) (layer "B.SilkS"))
		(pad "1" smd rect (at -0.8 0 120) (size 0.8 0.9) (layers "B.Cu" "B.Mask")
			(net "MID"))
		(pad "2" smd rect (at 0.8 0 90) (size 0.8 0.9) (layers "B.Cu" "B.Mask")
			(net "GND"))
		(fp_line (start -1 -0.5) (end 1 -0.5) (stroke (width 0.1) (type solid))
			(layer "B.SilkS"))
	)
	(segment (start 100 50.8) (end 105 50.8) (width 0.25) (layer "B.Cu") (net "MID"))
	(via (at 105 50.8) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "MID"))
	(gr_line (start 90 40) (end 110 40) (layer "Edge.Cuts"))
)
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "t.kicad_pro").write_text("{}", encoding="utf-8")
    (tmp_path / "t.kicad_sch").write_text(_sheet_text(), encoding="utf-8")
    (tmp_path / "t.kicad_pcb").write_text(_BOARD, encoding="utf-8")
    return tmp_path / "t.kicad_pro"


def test_reads_parts_power_wires_and_labels(project):
    r = read_project(str(project))
    sheet = r["sheets"][0]
    refs = {p["reference"]: p for p in sheet["parts"]}
    assert set(refs) == {"R1", "R2"}
    assert refs["R2"]["mirror"] == "y" and refs["R2"]["angle_deg"] == 90
    assert {p["net"] for p in sheet["power"]} == {"GND", "PWR_FLAG"}
    assert len(sheet["wires"]) == 2 and sheet["labels"][0]["text"] == "MID"
    assert "Lib:R" in r["symbols"]


def test_board_nets_by_name_and_back_side_footprint(tmp_path):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(_BOARD, encoding="utf-8")
    b = read_board(str(path))
    assert b["nets"] == ["GND", "MID"]
    fp = b["footprints"][0]
    assert fp["layer"] == "B.Cu" and fp["reference"] == "R1"
    assert {p["number"]: p["net"] for p in fp["pads"]} == {"1": "MID", "2": "GND"}
    assert b["tracks"][0]["net"] == "MID" and b["vias"][0]["drill"] == 0.3


def test_a_back_footprint_returns_to_its_library_frame(tmp_path):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(_BOARD, encoding="utf-8")
    from eda_agent.libimport.kicad.sexpr import find, find_all, loads

    node = loads(P.library_footprint(read_board(str(path))["footprints"][0]))
    layers = {str(v) for pad in find_all(node, "pad") for v in find(pad, "layers")[1:]}
    assert layers == {"F.Cu", "F.Mask"}
    assert find(node, "at") is None, "the board position stays on the board"
    pad1 = next(p for p in find_all(node, "pad") if p[1] == "1")
    x, y, a = (float(v) for v in find(pad1, "at")[1:4])
    # The footprint's 90 degrees taken off the pad's 120, then the flip undone.
    assert (x, y, a) == (-0.8, 0.0, 330.0)
    assert find(pad1, "net") is None
    line = find(node, "fp_line")
    assert [float(v) for v in find(line, "start")[1:]] == [-1.0, 0.5]
    assert find(line, "layer")[1] == "F.SilkS"


@pytest.mark.parametrize("angle, mirror, want", [
    (0, "", (0, False)), (90, "y", (90, True)), (90, "x", (270, True)),
    (180, "x", (0, True)), (270, "", (270, False))])
def test_kicad_orientation_to_altium(angle, mirror, want):
    assert P.placement(angle, mirror) == want


def test_a_mirrored_part_moves_its_pins_to_the_other_side():
    pins = [{"designator": "1", "x": 100, "y": 0, "orientation": 0, "length": 100}]
    plain = st.place_symbol_pins("R", pins, 1000, 1000)
    flipped = st.place_symbol_pins("R", pins, 1000, 1000, mirror=True)
    assert (plain[0].x, plain[0].orientation) == (1200, 0)
    assert (flipped[0].x, flipped[0].orientation) == (800, 2)


def test_a_symbol_keeps_kicads_exact_drawing_with_pins_pointing_out(project):
    # KiCad's R: pin 1 wired at (0, 150) mil pointing down into a body
    # 80 x 200 mil. Altium places a pin at its body end pointing out, and
    # nothing snaps to the 100 mil grid.
    r = read_project(str(project))
    steps = P.library_plan(r, "C:/o/t.SchLib", "C:/o/t.PcbLib")["steps"]
    pins = next(s["args"] for s in steps if s["tool"] == "lib_add_pins")
    assert pins["grid"] == 1
    one = next(p for p in pins["pins"] if p["designator"] == "1")
    assert (one["x"], one["y"], one["rotation"], one["length"]) == (0, 100, 90, 50)
    assert one["show_designator"] is False and one["show_name"] is False
    rect = next(s["args"] for s in steps if s["tool"] == "lib_add_symbol_rectangle")
    assert (rect["x1"], rect["x2"], rect["grid"], rect["is_solid"]) == (-40, 40, 1, False)


def test_the_sheet_template_holds_together(project):
    r = read_project(str(project))
    lib = P.library_plan(r, "C:/o/t.SchLib", "C:/o/t.PcbLib")
    sch = P.schematic_plan(r, "C:/o", "C:/o/t.PrjPcb", lib, "C:/o/t.SchLib")
    t = sch["templates"]["t.SchDoc"]
    assert {p["designator"] for p in t["parts"]} == {"R1", "R2"}
    r2 = next(p for p in t["parts"] if p["designator"] == "R2")
    assert r2["rotation"] == 90 and r2["mirror"] is True
    assert [p["net"] for p in t["ports"]] == ["GND"]
    assert any("PWR_FLAG" in w for w in sch["warnings"])
    d = st.build(t, P.preview_pins(r["sheets"][0]), grid=P.SHEET_GRID)
    assert d.ok, d.errors
    tools = [s["tool"] for s in sch["steps"]]
    assert "sch_place_no_erc" in tools and "sch_set_components_parameters" in tools


def test_a_sheet_flips_about_its_paper_height_on_the_grid():
    # A4 is 210 mm (8267.7 mil) tall: the flip height rounds up to 8300 so
    # a KiCad point on its 50 mil grid stays on the grid in Altium.
    h = P.sheet_height([297.0, 210.0])
    assert h == 8300
    assert P._sheet_point([0, 0], h) == [0, 8300]
    assert P._sheet_point([25.4, 12.7], h) == [1000, 7800]
    sheet = {"paper_mm": [297.0, 210.0], "parts": [], "power": [], "wires": [],
             "junctions": [], "no_connects": [], "labels": [], "sheets": []}
    size = P.sheet_plan(sheet, {}, "", {}, "x.SchDoc", {})["steps"][1]["args"]
    assert (size["custom_width"], size["custom_height"]) == (11700, 8300)


def test_nets_compare_by_the_pins_they_join_not_their_names():
    kicad = {"Net-(R1-Pad1)": ["R1.1", "R2.2"], "GND": ["R1.2", "C1.2"]}
    altium = {"NetR1_1": ["R2.2", "R1.1"], "GND": ["C1.2", "R1.2"]}
    assert P.compare_nets(kicad, altium)["same"]
    altium["GND"] = ["C1.2"]
    diff = P.compare_nets(kicad, altium)
    assert not diff["same"] and diff["only_in_kicad"] == [["C1.2", "R1.2"]]


class _Capture:
    def __init__(self) -> None:
        self.fns: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.fns[fn.__name__] = fn
            return fn
        return deco

    prompt = resource = tool


def test_every_step_is_a_real_tool_call(project):
    import eda_agent.tools as tools_pkg

    cap = _Capture()
    for mod_info in pkgutil.iter_modules(tools_pkg.__path__):
        try:
            mod = importlib.import_module(f"eda_agent.tools.{mod_info.name}")
        except Exception:
            continue
        for attr in dir(mod):
            if attr.startswith("register_") and callable(getattr(mod, attr)):
                try:
                    getattr(mod, attr)(cap)
                except Exception:
                    pass
    r = read_project(str(project))
    lib = P.library_plan(r, "C:/o/t.SchLib", "C:/o/t.PcbLib")
    sch = P.schematic_plan(r, "C:/o", "C:/o/t.PrjPcb", lib, "C:/o/t.SchLib")
    assert lib["steps"] and sch["steps"]
    for step in lib["steps"] + sch["steps"]:
        assert step["tool"] in cap.fns, step["tool"]
        inspect.signature(cap.fns[step["tool"]]).bind(**step["args"])


# --- board -----------------------------------------------------------------------

from eda_agent.convert.kicad_altium import board as B  # noqa: E402


def _outline_board() -> dict:
    # A 20 x 10 mm board whose right side is a half circle, drawn with one
    # edge reversed, as KiCad files often are.
    return {"outline": [
        {"kind": "line", "start": [0, 0], "end": [20, 0]},
        {"kind": "arc", "start": [20, 10], "mid": [25, 5], "end": [20, 0]},
        {"kind": "line", "start": [20, 10], "end": [0, 10]},
        {"kind": "line", "start": [0, 10], "end": [0, 0]}],
        "footprints": [], "nets": [], "copper_layers": ["F.Cu", "B.Cu"]}


def test_the_outline_closes_into_one_loop_with_its_arc():
    b = _outline_board()
    step, warnings = B.outline_step(b, B.Frame(b))
    pts = step["args"]["points"]
    assert step["tool"] == "pcb_set_board_shape" and len(pts) == 4 and not warnings
    arc = next(p for p in pts if isinstance(p, dict))
    # The arc bulges right of x = 20 mm, about (20, 5) mm: (787.4, 196.85) mil.
    assert arc["arc_center"] == [787.4016, 196.8504]


def test_the_frame_puts_the_lower_left_corner_at_the_origin():
    f = B.Frame(_outline_board())
    assert f([0, 10]) == [0.0, 0.0] and f([0, 0]) == [0.0, 393.7008]


def test_a_bottom_part_keeps_kicads_angle():
    # Measured live: a back header at 90 degrees put every pad on KiCad's
    # with rotation 90, and on the opposite corner with 270.
    b = {**_outline_board(), "tracks": [], "arcs": [], "vias": [], "zones": [],
         "nets": ["N"], "footprints": [
             {"lib_id": "L:F", "reference": "U1", "value": "v", "at": [5, 5], "angle": 30,
              "layer": "B.Cu", "pads": [{"number": "1", "net": "N", "at": [5, 5]}]},
             {"lib_id": "L:F", "reference": "U2", "value": "v", "at": [8, 5], "angle": 30,
              "layer": "F.Cu", "pads": []}]}
    plan = B.board_plan(b, "C:/o/b.PcbDoc", "C:/o/l.PcbLib", {"L:F": "F"}, {"N": "NetU1_1"},
                        {"U1": "ABC"}, {})
    p = {x["designator"]: x for x in plan["placements"]}
    assert (p["U1"]["rotation"], p["U1"]["layer"]) == (30, "BottomLayer")
    assert (p["U2"]["rotation"], p["U2"]["layer"]) == (30, "TopLayer")
    assert p["U1"]["pad_nets"] == {"1": "NetU1_1"} and p["U1"]["unique_id"] == "ABC"


def test_copper_takes_the_altium_name_of_its_pads_net():
    b = {"nets": ["Net-(R1-Pad1)", "LONELY"], "footprints": [
        {"reference": "R1", "pads": [{"number": "1", "net": "Net-(R1-Pad1)"}]}]}
    nets = B.net_map(b, {"R1.1": "NetR1_1"})
    assert nets == {"Net-(R1-Pad1)": "NetR1_1", "LONELY": "LONELY"}


def test_an_arc_runs_counter_clockwise_from_its_start():
    a = B._arc([100, 0], [0, 100], [-100, 0])
    assert (a["radius"], a["start_angle"], a["end_angle"]) == (100.0, 0.0, 180.0)
    back = B._arc([-100, 0], [0, 100], [100, 0])
    assert (back["start_angle"], back["end_angle"]) == (0.0, 180.0)


def test_net_classes_from_patterns_and_assignments():
    b = {"nets": ["+5V", "+3V3", "SDA"]}
    classes = {"classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.25},
                           {"name": "Power", "clearance": 0.3, "track_width": 0.5}],
               "patterns": [{"netclass": "Power", "pattern": "+*"}],
               "assignments": {"SDA": "Bus"}}
    assert B.class_members(b, classes) == {"Bus": ["SDA"], "Power": ["+5V", "+3V3"]}
    steps = B.rule_steps(b, {"+5V": "+5V", "+3V3": "+3V3"}, classes)
    tools = [s["tool"] for s in steps]
    assert tools.count("pcb_set_rule_properties") == 2
    assert {"pcb_create_net_class", "pcb_create_design_rule"} <= set(tools)


def test_every_board_step_is_a_real_tool_call(tmp_path):
    import eda_agent.tools as tools_pkg

    cap = _Capture()
    for mod_info in pkgutil.iter_modules(tools_pkg.__path__):
        try:
            mod = importlib.import_module(f"eda_agent.tools.{mod_info.name}")
        except Exception:
            continue
        for attr in dir(mod):
            if attr.startswith("register_") and callable(getattr(mod, attr)):
                try:
                    getattr(mod, attr)(cap)
                except Exception:
                    pass
    path = tmp_path / "b.kicad_pcb"
    path.write_text(_BOARD, encoding="utf-8")
    b = read_board(str(path))
    b["zones"] = [{"net": "GND", "name": "", "layers": ["F.Cu"], "keepout": None,
                   "priority": 0, "outline": [[90, 40], [110, 40], [110, 60]]}]
    plan = B.board_plan(b, "C:/o/b.PcbDoc", "C:/o/l.PcbLib", {"Lib:R_0603": "R_0603"},
                        {n: n for n in b["nets"]}, {}, {})
    assert plan["steps"]
    for step in plan["steps"]:
        assert step["tool"] in cap.fns, step["tool"]
        inspect.signature(cap.fns[step["tool"]]).bind(**step["args"])


# --- the library check: Altium's symbols and footprints against KiCad's ----------

from eda_agent.convert.kicad_altium import verify as V  # noqa: E402
from eda_agent.libimport.easyeda.altium import build_altium_plan  # noqa: E402
from eda_agent.libimport.kicad.reader import read_kicad_footprint, read_kicad_symbol  # noqa: E402

_ARC_SYMBOL = """(symbol "L:ARC"
	(symbol "ARC_1_1"
		(arc (start {s}) (mid 0 2.54) (end {e}) (stroke (width 0)) (fill (type none)))
		(pin passive line (at -5.08 0 0) (length 2.54)
			(name "A" (effects (font (size 1.27 1.27))))
			(number "1" (effects (font (size 1.27 1.27)))))))"""


def _planned_arc(text: str) -> tuple:
    comp = read_kicad_symbol(P.symbol_library_text(text), "L:ARC")
    comp.footprint = None
    steps = build_altium_plan(comp, "s.SchLib", "p.PcbLib", grid=1)["steps"]
    a = next(s["args"] for s in steps if s["tool"] == "lib_add_symbol_arc")
    return (a["x_center"], a["y_center"], a["radius"], a["start_angle"], a["end_angle"])


@pytest.mark.parametrize("s, e", [("2.54 0", "-2.54 0"), ("-2.54 0", "2.54 0")])
def test_an_arc_is_drawn_over_the_side_its_middle_point_is_on(s, e):
    # The top half of a 100 mil circle, given either way round: Altium
    # draws counter-clockwise, so both must arrive as 0 to 180 degrees.
    text = _ARC_SYMBOL.format(s=s, e=e)
    want = V.expected_symbol(text)["art"][1]["arcs"][0]
    assert V._same_arc(want, (0.0, 0.0, 100.0, 0.0, 180.0))
    assert V._same_arc(want, _planned_arc(text)), _planned_arc(text)
    assert not V._same_arc(want, (0.0, 0.0, 100.0, 180.0, 0.0))


def _altium_pin(x, y, orientation, length=100, designator="1"):
    return {"designator": designator, "x": x, "y": y, "orientation": orientation,
            "length": length, "hidden": False}


def test_the_symbol_check_finds_a_pin_turned_into_its_body():
    want = V.expected_symbol(_ARC_SYMBOL.format(s="2.54 0", e="-2.54 0"))
    art = {1: {"rects": [], "segments": [], "arcs": [(0.0, 0.0, 100.0, 0.0, 180.0)], "texts": []}}
    # KiCad: wire end (-200, 0), into the body to the right.
    assert V.compare_symbol(want, [_altium_pin(-100, 0, 2)], art) == []
    found = V.compare_symbol(want, [_altium_pin(-200, 0, 0)], art)
    assert found and "pin 1 connects at (-100, 0)" in found[0]
    missing = V.compare_symbol(want, [_altium_pin(-100, 0, 2)], {1: {}})
    assert any("arc" in m and "missing" in m for m in missing)


def test_the_symbol_check_reads_altiums_rows():
    art = V.symbol_art_from_rows(
        [{"Location.X": "40", "Location.Y": "-100", "Corner.X": "-40", "Corner.Y": "100"}],
        [{"Location.X": "0", "Location.Y": "0", "Corner.X": "0", "Corner.Y": "0"}],
        [{"Location.X": "0", "Location.Y": "99", "Radius": "200",
          "StartAngle": "0.25", "EndAngle": "179.75"}],
        [{"Text": "NC"}])
    assert art == {"rects": [(-40.0, -100.0, 40.0, 100.0)], "segments": [],
                   "arcs": [(0.0, 99.0, 200.0, 0.25, 179.75)], "texts": ["NC"]}


_FOOTPRINT = """(footprint "C_Disc" (layer "F.Cu")
	(fp_line (start -1 -1) (end 1 -1) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
	(fp_line (start -1 -1) (end 1 -1) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
	(pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask"))
	(pad "2" thru_hole oval (at 5 0 90) (size 1.6 2.4) (drill oval 0.8 1.2) (layers "*.Cu" "*.Mask"))
	(pad "" np_thru_hole circle (at 2.5 -3) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask"))
	(pad "3" smd roundrect (at 2.5 2) (size 1 0.5) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)))"""


def _altium_pads(drill=0.8):
    return [
        {"name": "1", "x_mm": 0, "y_mm": 0, "w_mm": 1.6, "h_mm": 1.6, "shape": "round",
         "rotation": 0, "hole_mm": drill, "hole_w_mm": 0, "plated": True},
        {"name": "2", "x_mm": 5, "y_mm": 0, "w_mm": 1.6, "h_mm": 2.4, "shape": "round",
         "rotation": 90, "hole_mm": 0.8, "hole_w_mm": 1.2, "plated": True},
        {"name": "", "x_mm": 2.5, "y_mm": 3, "w_mm": 3.2, "h_mm": 3.2, "shape": "round",
         "rotation": 0, "hole_mm": 3.2, "hole_w_mm": 0, "plated": False},
        {"name": "3", "x_mm": 2.5, "y_mm": -2, "w_mm": 1, "h_mm": 0.5,
         "shape": "roundrectangle", "corner_pct": 50, "rotation": 0, "hole_mm": 0}]


def test_the_footprint_check_holds_kicads_pads_to_a_few_microns():
    want = V.expected_footprint(_FOOTPRINT)
    silk = {"tracks": [{"layer": "TopOverlay"}]}
    assert V.compare_footprint(want, _altium_pads(), silk) == []
    # A 0.8 mm drill sent as 31 whole mils comes back 0.7874 mm.
    rounded = V.compare_footprint(want, _altium_pads(drill=0.7874), silk)
    assert rounded == ["pad 1 drill is (0.7874, 0.7874) mm, KiCad (0.8, 0.8)"]
    assert any("F.SilkS" in r for r in V.compare_footprint(want, _altium_pads(), {}))


def test_a_kicad_footprint_goes_in_exactly_in_millimetres():
    comp = read_kicad_footprint(_FOOTPRINT)
    comp.symbol = None
    steps = build_altium_plan(comp, "s.SchLib", "p.PcbLib", footprint_name="C_Disc",
                              exact=True)["steps"]
    pads = next(s["args"] for s in steps if s["tool"] == "lib_add_footprint_pads")
    assert pads["units"] == "mm"
    one = next(p for p in pads["pads"] if p["designator"] == "1")
    assert (one["hole_size"], one["x_size"]) == (0.8, 1.6)
    assert all(s["args"].get("units") == "mm" for s in steps
               if s["tool"].startswith("lib_add_footprint_"))


_QFN_CORNER = """(footprint "QFN_EP" (layer "F.Cu")
	(pad "57" smd rect (at 0 0) (size 3.2 3.2) (layers "F.Cu" "F.Mask"))
	(pad "" smd roundrect (at -0.8 0.8) (size 1.29 1.29) (layers "F.Paste") (roundrect_rratio 0.2))
	(pad "" smd circle (at 3 3) (size 1 1) (layers "F.Mask" "F.Cu")))"""


def test_a_paste_opening_and_a_fiducial_come_across():
    comp = read_kicad_footprint(_QFN_CORNER)
    comp.symbol = None
    steps = build_altium_plan(comp, "s.SchLib", "p.PcbLib", footprint_name="QFN_EP",
                              exact=True)["steps"]
    pads = next(s["args"]["pads"] for s in steps if s["tool"] == "lib_add_footprint_pads")
    # The fiducial lists its mask first and is still copper, kept unnumbered.
    fid = next(p for p in pads if p["designator"] == "")
    assert fid.get("unnumbered") is True and fid["layer"] == "TopLayer"
    assert len(pads) == 2
    regions = next(s["args"]["regions"] for s in steps
                   if s["tool"] == "lib_add_footprint_regions")
    xs = [p[0] for p in regions[0]["points"]]
    ys = [p[1] for p in regions[0]["points"]]
    assert regions[0]["layer"] == "TopPaste"
    assert (round(min(xs), 3), round(max(xs), 3)) == (-1.445, -0.155)
    assert (round(min(ys), 3), round(max(ys), 3)) == (-1.445, -0.155)
    want = V.expected_footprint(_QFN_CORNER)
    got = {"regions": [{"layer": "TopPaste", "x1": -1.445, "y1": -1.445,
                        "x2": -0.155, "y2": -0.155}]}
    altium = [{"name": "57", "x_mm": 0, "y_mm": 0, "w_mm": 3.2, "h_mm": 3.2,
               "shape": "rectangular", "rotation": 0, "hole_mm": 0},
              {"name": "", "x_mm": 3, "y_mm": -3, "w_mm": 1, "h_mm": 1,
               "shape": "round", "rotation": 0, "hole_mm": 0}]
    assert V.compare_footprint(want, altium, got) == []
    assert V.compare_footprint(want, altium, {}) == [
        "TopPaste opening (1.29, 1.29) mm at (-0.8, -0.8) is missing"]


def test_a_pin_of_no_length_keeps_none():
    text = """(symbol "L:TP"
	(symbol "TP_1_1"
		(pin passive line (at 0 0 90) (length 0)
			(name "1" (effects (font (size 1.27 1.27))))
			(number "1" (effects (font (size 1.27 1.27)))))))"""
    comp = read_kicad_symbol(P.symbol_library_text(text), "L:TP")
    comp.footprint = None
    steps = build_altium_plan(comp, "s.SchLib", "p.PcbLib", grid=1, exact=True)["steps"]
    pin = next(s["args"]["pins"][0] for s in steps if s["tool"] == "lib_add_pins")
    assert (pin["x"], pin["y"], pin["length"]) == (0, 0, 0)
    assert V.compare_symbol(V.expected_symbol(text), [_altium_pin(0, 0, 3, length=0)], {1: {}}) == []


def test_a_filled_body_goes_in_before_the_pins_so_their_names_show():
    text = """(symbol "L:IC"
	(symbol "IC_0_1"
		(rectangle (start -5.08 5.08) (end 5.08 -5.08) (stroke (width 0.254)) (fill (type background))))
	(symbol "IC_1_1"
		(pin input line (at -7.62 0 0) (length 2.54)
			(name "EN" (effects (font (size 1.27 1.27))))
			(number "1" (effects (font (size 1.27 1.27)))))))"""
    comp = read_kicad_symbol(P.symbol_library_text(text), "L:IC")
    comp.footprint = None
    tools = [s["tool"] for s in build_altium_plan(comp, "s.SchLib", "p.PcbLib", grid=1,
                                                  exact=True)["steps"]]
    rect = next(s for s in build_altium_plan(comp, "s.SchLib", "p.PcbLib", grid=1)["steps"]
                if s["tool"] == "lib_add_symbol_rectangle")
    assert rect["args"]["is_solid"] is True
    assert tools.index("lib_add_symbol_rectangle") < tools.index("lib_add_pins")


def test_a_part_in_several_units_is_stamped_once():
    unit = lambda u: {"reference": "U4", "lib_id": "L:X", "unit": u, "angle_deg": 0,
                      "position_mm": [10, 10], "pins": [{"number": str(u)}],
                      "properties": {"MPN": "X1"}}
    sheet = {"paper_mm": [297.0, 210.0], "parts": [unit(1), unit(2)], "power": [],
             "wires": [], "junctions": [], "no_connects": [], "labels": [], "sheets": []}
    steps = P.sheet_plan(sheet, {"L:X": "X"}, "s.SchLib", {}, "x.SchDoc", {})["steps"]
    stamps = next(s["args"]["stamps"] for s in steps
                  if s["tool"] == "sch_set_components_parameters")
    assert stamps == [{"designator": "U4", "MPN": "X1"}]


def test_hidden_pins_stacked_on_a_shown_one_stay_on_its_wire():
    # KiCad stacks supply pins: one shown, the rest hidden at the same end.
    # Altium joins a hidden pin to no wire, so a stacked one goes in shown.
    text = """(symbol "L:MCU"
	(symbol "MCU_1_1"
		(pin power_in line (at -7.62 0 0) (length 2.54)
			(name "IOVDD" (effects (font (size 1.27 1.27))))
			(number "1" (effects (font (size 1.27 1.27)))))
		(pin passive line (at -7.62 0 0) (length 2.54) (hide yes)
			(name "IOVDD" (effects (font (size 1.27 1.27))))
			(number "10" (effects (font (size 1.27 1.27)))))
		(pin no_connect line (at 7.62 0 180) (length 2.54) (hide yes)
			(name "NC" (effects (font (size 1.27 1.27))))
			(number "3" (effects (font (size 1.27 1.27)))))))"""
    comp = read_kicad_symbol(P.symbol_library_text(text), "L:MCU")
    comp.footprint = None
    steps = build_altium_plan(comp, "s.SchLib", "p.PcbLib", grid=1, exact=True)["steps"]
    pins = {p["designator"]: p for p in next(
        s["args"]["pins"] for s in steps if s["tool"] == "lib_add_pins")}
    assert not pins["10"].get("hidden") and pins["3"].get("hidden") is True
    want = V.expected_symbol(text)
    assert (want["pins"]["10"]["hidden"], want["pins"]["3"]["hidden"]) == (False, True)


@pytest.mark.parametrize("fill", ["background", "none"])
def test_the_box_goes_in_first_wherever_it_sits_in_the_file(fill):
    # KiCad's DIP switch keeps its switches in sub-symbol 0_0 and the
    # body in 0_1; KiCad paints fills behind, Altium in order.
    text = """(symbol "L:SW"
	(symbol "SW_0_0"
		(polyline (pts (xy -1 0) (xy 1 1)) (stroke (width 0)) (fill (type none))))
	(symbol "SW_0_1"
		(rectangle (start -5.08 5.08) (end 5.08 -5.08) (stroke (width 0.254)) (fill (type FILL))))""".replace(
        "FILL", fill) + """
	(symbol "SW_1_1"
		(pin passive line (at -7.62 0 0) (length 2.54)
			(name "A" (effects (font (size 1.27 1.27))))
			(number "1" (effects (font (size 1.27 1.27)))))))"""
    comp = read_kicad_symbol(P.symbol_library_text(text), "L:SW")
    comp.footprint = None
    tools = [s["tool"] for s in build_altium_plan(comp, "s.SchLib", "p.PcbLib", grid=1,
                                                  exact=True)["steps"]]
    assert tools.index("lib_add_symbol_rectangle") < tools.index("lib_add_symbol_lines") \
        < tools.index("lib_add_pins")


_JUMPER = """(footprint "SolderJumper" (layer "F.Cu")
	(pad "1" smd custom (at -0.65 0) (size 1 0.5) (layers "F.Cu" "F.Mask")
		(options (clearance outline) (anchor rect))
		(primitives
			(gr_poly (pts (xy -0.5 -0.75) (xy 0.5 -0.75) (xy 0.5 0.75) (xy -0.5 0.75)) (width 0) (fill yes)))))"""


def test_a_custom_pad_brings_its_copper_and_the_check_holds_it():
    comp = read_kicad_footprint(_JUMPER)
    comp.symbol = None
    steps = build_altium_plan(comp, "s.SchLib", "p.PcbLib", footprint_name="SJ",
                              exact=True)["steps"]
    pad = next(s["args"]["pads"][0] for s in steps if s["tool"] == "lib_add_footprint_pads")
    assert (pad["x_size"], pad["y_size"], pad["shape"]) == (1.0, 0.5, "rectangular")
    regions = next(s["args"]["regions"] for s in steps
                   if s["tool"] == "lib_add_footprint_regions")
    assert sorted(r["layer"] for r in regions) == ["TopLayer", "TopSolder"]
    xs = sorted({p[0] for p in regions[0]["points"]})
    ys = sorted({p[1] for p in regions[0]["points"]})
    assert (xs, ys) == ([-1.15, -0.15], [-0.75, 0.75])

    want = V.expected_footprint(_JUMPER)
    altium = [{"name": "1", "x_mm": -0.65, "y_mm": 0, "w_mm": 1, "h_mm": 0.5,
               "shape": "rectangular", "rotation": 0, "hole_mm": 0}]
    drawn = {"regions": [{"layer": lay, "x1": -1.15, "y1": -0.75, "x2": -0.15, "y2": 0.75}
                         for lay in ("TopLayer", "TopSolder")]}
    assert V.compare_footprint(want, altium, drawn) == []
    assert V.compare_footprint(want, altium, {}) == [
        "TopLayer pad 1 copper (1, 1.5) mm at (-0.65, 0) is missing",
        "TopSolder pad 1 opening (1, 1.5) mm at (-0.65, 0) is missing"]


_LABELLED = """(footprint "R" (layer "F.Cu") (at 10 20 90)
	(property "Reference" "R1" (at 0 -1.5 90) (layer "F.SilkS")
		(effects (font (size 0.8 0.8) (thickness 0.12))))
	(property "Value" "10k" (at 0 1.5 270) (layer "F.Fab") (hide yes)
		(effects (font (size 1 1) (thickness 0.15)))))"""


def test_a_part_keeps_kicads_designator_and_comment_text():
    from eda_agent.convert.kicad_altium.read import _label
    from eda_agent.libimport.kicad.sexpr import loads

    node = loads(_LABELLED)
    name = _label(node, (10, 20, 90), "Reference")
    comment = _label(node, (10, 20, 90), "Value")
    # The position turns with the part; the angle is the board angle,
    # read upright, so 270 reads as 90.
    assert (name["at"], name["angle"], name["hidden"]) == ([8.5, 20.0], 90.0, False)
    assert (comment["at"], comment["angle"], comment["hidden"]) == ([11.5, 20.0], 90.0, True)

    frame = B.Frame({"outline": [], "footprints": [{"at": [0, 40]}]})
    fields = B.label_fields(name, "name", frame)
    assert fields == {"name_shown": True, "name_layer": "TopOverlay",
                      "name_height": 31.4961, "name_stroke": 4.7244, "name_angle": 90.0,
                      "name_mirror": False, "name_x": 334.6457, "name_y": 787.4016}
    assert B.label_fields(comment, "comment", frame)["comment_shown"] is False
    assert B.label_fields(comment, "comment", frame)["comment_layer"] == "Mechanical13"


def test_place_components_passes_the_texts_on(monkeypatch):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import pcb as pcb_tools

    sent = {}

    class Bridge:
        async def send_command_async(self, command, params, **_):
            sent[command] = params
            return {"placed": 1}

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: Bridge())
    m = FastMCP("t")
    pcb_tools.register_pcb_tools(m)
    asyncio.run(m.call_tool("pcb_place_components", {"placements": [
        {"footprint": "R", "library_path": "p.PcbLib", "designator": "R1",
         "name_shown": True, "name_height": 31.5, "name_x": 10, "name_y": 20.25,
         "name_mirror": False, "comment_shown": False, "comment_x": 3}]}))
    rec = sent["pcb.place_components"]["placements"].split(";;")
    assert {"name_shown==1", "name_height==31.5", "name_x==10", "name_y==20.25",
            "name_mirror==0", "comment_shown==0", "comment_x==3"} <= set(rec)


def test_runs_of_one_tool_go_as_one_call_in_order():
    from eda_agent.convert.kicad_altium.run import gather_steps

    steps = [{"tool": "lib_add_symbol_rectangle", "args": {"x1": 0, "grid": 1}},
             {"tool": "lib_add_symbol_rectangle", "args": {"x1": 5, "grid": 1}},
             {"tool": "lib_add_symbol_lines", "args": {"lines": [1], "grid": 1}},
             {"tool": "lib_add_symbol_lines", "args": {"lines": [2], "grid": 1}},
             {"tool": "lib_add_symbol_lines", "args": {"lines": [3], "grid": 2}},
             {"tool": "lib_add_pins", "args": {"pins": []}},
             {"tool": "lib_add_pins", "args": {"pins": []}},
             {"tool": "lib_add_symbol_arc", "args": {"radius": 1}}]
    assert gather_steps(steps) == [
        {"tool": "lib_add_symbol_rectangle",
         "args": {"items": [{"x1": 0, "grid": 1}, {"x1": 5, "grid": 1}]}},
        {"tool": "lib_add_symbol_lines", "args": {"lines": [1, 2], "grid": 1}},
        {"tool": "lib_add_symbol_lines", "args": {"lines": [3], "grid": 2}},
        {"tool": "lib_add_pins", "args": {"pins": []}},
        {"tool": "lib_add_pins", "args": {"pins": []}},
        {"tool": "lib_add_symbol_arc", "args": {"radius": 1}}]
    # The plan itself is left as it was.
    assert steps[0]["args"] == {"x1": 0, "grid": 1}
    many = [{"tool": "sch_place_no_erc", "args": {"x": i}} for i in range(201)]
    gathered = gather_steps(many)
    assert [len(s["args"].get("items", [s["args"]])) for s in gathered] == [200, 1]


def test_footprints_are_linked_after_every_symbol_in_one_row(project):
    lib = P.library_plan(read_project(str(project)), "C:/out/x.SchLib", "C:/out/x.PcbLib")
    tools = [s["tool"] for s in lib["steps"]]
    first = tools.index("lib_link_footprint")
    assert tools[first - 1] == "app_set_active_document"
    assert set(tools[first:]) == {"lib_link_footprint"}
    assert "lib_create_symbol" not in tools[first:]


def test_a_list_of_links_reads_each_footprint_once(monkeypatch):
    import asyncio
    from eda_agent.tools import library as L

    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, **_):
            sent.append((command, dict(params or {})))
            if command == "application.get_active_document":
                return {"file_path": "C:/out/x.SchLib"}
            if command == "application.get_open_documents":
                return [{"file_path": "C:/out/x.PcbLib"}, {"file_path": "C:/out/x.SchLib"}]
            if command == "library.get_pad_geometry":
                return {"name": params["footprint_name"],
                        "pads": [{"name": "1", "plated": True}, {"name": "2", "plated": True}]}
            if command == "library.get_pin_list":
                return {"pins": [{"designator": "1"}, {"designator": "2"}]}
            if command == "library.link_footprint":
                return {"success": True}
            return {}

    monkeypatch.setattr(L, "get_bridge", lambda: Bridge())
    reply = asyncio.run(L._links_together(
        [{"component_name": "R", "footprint_name": "R0603"},
         {"component_name": "C", "footprint_name": "R0603"},
         {"component_name": "D", "footprint_name": "D0603"}], {"replace": True}))
    assert (reply["count"], reply["failed"]) == (3, 0)
    reads = [p["footprint_name"] for c, p in sent if c == "library.get_pad_geometry"]
    assert reads == ["R0603", "D0603"]
    assert [p["component_name"] for c, p in sent if c == "library.link_footprint"] == \
        ["R", "C", "D"]
    assert sum(1 for c, _ in sent if c == "application.get_open_documents") == 1
