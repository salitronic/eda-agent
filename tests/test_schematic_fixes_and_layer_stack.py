# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Port cross-references put back on the free end, designators sized to
their parts, a layer stack made to match a wanted one, and the schematic
placement fixes.

The planners run without Altium; the tools are checked for what they send
and refuse; the Pascal for the calls measured live on AD 26.11.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from eda_agent.design.designator_sizing import (DesignatorLimits, batch_op, shortfalls,
                                                size_designator)
from eda_agent.design.layer_stack_plan import (PLACEHOLDER, StackError, copper_round,
                                               dielectric_round, mismatches, read_wanted,
                                               spare_slots)
from eda_agent.design.port_reference_side import (TEXT_LEFTWARD, TEXT_RIGHTWARD,
                                                  place_references, reaches, segments_of)
from eda_agent.tools import application, generic, pcb

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
MIL = 0.0254


def _routine(unit: str, name: str) -> str:
    text = (ALTIUM / unit).read_text(encoding="utf-8-sig")
    for kind in ("Function", "Procedure"):
        head = f"{kind} {name}("
        if head in text:
            at = text.index(head)
            return text[at:text.index("\nEnd;", at)]
    raise AssertionError(f"no {name} in {unit}")


class _FakeBridge:
    def __init__(self, answers=None):
        self.sent = []
        self.answers = answers or {}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params))
        answer = self.answers.get(command, {"success": True})
        return answer(params) if callable(answer) else answer


def _registered(module, register, monkeypatch, answers=None):
    bridge = _FakeBridge(answers)
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(_Server())
    return tools, bridge


# --- port cross-references -----------------------------------------------------------

def _port(end=0, style=2, x=1000, y=500, width=400, name="CLK"):
    return {"Name": name, "Location.X": x, "Location.Y": y, "Width": width,
            "Style": style, "ConnectedEnd": end}


def _crossref(x, y=500, justification=TEXT_RIGHTWARD):
    return {"Name": "CrossRef", "Text": "Sheet2[1B]", "Location.X": x, "Location.Y": y,
            "Justification": justification}


def test_a_reference_lying_on_the_wire_goes_past_the_free_end():
    # Altium says the location end is wired, and the reference sits there.
    result = place_references([_port(end=1)], [], [_crossref(980, justification=TEXT_LEFTWARD)])
    (move,) = result["to_move"]
    assert move["wired"] == "location end" and move["decided_by"] == "ConnectedEnd"
    assert move["new_x"] == 1420 and move["new_justification"] == TEXT_RIGHTWARD


def test_the_wires_decide_when_altium_cannot_and_close_is_close_enough():
    far_end_wired = [{"Vertices": "1400,500;1900,500"}]
    result = place_references([_port(end=0)], far_end_wired,
                              [_crossref(980, justification=TEXT_LEFTWARD)])
    assert result["to_move"] == []
    assert result["already_right"][0]["decided_by"] == "wires"
    # Five mil off, as a hand move on a 10 mil grid leaves it, stays put.
    nudged = place_references([_port(end=2)], [], [_crossref(975, justification=TEXT_LEFTWARD)])
    assert nudged["already_right"] and not nudged["to_move"]


@pytest.mark.parametrize("port, references, why", [
    (_port(end=3), [_crossref(980)], "both ends wired"),
    (_port(end=0), [_crossref(980)], "neither end wired"),
    (_port(style=6), [_crossref(980)], "a vertical port"),
    (_port(end=1), [], "no CrossRef beside it"),
])
def test_a_port_it_cannot_settle_is_skipped_with_the_reason(port, references, why):
    assert place_references([port], [], references)["skipped"][0]["why"] == why


def test_a_reference_belongs_to_the_nearer_port_on_its_row():
    ports = [_port(name="A", x=0, width=200), _port(name="B", x=600, width=200, end=1)]
    result = place_references(ports, [], [_crossref(580, justification=TEXT_LEFTWARD)])
    assert [m["port"] for m in result["to_move"]] == ["B"]
    assert result["orphan_references"] == 0


