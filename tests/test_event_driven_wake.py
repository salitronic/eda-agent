# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Event-driven Win32 wake replaces high-frequency TTimer polling in Altium.

Altium's DelphiScript engine leaks ~2 KB of runtime identifier heap memory on
every TTimer event re-entry (measured at 5.2 GB across 329 15.81 MB heaps over
a 20-hour session on AD 26.1.1.7 at 30 ms idle polling). StatusForm now hosts
an off-screen TButton (``btn_Wake``) and drops a ``wake.ready`` marker in its
workspace; Python posts ``WM_COMMAND`` (``BN_CLICKED``) for that button after
publishing each request file, waking the pump immediately without stealing
focus and letting ``tmr_Poll`` drop to the 2 s watchdog cadence.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from eda_agent.bridge import altium_bridge as ab
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest
from eda_agent.bridge.process_manager import AltiumProcessInfo
from eda_agent.config import AltiumConfig, MCPRuntimeConfig
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _form() -> dict:
    return load(_ALTIUM / "StatusForm.pas")


def _main() -> dict:
    return load(_ALTIUM / "Main.pas")


def test_status_form_dfm_declares_offscreen_wake_button():
    dfm = (_ALTIUM / "StatusForm.dfm").read_text(encoding="utf-8")
    block = re.search(
        r"object btn_Wake: TButton(.*?)\n\s*(object |end)", dfm, re.S)
    assert block, "btn_Wake: TButton missing from StatusForm.dfm"
    body = block.group(1)
    assert "Caption = 'eda_wake'" in body
    assert "TabStop = False" in body
    assert "OnClick = btn_WakeClick" in body
    assert "Left = -100" in body


def test_pascal_wake_handler_and_watchdog_cadence():
    form = _form()
    wake = form["btn_WakeClick"]
    assert "PumpWakeSeen := True;" in wake
    assert "PumpWakeMissed := True;" in wake
    assert "tmr_PollTimer(Sender);" in wake

    tick = form["tmr_PollTimer"]
    assert "PumpWakeMissed := False;" in tick
    assert "KeepDraining := PumpWakeMissed;" in tick
    assert "PollIntervalWatchdogMs" in tick
    assert "StatusLastCommand <> 'application.ping'" in tick
    assert "STATS_REFRESH_MS" in tick

    start = form["StartMCPPump"]
    assert "PumpWakeSeen := False;" in start
    assert "PumpWakeMissed := False;" in start
    assert "WAKE_READY_FILE" in start

    fin = form["FinalisePump"]
    assert "WAKE_READY_FILE" in fin

    req = form["ProcessSingleRequest"]
    assert "If WorkspaceDir = '' Then EnsureWorkspaceDir(0);" in req


def test_watchdog_config_matches_between_python_and_pascal():
    main = _main()
    default_ms = MCPRuntimeConfig().poll_interval_watchdog_ms
    assert default_ms == 2000
    assert f"PollIntervalWatchdogMs := {default_ms};" in main["InitDefaultConfig"]
    assert "ExtractJsonValue(Content, 'poll_interval_watchdog_ms')" in main["LoadMCPConfig"]


def _make_bridge(tmp_path: Path, pid: int = 4242) -> AltiumBridge:
    cfg = AltiumConfig(workspace_dir=tmp_path)
    with patch("eda_agent.bridge.altium_bridge.get_config", return_value=cfg):
        bridge = AltiumBridge()

    class _FakePM:
        STATUS_FORM_TITLE = "EDA Agent MCP"

        def is_altium_running(self):
            return True

        def get_altium_info(self):
            return AltiumProcessInfo(pid=pid, name="X2.exe", exe_path="C:\\X2.exe")

    bridge.process_manager = _FakePM()
    return bridge


