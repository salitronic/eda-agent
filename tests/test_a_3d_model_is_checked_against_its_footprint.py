# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which way round a 3D model sits on its footprint is checked, not looked at.

A linked STEP model gives its box and height. Whether its pin 1 is over
pad 1, or a module's antenna end is where the footprint has it, was found
by opening the 3D view. The model's terminals are flat faces on its
underside: placed as the body is placed, each lies over a pad or not.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design import model_fit as mf
from eda_agent.design.models3d import _freecadcmd


def _module():
    """A module 10 x 14 x 2 mm, origin at a corner: five terminals along
    each long side and one odd terminal near the bottom left, with 4 mm of
    body past the terminals at the top (an antenna)."""
    faces = [{"z": 0.0, "area": 140.0, "box": [0, 0, 10, 14]}]       # the body's own underside
    for i in range(5):
        y = 1.0 + 2.0 * i
        faces.append({"z": 0.0, "area": 0.8, "box": [0.2, y, 1.2, y + 0.8]})
        faces.append({"z": 0.0, "area": 0.8, "box": [8.8, y, 9.8, y + 0.8]})
    faces.append({"z": 0.0, "area": 1.0, "box": [3.0, 1.0, 4.0, 2.0]})
    faces.append({"z": 1.5, "area": 50.0, "box": [1, 1, 9, 9]})      # inside the part, not underside
    return mf.facts_from_faces([0, 0, 0, 10, 14, 2], faces, solids=1)


def _pads(facts, dx=0.0, dy=0.0):
    """The footprint as drawn: a pad under each terminal, origin moved."""
    small = [f for f in facts["underside"] if f["area"] < 5]
    return [{"designator": str(i + 1),
             "x": (f["box"][0] + f["box"][2]) / 2 + dx, "y": (f["box"][1] + f["box"][3]) / 2 + dy,
             "x_size": f["box"][2] - f["box"][0] + 0.2, "y_size": f["box"][3] - f["box"][1] + 0.2}
            for i, f in enumerate(small)]


def test_only_the_underside_is_read_and_the_body_is_not_a_terminal():
    facts = _module()
    assert len(facts["underside"]) == 12                # 11 terminals and the body; not the inner face
    out = mf.check_fit(facts, _pads(facts))
    assert out["ok"], out
    assert (out["pads"], out["terminals"]) == (11, 11)
    assert len(out["other_faces"]) == 1


def test_a_model_left_at_its_own_origin_is_told_the_move_that_fits():
    facts = _module()
    pads = _pads(facts, dx=-5.0, dy=-5.4)               # a footprint drawn round its centre
    out = mf.check_fit(facts, pads)
    assert not out["ok"]
    assert "offsets (-5, -5.4) mm" in out["reason"] and "0 degrees" in out["reason"]
    assert mf.check_fit(facts, pads, offset_x=-5.0, offset_y=-5.4)["ok"]


def test_a_model_turned_a_quarter_is_told_the_turn_that_fits():
    facts = _module()
    pads = _pads(facts)
    out = mf.check_fit(facts, pads, rotation=90)
    assert not out["ok"]
    assert "270 degrees" not in out["reason"] and "Turned to 0 degrees" in out["reason"]
    fits = [t for t in out["rotations"] if t["pads_with_terminal"] == 11]
    assert [t["rotation"] for t in fits] == [0]         # the odd terminal rules out 180


def test_pads_that_read_the_same_both_ways_are_said_to():
    facts = _module()
    facts["underside"] = [f for f in facts["underside"] if f["box"] != [3.0, 1.0, 4.0, 2.0]]
    pads = _pads(facts)
    out = mf.check_fit(facts, pads)
    assert out["ok"] and "180" in out["note"] and "courtyard" in out["note"]
    assert out["body_past_pads_mm"]["top"] > out["body_past_pads_mm"]["bottom"] + 3


def test_the_courtyard_tells_the_two_turns_apart():
    facts = _module()
    facts["underside"] = [f for f in facts["underside"] if f["box"] != [3.0, 1.0, 4.0, 2.0]]
    pads = _pads(facts)
    yard = [-0.25, -0.25, 10.25, 14.25]
    assert mf.check_fit(facts, pads, courtyard=yard)["ok"]
    # Turned half round about the middle of the pads: every pad still has a terminal.
    span_y = (min(p["y"] - p["y_size"] / 2 for p in pads) + max(p["y"] + p["y_size"] / 2 for p in pads))
    out = mf.check_fit(facts, pads, rotation=180, offset_x=10.0, offset_y=span_y, courtyard=yard)
    assert not out["pads_without_terminal"]
    assert not out["ok"]
    assert "bottom" in out["outside_courtyard_mm"] and "leaves the courtyard" in out["reason"]