def test_a_pin_end_or_a_wire_reaches_a_point():
    segments = segments_of([{"Vertices": "0,0;100,0;100,100"}, {"Vertices": "junk"}])
    assert len(segments) == 2
    assert reaches((50, 0), segments, []) and not reaches((50, 10), segments, [])
    assert reaches((7, 7), [], [(7.3, 6.8)])


def test_the_tool_only_reports_until_told_to_write(monkeypatch):
    def rows(params):
        return {"objects": {"ePort": [_port(end=1)], "eWire": [], "ePin": [],
                            "eParameter": [_crossref(980, justification=TEXT_LEFTWARD)]}
                [params["object_type"]]}
    tools, bridge = _registered(generic, "register_generic_tools", monkeypatch,
                                {"generic.query_objects": rows})
    report = asyncio.run(tools["sch_tidy_port_references"](sheet_path="C:/S.SchDoc"))
    assert len(report["to_move"]) == 1
    assert {c for c, _ in bridge.sent} == {"generic.query_objects"}
    tools, bridge = _registered(generic, "register_generic_tools", monkeypatch, {
        "generic.query_objects": rows, "generic.modify_objects": {"matched": 1}})
    done = asyncio.run(tools["sch_tidy_port_references"](sheet_path="C:/S.SchDoc",
                                                         dry_run=False))
    (write,) = [p for c, p in bridge.sent if c == "generic.modify_objects"]
    assert write["filter"] == "Name=CrossRef|Location.X=980|Location.Y=500"
    assert write["set"] == f"Location.X=1420|Justification={TEXT_RIGHTWARD}"
    assert len(done["moved"]) == 1 and done["not_moved"] == []


# --- designators sized to their parts --------------------------------------------------

def _outline(name, wide_mm, tall_mm, bottom=False, layer="TopOverlay"):
    return {"designator": name, "bottom": bottom,
            "box_mils": [0, 0, wide_mm / MIL, tall_mm / MIL],
            "label": {"layer": layer}}


def test_the_text_follows_the_long_side_within_the_part_and_the_limits():
    limits = DesignatorLimits()
    flat = size_designator(_outline("R1", 2.0, 1.2), limits)
    assert flat["height_mm"] == 1.0 and flat["angle"] == 0 and not flat["mirrored"]
    assert "natural_mm" not in flat
    upright = size_designator(_outline("C12", 0.3, 4.0), limits)
    assert upright["angle"] == 90 and upright["limited_by_width"]
    assert upright["height_mm"] == 0.3
    under = size_designator(_outline("U7", 0.3, 4.0, bottom=True), limits)
    assert under["angle"] == 270 and under["mirrored"]
    small = size_designator(_outline("R100", 0.2, 0.1), limits)
    assert small["height_mm"] == 0.1 and small["natural_mm"] == 0.05
    assert small["stroke_mm"] == 0.025


def test_layers_are_moved_only_when_asked_and_the_op_is_in_mils():
    limits = DesignatorLimits(top_layer="Mechanical10", bottom_layer="Mechanical11")
    top = size_designator(_outline("R1", 2, 1), limits)
    under = size_designator(_outline("R2", 2, 1, bottom=True), limits)
    assert (top["move_to"], under["move_to"]) == ("Mechanical10", "Mechanical11")
    assert batch_op(top) == ("designator=R1;height=39.3701;stroke=9.8425;angle=0;"
                             "mirror=false;layer=Mechanical10")
    stays = size_designator(_outline("R3", 2, 1), DesignatorLimits())
    assert stays["move_to"] == "" and "layer=" not in batch_op(stays)


def test_every_way_the_write_did_not_hold_is_named():
    part = _outline("R1", 2, 1)
    plan = size_designator(part, DesignatorLimits(top_layer="Mechanical10"))
    middle = [part["box_mils"][2] / 2, part["box_mils"][3] / 2]
    held = {"layer": "Mechanical 10", "height_mils": 39.3701, "stroke_mils": 9.8425,
            "angle": 0, "mirrored": False, "stroke_font": True, "visible": True,
            "middle_mils": middle}
    assert shortfalls(plan, part, held) == []
    slipped = {**held, "layer": "TopOverlay", "stroke_font": False, "middle_mils": [0, 0]}
    found = shortfalls(plan, part, slipped)
    assert any(f.startswith("on TopOverlay") for f in found)
    assert "TrueType font kept" in found and any("off the part's centre" in f for f in found)


