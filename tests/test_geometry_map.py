# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_apply_geometry_map finds each object by its geometry, and never guesses.

Regenerating a board's other sizes moves some twenty thousand free
primitives, and obj_batch_modify searches the whole board for each one.
The map names every object by its current geometry, since Altium gives no
handle known to survive a reopen; the board is dumped once, matched here,
and applied in one pass that checks each object's old geometry first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from eda_agent.geometry_map import build_edits, parse_dump

PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"
C = 10000


def _dump(*lines):
    return parse_dump("\n".join(lines + ("L\t1\tTopLayer", "L\t57\tMechanical1",
                                         "L\t10\tTopOverlay")))


def _track_line(o, lyr, w, x1, y1, x2, y2):
    return f"T\t{o}\t{lyr}\t{w * C}\t{x1 * C}\t{y1 * C}\t{x2 * C}\t{y2 * C}"


def _kind(lines, k):
    return [ln for ln in lines if ln.startswith(k + "\t")]


def test_the_dump_is_read_for_every_kind():
    d = parse_dump("\n".join([
        "T\t0\t1\t100\t1\t2\t3\t4",
        "A\t3\t1\t100\t5\t6\t7\t0\t90.5",
        "V\t1\t8\t9",
        "X\t2\t10\t1\t2\tSIZE M",
        "F\t0\t1\t1\t2\t3\t4",
        "R\t4\t1\t5\t\t\t10\t20\t2",
        "P\t0\t1\t4\t30\t40",
        "O\t6391",
        "L\t1\tTopLayer"]))
    assert d.tracks == [(0, 1, 100, 1, 2, 3, 4)]
    assert d.arcs == [(3, 1, 100, 5, 6, 7, "0", "90.5")]
    assert d.vias == [(1, 8, 9)] and d.texts == [(2, 10, 1, 2, "SIZE M")]
    assert d.regions == [(4, 1, 5, None, None, 10, 20, 2)]
    assert d.polygons == [(0, 1, 4, 30, 40)]
    assert d.outline_segments == 6391 and d.layers == {1: "TopLayer"}


def test_a_track_within_tolerance_matches_and_one_past_it_does_not():
    d = _dump(_track_line(7, 57, 10, 100, 200, 300, 200))
    near = {"layer": "Mechanical1", "width": 10, "key": [100.005, 200, 300, 200],
            "new": [110, 210, 310, 210]}
    far = dict(near, key=[100.02, 200, 300, 200])
    lines, rep = build_edits({"tracks": [near]}, d, tolerance_mil=0.01)
    assert rep["tracks"]["matched"] == 1
    assert _kind(lines, "T") == [
        f"T\t7\t{100 * C}\t{200 * C}\t{300 * C}\t{200 * C}\t57\t{10 * C}\t"
        f"{110 * C}\t{210 * C}\t{310 * C}\t{210 * C}\t{10 * C}"]
    _, rep = build_edits({"tracks": [far]}, d, tolerance_mil=0.01)
    assert rep["tracks"]["unmatched"] == 1


