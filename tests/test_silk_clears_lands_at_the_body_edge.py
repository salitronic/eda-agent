# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Silkscreen and courtyard around lands that reach or cross the body edge.

In one take the BGT60LTR11AIP footprint came out with no silkscreen at
all: its balls sit so near the body edge all round that every side of the
outline was cut away. The TPSM63603 silk ran over its stepped corner
lands, drawn as copper outlines that the clipping did not know about.
The SIM7080G lands, meant to straddle the body edge, were built inside
it, and the comparison image showed no body to judge that by.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.check import read_package
from eda_agent.design.landpattern.drafting import DEFAULT_RULES


def _ring(body=3.3, pitch=0.5, n=6, inset=0.275, land=0.25):
    """Balls in a ring, ``inset`` from the body edge to their centres."""
    half = body / 2 - inset
    pads, k = [], 0
    for i in range(n):
        t = -half + i * (2 * half) / (n - 1)
        for x, y in ((t, half), (t, -half), (half, t), (-half, t)):
            k += 1
            pads.append({"number": str(k), "x": round(x, 4), "y": round(y, 4),
                         "width": land, "height": land, "shape": "circle"})
    seen, out = set(), []
    for p in pads:
        if (p["x"], p["y"]) not in seen:
            seen.add((p["x"], p["y"]))
            out.append(p)
    return {"family": "custom", "body_x": body, "body_y": body, "height": 0.6,
            "custom_pads": out}


def _silk(model):
    return [g for g in model.graphics if g.layer == "silk" and g.kind == "line"]


def _clear_of(lines, boxes, gap):
    for g in lines:
        for x0, y0, x1, y1 in boxes:
            for t in (i / 20 for i in range(21)):
                x, y = g.x1 + (g.x2 - g.x1) * t, g.y1 + (g.y2 - g.y1) * t
                dx = max(x0 - x, 0, x - x1)
                dy = max(y0 - y, 0, y - y1)
                if (dx * dx + dy * dy) ** 0.5 < gap - 1e-6:
                    return False
    return True


def test_balls_at_the_edge_all_round_get_an_outline_outside_them():
    model = build_footprint(read_package(_ring()))
    silk = _silk(model)
    assert len(silk) == 4  # a whole rectangle, not nothing
    gap = DEFAULT_RULES.silk_clearance + DEFAULT_RULES.silk_width / 2
    assert _clear_of(silk, [p.bbox() for p in model.pads], gap)
    xs = [v for g in silk for v in (g.x1, g.x2)]
    assert max(xs) > 3.3 / 2  # moved out past the body edge
    c = model.courtyard.points
    assert max(x for x, _ in c) >= max(xs) + DEFAULT_RULES.silk_width / 2


def test_lands_well_inside_keep_the_outline_on_the_body():
    model = build_footprint(read_package(_ring(inset=0.9)))
    xs = [v for g in _silk(model) for v in (g.x1, g.x2)]
    assert xs and max(xs) == pytest.approx(3.3 / 2)


def test_silk_and_courtyard_clear_a_land_drawn_as_an_outline():
    # a corner land: a narrow pad with a foot stepping out past the body
    foot = [[-1.375, 2.25], [-1.375, 3.2], [-1.625, 3.2], [-1.625, 2.875],
            [-1.875, 2.875], [-1.875, 2.55], [-2.3, 2.55], [-2.3, 2.3],
            [-1.875, 2.3], [-1.875, 2.25]]
    pkg = {"family": "custom", "body_x": 4.0, "body_y": 6.0, "height": 1.9,
           "custom_pads": [
               {"number": "1", "x": -1.5, "y": 2.725, "width": 0.25, "height": 0.95,
                "outline": foot},
               {"number": "2", "x": 1.5, "y": -2.725, "width": 0.25, "height": 0.95}]}
    model = build_footprint(read_package(pkg))
    gap = DEFAULT_RULES.silk_clearance + DEFAULT_RULES.silk_width / 2
    box = (min(x for x, _ in foot), min(y for _, y in foot),
           max(x for x, _ in foot), max(y for _, y in foot))
    assert _clear_of(_silk(model), [box], gap)
    c = model.courtyard.points
    assert min(x for x, _ in c) < -2.3 and max(y for _, y in c) > 3.2


def test_the_comparison_draws_the_body_over_the_lands():
    pytest.importorskip("PIL")
    from eda_agent.design.landpattern.compare_figure import draw_lands

    class _Land:
        number, x, y, width, height, rotation, shape = "1", 0.0, 0.0, 1.0, 1.0, 0, "rect"

    def blue(img):
        return {c: n for n, c in img.getcolors(1 << 20)}.get((30, 90, 200), 0)

    plain = draw_lands([_Land()], 400, width_px=400)
    framed = draw_lands([_Land()], 400, width_px=400, body=(-2, -2, 2, 2))
    assert blue(plain) == 0 and blue(framed) > 100
