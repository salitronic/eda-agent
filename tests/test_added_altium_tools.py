# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tools added for Altium.

Symbol parts, display modes and aliases, model and part STEP export,
impedance profiles, variant entries, net colours, the rule Altium applies,
rule scopes, net clean-up, drill pairs, declared classes and rooms,
sheet zones, xSignals, shared packages, part text positions, net naming
objects, the library rule check, project setup, board grids, part model
export, auto-save, board and schematic editor preferences, and the extra
board, pin, parameter and port properties. What each sends, what each refuses before Altium sees
it, and the Pascal shapes that keep a refused or missing call from
passing for a success.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import application, generic, library, pcb, project

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(unit: str, name: str) -> str:
    text = (ALTIUM / unit).read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        return {"success": True}


def _tools(module, register, monkeypatch):
    bridge = _Bridge()
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

    getattr(module, register)(_Mcp())
    return captured, bridge


def test_symbol_parts_sends_only_what_was_asked(monkeypatch, tmp_path):
    lib = tmp_path / "Parts.SchLib"
    lib.write_bytes(b"")
    tools, bridge = _tools(library, "register_library_tools", monkeypatch)
    asyncio.run(tools["lib_set_symbol_parts"]("OPAMP", library_path=str(lib),
                                              part_count=4))
    assert bridge.sent == [("library.set_symbol_parts", {
        "name": "OPAMP", "part_count": "4", "library_path": str(lib)})]


@pytest.mark.parametrize("kwargs", [
    {"name": " ", "part_count": 2},
    {"name": "U", "part_count": 0},
    {"name": "U", "display_mode_count": 0},
    {"name": "U"},
    {"name": "U", "part_count": 2, "library_path": "C:/no/such.SchLib"},
])
def test_symbol_parts_refuses_before_altium(monkeypatch, kwargs):
    tools, bridge = _tools(library, "register_library_tools", monkeypatch)
    result = asyncio.run(tools["lib_set_symbol_parts"](**kwargs))
    assert bridge.sent == [] and result["ok"] is False


def test_symbol_parts_reads_back_every_step():
    body = _body("Library.pas", "Lib_SetSymbolParts")
    # Each add or remove is checked against the count before it, so a
    # command that does nothing stops the loop and says so.
    for call, check in (
            ("SetState_CurrentSchComponentAddPart", "If Comp.PartCount <= Was Then"),
            ("SetState_CurrentSchComponentRemovePart", "If Comp.PartCount >= Was Then"),
            ("SetState_CurrentSchComponentAddDisplayMode",
             "If Comp.DisplayModeCount <= Was Then"),
            ("SetState_CurrentSchComponentRemoveDisplayMode",
             "If Comp.DisplayModeCount >= Was Then")):
        assert body.index(call) < body.index(check), call
    assert body.count("Guard < 64") == 4
    assert "pins_removed" in body and "PinsBefore - PinsAfter" in body
    # A part is removed from the end: the last part is made current first.
    assert body.index("Comp.CurrentPartID := Was;") < body.index(
        "SetState_CurrentSchComponentRemovePart")


def test_model_export_sends_and_refuses(monkeypatch, tmp_path):
    lib = tmp_path / "Ics.PcbLib"
    lib.write_bytes(b"")
    out = tmp_path / "out" / "SOT23.step"
    tools, bridge = _tools(library, "register_library_tools", monkeypatch)
    asyncio.run(tools["lib_export_3d_model"]("SOT23", str(out), library_path=str(lib)))
    assert bridge.sent == [("library.export_model_step", {
        "footprint_name": "SOT23", "out_path": str(out), "body_index": "1",
        "library_path": str(lib)})]
    assert out.parent.is_dir()
    for kwargs in ({"footprint_name": "SOT23", "out_path": "rel.step"},
                   {"footprint_name": "", "out_path": str(out)},
                   {"footprint_name": "SOT23", "out_path": str(out), "body_index": 0},
                   {"footprint_name": "SOT23", "out_path": str(out),
                    "library_path": str(tmp_path / "Missing.PcbLib")}):
        bridge.sent.clear()
        assert asyncio.run(tools["lib_export_3d_model"](**kwargs))["ok"] is False
        assert bridge.sent == []


def test_model_export_reports_a_new_file_only():
    body = _body("Library.pas", "Lib_ExportModelStep")
    assert "Chosen.ExportToStep(OutPath);" in body
    assert "(AgeAfter <> -1) And (AgeAfter <> AgeBefore)" in body
    # Only STEP bodies (ModelType 1) are counted for body_index.
    assert "If Kind = 1 Then" in body
    assert "WRONG_DOCUMENT_FOCUSED" in body


def test_impedance_profiles_tool_sends_its_command(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)
    asyncio.run(tools["pcb_get_impedance_profiles"]())
    assert bridge.sent == [("pcb.get_impedance_profiles", {})]


def test_impedance_profiles_guard_every_read():
    body = _body("PCB.pas", "PCB_GetImpedanceProfiles")
    # Every Profile./Trace. member read sits inside a Try, so a member
    # this build lacks costs one field, counted in read_errors.
    reads = [ln.strip() for ln in body.splitlines()
             if re.search(r"\b(Profile|Trace)\.\w+", ln) and ":=" in ln]
    assert reads and all(ln.startswith("Try ") for ln in reads), reads
    assert "read_errors" in body
    # Typed with the real interfaces: the declared type decides dispatch.
    assert "Profile : IPCB_ImpedanceProfile;" in body
    assert "Trace : IPCB_TraceImpedance;" in body


def test_step_export_api_handler_checks_for_a_new_file():
    body = _body("Project.pas", "Proj_ExportSTEPApi")
    assert "Exporter.ExportBoard3DModel(OutputPath, Format, Board, Proj);" in body
    assert "(AgeAfter <> -1) And (AgeAfter <> AgeBefore)" in body
    assert "Exporter : IPCB_Board3DModelExporter;" in body


def _sent(module, register, monkeypatch, tool, **kwargs):
    tools, bridge = _tools(module, register, monkeypatch)
    result = asyncio.run(tools[tool](**kwargs))
    return result, bridge.sent


def test_variation_sends_and_refuses(monkeypatch):
    _, sent = _sent(project, "register_project_tools", monkeypatch,
                    "proj_set_component_variation", variant="Lite",
                    designator="R12", kind="not_fitted")
    assert sent == [("project.set_component_variation",
                     {"variant": "Lite", "designator": "R12", "kind": "not_fitted"})]
    for kwargs in ({"variant": "Lite", "designator": "R12", "kind": "maybe"},
                   {"variant": "", "designator": "R12", "kind": "fitted"},
                   {"variant": "Lite", "designator": "R12", "kind": "alternate"}):
        result, sent = _sent(project, "register_project_tools", monkeypatch,
                             "proj_set_component_variation", **kwargs)
        assert sent == [] and result["ok"] is False


def test_net_color_goes_to_altium_as_bgr(monkeypatch):
    _, sent = _sent(project, "register_project_tools", monkeypatch,
                    "proj_net_color", net="VBUS", color="#FF8000")
    assert sent == [("project.net_color", {"net": "VBUS", "color": str(0x0080FF)})]
    result, sent = _sent(project, "register_project_tools", monkeypatch,
                         "proj_net_color", net="VBUS", color="orange")
    assert sent == [] and result["ok"] is False


def test_rule_tools_need_their_objects(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_find_dominant_rule",
                    object1="U1.3", object2="net:GND", rule_name="Clearance")
    assert sent == [("pcb.find_dominant_rule",
                     {"object1": "U1.3", "object2": "net:GND", "rule_name": "Clearance"})]
    result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                         "pcb_find_dominant_rule", object1="U1")
    assert sent == [] and result["ok"] is False
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_check_rule_scope",
                    rule_name="Width_VBUS", object1="net:VBUS")
    assert sent == [("pcb.rule_scope_check",
                     {"rule_name": "Width_VBUS", "object1": "net:VBUS"})]


def test_drill_pairs_bound_their_input(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_drill_pair",
                    index=1, backdrill=True)
    assert sent == [("pcb.set_drill_pair", {"index": "1", "backdrill": "true"})]
    result, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_drill_pair",
                         index=1)
    assert sent == [] and result["ok"] is False


