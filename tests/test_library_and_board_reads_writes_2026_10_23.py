# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Board and library reads and writes reported on 2026-10-22 (#100-#118).

Each guard names the defect it exists to catch. The Pascal ones read the
handler's code with comments stripped, so a mention in prose cannot pass
for the code; the Python ones run the tool against a fake bridge.
"""

from __future__ import annotations

import asyncio
import re

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src


def _fn(unit: str, func: str) -> str:
    return _decommented(_body(_src(unit), func))


def _tools(module_name: str):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params=None, **kw):
            sent.append((command, dict(params or {}), kw))
            return {"ok": True}

    import importlib
    module = importlib.import_module(f"eda_agent.tools.{module_name}")
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(module, "get_bridge", lambda: Bridge())
    getattr(module, f"register_{module_name}_tools")(Mcp())
    return tools, sent, mp


# -- #100: a board read with a library in front --------------------------------

def test_a_library_in_front_with_several_boards_gives_no_board():
    body = _fn("Main.pas", "GetPCBBoardAnywhere")
    lib = body.index("If FocusKind = 'PCBLIB' Then")
    assert "If Found > 1 Then" in body[lib:]
    assert "BoardMissWhy :=" in body[lib:]
    # decided before the walk that opens the first board it meets
    assert lib < body.index("Client.OpenDocument('PCB', Path)")


def test_a_board_found_by_walking_is_named_on_the_reply():
    body = _fn("Main.pas", "GetPCBBoardAnywhere")
    walk = body.index("Client.OpenDocument('PCB', Path)")
    assert "BoardFallbackPath := Path;" in body[walk:]
    # ProcessOne: each item of a bulk call is a request of its own.
    process = _fn("StatusForm.pas", "ProcessOne")
    assert "BoardFallbackPath := '';" in process
    assert process.index("BoardFallbackPath := '';") < process.index("Case Category Of")
    assert "Result := BoardFallbackNote(Result);" in process
    note = _fn("StatusForm.pas", "BoardFallbackNote")
    assert '"board_used":"' in note and "EscapeJsonString(BoardFallbackPath)" in note


def test_the_no_board_refusal_says_why():
    body = _fn("Main.pas", "BuildErrorResponseDetailed")
    assert "(ErrorCode = 'NO_PCB') And (BoardMissWhy <> '')" in body


# -- #118: custom pads ---------------------------------------------------------

def test_every_pad_shape_reader_knows_a_custom_pad():
    readers = 0
    for unit in ("Generic.pas", "Library.pas", "PCB.pas", "PCBGeneric.pas"):
        text = _decommented(_src(unit))
        for m in re.finditer(r"= eRoundedRectangular Then (?:ShapeStr|Result|Str|Name) :=", text):
            window = text[m.start():m.start() + 260]
            assert "eCustomShape" in window, f"{unit}: a shape mapper falls through for a custom pad"
            readers += 1
    assert readers >= 8


def test_a_custom_pad_exported_to_kicad_is_named(monkeypatch):
    from eda_agent.export.kicad_footprint import _pad_shape
    assert _pad_shape("custom") == "rect"


# -- #105: the paste flag on Altium 25 -----------------------------------------

def test_the_paste_flag_refusal_names_25():
    body = _src("Library.pas")
    assert "Altium Designer 26 or later" not in body
    assert "'Altium Designer 25 or later; it was not changed;'" in body


# -- #106: footprint delete ----------------------------------------------------

def test_every_footprint_delete_walk_is_filtered():
    body = _fn("Library.pas", "Lib_DeleteFootprintPrimitives")
    creates = body.count("GrpIter := Target.GroupIterator_Create;")
    assert creates == 2
    assert body.count("FilterFootprintWalk(GrpIter, ") == creates


def test_the_delete_counts_again_and_says_when_something_stayed():
    body = _fn("Library.pas", "Lib_DeleteFootprintPrimitives")
    assert body.index("Leftover := CountFootprintMatches(") > body.index("PcbEndEdit(0);")
    assert '"leftover":' in body and '"hint":"' in body and '"matched":' in body
    count = _fn("Library.pas", "CountFootprintMatches")
    assert "FilterFootprintWalk(GrpIter, ObjFilter);" in count


def test_the_delete_does_not_select_and_clear():
    body = _fn("Library.pas", "Lib_DeleteFootprintPrimitives")
    assert "PCB:Clear" not in body and ".Selected := True" not in body


# -- #107: SchLib designator and the symbol on screen ---------------------------

def test_a_schlib_query_walks_the_symbol_on_screen_first():
    body = _fn("Generic.pas", "ProcessSchDocObjects")
    pick = body.index("ParamsOfSymbol := False;")
    current = body.index("LibComp := SchDoc.CurrentSchComponent", pick)
    fallback = body.index("LibComp := LastCreatedLibComponent", pick)
    assert current < fallback
    assert "(ObjTypeInt = eParameter) Or (ObjTypeInt = eDesignator)" in body
    assert '"_symbol":"' in body


def test_a_schlib_parameter_delete_prefers_the_symbol_on_screen():
    body = _decommented(_src("Generic.pas"))
    start = body.index("Procedure DeleteParametersAnyOwner(")
    part = body[start:body.index("RemoveMatchingParamsFromComponent(Comp, ReducedFilter", start)]
    assert part.index("SchDoc.CurrentSchComponent") < part.index("LastCreatedLibComponent")


# -- #108: vias in one call -----------------------------------------------------

def test_footprint_vias_go_in_one_call_and_bad_ones_are_named():
    tools, sent, mp = _tools("library")
    try:
        got = asyncio.run(tools["lib_add_footprint_vias"](
            [{"x": 0, "y": 0}, {"x": 1, "y": 0, "size": 10, "hole_size": 12},
             {"x": "a", "y": 0}], units="mm", size=0.6, hole_size=0.3))
    finally:
        mp.undo()
    command, params, _ = sent[-1]
    assert command == "library.add_footprint_vias"
    assert params["vias"].count("~~") == 0 and "x=0;y=0;size=0.6;hole_size=0.3" in params["vias"]
    assert len(got["refused"]) == 2


def test_the_via_batch_settles_the_footprint():
    body = _fn("Library.pas", "LibActionAddsToFootprint")
    assert "'add_footprint_vias'" in body and "'add_footprint_bodies'" in body


# -- #109: the footprint read ---------------------------------------------------

def test_the_footprint_read_reports_via_mask_tenting_testpoints_and_stack():
    detail = _fn("Library.pas", "LibViaDetailJson")
    for key in ('"mask_expansion_source":"', '"mask_from_hole_edge":',
                '"tenting_top":', '"testpoint_top":', '"assy_testpoint_bottom":',
                '"stack_mode":"', '"diameter_mid":'):
        assert key in detail, key
    body = _fn("Library.pas", "Lib_GetFootprintPrimitives")
    assert "LibViaDetailJson(Via)" in body
    assert body.count('"selected":') >= 5
    assert '"editor_origin_x_mm":' in body


def test_each_kind_of_the_footprint_read_can_be_left_out():
    body = _fn("Library.pas", "Lib_GetFootprintPrimitives")
    for kind in ("track", "arc", "region", "fill", "text", "via", "body"):
        assert f"If KindWanted(Kinds, '{kind}') Then" in body, kind


# -- #110 / #111: pad and via writers -------------------------------------------

def test_handing_a_mask_back_to_the_rule_uses_eCacheInvalid():
    for unit, func in (("Library.pas", "Lib_SetPadProperties"),
                       ("Library.pas", "Lib_SetViaProperties"),
                       ("PCBGeneric.pas", "PCBSetMask")):
        body = _fn(unit, func)
        assert "eCacheInvalid" in body, func
        assert "SolderMaskExpansionValid := eCacheValid" not in body, func


def test_the_pad_writer_sets_the_stack_before_the_sizes():
    body = _fn("Library.pas", "Lib_SetPadProperties")
    assert body.index("Pad.Mode := ePadMode_Simple") < body.index("Pad.TopXSize :=")
    assert "SetPadRoundRect(Pad, CornerPct)" in body


def test_pad_writer_refusals_happen_before_sending():
    tools, sent, mp = _tools("library")
    try:
        for kw in ({"stack_mode": "full-stack"}, {"corner_radius_pct": 120},
                   {"mask_manual": False, "mask_expansion": 2},
                   {"paste_manual": False, "paste_expansion": 1}):
            got = asyncio.run(tools["lib_set_pad_properties"]("FP", "1", **kw))
            assert got["success"] is False, kw
        assert not sent
        asyncio.run(tools["lib_set_pad_properties"](
            "FP", "1", shape="roundrect", corner_radius_pct=25,
            mask_from_hole_edge=True, paste_manual=False))
    finally:
        mp.undo()
    params = sent[-1][1]
    assert params["corner_radius_pct"] == 25.0
    assert params["mask_from_hole_edge"] == "true"
    assert params["paste_manual"] == "false"


def test_a_middle_diameter_that_reads_zero_is_unverified_not_failed():
    body = _fn("Library.pas", "Lib_SetViaProperties")
    assert "If Found.SizeOnLayer(eMidLayers) = 0 Then MidUnverified := True" in body


# -- #112: layer counts ---------------------------------------------------------

def test_layer_counts_name_footprints_and_every_mechanical_layer():
    body = _fn("Library.pas", "Lib_CountLayerObjects")
    assert "LayerDisplayName(Prim.Layer)" in body and "GetLayerString(" not in body
    assert "eCoordinateObject" in body
    assert '"footprints":[' in body
    assert "'library_path is required'" not in body


# -- a footprint name the library already has -----------------------------------

def test_creating_a_footprint_refuses_a_name_already_taken():
    body = _fn("Library.pas", "Lib_CreateFootprint")
    check = body.index("UpperCase(CurName) = UpperCase(Name)")
    assert "'NAME_EXISTS'" in body
    assert check < body.index("PCBServer.CreatePCBLibComp")


def test_an_empty_footprint_name_is_refused_before_sending():
    tools, sent, mp = _tools("library")
    try:
        got = asyncio.run(tools["lib_create_footprint"]("  "))
    finally:
        mp.undo()
    assert got["success"] is False and not sent


# -- #113: extruded bodies ------------------------------------------------------

def test_an_extruded_body_joins_the_board_before_any_property():
    body = _fn("Library.pas", "AddExtrudedBody")
    added = body.index("Board.AddPCBObject(Body);")
    for write in ("Body.Layer :=", "Body.StandoffHeight :=", "Body.SetState_Identifier("):
        assert added < body.index(write), write
    assert "Footprint.AddPCBObject" not in body


def test_the_board_body_takes_an_outline_or_a_rectangle():
    tools, sent, mp = _tools("pcb")
    try:
        asyncio.run(tools["pcb_place_extruded_body"](
            100, points=[[0, 0], [10.5, 0], [5, 8]], identifier="env"))
        asyncio.run(tools["pcb_place_extruded_body"](50, x1=0, y1=0, x2=19.685, y2=10))
        bad = asyncio.run(tools["pcb_place_extruded_body"](50, standoff_height=60))
    finally:
        mp.undo()
    assert sent[0][1]["points"] == "0.0,0.0/10.5,0.0/5.0,8.0"
    assert sent[1][1]["x2"] == "19.685"
    assert bad["ok"] is False and len(sent) == 2


# -- #114: symbol shape style ---------------------------------------------------

def test_symbol_shapes_take_and_read_back_their_style():
    rect = _fn("Library.pas", "Lib_AddSymbolRectangle")
    assert "Rect.LineWidth := LineW" in rect and "SchRectStyleJson(Rect)" in rect
    poly = _fn("Library.pas", "Lib_AddSymbolPolygon")
    assert "Polygon.Color :=" in poly and "SchPolygonStyleJson(Polygon)" in poly
    for func in ("Lib_AddSymbolArc", "Lib_AddSymbolLine"):
        assert '"color":' in _fn("Library.pas", func), func


def test_bezier_is_a_schematic_type_name_again():
    assert "N = 'bezier'" in _src("Generic.pas")
