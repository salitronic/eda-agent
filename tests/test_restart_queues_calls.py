# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Restarting the bridge makes other sessions wait, not fail.

A deploy used to mean asking every other session to pause by hand, because
a call sent while the loop was down timed out and the new loop purged the
requests left on disk. ``eda-agent relaunch --restart`` keeps
maintenance.json in the workspace while it stops and restarts the loop:
calls not yet taken keep waiting, and the loop keeps their requests across
the stop and the start. A server whose Python predates a deploy says so.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from eda_agent import cli
from eda_agent.bridge import altium_bridge as ab
from tests.pascal_source import load
from pathlib import Path

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def test_a_fresh_marker_is_a_restart_and_an_old_one_is_not(tmp_path):
    assert not ab.maintenance_in_progress(tmp_path)
    marker = tmp_path / ab.MAINTENANCE_FILE
    marker.write_text("{}")
    assert ab.maintenance_in_progress(tmp_path)
    old = time.time() - ab._MAINTENANCE_FRESH_S - 5
    os.utime(marker, (old, old))
    assert not ab.maintenance_in_progress(tmp_path), "a dead restart must not hold calls"


@pytest.fixture
def bridge(tmp_path):
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    b = ab.AltiumBridge()
    yield b
    b.detach()


def test_a_call_waits_through_a_restart(bridge, tmp_path):
    rid = ab._short_id()
    (tmp_path / f"request_{rid}.json").write_text("{}")
    (tmp_path / ab.MAINTENANCE_FILE).write_text("{}")

    def new_loop_comes_up():
        time.sleep(1.5)                       # three windows of 0.5 s
        (tmp_path / f"request_{rid}.json").unlink()
        (tmp_path / f"response_{rid}.json").write_text(
            '{"protocol_version": 2, "id": "%s", "success": true, '
            '"data": {"ok": 1}, "error": null}' % rid)

    t = threading.Thread(target=new_loop_comes_up)
    t.start()
    try:
        resp = bridge._poll_response(rid, timeout=0.5)
    finally:
        t.join()
    assert resp.success and resp.data == {"ok": 1}


def test_without_a_restart_a_dead_loop_still_times_out(bridge, tmp_path):
    from eda_agent.bridge.exceptions import AltiumTimeoutError

    rid = ab._short_id()
    (tmp_path / f"request_{rid}.json").write_text("{}")
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_response(rid, timeout=0.5)


def test_the_loop_keeps_waiting_requests_across_a_restart():
    body = load(_ALTIUM / "Main.pas")["CleanupOrphanRequests"]
    keep = body.index("If FileExists(WorkspaceDir + 'maintenance.json') Then")
    assert keep < body.index("FindFiles(WorkspaceDir, 'request_*.json'")
    assert "Exit;" in body[keep:body.index("FindFiles")]


# ---- the out-of-date note ---------------------------------------------------

def test_a_deploy_after_the_server_started_is_named():
    note = ab.outdated_note("2026.10.23.3", "2026.10.23.4", "2026.10.23.4")
    assert "2026.10.23.3" in note and "2026.10.23.4" in note and "/mcp" in note


@pytest.mark.parametrize("started,altium,disk", [
    ("2026.10.23.4", "2026.10.23.4", "2026.10.23.4"),   # current
    ("2026.10.23.4", "2026.10.23.3", "2026.10.23.4"),   # Altium behind: app_ping's job
    ("", "2026.10.23.4", "2026.10.23.4"),               # unknown start
    ("2026.10.23.3", "", "2026.10.23.4"),               # no ping yet
])
def test_no_note_otherwise(started, altium, disk):
    assert ab.outdated_note(started, altium, disk) == ""


def test_the_note_rides_a_reply_now_and_then_never_a_ping(bridge, monkeypatch):
    monkeypatch.setattr(ab, "STARTED_WITH_VERSION", "2026.10.23.3")
    monkeypatch.setattr(ab, "bundled_script_version", lambda: "2026.10.23.4")
    bridge._loop_script_version = "2026.10.23.4"
    assert "server_outdated" not in bridge._with_outdated_note(
        "application.ping", {"pong": True})
    first = bridge._with_outdated_note("pcb.get_nets", {"nets": []})
    assert "server_outdated" in first
    again = bridge._with_outdated_note("pcb.get_nets", {"nets": []})
    assert "server_outdated" not in again, "repeated every few minutes, not every reply"


# ---- relaunch --restart -------------------------------------------------------

pytestmark_win = pytest.mark.skipif(sys.platform != "win32",
                                    reason="relaunch drives Windows Altium")


