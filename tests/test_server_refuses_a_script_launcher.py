# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A server started by a script in an AI session's shell is refused; one
started by the client, directly or through a wrapper, serves."""
from __future__ import annotations

import pytest

from eda_agent import launch_guard as lg
from eda_agent import server

SERVER_ENV = {"CLAUDECODE": "1", "CLAUDE_CODE_MESSAGING_SOCKET": "pipe"}
SHELL_ENV = {**SERVER_ENV, "CLAUDE_PID": "500", "CLAUDE_CODE_CHILD_SESSION": "1"}


class _Proc:
    def __init__(self, pid, name, cmd=(), env=None, parents=()):
        self.pid, self._name, self._cmd = pid, name, list(cmd)
        self._env, self._parents = env, list(parents)

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmd

    def environ(self):
        if self._env is None:
            raise PermissionError("not readable")
        return self._env

    def parents(self):
        return self._parents


CLAUDE = _Proc(500, "claude.exe", ["claude.exe"], SERVER_ENV)
LAUNCHER = _Proc(20, "eda-agent.exe", [r"C:\Scripts\eda-agent.exe"], SERVER_ENV)
PROXY = _Proc(21, "python.exe", ["python.exe", r"C:\Scripts\eda-agent.exe"], SERVER_ENV)
SCRIPT = _Proc(30, "python.exe", ["python.exe", "route_all.py", "nets:GND"], SHELL_ENV)


def _launcher(monkeypatch, own_env, chain):
    import psutil
    for key in lg.SHELL_MARKERS:
        monkeypatch.delenv(key, raising=False)
    for key, value in own_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(psutil, "Process", lambda: _Proc(1, "python.exe", parents=chain))
    return lg.script_launcher()


@pytest.mark.parametrize("chain", [
    [CLAUDE],
    [LAUNCHER, CLAUDE],
    [PROXY, LAUNCHER, CLAUDE],
    [_Proc(40, "cmd.exe", ["cmd.exe", "/c", "eda-agent"], SERVER_ENV), CLAUDE],
    [_Proc(41, "uv.exe", ["uvx", "eda-agent"], SERVER_ENV), CLAUDE],
    [_Proc(42, "Cursor.exe", ["Cursor.exe"], {})],
    [_Proc(43, "python.exe", ["python.exe", "my_agent_host.py"], {})],
    [_Proc(44, "bash.exe", ["bash.exe", "run.sh"], None)],
    [],
])
def test_a_server_the_client_started_serves(monkeypatch, chain):
    assert _launcher(monkeypatch, SERVER_ENV, chain) is None


def test_a_server_a_script_started_with_a_cut_down_environment_is_refused(monkeypatch):
    found = _launcher(monkeypatch, {}, [SCRIPT, _Proc(31, "bash.exe", ["bash.exe", "pipeline.sh"])])
    assert found == {"pid": 30, "name": "python.exe",
                     "command": "python.exe route_all.py nets:GND"}
    text = lg.refusal(found)
    assert "route_all.py" in text and "items" in text


def test_a_server_typed_into_the_sessions_shell_is_refused(monkeypatch):
    shell = _Proc(50, "bash.exe", ["bash.exe", "-c", "eda-agent"], None)
    assert _launcher(monkeypatch, SHELL_ENV, [LAUNCHER, shell, CLAUDE])["pid"] == 50


def test_a_look_that_fails_never_stops_a_server(monkeypatch):
    import psutil

    def broken():
        raise OSError("no process table")
    monkeypatch.setattr(psutil, "Process", broken)
    assert lg.script_launcher() is None


def test_serve_returns_before_anything_starts(monkeypatch, capsys):
    monkeypatch.setattr(lg, "script_launcher", lambda: {
        "pid": 30, "name": "python.exe", "command": "python.exe route_all.py"})
    monkeypatch.setattr(server, "_install_stdio_guard", lambda: None)
    monkeypatch.setattr(server, "setup_logging", lambda: pytest.fail("server started"))
    assert server.serve_mcp() == 2
    assert "does not serve a script" in capsys.readouterr().err


def test_the_instructions_say_so():
    for backend in ("altium", "kicad", "easyeda"):
        assert "NO SERVER OF YOUR OWN" in server.build_server_instructions(backend=backend)
