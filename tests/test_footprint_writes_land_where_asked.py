# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Footprint authoring lands in the footprint asked for, at its own origin.

Three PcbLib defects from one library session:

- lib_add_footprint_pads, _tracks and the singular pad, track and arc
  wrote into the editor's CURRENT footprint, and nothing moved the
  editor, so silkscreen meant for one footprint ended up in another.
- lib_link_3d_model put the body at (-50000, -50000) mil from its
  footprint: the body arrives at the board origin, and a footprint open
  in the editor sits at Altium's library origin.
- lib_copy_footprint within one library kept the source's absolute
  coordinates, so every pad of the copy sat 50000 mil from its origin.

The handlers need Altium to run, so the Pascal is checked for the shape
of the fix with comments stripped; release verification runs it live.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import library as library_module

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
WRITERS = ["Lib_AddFootprintPad", "Lib_AddFootprintPads", "Lib_AddFootprintTrack",
           "Lib_AddFootprintTracks", "Lib_AddFootprintArc"]


def _routine(name: str) -> str:
    code = (SCRIPTS / "Library.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\(", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body)


@pytest.mark.parametrize("name", WRITERS)
def test_every_writer_resolves_the_named_footprint(name):
    body = _routine(name)
    assert ("PcbLibTarget(PcbLib, ExtractJsonValue(Params, 'footprint_name'), "
            "TargetProblem)") in body
    assert "Footprint := PcbLib.CurrentComponent" not in body


def test_the_target_is_made_current_and_checked_by_name():
    body = _routine("PcbLibTarget")
    assert "PcbLib.GetComponentByName(Name)" in body
    assert "PcbLib.CurrentComponent := Result" in body
    check = body.index("If CurName <> Name Then")
    assert body.index("Result := Nil", check) > check, "a switch that did not take writes nothing"


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"success": True}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(library_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(DummyMcp())
    return captured, bridge


@pytest.mark.parametrize("tool,kwargs", [
    ("lib_add_footprint_pad", {"designator": "1", "x": 0, "y": 0}),
    ("lib_add_footprint_pads", {"pads": [{"designator": "1", "x": 0, "y": 0}]}),
    ("lib_add_footprint_track", {"x1": 0, "y1": 0, "x2": 10, "y2": 0}),
    ("lib_add_footprint_tracks", {"tracks": [{"x1": 0, "y1": 0, "x2": 10, "y2": 0}]}),
    ("lib_add_footprint_arc", {"x_center": 0, "y_center": 0, "radius": 10}),
])
def test_the_tools_send_the_footprint_only_when_named(monkeypatch, tool, kwargs):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools[tool](**kwargs))
    asyncio.run(tools[tool](footprint_name="SOT23", **kwargs))
    (_c1, first), (_c2, second) = bridge.calls
    assert "footprint_name" not in first, "unnamed calls keep their old wire shape"
    assert second["footprint_name"] == "SOT23"


def test_a_linked_body_is_moved_onto_its_footprint_origin():
    body = _routine("Lib_Link3DModel")
    assert "OrgX := FootprintOriginX(Footprint)" in body
    assert "Body.MoveByXY(OrgX + MilsToCoordF(OffX), OrgY + MilsToCoordF(OffY))" in body
    # The move is not skipped just because the caller gave no offset.
    assert "If (OrgX <> 0) Or (OrgY <> 0) Or (OffX <> 0) Or (OffY <> 0) Then" in body


@pytest.mark.parametrize("name", ["Lib_CopyFootprint", "Lib_MoveFootprints"])
def test_a_copy_is_realigned_right_after_copy_to(name):
    body = _routine(name)
    copy = body.index("Footprint.CopyTo(NewFP, eFullCopy)")
    align = body.index("AlignCopiedFootprint(Footprint, NewFP)")
    assert copy < align < body.index("RegisterComponent(NewFP)")


def test_the_realignment_moves_by_the_origin_difference_once_each():
    body = _routine("AlignCopiedFootprint")
    assert "DX := FootprintOriginX(Dest) - FootprintOriginX(Src)" in body
    assert "DY := FootprintOriginY(Dest) - FootprintOriginY(Src)" in body
    assert "Seen.IndexOf(Addr) < 0" in body, "a primitive the iterator yields twice moves once"
    assert "Prims.Free" not in body
    assert body.index("Dest.GroupIterator_Destroy(Iter)") < body.index("Prim.MoveByXY(DX, DY)")
