# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The teardrop tools fill in and confirm Altium's Teardrops dialog."""
from __future__ import annotations

import asyncio
import contextlib
import threading

from eda_agent.tools import answering, pcb
from eda_agent.ui.dialog_answer import Check, Press


class _Bridge:
    """Altium as far as teardrops go: the command does nothing in 3D view."""

    def __init__(self, view="2d"):
        self.drops = 0
        self.view = view
        self.sent = []

    @contextlib.contextmanager
    def expecting(self, title):
        yield

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append(command)
        if command == "generic.switch_view":
            self.view = params["mode"]
            return {"success": True, "mode": params["mode"]}
        if command == "generic.get_object_count":
            return {"count": self.drops if params["object_type"] == "eRegionObject" else 0}
        if command == "pcb.teardrops":
            if self.view == "3d":
                return {"launched": True, "modal": True, "note": "choose Add or Remove"}
            self.drops = 4
            return {"launched": True, "modal": True, "note": "choose Add or Remove"}
        raise AssertionError(command)


def _run(monkeypatch, tool, view="2d", appears=True, **kwargs):
    bridge = _Bridge(view)
    seen = {"bridge": bridge}

    def driver(title, steps, on_the_way=None):
        seen["title"], seen["steps"] = title, steps
        shown = appears and bridge.view == "2d"
        return (lambda: {"appeared": shown, "done": [], "closed": shown}), threading.Event()

    monkeypatch.setattr(pcb, "get_bridge", lambda: bridge)
    monkeypatch.setattr(answering, "driver", driver)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    pcb.register_pcb_tools(_Mcp())
    return asyncio.run(tools[tool](**kwargs)), seen


def test_add_sets_the_choices_given_and_presses_ok(monkeypatch):
    out, seen = _run(monkeypatch, "pcb_add_teardrops", scope="selected",
                     smd_pads=True, tracks=False)
    assert seen["title"] == "Teardrops"
    assert seen["steps"] == [
        Check("Add"), Check("Selected only"), Check("SMD Pad", True),
        Check("Tracks", False), Check("Generate report", False),
        Press("OK", closes=True)]
    assert (out["teardrops_before"], out["teardrops_after"]) == (0, 4)
    assert out["dialog"]["closed"] is True and "note" not in out


def test_remove_chooses_remove(monkeypatch):
    _, seen = _run(monkeypatch, "pcb_remove_teardrops")
    assert seen["steps"][:2] == [Check("Remove"), Check("All")]


def test_an_unknown_scope_opens_nothing(monkeypatch):
    out, seen = _run(monkeypatch, "pcb_add_teardrops", scope="board")
    assert out["ok"] is False and "steps" not in seen


def test_a_board_in_3d_is_put_in_2d_before_the_command(monkeypatch):
    """Altium runs no Teardrops in 3D view: no dialog came up and the
    call came back as if it had worked."""
    out, seen = _run(monkeypatch, "pcb_add_teardrops", view="3d")
    sent = seen["bridge"].sent
    assert sent.index("generic.switch_view") < sent.index("pcb.teardrops")
    assert (out["teardrops_before"], out["teardrops_after"]) == (0, 4)
    assert out["view"] == "2d" and out.get("ok", True) is True


def test_a_dialog_that_never_came_up_is_a_failure(monkeypatch):
    out, _ = _run(monkeypatch, "pcb_remove_teardrops", appears=False)
    assert out["ok"] is False and "removed" in out["reason"]