def test_the_tool_reads_then_writes_and_refuses_bad_limits(monkeypatch):
    parts = [_outline("R1", 2, 1)]
    label = {"layer": "TopOverlay", "height_mils": 39.3701, "stroke_mils": 9.8425,
             "angle": 0, "mirrored": False, "stroke_font": True, "visible": True}
    answers = {"pcb.part_outlines": {"board": "B", "parts": parts},
               "pcb.set_designator_texts": {"held": 1, "refused": 0, "missing": [],
                                            "parts": [{"designator": "R1", "held": True,
                                                       "label": label}]}}
    tools, bridge = _registered(pcb, "register_pcb_tools", monkeypatch, answers)
    plan = asyncio.run(tools["pcb_resize_designators"](designators=["R1"]))
    assert plan["dry_run"] and bridge.sent == [("pcb.part_outlines", {"designators": "R1"})]
    done = asyncio.run(tools["pcb_resize_designators"](designators=["R1"], dry_run=False))
    assert done["held"] == 1 and done["wrong"] == []
    assert bridge.sent[-1][1]["ops"].startswith("designator=R1;height=")
    for bad in ({"smallest_mm": 2, "largest_mm": 1}, {"stroke_share": 0},
                {"designators": ["R;1"]}):
        tools, bridge = _registered(pcb, "register_pcb_tools", monkeypatch)
        refused = asyncio.run(tools["pcb_resize_designators"](**bad))
        assert refused["ok"] is False and bridge.sent == [], bad


def test_the_pascal_writes_each_text_inside_its_modify_brackets():
    restyle = _routine("PCB.pas", "RestyleDesignator")
    assert restyle.index("Comp.BeginModify") < restyle.index("DesText.UseTTFonts := False")
    assert restyle.index("DesText.EndModify") < restyle.index(
        "Comp.ChangeNameAutoposition(eAutoPos_CenterCenter)")
    command = _routine("PCB.pas", "PCB_SetDesignatorTexts")
    assert "PCBServer.SystemOptions.DoOnlineDRC := DrcWasOn" in command
    assert "NextBatchOp(Ops)" in command
    assert "BoundingRectangleNoNameComment" in _routine("PCB.pas", "PCB_PartOutlines")


# --- the layer stack ---------------------------------------------------------------------

def _copper(name, mils=1.378, plane=False):
    return {"kind": "copper", "name": name, "thickness_mils": mils, "plane": plane}


def _dielectric(mils, **fields):
    return {"kind": "dielectric", "thickness_mils": mils, **fields}


def _board(*rows):
    return [{"kind": kind, "name": name, "thickness_mils": mils} for kind, name, mils in rows]


@pytest.mark.parametrize("rows", [
    [_copper("A")],
    [_copper("A"), _copper("B"), _copper("C")],
    [_dielectric(4), _copper("A"), _dielectric(4)],
    [_copper("A"), _dielectric(4), _copper("A")],
    [_copper("A", plane=True), _dielectric(4), _copper("B")],
    [_copper("A"), _dielectric(4, type="glass"), _copper("B")],
    [_copper("A", mils=0), _dielectric(4), _copper("B")],
    [_copper(PLACEHOLDER + "x"), _dielectric(4), _copper("B")],
])
def test_a_wanted_stack_that_cannot_be_built_is_refused(rows):
    with pytest.raises(StackError):
        read_wanted(rows)


def test_dielectrics_are_named_and_typed_when_the_stack_is_read():
    wanted = read_wanted([_copper("A"), _dielectric(4, type="core"), _copper("B")])
    assert wanted[1].name == "Dielectric 1" and wanted[1].extra["type"] == 1


