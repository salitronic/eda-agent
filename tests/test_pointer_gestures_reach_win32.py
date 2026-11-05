# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""drag, click_at and window_at get as far as their Win32 calls.

ui/windows.py used ``wintypes.POINT`` and ``byref`` without importing
either, so every pointer gesture raised NameError before it moved
anything and app_canvas could not click or drag at all. window_at
swallowed the same NameError and answered None, so every coordinate
check refused with "there is no window at that point".

user32 is replaced by a recorder here, so nothing reaches the real
pointer or any window.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from eda_agent.ui import windows as win


class _User32:
    """Records every call; answers like a 1920x1080 desktop."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if name == "GetSystemMetrics":
                return {win._SM_CXVIRTUALSCREEN: 1920,
                        win._SM_CYVIRTUALSCREEN: 1080}.get(args[0], 0)
            if name == "WindowFromPoint":
                return 4242
            return 1
        return call

    def names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def user32(monkeypatch):
    fake = _User32()
    monkeypatch.setattr(win, "ctypes", SimpleNamespace(
        windll=SimpleNamespace(user32=fake)))
    monkeypatch.setattr(win, "_require", lambda: None)
    monkeypatch.setattr(win, "time", SimpleNamespace(sleep=lambda s: None))
    monkeypatch.delenv(win.UI_AUTOMATION_ENV, raising=False)
    return fake


def test_drag_reaches_the_pointer_calls(user32):
    out = win.drag(10, 20, 110, 220, steps=4)
    assert out["ok"] is True
    names = user32.names()
    assert names[0] == "GetCursorPos"
    assert names.count("mouse_event") == 1 + 1 + 4 + 1   # move, down, 4 steps, up
    assert names[-1] == "SetCursorPos"


def test_click_at_reaches_the_pointer_calls(user32):
    out = win.click_at(50, 60, double=True)
    assert out["ok"] is True
    names = user32.names()
    assert names[0] == "GetCursorPos"
    assert names.count("mouse_event") == 1 + 4            # move, two clicks
    assert names[-1] == "SetCursorPos"


def test_window_at_answers_the_window_under_the_point(user32):
    assert win.window_at(5, 5) == 4242
    assert user32.names() == ["WindowFromPoint"]


def test_click_at_moves_across_the_whole_virtual_desktop(user32):
    win.click_at(50, 60)
    move = next(args for name, args in user32.calls if name == "mouse_event")
    # Without VIRTUALDESK the absolute range is the primary monitor only.
    assert move[0] & win._ME_VIRTUALDESK and move[0] & win._ME_ABSOLUTE


def test_to_absolute_subtracts_a_negative_virtual_origin():
    """Three 2560x1440 monitors, one left of the primary at X=-2560.

    The reported failure: a click at 4183 on the right-hand monitor
    landed on the primary, because the origin and VIRTUALDESK were
    both ignored.
    """
    desk = (-2560, 0, 7680, 1440)
    assert win._to_absolute(-2560, 0, desk) == (0, 0)
    assert win._to_absolute(5119, 1439, desk) == (65535, 65535)
    x, _ = win._to_absolute(4183, 117, desk)
    assert x == round((4183 + 2560) * 65535 / 7679)
    # The primary's left edge is a third of the way across, not zero.
    assert win._to_absolute(0, 0, desk)[0] == round(2560 * 65535 / 7679)
