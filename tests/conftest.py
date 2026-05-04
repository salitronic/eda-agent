# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Shared fixtures for DelphiScript logic tests and end-to-end integration tests.

These tests validate the PURE LOGIC portions of the EDA Agent DelphiScript code
by reimplementing them in Python and testing against identical inputs/outputs.
Any divergence between the Python reimplementation and expected behavior IS a bug.
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.altium_simulator import AltiumSimulator, SIM_PROTOCOL_VERSION


@pytest.fixture
def workspace_dir():
    """Create a temporary workspace directory for IPC tests."""
    with tempfile.TemporaryDirectory(prefix="eda_agent_test_") as tmpdir:
        yield Path(tmpdir)


def request_path_for(workspace_dir: Path, request_id: str) -> Path:
    """Path to request_<id>.json in the workspace."""
    return workspace_dir / f"request_{request_id}.json"


def response_path_for(workspace_dir: Path, request_id: str) -> Path:
    """Path to response_<id>.json in the workspace."""
    return workspace_dir / f"response_{request_id}.json"


@pytest.fixture
def altium_sim(tmp_path):
    """Start an AltiumSimulator pointing at a temp workspace directory."""
    sim = AltiumSimulator(str(tmp_path))
    sim.start()
    yield sim
    sim.stop()


@pytest.fixture
def e2e_bridge(altium_sim):
    """Create a real AltiumBridge wired to the simulator's workspace.

    Calls the real ``AltiumBridge()`` constructor so every instance attribute
    is initialized identically to production. Bypassing ``__init__`` via
    ``__new__`` was the source of repeated test breakage as bridge internals
    evolved; don't reintroduce that pattern.
    """
    from eda_agent.config import AltiumConfig, MCPRuntimeConfig
    from eda_agent.bridge.altium_bridge import AltiumBridge

    test_config = AltiumConfig(
        workspace_dir=altium_sim.workspace_dir,
        runtime=MCPRuntimeConfig(
            py_poll_interval_seconds=0.01,
            py_poll_timeout_seconds=5.0,
        ),
    )

    class FakeProcessManager:
        def is_altium_running(self):
            return True

        def get_altium_info(self):
            from eda_agent.bridge.process_manager import AltiumProcessInfo
            return AltiumProcessInfo(pid=12345, name="X2.exe", exe_path="C:\\X2.exe")

    with patch("eda_agent.bridge.altium_bridge.get_config", return_value=test_config):
        bridge = AltiumBridge()

    bridge.process_manager = FakeProcessManager()
    bridge._attached = True
    yield bridge
    try:
        bridge.detach()
    except Exception:
        pass


def write_request(workspace: Path, request_id: str, command: str, params: dict) -> Path:
    """Write a request_<id>.json file in the exact format the bridge uses.

    Returns the path of the written file.
    """
    data = {
        "protocol_version": SIM_PROTOCOL_VERSION,
        "id": request_id,
        "command": command,
        "params": params,
    }
    path = request_path_for(workspace, request_id)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_response(workspace: Path, request_id: str, success: bool,
                   data=None, error=None) -> Path:
    """Write a response_<id>.json file in the exact format Altium produces."""
    resp = {
        "protocol_version": SIM_PROTOCOL_VERSION,
        "id": request_id,
        "success": success,
        "data": data,
        "error": error,
    }
    path = response_path_for(workspace, request_id)
    path.write_text(json.dumps(resp), encoding="utf-8")
    return path


def parse_response(path: Path) -> dict:
    """Read and parse a response_<id>.json file."""
    return json.loads(path.read_text(encoding="utf-8"))


def validate_success_response(resp: dict, request_id: str) -> None:
    """Assert that a response dict is a valid success response."""
    assert resp["id"] == request_id
    assert resp["success"] is True
    assert resp["error"] is None
    assert resp.get("protocol_version") == SIM_PROTOCOL_VERSION


def validate_error_response(resp: dict, request_id: str,
                            expected_code: str = None) -> None:
    """Assert that a response dict is a valid error response."""
    assert resp["id"] == request_id
    assert resp["success"] is False
    assert resp["data"] is None
    assert resp["error"] is not None
    assert "code" in resp["error"]
    assert "message" in resp["error"]
    if expected_code:
        assert resp["error"]["code"] == expected_code
