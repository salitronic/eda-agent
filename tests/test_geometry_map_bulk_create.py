# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_apply_geometry_map creates copper in bulk from one file.

Asked for to lay out a 6-layer board from ~2800 tracks, ~1300 vias, ~360
regions and 45 pours without sending them inline: tracks and arcs take a
net, vias and polygon pours are new kinds, and a refused create is named
by its entry in the map.
"""
from __future__ import annotations

from pathlib import Path

from eda_agent.geometry_map import build_edits, parse_dump
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
C = 10000  # internal units per mil


def _creates(entries):
    lines, summary = build_edits({"create": entries}, parse_dump(""), 0.01, 1e-4)
    return [l for l in lines if l.startswith("C\t")], summary["create"]


def test_a_track_carries_its_net_last():
    lines, rep = _creates([{"kind": "track", "layer": "TopLayer", "width": 10,
                            "geom": [0, 0, 100, 0], "net": "GND"}])
    assert lines == [f"C\tT\tTopLayer\t{10*C}\t0\t0\t{100*C}\t0\t0\tGND"]
    assert rep["matched"] == 1


def test_a_track_with_no_net_ends_in_an_empty_field():
    lines, _ = _creates([{"kind": "track", "layer": "TopLayer", "width": 10,
                          "geom": [0, 0, 100, 0]}])
    assert lines[0].endswith("\t0\t")


def test_a_via_is_a_through_via_line():
    lines, _ = _creates([{"kind": "via", "x": 50, "y": 60, "size": 24, "hole": 12,
                          "net": "VCC"}])
    assert lines == [f"C\tV\tMultiLayer\t{24*C}\t{50*C}\t{60*C}\t{12*C}\tVCC"]


def test_a_via_whose_hole_is_not_smaller_is_refused():
    lines, rep = _creates([{"kind": "via", "x": 0, "y": 0, "size": 10, "hole": 12}])
    assert lines == [] and rep["refused"] == 1


def test_a_polygon_drops_a_repeated_closing_vertex_and_carries_order_name_net():
    lines, _ = _creates([{"kind": "polygon", "layer": "MidLayer1", "net": "GND",
                          "name": "GND_L2", "order": 3,
                          "contour": [[0, 0], [100, 0], [100, 100], [0, 0]]}])
    f = lines[0].split("\t")
    assert f[:4] == ["C", "P", "MidLayer1", "3"]
    assert f[-4:] == ["3", "1", "GND_L2", "GND"]


def test_refusals_map_back_to_their_entries():
    lines, rep = _creates([
        {"kind": "via", "x": 0, "y": 0, "size": 10, "hole": 12},        # refused here
        {"kind": "track", "layer": "TopLayer", "width": 5, "geom": [0, 0, 1, 0]},
        {"kind": "via", "x": 0, "y": 0, "size": 24, "hole": 12, "net": "NOPE"},
    ])
    assert rep["line_entries"] == [1, 2], "the n-th C line is this entry"


def _apply():
    return load(_ALTIUM / "PCB.pas")["PCB_ApplyFreePrimitives"]


def test_an_unknown_net_creates_nothing_and_is_listed():
    body = _apply()
    for kind in ("'T'", "'V'", "'P'", "'R'"):
        assert f"Hdr = {kind}" in body
    # Every net check comes before the factory call of its kind.
    for net_var, factory in (("A5", "eViaObject"), ("A4", "ePolyObject"),
                             ("A4", "eRegionObject")):
        check = body.index(f"RNet := FindNetByName(Board, {net_var});",
                           body.index(f"Hdr = 'V'") if factory == "eViaObject" else 0)
        assert check < body.index(f"PCBObjectFactory({factory}")
    assert body.count("NoteCreateRefused(Refused, NRefused, CIdx, 'no net '") >= 3


def test_a_track_net_is_read_from_the_right_field():
    body = _apply()
    i = body.index("If (Hdr = 'T') Or (Hdr = 'A') Then")
    chunk = body[i:i + 400]
    assert "For K := 1 To 5 Do SplitNextTab(A5);" in chunk
    assert "If Hdr = 'A' Then SplitNextTab(A5);" in chunk


def test_a_pour_is_closed_and_its_order_read_back():
    body = _apply()
    p = body[body.index("If Hdr = 'P' Then"):]
    assert "ClosePolySegments(Poly, N);" in p
    assert "If Poly.PourIndex <> StrToIntDef(A6, -1) Then Inc(PourOrderOff);" in p
    assert "Poly.Rebuild" not in p[:p.index("If Hdr = 'R' Then")], "not poured here"


def test_the_reply_counts_by_kind_and_lists_refusals():
    body = _apply()
    for field in ('"created_by_kind":', '"create_refused_count":', '"create_refused":[',
                  '"pour_order_off":'):
        assert field in body
