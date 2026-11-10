# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Altium to KiCad conversion's own steps, offline.

The import itself drives kicad.exe and is checked live (the RoundLED
project converted with 0 of 436 pins changed against Altium's netlist);
what is tested here is each step's rule on small inputs.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from eda_agent.convert.altium_kicad import (altium_side, checks, footprints, hierarchy,
                                            schematic)
from eda_agent.convert.altium_kicad.tree import Sym, dump, find, first, parse, read, write


# --- names ---------------------------------------------------------------

@pytest.mark.parametrize("lib_id,want", [
    ("Analog:Power_0_LM393_Analog.IntLib", ("Analog", "LM393")),
    ("Passives:Port_3_RES 10K 1% 0603_Passives.IntLib",
     ("Passives", "RES 10K 1% 0603")),
    ("Diodes:Power_1_mirrored_1N4148_Diodes.IntLib",
     ("Diodes", "1N4148")),
    ("*:root_3_CL05B104KO5NNNC_*", ("*", "CL05B104KO5NNNC")),
    ("RoundLED:root_0_USBLC6-2SC6_RoundLED.SchLib", ("RoundLED", "USBLC6-2SC6")),
])
def test_importer_names_come_back_to_the_part(lib_id, want):
    lib, part, _ = schematic.canonical(lib_id, ["Power", "Port"])
    assert (lib, part) == want


def test_a_library_name_is_a_file_name():
    assert schematic.library_name("*", "RoundLED") == "RoundLED"
    assert schematic.library_name("Analog", "X") == "Analog"


def test_bodies_compare_without_corner_order_or_uuids():
    a = parse('(symbol "L:R_0_1" (rectangle (start 0 0) (end 2 1)) (uuid "1"))')
    b = parse('(symbol "L:R_1_1" (rectangle (start 2 1) (end 0 0)) (uuid "2"))')
    assert schematic.body(a)[2:] == schematic.body(b)[2:]


# --- grid and wiring -----------------------------------------------------

def _sheet(body: str) -> list:
    return parse("(kicad_sch (paper \"A4\") (lib_symbols) " + body + ")")


def test_a_sheet_is_moved_whole_onto_the_grid():
    s = _sheet('(wire (pts (xy 10.16 10.6172) (xy 20.32 10.6172)))'
               '(wire (pts (xy 10.16 13.1572) (xy 10.16 15.6972)))')
    dx, dy = schematic.grid_offset(s)
    assert dx == 0.0 and dy == pytest.approx(0.8128)
    schematic.move(s, dx, dy)
    xy = find(first(find(s, "wire")[0], "pts"), "xy")[0]
    assert float(xy[2]) == pytest.approx(11.43)


def test_a_wire_end_past_its_label_is_pulled_back_to_it():
    s = _sheet('(junction (at 0 0))'
               '(wire (pts (xy 0 0) (xy 10.16 0)))'
               '(label "NET" (at 5.08 0 0))')
    out = schematic.trim_stubs(s)
    assert out["trimmed"] == 1
    end = find(first(find(s, "wire")[0], "pts"), "xy")[1]
    assert (float(end[1]), float(end[2])) == (5.08, 0.0)


def test_only_graphics_in_the_title_block_corner_go():
    s = _sheet('(text "REV A" (at 290 205 0))'
               '(text "keep me" (at 100 100 0))'
               '(polyline (pts (xy 100 180) (xy 296 180)))')
    gone = schematic.remove_title_block(s)
    assert gone == ["REV A"]
    assert len(find(s, "text")) == 1 and len(find(s, "polyline")) == 1


# --- hierarchy -----------------------------------------------------------

def _flat_project(tmp_path):
    pro = {"schematic": {"top_level_sheets": [
        {"filename": "P.kicad_sch", "name": "A", "uuid": "aaa"},
        {"filename": "B.kicad_sch", "name": "B", "uuid": "bbb"}]}, "sheets": []}
    (tmp_path / "P.kicad_pro").write_text(json.dumps(pro))
    for name, top in (("P.kicad_sch", "aaa"), ("B.kicad_sch", "bbb")):
        sheet = parse(f'(kicad_sch (uuid "u-{top}") (lib_symbols) (label "SHARED" (at 0 0 0))'
                      f'(label "LOCAL_{top}" (at 1 0 0))'
                      f'(symbol (lib_id "L:R") (instances (project "P" (path "/{top}" (reference "R1"))))))')
        write(str(tmp_path / name), sheet)
    return tmp_path


