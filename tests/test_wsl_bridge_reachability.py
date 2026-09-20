# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Running under WSL must not make the bridge unreachable.

The server can run in a WSL distro while Altium runs on the Windows
desktop; the two meet over files in a shared workspace.  Three things in
this codebase assumed the two sides shared an OS, and each one failed
silently rather than loudly:

* ``is_altium_running()`` gates every ``send_command``.  Under WSL the
  Toolhelp scan is unavailable and psutil enumerates the LINUX namespace,
  which a Windows desktop process is not in, so the gate refused every
  call while the polling loop was healthy and answering.
* ``write_workspace_pointer`` wrote ``str(workspace_dir)``, which under
  WSL is a POSIX path that the DelphiScript side, a Windows process,
  cannot resolve.
* ...and wrote it with the ``mbcs`` codec, which does not exist off
  Windows, so the write raised ``LookupError`` inside a best-effort
  handler and the pointer file was never written at all.

Reported in GH #32.  The tests below run on any platform: they drive the
decision functions directly rather than requiring a WSL host.
"""

from __future__ import annotations

import codecs
import subprocess
import sys
from pathlib import Path

import pytest

from eda_agent import config as cfg
from eda_agent.bridge import process_manager as pm


# --------------------------------------------------------------------------
# "cannot look" must never be recorded as "not running"
# --------------------------------------------------------------------------

def test_unreachable_interop_does_not_report_altium_absent(monkeypatch):
    """No interop under WSL means unknown, and unknown must not block.

    This is the whole GH #32 report in one assertion.  Returning False
    here is not a measurement, it is a guess about a process table this
    process cannot see, and it refuses every command before sending it
    while Altium is running and the loop is answering.
    """
    monkeypatch.setattr(pm, "_under_wsl", lambda: True)
    # Native scan unavailable (as it is off Windows) and interop dead.
    monkeypatch.setattr(pm, "_scan_process_names_native", lambda wanted: None)
    monkeypatch.setattr(pm, "_windows_tasklist_rows", lambda: None)
    # psutil would answer False here; if it is ever consulted on this path
    # the bug is back, so make it loud rather than let it quietly win.
    monkeypatch.setattr(
        pm.psutil, "process_iter",
        lambda *a, **k: pytest.fail(
            "psutil was consulted under WSL: it enumerates the Linux "
            "namespace, where a Windows Altium can never appear, so its "
            "answer is always a false negative"))

    manager = pm.AltiumProcessManager()
    manager._running_cache = None
    assert manager._scan_running() is True, (
        "under WSL with no interop the process table cannot be consulted "
        "at all. Reporting False refuses every command up front; the "
        "request itself must be allowed through so a real absence "
        "surfaces as the IPC timeout, which names the fault.")


def test_interop_answer_is_used_when_it_is_available(monkeypatch):
    """When tasklist does answer, its answer decides, both ways."""
    monkeypatch.setattr(pm, "_under_wsl", lambda: True)
    monkeypatch.setattr(pm, "_scan_process_names_native", lambda wanted: None)

    monkeypatch.setattr(
        pm, "_windows_tasklist_rows",
        lambda: [(4, "System"), (33352, "X2.EXE")])
    manager = pm.AltiumProcessManager()
    manager._running_cache = None
    assert manager._scan_running() is True

    # An empty list is a real measurement: we looked, Altium is not there.
    monkeypatch.setattr(pm, "_windows_tasklist_rows", lambda: [(4, "System")])
    manager = pm.AltiumProcessManager()
    manager._running_cache = None
    assert manager._scan_running() is False, (
        "a tasklist that came back without Altium is evidence of absence, "
        "unlike a tasklist that could not run at all")


def test_plain_linux_keeps_the_honest_negative(monkeypatch):
    """Not every non-Windows host is WSL.

    On plain Linux there is no Windows side, so "not running" is the
    correct answer and the assume-reachable arm must not fire.
    """
    monkeypatch.setattr(pm, "_under_wsl", lambda: False)
    monkeypatch.setattr(pm, "_scan_process_names_native", lambda wanted: None)
    monkeypatch.setattr(pm, "_windows_tasklist_rows", lambda: None)
    monkeypatch.setattr(pm.psutil, "process_iter", lambda *a, **k: iter(()))

    manager = pm.AltiumProcessManager()
    manager._running_cache = None
    assert manager._scan_running() is False


def test_tasklist_failure_is_none_not_empty(monkeypatch):
    """A tasklist that cannot run returns None, never ``[]``.

    ``[]`` means "looked, found nothing" and legitimately reports Altium
    absent.  Collapsing the two is how a failed scan becomes a confident
    wrong answer.
    """
    def _boom(*a, **k):
        raise OSError("Exec format error")

    monkeypatch.setattr(pm.subprocess, "run", _boom)
    monkeypatch.setattr(pm, "_WSL_TASKLIST", Path("/mnt/c/anything.exe"))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(sys, "platform", "linux")
    assert pm._windows_tasklist_rows() is None


def test_interop_scan_is_not_slower_than_the_gate_tolerates():
    """The tasklist timeout sits inside the pre-command gate.

    ``is_altium_running`` runs before every send, cached only briefly, so
    a wedged interop becomes command latency. Keep the ceiling low.
    """
    assert pm._TASKLIST_TIMEOUT_S <= 10, (
        f"tasklist timeout is {pm._TASKLIST_TIMEOUT_S}s and runs inside "
        f"the gate before every command; a hung interop would stall calls "
        f"for that long")


# --------------------------------------------------------------------------
# the pointer file has to be readable by a Windows process
# --------------------------------------------------------------------------

def test_pointer_is_translated_to_a_windows_path_under_wsl(monkeypatch):
    """DelphiScript is a Windows process and cannot open ``/mnt/c/...``."""
    monkeypatch.setattr(cfg, "_under_wsl", lambda: True)

    def _fake_run(argv, capture_output=False, timeout=None):
        assert argv[:2] == ["wslpath", "-w"], argv
        return subprocess.CompletedProcess(
            argv, 0, stdout=b"C:\\ProgramData\\eda-agent\\workspace\n",
            stderr=b"")

    monkeypatch.setattr(cfg.subprocess, "run", _fake_run)
    out = cfg._windows_path_str(Path("/mnt/c/ProgramData/eda-agent/workspace"))
    assert out == "C:\\ProgramData\\eda-agent\\workspace", (
        "the pointer must carry a path the Windows side can open; a POSIX "
        "path there resolves to nothing and the loop never finds the "
        "workspace")
    assert "/mnt/" not in out


def test_pointer_encoding_exists_off_windows(monkeypatch):
    """``mbcs`` is Windows-only and raised inside a best-effort handler.

    The failure mode is the nastiest kind: no exception reaches the
    caller, and the pointer file simply never appears.

    The platform is monkeypatched rather than read.  CI runs on Windows
    only, so a test gated on the real ``sys.platform`` would assert
    nothing at all about the arm that broke, which is the off-Windows
    one.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    assert cfg._pointer_encoding() == "mbcs", (
        "the Windows ANSI codepage is still the right answer on Windows")

    monkeypatch.setattr(sys, "platform", "linux")
    enc = cfg._pointer_encoding()
    assert enc != "mbcs", (
        "mbcs does not exist off Windows; using it there raises "
        "LookupError, which write_workspace_pointer swallows, leaving no "
        "pointer file at all and no sign that anything went wrong")
    codecs.lookup(enc)
    "C:\\ProgramData\\eda-agent\\workspace\\".encode(enc)