def test_wake_is_skipped_when_workspace_has_no_wake_marker(tmp_path, monkeypatch):
    """Isolated test workspaces and older Pascal scripts never trigger Win32
    window enumeration or PostMessageW."""
    bridge = _make_bridge(tmp_path)
    calls = []
    monkeypatch.setattr(ab, "_user32", object())
    monkeypatch.setattr(ab, "_find_wake_target",
                        lambda *a, **k: calls.append(("find", a, k)) or (1, 2))
    monkeypatch.setattr(ab, "_post_wake",
                        lambda *a, **k: calls.append(("post", a, k)) or True)

    req = CommandRequest(id="abc12345-00000001-deadbeef0001", command="application.ping")
    bridge._publish_request(req)
    assert calls == []
    assert bridge._wake_target is None


def test_publish_request_wakes_and_caches_target_with_pid_scoping(tmp_path, monkeypatch):
    bridge = _make_bridge(tmp_path, pid=7777)
    (tmp_path / ab._WAKE_READY_FILE).write_text(
        '{"caption":"EDA Agent MCP","button":"eda_wake"}', encoding="utf-8")

    finds = []
    posts = []
    monkeypatch.setattr(ab, "_user32", object())
    monkeypatch.setattr(ab, "_find_wake_target",
                        lambda prefix, pid=None: finds.append((prefix, pid)) or (100, 200))
    monkeypatch.setattr(ab, "_is_valid_wake_target",
                        lambda f, w, prefix: (f, w, prefix) == (100, 200, "EDA Agent MCP"))
    monkeypatch.setattr(ab, "_post_wake",
                        lambda f, w: posts.append((f, w)) or True)

    req1 = CommandRequest(id="abc12345-00000001-deadbeef0001", command="application.ping")
    req2 = CommandRequest(id="abc12345-00000002-deadbeef0002", command="application.ping")
    bridge._publish_request(req1)
    bridge._publish_request(req2)

    assert finds == [("EDA Agent MCP", 7777)]
    assert posts == [(100, 200), (100, 200)]


def test_stale_wake_target_is_rediscovered_after_recreate_wnd(tmp_path, monkeypatch):
    """Toggling Always-on-top calls VCL RecreateWnd and invalidates HWNDs;
    the bridge must re-discover the new handles rather than posting to old ones."""
    bridge = _make_bridge(tmp_path, pid=8888)
    (tmp_path / ab._WAKE_READY_FILE).write_text("{}", encoding="utf-8")
    bridge._wake_target = (100, 200)

    posts = []
    monkeypatch.setattr(ab, "_user32", object())
    monkeypatch.setattr(ab, "_is_valid_wake_target",
                        lambda f, w, prefix: (f, w) == (300, 400))
    monkeypatch.setattr(ab, "_find_wake_target",
                        lambda prefix, pid=None: (300, 400))
    monkeypatch.setattr(ab, "_post_wake",
                        lambda f, w: posts.append((f, w)) or True)

    assert bridge._wake_altium() is True
    assert bridge._wake_target == (300, 400)
    assert posts == [(300, 400)]


def test_is_valid_wake_target_rejects_recycled_or_foreign_handles(monkeypatch):
    class _FakeUser32:
        def __init__(self, windows, parents, texts):
            self._windows = windows
            self._parents = parents
            self._texts = texts

        def IsWindow(self, h):
            return h in self._windows

        def GetParent(self, h):
            return self._parents.get(h, 0)

    texts = {10: "EDA Agent MCP", 20: "eda_wake", 30: "Notepad", 40: "OK"}
    fake = _FakeUser32(
        windows={10, 20, 30, 40},
        parents={20: 10, 40: 30},
        texts=texts,
    )
    monkeypatch.setattr(ab, "_user32", fake)
    monkeypatch.setattr(ab, "_win32_window_text", lambda h: texts.get(h, ""))

    assert ab._is_valid_wake_target(10, 20, "EDA Agent MCP") is True
    assert ab._is_valid_wake_target(30, 40, "EDA Agent MCP") is False
    assert ab._is_valid_wake_target(10, 40, "EDA Agent MCP") is False
    assert ab._is_valid_wake_target(10, 999, "EDA Agent MCP") is False
