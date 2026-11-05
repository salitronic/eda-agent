# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Moving, turning and flipping a component carries its whole footprint."""

from __future__ import annotations

import pytest

from eda_agent.layout.model import (Arc, Component, LayoutBoard, Pad, PadCopper,
                                    Track)
from eda_agent.layout.place import set_pose


def _board():
    b = LayoutBoard(name="t")
    b.components = [Component("U1", x=100, y=100, courtyard=[(90, 95), (120, 95), (120, 105), (90, 105)])]
    b.pads = [Pad("U1", "1", 110, 100, rotation=0.0, net="A",
                  copper=[PadCopper("TopLayer", "rect", 6, 2, offset_x=1.0, offset_y=0.5)]),
              Pad("U1", "2", 100, 100, net="B", copper=[PadCopper("TopLayer", "round", 4, 4)]),
              Pad("R9", "1", 500, 500, net="C", copper=[PadCopper("TopLayer", "rect", 4, 4)])]
    b.tracks = [Track("TopLayer", 100, 100, 110, 100, 2.0, "", comp="U1")]
    b.arcs = [Arc("TopLayer", 100, 100, 5, 0, 90, 1.0, "", comp="U1")]
    return b


def _pad(b, name):
    return next(p for p in b.pads if p.comp == "U1" and p.name == name)


def test_a_move_carries_pads_courtyard_and_footprint_copper():
    b = _board()
    set_pose(b, "U1", x=300, y=50)
    assert (_pad(b, "1").x, _pad(b, "1").y) == pytest.approx((310, 50))
    assert b.component("U1").courtyard[0] == pytest.approx((290, 45))
    t = b.tracks[0]
    assert (t.x1, t.y1, t.x2, t.y2) == pytest.approx((300, 50, 310, 50))
    other = next(p for p in b.pads if p.comp == "R9")
    assert (other.x, other.y) == (500, 500), "another part's pad must not move"


def test_a_quarter_turn_turns_every_pad_about_the_origin():
    b = _board()
    set_pose(b, "U1", rotation=90.0)
    p = _pad(b, "1")
    assert (p.x, p.y) == pytest.approx((100, 110))
    assert p.rotation == pytest.approx(90.0)
    a = b.arcs[0]
    assert (a.a1, a.a2) == pytest.approx((90.0, 180.0))


def test_a_flip_mirrors_in_x_and_swaps_the_outer_copper_layer():
    b = _board()
    set_pose(b, "U1", side="bottom")
    p = _pad(b, "1")
    assert (p.x, p.y) == pytest.approx((90, 100))
    assert p.copper[0].layer == "BottomLayer"
    assert (p.copper[0].offset_x, p.copper[0].offset_y) == pytest.approx((1.0, -0.5))
    assert b.tracks[0].layer == "BottomLayer"
    assert b.component("U1").side == "bottom"


def test_flipping_twice_puts_everything_back():
    b = _board()
    before = b.to_dict()
    set_pose(b, "U1", side="bottom", rotation=30.0)
    set_pose(b, "U1", side="top", rotation=0.0)
    after = b.to_dict()
    for key in ("pads", "tracks", "arcs"):
        for x, y in zip(before[key], after[key]):
            for k in x:
                if isinstance(x[k], float):
                    assert y[k] == pytest.approx(x[k], abs=1e-9), (key, k)
                else:
                    assert y[k] == x[k] or k == "copper", (key, k)
    # Point by point: pytest 9.2 refuses approx on a list of points.
    was, now = before["components"][0]["courtyard"], after["components"][0]["courtyard"]
    assert len(now) == len(was)
    for p, q in zip(was, now):
        assert tuple(q) == pytest.approx(tuple(p))


def test_a_flipped_pad_shape_lands_where_its_mirror_image_is():
    b = _board()
    before = _pad(b, "1").shape_on("TopLayer")
    set_pose(b, "U1", side="bottom")
    after = _pad(b, "1").shape_on("BottomLayer")
    mirrored = sorted((round(200 - x, 6), round(y, 6)) for x, y in before.pts)
    assert sorted((round(x, 6), round(y, 6)) for x, y in after.pts) == mirrored