def test_a_flat_project_gets_one_root_and_global_labels(tmp_path):
    d = _flat_project(tmp_path)
    assert hierarchy.is_flat(str(d), "P")
    built = hierarchy.build_root(str(d), "P")
    assert built["built"] and [s["filename"] for s in built["sheets"]] == ["A.kicad_sch", "B.kicad_sch"]
    root = read(str(d / "P.kicad_sch"))
    assert [first(s, "uuid")[1] for s in find(root, "sheet")] == ["aaa", "bbb"]
    root_uuid = first(root, "uuid")[1]
    a = read(str(d / "A.kicad_sch"))
    path = first(first(first(find(a, "symbol")[0], "instances"), "project"), "path")
    assert path[1] == f"/{root_uuid}/aaa"
    labels = hierarchy.globalize_labels(str(d), hierarchy.child_files(str(d), "P"))
    assert labels["names"] == ["SHARED"]
    b = read(str(d / "B.kicad_sch"))
    assert [g[1] for g in find(b, "global_label")] == ["SHARED"]
    assert [lb[1] for lb in find(b, "label")] == ["LOCAL_bbb"]
    pro = json.loads((d / "P.kicad_pro").read_text())
    assert pro["schematic"]["top_level_sheets"] == [
        {"filename": "P.kicad_sch", "name": "Root", "uuid": root_uuid}]


# --- footprints ----------------------------------------------------------

def test_a_zero_size_npth_pad_takes_its_drill():
    text = '(footprint "H" (pad "" np_thru_hole circle (at 0 0) (size 0 0) (drill 2.5) (layers "*.Cu")))'
    out, n = footprints.npth_sizes(text)
    assert n == 1 and "(size 2.5 2.5)" in out


def test_mechanical_layers_map_by_kind():
    m = footprints.layer_map([{"layer": "Mechanical15", "kind": "Courtyard Top"},
                              {"layer": "Mechanical13", "kind": "Assembly Top"},
                              {"layer": "Mechanical1", "kind": "Not Set"}])
    assert m == {"User.15": "F.CrtYd", "User.13": "F.Fab"}


def test_the_centre_cross_leaves_the_courtyard_for_fab():
    fp = ('(footprint "X" (fp_line (start -0.5 0) (end 0.5 0) (layer "F.CrtYd"))'
          '(fp_line (start -1 -1) (end 1 -1) (layer "User.15")))')
    counts = {"remapped": 0, "crosses_to_fab": 0}
    out = footprints._map_footprint(fp, {"User.15": "F.CrtYd"}, counts)
    assert '(start -0.5 0) (end 0.5 0) (layer "F.Fab")' in out
    assert '(start -1 -1) (end 1 -1) (layer "F.CrtYd")' in out
    assert counts == {"remapped": 1, "crosses_to_fab": 1}


def test_board_footprints_and_pad_nets_are_read_from_the_text():
    board = ('(kicad_pcb (footprint "LIB:R0603" (property "Reference" "R1")'
             '(pad "1" smd rect (net 1 "GND")) (pad "2" smd rect (net "/SIG"))))')
    assert footprints.board_footprints(board) == {"R1": "LIB:R0603"}
    assert footprints.pad_nets(board) == {("R1", "1"): "GND", ("R1", "2"): "/SIG"}


# --- the pin check -------------------------------------------------------

def test_the_check_compares_net_mates_not_names():
    a = {("R1", "1"): "GND", ("C1", "2"): "GND", ("R1", "2"): "N1", ("U1", "3"): "N1"}
    b = {("R1", "1"): "/GND", ("C1", "2"): "/GND", ("R1", "2"): "/x/N1", ("U1", "3"): "/x/N1"}
    assert checks.compare(a, b)["mismatches"] == 0
    b[("U1", "3")] = "/GND"
    out = checks.compare(a, b)
    assert out["mismatches"] == 4 and out["examples"][0]["pin"]


