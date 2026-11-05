# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The click fallback in ui/controls.py names the window it clicks in.

_click_node passed ``hwnd`` to the real click without having one, so the
last route of select_item, expand, set_cell and select_tab raised
NameError instead of clicking.
"""
from __future__ import annotations

from eda_agent.ui import controls


def test_the_click_reaches_the_window_it_was_given(monkeypatch):
    clicked = []
    monkeypatch.setattr(controls, "_node_rect", lambda node: (10, 20, 30, 40))
    monkeypatch.setattr(controls, "_real_click",
                        lambda hwnd, x, y: clicked.append((hwnd, x, y)))
    assert controls._click_node(object(), 1234) is True
    assert clicked == [(1234, 25, 40)]
