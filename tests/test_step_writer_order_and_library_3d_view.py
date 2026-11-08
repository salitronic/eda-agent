# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The STEP writer can be put first by name, and the 3D view switch serves
a footprint library as well as a board."""
from __future__ import annotations

from pathlib import Path

from eda_agent.design import models3d
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _both(monkeypatch):
    monkeypatch.setattr(models3d, "_has_cadquery", lambda: True)
    monkeypatch.setattr(models3d, "_freecadcmd", lambda: "freecadcmd")


def test_cadquery_is_tried_first_by_default(monkeypatch):
    _both(monkeypatch)
    monkeypatch.delenv(models3d.STEP_WRITER_ENV, raising=False)
    assert models3d.available_writers() == ["cadquery", "freecad"]


def test_the_setting_puts_freecad_first_and_keeps_the_fallback(monkeypatch):
    _both(monkeypatch)
    monkeypatch.setenv(models3d.STEP_WRITER_ENV, "FreeCAD")
    assert models3d.available_writers() == ["freecad", "cadquery"]


def test_a_writer_the_machine_lacks_is_not_added(monkeypatch):
    monkeypatch.setattr(models3d, "_has_cadquery", lambda: True)
    monkeypatch.setattr(models3d, "_freecadcmd", lambda: None)
    monkeypatch.setenv(models3d.STEP_WRITER_ENV, "freecad")
    assert models3d.available_writers() == ["cadquery"]


def test_a_focused_pcblib_is_switched_without_a_board():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["Gen_SwitchView"]
    lib = body.index("If FocusedKind <> 'PCBLIB' Then")
    assert lib < body.index("GetPCBBoardAnywhere(0)")
    assert lib < body.index("RunProcess('PCB:SwitchTo3D')")


def test_the_camera_presets_are_the_ones_altium_takes():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["Gen_SwitchView"]
    presets = load(_ALTIUM / "Generic.pas", minimum=20)["CameraPreset"]
    for to in ("'Top'", "'RotateOrtho'", "'Bottom'", "'Front'", "'Back'", "'Left'", "'Right'"):
        assert f"Result := {to}" in presets
    assert body.index("RunProcess('PCB:SwitchTo3D')") < body.index("TurnCamera(")


def test_a_named_footprint_is_made_current_before_the_switch():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["Gen_SwitchView"]
    made = body.index("PcbLib.SetState_CurrentComponent(Footprint)")
    assert made < body.index("RunProcess('PCB:SwitchTo3D')")
    assert "'FOOTPRINT_NOT_FOUND'" in body


def test_views_are_checked_whole_before_the_camera_moves():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["Gen_SwitchView"]
    assert body.index("'views lists cameras") < body.index("RunProcess('PCB:SwitchTo3D')")


def test_the_3d_fit_is_taken_from_above_and_steps_out():
    body = load(_ALTIUM / "Generic.pas", minimum=20)["Gen_SwitchView"]
    fit = body[body.index("If Fit Then"):]
    top = fit.index("AddStringParameter('To', 'Top')")
    zoom_all = fit.index("AddStringParameter('Action', 'All')")
    out = fit.index("AddStringParameter('Action', 'Out')")
    assert top < zoom_all < out
    # No redraw between the steps: each one drawn put a black frame on
    # screen for a large module (the LTM8060), the camera inside the body.
    assert "SettleView" not in fit[top:out]
    assert "SettleView" in fit[out:fit.index("End;", out) + 40]
    # The 3D view is drawn once before the fit: fitted before it existed,
    # the zoom kept the last part's close camera.
    switch = body[:body.index("If Fit Then")]
    assert switch.rstrip().endswith("SettleView(0);")
