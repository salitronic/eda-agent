# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""--toolset and --backend start the server again, and the client stays
connected.

The tool surface is chosen at import, so a flag that asks for another one
runs the server again with the environment set. On Windows os.execv ends
the process the client started and with it the client's pipes: the client
then waited out its connect limit on a server that had already left.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from eda_agent import server


def _other_toolset() -> str:
    return next(t for t in server.TOOLSETS if t != server.ACTIVE_TOOLSET)


@pytest.fixture
def flag(monkeypatch):
    monkeypatch.delenv("_EDA_AGENT_BACKEND_REEXEC", raising=False)
    monkeypatch.setenv("EDA_AGENT_TOOLSET", server.ACTIVE_TOOLSET)
    monkeypatch.setattr(server, "serve_mcp", lambda **k: pytest.fail("served without the restart"))
    monkeypatch.setattr("sys.argv", ["eda-agent", "--toolset", _other_toolset()])
    return _other_toolset()


def test_on_windows_the_restart_is_a_child_on_the_same_pipes(monkeypatch, flag):
    calls = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("execv ends the client's process on Windows"))
    monkeypatch.setattr(subprocess, "call", lambda command, **k: calls.append((command, k)) or 7)
    assert server.main() == 7                      # the child's exit code is passed on
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, "-m", "eda_agent.server"]
    assert command[3:] == ["--toolset", flag]
    # No stdin, stdout or stderr given: the child inherits this process's.
    assert not {"stdin", "stdout", "stderr"} & set(kwargs)
    assert os.environ["EDA_AGENT_TOOLSET"] == flag
    assert os.environ["_EDA_AGENT_BACKEND_REEXEC"] == "1"


def test_elsewhere_the_process_is_replaced(monkeypatch, flag):
    class Replaced(Exception):
        pass

    def execv(path, command):
        assert path == sys.executable and command[1:3] == ["-m", "eda_agent.server"]
        raise Replaced

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "execv", execv)
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: pytest.fail("no child where execv replaces"))
    with pytest.raises(Replaced):
        server.main()


def test_the_server_started_again_does_not_start_a_third(monkeypatch, flag):
    monkeypatch.setenv("_EDA_AGENT_BACKEND_REEXEC", "1")
    monkeypatch.setattr(server, "serve_mcp", lambda **k: 0)
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: pytest.fail("a second restart"))
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("a second restart"))
    assert server.main() == 0
