# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A test run must not send anything to a running Altium.

On 2026-09-23 a parallel run sent four read-only queries into a live
session that was in use in another window. The test passed bridge=None
and the orchestrator resolved the global bridge, whose config had been
built at import time from the real workspace. In a single-process run an
earlier test happened to swap that config for a temp one, which is the
only reason it had never shown.

Two layers now, checked here from the outside. Each probe runs in a
fresh pytest process, because the property is about what happens at
IMPORT time and this process imported everything long ago:

* conftest gives the whole session a scratch workspace before anything
  imports eda_agent, so a bridge resolved from the global config writes
  into a directory nothing polls;
* a test that leaves a bridge keep-alive running fails, which is the
  symptom the leak above showed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_HERE = Path(__file__).relative_to(_ROOT).as_posix()


def workspace_probe():
    """Collected only by the child runs below. Reports the import-time config."""
    from eda_agent import config

    print(f"\nPROBE_WORKSPACE={config.config.workspace_dir}\n")


def leaky_probe(tmp_path):
    """Collected only by the child runs below. Leaves a keep-alive running."""
    from unittest.mock import patch

    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import AltiumConfig

    with patch("eda_agent.bridge.altium_bridge.get_config",
               return_value=AltiumConfig(workspace_dir=tmp_path)):
        bridge = AltiumBridge()
    bridge._start_keepalive()


def _child(probe: str, **env_overrides: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("EDA_AGENT_WORKSPACE", "EDA_AGENT_TEST_WORKSPACE_ISOLATED",
                "EDA_AGENT_INTEGRATION"):
        env.pop(key, None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "pytest", _HERE, "-q", "-s", "--no-header",
         "-o", f"python_functions={probe}",
         "-p", "no:xdist", "-p", "no:randomly", "-p", "no:cacheprovider"],
        cwd=str(_ROOT), env=env, capture_output=True, text=True, timeout=300)


def _probed_workspace(result: subprocess.CompletedProcess) -> Path:
    for line in result.stdout.splitlines():
        if line.startswith("PROBE_WORKSPACE="):
            return Path(line.split("=", 1)[1])
    raise AssertionError("the probe did not run:\n" + result.stdout[-2000:])


def _real_default(monkeypatch) -> Path:
    from eda_agent import config

    monkeypatch.delenv("EDA_AGENT_WORKSPACE", raising=False)
    return config._default_workspace_dir()


def test_the_session_workspace_is_scratch_from_import_time(monkeypatch):
    got = _probed_workspace(_child("workspace_probe"))
    assert got != _real_default(monkeypatch), (
        "a test session's import-time config points at the real workspace, "
        "so any test that reaches the global bridge talks to a running "
        "Altium")
    assert Path(tempfile.gettempdir()).resolve() in got.resolve().parents, (
        f"the session workspace {got} is not under the temp directory")


def test_a_users_own_workspace_setting_is_not_trusted(tmp_path):
    """EDA_AGENT_WORKSPACE in a user's shell names their REAL workspace."""
    theirs = tmp_path / "users_real_workspace"
    got = _probed_workspace(_child("workspace_probe",
                                   EDA_AGENT_WORKSPACE=str(theirs)))
    assert got != theirs, (
        "the session kept an inherited EDA_AGENT_WORKSPACE that no parent "
        "test chose, which on a working machine is the live one")


def test_a_nested_session_keeps_the_workspace_its_parent_chose(tmp_path):
    """Or a parent that asserts its directory stays empty checks nothing."""
    chosen = tmp_path / "chosen"
    got = _probed_workspace(_child(
        "workspace_probe", EDA_AGENT_WORKSPACE=str(chosen),
        EDA_AGENT_TEST_WORKSPACE_ISOLATED="1"))
    assert got == chosen


def test_a_test_that_leaves_a_keepalive_running_fails():
    result = _child("leaky_probe")
    assert result.returncode != 0, (
        "a test left a bridge keep-alive running and the run still passed:\n"
        + result.stdout[-2000:])
    assert "keep-alive thread" in result.stdout, (
        "the run failed, but not on the keep-alive guard:\n"
        + result.stdout[-2000:])