@pytestmark_win
def test_restart_stops_the_loop_starts_it_and_clears_the_marker(tmp_path, monkeypatch, capsys):
    from eda_agent.config import configure
    from eda_agent.ui import windows, dialog_report
    import eda_agent.diag.doctor as doctor
    import subprocess

    configure(workspace_dir=tmp_path)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(dialog_report, "report", lambda pid: {"dialog_count": 0})
    monkeypatch.setattr(windows, "visible_main_frames", lambda pid: [])
    seen = {}

    def loop_takes_the_stop():
        # The running loop deletes the stop file between commands.
        for _ in range(100):
            if (tmp_path / "stop").exists():
                seen["marker_during_stop"] = (tmp_path / ab.MAINTENANCE_FILE).exists()
                (tmp_path / "stop").unlink()
                return
            time.sleep(0.05)

    t = threading.Thread(target=loop_takes_the_stop)
    t.start()

    def launch(command, **kw):
        seen["launched"] = command
        seen["marker_at_launch"] = (tmp_path / ab.MAINTENANCE_FILE).exists()

        class _P:
            def wait(self, timeout=None):
                return 0
        return _P()

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(doctor, "_ping_altium",
                        lambda: {"pong": True, "script_version": "v"})
    try:
        assert cli._restart(["X2.EXE", "-R..."], 4242) == 0
    finally:
        t.join()
    assert seen["marker_during_stop"] and seen["marker_at_launch"]
    assert seen["launched"] == ["X2.EXE", "-R..."]
    assert not (tmp_path / ab.MAINTENANCE_FILE).exists()
    assert "answers again" in capsys.readouterr().out


@pytestmark_win
def test_restart_refuses_while_a_dialog_is_open(tmp_path, monkeypatch, capsys):
    from eda_agent.config import configure
    from eda_agent.ui import windows, dialog_report

    configure(workspace_dir=tmp_path)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(dialog_report, "report",
                        lambda pid: {"dialog_count": 1, "blocked": True, "summary": "DRC"})
    assert cli._restart(["X2.EXE"], 4242) == 1
    assert not (tmp_path / "stop").exists()
    assert not (tmp_path / ab.MAINTENANCE_FILE).exists()


def test_a_loop_that_is_not_running_does_not_leave_a_stop_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESTART_IDLE_STOP_WAIT_S", 0.3)
    assert cli._wait_for_loop_to_stop(tmp_path) == "not running"
    assert not (tmp_path / "stop").exists()


def test_every_call_is_traced_before_it_waits_for_the_worker():
    import inspect
    # send_command_async hands a lone call to send_command_direct, which
    # holds the wait; a bulk call gathers first (bridge/bulk.py).
    src = inspect.getsource(ab.AltiumBridge.send_command_direct)
    call = src.index('f"CALL cmd={command}')
    assert call < src.index("run_in_executor")


@pytestmark_win
def test_restart_stops_the_loop_that_is_running_not_the_configured_one(
        tmp_path, monkeypatch, capsys):
    """After a demo take the config named the normal workspace while the
    loop still served the demo one: the stop file went where nobody read
    it. The ping says which workspace the running loop serves."""
    from eda_agent.config import configure
    from eda_agent.ui import windows, dialog_report
    import eda_agent.diag.doctor as doctor
    import subprocess

    configured, running = tmp_path / "normal", tmp_path / "demo"
    configured.mkdir()
    running.mkdir()
    configure(workspace_dir=configured)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(dialog_report, "report", lambda pid: {"dialog_count": 0})
    monkeypatch.setattr(windows, "visible_main_frames", lambda pid: [])
    monkeypatch.setattr(doctor, "_ping_altium",
                        lambda: {"pong": True, "workspace_dir": str(running)})
    taken = {}

    def loop_takes_the_stop():
        for _ in range(100):
            for ws in (configured, running):
                if (ws / "stop").exists():
                    taken["where"] = ws
                    (ws / "stop").unlink()
                    return
            time.sleep(0.05)

    class _P:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda command, **kw: _P())
    t = threading.Thread(target=loop_takes_the_stop)
    t.start()
    try:
        assert cli._restart(["X2.EXE"], 4242) == 0
    finally:
        t.join()
    assert taken.get("where") == running
    assert "stopping that one" in capsys.readouterr().out


@pytestmark_win
def test_a_plain_relaunch_refuses_behind_a_dialog(tmp_path, monkeypatch, capsys):
    """With "Unsaved Changes" up the ping fails, and a relaunch then started
    the script: X2.EXE -R went to a NEW Altium instead of the busy one."""
    from eda_agent.bridge import process_manager
    from eda_agent.ui import windows, dialog_report
    import subprocess

    project = tmp_path / "Altium_API.PrjScr"
    project.write_text("")
    exe = tmp_path / "X2.EXE"
    exe.write_text("")

    class _Proc:
        pid = 4242
        exe_path = str(exe)

    class _Sel:
        process = _Proc()
        reason = ""

    monkeypatch.setattr(process_manager.AltiumProcessManager,
                        "select_altium_process", lambda self: _Sel())
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(dialog_report, "report", lambda pid: {
        "dialog_count": 1, "blocked": True, "summary": "Unsaved Changes"})
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert cli.cmd_relaunch(str(project), force=True) == 1
    assert not launched
    assert "Unsaved Changes" in capsys.readouterr().err


