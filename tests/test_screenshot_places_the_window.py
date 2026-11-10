# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A position read off a screenshot can be clicked where it was seen.

app_screenshot pictures the Altium window, so its pixels are window
pixels, while the click tools take screen points. With Altium on a
second monitor (origin 2552, -8) a right click at a Projects-panel row
read off the picture landed on another application (#292).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from eda_agent.tools import uiauto


def _tools(monkeypatch):
    tools = {}

    class Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(uiauto, "_altium_pid", lambda reads_only=False: (7, None))
    monkeypatch.setattr(uiauto.menu, "frame", lambda pid: SimpleNamespace(hwnd=99))
    monkeypatch.setattr(uiauto.windows, "window_rect",
                        lambda hwnd: (2552, -8, 5128, 1400))
    uiauto.register_uiauto_tools(Mcp())
    return tools


def test_window_pixels_are_moved_to_the_screen(monkeypatch):
    tools = _tools(monkeypatch)
    seen = []
    monkeypatch.setattr(uiauto.menu, "context_menu",
                        lambda pid, x, y: seen.append((x, y)) or {"ok": True})
    asyncio.run(tools["app_context_menu"](x=160, y=570, relative_to="window"))
    asyncio.run(tools["app_context_menu"](x=2712, y=562))
    assert seen == [(2712, 562), (2712, 562)]


def test_an_unknown_frame_of_reference_is_refused(monkeypatch):
    tools = _tools(monkeypatch)
    out = asyncio.run(tools["app_context_menu"](x=1, y=1, relative_to="panel"))
    assert out["ok"] is False and "'window'" in out["reason"]


def test_a_drag_moves_both_ends(monkeypatch):
    tools = _tools(monkeypatch)
    seen = []
    monkeypatch.setattr(uiauto.windows, "available", lambda: True)
    monkeypatch.setattr(uiauto.menu, "bring_to_front", lambda pid: True)
    monkeypatch.setattr(uiauto.windows, "drag",
                        lambda *a, **k: seen.append(a) or {"ok": True})
    asyncio.run(tools["app_canvas"](x=10, y=20, to_x=30, to_y=40,
                                    relative_to="window"))
    assert seen == [(2562, 12, 2582, 32)]