def test_width_and_layer_tell_overlaid_outline_copies_apart():
    # The same outline segment on KeepOut, Mech1 and Mech16, or twice as wide.
    d = _dump(_track_line(0, 1, 10, 0, 0, 50, 0), _track_line(1, 57, 10, 0, 0, 50, 0),
              _track_line(2, 57, 20, 0, 0, 50, 0))
    e = {"layer": "Mech1", "width": 10, "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}
    lines, rep = build_edits({"tracks": [e]}, d)
    assert rep["tracks"]["matched"] == 1 and _kind(lines, "T")[0].startswith("T\t1\t")


def test_a_track_keyed_end_to_end_gets_its_new_ends_swapped_too():
    d = _dump(_track_line(0, 1, 10, 0, 0, 50, 0))
    e = {"layer": "TopLayer", "width": 10, "key": [50, 0, 0, 0], "new": [60, 5, 10, 5]}
    lines, _ = build_edits({"tracks": [e]}, d)
    assert _kind(lines, "T")[0].split("\t")[8:12] == [
        str(10 * C), str(5 * C), str(60 * C), str(5 * C)]


def test_exact_duplicates_asking_the_same_move_are_paired_off():
    d = _dump(*(_track_line(o, 1, 10, 0, 0, 50, 0) for o in (3, 5, 9)))
    e = {"layer": "TopLayer", "width": 10, "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}
    lines, rep = build_edits({"tracks": [e, dict(e), dict(e)]}, d)
    assert rep["tracks"]["matched"] == 3 and rep["tracks"]["ambiguous"] == 0
    assert [ln.split("\t")[1] for ln in _kind(lines, "T")] == ["3", "5", "9"]


def test_duplicates_asking_different_moves_are_not_guessed():
    d = _dump(*(_track_line(o, 1, 10, 0, 0, 50, 0) for o in (3, 5)))
    e = {"layer": "TopLayer", "width": 10, "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}
    lines, rep = build_edits({"tracks": [e, dict(e, new=[2, 2, 52, 2])]}, d)
    assert rep["tracks"]["ambiguous"] == 2 and not _kind(lines, "T")


def test_one_object_two_entries_reach_is_applied_by_neither():
    d = _dump(_track_line(0, 1, 10, 0, 0, 50, 0))
    a = {"layer": "TopLayer", "width": 10, "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}
    b = dict(a, key=[0.004, 0, 50, 0], new=[9, 9, 59, 9])
    lines, rep = build_edits({"tracks": [a, b]}, d)
    assert rep["tracks"]["ambiguous"] == 2 and not lines


def test_arc_angles_match_across_zero_degrees():
    d = _dump(f"A\t4\t1\t{10 * C}\t{100 * C}\t{100 * C}\t{50 * C}\t359.99995\t90")
    e = {"layer": "TopLayer", "width": 10, "key": [100, 100, 50, 0.0, 90.0],
         "new": [110, 100, 50, 0.0, 90.0]}
    lines, rep = build_edits({"arcs": [e]}, d, angle_tolerance_deg=1e-4)
    assert rep["arcs"]["matched"] == 1
    # The old angles go back exactly as Altium printed them.
    assert _kind(lines, "A")[0].split("\t")[5:7] == ["359.99995", "90"]


def test_a_pours_short_layer_name_resolves():
    d = _dump(f"P\t2\t1\t4\t{10 * C}\t{20 * C}")
    seg = [{"kind": 0, "vx": 1, "vy": 2}, {"kind": 0, "vx": 3, "vy": 2},
           {"kind": 1, "vx": 3, "vy": 4, "cx": 2, "cy": 4, "r": 1, "sa": 0, "ea": 180}]
    lines, rep = build_edits({"polygons": [{"layer": "TOP", "key_first": [10, 20],
                                            "segments": seg}]}, d)
    assert rep["polygons"]["matched"] == 1
    assert lines[0] == f"P\t2\t1\t4\t{10 * C}\t{20 * C}\t3"
    assert lines[3] == f"s\t1\t{3 * C}\t{4 * C}\t{2 * C}\t{4 * C}\t{1 * C}\t0.0\t180.0"


def test_an_unknown_layer_is_refused_and_names_the_boards_layers():
    d = _dump(_track_line(0, 1, 10, 0, 0, 50, 0))
    _, rep = build_edits({"tracks": [{"layer": "Mechanical99", "width": 10,
                                      "key": [0, 0, 50, 0], "new": [0, 0, 1, 1]}]}, d)
    assert rep["tracks"]["refused"] == 1
    assert "TopLayer" in rep["tracks"]["samples"][0]["refused"]


@pytest.mark.parametrize("first", [(0, 0), (1, 1)])
def test_a_region_is_found_by_its_first_vertex_at_either_contour_index(first):
    pts = {0: (f"{5 * C}", f"{6 * C}"), 1: (f"{7 * C}", f"{8 * C}")}
    d = _dump(f"R\t3\t1\t4\t{pts[0][0]}\t{pts[0][1]}\t{pts[1][0]}\t{pts[1][1]}\t1")
    fx, fy = (5, 6) if first == (0, 0) else (7, 8)
    e = {"layer": "TopLayer", "key": {"n": 4, "first": [fx, fy], "holes": 1},
         "contour": [[0, 0], [1, 0], [1, 1], [0, 1]], "holes": [[[0.2, 0.2], [0.4, 0.2],
                                                                  [0.3, 0.4]]]}
    lines, rep = build_edits({"regions": [e]}, d)
    assert rep["regions"]["matched"] == 1
    # The index it was found at goes along as the contour's base.
    assert lines[0] == f"R\t3\t1\t4\t{fx * C}\t{fy * C}\t1\t4\t1\t{first[0]}"
    assert lines[5] == "h\t3" and len(lines) == 9


def test_a_region_whose_new_holes_do_not_match_its_old_count_is_refused():
    d = _dump(f"R\t3\t1\t4\t\t\t{5 * C}\t{6 * C}\t2")
    e = {"layer": "TopLayer", "key": {"n": 4, "first": [5, 6], "holes": 2},
         "contour": [[0, 0], [1, 0], [1, 1]], "holes": [[[0, 0], [1, 0], [0, 1]]]}
    lines, rep = build_edits({"regions": [e]}, d)
    assert rep["regions"]["refused"] == 1 and not lines


def test_a_text_must_say_what_the_key_says_and_its_new_string_is_written():
    d = _dump(f"X\t5\t10\t{1 * C}\t{2 * C}\tM")
    e = {"layer": "TopOverlay", "key": [1, 2, "M"], "new": [3, 4, "XL"]}
    lines, rep = build_edits({"texts": [e]}, d)
    assert lines == [f"X\t5\t{1 * C}\t{2 * C}\t10\t{3 * C}\t{4 * C}\t1\tXL"]
    _, rep = build_edits({"texts": [dict(e, key=[1, 2, "S"])]}, d)
    assert rep["texts"]["unmatched"] == 1
    _, rep = build_edits({"texts": [dict(e, new=[3, 4, "X\tL"])]}, d)
    assert rep["texts"]["refused"] == 1


def test_edit_lines_come_in_the_order_the_apply_pass_reads():
    d = _dump(_track_line(9, 1, 10, 0, 0, 50, 0), _track_line(2, 1, 10, 0, 10, 50, 10),
              f"V\t0\t{5 * C}\t{5 * C}", "O\t4")
    t = {"layer": "TopLayer", "width": 10, "new": [0, 0, 1, 1]}
    seg = [{"kind": 0, "vx": x, "vy": y} for x, y in ((0, 0), (9, 0), (9, 9))]
    lines, _ = build_edits({"board_outline": seg,
                            "vias": [{"key": [5, 5], "new": [6, 6]}],
                            "tracks": [dict(t, key=[0, 0, 50, 0]),
                                       dict(t, key=[0, 10, 50, 10])]}, d)
    assert [ln[:3] for ln in lines] == ["T\t2", "T\t9", "V\t0", "O\t4", "s\t0",
                                        "s\t0", "s\t0"]


def test_a_delete_is_matched_like_a_move_and_marked():
    d = _dump(_track_line(4, 57, 10, 0, 0, 50, 0),
              f"A\t2\t57\t{10 * C}\t{5 * C}\t{5 * C}\t{3 * C}\t0\t90")
    lines, rep = build_edits({"delete": [
        {"kind": "track", "layer": "Mechanical1", "width": 10, "key": [50, 0, 0, 0]},
        {"kind": "arc", "layer": "Mechanical1", "width": 10, "key": [5, 5, 3, 0, 90]},
        {"kind": "track", "layer": "Mechanical1", "width": 10, "key": [9, 9, 8, 8]},
    ]}, d)
    assert lines[0] == f"T\t4\t0\t0\t{50 * C}\t0\t57\t{10 * C}\tDEL"
    assert lines[1].startswith("A\t2\t") and lines[1].endswith("\tDEL")
    assert rep["delete"] == {"entries": 3, "matched": 2, "tracks": 1, "arcs": 1,
                             "regions": 0}
    assert rep["tracks"]["unmatched"] == 1


def test_region_deletes_pair_off_exact_duplicates_and_carry_no_contour():
    """MEASURED: Teardrops made two identical teardrop regions where two
    track ends coincide; one delete entry per copy removes both."""
    dup = f"R\t{{o}}\t1\t4\t{5 * C}\t{6 * C}\t\t\t0"
    d = _dump(dup.format(o=3), dup.format(o=8),
              f"R\t9\t1\t4\t{7 * C}\t{7 * C}\t\t\t0")
    key = {"kind": "region", "layer": "TopLayer", "key": {"n": 4, "first": [5, 6]}}
    lines, rep = build_edits({"delete": [dict(key), dict(key)]}, d)
    assert lines == [f"R\t3\t1\t4\t{5 * C}\t{6 * C}\t0\tDEL",
                     f"R\t8\t1\t4\t{5 * C}\t{6 * C}\t0\tDEL"]
    assert rep["delete"]["regions"] == 2 and rep["delete"]["matched"] == 2
    # One entry for two copies is still ambiguous, never a guess.
    lines, rep = build_edits({"delete": [dict(key)]}, d)
    assert not lines and rep["regions"]["ambiguous"] == 1


def test_one_object_moved_and_deleted_is_neither():
    d = _dump(_track_line(4, 1, 10, 0, 0, 50, 0))
    key = {"layer": "TopLayer", "width": 10, "key": [0, 0, 50, 0]}
    lines, rep = build_edits({"tracks": [dict(key, new=[1, 1, 51, 1])],
                              "delete": [dict(key, kind="track")]}, d)
    assert not lines and rep["tracks"]["ambiguous"] == 2


def test_creates_come_last_and_bad_ones_are_refused():
    d = _dump(_track_line(4, 1, 10, 0, 0, 50, 0))
    lines, rep = build_edits({
        "tracks": [{"layer": "TopLayer", "width": 10, "key": [0, 0, 50, 0],
                    "new": [1, 1, 51, 1]}],
        "create": [{"kind": "track", "layer": "KeepOutLayer", "width": 47.24,
                    "geom": [0, 0, 10, 0]},
                   {"kind": "arc", "layer": "Mechanical1", "width": 3.94,
                    "geom": [5, 5, 2, 0, 180]},
                   {"kind": "arc", "layer": "Mechanical1", "width": 3.94,
                    "geom": [5, 5, 0, 0, 180]},
                   {"kind": "polygon", "layer": "TopLayer", "width": 1, "geom": []}]}, d)
    assert lines[0].startswith("T\t4\t")
    # MEASURED: keep-out copies carry IsKeepout; a create there defaults to it.
    # The net is the last field, empty here.
    assert lines[1] == f"C\tT\tKeepOutLayer\t{472400}\t0\t0\t{10 * C}\t0\t1\t"
    assert lines[2] == (f"C\tA\tMechanical1\t{39400}\t{5 * C}\t{5 * C}\t{2 * C}"
                        "\t0.0\t180.0\t0\t")
    assert rep["create"]["matched"] == 2 and rep["create"]["refused"] == 2
    lines, _ = build_edits({"create": [{"kind": "track", "layer": "KeepOutLayer",
                                        "width": 1, "geom": [0, 0, 1, 0],
                                        "keepout": False}]}, d)
    assert lines == [f"C\tT\tKeepOutLayer\t{C}\t0\t0\t{C}\t0\t0\t"]


def test_a_created_region_carries_its_net_and_teardrop_flag():
    """Applying a map dropped a board's teardrops; they are recreated as
    free regions with a net and Altium's teardrop flag."""
    lines, rep = build_edits({"create": [
        {"kind": "region", "layer": "TopLayer", "net": "N1", "teardrop": True,
         "contour": [[0, 0], [4, 0], [2, 3], [0, 0]]},
        {"kind": "region", "layer": "BottomLayer", "contour": [[0, 0], [1, 0], [1, 1]]},
        {"kind": "region", "layer": "TopLayer", "contour": [[0, 0], [1, 0]]},
        {"kind": "region", "layer": "TopLayer", "net": "A\tB",
         "contour": [[0, 0], [1, 0], [1, 1]]},
        {"kind": "region", "layer": "TopLayer", "contour": [[0, 0], [1], [1, 1]]},
        {"kind": "region", "layer": "TopLayer",
         "contour": [[0, 0], [_HUGE, 0], [1, 1]]}]}, _dump())
    # The repeated closing vertex is dropped; the net goes last.
    assert lines == [
        f"C\tR\tTopLayer\t3\t1\t0\t0\t0\t{4 * C}\t0\t{2 * C}\t{3 * C}\tN1",
        f"C\tR\tBottomLayer\t3\t0\t0\t0\t0\t{C}\t0\t{C}\t{C}\t"]
    assert rep["create"]["matched"] == 2 and rep["create"]["refused"] == 4
    assert rep["create"]["out_of_range"] == 1


def test_the_region_create_flags_binds_and_reads_back_before_it_counts():
    body = _routine("PCB_ApplyFreePrimitives")
    reg = body[body.index("If Hdr = 'R' Then"):]
    assert "PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default)" in reg
    # A net the board lacks creates nothing, rather than a netless copy.
    missing = reg.index("If RNet = Nil Then")
    assert reg.index("Inc(CreateFailed);", missing) < reg.index("PCBObjectFactory")
    add = reg.index("Board.AddPCBObject(Region);")
    assert reg.index("Region.UpdateContourFromShape(True);") < add
    assert reg.index("Region.TearDrop := True;") < add
    assert reg.index("BindPrimitiveToNet(RNet, Region);") < add
    for check in ("Region.MainContour.Count", "Region.BoundingRectangle",
                  "Not Region.TearDrop", "Region.Net.Name <> A4"):
        assert reg.index(check) > add, check
    assert "Inc(CreateOff);" in reg
    raw = PCB.read_text(encoding="ascii")
    raw = raw[raw.index("Function PCB_ApplyFreePrimitives("):]
    assert """',"create_readback_off":'""" in raw
    assert """',"teardrops_created":'""" in raw


#: MEASURED: an arc radius of 662,000 mil, a near-straight outline arc,
#: raised EConvertError "'3249903854' is not a valid integer value" in the
#: middle of an apply and stopped the script. Altium's coordinates are
#: 32-bit, so every value is checked before anything is sent.
_HUGE = 662000


def test_values_altium_cannot_hold_are_refused_before_anything_is_sent():
    d = _dump(_track_line(4, 1, 10, 0, 0, 50, 0), "O\t4",
              f"A\t2\t57\t{10 * C}\t{5 * C}\t{5 * C}\t{3 * C}\t0\t90",
              f"V\t0\t{5 * C}\t{5 * C}", f"X\t5\t10\t{1 * C}\t{2 * C}\tM",
              f"F\t6\t1\t0\t0\t{C}\t{C}")
    near_straight = [{"kind": 0, "vx": 0, "vy": 0}, {"kind": 0, "vx": 9, "vy": 0},
                     {"kind": 1, "vx": 9, "vy": 9, "cx": -_HUGE + 9, "cy": 9,
                      "r": _HUGE, "sa": 0, "ea": 0.001}]
    lines, rep = build_edits({
        "tracks": [{"layer": "TopLayer", "width": 10, "key": [0, 0, 50, 0],
                    "new": [0, 0, 50, 0], "new_width": float("inf")}],
        "arcs": [{"layer": "Mechanical1", "width": 10, "key": [5, 5, 3, 0, 90],
                  "new": [5, 5, _HUGE, 0, 90]}],
        "vias": [{"key": [5, 5], "new": [_HUGE, 0]}],
        "texts": [{"layer": "TopOverlay", "key": [1, 2, "M"], "new": [0, -_HUGE]}],
        "fills": [{"layer": "TopLayer", "key": [0, 0, 1, 1], "new": [0, 0, _HUGE, 1]}],
        "board_outline": near_straight,
        "create": [{"kind": "arc", "layer": "Mechanical1", "width": 3.94,
                    "geom": [0, 0, _HUGE, 0, 1]},
                   # The centre fits and the radius fits; the far side does not.
                   {"kind": "arc", "layer": "Mechanical1", "width": 3.94,
                    "geom": [200000, 0, 20000, 0, 1]},
                   {"kind": "track", "layer": "Mechanical1", "width": 3.94,
                    "geom": [0, 0, float("nan"), 0]}]}, d)
    assert lines == []
    for kind in ("tracks", "arcs", "vias", "texts", "fills", "board_outline"):
        assert rep[kind]["refused"] == 1, kind
        assert "outside Altium's coordinate range" in rep[kind]["samples"][0]["refused"]
    assert rep["create"]["refused"] == 3 and rep["create"]["matched"] == 0
    # The largest value that fits still goes through.
    lines, rep = build_edits({"vias": [{"key": [5, 5], "new": [214748, 0]}]}, d)
    assert lines == [f"V\t0\t{5 * C}\t{5 * C}\t{214748 * C}\t0"]


def test_every_polygon_writer_rewrites_the_closing_vertex():
    """MEASURED: a replaced board outline saved 412 vertices for 411
    segments, the last still the OLD first vertex, a spike in the shape.
    A polygon keeps a closing vertex at index PointCount, so every routine
    that sets PointCount must write it after its last segment."""
    scripts = PCB.parent
    writers = 0
    for name in ("PCB.pas", "PCBGeneric.pas", "Generic.pas", "Library.pas"):
        text = (scripts / name).read_text(encoding="ascii", errors="replace")
        for m in re.finditer(r"(\w+(?:\.\w+)?)\.PointCount := ([^;]+);", text):
            obj, count = m.group(1), m.group(2).strip()
            body = text[m.end():]
            body = body[:re.search(r"\n(?:Function|Procedure) ", body).start()]
            # Up to this object's next PointCount: one routine can build
            # several polygons, each closed in its own turn.
            nxt = body.find(f"{obj}.PointCount := ")
            if nxt >= 0:
                body = body[:nxt]
            close = body.find(f"ClosePolySegments({obj}, {count});")
            assert close >= 0, f"{name}: {obj}.PointCount := {count} is never closed"
            writes = [w.start() for w in re.finditer(
                re.escape(obj) + r"\.Segments\[[^\]]+\] :=", body)]
            assert writes and writes[-1] < close, (
                f"{name}: {obj} writes a segment after it is closed")
            writers += 1
    assert writers == 9
    helper = (scripts / "PCBGeneric.pas").read_text(encoding="ascii")
    helper = helper[helper.index("Procedure ClosePolySegments("):]
    helper = helper[:helper.index("End;") + 4]
    assert "Seg := Poly.Segments[0];" in helper
    assert "Poly.Segments[N] := Seg;" in helper
    apply = _routine("PCB_ApplyFreePrimitives")
    assert "Board.BoardOutline.Segments[N].vx <> Board.BoardOutline.Segments[0].vx" in apply


def test_the_apply_pass_deletes_and_creates_with_proven_calls():
    body = _routine("PCB_ApplyFreePrimitives")
    dele = body[body.index("If A1 = 'DEL' Then"):]
    assert dele.index("PCBM_BoardRegisteration, Prim.I_ObjectAddress);") < dele.index(
        "Board.RemovePCBObject(Prim);")
    assert body.count("Board.RemovePCBObject(Prim);") == 3
    region = body[body.index("Else If (KindNo = 6) And (Pos(#9 + 'DEL', Lines[J]) > 0) Then"):]
    region = region[:region.index("Else If KindNo = 6 Then")]
    assert region.index("PCBM_BoardRegisteration, Prim.I_ObjectAddress);") < region.index(
        "Board.RemovePCBObject(Prim);")
    create = body[body.index("If Copy(Lines[LI], 1, 2) <> 'C' + #9 Then Continue;"):]
    assert "NewLayer := ResolveLayerId(Board, A1);" in create
    assert "If NewLayer = eNoLayer Then" in create
    assert "PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default)" in create
    assert "PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default)" in create
    assert create.index("Board.AddPCBObject(Track);") < create.index("PcbEndEdit(0)")
    assert create.index("Track.IsKeepout := True;") < create.index("Board.AddPCBObject(Track);")
    assert create.index("Arc.IsKeepout := True;") < create.index("Board.AddPCBObject(Arc);")
    raw = PCB.read_text(encoding="ascii")
    raw = raw[raw.index("Function PCB_ApplyFreePrimitives("):]
    assert """',"deleted":'""" in raw and """',"created":'""" in raw


def test_a_moved_track_or_arc_is_read_back_width_included():
    """A width trim of a hundredth of a mil is only worth anything if a
    write that did not take is counted."""
    body = _routine("PCB_ApplyFreePrimitives")
    track = body[body.index("WW := StrToIntDef(SplitNextTab(Rest), Track.Width);"):]
    assert track.index("Track.EndModify;") < track.index("(Track.Width <> WW) Then Inc(WireOff);")
    arc = body[body.index("WW := StrToIntDef(SplitNextTab(Rest), Arc.LineWidth);"):]
    assert arc.index("Arc.EndModify;") < arc.index("(Arc.LineWidth <> WW) Then Inc(WireOff);")
    raw = PCB.read_text(encoding="ascii")
    raw = raw[raw.index("Function PCB_ApplyFreePrimitives("):]
    assert """',"track_arc_readback_off":' + IntToStr(WireOff)""" in raw


def test_a_new_width_rides_the_track_line_in_internal_units():
    d = _dump(_track_line(4, 1, 10, 0, 0, 50, 0))
    lines, _ = build_edits({"tracks": [{"layer": "TopLayer", "width": 10,
                                        "key": [0, 0, 50, 0], "new": [0, 0, 50, 0],
                                        "new_width": 9.97}]}, d)
    assert lines[0].endswith(f"\t0\t0\t{50 * C}\t0\t99700")


def test_components_are_counted_and_left_to_pcb_move_components():
    _, rep = build_edits({"components": [{"designator": "R1"}]}, _dump())
    assert rep["components"]["applied"] == 0
    assert "pcb_move_components" in rep["components"]["note"]


# --- the Pascal passes --------------------------------------------------------


def _routine(name: str) -> str:
    code = PCB.read_text(encoding="ascii")
    start = re.search(rf"(?m)^Function {name}\(", code)
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"\{[^}]*\}", "", body)


def test_dump_and_apply_walk_every_layer_with_one_layer_set():
    """MEASURED: 6390 outline primitives on Mechanical17 were missing from
    the dump; the legacy AllLayers set stops at Mechanical16. The apply pass
    finds objects by ordinal, so it must walk the same set."""
    for name in ("PCB_DumpFreePrimitives", "PCB_ApplyFreePrimitives"):
        body = _routine(name)
        assert "AddFilter_LayerSet(AllLayers)" not in body, name
        assert "AddFilter_IPCB_LayerSet(LayerSet.AllLayers)" in body, name
    generic = (PCB.parent / "PCBGeneric.pas").read_text(encoding="ascii")
    assert "AddFilter_LayerSet(AllLayers)" not in generic
    assert "Then Result := LayerDisplayName(Obj.Layer)" in generic


def test_the_three_actions_are_dispatched():
    code = PCB.read_text(encoding="ascii")
    for action, fn in (("save_copy_as", "PCB_SaveCopyAs"),
                       ("dump_free_primitives", "PCB_DumpFreePrimitives"),
                       ("apply_free_primitives", "PCB_ApplyFreePrimitives")):
        assert re.search(rf"'{action}':\s+Result := {fn}\(Params, RequestId\);", code)


def test_every_pass_acts_only_on_the_named_board():
    for fn in ("PCB_SaveCopyAs", "PCB_DumpFreePrimitives", "PCB_ApplyFreePrimitives"):
        body = _routine(fn)
        assert "Board := GeoMapBoard(Params, Why);" in body
        assert "GetPCBBoardAnywhere" not in body
    guard = _routine("GeoMapBoard")
    assert "If UpperCase(Got) <> UpperCase(Path) Then" in guard
    assert "Result := Nil;" in guard[guard.index("If UpperCase(Got)"):]


def test_apply_checks_old_geometry_and_changes_only_after_the_walk():
    body = _routine("PCB_ApplyFreePrimitives")
    walk_end = body.index("Board.BoardIterator_Destroy(Iter);")
    assert body.index("Matched.Add(Prim);") < walk_end
    assert body.index("Track.BeginModify;") > walk_end
    assert "(A1 = IntToStr(Track.X1))" in body
    assert "(A6 = IntToStr(Track.Width))" in body
    # Owned objects are skipped before any check.
    assert "Prim.InComponent Or Prim.InPolygon Or Prim.InDimension" in body


def test_polygons_are_changed_only_through_a_polygon_typed_walk():
    # MEASURED: Poly.Invalidate on a polygon taken from the primitive list
    # halted the script with "Undeclared identifier: Invalidate".
    # A typed polygon then halted on the same name: Invalidate is undeclared
    # on IPCB_Polygon. The edit runs the sequence PCB_RepourPolygons runs.
    body = _routine("PCB_ApplyFreePrimitives")
    walk = body[body.index("If KindNo = 7 Then\n                    Begin"):]
    assert "Poly := Iter.FirstPCBObject;" in walk
    assert "Poly.PointCount := N;" not in body[:body.index("Poly := Iter.FirstPCBObject;")]
    for routine in ("PCB_ApplyFreePrimitives", "PCB_BevelPolygonCorners"):
        code = _routine(routine)
        assert not re.search(r"\b(Poly|Target)\.(Invalidate|Validate)\b", code), routine
        edit = code[code.index(".BeginModify;"):]
        assert re.search(r"SetState_CopperPourInvalid;\s+\w+\.Rebuild;\s+(End;\s+)?"
                         r"\w+\.EndModify;", edit), routine


def test_regions_are_rewritten_without_the_undeclared_geometric_polygon():
    body = _routine("PCB_ApplyFreePrimitives")
    assert "GeometricPolygon" not in body
    assert "Region.SetOutlineContour(Contour);" in body
    assert "Hole := Region.Holes[HN];" in body
    # MEASURED: 1..n written into a contour counting from 0 shifted every
    # vertex of 124 regions. Every index runs from the dumped base.
    assert "Base := StrToIntDef(SplitNextTab(Rest), 1);" in body
    writes = re.findall(r"(?:Contour|Hole)\.[XY]\[([^\]]+)\] :=", body)
    assert writes and all(w == "Base + K - 1" for w in writes)
    assert "HoleOk := (Hole.Count = N);" in body
    assert "Region.MainContour.X[Base]" in body


def test_a_shape_based_region_does_not_keep_its_old_shape():
    # MEASURED: contours read back new while Altium drew and saved the old
    # shape segments. The shape is cleared or rebuilt, and the readback
    # checks the drawn box, not only the contour arrays.
    body = _routine("PCB_ApplyFreePrimitives")
    shape = body[body.index("SC := Region.ShapeSegmentCount;"):]
    assert "If (SC > 0) And (ShapeMode <> 'keep') Then" in shape
    assert "Region.ShapeSegmentCount := 0;" in shape
    assert "Region.UpdateContourFromShape(True);" in shape
    assert shape.index("Region.UpdateContourFromShape(True);") < shape.index(
        "BR := Region.BoundingRectangle;")
    assert "Inc(BBoxOff);" in shape
    assert "If (ShapeMode <> 'keep') And (ShapeMode <> 'clear') Then ShapeMode := 'rebuild';" in body


def test_texts_are_moved_by_delta_after_their_string():
    # MEASURED: XLocation/YLocation writes left all 27 texts in place.
    body = _routine("PCB_ApplyFreePrimitives")
    assert "Txt.XLocation :=" not in body and "Txt.YLocation :=" not in body
    # The string is set in an edit of its own, closed before the delta is
    # taken: a delta measured inside it left three labels 3.8 mil off.
    text_set = body.index("Txt.Text := Rest;")
    move = body.index("Txt.MoveByXY(")
    assert body.index("Txt.EndModify;", text_set) < body.index("Txt.BeginModify;", text_set) < move
    assert move < body.index("Txt.EndModify;", move)


def test_the_dump_skips_owned_primitives_of_every_kind():
    body = _routine("PCB_DumpFreePrimitives")
    for var in ("Track", "Arc", "Via", "Txt", "Fil", "Region"):
        assert f"If Not ({var}.InComponent Or {var}.InPolygon Or {var}.InDimension)" in body


def test_save_copy_puts_the_open_boards_path_back_whatever_happens():
    body = _routine("PCB_SaveCopyAs")
    fin = body[body.index("Finally"):]
    assert "ServerDoc.SetFileName(SourcePath)" in fin
    assert "ServerDoc.SetModified(WasModified)" in fin
    assert body.index("'TARGET_OPEN'") < body.index("ServerDoc.DoFileSave('')")


# --- the tools ----------------------------------------------------------------


class _Bridge:
    def __init__(self, workspace, dump_text="", target_exists=False):
        self.config = type("Cfg", (), {"workspace_dir": workspace})()
        self.dump_text = dump_text
        self.target_exists = target_exists
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.dump_free_primitives":
            Path(params["out_path"]).write_text(self.dump_text, encoding="ascii")
            return {"board": params.get("board_path", ""), "tracks": 1}
        if command == "pcb.apply_free_primitives":
            return {"applied": 1, "stale": 0, "missing": 0}
        if command == "pcb.save_copy_as":
            if params.get("check_only"):
                return {"target_exists": self.target_exists, "written": False}
            Path(params["target_path"]).write_text("copy", encoding="ascii")
            return {"written": True}
        raise AssertionError(command)


def _tools(monkeypatch, bridge):
    import eda_agent.tools.pcb as pcb

    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(Mcp())
    return tools


@pytest.mark.asyncio
async def test_a_dry_run_matches_and_changes_nothing(tmp_path, monkeypatch):
    bridge = _Bridge(tmp_path, "T\t0\t1\t100000\t0\t0\t500000\t0\nL\t1\tTopLayer\n")
    tools = _tools(monkeypatch, bridge)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"tracks": [{"layer": "TopLayer", "width": 10,
                                          "key": [0, 0, 50, 0], "new": [1, 1, 51, 1]}]}))
    out = await tools["pcb_apply_geometry_map"](str(mp), board_path="C:/b.PcbDoc",
                                                dry_run=True)
    assert out["match"]["tracks"]["matched"] == 1 and "apply" not in out
    assert [c for c, _ in bridge.sent] == ["pcb.dump_free_primitives"]
    assert bridge.sent[0][1]["board_path"] == "C:/b.PcbDoc"

    out = await tools["pcb_apply_geometry_map"](str(mp), board_path="C:/b.PcbDoc")
    command, params = bridge.sent[-1]
    assert command == "pcb.apply_free_primitives" and params["board_path"] == "C:/b.PcbDoc"
    raw = Path(params["in_path"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"T\t0\t" in raw
    assert out["apply"]["applied"] == 1


@pytest.mark.asyncio
async def test_one_value_altium_cannot_hold_stops_the_whole_apply(tmp_path, monkeypatch):
    # The deletes of an outline whose replacement was refused would leave
    # the board half changed, so nothing is applied.
    bridge = _Bridge(tmp_path, f"V\t0\t{5 * C}\t{5 * C}\n"
                               f"T\t1\t57\t{10 * C}\t0\t0\t{50 * C}\t0\nL\t57\tMechanical1\n")
    tools = _tools(monkeypatch, bridge)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({
        "delete": [{"kind": "track", "layer": "Mechanical1", "width": 10,
                    "key": [0, 0, 50, 0]}],
        "create": [{"kind": "arc", "layer": "Mechanical1", "width": 3.94,
                    "geom": [0, 0, _HUGE, 0, 1]}]}))
    out = await tools["pcb_apply_geometry_map"](str(mp), board_path="b")
    assert out["success"] is False and "nothing applied" in out["error"]
    assert out["out_of_range"] == {"create": 1}
    assert [c for c, _ in bridge.sent] == ["pcb.dump_free_primitives"]
    # A dry run still reports the match, the refusal included.
    out = await tools["pcb_apply_geometry_map"](str(mp), board_path="b", dry_run=True)
    assert out["match"]["create"]["out_of_range"] == 1 and "error" not in out


@pytest.mark.asyncio
async def test_apply_can_leave_the_pours_for_later(tmp_path, monkeypatch):
    # MEASURED: a hatched pour of ~28k pieces took over 300 s to rebuild.
    bridge = _Bridge(tmp_path, f"V\t0\t{5 * C}\t{5 * C}\n")
    tools = _tools(monkeypatch, bridge)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"vias": [{"key": [5, 5], "new": [6, 6]}]}))
    await tools["pcb_apply_geometry_map"](str(mp), board_path="b", repour=False)
    assert bridge.sent[-1][1]["repour"] == "false"
    await tools["pcb_apply_geometry_map"](str(mp), board_path="b")
    assert "repour" not in bridge.sent[-1][1]
    body = _routine("PCB_ApplyFreePrimitives")
    guard = body[body.index("If DoRepour Then"):]
    assert guard.index("Poly.Rebuild;") < guard.index("Poly.EndModify;")
    assert "DoRepour := LowerCase(ExtractJsonValue(Params, 'repour')) <> 'false';" in body


