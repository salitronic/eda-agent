# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A fit in a symbol library steps out once, so the top pin row clears the
editor's floating toolbar; other documents keep the plain fit."""
from __future__ import annotations

import asyncio

from eda_agent.tools import generic


class _Bridge:
    def __init__(self, kind):
        self.kind, self.sent = kind, []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        if command == "generic.zoom":
            return {"action": params["action"], "document_kind": self.kind,
                    "dispatched": True}
        return {"dispatched": True}


def _zoom(monkeypatch, kind, action="fit"):
    bridge = _Bridge(kind)
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
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
    result = asyncio.run(tools["obj_zoom"](action=action))
    return result, bridge.sent


def test_a_symbol_library_fit_zooms_out_one_step(monkeypatch):
    result, sent = _zoom(monkeypatch, "SCHLIB")
    assert sent == [("generic.zoom", {"action": "fit"}),
                    ("generic.run_process",
                     {"process": "Sch:Zoom", "params": "Action=ZoomOut"})]
    assert result["margin"]


def test_sheets_boards_and_other_actions_are_left_alone(monkeypatch):
    for kind, action in (("SCH", "fit"), ("PCBLIB", "fit"), ("PCB", "fit"),
                         ("SCHLIB", "selection")):
        _, sent = _zoom(monkeypatch, kind, action)
        assert [c for c, _ in sent] == ["generic.zoom"], (kind, action)
