# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A stalled target window must not stall screenshots or crash recovery.

The deadline test starts an ordinary sleeping child, never a window or
Altium, and checks that capture kills/reaps it and removes partial output.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from eda_agent.ui import ocr


def test_capture_uses_hidden_worker_and_atomically_replaces(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "no_window", lambda: {"creationflags": 0x08000000})
    destination = tmp_path / "a path with spaces.png"
    destination.write_bytes(b"previous")
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs)
        assert command[:3] == [sys.executable, "-m", "eda_agent.ui.ocr"]
        assert command[3] == "123" and command[5] == "2"
        stage = Path(command[4])
        assert stage.parent == destination.parent
        assert destination.read_bytes() == b"previous"
        stage.write_bytes(b"new PNG")
        return 0

    monkeypatch.setattr(ocr, "_run_capture", run)
    assert ocr.capture_png(123, str(destination), enlarge=2, timeout=1.25)
    assert destination.read_bytes() == b"new PNG"
    assert seen == {"timeout": 1.25}
    assert not list(tmp_path.glob(".eda-capture-*"))


@pytest.mark.parametrize("failure", ["nonzero", "empty", "spawn", "timeout"])
def test_failed_capture_keeps_existing_destination_and_cleans_stage(
        monkeypatch, tmp_path, failure):
    monkeypatch.setattr(ocr, "available", lambda: True)
    destination = tmp_path / "capture.png"
    destination.write_bytes(b"previous capture")

    def run(command, **kwargs):
        if failure != "empty":
            Path(command[4]).write_bytes(b"incomplete PNG")
        if failure == "spawn":
            raise OSError("worker could not start")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return 1 if failure == "nonzero" else 0

    monkeypatch.setattr(ocr, "_run_capture", run)
    assert not ocr.capture_png(123, str(destination))
    assert destination.read_bytes() == b"previous capture"
    assert not list(tmp_path.glob(".eda-capture-*"))


def test_deadline_kills_and_reaps_a_real_stalled_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    destination = tmp_path / "capture.png"
    destination.write_bytes(b"previous capture")
    pid_file = tmp_path / "worker-pid.txt"
    code = (
        "import os,pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_bytes(b'partial'); "
        "pathlib.Path(sys.argv[2]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    monkeypatch.setattr(ocr, "_capture_command", lambda hwnd, path, enlarge:
                        [sys.executable, "-c", code, path, str(pid_file)])
    started = time.monotonic()
    assert not ocr.capture_png(123, str(destination), timeout=1.5)
    assert time.monotonic() - started < 8
    # The PID file proves this exercised a running, stalled worker rather
    # than a process-start failure. Cleanup must finish before return.
    assert not psutil.pid_exists(int(pid_file.read_text()))
    assert destination.read_bytes() == b"previous capture"
    assert not list(tmp_path.glob(".eda-capture-*"))