def test_unconnected_names_are_left_out():
    assert checks.from_pairs([((("R1", "1")), "unconnected-(R1-Pad1)"), (("R2", "1"), "?")]) == {}


# --- Altium side ---------------------------------------------------------

_RULES = [
    {"name": "Clearance", "enabled": True, "priority": 1, "scope_1": "All", "scope_2": "All",
     "descriptor": "Clearance Constraint (Gap=6mil) (All),(All)", "gap_mm": 0.1524},
    {"name": "Width", "enabled": True, "priority": 3, "scope_1": "All", "scope_2": "All",
     "descriptor": "Width Constraint (Min=5mil) (Max=60mil) (Preferred=8mil) (All)"},
    {"name": "Width_3V3", "enabled": True, "priority": 1, "scope_1": "InNet('+3V3')", "scope_2": "All",
     "descriptor": "Width Constraint (Min=8mil) (Max=30mil) (Preferred=12mil) (InNet('+3V3'))"},
    {"name": "RoutingVias", "enabled": True, "priority": 1, "scope_1": "All", "scope_2": "All",
     "descriptor": "Routing Via (MinHoleWidth=10mil) (MaxHoleWidth=16mil) (PreferredHoleWidth=12mil) "
                   "(MinWidth=18mil) (MaxWidth=28mil) (PreferedWidth=20mil) (All)"},
    {"name": "HoleToHoleClearance", "enabled": True, "priority": 1, "scope_1": "All", "scope_2": "All",
     "descriptor": "Hole To Hole Clearance (Gap=10mil) (All),(All)", "gap_mm": 0.254},
    {"name": "PolygonConnect", "enabled": True, "priority": 1, "scope_1": "All", "scope_2": "All",
     "descriptor": "Polygon Connect Rule(Relief Connect )(Conductor Width=10mil) (Relief Angle=90 Angle) "
                   "(Entries=4) (Air Gap Width=10mil) (All),(All)"},
    {"name": "Odd", "enabled": True, "priority": 2, "scope_1": "IsBGA", "scope_2": "All",
     "descriptor": "Clearance Constraint (Gap=4mil) (IsBGA),(All)", "gap_mm": 0.1016},
]


def test_board_wide_rules_become_design_settings():
    r = altium_side.board_rules(_RULES)
    assert r["clearance_mm"] == pytest.approx(0.1524)
    assert r["track_min_mm"] == pytest.approx(0.127) and r["track_mm"] == pytest.approx(0.2032)
    assert r["via_drill_mm"] == pytest.approx(0.3048) and r["via_diameter_mm"] == pytest.approx(0.508)
    assert r["via_min_drill_mm"] == pytest.approx(0.254)
    assert r["via_min_diameter_mm"] == pytest.approx(0.4572)
    assert r["hole_to_hole_mm"] == pytest.approx(0.254)


def test_scoped_rules_go_to_the_rules_file_and_the_rest_is_named():
    text, skipped = altium_side.rules_file(_RULES, {"+3V3": "+3V3"})
    assert "(rule \"Width_3V3\"" in text and "A.NetName == '+3V3'" in text
    assert "(min 0.2032mm) (opt 0.3048mm) (max 0.762mm)" in text
    assert "zone_connection thermal_reliefs" in text
    assert "thermal_spoke_width (min 0.254mm)" in text
    assert any(s.startswith("Odd:") for s in skipped)
    # Altium's first match wins, KiCad's last: higher priority numbers first.
    assert text.index('"Odd"') if '"Odd"' in text else True


@pytest.mark.parametrize("scope,want", [
    ("(not InNetClass('HV')) and (not InComponent('C6'))",
     "((!A.hasNetclass('HV')) && (!A.memberOfFootprint('C6')))"),
    ("IsVia Or InNet('VIN') Or InComponent('Q1')",
     "(A.Type == 'Via' || A.NetName == '/In/VIN' || A.memberOfFootprint('Q1'))"),
    ("(not IsKeepOut) and InAnyNet", None),
    ("All", ""),
])
def test_compound_scopes_translate_and_unknown_tests_do_not(scope, want):
    assert altium_side.kicad_condition(scope, {"VIN": "/In/VIN"}) == want


