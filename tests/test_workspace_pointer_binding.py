# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A process started without EDA_AGENT_WORKSPACE joins the bound workspace.

The pointer file tells Altium's script where to poll. A relaunch from a
terminal without the MCP server's override wrote the default there, so the
restarted loop served one directory while the server sent to another, and
every call timed out as though the loop were dead (GH #144).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent import config
from eda_agent.config import (
    CONFIG_FILE_NAME, WORKSPACE_POINTER_ENV, WORKSPACE_POINTER_FILE, AltiumConfig,
    pointer_mismatch, read_workspace_pointer,
)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A scratch pointer and profile, and no workspace override."""
    pointer = tmp_path / "workspace-path.txt"
    monkeypatch.setenv(WORKSPACE_POINTER_ENV, str(pointer))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.delenv("EDA_AGENT_WORKSPACE", raising=False)
    return tmp_path, pointer


def _pointer(pointer: Path) -> Path:
    return Path(pointer.read_text(encoding=config._pointer_encoding()).strip().rstrip("\\/"))


def _bind(machine, name="host-ipc") -> Path:
    """What the MCP server with its override leaves behind."""
    root, pointer = machine
    custom = root / name
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EDA_AGENT_WORKSPACE", str(custom))
        AltiumConfig().ensure_workspace()
    assert _pointer(pointer) == custom
    return custom


def test_a_process_without_the_override_keeps_the_bound_workspace(machine):
    root, pointer = machine
    custom = _bind(machine)
    cfg = AltiumConfig()
    assert cfg.workspace_dir == custom
    cfg.ensure_workspace()
    assert _pointer(pointer) == custom, "the relaunch repointed the bridge"
    assert not (root / "profile" / "EDA Agent" / "workspace").exists()


def test_an_explicit_override_still_moves_the_binding(machine, monkeypatch):
    root, pointer = machine
    _bind(machine)
    other = root / "other"
    monkeypatch.setenv("EDA_AGENT_WORKSPACE", str(other))
    cfg = AltiumConfig()
    cfg.ensure_workspace()
    assert cfg.workspace_dir == other and _pointer(pointer) == other


def test_a_pointer_to_no_workspace_falls_back_to_the_default(machine):
    root, pointer = machine
    pointer.write_text(str(root / "gone") + "\\", encoding=config._pointer_encoding())
    cfg = AltiumConfig()
    default = root / "profile" / "EDA Agent" / "workspace"
    assert cfg.workspace_dir == default
    # A folder without the runtime config is not a workspace either.
    (root / "bare").mkdir()
    pointer.write_text(str(root / "bare"), encoding=config._pointer_encoding())
    assert AltiumConfig().workspace_dir == default


def test_a_timeout_names_the_other_workspace(machine):
    root, _ = machine
    custom = _bind(machine)
    assert pointer_mismatch(custom) is None
    text = pointer_mismatch(root / "elsewhere")
    assert text and str(custom) in text and "EDA_AGENT_WORKSPACE" in text


def test_the_machine_pointer_is_never_read_under_pytest(monkeypatch):
    monkeypatch.delenv(WORKSPACE_POINTER_ENV, raising=False)
    assert config.workspace_pointer_file() == WORKSPACE_POINTER_FILE
    assert read_workspace_pointer() is None


def test_the_bound_workspace_holds_the_runtime_config(machine):
    custom = _bind(machine)
    assert (custom / CONFIG_FILE_NAME).is_file()
