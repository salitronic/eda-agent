# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_update_from_libraries: the update runs as Altium's own process, and
the dialogs on the way are answered, a "Confirm" only when its text is
the one expected."""
from __future__ import annotations

import asyncio
import threading

from eda_agent.ui import change_order, dialog_answer, ocr, windows


class _Button:
    def __init__(self, text, enabled=True):
        self.text, self.enabled = text, enabled


class _Dialog:
    def __init__(self, hwnd, title, buttons):
        self.hwnd, self.title, self.buttons = hwnd, title, buttons

    def find_button(self, caption):
        return _Button(caption) if caption in self.buttons else None


class _Altium:
    """Dialogs in the order the board's update raises them (AD 26.11):
    layer options, a Confirm, the change order, and on Execute the
    attribute options over it."""

    def __init__(self, confirm_text):
        self.confirm_text = confirm_text
        self.stack = [_Dialog(1, "Update From PCB Libraries - Options",
                              ["OK", "Cancel", "Update All Footprints (Create ECO)"])]
        self.pressed: list[tuple[str, str]] = []
        self.executed = False

    def dialogs(self, pid):
        return list(self.stack)

    def click(self, button):
        top = next(d for d in self.stack if button.text in d.buttons)
        self.pressed.append((top.title, button.text))
        self.stack.remove(top)
        if top.hwnd == 1:
            self.stack.append(_Dialog(2, "Confirm", ["Yes", "No"]))
        elif top.hwnd == 2 and button.text == "Yes":
            self.stack.append(_Dialog(3, change_order.TITLE,
                                      ["Execute Changes", "Close", "Only Show Errors"]))
        elif top.hwnd == 3 and button.text == "Execute Changes":
            self.stack.append(_Dialog(3, change_order.TITLE, ["Close"]))
            self.stack.append(_Dialog(4, "Component(s) Update Options", ["OK", "Cancel"]))
        elif top.hwnd == 4:
            self.executed = True


def _run(monkeypatch, confirm_text):
    fake = _Altium(confirm_text)
    monkeypatch.setattr(windows, "dialogs", fake.dialogs)
    monkeypatch.setattr(windows, "click", fake.click)
    monkeypatch.setattr(ocr, "read_window_rows", lambda hwnd: (
        ["Confirm", fake.confirm_text, "Yes No"] if hwnd == 2 else []))
    monkeypatch.setattr(dialog_answer, "_set", lambda d, step: {"ok": False})
    out = dialog_answer.answer(1, change_order.TITLE, change_order.STEPS,
                               threading.Event(), appear_within=2.0, run_within=2.0,
                               poll=0.01, on_the_way=change_order.library_on_the_way())
    return fake, out


def test_the_board_update_is_answered_to_the_end(monkeypatch):
    fake, out = _run(monkeypatch, "9 of the 155 footprints were not found in libraries. Continue?")
    assert ("Confirm", "Yes") in fake.pressed
    assert ("Component(s) Update Options", "OK") in fake.pressed
    assert out["done"][0] == "Execute Changes" and fake.executed
    said = [p for p in out["passed"] if p["dialog"] == "Confirm"][0]["said"]
    assert "155 footprints were not found" in said


def test_another_confirm_is_left_for_a_person(monkeypatch):
    fake, out = _run(monkeypatch, "Delete 12 unused footprints? This cannot be undone.")
    assert ("Confirm", "Yes") not in fake.pressed
    assert not out["appeared"] and not fake.executed


def test_ocr_slips_still_match():
    assert dialog_answer._letters("n0t f0und in librar") == \
        dialog_answer._letters("not found in librar")


class _Bridge:
    def __init__(self, kind, path="C:\\scratch\\A.SchDoc"):
        self.kind, self.path, self.sent = kind, path, []

    async def send_command_async(self, command, params, **kw):
        self.sent.append((command, params))
        if command == "application.get_active_document":
            return {"file_path": self.path, "document_kind": self.kind}
        return {"success": True}

    def expecting(self, title):
        import contextlib
        return contextlib.nullcontext()


def _tool(monkeypatch, bridge, auto=False):
    from eda_agent.tools import answering, project
    monkeypatch.setattr(project, "get_bridge", lambda: bridge)
    monkeypatch.setattr(answering, "driver", lambda *a, **k: None)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    project.register_project_tools(_Mcp())
    return tools["proj_update_from_libraries"]


def test_the_update_runs_as_altiums_process(monkeypatch):
    bridge = _Bridge("SCH")
    out = asyncio.run(_tool(monkeypatch, bridge)(target="schematic"))
    assert ("generic.run_process", {"process": "WorkspaceManager:DocumentOptions",
                                    "params": "ObjectKind=Project|Action=LibrarySynch"}) \
        in bridge.sent
    assert out["ok"] and "Manual" in out["note"]
    bridge = _Bridge("PCB", "C:\\scratch\\A.PcbDoc")
    asyncio.run(_tool(monkeypatch, bridge)(target="pcb"))
    assert bridge.sent[-1][1]["params"].endswith("Action=PCBLibrarySynch")


def test_the_wrong_document_in_front_is_refused(monkeypatch):
    bridge = _Bridge("PCB")
    out = asyncio.run(_tool(monkeypatch, bridge)(target="schematic"))
    assert out["error"] == "WRONG_FOCUS"
    assert not any(c == "generic.run_process" for c, _ in bridge.sent)
    out = asyncio.run(_tool(monkeypatch, _Bridge("SCH"))(target="both"))
    assert out["ok"] is False