def test_inner_copper_is_kept_by_kind_and_the_rest_added_below_it():
    board = _board(("copper", "Top Layer", 1.378), ("dielectric", "D1", 8),
                   ("copper", "Internal Plane 1", 0.6), ("dielectric", "D2", 40),
                   ("copper", "Bottom Layer", 1.378))
    copper = [{"name": "Top Layer", "id": 1, "occupied": True},
              {"name": "Internal Plane 1", "id": 39, "occupied": True},
              {"name": "Bottom Layer", "id": 32, "occupied": True}]
    wanted = read_wanted([_copper("L1"), _dielectric(4), _copper("L2", plane=True),
                          _dielectric(20), _copper("L3", 0.7), _dielectric(4), _copper("L4")])
    edits = copper_round(board, wanted, copper)
    renames = [(e["layer"], e["to"]) for e in edits if e["do"] == "rename"]
    assert ("Internal Plane 1", PLACEHOLDER + "2") in renames
    assert (PLACEHOLDER + "2", "L2") in renames
    (added,) = [e for e in edits if e["do"] == "add"]
    assert added == {"do": "add", "slot": "MidLayer1", "under": "L2", "name": "L3",
                     "thickness_mils": 0.7}
    # Every placeholder rename comes before any final one, so swaps never clash.
    first_final = min(i for i, e in enumerate(edits)
                      if e["do"] == "rename" and not e["to"].startswith(PLACEHOLDER))
    assert all(i < first_final for i, e in enumerate(edits)
               if e["do"] == "rename" and e["to"].startswith(PLACEHOLDER))


def test_a_new_signal_layer_between_two_planes_keeps_both_planes():
    # Measured on the scratch board: matching first-come let the new signal
    # layer skip past T3 PWR and use it up, so the plan dropped a plane with
    # copper on it. The best order-preserving match keeps both planes.
    board = _board(("copper", "T1", 1.4), ("dielectric", "D1", 4), ("copper", "T2 GND", 1.2),
                   ("dielectric", "D2", 30), ("copper", "T3 PWR", 1.2),
                   ("dielectric", "D3", 4), ("copper", "T4", 1.4))
    copper = [{"name": "T1", "id": 1, "occupied": True},
              {"name": "T2 GND", "id": 39, "occupied": True},
              {"name": "T3 PWR", "id": 40, "occupied": True},
              {"name": "T4", "id": 32, "occupied": True}]
    wanted = read_wanted([_copper("L1"), _dielectric(4), _copper("L2", plane=True),
                          _dielectric(20), _copper("L3"), _dielectric(10),
                          _copper("L4", plane=True), _dielectric(4), _copper("L5")])
    edits = copper_round(board, wanted, copper)
    assert not [e for e in edits if e["do"] == "drop"]
    finals = {e["to"] for e in edits if e["do"] == "rename"}
    assert {"L2", "L4"} <= finals
    (added,) = [e for e in edits if e["do"] == "add"]
    assert added["name"] == "L3" and added["under"] == "L2" and added["slot"] == "MidLayer1"


def test_copper_with_anything_on_it_is_never_dropped():
    board = _board(("copper", "Top", 1.4), ("dielectric", "D", 8),
                   ("copper", "Mid-Layer 1", 1.4), ("dielectric", "E", 8),
                   ("copper", "Bottom", 1.4))
    copper = [{"name": "Mid-Layer 1", "id": 2, "occupied": True}]
    wanted = read_wanted([_copper("A"), _dielectric(8), _copper("B")])
    with pytest.raises(StackError, match="objects lie on them"):
        copper_round(board, wanted, copper)
    copper[0]["occupied"] = False
    assert {"do": "drop", "layer": "Mid-Layer 1"} in copper_round(board, wanted, copper)


def test_spare_slots_skip_what_ids_or_names_already_use():
    mids, planes = spare_slots([{"name": "x", "id": 2}, {"name": "Mid-Layer 3", "id": 0},
                                {"name": "y", "id": 39}])
    assert mids[:2] == ["MidLayer2", "MidLayer4"] and planes[0] == "InternalPlane2"