def test_pointer_write_survives_a_non_windows_host(monkeypatch, tmp_path):
    """End to end: the file exists and holds a usable Windows path."""
    target = tmp_path / "workspace-path.txt"
    monkeypatch.setenv(cfg.WORKSPACE_POINTER_ENV, str(target))
    monkeypatch.setattr(cfg, "_under_wsl", lambda: True)
    monkeypatch.setattr(
        cfg, "_windows_path_str",
        lambda p: "C:\\ProgramData\\eda-agent\\workspace")

    cfg.write_workspace_pointer(Path("/mnt/c/ProgramData/eda-agent/workspace"))

    assert target.exists(), (
        "no pointer file was written at all, which is what the mbcs "
        "LookupError did under WSL")
    body = target.read_bytes().decode(cfg._pointer_encoding())
    assert body == "C:\\ProgramData\\eda-agent\\workspace\\", body


# --------------------------------------------------------------------------
# the workspace itself must be reachable from both sides
# --------------------------------------------------------------------------

def test_wsl_workspace_default_is_not_on_the_distro_filesystem(monkeypatch):
    """A default on ext4 leads users straight into the 9P failure.

    ``USERPROFILE`` is unset under WSL, so without this the default falls
    through to ``Path.home()``, which Windows can only reach over the 9P
    share -- the setup GH #32 reports as REQUEST_UNREADABLE.
    """
    monkeypatch.delenv("EDA_AGENT_WORKSPACE", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(cfg, "_under_wsl", lambda: True)

    got = cfg._default_workspace_dir()
    # as_posix(), because this test also runs on Windows, where Path()
    # renders the same string with backslashes and a naive prefix check
    # fails on a path that is perfectly correct.
    assert got.as_posix().startswith("/mnt/"), (
        f"default workspace under WSL is {got}, which is on the distro "
        f"filesystem. Windows reaches that only over 9P, and the Altium "
        f"side then enumerates request files it cannot read.")


def test_under_wsl_is_false_on_windows():
    """The WSL arms must never fire on a Windows host."""
    if sys.platform == "win32":
        assert cfg._under_wsl() is False
        assert pm._under_wsl() is False
