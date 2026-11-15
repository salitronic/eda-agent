# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A board read spends its time on the script's own string work and on
walking the board, not on Altium's calls: measured on a board of 10,000
copper objects, adding each pad's text to one growing string took 7 s of
the pads read's 8, and escaping four names per track took 7.7 s. These
pin the shapes that avoid it, and the Python side's index for the
foreign-copper test that scanned every via, pad and track once for each of
12,700 poured pieces.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from eda_agent.layout import geom
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Region, Track, Via
from eda_agent.layout.read_altium import _CopperPoints
from tests.pascal_source import functions, strip_comments

_PCB = Path(__file__).resolve().parents[2] / "scripts" / "altium" / "PCB.pas"


def _section(name):
    return functions(strip_comments(_PCB.read_text(encoding="utf-8")))[name]


def test_sections_join_their_entries_in_pieces():
    for name in ("LmPadsSection", "LmComponentsSection", "LmCopperSection"):
        body = _section(name)
        assert "Items := LmJoinPieces(Pieces);" in body, name
        assert not re.search(r"Items := Items \+", body), name
    join = _section("LmJoinPieces")
    assert "Nxt.Add(Cur[I] + ',' + Cur[I + 1])" in join


def test_the_text_after_a_copper_shape_is_built_once_per_key():
    body = _section("LmCopperSection")
    look = body.index("K := CommonKeys.IndexOf(CKey);")
    exact = body.index("If CommonKeys[K] <> CKey Then K := -1;")
    escape = body.index("EscapeJsonString(NetName)")
    assert look < exact < escape < body.index("CommonKeys.Add(CKey);")
    assert "Format('{\"k\":\"track\",\"v\":[%d,%d,%d,%d,%d]%s'" in body


def _brute(board, region, net):
    body = region.shape()
    x0, y0, x1, y1 = body.bbox
    inside = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1 and geom.point_in_poly(x, y, body)  # noqa: E731
    return (any(v.net != net and region.layer in board.layers_between(v.low_layer, v.high_layer)
                and inside(v.x, v.y) for v in board.vias)
            or any(p.net != net and (region.layer in p.layers() or (p.hole > 0 and p.plated))
                   and inside(p.x, p.y) for p in board.pads)
            or any(t.net != net and t.layer == region.layer and not t.keepout
                   and inside((t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2) for t in board.tracks))


def test_the_foreign_copper_index_answers_as_a_scan_of_everything():
    rnd = random.Random(7)
    names = ["TopLayer", "MidLayer1", "BottomLayer"]
    b = LayoutBoard(outline=[(0, 0), (100, 0), (100, 100), (0, 100)])
    b.layers = [Layer(n, order=i) for i, n in enumerate(names)]
    nets = ["GND", "VCC", "SIG", ""]
    pick = lambda: (rnd.uniform(0, 100), rnd.uniform(0, 100))  # noqa: E731
    for _ in range(60):
        x, y = pick()
        b.vias.append(Via(x, y, 0.6, 0.3, rnd.choice(names), rnd.choice(names), rnd.choice(nets)))
    for i in range(60):
        x, y = pick()
        hole = rnd.choice([0.0, 0.0, 0.8])
        b.pads.append(Pad("U1", str(i), x, y, net=rnd.choice(nets), hole=hole,
                          plated=rnd.random() < 0.8,
                          copper=[PadCopper(rnd.choice(names), "rect", 1, 1)]))
    for _ in range(120):
        (x, y), (u, v) = pick(), pick()
        b.tracks.append(Track(rnd.choice(names), x, y, u, v, 0.2, rnd.choice(nets),
                              keepout=rnd.random() < 0.1))
    points = _CopperPoints(b)
    checked = 0
    for _ in range(200):
        x, y = pick()
        w, h = rnd.uniform(1, 40), rnd.uniform(1, 40)
        r = Region(rnd.choice(names), [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                   net="GND", kind="pour")
        for net in nets:
            assert points.foreign(r, net) == _brute(b, r, net)
            checked += 1
    assert checked == 800
