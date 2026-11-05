# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A part turned off the board axes keeps its courtyard's real shape.

Measured on a demo board: an LQFP-48 placed at 45 degrees had its
courtyard read as the box its lines cover on the board axes, a square
of twice the area, and the placement audit reported it overlapping ten
neighbours, sitting on a mounting hole and leaving the board.
"""
from __future__ import annotations

import math

import pytest

from eda_agent.layout.read_altium import _courtyard_polygon

_C = 10000.0  # raw units per mil


def _area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
                   for i in range(len(p)))) / 2


def _rect_tracks(cx, cy, w, h, deg):
    a = math.radians(deg)
    pts = [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
           for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    return [{"t": [pts[i][0] * _C, pts[i][1] * _C,
                   pts[(i + 1) % 4][0] * _C, pts[(i + 1) % 4][1] * _C, 0]} for i in range(4)]


@pytest.mark.parametrize("deg", [0, 30, 45, 90, 135, 200, 315])
def test_a_rectangle_keeps_its_area_at_any_rotation(deg):
    poly = _courtyard_polygon(_rect_tracks(1000, 2000, 400, 200, deg), 1000, 2000, deg)
    assert _area(poly) == pytest.approx(400 * 200, rel=1e-6)


def test_the_corners_land_where_the_lines_are():
    poly = _courtyard_polygon(_rect_tracks(0, 0, 400, 200, 45), 0, 0, 45)
    far = max(math.hypot(x, y) for x, y in poly)
    assert far == pytest.approx(math.hypot(200, 100), rel=1e-6)


def test_without_the_rotation_it_is_the_old_box():
    prims = _rect_tracks(0, 0, 400, 200, 45)
    assert _area(_courtyard_polygon(prims)) > 400 * 200 * 1.5