def test_aliases_and_sheet_options_send_what_was_asked(monkeypatch, tmp_path):
    lib = tmp_path / "Logic.SchLib"
    lib.write_bytes(b"")
    _, sent = _sent(library, "register_library_tools", monkeypatch,
                    "lib_set_symbol_aliases", name="74HC00", library_path=str(lib),
                    add=["74HCT00", " "], remove=["74LS00"])
    assert sent == [("library.set_symbol_aliases", {
        "name": "74HC00", "add": "74HCT00", "remove": "74LS00",
        "library_path": str(lib)})]
    _, sent = _sent(generic, "register_generic_tools", monkeypatch,
                    "sch_set_sheet_options", zones_x=6, reference_zones=False)
    assert sent == [("generic.sheet_options",
                     {"zones_x": "6", "reference_zones": "false"})]
    result, sent = _sent(generic, "register_generic_tools", monkeypatch,
                         "sch_set_sheet_options", zones_y=0)
    assert sent == [] and result["ok"] is False


def test_variation_is_read_back_after_the_write():
    body = _body("Project.pas", "Proj_SetComponentVariation")
    write = body.index("CompVar.DM_SetVariationKind(Kind);")
    assert body.index("DM_FindComponentVariationByUniqueId", write) > write
    assert "DM_RemoveComponentVariation(Found)" in body
    assert "applied" in body


def test_rule_answers_come_from_altium():
    body = _body("PCB.pas", "PCB_FindDominantRule")
    assert "Board.FindDominantRuleForObject(P1, Kind)" in body
    assert "Board.FindDominantRuleForObjectPair(P1, P2, Kind)" in body
    body = _body("PCB.pas", "PCB_RuleScopeCheck")
    assert "Rule.CheckUnaryScope(P1)" in body and "Rule.CheckBinaryScope(P1, P2)" in body


def test_the_fast_designator_lookup_is_checked_and_backed_up():
    body = _body("PCB.pas", "FindPCBComponentByDesignator")
    fast = body.index("Board.GetPcbComponentByRefDes(Desig)")
    assert body.index("If NameStr = Desig Then", fast) > fast
    assert "BoardIterator_Create" in body[fast:]


def test_extra_properties_fit_only_their_owner():
    text = (ALTIUM / "PCBGeneric.pas").read_text(encoding="utf-8-sig")
    assert "(ExtraPCBPropertyOwner(PropName) > 0);" in text
    body = _body("PCBGeneric.pas", "PCBSetExtra")
    assert ("If (PropName = 'IsCounterHole') Or (PropName = 'IsBGA') Or\n"
            "       (PropName = 'PackageSpecificHash') Or (PropName = 'ConvertedString') Or\n"
            "       (PropName = 'FlippedOnLayer') Or (PropName = 'IsVirtualPin') Or\n"
            "       (PropName = 'InvRectWidth') Or (PropName = 'InvRectHeight') Then Exit;") in body
    # A text's TrueType font is writable; an empty name is refused.
    assert "(PropName = 'FontName') Then Exit;" not in body
    assert "If Trim(Value) = '' Then Exit;" in body
    # Read-only first, then the type check has already passed, then values.
    assert body.index("ExtraPCBPropertyFits(Oid, PropName)") < body.index("Result := -1;")
    swaps = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    assert "SwapPin.SetState_SwapIdPart(Value)" in swaps
    assert "And (Obj.ObjectId <> ePin))" in swaps


def test_net_color_none_is_altiums_unset_flag(monkeypatch):
    _, sent = _sent(project, "register_project_tools", monkeypatch,
                    "proj_net_color", net="VBUS", color="none")
    assert sent == [("project.net_color", {"net": "VBUS", "color": "536870912"})]
    body = _body("Project.pas", "Proj_NetColor")
    assert "(Wanted <> 536870912)" in body


def test_an_unset_tolerance_reads_and_writes_as_unset():
    get = _body("PCBGeneric.pas", "PCBGetExtra")
    assert "If Tol = UnsetTolerance(0) Then Result := 'unset'" in get
    put = _body("PCBGeneric.pas", "PCBSetExtra")
    assert "(LowerCase(Value) = 'unset')" in put
    assert put.index("'unset'") < put.index("If Not IsFloatStr(Value) Then Exit;")


def test_variant_creation_tries_the_api_first():
    body = _body("Project.pas", "Proj_CreateVariant")
    assert body.index("Project.DM_AddProjectVariant") < body.index("RunProcess('WorkspaceManager:VariantManagement')")


def test_xsignals_send_pins_and_refuse_bad_ones(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_create_xsignals",
                    pins=["U4.4", " LED1.3 "], class_name="LED_DATA")
    assert sent == [("pcb.create_xsignals",
                     {"pins": "U4.4\tLED1.3", "class_name": "LED_DATA"})]
    for pins in (["U4.4"], ["U4.4", "LED1"], ["U4.4", ".3"], []):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_create_xsignals", pins=pins)
        assert sent == [] and result["ok"] is False, pins


def test_xsignal_listing_names_pads_and_counts_copper():
    body = _body("PCB.pas", "XSignalJson")
    # Tracks, arcs and vias are counted, not listed: a routed xSignal
    # otherwise lists every segment.
    assert body.index("Inc(Tracks);") < body.index("GetPinPairItemDisplayName")
    assert body.index("Inc(Vias);") < body.index("GetPinPairItemDisplayName")
    assert "JsonRaw('path'," in body
    create = _body("PCB.pas", "PCB_CreateXSignals")
    # New xSignals are found by name against the list taken before.
    assert create.index("BeforeNames := BeforeNames + PP.Name") < create.index("Mgr.CreateFromPins(Pins)")
    assert "If Pos(#9 + PP.Name + #9, BeforeNames) > 0 Then Continue;" in create
    assert "'NO_PIN'" in create


def test_same_package_groups_are_confirmed_member_by_member(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                    "pcb_find_same_package", designator=" R1 ")
    assert sent == [("pcb.find_same_package", {"designator": "R1"})]
    body = _body("PCB.pas", "PCB_FindSamePackage")
    # A shared hash is a candidate; IsPackageEqual decides.
    assert "Same := First.IsPackageEqual(Comp);" in body
    assert "If Size < 2 Then Continue;" in body
    assert "parts_without_hash" in body


@pytest.mark.parametrize("given, number", [
    ("manual", 0), ("top_left", 1), ("bottom-center", 6), ("Bottom Right", 9),
    ("4", 4), (5, 5)])
def test_text_anchors_take_names_or_numbers(given, number):
    assert pcb._text_anchor(given) == number


