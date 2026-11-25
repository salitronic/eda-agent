# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tools that save a sheet to reopen it say so, can be told not to, and
leave a sheet that was not written open as it is.

Found drawing a real sheet: sch_draw_template's description ended "Does
not save", and after a draw it saves, closes and reopens the sheet. The
caller had committed to not saving and learned of the save from the file
on disk."""
from __future__ import annotations

import asyncio
import inspect

from eda_agent.bridge.exceptions import AltiumError
from eda_agent.tools import generic, sch_template

_SHEET = "C:/p/Sheet1.SchDoc"


class _Bridge:
    def __init__(self, answers):
        self.sent = []
        self.answers = {"application.get_active_document": {"file_path": _SHEET}, **answers}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append(command)
        return self.answers.get(command, {"success": True})


def _tools(monkeypatch, bridge):
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Server())
    return tools


def test_a_written_sheet_is_closed_and_opened_again():
    bridge = _Bridge({"application.save_document": {"saved": True}})
    out = asyncio.run(generic._reopen_sheet(bridge))
    assert out["done"] and out["saved"] and out["file_path"] == _SHEET
    assert bridge.sent == ["application.get_active_document", "application.save_document",
                           "application.close_document", "application.run_process",
                           "application.set_active_document"]


def test_a_sheet_the_saving_switch_refused_stays_open():
    class _Refusing(_Bridge):
        async def send_command_async(self, command, params=None, **kwargs):
            self.sent.append(command)
            if command == "application.save_document":
                raise AltiumError("This command saves documents. Saving is switched off",
                                  code="SAVING_OFF")
            return self.answers.get(command, {"success": True})

    bridge = _Refusing({})
    out = asyncio.run(generic._reopen_sheet(bridge))
    assert out["done"] is False and "switched off" in out["reason"]
    # Not closed: the drawing exists only in Altium.
    assert bridge.sent == ["application.get_active_document", "application.save_document"]


def test_a_sheet_altium_declined_to_save_stays_open():
    bridge = _Bridge({"application.save_document": {"saved": False, "reason": "read-only"}})
    out = asyncio.run(generic._reopen_sheet(bridge))
    assert out["done"] is False and out["saved"] is False and out["reason"] == "read-only"
    assert out["until_reopened"]
    assert "application.close_document" not in bridge.sent


def test_the_sheet_size_can_be_set_without_saving(monkeypatch):
    bridge = _Bridge({"generic.set_sheet_size": {"success": True, "style": "A3"}})
    tools = _tools(monkeypatch, bridge)
    out = asyncio.run(tools["sch_set_sheet_size"](style="A3", reopen=False))
    assert bridge.sent == ["generic.set_sheet_size"]
    assert out["reopened"]["done"] is False and out["reopened"]["saved"] is False
    assert out["reopened"]["until_reopened"]
    # The shared reply is copied, not handed out.
    out["reopened"]["done"] = True
    assert generic._NOT_REOPENED["done"] is False


def test_the_sheet_size_saves_and_reopens_unless_told_not_to(monkeypatch):
    bridge = _Bridge({"generic.set_sheet_size": {"success": True},
                      "application.save_document": {"saved": True}})
    tools = _tools(monkeypatch, bridge)
    out = asyncio.run(tools["sch_set_sheet_size"](style="A3"))
    assert out["reopened"]["done"] and "application.close_document" in bridge.sent


def test_the_draw_and_the_sheet_size_say_that_they_save(monkeypatch):
    tools = {}

    class _Server:
        def tool(self, *a, **k):
            def keep(fn):
                tools[fn.__name__] = fn
                return fn
            return keep

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    sch_template.register_sch_template_tools(_Server())
    generic.register_generic_tools(_Server())
    for name in ("sch_draw_template", "sch_set_sheet_size"):
        doc = " ".join(tools[name].__doc__.split())
        assert "SAVES THE SHEET" in doc and "reopen=False" in doc, name
        assert "Does not save" not in doc, name
        assert inspect.signature(tools[name]).parameters["reopen"].default is True, name
    assert "SAVES" in tools["sch_audit_outside_sheet"].__doc__

