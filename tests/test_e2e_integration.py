# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""End-to-end bridge integration tests.

These tests exercise the real AltiumBridge, the real file-based IPC, and
the AltiumSimulator lifecycle. Tests that merely verify the simulator
returns what the simulator defines as its own response have been removed --
those are tautologies, not behavior tests.

What remains:
  - Bridge mechanics (sync/async send, stale-response cleanup, error raising)
  - Simulator lifecycle (start/stop, malformed/empty request handling)
  - Latin-1 encoding at the IPC boundary (the one non-obvious behavior)
"""

import asyncio
import json
import time

import pytest

from tests.altium_simulator import AltiumSimulator
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest
from eda_agent.bridge.exceptions import AltiumCommandError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _event_loop():
    """Ensure there is an event loop for async tests."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


# =========================================================================
# BRIDGE INTEGRATION
# =========================================================================

class TestBridgeIntegration:
    """Test the bridge itself works correctly against a live simulator."""

    def test_bridge_sync_command(self, e2e_bridge):
        """send_command (sync) works end-to-end."""
        result = e2e_bridge.send_command("application.ping", timeout=5.0)
        assert result == "pong"

    def test_bridge_async_command(self, e2e_bridge):
        """send_command_async works end-to-end."""
        result = run_async(
            e2e_bridge.send_command_async("application.ping", timeout=5.0)
        )
        assert result == "pong"

    def test_bridge_ping_method(self, e2e_bridge):
        """Bridge.ping() returns True when simulator is running."""
        assert e2e_bridge.ping() is True

    def test_bridge_command_error_raised(self, e2e_bridge):
        """Bridge raises AltiumCommandError for error responses."""
        with pytest.raises(AltiumCommandError):
            e2e_bridge.send_command("application.unknown_action", timeout=5.0)

    def test_bridge_error_has_code_and_message(self, e2e_bridge):
        """Error responses produce AltiumCommandError with populated fields."""
        with pytest.raises(AltiumCommandError) as exc_info:
            e2e_bridge.send_command("application.unknown_action", timeout=5.0)
        error = exc_info.value
        assert error.code
        assert error.message

    def test_bridge_clears_stale_response(self, e2e_bridge):
        """Bridge clears stale response files before sending new request."""
        # Write a stale response
        stale_path = e2e_bridge.config.response_path
        stale_data = {
            "id": "stale-id-000",
            "success": True,
            "data": "stale",
            "error": None,
        }
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stale_path, "w") as f:
            json.dump(stale_data, f)

        # The next command should still work (bridge clears stale response)
        result = e2e_bridge.send_command("application.ping", timeout=5.0)
        assert result == "pong"

    def test_unknown_command_category_raises(self, e2e_bridge):
        """Completely unknown category -> UNKNOWN_COMMAND error from simulator."""
        with pytest.raises(AltiumCommandError) as exc_info:
            e2e_bridge.send_command("bogus.action", timeout=5.0)
        assert exc_info.value.code == "UNKNOWN_COMMAND"

    def test_no_dot_in_command_raises(self, e2e_bridge):
        """Command without dot -> UNKNOWN_COMMAND error."""
        with pytest.raises(AltiumCommandError) as exc_info:
            e2e_bridge.send_command("nodotcommand", timeout=5.0)
        assert exc_info.value.code == "UNKNOWN_COMMAND"


# =========================================================================
# LATIN-1 ENCODING AT THE IPC BOUNDARY
# =========================================================================

class TestLatin1Encoding:
    """Verify the latin-1 boundary that exists in the real pipeline.

    Real Altium writes response.json in Latin-1 (DelphiScript AnsiString).
    The bridge reads with encoding='latin-1'. These tests verify that
    boundary holds.
    """

    def test_response_is_latin1(self, altium_sim):
        """Verify the simulator writes latin-1 encoded responses."""
        request_path = altium_sim.workspace_dir / "request.json"
        response_path = altium_sim.workspace_dir / "response.json"

        request_data = {
            "id": "test-encoding-001",
            "command": "application.ping",
            "params": {},
        }
        with open(request_path, "w", encoding="utf-8") as f:
            json.dump(request_data, f)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if response_path.exists():
                break
            time.sleep(0.01)

        assert response_path.exists()
        # Read with latin-1 -- this must not raise
        with open(response_path, "r", encoding="latin-1") as f:
            data = json.load(f)
        assert data["id"] == "test-encoding-001"
        assert data["success"] is True


# =========================================================================
# SIMULATOR LIFECYCLE
# =========================================================================

class TestSimulatorLifecycle:
    """Test the simulator's own start/stop/cleanup behavior."""

    def test_start_and_stop(self, tmp_path):
        """Simulator starts and stops cleanly."""
        sim = AltiumSimulator(str(tmp_path))
        assert sim.running is False
        sim.start()
        assert sim.running is True
        sim.stop()
        assert sim.running is False

    def test_stop_via_stop_file(self, tmp_path):
        """Simulator stops when 'stop' file appears."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        assert sim.running is True

        (tmp_path / "stop").write_text("", encoding="utf-8")
        time.sleep(0.2)
        assert sim.running is False
        sim.stop()

    def test_cleanup_removes_ipc_files(self, tmp_path):
        """Simulator cleanup removes request.json and response.json."""
        sim = AltiumSimulator(str(tmp_path))
        (tmp_path / "request.json").write_text("{}", encoding="utf-8")
        (tmp_path / "response.json").write_text("{}", encoding="utf-8")

        sim.start()
        sim.stop()

        assert not (tmp_path / "request.json").exists()
        assert not (tmp_path / "response.json").exists()

    def test_double_start_is_idempotent(self, tmp_path):
        """Starting twice does not create duplicate threads."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        thread1 = sim._thread
        sim.start()  # second start should be no-op
        assert sim._thread is thread1
        sim.stop()

    def test_malformed_request_ignored(self, tmp_path):
        """Simulator handles malformed JSON gracefully."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()

        (tmp_path / "request.json").write_text("not json at all", encoding="utf-8")
        time.sleep(0.1)

        assert not (tmp_path / "request.json").exists()
        assert sim.running is True
        sim.stop()

    def test_empty_request_ignored(self, tmp_path):
        """Simulator handles empty request file gracefully."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()

        (tmp_path / "request.json").write_text("", encoding="utf-8")
        time.sleep(0.1)

        assert not (tmp_path / "request.json").exists()
        assert sim.running is True
        sim.stop()

    def test_stop_server_command_stops_simulator(self, altium_sim, e2e_bridge):
        """application.stop_server command halts the simulator."""
        assert altium_sim.running is True
        result = e2e_bridge.send_command("application.stop_server", timeout=5.0)
        assert result["stopped"] is True
        time.sleep(0.1)
        assert altium_sim.running is False
