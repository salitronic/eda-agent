# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""End-to-end bridge integration tests.

These tests exercise the real AltiumBridge, the real per-request file IPC,
and the AltiumSimulator lifecycle.

What we verify:
  - Bridge mechanics (sync/async send, error raising, protocol versioning)
  - Simulator lifecycle (start/stop, malformed/empty request handling)
  - UTF-8 encoding at the IPC boundary
"""

import asyncio
import json
import time

import pytest

from tests.altium_simulator import AltiumSimulator, SIM_PROTOCOL_VERSION
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest, PROTOCOL_VERSION
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

    def test_unrelated_response_invisible(self, e2e_bridge):
        """A foreign caller's response file does not interfere with our poll."""
        # Per-request files: this file belongs to no current request and the
        # bridge should never poll for it.
        ws = e2e_bridge.config.workspace_dir
        ws.mkdir(parents=True, exist_ok=True)
        foreign = ws / "response_foreigncaller.json"
        foreign.write_text(
            json.dumps({
                "protocol_version": PROTOCOL_VERSION,
                "id": "foreigncaller",
                "success": True,
                "data": "stale",
                "error": None,
            }),
            encoding="utf-8",
        )

        result = e2e_bridge.send_command("application.ping", timeout=5.0)
        assert result == "pong"
        # The unrelated file is left alone — we never touch responses we don't own.
        assert foreign.exists()

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
# UTF-8 ENCODING AT THE IPC BOUNDARY
# =========================================================================

class TestEncoding:
    """Verify both sides write/read UTF-8 with no encoding ambiguity.

    Pascal escapes any non-ASCII byte as \\u00XX so output is pure ASCII;
    it is therefore valid UTF-8 by construction. Python writes UTF-8.
    """

    def test_response_is_utf8(self, altium_sim):
        """Simulator writes UTF-8 responses readable as JSON."""
        rid = "testencoding001"
        request_path = altium_sim.workspace_dir / f"request_{rid}.json"
        response_path = altium_sim.workspace_dir / f"response_{rid}.json"

        request_data = {
            "protocol_version": SIM_PROTOCOL_VERSION,
            "id": rid,
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
        with open(response_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["id"] == rid
        assert data["success"] is True
        assert data["protocol_version"] == SIM_PROTOCOL_VERSION


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
        """Simulator cleanup removes leftover per-request files on stop."""
        sim = AltiumSimulator(str(tmp_path))
        (tmp_path / "request_leftover.json").write_text("{}", encoding="utf-8")
        (tmp_path / "response_leftover.json").write_text("{}", encoding="utf-8")

        sim.start()
        sim.stop()

        assert not list(tmp_path.glob("request_*.json"))
        assert not list(tmp_path.glob("response_*.json"))

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

        (tmp_path / "request_malformed1.json").write_text("not json at all", encoding="utf-8")
        time.sleep(0.1)

        assert not (tmp_path / "request_malformed1.json").exists()
        assert sim.running is True
        sim.stop()

    def test_empty_request_ignored(self, tmp_path):
        """Simulator handles empty request file gracefully."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()

        (tmp_path / "request_empty1.json").write_text("", encoding="utf-8")
        time.sleep(0.1)

        assert not (tmp_path / "request_empty1.json").exists()
        assert sim.running is True
        sim.stop()

    def test_stop_server_command_stops_simulator(self, altium_sim, e2e_bridge):
        """application.stop_server command halts the simulator."""
        assert altium_sim.running is True
        result = e2e_bridge.send_command("application.stop_server", timeout=5.0)
        assert result["stopped"] is True
        time.sleep(0.1)
        assert altium_sim.running is False