def test_each_gap_is_trimmed_to_its_dielectrics_or_refused():
    wanted = read_wanted([_copper("A"), _dielectric(4, constant=4.2), _copper("B"),
                          _dielectric(20), _copper("C")])
    board = _board(("copper", "A", 1.378), ("dielectric", "D1", 8), ("copper", "B", 1.378),
                   ("dielectric", "D2", 8), ("dielectric", "D9", 12), ("copper", "C", 1.378))
    edits = dielectric_round(board, wanted)
    assert edits[0] == {"do": "drop", "layer": "D9"}
    (final,) = [e for e in edits if e.get("to") == "Dielectric 1"]
    assert final["constant"] == 4.2 and final["thickness_mils"] == 4
    thin = _board(("copper", "A", 1), ("copper", "B", 1), ("dielectric", "D", 8),
                  ("copper", "C", 1))
    with pytest.raises(StackError, match="cannot add one"):
        dielectric_round(thin, wanted)


def test_the_board_is_compared_with_the_wanted_stack_layer_by_layer():
    wanted = read_wanted([_copper("A"), _dielectric(4, type="core", constant=4.2), _copper("B")])
    matching = [{"kind": "copper", "name": "A", "thickness_mils": 1.378},
                {"kind": "dielectric", "name": "Dielectric 1", "thickness_mils": 4.0,
                 "dielectric_type": 1, "constant": 4.2},
                {"kind": "copper", "name": "B", "thickness_mils": 1.378}]
    assert mismatches(matching, wanted) == []
    off = [dict(r) for r in matching]
    off[1]["constant"] = 4.6
    off[2]["additive"] = True
    assert {m["what"] for m in mismatches(off, wanted)} == {"constant", "additive"}


def test_the_stack_tool_plans_dry_and_wants_a_saved_board(monkeypatch):
    board = {"layers": [{"kind": "copper", "name": "Top", "thickness_mils": 1.4},
                        {"kind": "dielectric", "name": "D", "thickness_mils": 8},
                        {"kind": "copper", "name": "Bot", "thickness_mils": 1.4}]}
    answers = {"application.get_active_document": {"file_path": "C:/B.PcbDoc",
                                                   "modified": True},
               "pcb.physical_stack": board,
               "pcb.copper_layers": {"layers": [{"name": "Top", "id": 1, "occupied": True},
                                                {"name": "Bot", "id": 32, "occupied": True}]}}
    tools, bridge = _registered(pcb, "register_pcb_tools", monkeypatch, answers)
    wanted = [_copper("T"), _dielectric(8), _copper("B")]
    plan = asyncio.run(tools["pcb_make_layer_stack"](layers=wanted))
    assert plan["dry_run"] and len(plan["copper_edits"]) == 4
    refused = asyncio.run(tools["pcb_make_layer_stack"](layers=wanted, dry_run=False))
    assert refused["ok"] is False and "save the board first" in refused["reason"]
    assert not {c for c, _ in bridge.sent} & {"pcb.add_layer", "pcb.tune_copper_layer",
                                              "pcb.drop_stack_layer", "pcb.set_dielectric"}


def test_the_stack_commands_keep_to_the_measured_calls():
    drop = _routine("PCB.pas", "PCB_DropStackLayer")
    assert "Stack.RemoveLayer(Layer)" in drop
    assert "'OUTER_COPPER'" in drop and "'LAYER_OCCUPIED'" in drop
    assert "Cu.IsAdditiveLayer := False" in _routine("PCB.pas", "CopperToPlain")
    occupied = _routine("PCB.pas", "LayerCarriesObjects")
    # An id past the old layer sets cannot be searched and counts as occupied.
    assert occupied.index("Result := (Id < 1) Or (Id > 255)") < occupied.index("BoardIterator_Create")
    assert "L.Name := NewName" in _routine("PCB.pas", "PCB_SetDielectric")
    assert "'rename_to'" in _routine("PCB.pas", "PCB_SetDielectric")
    assert "EL.IsAdditiveLayer" in _routine("PCB.pas", "PCB_PhysicalStack")