def test_text_position_sends_and_refuses(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_auto_position_text",
                    designators=["LED2", "R4"], name_position="top_center")
    assert sent == [("pcb.auto_position_text",
                     {"designators": "LED2\tR4", "name_position": "4"})]
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_auto_position_text")
    assert sent == [("pcb.auto_position_text", {})]
    for kwargs in ({"name_position": "middle"}, {"comment_position": "10"},
                   {"designators": []}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_auto_position_text", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs


def test_text_position_reports_whether_the_text_is_shown():
    # Measured: Altium records the anchor on hidden text but does not
    # move it, so the reply has to say which texts are hidden.
    body = _body("PCB.pas", "CompTextPosJson")
    assert "JsonBool('name_shown', NameOn)" in body
    assert "JsonBool('comment_shown', CommentOn)" in body
    run = _body("PCB.pas", "PCB_AutoPositionText")
    assert "Comp.ChangeNameAutoposition(NamePos);" in run
    assert "Comp.AutoPosition_NameComment;" in run


def test_part_text_properties_write_through_the_moving_call():
    body = _body("PCBGeneric.pas", "PCBSetExtra")
    assert "Comp.ChangeNameAutoposition(N)" in body
    assert "Comp.ChangeCommentAutoposition(N)" in body
    assert "Comp.LockStrings := B" in body
    get = _body("PCBGeneric.pas", "PCBGetExtra")
    assert "Result := Comp.PackageSpecificHash" in get


def test_net_items_sends_and_refuses(monkeypatch):
    _, sent = _sent(project, "register_project_tools", monkeypatch,
                    "proj_get_net_items", net="GND", named_only=True)
    assert sent == [("project.get_net_items",
                     {"limit": "500", "net": "GND", "named_only": "true"})]
    result, sent = _sent(project, "register_project_tools", monkeypatch,
                         "proj_get_net_items", limit=0)
    assert sent == [] and result["ok"] is False


def test_net_items_are_in_mils_and_name_power_ports():
    body = _body("Project.pas", "NetNamingItemJson")
    assert "JsonFloat('x', X / 10000)" in body
    # A power port has no text of its own; the net name stands in.
    assert body.index("If Text = '' Then") < body.index("Item.DM_NetName")
    listing = _body("Project.pas", "Proj_GetNetItems")
    assert "nets_examined" in listing and "truncated" in listing


def test_project_setup_sends_only_what_was_asked(monkeypatch):
    _, sent = _sent(project, "register_project_tools", monkeypatch, "proj_project_setup")
    assert sent == [("project.project_setup", {})]
    _, sent = _sent(project, "register_project_tools", monkeypatch, "proj_project_setup",
                    move_document="C:/p/A.SchDoc", move_to_index=2,
                    add_search_path="C:/libs", include_subfolders=True,
                    allow_port_net_names=False)
    assert sent == [("project.project_setup", {
        "move_document": "C:/p/A.SchDoc", "move_to_index": "2",
        "add_search_path": "C:/libs", "include_subfolders": "true",
        "allow_port_net_names": "false"})]
    for kwargs in ({"move_document": "C:/p/A.SchDoc"}, {"move_to_index": 1},
                   {"move_document": "C:/p/A.SchDoc", "move_to_index": -1}):
        result, sent = _sent(project, "register_project_tools", monkeypatch,
                             "proj_project_setup", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs


def test_project_setup_reads_every_change_back():
    body = _body("Project.pas", "Proj_ProjectSetup")
    for write, check in (
            ("Project.DM_MoveSourceDocument(MoveDoc, NewIdx);", "If NowIdx <> NewIdx Then"),
            ("Project.DM_AddSearchPath(AddPath", "If Not Found Then"),
            ("Project.DM_SetAllowPortNetNames(PortStr = 'true');",
             "If Project.DM_GetAllowPortNetNames <> (PortStr = 'true') Then"),
            ("Project.DM_SetAllowSheetEntryNetNames(EntryStr = 'true');",
             "If Project.DM_GetAllowSheetEntryNetNames <> (EntryStr = 'true') Then")):
        assert body.index(write) < body.index(check), write
    assert "'NO_DOCUMENT'" in body


def test_schlib_check_sends_and_refuses(monkeypatch, tmp_path):
    lib = tmp_path / "Parts.SchLib"
    lib.write_bytes(b"")
    _, sent = _sent(library, "register_library_tools", monkeypatch, "lib_check_schlib",
                    library_path=str(lib), checks=["missing_footprint", "duplicate_pins"])
    assert sent == [("library.check_schlib", {
        "checks": "missing_footprint\tduplicate_pins", "library_path": str(lib)})]
    for kwargs in ({"checks": ["missing_everything"]}, {"checks": []},
                   {"library_path": str(tmp_path / "None.SchLib")}):
        result, sent = _sent(library, "register_library_tools", monkeypatch,
                             "lib_check_schlib", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs


def test_schlib_check_hides_the_report_and_frees_the_checker():
    body = _body("Library.pas", "Lib_CheckSchLib")
    assert "Checker.ShowReport := False;" in body
    assert body.index("Ran := Checker.Run;") < body.index("Sch_LibraryRuleChecker_Destroy(Checker)")
    assert body.index("Finally") < body.index("Sch_LibraryRuleChecker_Destroy")
    for check in library._SCHLIB_CHECKS:
        assert f"#9 + '{check}' + #9" in body, check


def test_schlib_check_report_is_read_per_symbol(tmp_path):
    lib = tmp_path / "Parts.SchLib"
    lib.write_bytes(b"")
    (tmp_path / "Parts.ERR").write_text(
        "Component Rule Check Report for : Parts.SchLib\n\n\n"
        "Name                Errors\n"
        "------------------------------------------------------------------\n"
        "Component_1         (No Footprint) (No Description) \n"
        "LM317 ADJ           (Duplicate Pins: 2) \n", encoding="utf-8")
    report, problems = library._schlib_check_report(lib, 0)
    assert report == tmp_path / "Parts.ERR"
    assert problems == [
        {"symbol": "Component_1", "problems": ["No Footprint", "No Description"]},
        {"symbol": "LM317 ADJ", "problems": ["Duplicate Pins: 2"]}]
    # A report older than the run is not this run's report.
    assert library._schlib_check_report(lib, 4102444800) == (None, [])


def test_xsignal_delete_sends_and_refuses(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_delete_xsignals",
                    names=["A_PP1", " B_PP2 "])
    assert sent == [("pcb.delete_xsignals", {"names": "A_PP1\tB_PP2"})]
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_delete_xsignals", all=True)
    assert sent == [("pcb.delete_xsignals", {"all": "true"})]
    for kwargs in ({}, {"names": ["A"], "all": True}, {"names": [" "]}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_delete_xsignals", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    # Deleting while walking forward would skip the one after each delete.
    body = _body("PCB.pas", "PCB_DeleteXSignals")
    assert "For I := Before - 1 DownTo 0 Do" in body


def test_drill_pair_add_refuses_a_copy_and_a_non_copper_layer(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_add_drill_pair",
                    low_layer="Top Layer", high_layer="Mid-Layer 1")
    assert sent == [("pcb.add_drill_pair",
                     {"low_layer": "Top Layer", "high_layer": "Mid-Layer 1"})]
    for kwargs in ({"low_layer": "Top Layer", "high_layer": " "},
                   {"low_layer": "Top Layer", "high_layer": "top layer"}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_add_drill_pair", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_AddDrillPair")
    add = body.index("Board.AddLayerPair(LowLayer, HighLayer);")
    assert body.index("LayerUtils.IsElectricalLayer(LowLayer)") < add
    assert body.index("'ALREADY_EXISTS'") < add


def test_net_display_sends_and_reads_back(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_net_display",
                    nets=["GND", "VBUS"], show_connections=False)
    assert sent == [("pcb.net_display",
                     {"nets": "GND\tVBUS", "show_connections": "false"})]
    result, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_net_display", nets=[])
    assert sent == [] and result["ok"] is False
    body = _body("PCB.pas", "PCB_NetDisplay")
    assert "Net.HideNetConnects;" in body and "Net.ShowNetConnects;" in body
    assert body.index("Net.LoopRemoval := (LoopStr = 'true');") < body.index("NetDisplayJson(Net)")


def test_footprint_library_save_sends_and_refuses(monkeypatch, tmp_path):
    lib = tmp_path / "Src.PcbLib"
    lib.write_bytes(b"")
    out = tmp_path / "out" / "One.PcbLib"
    _, sent = _sent(library, "register_library_tools", monkeypatch,
                    "lib_save_footprint_library", footprint_name="SOT23",
                    out_path=str(out), library_path=str(lib))
    assert sent == [("library.save_footprint_library", {
        "footprint_name": "SOT23", "out_path": str(out), "library_path": str(lib)})]
    for kwargs in ({"footprint_name": "SOT23", "out_path": "rel.PcbLib"},
                   {"footprint_name": " ", "out_path": str(out)},
                   {"footprint_name": "SOT23", "out_path": str(tmp_path / "x.step")},
                   {"footprint_name": "SOT23", "out_path": str(lib), "library_path": str(lib)}):
        result, sent = _sent(library, "register_library_tools", monkeypatch,
                             "lib_save_footprint_library", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs


def test_library_splitting_stays_out():
    # Measured on AD 26.11: ISch_Lib and IPCB_Library SplitIntoComponents
    # are "Undeclared identifier" to a script, a modal that halts the loop.
    for unit in ALTIUM.glob("*.pas"):
        if unit.name == "Altium_MCP.pas":
            continue
        assert "SplitIntoComponents" not in unit.read_text(encoding="utf-8-sig"), unit.name


def test_schematic_library_is_made_then_saved(monkeypatch, tmp_path):
    out = tmp_path / "Made.SchLib"
    tools, bridge = _tools(project, "register_project_tools", monkeypatch)

    async def send(command, params, **kwargs):
        bridge.sent.append((command, params))
        if command == "application.save_document":
            out.write_bytes(b"x")
            return {"saved": True}
        return {"sheet": "S.SchDoc"}

    bridge.send_command_async = send
    result = asyncio.run(tools["proj_make_schematic_library"](out_path=str(out)))
    assert [c for c, _ in bridge.sent] == ["project.make_schematic_library",
                                          "application.save_document"]
    assert result["saved"] is True and result["written"] is True
    for kwargs in ({"out_path": "rel.SchLib"}, {"out_path": str(tmp_path / "x.PcbLib")}):
        result, sent = _sent(project, "register_project_tools", monkeypatch,
                             "proj_make_schematic_library", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("Project.pas", "Proj_MakeSchematicLibrary")
    assert "SchDoc.CreateLibraryFromProject(AddIt, OutPath, True);" in body
    assert "'NOT_LOADED'" in body


@pytest.mark.parametrize("given, sent_as", [
    ("automatic", "0"), ("Flat", "1"), ("hierarchical", "2"), ("global", "3"),
    ("strict hierarchical", "4"), ("2", "2")])
def test_hierarchy_mode_takes_names(monkeypatch, given, sent_as):
    _, sent = _sent(project, "register_project_tools", monkeypatch,
                    "proj_project_setup", hierarchy_mode=given)
    assert sent == [("project.project_setup", {"hierarchy_mode": sent_as})]


def test_hierarchy_mode_refuses_and_is_read_back(monkeypatch):
    for value in ("sideways", "5", "-1"):
        result, sent = _sent(project, "register_project_tools", monkeypatch,
                             "proj_project_setup", hierarchy_mode=value)
        assert sent == [] and result["ok"] is False, value
    body = _body("Project.pas", "Proj_ProjectSetup")
    assert body.index("Project.DM_SetHierarchyMode(StrToInt(ModeStr));") < body.index(
        "If Project.DM_HierarchyMode <> StrToInt(ModeStr) Then")
    assert body.index("Project.DM_SetOutputPath(OutStr);") < body.index("NoTrailingSlash(OutStr)")


def test_project_options_name_the_scope_in_altiums_order():
    body = _body("Project.pas", "Proj_GetProjectOptions")
    order = ["'Automatic'", "'Flat'", "'Hierarchical'", "'Global'", "'StrictHierarchical'"]
    for n, name in enumerate(order):
        assert f"If Project.DM_HierarchyMode = {n} Then HierMode := {name};" in body
    assert project._HIERARCHY_MODES == ("automatic", "flat", "hierarchical",
                                        "global", "strict_hierarchical")


def test_pin_functions_count_from_one():
    body = _body("Generic.pas", "PinFunctionsText")
    assert "For I := 1 To Count Do" in body
    put = _body("Generic.pas", "SchSetExtra")
    assert put.index("Pin.ClearDefinedFunctions;") < put.index("Pin.AddDefinedFunction(Trim(Item));")
    assert "Result := SchGetExtra(Obj, PropName) = Wanted" in put


def test_schematic_extras_go_to_their_own_type():
    text = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    assert "Or ((SchExtraOwner(PropName) > 0) And (Not SchExtraFits(Obj, PropName))) Then" in text
    get = _body("Generic.pas", "GetSchProperty")
    assert "If SchExtraOwner(PropName) > 0 Then Result := SchGetExtra(Obj, PropName)" in get
    put = _body("Generic.pas", "SetSchProperty")
    assert "WroteOK := SchSetExtra(Obj, PropName, Value);" in put
    fits = _body("Generic.pas", "SchExtraFits")
    assert "((Owner = 1) And (Obj.ObjectId = ePin))" in fits
    assert "((Owner = 2) And (Obj.ObjectId = eSchComponent))" in fits


def test_pad_jumpers_and_swap_ids_are_pad_only():
    fits = _body("PCBGeneric.pas", "ExtraPCBPropertyFits")
    assert "((Owner = 7) And (Oid = ePadObject))" in fits
    put = _body("PCBGeneric.pas", "PCBSetExtra")
    assert "If PropName = 'JumperID' Then Pad.JumperID := N" in put
    # JumperID is a whole number of 0 or more; the shared check refuses
    # anything else before the write.
    ints = put[put.index("IsInt :="):]
    assert "(PropName = 'JumperID')" in ints[:ints.index(";")]


def test_board_grids_send_mils_and_refuse_zero(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_grids",
                    snap_x=5, track=25.4, dot_grid=True)
    assert sent == [("pcb.set_grids", {"snap_x": "5.0", "track": "25.4",
                                       "dot_grid": "true"})]
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_grids")
    assert sent == [("pcb.set_grids", {})]
    for kwargs in ({"track": 0}, {"via": -5}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_set_grids", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_SetGrids")
    # The component grid follows the snap grid; it is read, never written.
    assert "ComponentGridSizeX :=" not in body
    # Every value is read back against what was asked.
    assert "If Abs(Got - V) > 0.001 Then" in body
    assert body.index("Board.DrawDotGrid := (DotStr = 'true');") < body.index(
        "If Board.DrawDotGrid <> (DotStr = 'true') Then")


def test_component_model_export_sends_and_refuses(monkeypatch, tmp_path):
    out = tmp_path / "U1.step"
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)

    async def send(command, params, **kwargs):
        bridge.sent.append((command, params))
        out.write_bytes(b"ISO-10303-21;")
        return {"saved": True}

    bridge.send_command_async = send
    result = asyncio.run(tools["pcb_export_component_model"](
        designator=" U1 ", out_path=str(out)))
    assert bridge.sent == [("pcb.export_component_model",
                            {"designator": "U1", "out_path": str(out)})]
    assert result["written"] is True
    for kwargs in ({"designator": "U1", "out_path": "rel.step"},
                   {"designator": " ", "out_path": str(out)},
                   {"designator": "U1", "out_path": str(tmp_path / "U1.stl")}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_export_component_model", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_ExportComponentModel")
    assert "Saved := Comp.SaveModelToFileAsPart(OutPath)" in body
    assert "'NOT_FOUND'" in body


def test_autosave_sends_minutes_and_converts(monkeypatch):
    _, sent = _sent(application, "register_application_tools", monkeypatch,
                    "app_autosave", enabled=True, interval_minutes=10)
    assert sent == [("application.autosave", {"enabled": "true", "interval": "10"})]
    result, sent = _sent(application, "register_application_tools", monkeypatch,
                         "app_autosave", interval_minutes=0)
    assert sent == [] and result["ok"] is False
    # Altium keeps the interval in milliseconds.
    body = _body("Application.pas", "App_Autosave")
    assert "Interval := StrToInt(IntStr) * 60000;" in body
    assert "Workspace.DM_GetRecoveryInterval Div 60000" in body


def test_rule_list_stays_off_names_a_script_cannot_reach():
    # Measured on AD 26.11: IPCB_Rule DataSummaryString is "Undeclared
    # identifier" to a script, a modal that halts the loop.
    body = _body("PCB.pas", "PCB_GetDesignRules")
    assert "DataSummaryString" not in body
    assert "ScopeDescriptorString" not in body


@pytest.mark.parametrize("key", ["append_sheet_number_to_local_nets",
                                 "pin_swap_by_net_label", "pin_swap_by_pin"])
def test_project_setup_sends_the_swap_and_local_net_options(monkeypatch, key):
    _, sent = _sent(project, "register_project_tools", monkeypatch,
                    "proj_project_setup", **{key: False})
    assert sent == [("project.project_setup", {key: "false"})]
    body = _body("Project.pas", "Proj_ProjectSetup")
    assert f"'\"{key}\",'" in body


def test_net_items_say_what_kind_of_net():
    kind = _body("Project.pas", "NetKindJson")
    assert "JsonBool('local_scope', IsLocal)" in kind
    assert "JsonStr('electrical', Electric)" in kind
    assert "JsonBool('erc_suppressed', NoErc)" in kind
    # The kind is defined before the routine that calls it: a call to a
    # function defined later resolves to nothing at runtime.
    text = (ALTIUM / "Project.pas").read_text(encoding="utf-8-sig")
    assert text.index("Function NetKindJson(") < text.index("Function NetContentsJson(")
    assert "+ NetKindJson(Net) + ','" in _body("Project.pas", "NetContentsJson")


def test_parameters_ports_and_pin_label_positions_go_to_their_own_type():
    fits = _body("Generic.pas", "SchExtraFits")
    assert "((Owner = 3) And (Obj.ObjectId = eParameter))" in fits
    assert "((Owner = 4) And (Obj.ObjectId = ePort))" in fits
    owner = _body("Generic.pas", "SchExtraOwner")
    for name in ("Name_PositionMode", "Designator_CustomPosition_Margin"):
        assert owner.index(f"'{name}'") < owner.index("Then Result := 1")
    put = _body("Generic.pas", "SchSetExtra")
    assert "If N < 0 Then Exit;" in put
    assert "If PropName = 'ReadOnlyState' Then Par.ReadOnlyState := N" in put
    assert "If PropName = 'AutoSize' Then Port.AutoSize := B" in put


def test_pour_and_text_extras_are_typed_and_converted_string_reads_only():
    fits = _body("PCBGeneric.pas", "ExtraPCBPropertyOwner")
    pour = fits[fits.index("'PolygonType'"):fits.index("Then Result := 5")]
    for name in ("DrawRemovedNecks", "DrawDeadCopper", "AutoGenerateName"):
        assert f"'{name}'" in pour
    put = _body("PCBGeneric.pas", "PCBSetExtra")
    refused = put[put.index("Result := -1;"):put.index("Owner := ExtraPCBPropertyOwner")]
    assert "'ConvertedString'" in refused
    assert "Txt.InvertedTTTextBorder := MilsToCoordF(F)" in put


def test_board_view_and_edit_options_map_names_both_ways(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)

    async def send(command, params, **kwargs):
        bridge.sent.append((command, params))
        return {"options": {"ShowPadNets": True, "SingleLayerMode": False},
                "not_applied": ["SingleLayerMode"], "refused": []}

    monkeypatch.setattr("eda_agent.tools.editor_options.get_bridge", lambda: bridge)
    bridge.send_command_async = send
    result = asyncio.run(tools["pcb_view_options"](show_pad_nets=True,
                                                   single_layer_mode=False))
    command, params = bridge.sent[0]
    assert command == "pcb.editor_options"
    assert params["set"] == "ShowPadNets=true|SingleLayerMode=false"
    assert params["names"].split(",") == list(pcb._PCB_VIEW_OPTIONS.values())
    assert result["options"] == {"show_pad_nets": True, "single_layer_mode": False}
    assert result["not_applied"] == ["single_layer_mode"]
    bridge.sent.clear()
    asyncio.run(tools["pcb_edit_options"](rotation_step=45, auto_pan=False))
    assert bridge.sent[0][1]["set"] == "RotationStep=45.0|AutoPan=false"
    for kwargs in ({"rotation_step": -1},):
        bridge.sent.clear()
        result = asyncio.run(tools["pcb_edit_options"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


def test_board_options_reach_only_names_the_script_knows():
    kind = _body("PCB.pas", "PcbOptionKind")
    for altium in list(pcb._PCB_VIEW_OPTIONS.values()) + list(pcb._PCB_EDIT_OPTIONS.values()):
        assert f"'{altium}'" in kind, altium
        if altium == "UndoRedoStackSize":
            # Read only: a write did not take on AD 26.11.
            assert "Opts.UndoRedoStackSize :=" not in _body("PCB.pas", "PcbOptionSet")
            continue
        assert f"Opts.{altium} := " in _body("PCB.pas", "PcbOptionSet"), altium
    handler = _body("PCB.pas", "PCB_EditorOptions")
    # The PCB server holds these and loads with the first board.
    assert handler.index("GetPCBBoardAnywhere(0) = Nil") < handler.index(
        "Opts := PCBServer.SystemOptions")


def test_schematic_editor_options_map_names_and_refuse(monkeypatch):
    tools, bridge = _tools(generic, "register_generic_tools", monkeypatch)
    monkeypatch.setattr("eda_agent.tools.editor_options.get_bridge", lambda: bridge)
    asyncio.run(tools["sch_editor_options"](power_ground_name="GND",
                                            components_cut_wires=False,
                                            pin_name_margin=5))
    command, params = bridge.sent[0]
    assert command == "generic.sch_editor_options"
    assert params["set"] == ("ComponentsCutWires=false|PinNameMargin=50000|"
                             "DefaultPowerGndName=GND")
    for kwargs in ({"undo_levels": -1}, {"earth_name": "A|B"}):
        bridge.sent.clear()
        result = asyncio.run(tools["sch_editor_options"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs
    kind = _body("Generic.pas", "SchOptionKind")
    for altium in generic._SCH_EDITOR_OPTIONS.values():
        assert f"'{altium}'" in kind, altium
        assert f"Prefs.{altium} := " in _body("Generic.pas", "SchOptionSet"), altium


def test_sheet_grid_takes_the_electrical_grid(monkeypatch):
    _, sent = _sent(generic, "register_generic_tools", monkeypatch, "obj_set_grid",
                    hotspot_grid=40, hotspot_grid_on=False)
    assert sent == [("generic.set_grid", {"hotspot_grid": "40",
                                          "hotspot_grid_on": "false"})]
    body = _body("Generic.pas", "Gen_SetGrid")
    assert "SchDoc.HotspotGridSize := MilsToCoordF(HotGrid);" in body
    assert "BoolToJsonStr(SchDoc.HotspotGridOn)" in body


def test_net_display_sends_colour_and_jumpers(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_net_display",
                    nets=["GND"], use_net_color=True, show_jumpers=False)
    assert sent == [("pcb.net_display", {"nets": "GND", "use_net_color": "true",
                                         "show_jumpers": "false"})]
    body = _body("PCB.pas", "PCB_NetDisplay")
    assert "Net.OverrideColorForDraw := (ColourStr = 'true');" in body
    assert "Net.JumpersVisible := (JumperStr = 'true');" in body
    assert "JsonBool('in_differential_pair', InPair)" in _body("PCB.pas", "NetDisplayJson")


def test_compile_freshness_carries_altiums_own_answer():
    body = _body("Project.pas", "Proj_GetCompileFreshness")
    assert "Try NeedsCompile := Project.DM_NeedsCompile; Except End;" in body
    assert "Try Violations := Project.DM_ExistingViolationCount; Except End;" in body


def test_part_pad_and_pin_extras_are_typed():
    owner = _body("PCBGeneric.pas", "ExtraPCBPropertyOwner")
    part = owner[owner.index("'EnablePinSwapping'"):owner.index("Then Result := 6")]
    for name in ("SourceDescription", "JumpersVisible", "ChannelOffset", "GroupNum"):
        assert f"'{name}'" in part, name
    pad = owner[owner.index("'JumperID'"):owner.index("Then Result := 7")]
    assert "'SwappedPadName'" in pad and "'OwnerPart_ID'" in pad
    sch = _body("Generic.pas", "SchExtraOwner")
    assert sch.index("'DefaultValue'") < sch.index("Then Result := 1")
    assert sch.index("'LibraryPath'") < sch.index("Then Result := 2")
    # A part's library path and aliases are text, not switches.
    is_bool = _body("Generic.pas", "SchExtraIsBool")
    assert "(PropName <> 'LibraryPath') And (PropName <> 'AliasAsText')" in is_bool


def test_route_options_and_board_options_send_and_refuse(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)
    monkeypatch.setattr("eda_agent.tools.editor_options.get_bridge", lambda: bridge)
    asyncio.run(tools["pcb_route_options"](auto_via=False, shove_depth=2))
    command, params = bridge.sent[0]
    assert command == "pcb.editor_options"
    assert params["set"] == "AutoVia=false|PlaceShoveDepth=2"
    assert params["names"].split(",") == list(pcb._PCB_ROUTE_OPTIONS.values())
    bridge.sent.clear()
    result = asyncio.run(tools["pcb_route_options"](route_mode=-1))
    assert bridge.sent == [] and result["ok"] is False
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_board_options",
                    polygon_name_template=2)
    assert sent == [("pcb.board_options", {"polygon_name_template": "2"})]
    # Automatic split planes is reported only: a write did not take.
    body = _body("PCB.pas", "PCB_BoardOptions")
    assert "Board.AutomaticSplitPlanes :=" not in body
    assert "Board.PolygonNameTemplate := StrToInt(TemplStr);" in body
    for altium in pcb._PCB_ROUTE_OPTIONS.values():
        assert f"Opts.{altium} := " in _body("PCB.pas", "PcbOptionSet"), altium


def test_new_view_and_edit_options_are_wired():
    for altium in ("PlaneDrawMode", "DraftTrackThreshold", "DragMode", "ComponentMoveKind"):
        assert f"'{altium}'" in _body("PCB.pas", "PcbOptionKind"), altium
    put = _body("PCB.pas", "PcbOptionSet")
    assert "Opts.DraftTrackThreshold := MilsToCoordF(F)" in put


def test_pours_at_sends_mils_and_reads_the_outline():
    body = _body("PCB.pas", "PCB_PoursAt")
    assert "If Poly.PointInPolygon(X, Y) Then" in body
    assert "MilsToCoordF(StrToFloatDef(XStr, 0)) + Board.XOrigin" in body


def test_pours_at_tool_sends(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_pours_at", x=100, y=50.5)
    assert sent == [("pcb.pours_at", {"x": "100.0", "y": "50.5"})]


def test_diff_pair_impedance_is_sent_read_back_and_refused(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_rule_properties",
                    name="DP", dp_impedance_driven=True, dp_preferred_impedance=90)
    assert sent == [("pcb.set_rule_properties", {
        "name": "DP", "dp_preferred_impedance": "90", "dp_impedance_driven": "true"})]
    result, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_rule_properties",
                         name="DP", dp_min_impedance=0)
    assert sent == [] and result["ok"] is False
    body = _body("PCB.pas", "PCB_SetRuleProperties")
    assert body.index("RuleDiff.FavoredImpedance := StrToFloatDef(ImpFav, 0);") < body.index(
        "'\"dp_preferred_impedance\",'")


def test_rules_say_when_the_schematic_defines_them():
    body = _body("PCB.pas", "PCB_GetDesignRules")
    assert "Try FromSch := Rule.DefinedByLogicalDocument; Except" in body


def test_body_extras_and_parameter_flags_are_typed():
    owner = _body("PCBGeneric.pas", "ExtraPCBPropertyOwner")
    assert owner.index("'OverrideColor'") < owner.index("Then Result := 8")
    assert "((Owner = 8) And (Oid = eComponentBodyObject))" in _body(
        "PCBGeneric.pas", "ExtraPCBPropertyFits")
    put = _body("Generic.pas", "SchSetExtra")
    # The parameter flags are Altium's answers, never written.
    flags = put[put.index("If (PropName = 'NameIsReadOnly')"):put.index("IsInt := ")]
    assert "Then Exit;" in flags and "'IsConfigurable'" in flags


def test_schematic_display_options_are_wired():
    for altium in ("ExpandComponentDesignators", "WatermarkReadOnlySheet",
                   "DefaultTemplateFileName", "AlwaysDrag"):
        assert altium in generic._SCH_EDITOR_OPTIONS.values()
        assert f"Prefs.{altium} := " in _body("Generic.pas", "SchOptionSet"), altium


def test_undo_and_violation_clearing_stay_out():
    # Measured on AD 26.11: the board's undo did not step back a scripted
    # change, and clearing violations left the count where it was.
    pcb_text = (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    assert "Board.DoUndo" not in pcb_text
    assert "DM_ClearViolations" not in (ALTIUM / "Project.pas").read_text(encoding="utf-8-sig")


def test_grow_pour_sends_and_refuses(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_grow_pour",
                    name=" Top Layer-GND ", distance_mils=-20)
    assert sent == [("pcb.grow_pour", {"name": "Top Layer-GND", "distance_mils": "-20.0"})]
    for kwargs in ({"name": " ", "distance_mils": 5}, {"name": "P", "distance_mils": 0}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_grow_pour", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_GrowPour")
    assert "Found.GrowPolyshape(MilsToCoordF(Dist));" in body
    assert body.index("Before := PourBoundsJson(Found);") < body.index("GrowPolyshape")


def test_plane_connections_name_the_rule_style(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)

    async def send(command, params, **kwargs):
        bridge.sent.append((command, params))
        return {"pads": [{"pad": "1", "rule_style": 0}, {"pad": "2", "rule_style": 1},
                         {"pad": "3", "rule_style": 2}]}

    bridge.send_command_async = send
    result = asyncio.run(tools["pcb_plane_connections"](designator="U1", pad="2"))
    assert bridge.sent == [("pcb.plane_connections", {"designator": "U1", "pad": "2"})]
    assert [p["connection"] for p in result["pads"]] == ["relief", "direct", "none"]
    # The per-layer style read 0 whatever the rule said; it is not reported.
    assert "PlaneConnectionStyleForLayer" not in _body("PCB.pas", "PCB_PlaneConnections")


def test_more_rule_settings_go_to_their_own_kind(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_rule_properties",
                    name="Height", height_max_mils=900, show_distances=True,
                    width_impedance_driven=False)
    assert sent == [("pcb.set_rule_properties", {
        "name": "Height", "height_max_mils": "900", "width_impedance_driven": "false",
        "show_distances": "true"})]
    for kwargs in ({"corner_min_setback_mils": -1}, {"width_min_impedance": 0}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch,
                             "pcb_set_rule_properties", name="R", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_SetRuleProperties")
    assert "'WRONG_RULE_KIND'" in body
    for helper, kind in (("RuleExtrasWidth", 2), ("RuleExtrasHeight", 50),
                         ("RuleExtrasCompClear", 24), ("RuleExtrasCorners", 10),
                         ("RuleExtrasAntennae", 56), ("RuleExtrasFanout", 49)):
        assert f"(R.RuleKind = {kind}) And (R.Name = RuleName)" in _body("PCB.pas", helper)
        assert f"XReport := {helper}(Board, RuleName" in body


def test_optimize_wires_counts_before_and_after(monkeypatch):
    _, sent = _sent(generic, "register_generic_tools", monkeypatch, "sch_optimize_wires")
    assert sent == [("generic.optimize_wires", {})]
    body = _body("Generic.pas", "Gen_OptimizeWires")
    assert body.index("CountSheetWires(SchDoc, WiresBefore, VertsBefore);") < body.index(
        "Sheet.OptimizeUseOfPolylines;") < body.index("CountSheetWires(SchDoc, WiresAfter, VertsAfter);")


def test_pre_route_and_paste_settings_are_typed():
    owner = _body("PCBGeneric.pas", "ExtraPCBPropertyOwner")
    assert owner.index("'IsPreRoute'") < owner.index("Then Result := 9")
    assert owner.index("'PasteMaskPercent'") < owner.index("Then Result := 1")
    put = _body("PCBGeneric.pas", "PCBSetExtra")
    assert "If (PropName = 'PasteMaskPercent') And (F > 100) Then" in put
    # Only the hole tolerances take 'unset'.
    assert ("If ((PropName = 'HolePositiveTolerance') Or (PropName = 'HoleNegativeTolerance'))"
            in put)


def test_compile_mask_stays_out():
    # Measured on AD 26.11: a part's compile mask read back unset after it
    # was written.
    text = (ALTIUM / "Generic.pas").read_text(encoding="utf-8-sig")
    assert "CompilationMasked" not in text


def test_rigid_flex_tools_send_and_refuse(monkeypatch):
    def sent(tool, **kwargs):
        return _sent(pcb, "register_pcb_tools", monkeypatch, tool, **kwargs)

    _, s = sent("pcb_get_board_regions")
    assert s == [("pcb.board_regions", {"action": "read"})]
    _, s = sent("pcb_split_board", x1=1, y1=2, x2=3, y2=4)
    assert s == [("pcb.board_regions", {"action": "split", "x1": "1.0", "y1": "2.0",
                                        "x2": "3.0", "y2": "4.0"})]
    _, s = sent("pcb_add_bend_line", region=" Flex ", x1=0, y1=0, x2=10, y2=0,
                radius_mils=40, angle=-90, fold_index=1)
    assert s[0][1]["region"] == "Flex" and s[0][1]["angle"] == "-90.0"
    _, s = sent("pcb_modify_bend_line", region="#1", index=0, fold_index=2)
    assert s == [("pcb.board_regions", {"action": "modify_bend", "region": "#1",
                                        "index": "0", "fold_index": "2"})]
    for tool, kwargs in (("pcb_remove_split_line", {"index": -1}),
                         ("pcb_add_bend_line", {"region": "", "x1": 0, "y1": 0, "x2": 1,
                                                "y2": 1, "radius_mils": 1, "angle": 9}),
                         ("pcb_add_bend_line", {"region": "R", "x1": 0, "y1": 0, "x2": 1,
                                                "y2": 1, "radius_mils": -1, "angle": 9}),
                         ("pcb_modify_bend_line", {"region": "R", "index": 0}),
                         ("pcb_delete_bend_line", {"region": " ", "index": 0}),
                         ("pcb_set_board_region", {"region": "R"})):
        result, s = sent(tool, **kwargs)
        assert s == [] and result["ok"] is False, (tool, kwargs)


def test_set_board_region_renames_then_locks_by_the_new_name(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)

    async def send(command, params, **kwargs):
        bridge.sent.append((command, params))
        return {"regions": []}

    bridge.send_command_async = send
    asyncio.run(tools["pcb_set_board_region"](region="#1", name="Flex Tail", locked_3d=True))
    assert bridge.sent == [
        ("pcb.board_regions", {"action": "rename", "region": "#1", "name": "Flex Tail"}),
        ("pcb.board_regions", {"action": "lock_3d", "region": "Flex Tail", "locked": "true"})]


def test_layer_stacks_need_their_arguments(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_layer_stacks",
                    action="enable_layer", stack="Flex", layer="Top Layer")
    assert sent == [("pcb.layer_stacks", {"action": "enable_layer", "stack": "Flex",
                                          "layer": "Top Layer"})]
    for kwargs in ({"action": "explode"}, {"action": "assign", "stack": "Flex"},
                   {"action": "set_flex", "stack": "Flex"}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_layer_stacks", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs


def test_rigid_flex_pascal_keeps_its_measured_guards():
    body = _body("PCB.pas", "PCB_BoardRegions")
    # A full rebuild threw a new split line away and merged regions.
    assert "RebuildSplitBoardRegions(True)" not in body
    assert "'REGIONS_NOT_FROM_SPLIT_LINES'" in body
    assert body.index("'REGIONS_NOT_FROM_SPLIT_LINES'") < body.index("Outline.CreateSplitLine(P, Q)")
    assert "'SPLIT_DID_NOT_DIVIDE'" in body and "'BEND_OUTSIDE_REGION'" in body
    # Points are TPoint records made with Point(); TCoordPoint fields are
    # undeclared to the script engine.
    assert "P := Point(" in body
    assert "TCoordPoint" not in body.replace("TCoordPoint is not", "")
    # Bend lines belong to regions, not to the outline.
    assert "Reg.CreateBendingLine(P, Q," in body
    regions = _body("PCB.pas", "FlexRegionsJson")
    assert "Prim.ViewableObjectID = eViewableObject_BoardRegion" in regions
    assert "BL := Reg.GetBendingLines(I);" in regions


def test_gloss_sends_and_selects_through_the_editor(monkeypatch):
    _, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_gloss",
                    nets=["GND", " VBUS "], mode="Retrace")
    assert sent == [("pcb.gloss", {"nets": "GND\tVBUS", "mode": "retrace"})]
    for kwargs in ({"nets": []}, {"nets": ["A"], "mode": "smooth"}):
        result, sent = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_gloss", **kwargs)
        assert sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_Gloss")
    assert body.index("Board.SelectedObjects_BeginUpdate") < body.index("Prim.Selected := True;")
    assert "If Mode = 'gloss' Then Process := 'PCB:Gloss' Else Process := 'PCB:Retrace';" in body
    assert body.index("RunProcess(Process);") < body.index("Board.SelectedObjects_Clear; Except End;\n    Try Board.ViewManager_FullUpdate")


def test_stack_manager_tools_send_and_refuse(monkeypatch):
    def sent(tool, **kwargs):
        return _sent(pcb, "register_pcb_tools", monkeypatch, tool, **kwargs)

    _, s = sent("pcb_physical_stack", style=1)
    assert s == [("pcb.physical_stack", {"style": "1"})]
    _, s = sent("pcb_set_dielectric", layer="Dielectric 1", loss_tangent=0.02, stiffener=True)
    assert s == [("pcb.set_dielectric", {"layer": "Dielectric 1", "loss_tangent": "0.02",
                                         "stiffener": "true"})]
    _, s = sent("pcb_impedance_profile", action="Create", profile="Z50", target_ohms=50,
                differential=False)
    assert s == [("pcb.impedance_profile", {"action": "create", "profile": "Z50",
                                            "target_ohms": "50.0", "differential": "false"})]
    _, s = sent("pcb_add_layer", layer="MidLayer1", below="Top Layer")
    assert s == [("pcb.add_layer", {"layer": "MidLayer1", "below": "Top Layer"})]
    for tool, kwargs in (("pcb_physical_stack", {"style": -1}),
                         ("pcb_set_dielectric", {"layer": "D"}),
                         ("pcb_set_dielectric", {"layer": "D", "constant": 0.5}),
                         ("pcb_impedance_profile", {"action": "explode", "profile": "Z"}),
                         ("pcb_impedance_profile", {"action": "create", "profile": "Z",
                                                    "target_ohms": 0}),
                         ("pcb_impedance_profile_layer", {"profile": "Z", "layer": " "}),
                         ("pcb_impedance_profile_layer", {"profile": "Z", "layer": "L",
                                                          "min_width_mils": -1}),
                         ("pcb_add_layer", {"layer": "MidLayer1", "above": "A", "below": "B"}),
                         ("pcb_via_structures", {"limit": 0})):
        result, s = sent(tool, **kwargs)
        assert s == [] and result["ok"] is False, (tool, kwargs)


def test_profile_layer_reports_no_limit_as_null(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)

    async def send(command, params, **kwargs):
        return {"max_width_mils": 214748.3647, "max_gap_mils": 8, "min_width_mils": 4}

    bridge.send_command_async = send
    result = asyncio.run(tools["pcb_impedance_profile_layer"](profile="Z", layer="Top Layer"))
    assert result["max_width_mils"] is None and result["max_gap_mils"] == 8


def test_stack_pascal_keeps_its_measured_shapes():
    stack = _body("PCB.pas", "PCB_PhysicalStack")
    # Altium's Z runs down from the top copper.
    assert "CoordToMilsF(ZBot - ZTop)" in stack
    layer = _body("PCB.pas", "PCB_ImpedanceProfileLayer")
    # A record's own layer is a handle a script cannot name; layers go by ID.
    assert "P.GetTraceImpedanceForLayer(Sub, WantId)" in layer
    assert "P.CreateTraceImpedance(Sub, WantId)" in layer
    assert "T.Layer" not in layer
    reader = _body("PCB.pas", "PCB_GetImpedanceProfiles")
    assert "Profile.GetTraceImpedanceForLayer(Sub, Layer.LayerID)" in reader
    assert "Trace.Layer" not in reader and "RefTopLayer" not in reader
    assert "ImpLayerName" not in (ALTIUM / "PCB.pas").read_text(encoding="utf-8-sig")
    add = _body("PCB.pas", "PCB_AddLayer")
    assert "LayerStack.InsertInStackBelow(RefObj, NewObj)" in add
    # ResolveStackLayerObject is defined after PCB_AddLayer: not called here.
    assert "ResolveStackLayerObject" not in add


def test_board_output_options_send_their_group_and_map_names(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)
    monkeypatch.setattr("eda_agent.tools.editor_options.get_bridge", lambda: bridge)
    asyncio.run(tools["pcb_gerber_options"](embed_apertures=True, film_x_mils=20000))
    command, params = bridge.sent[0]
    assert command == "pcb.output_options" and params["group"] == "gerber"
    assert params["set"] == "EmbedApertures=true|FilmSizeX=20000.0"
    assert params["names"].split(",") == list(pcb._GERBER_OPTIONS.values())
    bridge.sent.clear()
    asyncio.run(tools["pcb_print_options"](scale=2, pad_numbers=False))
    command, params = bridge.sent[0]
    assert params["group"] == "print"
    assert params["set"] == "Scale=2.0|PlotPadNumbers=false"
    for tool, kwargs in (("pcb_gerber_options", {"border_mils": -1}),
                         ("pcb_print_options", {"scale": 0}),
                         ("pcb_print_options", {"batch_type": -1})):
        bridge.sent.clear()
        result = asyncio.run(tools[tool](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, (tool, kwargs)
    kind = _body("PCB.pas", "OutputOptionKind")
    setter = _body("PCB.pas", "OutputOptionSet")
    for altium in pcb._GERBER_OPTIONS.values():
        assert f"'{altium}'" in kind and f"G.{altium} := " in setter, altium
    for altium in pcb._PRINT_OPTIONS.values():
        assert f"'{altium}'" in kind and f"P.{altium} := " in setter, altium


def test_grid_factors_and_units_send_and_refuse(monkeypatch):
    def sent(module, register, tool, **kwargs):
        return _sent(module, register, monkeypatch, tool, **kwargs)

    _, s = sent(pcb, "register_pcb_tools", "pcb_set_grids", visible_big_factor=5,
                snap_unit="MM")
    assert s == [("pcb.set_grids", {"visible_big_factor": "5.0", "snap_unit": "mm"})]
    for tool, kwargs in (("pcb_set_grids", {"visible_big_factor": 0}),
                         ("pcb_set_grids", {"visible_unit": "inch"})):
        result, s = sent(pcb, "register_pcb_tools", tool, **kwargs)
        assert s == [] and result["ok"] is False, (tool, kwargs)
    grids = _body("PCB.pas", "PCB_SetGrids")
    assert "Board.BigVisibleGridMultFactor := V" in grids
    # The fine factor stayed 1 whatever was written: it is only reported.
    assert "Board.VisibleGridMultFactor :=" not in grids
    assert "If UnitGot <> UnitWanted Then" in grids


def test_testpoint_rule_settings_go_to_their_own_kind(monkeypatch):
    _, s = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_set_rule_properties",
                 name="TP", testpoint={"min_size_mils": 30, "use_grid": False})
    assert s == [("pcb.set_rule_properties", {"name": "TP",
                                              "testpoint_min_size_mils": "30.0",
                                              "testpoint_use_grid": "false"})]
    for bad in ({"size": 3}, {"min_size_mils": -1}, {"use_grid": 1},
                {"grid_mils": True}):
        result, s = _sent(pcb, "register_pcb_tools", monkeypatch,
                          "pcb_set_rule_properties", name="TP", testpoint=bad)
        assert s == [] and result["ok"] is False, bad
    keys = _body("PCB.pas", "TestpointKeys")
    for key, switch in pcb._TESTPOINT_SETTINGS.items():
        if not switch:
            assert "testpoint_" + key in keys, key
    handler = _body("PCB.pas", "PCB_SetRuleProperties")
    assert "TestpointAsked(Params) And (Not IsTestpointStyleKind(Kind))" in handler
    assert "(Kind = 43) Or (Kind = 57)" in _body("PCB.pas", "IsTestpointStyleKind")


def test_reference_zones_send_and_refuse(monkeypatch):
    _, s = _sent(generic, "register_generic_tools", monkeypatch, "sch_reference_zones",
                 kinds=["Port", "net_label"])
    assert s == [("generic.reference_zones", {"kinds": "port,net_label"})]
    result, s = _sent(generic, "register_generic_tools", monkeypatch,
                      "sch_reference_zones", kinds=["wire"])
    assert s == [] and result["ok"] is False
    zones = _body("Generic.pas", "Gen_ReferenceZones")
    assert "SchDoc.ObjectReferenceZone(Obj)" in zones


def test_pad_drill_type_and_read_only_extras_are_typed():
    owner = _body("PCBGeneric.pas", "ExtraPCBPropertyOwner")
    for name in ("DrillType", "IsVirtualPin", "FlippedOnLayer", "BorderSpaceType"):
        assert f"'{name}'" in owner, name
    setter = _body("PCBGeneric.pas", "PCBSetExtra")
    read_only = setter[:setter.index("Owner := ExtraPCBPropertyOwner")]
    for name in ("IsVirtualPin", "FlippedOnLayer"):
        assert f"'{name}'" in read_only, name
    assert "If N > 3 Then Exit;" in setter


def test_paste_and_more_editor_options_send_and_refuse(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)
    monkeypatch.setattr("eda_agent.tools.editor_options.get_bridge", lambda: bridge)
    asyncio.run(tools["pcb_paste_options"](repeat_count=4, repeat_x_mils=-100,
                                           repeat_increment="2"))
    command, params = bridge.sent[0]
    assert command == "pcb.editor_options"
    assert params["set"] == "RepeatCountDefault=4|RepeatInc=2|RepeatX=-1000000.0"
    bridge.sent.clear()
    asyncio.run(tools["pcb_view_options"](board_area_color=0x203040, pad_net_font="Arial"))
    assert bridge.sent[0][1]["set"] == "PadViaFontName=Arial|BoardAreaColor=2109504"
    bridge.sent.clear()
    asyncio.run(tools["pcb_edit_options"](confirm_delete=False, auto_pan_speed=3))
    assert bridge.sent[0][1]["set"] == "QuestionDelete=false|AutoPanSpeed=3"
    for tool, kwargs in (("pcb_paste_options", {"repeat_count": -1}),
                         ("pcb_paste_options", {"repeat_angle": -5}),
                         ("pcb_view_options", {"sheet_line_color": 0x1000000}),
                         ("pcb_view_options", {"text_as_box_below": -1}),
                         ("pcb_edit_options", {"auto_pan_step": -2})):
        bridge.sent.clear()
        result = asyncio.run(tools[tool](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, (tool, kwargs)
    kind = _body("PCB.pas", "PcbOptionKind")
    setter = _body("PCB.pas", "PcbOptionSet")
    for altium in pcb._PCB_PASTE_OPTIONS.values():
        assert f"'{altium}'" in kind and f"Opts.{altium} := " in setter, altium
    # Only the repeat steps may be negative.
    assert "(F < 0) And (Name <> 'RepeatX') And (Name <> 'RepeatY')" in setter
    handler = _body("PCB.pas", "PCB_EditorOptions")
    assert "Took := Got = '\"' + EscapeJsonString(Value) + '\"'" in handler


def test_net_colour_highlighting_sends_bgr_and_reads_hex(monkeypatch):
    tools, bridge = _tools(pcb, "register_pcb_tools", monkeypatch)

    async def send(command, params, **kwargs):
        bridge.sent.append((command, params))
        return {"nets": [{"net": "GND", "color": 0x0080FF}]}

    bridge.send_command_async = send
    result = asyncio.run(tools["pcb_net_display"](nets=["GND"], color="#FF8000",
                                                  use_net_color=True))
    assert bridge.sent == [("pcb.net_display", {"nets": "GND", "use_net_color": "true",
                                                "color": str(0x0080FF)})]
    assert result["nets"][0]["color"] == "#FF8000"
    bridge.sent.clear()
    asyncio.run(tools["pcb_net_display"](from_project=True))
    assert bridge.sent == [("pcb.net_display", {"nets": "", "from_project": "true"})]
    for kwargs in ({"nets": ["GND"], "color": "orange"},
                   {"nets": ["GND"], "color": "#FF8000", "from_project": True},
                   {"nets": []}):
        bridge.sent.clear()
        result = asyncio.run(tools["pcb_net_display"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs
    body = _body("PCB.pas", "PCB_NetDisplay")
    assert "Given := Project.DM_GetNetColorByName(Net.Name);" in body
    # A net the project gives no colour is left as it is, and named.
    assert body.index("NoColour := NoColour +") < body.index("Net.Color := Given;")


def test_delete_object_searches_every_layer_unless_one_is_named(monkeypatch):
    _, s = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_delete_object",
                 x=-2000.25, y=-2000.75, object_type="via")
    assert s == [("pcb.delete_object", {"x": "-2000.25", "y": "-2000.75", "layer": "",
                                        "object_type": "via"})]
    body = _body("PCB.pas", "PCB_DeleteObject")
    # A through via sits on the multi-layer: a top-layer walk never found it.
    assert "If LayerStr = '' Then\n        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers)" in body
    assert "'\"layer\":\"' + EscapeJsonString(FoundLayer)" in body


def test_polygon_placers_can_leave_the_pour_for_later(monkeypatch):
    _, s = _sent(pcb, "register_pcb_tools", monkeypatch, "pcb_place_polygon_rect",
                 x1=0, y1=0, x2=100, y2=100, net="GND", pour=False)
    assert s[0][1]["pour"] == "false"
    for name in ("PCB_PlacePolygonRect", "PCB_PlacePolygonBoard"):
        body = _body("PCB.pas", name)
        assert "If Not Skipped Then Polygon.Rebuild;" in body, name
        # Named once it is on the board (before, the name stayed empty); the
        # reply says what copper is there.
        assert body.index("Polygon.Name := Polygon.GetDefaultName") > body.index(
            "Board.AddPCBObject(Polygon)"), name
        assert "PolygonCopper(Polygon, CopperSqMils, CopperExact) > 0" in body, name
        assert "RepourModeName(0)" in body, name
        assert "Polygon.Name := Polygon.GetDefaultName" in body, name


def test_footprint_bodies_go_by_identifier_prefix(monkeypatch):
    _, s = _sent(library, "register_library_tools", monkeypatch,
                 "lib_select_footprint_primitives", footprint_name="SOT23",
                 objects=[{"object_type": "body", "identifier": "Envelope_"}])
    assert s[0][1]["objects"] == "object_type=body;identifier=Envelope_"
    _, s = _sent(library, "register_library_tools", monkeypatch,
                 "lib_delete_footprint_primitives", footprint_name="SOT23",
                 identifier="Envelope_", confirm=True)
    assert s[0][1]["identifier"] == "Envelope_" and "object_type" not in s[0][1]
    result, s = _sent(library, "register_library_tools", monkeypatch,
                      "lib_delete_footprint_primitives", footprint_name="SOT23",
                      identifier="Envelope_", object_type="track", confirm=True)
    assert s == [] and result["ok"] is False
    match = _body("Library.pas", "BodyIdentifierMatches")
    assert "Copy(Ident, 1, Length(Prefix)) = Prefix" in match
    assert "Prim.ObjectId <> eComponentBodyObject" in match
    delete = _body("Library.pas", "Lib_DeleteFootprintPrimitives")
    assert delete.count("BodyIdentifierMatches(Prim, IdentStr)") == 1
    assert "CountFootprintMatches(Target, ObjFilter, WantLayer, IncludePads, IdentStr)" in delete
    # The footprint is fetched by name once the walk is over, and footprints
    # are compared and messaged without reading their I_ObjectAddress: both
    # faulted on AD 26.11.
    assert "Target := PcbLib.GetComponentByName(FoundName)" in delete
    assert "Target.I_ObjectAddress" not in delete
    assert "Result := Cur.Name = Target.Name;" in _body("Library.pas", "IsCurrentFootprint")
    assert "I_ObjectAddress" not in _body("Library.pas", "FootprintToHoldFocus")


def test_an_unreadable_via_middle_is_null_not_zero():
    body = _body("Library.pas", "LibViaDetailJson")
    assert "If (MidSize = 0) And (ModeName = 'top-middle-bottom') Then" in body
    assert "MidJson := 'null,\"mid_unreadable\":true'" in body
