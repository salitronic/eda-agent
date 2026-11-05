# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium's main window is shown again whenever the bridge finds it hidden.

The main frame has been found hidden, not minimized and with no taskbar
button, while the bridge kept answering: after relaunches, after a menu
walk, and once after hours of nothing but keep-alive pings. Every reply,
pings included, now checks it, shows it without taking the focus, and
leaves a trace line naming what ran before.
"""
from __future__ import annotations

import pytest

from eda_agent.bridge import AltiumBridge
from eda_agent.bridge.altium_bridge import CommandResponse
from eda_agent.ui import windows
from eda_agent.ui.windows import Window

_OWNER = "EDA Agent MCP"


def _desktop(monkeypatch, wins, visible):
    shown = []
    monkeypatch.setattr(windows, "enumerate_windows",
                        lambda pid=None, visible_only=True: list(wins))
    monkeypatch.setattr(windows.win32gui, "IsWindowVisible",
                        lambda h: h in visible)
    monkeypatch.setattr(windows, "_show_without_focus", shown.append)
    return shown


@pytest.fixture
def needs_win32():
    if not windows.available():
        pytest.skip("pywin32 not importable")


def test_only_the_scripted_altiums_hidden_frame_is_shown(monkeypatch,
                                                         needs_win32):
    wins = [Window(1, "TScriptForm", _OWNER, 100),
            Window(2, "TDocumentForm", "a - Altium Designer", 100),
            Window(3, "TDocumentForm", "b - Altium Designer", 200),
            Window(4, "TXPForm", "Some dialog", 100)]
    shown = _desktop(monkeypatch, wins, visible={1})
    assert windows.reshow_hidden_frames_of(_OWNER) == [2]
    assert shown == [2], "a second Altium or a hidden dialog was shown"


def test_a_visible_frame_is_left_alone(monkeypatch, needs_win32):
    wins = [Window(1, "TScriptForm", _OWNER, 100),
            Window(2, "TDocumentForm", "a - Altium Designer", 100)]
    assert _desktop(monkeypatch, wins, visible={1, 2}) == []
    assert windows.reshow_hidden_frames_of(_OWNER) == []


def test_nothing_is_shown_without_a_visible_status_window(monkeypatch,
                                                          needs_win32):
    wins = [Window(1, "TScriptForm", _OWNER, 100),
            Window(2, "TDocumentForm", "a - Altium Designer", 100)]
    shown = _desktop(monkeypatch, wins, visible=set())
    assert windows.reshow_hidden_frames_of(_OWNER) == [] and shown == []


def test_the_show_keeps_the_users_focus():
    assert windows._SW_SHOWNA == 8


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from eda_agent import config as config_module
    monkeypatch.setattr(
        config_module, "config",
        config_module.config.model_copy(update={"workspace_dir": tmp_path}))
    b = AltiumBridge()
    yield b
    b.detach()


def _reply(bridge, monkeypatch, command, hidden):
    monkeypatch.setattr(
        bridge, "_poll_response",
        lambda request_id, timeout: CommandResponse(
            id=request_id, success=True, data={"ok": True}))
    monkeypatch.setattr(windows, "reshow_hidden_frames_of",
                        lambda title: [0x2b0780] if hidden else [])
    return bridge._execute_command(command, {}, 1.0)


def test_a_ping_finds_and_reports_the_hidden_window(bridge, monkeypatch,
                                                    tmp_path):
    _reply(bridge, monkeypatch, "project.run_outjob", hidden=False)
    _reply(bridge, monkeypatch, "application.ping", hidden=True)
    trace = (tmp_path / "bridge_trace.log").read_text(encoding="utf-8")
    line = next(x for x in trace.splitlines() if "FRAME_SHOWN" in x)
    assert "cmd=application.ping" in line and "hwnd=0x2b0780" in line
    assert "last_cmd=project.run_outjob" in line, (
        "the ping must not overwrite the command that ran before it")


def test_a_tool_reply_says_the_window_was_shown(bridge, monkeypatch):
    out = _reply(bridge, monkeypatch, "pcb.focus_board", hidden=True)
    assert "altium_window_shown_again" in out
    out = _reply(bridge, monkeypatch, "pcb.focus_board", hidden=False)
    assert "altium_window_shown_again" not in out


def test_a_failing_check_never_fails_the_call(bridge, monkeypatch):
    def broken(title):
        raise OSError("no desktop")
    monkeypatch.setattr(
        bridge, "_poll_response",
        lambda request_id, timeout: CommandResponse(
            id=request_id, success=True, data={"ok": True}))
    monkeypatch.setattr(windows, "reshow_hidden_frames_of", broken)
    assert bridge._execute_command("pcb.focus_board", {}, 1.0) == {"ok": True}


def test_ui_tools_name_themselves_in_the_trace(monkeypatch, tmp_path):
    from eda_agent import config as config_module
    from eda_agent.bridge import altium_bridge
    from eda_agent.tools import uiauto
    monkeypatch.setattr(
        config_module, "config",
        config_module.config.model_copy(update={"workspace_dir": tmp_path}))

    class _Bridge:
        config = config_module.config

        def get_altium_status(self):
            return {"running": True, "pid": 4242}

    monkeypatch.setattr(altium_bridge, "_bridge", _Bridge())
    monkeypatch.setattr("eda_agent.bridge.get_bridge", lambda: _Bridge())

    def app_click_menu():
        return uiauto._altium_pid(reads_only=True)

    assert app_click_menu() == (4242, None)
    trace = (tmp_path / "bridge_trace.log").read_text(encoding="utf-8")
    assert "UI tool=app_click_menu" in trace


def test_a_menu_walk_finds_a_hidden_frame_and_shows_it(monkeypatch, needs_win32):
    """The menu walk does not go through the bridge, so its own frame
    lookup has to show a hidden frame: app_update_from_libraries said "no
    Altium main window" on a running Altium."""
    from eda_agent.ui import menu

    frame = Window(2, "TDocumentForm", "a - Altium Designer", 100)
    visible: set = set()
    shown = []
    monkeypatch.setattr(
        menu.win, "enumerate_windows",
        lambda pid=None, visible_only=True:
            [w for w in [frame] if not visible_only or w.hwnd in visible])
    monkeypatch.setattr(menu.win32gui, "IsWindowVisible", lambda h: h in visible)

    def show(hwnd):
        shown.append(hwnd)
        visible.add(hwnd)

    monkeypatch.setattr(menu.win, "show_without_focus", show)
    assert menu.frame(100) == frame
    assert shown == [2]
    # Shown once: a visible frame is returned as it is.
    assert menu.frame(100) == frame
    assert shown == [2]