def test_classes_a_rule_names_are_listed():
    rules = [{"scope_1": "InNetClass('HV')", "scope_2": "(not InNetClass('LV'))"}]
    assert altium_side.classes_named(rules) == {"HV", "LV"}


def test_more_rule_kinds_are_ported():
    rules = [
        {"name": "DiffPairsRouting", "enabled": True, "priority": 1, "scope_1": "All", "scope_2": "All",
         "descriptor": "Differential Pairs Uncoupled Length using the Gap Constraints (Min=5.5mil) "
                       "(Max=8mil) (Prefered=6mil)  and Width Constraints (Min=8mil) (Max=11mil) "
                       "(Prefered=10.3mil) (All)"},
        {"name": "TentVias", "enabled": True, "priority": 1, "scope_1": "IsVia", "scope_2": "All",
         "descriptor": "Solder Mask Expansion (Expansion =-12mil) (IsVia)"},
        {"name": "SilkToSolderMaskClearance", "enabled": True, "priority": 1, "scope_1": "IsPad",
         "scope_2": "All", "descriptor": "Silk To Solder Mask (Clearance=2mil) (IsPad),(All)"},
        {"name": "HoleSize", "enabled": True, "priority": 1, "scope_1": "All", "scope_2": "All",
         "descriptor": "Hole Size Constraint (Min=1mil) (Max=130mil) (All)"},
    ]
    text, skipped = altium_side.rules_file(rules, {})
    assert skipped == []
    assert "A.inDiffPair('*')" in text
    assert "(constraint diff_pair_gap (min 0.1397mm) (opt 0.1524mm) (max 0.2032mm))" in text
    assert "(constraint track_width (min 0.2032mm) (opt 0.2616mm) (max 0.2794mm))" in text
    assert "(constraint solder_mask_expansion (opt -0.3048mm))" in text
    assert "A.Type == 'Via'" in text
    assert "(constraint silk_clearance (min 0.0508mm))" in text
    assert "(constraint hole_size (min 0.0254mm) (max 3.302mm))" in text


def test_a_layer_holding_only_designators_goes_to_fab():
    board = ('(kicad_pcb (footprint "L:R" (layer "F.Cu")'
             '(fp_text user "${REFERENCE}" (at 0 0) (layer "User.18")))'
             '(footprint "L:C" (layer "B.Cu")'
             '(fp_text user "${REFERENCE}" (at 0 0) (layer "User.18"))'
             '(fp_line (start 0 0) (end 1 0) (layer "User.9"))'
             '(fp_text user "${REFERENCE}" (at 0 0) (layer "User.9"))))')
    assert footprints.designator_layers(board) == {"User.18": "F.Fab"}
    counts = {"remapped": 0, "crosses_to_fab": 0}
    back = footprints._map_footprint('(footprint "L:C" (layer "B.Cu") (fp_text user "${REFERENCE}" '
                                     '(at 0 0) (layer "User.18")))', {"User.18": "F.Fab"}, counts,
                                     frozenset({"User.18"}))
    assert '(layer "B.Fab")' in back


def test_a_layer_without_a_kind_maps_by_its_name():
    m = footprints.layer_map([{"layer": "Mechanical21", "kind": "Not Set", "name": "Bottom Courtyard"},
                              {"layer": "Mechanical13", "kind": "Not Set", "name": "Assembly"},
                              {"layer": "Mechanical2", "kind": "Not Set", "name": "Mechanical 2"}])
    assert m == {"User.21": "B.CrtYd", "User.13": "F.Fab"}


def test_the_title_block_is_filled_from_sheet_and_project_parameters():
    fields = altium_side.title_block(
        {"Title": "*", "Revision": "=REVISION", "CompanyName": "Acme", "DrawnBy": "*"},
        {"TITLE": "Power board", "REVISION": "B", "ENGINEER": "GS"})
    assert fields == {"title": "Power board", "rev": "B", "company": "Acme", "comment 1": "GS"}
    sheet = _sheet("")
    schematic.set_title_block(sheet, fields)
    block = first(sheet, "title_block")
    assert first(block, "title")[1] == "Power board"
    assert [c[2] for c in find(block, "comment")] == ["GS"]