@pytest.mark.asyncio
async def test_repour_takes_a_selection_and_a_longer_wait(monkeypatch):
    import eda_agent.tools.pcb as pcb

    sent = {}

    class B:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.update(command=command, params=params, timeout=timeout)
            return {"items": [{"name": "SITE_1"}]}

    tools = _tools(monkeypatch, B())
    out = await tools["pcb_repour_polygons"](names=["site_1", "missing"], indices=[3, 4],
                                             exclude=["BIG_HATCH"], timeout=90)
    assert sent["params"] == {"names": "site_1|missing", "indices": "3|4",
                              "exclude": "BIG_HATCH"}
    assert sent["timeout"] == 90 and out["not_found"] == ["missing"]
    out = await tools["pcb_repour_polygons"](names=["a|b"])
    assert out["repoured"] is False
    code = PCB.read_text(encoding="ascii")
    start = code.index("Function PCB_RepourPolygons(")
    body = code[start:code.index("\nEnd;", start)]
    # exclude wins, and an unnamed polygon is never matched by an empty list.
    assert body.index("If (ExclStr <> '') And (PolyName <> '') Then") > body.index(
        "If IdxStr <> '' Then")
    assert "If Want Then Polys.Add(Poly) Else Inc(Skipped);" in body
    # The board helper is defined before its first caller.
    assert code.index("Function GeoMapBoard(") < start
    del pcb


