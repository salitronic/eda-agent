# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A PcbLib footprint's primitives can be selected, and the selection read.

Altium's editor commands that act on the selection (PCB:CustomPadShape to
merge a pad and a region into a custom pad, PCB:Clear) had no way to be
pointed at a footprint's primitives: obj_select resolves a board. The new
library.select_footprint_primitives selects by type, layer, name or exact
box, several sets in one call, inside the SelectedObjects bracket the
editor's selection list needs; get_footprint_primitives reports what is
then selected.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import library as library_tools

from tests.pascal_source import load

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _fns():
    return load(_LIB)


def test_the_selection_is_made_inside_the_editor_bracket():
    body = _fns()["Lib_SelectFootprintPrimitives"]
    begin = body.index("PcbLib.Board.SelectedObjects_BeginUpdate;")
    flag = body.index("Prim.Selected := True;")
    end = body.index("PcbLib.Board.SelectedObjects_EndUpdate;")
    assert begin < flag < end
    assert "BIter := PcbLib.Board.BoardIterator_Create;" in body
    assert "BIter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);" in body


def test_a_bad_or_empty_set_is_refused_before_anything_is_selected():
    fns = _fns()
    body = fns["Lib_SelectFootprintPrimitives"]
    check = body.index("Problem := SelectSpecProblem(PcbLib, Spec);")
    assert check < body.index("'BAD_FILTER'") < body.index("SelectedObjects_BeginUpdate")
    problem = fns["SelectSpecProblem"]
    assert "would select the whole footprint" in problem
    assert "ObjectTypeFromStringPCB(T) = -1" in problem
    assert "ResolveLayerId(PcbLib.Board, L)" in problem


def test_the_footprint_is_made_current_only_when_it_is_not():
    body = _fns()["Lib_SelectFootprintPrimitives"]
    same = body.index("If CurName = FpWanted Then Target := Cur")
    assert same < body.index("Target := PcbLibTarget(PcbLib, FpWanted, Problem);")


def test_clear_deselects_and_any_set_selects():
    body = _fns()["Lib_SelectFootprintPrimitives"]
    assert "If ClearFirst Then Try PcbLib.Board.SelectedObjects_Clear; Except End;" in body
    assert "Else If ClearFirst Then\n                    Prim.Selected := False;" in body.replace("\r\n", "\n")
    assert "Match := SelectSpecMatches(PcbLib, Spec, Prim);" in body
    assert "While Not Match Do" in body


def test_the_reply_counts_what_the_editor_now_holds_selected():
    body = _fns()["Lib_SelectFootprintPrimitives"]
    assert "If Prim.Selected Then Total := Total + 1;" in body
    assert "',\"selected_total\":' + IntToStr(Total)" in body


def test_a_box_picks_one_primitive_within_a_thousandth_of_a_millimetre():
    fns = _fns()
    box = fns["SelectBoxMatches"]
    assert "BR := Prim.BoundingRectangle;" in box
    for side, org in (("Left", "OX"), ("Bottom", "OY"), ("Right", "OX"), ("Top", "OY")):
        assert f"CoordToMM(BR.{side} - {org})" in box
    assert box.count("< 0.001)") == 4
    assert "(Box <> '') And (Not SelectBoxMatches(PcbLib, Box, Prim))" in fns["SelectSpecMatches"]


def test_the_select_is_dispatched_and_keeps_the_focus():
    src = _LIB.read_text(encoding="latin-1")
    assert "'select_footprint_primitives': Result := Lib_SelectFootprintPrimitives(" in src
    # A read-only action has its focus put back afterwards; a selection
    # made for the next command must stay where it is.
    assert "'select_footprint_primitives'" not in _fns()["LibActionIsReadOnly"]


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(library_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    library_tools.register_library_tools(mcp)
    out = asyncio.run(mcp.tools["lib_select_footprint_primitives"](**kw))
    return out, bridge.sent


def test_a_pad_and_a_region_go_out_as_two_sets_in_one_call(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="QFN", objects=[
        {"object_type": "pad", "layer": "MultiLayer", "name": "12"},
        {"object_type": "region", "layer": "TopLayer", "box": [6.2, -1.7, 12.3, 5.4]}])
    assert sent[0][0] == "library.select_footprint_primitives"
    assert sent[0][1]["objects"] == ("object_type=pad;layer=MultiLayer;name=12"
                                     "~~object_type=region;layer=TopLayer;box=6.2,-1.7,12.3,5.4")
    assert sent[0][1]["clear"] == "true"


def test_clear_false_reaches_the_script(monkeypatch):
    _, sent = _call(monkeypatch, footprint_name="QFN",
                    objects=[{"object_type": "pad"}], clear=False)
    assert sent[0][1]["clear"] == "false"


def test_an_empty_set_or_a_bad_box_is_refused_before_sending(monkeypatch):
    for objects in ([{}], [], [{"box": [1, 2, 3]}], [{"box": "a,b,c,d"}], ["pad"]):
        out, sent = _call(monkeypatch, footprint_name="QFN", objects=objects)
        assert out["ok"] is False and not sent, objects