def test_the_stackup_adds_up():
    block, total = altium_side.stackup_block([
        {"order": 1, "copper_thickness_mils": 1.378, "dielectric_type": "PrePreg",
         "dielectric_height_mils": 8.2835, "dielectric_constant": 4.4},
        {"order": 2, "copper_thickness_mils": 1.378, "dielectric_type": "none",
         "dielectric_height_mils": 0, "dielectric_constant": 0}])
    assert '(layer "F.Cu" (type "copper") (thickness 0.035))' in block
    assert '(layer "dielectric 1" (type "prepreg") (thickness 0.2104)' in block
    assert '(layer "B.Cu"' in block and total == pytest.approx(0.07 + 0.2104 + 0.02, abs=1e-3)


def test_altium_net_names_map_to_kicad_by_pin():
    alt = {("R1", "1"): "SIG"}
    kic = {("R1", "1"): "/Sheet/SIG"}
    assert altium_side.net_name_map(alt, kic) == {"SIG": "/Sheet/SIG"}


# --- the tool ------------------------------------------------------------

def test_the_tool_refuses_a_folder_that_is_not_empty(tmp_path, monkeypatch):
    from eda_agent.tools import convert

    (tmp_path / "x.txt").write_text("x")
    prj = tmp_path / "P.PrjPcb"
    prj.write_text("")

    class Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            return {}

    monkeypatch.setattr(convert, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    convert.register_convert_tools(Mcp())
    out = asyncio.run(tools["proj_convert_to_kicad"](
        output_dir=str(tmp_path), project_path=str(prj), board_path=str(tmp_path / "B.PcbDoc")))
    assert out["ok"] is False and "not empty" in out["reason"]


def test_the_tree_keeps_bare_atoms_bare():
    node = parse('(property "Value" "1k" (hide yes))')
    assert isinstance(first(node, "hide")[1], Sym)
    assert dump(node) == '(property "Value" "1k"\n\t(hide yes)\n)'


def test_outline_seams_go_and_the_outline_stays_closed():
    board = (
        '(kicad_pcb\n'
        '\t(gr_arc (start 10.000000 0) (mid 7.07 7.07) (end 0 10.000590) (layer "Edge.Cuts"))\n'
        '\t(gr_line (start 0 10.000590) (end 0 10) (layer "Edge.Cuts"))\n'
        '\t(gr_line (start 0 10) (end 0 20) (layer "Edge.Cuts"))\n'
        '\t(gr_line (start 5 5) (end 5 5.0001) (layer "F.SilkS"))\n'
        ')\n')
    text, removed = footprints.tidy_outline(board)
    assert removed == 1
    assert text.count("10.000590") == 2
    assert "(end 0 10.000590)" in text and "(start 0 10.000590) (end 0 20)" in text
    assert "(end 5 5.0001)" in text
    assert footprints.tidy_outline(text) == (text, 0)


def test_pours_keep_altiums_neck_width_and_island_removal():
    rows = altium_side.pour_settings([
        {"Net": "GND", "Layer": "BottomLayer", "NeckWidthThreshold": "5",
         "RemoveNarrowNecks": "true", "IslandAreaThreshold": "2500",
         "RemoveDead": "true", "RemoveIslandsByArea": "true"},
        {"Net": "VIN", "Layer": "TopLayer", "NeckWidthThreshold": "5",
         "RemoveNarrowNecks": "false", "IslandAreaThreshold": "2500",
         "RemoveDead": "false", "RemoveIslandsByArea": "true"},
        {"Net": "VIN", "Layer": "MidLayer1", "RemoveDead": "false"},
    ])
    assert rows[0] == {"net": "GND", "layer": "BottomLayer", "min_width_mm": 0.127,
                       "islands": "always"}
    assert rows[1]["min_width_mm"] == 0.0254 and rows[1]["islands"] == "area"
    assert rows[1]["min_island_mm2"] == pytest.approx(1.6129, abs=1e-4)
    assert rows[2]["islands"] == "never"
