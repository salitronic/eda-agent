# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The headless shot runs in a profile of its own and cannot hang the call.

With the user's Edge open, a headless Edge without its own profile can
join the running browser and never exit, so every PNG ran into the 60 s
timeout (#301).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from eda_agent.render import rasterize


def test_the_command_carries_a_profile_of_its_own():
    cmd = rasterize.build_screenshot_cmd("edge.exe", "a.svg", "a.png", 10, 10,
                                         profile_dir=r"C:\t\p")
    assert r"--user-data-dir=C:\t\p" in cmd
    assert "--no-first-run" in cmd and cmd[-1].startswith("file:")


def test_a_browser_that_does_not_exit_is_stopped_and_its_profile_removed(
        tmp_path, monkeypatch):
    svg = tmp_path / "a.svg"
    svg.write_text("<svg/>")
    seen = {}

    class Stuck:
        pid = 1

        def __init__(self, cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs

        def wait(self, timeout=None):
            if "killed" not in seen:
                raise subprocess.TimeoutExpired("edge", timeout)

        def kill(self):
            seen["killed"] = True

    monkeypatch.setattr(rasterize, "find_browser", lambda p=None: "edge.exe")
    monkeypatch.setattr(rasterize.subprocess, "Popen", Stuck)
    monkeypatch.setattr(rasterize.subprocess, "run", lambda *a, **k: None)
    out = rasterize.rasterize_svg(str(svg), timeout=0.1)
    assert out["ok"] is False and "did not exit" in out["reason"]
    assert seen["killed"]
    assert seen["kwargs"]["stdout"] is subprocess.DEVNULL
    profile = next(a for a in seen["cmd"] if a.startswith("--user-data-dir="))
    assert not Path(profile.split("=", 1)[1]).exists()
