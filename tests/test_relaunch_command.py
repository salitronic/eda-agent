# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""`eda-agent relaunch` starts the bridge only when asked, and harms nothing.

It must not purge request files another session may be waiting on, must
leave a bridge that already answers alone, and must not guess between
two Altium processes.
"""

from __future__ import annotations

import sys

import pytest

from eda_agent import cli
from eda_agent.bridge import process_manager as pm

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="relaunch drives Windows Altium")


def _one_altium(tmp_path, monkeypatch):
    exe = tmp_path / "X2.EXE"
    exe.write_bytes(b"")
    info = pm.AltiumProcessInfo(pid=4242, name="X2.EXE", exe_path=str(exe))
    monkeypatch.setattr(
        pm.AltiumProcessManager, "select_altium_process",
        lambda self: pm.AltiumSelection(info, [info], "only_candidate"))
    project = tmp_path / "Altium_API.PrjScr"
    project.write_text("[Design]", encoding="utf-8")
    return exe, project


def test_the_command_runs_startmcpserver_from_the_project():
    command = cli.relaunch_command(r"C:\A\X2.EXE", r"C:\S\Altium_API.PrjScr")
    assert command == [
        r"C:\A\X2.EXE",
        r"-RScriptingSystem:RunScript(ProjectName=C:\S\Altium_API.PrjScr"
        r"|ProcName=Dispatcher.pas>StartMCPServer)"]


def test_a_live_bridge_is_left_alone(tmp_path, monkeypatch, capsys):
    _, project = _one_altium(tmp_path, monkeypatch)
    import eda_agent.diag.doctor as doctor
    monkeypatch.setattr(doctor, "_ping_altium", lambda: {"pong": True})
    started = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    assert cli.cmd_relaunch(prjscr=str(project)) == 0
    assert started == []
    assert "already answers" in capsys.readouterr().out


def test_pending_requests_survive_a_relaunch(tmp_path, monkeypatch):
    exe, project = _one_altium(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pending = workspace / "request_abc.json"
    pending.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("EDA_AGENT_WORKSPACE", str(workspace))
    import eda_agent.diag.doctor as doctor
    monkeypatch.setattr(doctor, "_ping_altium", lambda: None)
    started = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen",
                        lambda command, **k: started.append(command))
    assert cli.cmd_relaunch(prjscr=str(project)) == 0
    # A script left halted in the debugger is stopped first, or the new
    # loop meets "Another script executing now".
    assert started == [[str(exe), "-REditScript:Stop"],
                       cli.relaunch_command(str(exe), str(project))]
    assert pending.exists()


def test_two_altiums_are_not_guessed_between(tmp_path, monkeypatch, capsys):
    project = tmp_path / "Altium_API.PrjScr"
    project.write_text("[Design]", encoding="utf-8")
    monkeypatch.setattr(
        pm.AltiumProcessManager, "select_altium_process",
        lambda self: pm.AltiumSelection(None, [], "ambiguous", "2 running"))
    assert cli.cmd_relaunch(prjscr=str(project)) == 1
    assert "2 running" in capsys.readouterr().err


def test_a_missing_project_says_to_install_the_scripts(tmp_path, capsys):
    assert cli.cmd_relaunch(prjscr=str(tmp_path / "none.PrjScr")) == 1
    assert "install-scripts" in capsys.readouterr().err


def _relaunch_with_frames(tmp_path, monkeypatch, before, hidden):
    _, project = _one_altium(tmp_path, monkeypatch)
    import eda_agent.diag.doctor as doctor
    from eda_agent.ui import windows
    monkeypatch.setattr(doctor, "_ping_altium", lambda: None)
    monkeypatch.setattr(cli, "RELAUNCH_WATCH_S", 0.0)
    waited = []

    class Launcher:
        def wait(self, timeout=None):
            waited.append(timeout)

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda command, **k: Launcher())
    monkeypatch.setattr(windows, "visible_main_frames", lambda pid: list(before))
    asked = []

    def show_if_hidden(hwnds):
        asked.append(list(hwnds))
        return [h for h in hwnds if h in hidden]

    monkeypatch.setattr(windows, "show_if_hidden", show_if_hidden)
    assert cli.cmd_relaunch(prjscr=str(project)) == 0
    return waited, asked


def test_a_main_window_the_handoff_hid_is_shown_again(tmp_path, monkeypatch,
                                                      capsys):
    waited, asked = _relaunch_with_frames(tmp_path, monkeypatch,
                                          before=[11], hidden=[11])
    # Checked only once the second X2.EXE has handed over and exited (the
    # first wait is the stop sent before it).
    assert waited[-1:] == [cli.RELAUNCH_HANDOFF_TIMEOUT_S]
    assert asked == [[11]]
    assert "shown again" in capsys.readouterr().out


def test_a_window_hidden_before_the_relaunch_is_left_hidden(tmp_path,
                                                            monkeypatch, capsys):
    _, asked = _relaunch_with_frames(tmp_path, monkeypatch,
                                     before=[], hidden=[11])
    assert asked == []
    assert "shown again" not in capsys.readouterr().out


def test_show_if_hidden_shows_only_live_hidden_windows(monkeypatch):
    from eda_agent.ui import windows
    if not windows.available():
        pytest.skip("pywin32 not importable")
    state = {1: True, 2: False, 3: None}     # visible, hidden, destroyed
    shown = []
    monkeypatch.setattr(windows.win32gui, "IsWindow",
                        lambda h: state[h] is not None)
    monkeypatch.setattr(windows.win32gui, "IsWindowVisible",
                        lambda h: bool(state[h]))
    monkeypatch.setattr(windows.win32gui, "ShowWindow",
                        lambda h, cmd: shown.append((h, cmd)))
    assert windows.show_if_hidden([1, 2, 3]) == [2]
    assert shown == [(2, 5)]                 # SW_SHOW keeps it maximized
