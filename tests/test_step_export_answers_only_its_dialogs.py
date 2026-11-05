# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_export_step answers the STEP exporter's dialogs and nothing else.

PCB:Export with the STEP 3D format waits on three dialogs: the file name,
the export options and a closing "Done!". Measured live: answered in that
order it writes the file. A dialog that is not one of them may belong to
another session, so nothing is pressed in it.
"""
from __future__ import annotations

import asyncio

from eda_agent.bridge.exceptions import AltiumTimeoutError
from eda_agent.tools import project, uiauto
from eda_agent.ui import windows


class _Button:
    def __init__(self, text):
        self.text = text


class _Dialog:
    def __init__(self, title, hwnd):
        self.title, self.hwnd = title, hwnd

    def find_button(self, caption):
        return _Button(caption)


def _tool(monkeypatch, tmp_path, titles, writes=True, exporter_writes=False):
    """The tool, with the given dialogs shown one after another. The
    exporter object writes nothing unless ``exporter_writes``."""
    board = str(tmp_path / "Board.PcbDoc")
    out = tmp_path / "Board.step"
    queue = [_Dialog(t, i) for i, t in enumerate(titles)]
    pressed = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            if command == "application.get_active_document":
                return {"file_path": board}
            if command == "project.export_step":
                raise AltiumTimeoutError("Altium is showing a modal dialog")
            if command == "project.export_step_api":
                if exporter_writes:
                    out.write_text("ISO-10303-21; exporter")
                    return {"generated": True}
                return {"generated": False, "reason": "no new file"}
            return {"focused": True}

    def _click(button):
        pressed.append((queue[0].title, button.text))
        if queue[0].title == "Information" and writes:
            out.write_text("ISO-10303-21;")
        queue.pop(0)

    monkeypatch.setattr(project, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(uiauto, "_altium_pid", lambda reads_only=False: (1, None))
    monkeypatch.setattr(windows, "dialogs", lambda pid: queue[:1])
    monkeypatch.setattr(windows, "click", _click)
    monkeypatch.setattr(windows, "wait_for_close", lambda hwnd, timeout: True)

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    project.register_project_tools(_Mcp())
    return tools["proj_export_step"], str(out), board, pressed


def test_the_three_dialogs_are_answered_and_the_file_is_checked(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"])
    result = asyncio.run(tool(out, board_path=board))
    assert pressed == [("Export File", "Save"), ("Export Options", "OK"),
                       ("Information", "OK")]
    assert result["ok"] is True and result["generated"] is True
    assert result["size_bytes"] > 0


def test_a_strange_dialog_is_left_alone(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Teardrops"])
    result = asyncio.run(tool(out, board_path=board))
    assert pressed == [("Export File", "Save")]
    assert result["ok"] is False and "Teardrops" in result["reason"]


def test_answered_without_a_file_is_not_success(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"],
        writes=False)
    result = asyncio.run(tool(out, board_path=board))
    assert result["ok"] is False and result["generated"] is False


def test_an_existing_file_is_kept_unless_asked(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(monkeypatch, tmp_path, ["Export File"])
    (tmp_path / "Board.step").write_text("earlier")
    result = asyncio.run(tool(out, board_path=board))
    assert result["ok"] is False and pressed == []


def test_an_overwrite_moves_the_old_file_aside_first(monkeypatch, tmp_path):
    """Measured: overwriting through Altium raised "Confirm Save As" over
    the Save dialog and the export never ran. The old file is moved aside
    instead, so no confirmation comes, and dropped once the new one is in."""
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"])
    (tmp_path / "Board.step").write_text("earlier")
    result = asyncio.run(tool(out, board_path=board, overwrite=True))
    assert result["ok"] is True
    assert (tmp_path / "Board.step").read_text() == "ISO-10303-21;"
    assert not (tmp_path / "Board.step.previous").exists()


def test_a_failed_overwrite_puts_the_old_file_back(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"],
        writes=False)
    (tmp_path / "Board.step").write_text("earlier")
    result = asyncio.run(tool(out, board_path=board, overwrite=True))
    assert result["ok"] is False and result["previous_file_kept"] is True
    assert (tmp_path / "Board.step").read_text() == "earlier"


def test_done_is_answered_after_a_quiet_spell(monkeypatch, tmp_path):
    """Measured: the export runs with nothing on screen between its options
    and its "Done!". Taken for the end, that gap left "Done!" open, which
    blocked the next call, and the file was checked before it was written."""
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"])
    shown = windows.dialogs
    gap = {"left": 6}

    def _with_a_gap(pid):
        current = shown(pid)
        if current and current[0].title == "Information" and gap["left"]:
            gap["left"] -= 1
            return []
        return current

    monkeypatch.setattr(windows, "dialogs", _with_a_gap)
    result = asyncio.run(tool(out, board_path=board))
    assert ("Information", "OK") in pressed
    assert result["ok"] is True


def test_a_done_after_the_file_has_settled_is_answered_too(monkeypatch, tmp_path):
    """Measured on a demo board: the file was written and had settled, the
    screen stayed empty past the quiet spell, the export was judged done,
    and "Done!" came after it; left open, it failed the next two calls."""
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"])
    shown = windows.dialogs
    late = {"polls": 0}
    clock = {"t": 0.0}

    def _late(pid):
        current = shown(pid)
        if current and current[0].title == "Information":
            late["polls"] += 1
            if late["polls"] < 40:
                (tmp_path / "Board.step").write_text("ISO-10303-21;")
                return []
        return current

    import time as real_time

    def _mono():
        clock["t"] += 0.5
        return clock["t"]

    monkeypatch.setattr(windows, "dialogs", _late)
    monkeypatch.setattr(real_time, "monotonic", _mono)
    monkeypatch.setattr(real_time, "sleep", lambda s: None)
    result = asyncio.run(tool(out, board_path=board))
    assert ("Information", "OK") in pressed
    assert result["ok"] is True
    assert result["dialogs_answered"][-1] == "Information"


def test_a_missing_output_folder_is_made(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"])
    import os
    nested = os.path.join(os.path.dirname(out), "STEP", "Board.step")

    async def _no_wait(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_wait)
    asyncio.run(tool(nested, board_path=board))
    assert os.path.isdir(os.path.dirname(nested))


def test_the_exporter_object_writes_with_no_dialog(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(monkeypatch, tmp_path, ["Export File"],
                                      exporter_writes=True)
    (tmp_path / "Board.step").write_text("earlier")
    result = asyncio.run(tool(out, board_path=board, overwrite=True))
    assert result["ok"] is True and result["route"] == "exporter"
    assert pressed == [] and result["dialogs_answered"] == []
    assert (tmp_path / "Board.step").read_text() == "ISO-10303-21; exporter"
    assert not (tmp_path / "Board.step.previous").exists()


def test_an_exporter_that_writes_nothing_falls_back_to_the_dialogs(monkeypatch, tmp_path):
    tool, out, board, pressed = _tool(
        monkeypatch, tmp_path, ["Export File", "Export Options", "Information"])
    result = asyncio.run(tool(out, board_path=board))
    assert result["ok"] is True and result["route"] == "dialogs"
    assert result["exporter_reason"] == "no new file"
    assert pressed[0] == ("Export File", "Save")
