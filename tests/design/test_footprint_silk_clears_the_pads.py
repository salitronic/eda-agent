# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Standard footprints keep their silk off the pads and mark pin 1.

#302: a TDFN12 2x3 mm (ST's land pattern) came out with the body outline
drawn across all twelve pads, no pin 1 mark, and a courtyard on a layer
the renderer did not take for one.
"""
from __future__ import annotations

import pytest

from eda_agent.design.footprint_gen import SILK_CLEARANCE, generate_footprint

TDFN12 = dict(pitch=19.685, pad_w=27.559, pad_h=9.843, row_span=62.992,
              body_w=78.74, body_h=118.11)


def _gap(track, pad):
    """Edge-to-edge distance from a track's ink to a pad's copper."""
    w = track["width"] / 2
    tx1, tx2 = sorted((track["x1"], track["x2"]))
    ty1, ty2 = sorted((track["y1"], track["y2"]))
    px1 = pad["x"] - pad["x_size"] / 2
    px2 = pad["x"] + pad["x_size"] / 2
    py1 = pad["y"] - pad["y_size"] / 2
    py2 = pad["y"] + pad["y_size"] / 2
    dx = max(px1 - (tx2 + w), (tx1 - w) - px2, 0)
    dy = max(py1 - (ty2 + w), (ty1 - w) - py2, 0)
    return (dx * dx + dy * dy) ** 0.5


@pytest.mark.parametrize("family,n,kw", [
    ("dual", 12, TDFN12),
    ("dual", 8, dict(pitch=50, pad_w=60, pad_h=24, row_span=210, body_w=154,
                     body_h=193)),
    ("quad", 32, dict(pitch=19.685, pad_w=11, pad_h=30, row_span=180,
                      body_w=197, body_h=197)),
    ("header", 10, dict(pitch=100, pad_w=60, pad_h=60, row_span=100,
                        hole=40, body_w=600, body_h=200)),
    ("sip", 4, dict(pitch=100, pad_w=60, pad_h=60, hole=40)),
    ("chip", 2, dict(pitch=60, pad_w=30, pad_h=40)),
])
def test_no_ink_comes_near_a_pad(family, n, kw):
    g = generate_footprint(family, n, **kw)
    for t in g.silk_tracks:
        for p in g.pads:
            assert _gap(t, p) >= SILK_CLEARANCE - 1e-6, (t, p)


def test_the_tdfn_keeps_its_ends_and_gets_a_pin1_line():
    g = generate_footprint("dual", 12, **TDFN12)
    ys = sorted({round(t["y1"], 3) for t in g.silk_tracks if t["y1"] == t["y2"]})
    assert len(ys) == 3, "top and bottom outline, and the pin 1 line"
    pin1 = next(p for p in g.pads if p["designator"] == "1")
    line = max(g.silk_tracks, key=lambda t: t["y1"])
    assert line["y1"] > 118.11 / 2, "beyond the body, never under it"
    assert min(line["x1"], line["x2"]) <= pin1["x"] <= max(line["x1"], line["x2"])


def test_the_courtyard_holds_everything():
    g = generate_footprint("dual", 12, courtyard=10, **TDFN12)
    cx = max(max(t["x1"], t["x2"]) for t in g.courtyard_tracks)
    cy = max(max(t["y1"], t["y2"]) for t in g.courtyard_tracks)
    for t in g.silk_tracks:
        assert max(abs(t["x1"]), abs(t["x2"])) + 3 <= cx - 10 + 1e-6
        assert max(abs(t["y1"]), abs(t["y2"])) + 3 <= cy - 10 + 1e-6


def test_a_sot23_3_and_a_chip_carry_no_pin1_mark():
    sot = generate_footprint("dual", 3, pitch=37.4, pad_w=40, pad_h=24,
                             row_span=90, body_w=55, body_h=115)
    assert all(t["y1"] <= 115 / 2 + 3 + 1e-6 for t in sot.silk_tracks)


def test_a_quad_leaves_its_pin1_corner_open():
    g = generate_footprint("quad", 32, pitch=19.685, pad_w=11, pad_h=30,
                           row_span=180, body_w=197, body_h=197)
    h = 197 / 2 + 3
    ends = [(t["x1"], t["y1"]) for t in g.silk_tracks] + \
           [(t["x2"], t["y2"]) for t in g.silk_tracks]
    assert not any(abs(x + h) < 1e-6 and abs(y - h) < 1e-6 for x, y in ends)
    for corner in ((h, h), (h, -h), (-h, -h)):
        assert any(abs(x - corner[0]) < 1e-6 and abs(y - corner[1]) < 1e-6
                   for x, y in ends)


def test_a_bga_marks_three_corners():
    g = generate_footprint("bga", 0, rows=4, cols=4, pitch=31.5, pad_w=16,
                           body_w=200, body_h=200)
    assert len(g.silk_tracks) == 6