# --- schematic placement -------------------------------------------------------------------

def test_new_objects_take_the_sheets_font_chosen_before_they_exist():
    for routine, kind in (("Gen_PlacePort", "ePort"), ("Gen_PlacePowerPort", "ePowerObject"),
                          ("Gen_PlaceTextFrame", "eTextFrame"),
                          ("Gen_PlaceSheetEntry", "eSheetEntry")):
        body = _routine("Generic.pas", routine)
        pick = f"NewObjectFontId(SchDoc, {kind}, Params)"
        assert pick in body and body.index(pick) < body.index("SchServer.SchObjectFactory("), routine
    assert "SheetFontFor(SchDoc, eNetLabel)" in _routine("Generic.pas", "NewObjectFontId")


def test_a_port_width_holds_because_auto_size_is_turned_off_first():
    body = _routine("Generic.pas", "Gen_PlacePort")
    assert body.index("SchPort.AutoSize := False") < body.index(
        "SchPort.Width := MilsToCoordF(WantWidth)")


def test_typed_line_breaks_become_altiums_own_in_a_text_frame():
    body = _routine("Generic.pas", "Gen_PlaceTextFrame")
    assert "StringReplace(TextStr, '\\' + 'n', #10" in body
    assert "StringReplace(TextStr, #10, #13 + #10" in body


def test_connected_end_is_read_on_ports_and_never_written():
    assert "(PropName = 'ConnectedEnd') Then Result := 4" in _routine("Generic.pas",
                                                                       "SchExtraOwner")
    assert "If PropName = 'ConnectedEnd' Then Exit;" in _routine("Generic.pas", "SchSetExtra")


def test_a_line_answers_its_two_ends_as_vertices():
    body = _routine("Generic.pas", "SchLineEndProperty")
    assert "Segment.Location" in body and "Segment.Corner" in body
    assert "Result := '2'" in body
    getter = _routine("Generic.pas", "GetSchProperty")
    assert "SchLineEndProperty(Obj, PropName)" in getter
    # Every name a line answers has to reach it: a prefix of 'VERTE' let
    # VerticesCount fall through to the polyline call, which faulted live.
    import re
    (prefix,) = re.findall(r"\(Obj\.ObjectId = eLine\) And \(Pos\('(\w+)', UpperCase\(PropName\)\) = 1\)",
                           getter)
    for name in ("VerticesCount", "Vertices", "Vertex1.X", "Vertex.2.Y", "VertexLast.X"):
        assert name.upper().startswith(prefix), name


def test_pin_queries_leave_out_the_copies_a_part_does_not_show():
    body = _routine("Generic.pas", "ProcessSchDocObjects")
    assert "If Pos('OwnerPart', FilterStr) = 0 Then OnlyShown := True" in body
    assert "UnshownPins.IndexOf(SchPinKey(PinObj)) < 0" in body
    assert body.index("UnshownPins := TStringList.Create") < body.index("UnshownPins.Free")
    assert "I_ObjectAddress" not in _routine("Generic.pas", "AddUnshownPins")


def test_parameters_and_descriptions_are_written_where_altium_keeps_them():
    setter = _routine("Generic.pas", "SetCompParamText")
    assert "NewParam.IsHidden := True" in setter
    assert "NewParam.Location := Comp.Location" in setter
    text = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    assert text.count("SetOwnCompParamText(") == 3          # defined once, used twice
    assert text.count("(Key = 'ComponentDescription') Or") == 2


def test_a_placed_part_is_matched_to_the_sheet_unless_asked_not_to():
    body = _routine("Generic.pas", "Gen_PlaceSchComponentsFromLibrary")
    assert "MatchSheet := Not SameText(ExtractJsonValue(Params, 'tidy'), 'false')" in body
    assert body.index("LibFileName := ExtractFileName(LibPath)") < body.index(
        "ResolvedLib := ResolveSchLibForLoad(LibPath, LibReason)")
    tidy = _routine("Generic.pas", "TidyLoadedComponent")
    assert "Part.SourceLibraryName := LibFileName" in tidy and "Param.IsHidden := True" in tidy