def test_timeout_also_terminates_a_worker_descendant(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    parent_pid = tmp_path / "parent-pid.txt"
    child_pid = tmp_path / "child-pid.txt"
    child_code = (
        "import os,pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_code = (
        "import os,pathlib,subprocess,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[2]],"
        "creationflags=0x08000000 if os.name=='nt' else 0); time.sleep(60)"
    )
    monkeypatch.setattr(ocr, "_capture_command", lambda hwnd, path, enlarge:
                        [sys.executable, "-c", parent_code, str(parent_pid),
                         str(child_pid), child_code])
    assert not ocr.capture_png(123, str(tmp_path / "capture.png"), timeout=2)
    assert parent_pid.exists() and child_pid.exists()
    assert not psutil.pid_exists(int(parent_pid.read_text()))
    assert not psutil.pid_exists(int(child_pid.read_text()))


@pytest.mark.parametrize("available, timeout", [(False, 1), (True, 0)])
def test_unavailable_capture_or_invalid_deadline_starts_nothing(
        monkeypatch, tmp_path, available, timeout):
    monkeypatch.setattr(ocr, "available", lambda: available)

    def unexpected(*args, **kwargs):
        pytest.fail("must not start a capture process")

    monkeypatch.setattr(ocr, "_run_capture", unexpected)
    assert not ocr.capture_png(123, str(tmp_path / "capture.png"), timeout=timeout)
    assert not list(tmp_path.iterdir())


def test_worker_arguments_success_failure_and_exceptions(monkeypatch):
    seen = []

    def capture(hwnd, path, enlarge):
        seen.append((hwnd, path, enlarge))
        return hwnd != 0

    monkeypatch.setattr(ocr, "_capture_png_inline", capture)
    assert ocr._capture_worker(["123", "out.png", "2"]) == 0
    assert seen == [(123, "out.png", 2)]
    assert ocr._capture_worker(["0", "out.png", "1"]) == 1
    assert ocr._capture_worker(["x", "out.png", "1"]) == 1
    assert ocr._capture_worker([]) == 2

    def error(*args):
        raise RuntimeError("target disappeared")

    monkeypatch.setattr(ocr, "_capture_png_inline", error)
    assert ocr._capture_worker(["123", "out.png", "1"]) == 1


def test_capture_launch_is_hidden_and_output_is_discarded(monkeypatch):
    seen = {}

    class Worker:
        def wait(self, timeout):
            assert timeout == 1.25
            return 0

    def launch(command, **kwargs):
        seen.update(kwargs)
        assert command == ['worker']
        return Worker()

    monkeypatch.setattr(ocr, 'no_window', lambda: {'creationflags': 0x08000000})
    monkeypatch.setattr(ocr.subprocess, 'Popen', launch)
    assert ocr._run_capture(['worker'], timeout=1.25) == 0
    assert seen == {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
                    'stderr': subprocess.DEVNULL, 'creationflags': 0x08000000}


@pytest.mark.parametrize("failure", ["child_kill", "root_kill", "root_wait", "child_wait", "survivor"])
def test_cleanup_failure_cannot_skip_root_or_replace_timeout(monkeypatch, caplog, failure):
    from unittest.mock import Mock

    original = subprocess.TimeoutExpired(["worker"], 0.1)
    proc = Mock(pid=123)
    proc.wait.side_effect = [original, subprocess.TimeoutExpired(["worker"], 1) if failure == "root_wait" else 0]
    child = Mock(pid=124)
    if failure == "child_kill":
        child.kill.side_effect = psutil.AccessDenied(pid=124)
    if failure == "root_kill":
        proc.kill.side_effect = PermissionError("denied")
    monkeypatch.setattr(ocr.subprocess, "Popen", lambda *a, **k: proc)
    parent = Mock()
    parent.children.return_value = [child]
    monkeypatch.setattr(ocr.psutil, "Process", lambda pid: parent)
    wait = Mock(return_value=([], [child]) if failure == "survivor" else ([child], []))
    if failure == "child_wait":
        wait.side_effect = psutil.AccessDenied(pid=124)
    monkeypatch.setattr(ocr.psutil, "wait_procs", wait)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        ocr._run_capture(["worker"], 0.1)
    assert caught.value is original
    proc.kill.assert_called_once()
    assert proc.wait.call_count == 2
    wait.assert_called_once_with([child], timeout=1)
    assert caplog.records


def test_worker_initializes_dpi_before_window_rectangle(monkeypatch):
    from types import SimpleNamespace

    seen = []
    def rectangle(hwnd):
        assert seen == ["dpi"]
        seen.append("rectangle")
        return (0, 0, 0, 0)
    monkeypatch.setattr(ocr.win, "_make_dpi_aware", lambda: seen.append("dpi"))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(GetWindowRect=rectangle))
    monkeypatch.setitem(sys.modules, "win32ui", SimpleNamespace())
    assert not ocr._capture_png_inline(123, "unused.png")
    assert seen == ["dpi", "rectangle"]
