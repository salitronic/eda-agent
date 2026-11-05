# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Config tab scrolls with the wheel through the server, not the panel.

Altium never runs a script form's wheel events, so the panel holds no wheel
handler and the server's panel_wheel thread scrolls the box instead.
"""
from __future__ import annotations

from pathlib import Path

from eda_agent.ui import panel_wheel
from eda_agent.ui.panel_wheel import PIXELS_PER_NOTCH, wheel_position

ROOT = Path(__file__).resolve().parents[1]


def test_a_notch_moves_the_box_and_stays_in_range():
    assert wheel_position(0, -120, 174) == PIXELS_PER_NOTCH
    assert wheel_position(PIXELS_PER_NOTCH, 120, 174) == 0
    assert wheel_position(0, 120, 174) == 0            # already at the top
    assert wheel_position(170, -120, 174) == 174       # stops at the bottom
    assert wheel_position(0, -240, 174) == 2 * PIXELS_PER_NOTCH
    # a high-resolution wheel sends fractions of a notch
    assert wheel_position(0, -30, 174) == PIXELS_PER_NOTCH // 4


def test_the_server_starts_it_once_and_only_on_windows(monkeypatch):
    started = []
    monkeypatch.setattr(panel_wheel, "_started", False)
    monkeypatch.setattr(panel_wheel.threading, "Thread",
                        lambda **k: type("T", (), {"start": lambda s: started.append(k["name"])})())
    monkeypatch.setattr(panel_wheel.sys, "platform", "linux")
    assert panel_wheel.start() is False and started == []
    monkeypatch.setattr(panel_wheel.sys, "platform", "win32")
    assert panel_wheel.start() is True and panel_wheel.start() is False
    assert started == ["panel-wheel"]
    server = (ROOT / "src" / "eda_agent" / "server.py").read_text(encoding="utf-8")
    assert "panel_wheel.start()" in server


def test_the_panel_names_match_the_form():
    dfm = (ROOT / "scripts" / "altium" / "StatusForm.dfm").read_text(encoding="utf-8")
    assert f"Caption = '{panel_wheel.PANEL_TITLE}'" in dfm
    assert f"object scr_Config: {panel_wheel.SCROLL_CLASS}" in dfm
    # the form's own wheel events never run in Altium, so none are bound
    assert "OnMouseWheel" not in dfm