def test_a_body_drawn_as_one_block_is_reported_not_passed():
    facts = mf.facts_from_faces([0, 0, 0, 10, 14, 2], [{"z": 0.0, "area": 140.0, "box": [0, 0, 10, 14]}])
    out = mf.check_fit(facts, _pads(_module()))
    assert not out["ok"] and "no flat terminal faces" in out["reason"]


def test_a_pad_turned_a_quarter_is_as_wide_as_it_was_tall():
    assert mf.pad_box({"x": 1, "y": 2, "x_size": 2.0, "y_size": 0.5, "rotation": 90}) == pytest.approx(
        [0.75, 1.0, 1.25, 3.0])


def test_bad_input_is_a_reason_not_a_stack_trace(tmp_path):
    assert "pads" in mf.check_fit(_module(), [])["reason"]
    assert "courtyard" in mf.check_fit(_module(), _pads(_module()), courtyard=[1, 1, 0, 0])["reason"]
    out = mf.fit_report(str(tmp_path / "missing.step"), _pads(_module()))
    assert not out["ok"] and "no STEP file" in out["reason"]


def test_the_picture_is_drawn(tmp_path):
    pytest.importorskip("matplotlib")
    facts = _module()
    pads = _pads(facts)
    out = mf.check_fit(facts, pads, courtyard=[-0.25, -0.25, 10.25, 14.25])
    png = mf.render_fit(out, pads, tmp_path / "fit.png", title="module")
    assert (tmp_path / "fit.png").stat().st_size > 5000 and png.endswith("fit.png")


def test_the_tool_is_registered_as_one_that_needs_no_editor():
    from eda_agent.tools import footprint as fp_tools
    from eda_agent.tools.metadata import _OFFLINE_BY_NAME
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    fp_tools.register_footprint_tools(registry)
    names = {t.name for t in asyncio.run(registry.list_tools())}
    assert "footprint_check_3d_model" in names and "footprint_check_3d_model" in _OFFLINE_BY_NAME


@pytest.mark.skipif(_freecadcmd() is None, reason="FreeCAD is not installed")
def test_a_real_step_file_is_read_through_freecad(tmp_path):
    """A box with two terminal pads under it, written and read by FreeCAD."""
    import subprocess

    script = tmp_path / "make.py"
    step = tmp_path / "part.step"
    script.write_text(
        "import sys, Part, FreeCAD\n"
        "V = FreeCAD.Vector\n"
        "body = Part.makeBox(10, 6, 2, V(0, 0, 0.1))\n"
        "a = Part.makeBox(1, 1, 0.1, V(1, 1, 0))\n"
        "b = Part.makeBox(1, 1, 0.1, V(8, 4, 0))\n"
        "Part.makeCompound([body, a, b]).exportStep(sys.argv[-1])\n", encoding="utf-8")
    subprocess.run([_freecadcmd(), str(script), str(step)], capture_output=True, timeout=180)
    assert step.is_file()
    facts = mf.step_facts(step)
    assert facts["box"][3:] == pytest.approx([10, 6, 2.1], abs=1e-3)
    assert sorted(f["box"] for f in facts["underside"]) == [pytest.approx([1, 1, 2, 2], abs=1e-3),
                                                            pytest.approx([8, 4, 9, 5], abs=1e-3)]
    pads = [{"designator": "1", "x": 1.5, "y": 1.5, "x_size": 1.2, "y_size": 1.2},
            {"designator": "2", "x": 8.5, "y": 4.5, "x_size": 1.2, "y_size": 1.2}]
    assert mf.fit_report(str(step), pads)["ok"]
    turned = mf.fit_report(str(step), pads, rotation=180, offset_x=10, offset_y=6)
    assert turned["ok"] and "180" not in turned.get("reason", "")   # two pads, point-symmetric


def test_pads_read_in_mils_reach_the_check_whole():
    """The session's unit is converted by name: a pad's sizes must convert
    with its position, or a footprint read in mils is checked with
    positions in millimetres and sizes forty times too large."""
    from eda_agent import lengths

    args = {"step_path": "m.step", "tolerance": 4.0, "offset_x": -100.0, "courtyard": [-200, -100, 200, 100],
            "pads": [{"designator": "1", "x": 100.0, "y": -50.0, "x_size": 40.0, "y_size": 20.0}]}
    got = lengths.args_in("footprint_check_3d_model", args, "mil", list(args))
    assert got["pads"][0] == pytest.approx({"designator": "1", "x": 2.54, "y": -1.27,
                                            "x_size": 1.016, "y_size": 0.508})
    assert got["tolerance"] == pytest.approx(0.1016) and got["offset_x"] == pytest.approx(-2.54)
    assert got["courtyard"] == pytest.approx([-5.08, -2.54, 5.08, 2.54])
    back = lengths.reply_in("footprint_check_3d_model", {"body_box_mm": [0, 0, 2.54, 2.54]}, "mil")
    assert back["body_box_mils"] == pytest.approx([0, 0, 100, 100])
