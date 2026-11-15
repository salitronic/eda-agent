# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Hundreds of copper objects go through one file.

The geometry map deletes vias and pours as it deletes tracks, arcs and
regions; pcb_place_tracks / pcb_place_vias take their list from a file;
and tool_guide sends a question about many objects to the map.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eda_agent.geometry_map import build_edits, parse_dump
from eda_agent.tools import pcb as pcb_tools
from eda_agent.tools.guidance import guidance_for
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
C = 10000  # internal units per mil

_DUMP = "\n".join([
    "L\t1\tTopLayer",
    f"V\t0\t{100*C}\t{200*C}",
    f"V\t1\t{300*C}\t{400*C}",
    f"V\t2\t{300*C}\t{400*C}",          # a second via on the same spot
    f"P\t0\t1\t4\t{10*C}\t{20*C}",
    f"P\t1\t1\t4\t{50*C}\t{60*C}",
])


def _edits(geometry_map):
    lines, summary = build_edits(geometry_map, parse_dump(_DUMP), 0.01, 1e-4)
    return lines, summary


# ------------------------------------------------------------- the map

def test_a_via_delete_is_a_del_line_and_counted():
    lines, summary = _edits({"delete": [{"kind": "via", "key": [100, 200]}]})
    assert lines == [f"V\t0\t{100*C}\t{200*C}\tDEL"]
    assert summary["delete"]["vias"] == 1
    assert summary["delete"]["matched"] == 1


def test_two_vias_on_one_spot_delete_neither_for_one_entry():
    lines, summary = _edits({"delete": [{"kind": "via", "key": [300, 400]}]})
    assert lines == []
    assert summary["vias"]["ambiguous"] == 1


def test_a_via_moved_and_deleted_is_ambiguous_and_left_alone():
    lines, summary = _edits({"vias": [{"key": [100, 200], "new": [110, 200]}],
                             "delete": [{"kind": "via", "key": [100, 200]}]})
    assert lines == []
    assert summary["vias"]["ambiguous"] == 2


def test_a_pour_delete_is_matched_by_layer_and_first_vertex():
    lines, summary = _edits({"delete": [
        {"kind": "polygon", "layer": "TopLayer", "key_first": [50, 60]}]})
    assert lines == [f"P\t1\t1\t4\t{50*C}\t{60*C}\tDEL"]
    assert summary["delete"]["polygons"] == 1


def test_a_pour_delete_on_another_layer_matches_nothing():
    lines, summary = _edits({"delete": [
        {"kind": "polygon", "layer": "BottomLayer", "key_first": [50, 60]}]})
    assert lines == []
    assert summary["delete"]["matched"] == 0


# ---------------------------------------------------------- the apply pass

def _apply_body():
    return load(_ALTIUM / "PCB.pas", minimum=20)["PCB_ApplyFreePrimitives"]


def test_the_apply_removes_a_via_marked_del():
    body = _apply_body()
    # The second "Via := Prim;" is the write pass; the first only matches.
    write = body.index("Via := Prim;", body.index("Via := Prim;") + 1)
    via = body[write:body.index("Else If KindNo = 4", write)]
    assert "If A1 = 'DEL' Then" in via
    assert "Board.RemovePCBObject(Prim);" in via
    assert via.index("'DEL'") < via.index("Via.BeginModify;")


def test_pours_are_removed_after_the_reshape_walk_with_their_union_cleared():
    body = _apply_body()
    walk_end = body.index("Board.BoardIterator_Destroy(Iter);",
                          body.index("Poly := Iter.FirstPCBObject;"))
    removal = body.index("If Pos(#9 + 'DEL', Lines[J]) > 0 Then", walk_end)
    tail = body[removal:removal + 600]
    assert tail.index("SetState_UnionIndex := 0") < tail.index("RemovePCBObject(Prim)")


# ------------------------------------------------------------- the tools

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


def _call(monkeypatch, tool, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools[tool](**kw))
    return out, bridge.sent


_TRACK = {"x1": 0, "y1": 0, "x2": 100, "y2": 0, "width": 10}
_VIA = {"x": 50, "y": 60}


@pytest.mark.parametrize("tool,key,item", [
    ("pcb_place_tracks", "tracks", _TRACK), ("pcb_place_vias", "vias", _VIA)])
def test_a_file_sends_what_the_list_sends(monkeypatch, tmp_path, tool, key, item):
    _, inline = _call(monkeypatch, tool, **{key: [item, item]})
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([item, item]), encoding="utf-8")
    _, from_bare = _call(monkeypatch, tool, file=str(bare))
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({key: [item, item]}), encoding="utf-8")
    _, from_wrapped = _call(monkeypatch, tool, file=str(wrapped))
    assert inline == from_bare == from_wrapped


@pytest.mark.parametrize("tool,key,item", [
    ("pcb_place_tracks", "tracks", _TRACK), ("pcb_place_vias", "vias", _VIA)])
def test_both_neither_or_an_unreadable_file_is_refused(monkeypatch, tmp_path,
                                                       tool, key, item):
    good = tmp_path / "good.json"
    good.write_text(json.dumps([item]), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"other": [item]}), encoding="utf-8")
    numbers = tmp_path / "numbers.json"
    numbers.write_text(json.dumps([1, 2]), encoding="utf-8")
    for kw in ({key: [item], "file": str(good)}, {}, {"file": str(bad)},
               {"file": str(tmp_path / "missing.json")}, {"file": str(wrong)},
               {"file": str(numbers)}):
        out, sent = _call(monkeypatch, tool, **kw)
        assert out["success"] is False and sent == [], kw


# ------------------------------------------------------------- the guide

@pytest.mark.parametrize("task", [
    "place many tracks", "delete many vias", "create thousands of regions",
    "replace hundreds of pours"])
def test_a_question_about_many_objects_names_the_map_first(task):
    first = guidance_for(task, "board", "altium")["recipes"][0]
    assert first["use"] == ["pcb_apply_geometry_map"]


@pytest.mark.parametrize("task", ["place a track", "delete a via"])
def test_a_question_about_one_object_does_not(task):
    recipes = guidance_for(task, "board", "altium")["recipes"]
    assert all(r["use"] != ["pcb_apply_geometry_map"] for r in recipes)
