# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_pad_relief / pcb_pad_relief_spokes: one pad's own thermal relief.

A pad's own relief (Properties > Thermal Relief: style, spokes, width, gap,
rotation) and its custom spokes (the relief "Edit" points) could not be
read or written through the bridge, so dense boards had to be done by hand
one pad at a time. The record is ThermalReliefData keyed by an empty
TPadViaThermalReliefItem; spokes are points on the relief outline. The
outlines below follow the edge orders measured on AD 25.8.1: a rounded
rectangle runs clockwise from its west side, a plain rectangle
counter-clockwise from west going down, round pads are four quarter arcs,
an octagon has eight straight sides, an oblong two sides and four arcs.
"""
from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

from eda_agent.layout import pad_relief
from eda_agent.tools import pcb

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _line(x, y):
    return {"kind": "line", "x": x, "y": y}


def _arc(x, y, cx, cy, r, a1, a2):
    return {"kind": "arc", "x": x, "y": y, "cx": cx, "cy": cy, "r": r, "a1": a1, "a2": a2}


def _rounded_square():
    # Read back from a 110.24 mil rounded square, corner radius 27.559.
    c, h, r = (1929.1339, 6456.6929), 55.1181, 27.559
    x0, x1, y0, y1 = c[0] - h, c[0] + h, c[1] - h, c[1] + h
    return {"x": c[0], "y": c[1], "pad_rotation": 0, "width": 6, "outline": [
        _line(x0, y0 + r),
        _arc(x0, y1 - r, x0 + r, y1 - r, r, 90, 180),
        _line(x0 + r, y1),
        _arc(x1 - r, y1, x1 - r, y1 - r, r, 0, 90),
        _line(x1, y1 - r),
        _arc(x1, y0 + r, x1 - r, y0 + r, r, 270, 360),
        _line(x1 - r, y0),
        _arc(x0 + r, y0, x0 + r, y0 + r, r, 180, 270),
    ]}


def _rect():
    return {"x": 0, "y": 0, "pad_rotation": 0, "width": 4, "outline": [
        _line(-10, 15), _line(-10, -15), _line(10, -15), _line(10, 15)]}


def _round():
    return {"x": 0, "y": 0, "pad_rotation": 0, "width": 6, "outline": [
        _arc(15, 0, 0, 0, 15, 0, 90), _arc(0, 15, 0, 0, 15, 90, 180),
        _arc(-15, 0, 0, 0, 15, 180, 270), _arc(0, -15, 0, 0, 15, 270, 360)]}


def _octagon():
    v = [(-15, -7.5), (-15, 7.5), (-7.5, 15), (7.5, 15), (15, 7.5), (15, -7.5),
         (7.5, -15), (-7.5, -15)]
    return {"x": 0, "y": 0, "pad_rotation": 0, "width": 6,
            "outline": [_line(x, y) for x, y in v]}


def _oblong():
    return {"x": 0, "y": 0, "pad_rotation": 0, "width": 6, "outline": [
        _line(-10, -10), _line(-10, 10),
        _arc(-10, 10, 0, 10, 10, 90, 180), _arc(0, 20, 0, 10, 10, 0, 90),
        _line(10, 10), _line(10, -10),
        _arc(10, -10, 0, -10, 10, 270, 360), _arc(0, -20, 0, -10, 10, 180, 270)]}


def _turned(deg):
    pad = _rect()
    a = math.radians(deg)
    for seg in pad["outline"]:
        x, y = seg["x"], seg["y"]
        seg["x"], seg["y"] = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
    pad["pad_rotation"] = deg
    return pad


def _at(pad, spokes, frame="pad", width=None):
    return [(r["segment"], r["fraction"], r["x"], r["y"])
            for r in pad_relief.resolve(pad, spokes, frame, width)]


# --- names resolved from the outline ---------------------------------------

def test_sides_take_the_middle_of_the_straight_side_facing_them():
    pad = _rounded_square()
    got = _at(pad, ["W", "N", "E", "S"])
    assert [g[0] for g in got] == [0, 2, 4, 6]
    assert all(g[1] == 0.5 for g in got)
    assert got[1][2:] == (1929.1339, 6511.811)


def test_a_rounded_corner_is_the_middle_of_its_arc():
    seg, frac, x, y = _at(_rounded_square(), ["NE"])[0]
    assert (seg, frac) == (3, 0.5)
    assert x == pytest.approx(1956.693 + 27.559 * math.cos(math.radians(45)), abs=1e-3)


def test_a_square_corner_is_where_two_sides_meet():
    # Live: the S side at fraction 0 put the spoke exactly on the corner.
    assert _at(_rect(), ["SW"]) == [(1, 0.0, -10.0, -15.0)]
    assert _at(_rect(), ["NE"]) == [(3, 0.0, 10.0, 15.0)]


def test_an_octagon_corner_is_its_cut_side():
    got = _at(_octagon(), ["NE", "SW"])
    assert [(g[2], g[3]) for g in got] == [(11.25, 11.25), (-11.25, -11.25)]


def test_degrees_land_on_arcs_from_angle1_counter_clockwise():
    got = _at(_round(), ["45", "225", "200"])
    assert got[0][:2] == (0, 0.5)
    assert got[1][:2] == (2, 0.5)
    assert got[2][:2] == (2, pytest.approx(20 / 90, abs=1e-6))
    assert (got[0][2], got[0][3]) == (10.6066, 10.6066)


def test_degrees_land_on_lines_too():
    seg, frac, x, y = _at(_rounded_square(), ["0"])[0]
    assert (seg, frac, y) == (4, 0.5, 6456.6929)


def test_a_round_end_has_no_side_and_says_so():
    with pytest.raises(pad_relief.SpokeError, match="degrees"):
        pad_relief.resolve(_oblong(), ["N"])
    assert _at(_oblong(), ["90"])[0][2:] == (0.0, 20.0)
    assert _at(_oblong(), ["W"])[0][:2] == (0, 0.5)


def test_a_round_pad_has_no_corner_beyond_its_quarter_arcs():
    assert _at(_round(), ["NE"])[0][:2] == (0, 0.5)


def test_an_offset_moves_along_the_side_up_to_the_straight_part():
    pad = _rounded_square()
    # N side 55.12 long, spoke 6 wide: 24.56 each way from the middle.
    got = _at(pad, ["N:+1", "N:-1", "N:0.5"])
    assert got[0][2] == pytest.approx(1929.1339 + 24.5591, abs=1e-3)
    assert got[1][2] == pytest.approx(1929.1339 - 24.5591, abs=1e-3)
    # + on W / E is north, whichever way the side runs.
    assert _at(pad, ["W:+1"])[0][3] > 6456.6929
    assert _at(pad, ["E:+1"])[0][3] > 6456.6929


def test_an_offset_beyond_the_side_is_refused_not_clamped():
    with pytest.raises(pad_relief.SpokeError, match="outside"):
        pad_relief.resolve(_rounded_square(), ["N:1.2"])
    with pytest.raises(pad_relief.SpokeError, match="no room"):
        pad_relief.resolve(_rect(), ["N:0.5"], width=30)
    with pytest.raises(pad_relief.SpokeError, match="only a side"):
        pad_relief.resolve(_rect(), ["NE:0.5"])


def test_the_pad_frame_turns_with_the_pad_and_the_board_frame_does_not():
    pad = _turned(60)
    seg_pad = _at(pad, ["W"])[0]
    assert math.degrees(math.atan2(seg_pad[3], seg_pad[2])) % 360 == pytest.approx(240, abs=1e-3)
    seg_board = _at(pad, ["W"], frame="board")[0]
    assert math.degrees(math.atan2(seg_board[3], seg_board[2])) % 360 == pytest.approx(150, abs=1e-3)
    pad = _turned(30)
    seg_board = _at(pad, ["90"], frame="board")[0]
    assert math.degrees(math.atan2(seg_board[3], seg_board[2])) == pytest.approx(90, abs=1e-3)


def test_unknown_names_and_frames_are_refused():
    with pytest.raises(pad_relief.SpokeError):
        pad_relief.resolve(_rect(), ["UP"])
    with pytest.raises(pad_relief.SpokeError, match="frame"):
        pad_relief.resolve(_rect(), ["N"], frame="part")
    with pytest.raises(pad_relief.SpokeError, match="outline"):
        pad_relief.resolve({"x": 0, "y": 0, "outline": []}, ["N"])


def test_a_board_frame_corner_on_a_turned_rectangle_stays_a_corner():
    # Turned 30, a side faces NE within 15 degrees; a corner is still asked.
    seg, frac, x, y = _at(_turned(30), ["NE"], frame="board")[0]
    assert frac == 0.0
    assert math.hypot(x, y) == pytest.approx(math.hypot(10, 15), abs=1e-3)


def test_a_turned_octagon_keeps_its_cut_corners():
    pad = _octagon()
    a = math.radians(20)
    for seg in pad["outline"]:
        x, y = seg["x"], seg["y"]
        seg["x"], seg["y"] = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
    seg, frac, x, y = _at(pad, ["NE"], frame="board")[0]
    assert frac == 0.5
    assert math.degrees(math.atan2(y, x)) == pytest.approx(65, abs=1e-3)


def test_a_side_exactly_between_two_is_refused_not_guessed():
    with pytest.raises(pad_relief.SpokeError, match="no straight side"):
        pad_relief.resolve(_turned(45), ["N"], frame="board")


def test_points_never_use_exponent_notation():
    # Seen live: a via a hair off 90 degrees gave fraction 3e-06, which
    # Altium's script refused.
    assert pad_relief.points([{"segment": 1, "fraction": 3e-06}]) == "1:0.000003"
    assert "e" not in pad_relief.points([{"segment": 1, "fraction": 1e-9}])


def test_a_spoke_as_wide_as_the_pad_is_refused():
    # Seen live: 39 mil spokes on a 35 mil via poured as one blob.
    pad = dict(_round(), size_x=30, size_y=30)
    assert _at(pad, ["0"], width=29.9)
    for w in (30, 39.37):
        with pytest.raises(pad_relief.SpokeError, match="not narrower than the pad"):
            pad_relief.resolve(pad, ["0"], width=w)
    with pytest.raises(pad_relief.SpokeError, match="not narrower"):
        pad_relief.resolve(dict(_rect(), size_x=20, size_y=30, width=20), ["N"])


def test_points_are_segment_and_fraction_pairs():
    got = pad_relief.resolve(_rect(), ["N", "SW"])
    assert pad_relief.points(got) == "3:0.500000;1:0.000000"


# --- the Pascal handler ----------------------------------------------------

def _units() -> dict:
    return load(_ALTIUM / "PCB.pas", minimum=20)


def test_the_action_is_dispatched():
    assert re.search(r"'pad_relief':\s+Result := PCB_PadRelief\(Params, RequestId\);",
                     _units()["HandlePCBCommand"])


def test_the_record_is_keyed_by_an_empty_item_never_a_layer():
    body = _units()["PCB_PadRelief"]
    assert "Item := TPadViaThermalReliefItem;" in body
    assert not re.search(r"ThermalReliefData\[L\]", body)
    assert "EditThermalReliefData" not in body


def test_the_style_map_matches_altium_25():
    body = _units()["PCB_PadRelief"]
    assert "If Style = 'relief' Then StyleN := 0" in body
    assert "Else If Style = 'direct' Then StyleN := 1" in body
    assert "Else If Style = 'none' Then StyleN := 2" in body
    rec = _units()["PadReliefRecord"]
    assert "If D.Rotation = 0 Then S := S + ',' + JsonInt('rotation', 45)" in rec
    body_set = body[body.index("Obj.ThermalReliefData[Item] := D") - 600:]
    assert "If RotS = '45' Then D.Rotation := 0;" in body_set
    assert "If RotS = '90' Then D.Rotation := 1;" in body_set


def test_every_value_is_checked_before_the_board_is_touched():
    body = _units()["PCB_PadRelief"]
    first = body.index("GetPCBBoardAnywhere")
    for refusal in ("'style is rule, relief, direct or none'", "'spokes is 2 or 4'",
                    "'rotation is 45 or 90'", "'width is a length in mils above 0'",
                    "fraction 0 .. 1'"):
        assert body.index(refusal) < first, refusal


def test_a_thru_hole_pad_takes_a_layer_like_a_via():
    body = _units()["PCB_PadRelief"]
    smd = body.index("If IsSmd And (LayerS <> '') Then")
    assert body.index("IsSmd := (L = eTopLayer) Or (L = eBottomLayer);") < smd
    assert smd < body.index("PcbBeginEdit(0);")
    th = body.index("If Not IsSmd Then")
    assert "If Not PadReliefLayer(Board, LayerS, L) Then" in body[th:th + 200]
    assert "'NOT_SUPPORTED'" not in body
    assert "Body := Body + PadReliefScope(Pad.Mode)" in body
    scope = _units()["PadReliefScope"]
    assert "JsonStr('mode', 'simple') + ',' + JsonStr('record_scope', 'all layers')" in scope
    assert "JsonStr('mode', 'top-middle-bottom') + ',' + JsonStr('record_scope', 'inner layers')" in scope
    # Measured: a simple stack shares ONE spoke list across layers.
    simple = scope.index("JsonStr('mode', 'simple')")
    assert "JsonStr('spoke_scope', 'all layers')" in scope[simple:scope.index("Else If Mode = 1")]
    tmb = scope.index("Else If Mode = 1")
    full = scope.index("JsonStr('mode', 'full stack')")
    assert "JsonStr('spoke_scope', 'per layer')" in scope[tmb:full]
    # Measured: a full stack has a list on every copper layer, planes too.
    assert "JsonStr('spoke_scope', 'per layer')" in scope[full:]


def test_a_spoke_write_adds_before_it_clears_and_replaces_the_list():
    body = _units()["PCB_PadRelief"]
    add = body.index("Obj.AddCustomRelief(L, 0, Spare);")
    clear = body.index("Obj.ClearCustomRelief(L);")
    assert body.index("D.UseCustomRelief := True;") < body.index("Obj.ThermalReliefData[Item] := D;") < add < clear
    assert clear < body.index("Obj.AddCustomRelief(L, Edge, Frac);")


def test_the_custom_list_is_only_read_where_this_call_made_it():
    body = _units()["PCB_PadRelief"]
    read = body.index("PadReliefSpokes(Board, Obj, L)")
    guard = body.rfind("If IsSmd And (SegCount > 0) And D.UseCustomRelief", 0, read)
    assert guard > 0 and "((Action = 'spokes') Or ReadSpokes)" in body[guard:read]
    probe = body.index("Obj.RemoveCustomRelief(L, 0, Spare);")
    assert body.index("Obj.AddCustomRelief(L, 0, Spare);", body.index("Else If ReadSpokes")) < probe


def test_a_spoke_not_narrower_than_the_pad_is_refused_before_any_write():
    body = _units()["PCB_PadRelief"]
    guard = body.index("If SpokeW >= PadMin Then")
    assert guard < body.index("PcbBeginEdit(0);")
    assert "If CoordToMilsF(Pad.TopYSize) < PadMin Then PadMin := CoordToMilsF(Pad.TopYSize);" in body
    assert "If WidthS <> '' Then SpokeW := Width;" in body
    # Only where spokes will be poured: the style the pad ends up with.
    assert "If (Action <> 'read') And (Style <> 'rule') And (FinalStyle = 0) Then" in body
    assert "If Action = 'spokes' Then FinalStyle := 0;" in body


def test_a_via_is_a_free_via_named_by_x_and_y_with_a_layer_for_its_spokes():
    body = _units()["PCB_PadRelief"]
    assert "If IsVia And ((Des <> '') Or (PadName <> '') Or (XS = '') Or (YS = '')) Then" in body
    assert "Via := PadReliefFindVia(Board, XS, YS, Count)" in body
    layer = _units()["PadReliefLayer"]
    assert "If LowerCase(LayerS) = 'bottom' Then L := eBottomLayer" in layer
    assert "Else If LowerCase(LayerS) = 'mid' Then L := eMidLayer1" in layer
    find = _units()["PadReliefFindVia"]
    assert "Iter.AddFilter_ObjectSet(MkSet(eViaObject));" in find
    assert "Iter.AddFilter_Method(eProcessFree);" in find


def test_a_via_says_which_layers_its_record_covers():
    # Measured: on a top-middle-bottom via the empty key reaches the
    # MIDDLE record only; a simple via has one record for every layer.
    body = _units()["PCB_PadRelief"]
    assert "+ PadReliefScope(Via.Mode)" in body
    assert "PadMin := CoordToMilsF(Via.Size)" in body


def test_the_throwaway_spoke_avoids_every_wanted_spot():
    # Live: a spoke wanted at segment 0 / 0.5 (where the throwaway sat) was
    # dropped; a read's add + remove there would delete a real spoke.
    body = _units()["PCB_PadRelief"]
    assert "Spare := PadReliefSpare(Points);" in body
    assert "Spare := PadReliefSpare('');" in body
    spare = _units()["PadReliefSpare"]
    assert "If (Edge = 0) And (Abs(Frac - Want) < 0.0001) Then Used := True;" in spare
    assert "0.5;" not in spare


def test_an_ambiguous_pad_is_refused():
    body = _units()["PCB_PadRelief"]
    assert "If Count > 1 Then" in body and "'AMBIGUOUS'" in body


def test_a_pcblib_in_front_is_its_own_footprint_never_another_board():
    body = _units()["PCB_PadRelief"]
    lib = body.index("If PadReliefLibInFront(0) Then")
    board = body.index("Board := GetPCBBoardAnywhere(0);")
    # The library is asked first; another open board is only the fallback.
    assert lib < board
    branch = body[lib:board]
    assert "Footprint := PcbLibTarget(PcbLib, FootS, Problem);" in branch
    assert "Board := PcbLib.Board;" in branch
    assert "'a footprint has no parts: name the pad by pad name or x + y'" in branch
    assert "'footprint_name is for a PcbLib in front'" in body[board - 400:board]
    # Found inside the footprint, not by a board walk.
    assert ("Via := PadReliefFindInFootprint(Board, Footprint, True, '', XS, YS, Count)"
            in body)
    assert ("Pad := PadReliefFindInFootprint(Board, Footprint, False, PadName, XS, YS, Count)"
            in body)
    # PCBServer only after the DM layer said PcbLib (see GetPCBBoardAnywhere).
    front = _units()["PadReliefLibInFront"]
    assert "Doc := Workspace.DM_FocusedDocument;" in front
    assert "Result := Kind = 'PCBLIB';" in front
    assert "PCBServer" not in front


def test_a_footprint_object_is_found_by_its_own_iterator_and_origin():
    find = _units()["PadReliefFindInFootprint"]
    assert "GIter := Footprint.GroupIterator_Create;" in find
    assert "Footprint.GroupIterator_Destroy(GIter);" in find
    assert "If IsVia Then GIter.AddFilter_ObjectSet(MkSet(eViaObject))" in find
    assert "Else GIter.AddFilter_ObjectSet(MkSet(ePadObject));" in find
    assert "Abs(CoordToMilsF(Prim.X - Board.XOrigin) - X) < 0.001" in find
    assert "Hit := Name = PadName;" in find


def test_a_footprint_write_marks_the_library_and_names_the_footprint():
    body = _units()["PCB_PadRelief"]
    end = body.index("PcbEndEdit(0);", body.index("Obj.EndModify;"))
    assert "If Footprint <> Nil Then MarkDocDirtyByPath(Board.FileName);" in body[end:end + 120]
    assert "Body := Body + ',' + JsonStr('footprint', Footprint.Name);" in body


# --- the tools -------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def reg(fn):
            self.tools[fn.__name__] = fn
            return fn
        return reg


class _Bridge:
    def __init__(self, read):
        self.sent = []
        self.read = read

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, dict(params)))
        if params.get("action") == "read":
            return dict(self.read)
        return {"written": True}


def _tool(monkeypatch, name, read=None, **kwargs):
    bridge = _Bridge(read or {})
    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools[name](**kwargs)), bridge.sent


def test_no_value_reads_and_any_value_writes(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_pad_relief", designator="C1", pad="1")
    assert sent == [("pcb.pad_relief", {"designator": "C1", "pad": "1", "action": "read"})]
    _, sent = _tool(monkeypatch, "pcb_pad_relief", designator="C1", pad="1",
                    style="Relief", spokes=2, width=4, gap=4, rotation=45)
    assert sent[0][1] == {"designator": "C1", "pad": "1", "style": "relief", "spokes": "2",
                          "width": "4.0", "gap": "4.0", "rotation": "45", "action": "set"}


def test_a_via_and_its_layer_are_passed(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_pad_relief", x=1840.5512, y=5354.3307, kind="via",
                    layer="mid", width=10)
    assert sent[0][1] == {"kind": "via", "layer": "mid", "x": "1840.5512", "y": "5354.3307",
                          "width": "10.0", "action": "set"}
    via = dict(_round(), size_x=35.4, size_y=35.4, side="via")
    reply, sent = _tool(monkeypatch, "pcb_pad_relief_spokes", read=via, x=0, y=0, kind="via",
                        layer="bottom", spokes=["-22.5", "22.5"], frame="board")
    assert [s[1]["action"] for s in sent] == ["read", "spokes"]
    assert all(s[1]["kind"] == "via" and s[1]["layer"] == "bottom" for s in sent)
    assert sent[1][1]["points"] == "3:0.750000;0:0.250000"


def test_a_free_pad_is_found_by_x_and_y(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_pad_relief", x=1929.1339, y=6456.6929)
    assert sent[0][1] == {"x": "1929.1339", "y": "6456.6929", "action": "read"}


def test_spokes_are_resolved_from_the_read_and_written_as_points(monkeypatch):
    reply, sent = _tool(monkeypatch, "pcb_pad_relief_spokes", read=_rect(),
                        designator="C1", pad="1", spokes="N, SW", gap=4)
    assert [s[1]["action"] for s in sent] == ["read", "spokes"]
    assert sent[1][1]["points"] == "3:0.500000;1:0.000000"
    assert sent[1][1]["gap"] == "4.0"
    assert [p["segment"] for p in reply["planned"]] == [3, 1]


def test_a_dry_run_or_a_refusal_writes_nothing(monkeypatch):
    reply, sent = _tool(monkeypatch, "pcb_pad_relief_spokes", read=_rect(),
                        designator="C1", pad="1", spokes=["N"], dry_run=True)
    assert reply["dry_run"] and len(sent) == 1
    reply, sent = _tool(monkeypatch, "pcb_pad_relief_spokes", read=_oblong(),
                        designator="C1", pad="1", spokes=["N"])
    assert reply["ok"] is False and len(sent) == 1
    reply, sent = _tool(monkeypatch, "pcb_pad_relief_spokes", read=dict(_round(), side="thru"),
                        designator="J1", pad="1", layer="bottom", spokes=["0"])
    assert [s[1]["action"] for s in sent] == ["read", "spokes"]
    assert sent[1][1]["layer"] == "bottom"


def test_the_footprint_is_passed(monkeypatch):
    _, sent = _tool(monkeypatch, "pcb_pad_relief", pad="1", footprint=" MH1 ")
    assert sent[0][1] == {"footprint_name": "MH1", "pad": "1", "action": "read"}
    via = dict(_round(), size_x=35.4, size_y=35.4, side="via")
    _, sent = _tool(monkeypatch, "pcb_pad_relief_spokes", read=via, x=0, y=0, kind="via",
                    footprint="MH1", spokes=["0"], frame="board")
    assert [s[1]["action"] for s in sent] == ["read", "spokes"]
    assert all(s[1]["footprint_name"] == "MH1" for s in sent)