@pytest.mark.asyncio
async def test_the_repour_preference_reads_and_sets_by_name(monkeypatch):
    sent = {}

    class B:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.update(command=command, params=params)
            return {"before": 2, "after": 0}

    tools = _tools(monkeypatch, B())
    out = await tools["pcb_polygon_repour_mode"]("Never")
    assert sent == {"command": "pcb.polygon_repour_mode", "params": {"mode": "never"}}
    assert (out["mode_before"], out["mode"]) == ("always", "never")
    out = await tools["pcb_polygon_repour_mode"]("sometimes")
    assert out["success"] is False
    body = _routine("PCB_PolygonRepourMode")
    # Ordinals through an Integer, as PCB_RepourPolygons holds the setting;
    # no enum name this binding has not been seen to declare.
    assert "eNeverRepour :=" not in body and "eThresholdRepour" not in body.split("Begin", 1)[1]
    assert "Before := PCBServer.SystemOptions.PolygonRepour;" in body
    # MEASURED: with no PcbDoc open the PCB server is not loaded and
    # SystemOptions halted the script. A board is found first.
    assert body.index("If GetPCBBoardAnywhere(0) = Nil Then") < body.index(
        "PCBServer.SystemOptions")
    assert "If Value >= 0 Then PCBServer.SystemOptions.PolygonRepour := Value;" in body


@pytest.mark.asyncio
async def test_save_copy_keeps_the_overwritten_target(tmp_path, monkeypatch):
    target = tmp_path / "S.PcbDoc"
    target.write_text("old size", encoding="ascii")
    bridge = _Bridge(tmp_path, target_exists=True)
    tools = _tools(monkeypatch, bridge)
    out = await tools["pcb_save_copy_as"](str(target), source_path="C:/M.PcbDoc")
    assert out["written"] and Path(out["backup"]).read_text() == "old size"
    assert target.read_text() == "copy"
    assert all(p.get("board_path") == "C:/M.PcbDoc" for _, p in bridge.sent)
