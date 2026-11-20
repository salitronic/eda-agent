# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A slot is drawn the way Altium places it, in both renders.

Altium keeps two rotations on a slotted pad: the pad's own, which turns
copper and drill together, and HoleRotation, which turns the drill inside
the pad. The slot runs at their sum. HoleSize is the slot's width (the
drill) and HoleWidth its overall length.

The footprint render (lib_render_footprint) dropped the hole rotation, so
a vertical slot in an upright pad was drawn lying down. The board render
(pcb_render_svg) used the hole rotation alone and swapped the slot's width
and length, so it was right only where the two mistakes cancelled, and its
corner radius turned the slot into an ellipse. compare_live could not tell
a slot that runs across its pad from one that runs along it.

The pad replies are in the shape lib_get_pad_geometry and
generic.get_pcb_geometry return.
"""
from __future__ import annotations

import re

import pytest

from eda_agent.design.landpattern.live import compare_live, model_from_live
from eda_agent.design.landpattern.svg import _STYLE, render_svg
from eda_agent.render.pcb_svg import PcbRenderOptions, render_pcb_svg

DRILL, LENGTH = 0.6, 1.4

#: pad rotation, hole rotation, the way the slot really runs.
CASES = [(0, 0, 0), (0, 90, 90), (90, 0, 90), (90, 90, 0),
         (180, 90, 90), (270, 270, 0), (45, 0, 45), (30, 60, 90)]


def _lib_pad(rotation, hole_rotation, name="1"):
    return {"name": name, "x_mm": 0, "y_mm": 0, "copper_x_mm": 0,
            "copper_y_mm": 0, "w_mm": 2.0, "h_mm": 1.2,
            "shape": "roundrectangle", "corner_pct": 50,
            "rotation": rotation, "hole_rotation": hole_rotation,
            "hole_mm": DRILL, "hole_w_mm": LENGTH, "hole_type": "slot",
            "plated": True, "paste_top_enabled": False,
            "paste_expansion_mm": 0, "mask_expansion_mm": 0}


def _model(rotation, hole_rotation):
    return model_from_live({"name": "SLOT", "pads": [
        _lib_pad(rotation, hole_rotation)]}, {"name": "SLOT"}, [])


def _attr(element, name):
    m = re.search(rf'\b{name}="([-\d.e]+)"', element)
    return float(m.group(1)) if m else None


def _turn(element):
    m = re.search(r'rotate\(([-\d.e]+)', element)
    return float(m.group(1)) if m else 0.0


def _runs(element, turn_sign=1.0):
    """The direction a drawn slot's long side runs, 0 to 180 degrees.

    The footprint render flips Y and so turns by minus the angle; the
    board render draws inside its own flip and turns by the angle.
    """
    along = 0.0 if _attr(element, "width") >= _attr(element, "height") else 90.0
    return (along + turn_sign * _turn(element)) % 180.0


def _same_way(a, b):
    return abs(((a - b) + 90) % 180 - 90) < 1e-6


def _footprint_slot(svg):
    holes = re.search(r'<g id="holes">(.*?)</g>', svg, re.S)
    assert holes, svg
    found = re.findall(r"<rect [^>]*/>", holes.group(1))
    assert len(found) == 1, svg
    assert f'fill="{_STYLE["hole"]}"' in found[0]
    return found[0]


def _board_slot(rotation, hole_rotation):
    geometry = {
        "bbox": {"x1": -200, "y1": -200, "x2": 200, "y2": 200},
        "pads": [{"x": 0, "y": 0, "x_size": 80, "y_size": 48,
                  "shape": "RoundedRect", "hole_size": DRILL / 0.0254,
                  "hole_type": "Slot", "hole_width": LENGTH / 0.0254,
                  "hole_rotation": hole_rotation, "plated": True,
                  "rotation": rotation, "layer": "MultiLayer",
                  "name": "1"}],
    }
    svg = render_pcb_svg(geometry, PcbRenderOptions(show_drills=True))
    found = re.findall(r'<rect class="drill"[^>]*/>', svg)
    assert len(found) == 1, svg
    return found[0]


def test_the_live_model_keeps_the_hole_rotation():
    pad = _model(0, 90).pads[0]
    assert pad.hole_rotation == 90
    assert (pad.hole, pad.hole_length) == (DRILL, LENGTH)


@pytest.mark.parametrize("rotation,hole_rotation,runs", CASES)
def test_the_footprint_render_turns_the_slot_by_both_rotations(
        rotation, hole_rotation, runs):
    slot = _footprint_slot(render_svg(_model(rotation, hole_rotation)))
    assert _same_way(_runs(slot, turn_sign=-1.0), runs), slot


def test_the_reported_case_is_drawn_upright():
    """Pad 0, hole 90: a USB-C shell tab, drawn lying down before."""
    slot = _footprint_slot(render_svg(_model(0, 90)))
    assert _same_way(_runs(slot, turn_sign=-1.0), 90), slot


@pytest.mark.parametrize("rotation,hole_rotation,runs", CASES)
def test_the_board_render_turns_the_slot_by_both_rotations(
        rotation, hole_rotation, runs):
    slot = _board_slot(rotation, hole_rotation)
    assert _same_way(_runs(slot), runs), slot


def test_the_board_slot_is_its_length_long_and_its_drill_wide():
    slot = _board_slot(0, 0)
    long_side = max(_attr(slot, "width"), _attr(slot, "height"))
    short_side = min(_attr(slot, "width"), _attr(slot, "height"))
    assert long_side == pytest.approx(LENGTH / 0.0254)
    assert short_side == pytest.approx(DRILL / 0.0254)


def test_the_board_slot_has_round_ends_not_an_elliptical_outline():
    slot = _board_slot(0, 0)
    assert _attr(slot, "rx") == pytest.approx(DRILL / 0.0254 / 2)
    assert _attr(slot, "ry") == pytest.approx(DRILL / 0.0254 / 2)


def test_a_round_hole_is_still_a_circle():
    geometry = {"bbox": {"x1": -100, "y1": -100, "x2": 100, "y2": 100},
                "pads": [{"x": 0, "y": 0, "x_size": 60, "y_size": 60,
                          "shape": "Round", "hole_size": 30,
                          "hole_type": "Round", "hole_width": 0,
                          "hole_rotation": 90, "rotation": 45,
                          "layer": "MultiLayer", "name": "1"}]}
    svg = render_pcb_svg(geometry, PcbRenderOptions(show_drills=True))
    assert re.search(r'<circle class="drill"[^>]*r="15', svg)


def test_compare_finds_a_slot_that_runs_across_its_pad():
    diff = compare_live(_model(0, 0), _model(0, 90))
    assert not diff["same"]
    assert diff["pads_differing"] == [{"pad": "1", "slot_angle": [0.0, 90.0]}]


def test_compare_takes_a_half_turn_as_the_same_slot():
    assert compare_live(_model(0, 0), _model(0, 180))["same"]


def test_compare_names_the_pad_rotation_but_not_the_slot_when_both_agree():
    """Pad 90 / hole 0 and pad 0 / hole 90 both run upright."""
    pads = compare_live(_model(90, 0), _model(0, 90))["pads_differing"]
    assert pads == [{"pad": "1", "rotation": [90.0, 0.0]}]
