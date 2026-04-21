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

from tests.altium_simulator import AltiumSimulator


@pytest.fixture
def workspace_dir():
    """Create a temporary workspace directory for IPC tests."""
    with tempfile.TemporaryDirectory(prefix="eda_agent_test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def request_path(workspace_dir):
    """Path to the request.json file in the workspace."""
    return workspace_dir / "request.json"


@pytest.fixture
def response_path(workspace_dir):
    """Path to the response.json file in the workspace."""
    return workspace_dir / "response.json"


@pytest.fixture
def altium_sim(tmp_path):
    """Start an AltiumSimulator pointing at a temp workspace directory.

    Yields the simulator and stops it on teardown.
    """
    sim = AltiumSimulator(str(tmp_path))
    sim.start()
    yield sim
    sim.stop()


@pytest.fixture
def e2e_bridge(altium_sim):
    """Create a real AltiumBridge wired to the simulator's workspace.

    Calls the real ``AltiumBridge()`` constructor so every instance attribute
    (``_ipc_lock``, ``_attach_time``, ``_detach_hint_shown``, keep-alive state,
    etc.) is initialized identically to production. Bypassing ``__init__`` via
    ``__new__`` was the source of repeated test breakage as bridge internals
    evolved; don't reintroduce that pattern.

    Patches:
    - Config workspace_dir to point at the simulator's temp directory
    - process_manager so ``is_altium_running()`` returns True (no real
      Altium process needed for pure IPC tests)
    """
    from eda_agent.config import AltiumConfig
    from eda_agent.bridge.altium_bridge import AltiumBridge

    test_config = AltiumConfig(
        workspace_dir=altium_sim.workspace_dir,
        poll_interval=0.01,
        poll_timeout=5.0,
    )

    class FakeProcessManager:
        def is_altium_running(self):
            return True

        def get_altium_info(self):
            from eda_agent.bridge.process_manager import AltiumProcessInfo
            return AltiumProcessInfo(pid=12345, name="X2.exe", exe_path="C:\\X2.exe")

    # Stub get_config so AltiumBridge.__init__ sees our test workspace.
    with patch("eda_agent.bridge.altium_bridge.get_config", return_value=test_config):
        bridge = AltiumBridge()

    bridge.process_manager = FakeProcessManager()
    bridge._attached = True
    yield bridge
    # Make sure the keep-alive thread doesn't outlive the test.
    try:
        bridge.detach()
    except Exception:
        pass


def write_request(path: Path, request_id: str, command: str, params: dict) -> None:
    """Write a request.json file in the exact format the bridge uses.

    Mirrors: altium_bridge.py CommandRequest.to_dict()
    """
    data = {
        "id": request_id,
        "command": command,
        "params": params,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def write_response(path: Path, request_id: str, success: bool,
                   data=None, error=None) -> None:
    """Write a response.json file in the exact format Altium produces.

    Mirrors: Main.pas BuildSuccessResponse / BuildErrorResponse
    """
    resp = {
        "id": request_id,
        "success": success,
        "data": data,
        "error": error,
    }
    path.write_text(json.dumps(resp), encoding="utf-8")


def parse_response(path: Path) -> dict:
    """Read and parse a response.json file."""
    return json.loads(path.read_text(encoding="utf-8"))


def validate_success_response(resp: dict, request_id: str) -> None:
    """Assert that a response dict is a valid success response."""
    assert resp["id"] == request_id
    assert resp["success"] is True
    assert resp["error"] is None


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