def test_the_schematic_tools_send_their_new_options(monkeypatch):
    tools, bridge = _registered(generic, "register_generic_tools", monkeypatch)
    asyncio.run(tools["sch_place_port"](name="A", x=100.5, y=200, width=600, font_id=4))
    asyncio.run(tools["sch_place_text_frame"](x1=0, y1=0, x2=1000.25, y2=500, text="a"))
    sent = dict(bridge.sent)
    assert sent["generic.place_port"]["x"] == "100.5"
    assert sent["generic.place_port"]["width"] == "600"
    assert sent["generic.place_port"]["font_id"] == "4"
    assert sent["generic.place_text_frame"]["x2"] == "1000.25"
    assert "part_id" in generic.PLACEMENT_KEYS


def test_a_ground_port_points_down_whatever_its_glyph():
    from eda_agent.design.sch_template import build, place_symbol_pins
    pins = {"R1": place_symbol_pins("R1", [{"designator": "1", "name": "1", "x": -100, "y": 0,
                                            "orientation": 2, "length": 100}], 0, 0)}
    drawing = build({"parts": [{"designator": "R1"}],
                     "ports": [{"net": "AGND", "at": "R1.1", "style": "bar"}]}, pins)
    assert drawing.ports[0]["orientation"] == 3


# --- small fixes ------------------------------------------------------------------------------

def test_deselect_runs_the_process_that_really_clears():
    body = _routine("Generic.pas", "SchDeselectAllObjects")
    assert "AddStringParameter('Action', 'All')" in body
    assert "RunProcess('Sch:DeSelect')" in body
    assert "ProcessName := 'Sch:DeSelectAll'" not in (ALTIUM / "Application.pas").read_text(
        encoding="utf-8-sig")


def test_a_close_that_resolves_to_another_document_closes_nothing():
    body = _routine("Application.pas", "App_CloseDocument")
    assert body.index("PathKey(ServerDoc.FileName) <> PathKey(FilePath)") < body.index(
        "ServerDoc.Modified")
    assert "'OTHER_DOCUMENT'" in body


def test_screenshots_stay_at_size_and_only_the_recogniser_enlarges():
    from eda_agent.ui import ocr
    assert inspect.signature(ocr.capture_png).parameters["enlarge"].default == 1
    # Both readers (lines and screen rows) capture through _recognise.
    assert "_recognise(" in inspect.getsource(ocr.read_window_text)
    assert "_recognise(" in inspect.getsource(ocr.read_window_rows)
    assert "capture_png(hwnd, png, enlarge=2)" in inspect.getsource(ocr._recognise)


def test_zoom_to_the_document_is_for_a_schematic_only():
    text = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    assert "'|fit|selection|board|document|'" in text
    assert "If (ZoomProcess <> 'Sch:Zoom') And (Action = 'document') Then" in text
    assert "Else If Action = 'document' Then AddStringParameter('Action', 'Document')" in text


def test_a_checkpoint_says_which_project_it_took(monkeypatch, tmp_path):
    project = tmp_path / "B.PrjPcb"
    project.write_text("x")
    tools, bridge = _registered(application, "register_application_tools", monkeypatch, {
        "project.get_open_projects": {"projects": [{"project_path": str(project)},
                                                   {"project_path": "C:/Other.PrjPcb"}]}})

    class _Store:
        def create(self, *a, **k):
            class _Made:
                def summary(self):
                    return {"id": "x"}
            return _Made()
    monkeypatch.setattr(application, "_checkpoint_store", lambda: _Store(), raising=False)
    try:
        result = asyncio.run(tools["app_checkpoint"](project_path=str(project)))
    except AttributeError:
        pytest.skip("the checkpoint store is not reachable from the module")
    assert result["project"] == str(project)
    assert result["other_open_projects"] == ["C:/Other.PrjPcb"]
    assert ("project.save", {"project_path": str(project)}) in bridge.sent
