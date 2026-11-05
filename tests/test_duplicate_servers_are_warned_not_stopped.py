# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Other eda-agent servers on the workspace are reported, never stopped.

Twelve servers were once reported alive on one machine, the oldest three
weeks old, all polling the same request directory, and nothing said so.
Several sessions at once are allowed and used on purpose, so this only
warns: app_ping and app_get_status list the others with pid and start
time. Processes are faked here; nothing real is inspected or touched.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re

import psutil
import pytest

from eda_agent import server_registry as reg
from eda_agent.tools import application as app_mod
from eda_agent.tools.registry import ToolRegistry

STARTED = 1_790_000_000.0


class _Proc:
    """psutil.Process stand-in: ``table`` maps pid to create_time or an error."""

    table: dict = {}

    def __init__(self, pid):
        entry = self.table.get(pid, psutil.NoSuchProcess(pid))
        if isinstance(entry, Exception):
            raise entry
        self.pid = pid
        self._created = entry

    def create_time(self):
        return self._created

    def parent(self):
        # ``parents`` maps a pid to its parent's pid, or None for a server
        # whose session is gone; a pid not in it has a live session.
        up = self.parents.get(self.pid, 1)
        return None if up is None else _Parent(up, self.parents)

    parents: dict = {}


class _Parent:
    def __init__(self, pid, parents):
        self.pid = pid
        self._parents = parents

    def name(self):
        return "eda-agent.exe" if self.pid in self._parents else "claude.exe"

    def parent(self):
        up = self._parents.get(self.pid, 1)
        return None if up is None else _Parent(up, self._parents)


@pytest.fixture
def procs(monkeypatch):
    _Proc.table = {os.getpid(): STARTED}
    _Proc.parents = {}
    monkeypatch.setattr(reg.psutil, "Process", _Proc)
    return _Proc.table


def _write(ws, pid, created):
    folder = ws / reg.SERVERS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"server_{pid}.json"
    path.write_text(json.dumps({"pid": pid, "create_time": created,
                                "started": "2026-10-01T09:00:00"}), encoding="utf-8")
    return path


def test_a_server_records_itself_and_is_not_its_own_duplicate(tmp_path, procs):
    path = reg.register(tmp_path)
    assert path is not None and path.exists()
    assert json.loads(path.read_text())["pid"] == os.getpid()
    assert reg.others(tmp_path) == []
    reg.unregister(tmp_path)
    assert not path.exists()


def test_a_live_other_server_is_listed_with_its_start(tmp_path, procs):
    procs[40001] = STARTED - 3600
    _write(tmp_path, 40001, STARTED - 3600)
    assert reg.others(tmp_path) == [{"pid": 40001, "started": "2026-10-01T09:00:00",
                                     "orphaned": False}]


def test_a_server_whose_session_is_gone_is_orphaned(procs):
    procs[40010] = STARTED
    _Proc.parents = {40010: None}
    assert reg.orphaned(40010)


def test_a_server_behind_a_dead_launcher_is_orphaned(procs):
    procs[40011] = STARTED
    _Proc.parents = {40011: 50011, 50011: None}      # launcher with no session
    assert reg.orphaned(40011)


def test_a_server_of_a_live_session_is_not(procs):
    procs[40012] = STARTED
    _Proc.parents = {40012: 50012}                   # launcher under claude.exe
    assert not reg.orphaned(40012)


def test_a_vanished_process_is_not_called_orphaned(procs):
    assert not reg.orphaned(49999)


def test_stale_records_are_removed(tmp_path, procs):
    gone = _write(tmp_path, 40002, STARTED)          # no such process
    procs[40003] = STARTED + 99                       # pid reused since
    reused = _write(tmp_path, 40003, STARTED)
    assert reg.others(tmp_path) == []
    assert not gone.exists() and not reused.exists()


def test_a_process_that_cannot_be_inspected_is_kept(tmp_path, procs):
    procs[40004] = psutil.AccessDenied(40004)
    kept = _write(tmp_path, 40004, STARTED)
    assert [s["pid"] for s in reg.others(tmp_path)] == [40004]
    assert kept.exists()


def test_an_unreadable_record_is_skipped_not_deleted(tmp_path, procs):
    folder = tmp_path / reg.SERVERS_DIR
    folder.mkdir()
    half = folder / "server_40005.json"
    half.write_text('{"pid": 400', encoding="utf-8")
    assert reg.others(tmp_path) == []
    assert half.exists()


def test_the_warning_names_pids_and_says_nothing_was_stopped():
    text = reg.duplicate_warning([
        {"pid": 7, "started": "2026-09-18T08:00:00", "orphaned": True},
        {"pid": 8, "started": "2026-09-18T09:00:00", "orphaned": False}])
    assert "pid 7" in text and "2026-09-18T08:00:00" in text
    assert "pid 8" not in text, "a server of a running session is normal"
    assert "1 of the 2" in text
    assert "Nothing was stopped" in text
    assert reg.duplicate_warning([]) is None


def test_servers_of_running_sessions_raise_no_warning():
    assert reg.duplicate_warning([{"pid": 8, "orphaned": False},
                                  {"pid": 9, "orphaned": False}]) is None


def _tools(monkeypatch, servers):
    class _Bridge:
        def is_altium_running(self):
            return True

        def ping_with_version(self):
            return {"pong": True, "script_version": "x"}

        def get_altium_status(self):
            return {"running": True, "pid": 1}

    monkeypatch.setattr(app_mod, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(reg, "others", lambda ws: list(servers))
    registry = ToolRegistry()
    app_mod.register_application_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


@pytest.mark.parametrize("tool", ["app_ping", "app_get_status"])
def test_both_tools_carry_the_warning(monkeypatch, tool):
    servers = [{"pid": 40001, "started": "2026-09-18T08:00:00", "orphaned": True}]
    out = asyncio.run(_tools(monkeypatch, servers)[tool]())
    assert out["other_servers"] == servers
    assert any("pid 40001" in w for w in out["warnings"])


@pytest.mark.parametrize("tool", ["app_ping", "app_get_status"])
def test_no_others_means_no_warning(monkeypatch, tool):
    out = asyncio.run(_tools(monkeypatch, [])[tool]())
    assert out["other_servers"] == []
    assert not out.get("warnings")


def test_nothing_is_ever_stopped_or_refused():
    source = inspect.getsource(reg)
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    for forbidden in (".kill(", ".terminate(", "os.kill", "sys.exit", "raise "):
        assert forbidden not in code, forbidden


def test_the_server_records_itself_and_removes_the_record():
    from eda_agent import server
    body = inspect.getsource(server.serve_mcp)
    assert "server_registry.register(" in body
    assert re.search(r"server_registry\.unregister\(.*\)\s*\n\s*import os as _os", body), (
        "the record must be removed before _exit, which skips atexit")
