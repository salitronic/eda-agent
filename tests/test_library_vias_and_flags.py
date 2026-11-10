# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Footprint vias, via tenting and the pad paste flag (issues #79-#84).

Measured on Altium 26.11: via tenting and the top paste flag are accepted and
silently dropped while the matching expansion follows the rules, and kept once
it is manual. Each handler makes the expansion manual first and says so; the
read-back after the write is what caught the drop, so it has to stay.
"""

from __future__ import annotations

import asyncio

from tests.test_handlers_verify_their_outcome import _body, _decommented, _src


def _lib(func: str) -> str:
    return _decommented(_body(_src("Library.pas"), func))


def _tools(monkeypatch):
    sent = []

    class Bridge:
        async def send_command_async(self, command, params, **kw):
            sent.append((command, dict(params), kw))
            return {"ok": True}

    import eda_agent.tools.library as library
    monkeypatch.setattr(library, "get_bridge", lambda: Bridge())
    tools = {}

    class Mcp:
        def tool(self):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

    library.register_library_tools(Mcp())
    return tools, sent


def test_tenting_makes_the_mask_manual_before_writing():
    body = _lib("Lib_SetViaProperties")
    manual = body.index("PadCache.SolderMaskExpansionValid := eCacheManual")
    assert manual < body.index("Found.SetState_IsTenting_Top(")
    assert manual < body.index("Found.SetState_IsTenting_Bottom(")
    assert '"mask_made_manual":' in body


def test_tenting_is_read_back_not_assumed():
    body = _lib("Lib_SetViaProperties")
    write = body.index("Found.SetState_IsTenting_Top(")
    assert body.index("Found.GetState_IsTenting_Top <>", write) > write


def test_the_via_is_found_within_one_mil_whatever_the_units():
    body = _lib("Lib_SetViaProperties")
    assert "Tol := MilsToCoord(1)" in body
    assert "Tol := CoordFromUnits(" not in body


def test_size_and_mask_writes_are_compared_with_what_was_asked():
    body = _lib("Lib_SetViaProperties")
    for read in ("Abs(Found.HoleSize - WantC) > Tol",
                 "Abs(Found.SizeOnLayer(eTopLayer) - WantC) > Tol",
                 "Abs(PadCache.SolderMaskExpansion"):
        assert read in body, read


def test_the_paste_flag_makes_the_paste_manual_before_writing():
    body = _lib("ApplyPadEdit")
    flag = body.index("Pad.SetState_IsTopPasteEnabled(")
    manual = body.rindex("Cache.PasteMaskExpansionValid := eCacheManual", 0, flag)
    assert body.index("paste_top_enabled") < manual < flag
    assert body.index("Pad.GetState_IsTopPasteEnabled <>", flag) > flag


def test_tools_that_take_a_library_path_refuse_another_focused_library():
    for func in ("Lib_SetViaProperties", "Lib_CountLayerObjects",
                 "Lib_GetLibraryOrigin", "Lib_AddFootprintVia"):
        body = _lib(func)
        assert "PcbLibPathProblem(PcbLib, LibPath)" in body, func
        assert "'WRONG_LIBRARY'" in body, func


def test_the_library_path_check_ignores_slash_direction():
    body = _lib("PcbLibPathProblem")
    assert "StringReplace(Focused, '/', '\\', -1)" in body
    assert "StringReplace(LibPath, '/', '\\', -1)" in body


def test_a_via_add_settles_the_footprint_like_every_other_add():
    body = _lib("LibActionAddsToFootprint")
    assert "'add_footprint_via'" in body


def test_set_via_properties_sends_only_what_was_given(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["lib_set_via_properties"](
        0, 0, library_path="C:/lib.PcbLib", units="mm", tenting_top=True,
        mask_expansion=0.1))
    command, params, _ = sent[-1]
    assert command == "library.set_via_properties"
    assert set(params) == {"library_path", "x", "y", "units", "tenting_top",
                           "mask_expansion"}
    assert params["tenting_top"] == "true"


def test_set_via_properties_defaults_to_the_focused_library(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["lib_set_via_properties"](
        0, 0, stack_mode="simple", mask_manual=False, mask_from_hole_edge=True))
    params = sent[-1][1]
    assert "library_path" not in params
    assert params["stack_mode"] == "simple"
    assert params["mask_manual"] == "false"
    assert params["mask_from_hole_edge"] == "true"


def test_set_via_properties_refuses_contradictions_before_sending(monkeypatch):
    tools, sent = _tools(monkeypatch)
    before = len(sent)
    for kwargs in ({"stack_mode": "full-stack"},
                   {"stack_mode": "simple", "dia_mid": 20},
                   {"mask_manual": False, "mask_expansion": 2}):
        got = asyncio.run(tools["lib_set_via_properties"](0, 0, **kwargs))
        assert got["success"] is False, kwargs
    assert len(sent) == before


def test_add_footprint_via_passes_the_library_path_only_when_given(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["lib_add_footprint_via"](1, 2))
    assert "library_path" not in sent[-1][1]
    asyncio.run(tools["lib_add_footprint_via"](1, 2, library_path="C:/x.PcbLib"))
    assert sent[-1][1]["library_path"] == "C:/x.PcbLib"


def test_count_layer_objects_allows_a_large_library_time(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["lib_count_layer_objects"]("C:/x.PcbLib"))
    assert sent[-1][2].get("timeout", 0) >= 60


def test_paste_flag_and_keep_layers_reach_the_bridge(monkeypatch):
    tools, sent = _tools(monkeypatch)
    asyncio.run(tools["lib_set_pad_properties"]("FP", "1", paste_top_enabled=False))
    assert sent[-1][1]["paste_top_enabled"] == "false"
    asyncio.run(tools["lib_move_bodies_to_layer"]("Mechanical13",
                                                  keep_layers=["Mechanical15", " "]))
    assert sent[-1][1]["keep_layers"] == "Mechanical15"
