# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_link_footprint reads the stored link back.

Altium's Models pane is not redrawn after a scripted link and showed the
previous part; the agent stopped twice per take to check the links.
"""
from __future__ import annotations

import asyncio

from eda_agent.tools import library


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        if command == "application.get_active_document":
            return {"file_path": r"C:\lib\Parts.SchLib"}
        if command == "library.link_footprint":
            return {"success": True, "footprint": "FP1", "replaced": True}
        if command == "library.get_component_details":
            return {"models": [{"model_name": "FP1", "model_type": "PCBLIB"},
                               {"model_name": "U1.step", "model_type": "PCB3DLib"}]}
        raise AssertionError(command)


def _tools(monkeypatch, bridge):
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    library.register_library_tools(_Mcp())
    return tools


def test_the_link_reply_lists_the_footprints_stored(monkeypatch):
    b = _Bridge()
    out = asyncio.run(_tools(monkeypatch, b)["lib_link_footprint"](
        component_name="U1", footprint_name="FP1"))
    assert out["success"] and out["footprints"] == ["FP1"]
    assert b.calls[-1] == "library.get_component_details"
