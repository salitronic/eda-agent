# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A menu click whose point lands on the wrong item tries the point in the
window's other coordinate space, and takes it only where the item is.

Altium is system DPI aware. With monitors scaled differently from the main
one, a position it reports can be in its own scaled coordinates, and the
pointer then landed on the desktop instead of the View menu.
"""
from __future__ import annotations

from eda_agent.ui import menu


class _User32:
    def __init__(self, scale):
        self.scale = scale

    def LogicalToPhysicalPointForPerMonitorDPI(self, hwnd, ref):  # noqa: N802
        p = ref._obj
        p.x, p.y = int(p.x * self.scale), int(p.y * self.scale)
        return 1

    def PhysicalToLogicalPointForPerMonitorDPI(self, hwnd, ref):  # noqa: N802
        p = ref._obj
        p.x, p.y = int(p.x / self.scale), int(p.y / self.scale)
        return 1


def _names(where):
    return lambda x, y: where.get((x, y), "Desktop")


def test_the_point_that_finds_the_item_is_taken(monkeypatch):
    monkeypatch.setattr(menu, "_u", lambda: _User32(0.8))
    monkeypatch.setattr(menu, "_name_at", _names({(80, 40): "View"}))
    assert menu._rescaled_point(1, 100, 50, "View") == (80, 40)


def test_the_other_direction_is_tried_too(monkeypatch):
    monkeypatch.setattr(menu, "_u", lambda: _User32(0.8))
    monkeypatch.setattr(menu, "_name_at", _names({(125, 62): "&View"}))
    assert menu._rescaled_point(1, 100, 50, "View") == (125, 62)


def test_no_point_with_the_item_refuses(monkeypatch):
    monkeypatch.setattr(menu, "_u", lambda: _User32(0.8))
    monkeypatch.setattr(menu, "_name_at", _names({}))
    assert menu._rescaled_point(1, 100, 50, "View") is None


def test_an_unscaled_window_changes_nothing(monkeypatch):
    monkeypatch.setattr(menu, "_u", lambda: _User32(1.0))
    seen = []
    monkeypatch.setattr(menu, "_name_at", lambda x, y: seen.append((x, y)) or "View")
    assert menu._rescaled_point(1, 100, 50, "View") is None
    assert seen == []
