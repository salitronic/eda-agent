# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A 3D fit puts Altium's 3D cursor on the part before Zoom All.

In 3D, Zoom All frames the footprint together with the 3D cursor, which
follows the mouse; with the mouse elsewhere every footprint of take 24
came out a few pixels wide and the LTM8060 was fitted off screen. The fit
now goes into 3D from above, posts a mouse move to the middle of the view
(the user's pointer is not moved), fits, then turns the camera.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import generic


class _Bridge:
    def __init__(self, log):
        self.log = log

    async def send_command_async(self, command, params=None, timeout=None):
        self.log.append((command, dict(params or {})))
        return {"success": True}


def _tool(monkeypatch, log):
    monkeypatch.setattr(generic, "get_bridge", lambda: _Bridge(log))
    monkeypatch.setattr(generic, "_cursor_to_view_centre",
                        lambda: log.append(("cursor", {})) or True)

    async def no_wait(_):
        return None

    monkeypatch.setattr(generic.asyncio, "sleep", no_wait)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return tools["obj_switch_view"]


def test_the_cursor_is_put_on_the_part_between_the_switch_and_the_fit(monkeypatch):
    log = []
    tool = _tool(monkeypatch, log)
    out = asyncio.run(tool(mode="3d", fit=True, views="iso,bottom", camera="iso"))
    assert log == [
        ("generic.switch_view", {"mode": "3d", "camera": "top"}),
        ("cursor", {}),
        ("generic.run_process", {"process": "PCB:Zoom", "params": "Action=All"}),
        ("generic.run_process", {"process": "PCB:Zoom", "params": "Action=Out"}),
        ("generic.switch_view", {"mode": "3d", "camera": "iso", "views": "iso,bottom"}),
    ]
    assert out["fit"] is True and out["camera"] == "iso"


def test_2d_and_unfitted_3d_are_one_call(monkeypatch):
    log = []
    tool = _tool(monkeypatch, log)
    asyncio.run(tool(mode="2d", fit=True))
    asyncio.run(tool(mode="3d", camera="iso"))
    assert [c for c, _ in log] == ["generic.switch_view", "generic.switch_view"]
    assert ("cursor", {}) not in log
