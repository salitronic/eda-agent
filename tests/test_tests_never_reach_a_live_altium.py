# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""No test starts Altium, restarts its script loop or finds a running one.

A wait that runs out leads the bridge to its crash check, which finds
Altium by its process and restarts the loop. Run on a machine with
Altium open, that acts on the live session. conftest hides every Altium
process from a test and refuses to start X2.EXE or a relaunch.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from eda_agent.bridge import process_manager as pm


@pytest.mark.parametrize("command", [
    [r"C:\Program Files\Altium\AD26\X2.EXE", "-REditScript:Stop"],
    [sys.executable, "-m", "eda_agent.server", "relaunch", "--restart"],
    "X2.exe -RScriptingSystem:RunScript(ProjectName=x)",
])
def test_starting_altium_or_its_loop_is_refused(command):
    from tests.conftest import LaunchRefused
    with pytest.raises(LaunchRefused):
        subprocess.run(command, capture_output=True, timeout=5)


def test_other_programs_still_run():
    done = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True,
                          text=True, timeout=60)
    assert done.stdout.strip() == "1"


def test_no_altium_process_is_seen():
    manager = pm.AltiumProcessManager()
    assert manager.is_altium_running() is False
    assert manager.find_altium_process() is None