def test_a_crashed_handler_is_stopped_first_and_its_progress_file_ignored(
        tmp_path, monkeypatch, capsys):
    """A handler that crashed leaves the script parked at a breakpoint and
    its progress file on disk. Read as a command still running, that file
    held the restart for the whole long wait before Run > Stop was sent."""
    from eda_agent.config import configure
    from eda_agent.ui import windows, dialog_report
    import eda_agent.diag.doctor as doctor
    import subprocess

    configure(workspace_dir=tmp_path)
    (tmp_path / "progress_dead.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "RESTART_IDLE_STOP_WAIT_S", 0.3)
    monkeypatch.setattr(cli, "RESTART_STOP_WAIT_S", 30.0)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(dialog_report, "report", lambda pid: {"dialog_count": 0})
    monkeypatch.setattr(windows, "visible_main_frames", lambda pid: [])
    order = []

    class _P:
        def wait(self, timeout=None):
            return 0

    def popen(command, **kw):
        order.append("stop" if "-REditScript:Stop" in command else "launch")
        return _P()

    pings = iter([None] + [{"pong": True, "script_version": "v"}] * 5)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(doctor, "_ping_altium", lambda: next(pings))
    started = time.monotonic()
    assert cli._restart(["X2.EXE", "-R..."], 4242) == 0
    assert order == ["stop", "launch"]
    assert time.monotonic() - started < 10, "waited out the dead handler's progress file"


def _restart_rig(tmp_path, monkeypatch, screens, pings):
    from eda_agent.config import configure
    from eda_agent.bridge import script_crash
    from eda_agent.ui import windows, dialog_report
    import eda_agent.diag.doctor as doctor
    import subprocess

    configure(workspace_dir=tmp_path)
    monkeypatch.setattr(cli, "RESTART_IDLE_STOP_WAIT_S", 0.3)
    monkeypatch.setattr(cli, "RESTART_DIALOG_GIVE_UP_S", 0.5)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "visible_main_frames", lambda pid: [])
    monkeypatch.setattr(dialog_report, "report", lambda pid: screens())
    pressed = []
    monkeypatch.setattr(script_crash, "_press_ok", lambda hwnd: pressed.append(hwnd) or "")

    class _P:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda command, **kw: _P())
    monkeypatch.setattr(doctor, "_ping_altium", pings)
    return pressed


_ERROR_BOX = {"title": "Error", "kind": "error", "hwnd": 77, "message": [],
              "ocr_text": ["Another script executing now"],
              "buttons": [{"caption": "OK", "enabled": True}]}


@pytestmark_win
def test_an_error_box_in_the_way_of_the_new_loop_is_pressed_at_once(
        tmp_path, monkeypatch, capsys):
    """MEASURED 2026-10-30: the restart sat 80 s behind a box until a
    person closed it."""
    state = {"box": False, "pings": 0}

    def screens():
        if state["box"]:
            return {"blocked": True, "dialog_count": 1, "dialogs": [dict(_ERROR_BOX)],
                    "summary": "Error"}
        return {"dialog_count": 0}

    def pings():
        state["pings"] += 1
        if state["pings"] == 2:
            state["box"] = True             # appears while the loop starts
        if pressed:
            state["box"] = False
            return {"pong": True, "script_version": "v"}
        return None

    pressed = _restart_rig(tmp_path, monkeypatch, screens, pings)
    started = time.monotonic()
    assert cli._restart(["X2.EXE", "-R..."], 4242) == 0
    assert pressed == [77]
    assert time.monotonic() - started < 10
    assert "Another script executing now" in capsys.readouterr().out


@pytestmark_win
def test_another_dialog_in_the_way_is_named_within_seconds(tmp_path, monkeypatch, capsys):
    state = {"pings": 0}

    def screens():
        if state["pings"] >= 2:
            return {"blocked": True, "dialog_count": 1, "summary": "Unsaved Changes",
                    "dialogs": [{"title": "Unsaved Changes", "kind": "confirm",
                                 "hwnd": 5, "buttons": [{"caption": "Save"},
                                                        {"caption": "Discard"}]}]}
        return {"dialog_count": 0}

    def pings():
        state["pings"] += 1
        return None

    pressed = _restart_rig(tmp_path, monkeypatch, screens, pings)
    started = time.monotonic()
    assert cli._restart(["X2.EXE", "-R..."], 4242) == 1
    assert pressed == []
    assert time.monotonic() - started < cli.RESTART_PING_WAIT_S / 2
    assert "Unsaved Changes" in capsys.readouterr().err
    assert not (tmp_path / ab.MAINTENANCE_FILE).exists()
