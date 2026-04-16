# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Stress tests for the EDA Agent bridge.

Focus: real stress on the IPC boundary -- large payloads, encoding edge
cases, stale/corrupt response handling, concurrency hazards. Tests that
merely loop the simulator 100+ times (proving nothing new about bridge
behavior) have been removed.
"""

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.altium_simulator import (
    AltiumSimulator,
    MockSchObject,
    _build_success_response,
)
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest
from eda_agent.bridge.exceptions import (
    AltiumCommandError,
    AltiumTimeoutError,
)
from eda_agent.config import AltiumConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bridge(sim: AltiumSimulator, timeout: float = 5.0) -> AltiumBridge:
    """Build a real AltiumBridge wired to the given simulator."""
    config = AltiumConfig(
        workspace_dir=sim.workspace_dir,
        poll_interval=0.005,
        poll_timeout=timeout,
    )
    bridge = AltiumBridge.__new__(AltiumBridge)
    bridge.config = config
    bridge._attached = True

    class FakeProcessManager:
        def is_altium_running(self):
            return True

        def get_altium_info(self):
            from eda_agent.bridge.process_manager import AltiumProcessInfo
            return AltiumProcessInfo(pid=12345, name="X2.exe", exe_path="C:\\X2.exe")

    bridge.process_manager = FakeProcessManager()
    return bridge


def make_bare_bridge(workspace, timeout: float = 5.0) -> AltiumBridge:
    """Bridge with no simulator attached -- for tests that hand-write responses."""
    config = AltiumConfig(
        workspace_dir=workspace,
        poll_interval=0.01,
        poll_timeout=timeout,
    )
    bridge = AltiumBridge.__new__(AltiumBridge)
    bridge.config = config
    bridge._attached = True

    class FakePM:
        def is_altium_running(self):
            return True

        def get_altium_info(self):
            from eda_agent.bridge.process_manager import AltiumProcessInfo
            return AltiumProcessInfo(pid=1, name="X2.exe", exe_path="C:\\X2.exe")

    bridge.process_manager = FakePM()
    return bridge


# =========================================================================
# LARGE PAYLOADS
# =========================================================================

class TestLargePayloads:
    """Verify the IPC pipeline handles large data without truncation."""

    def test_query_returns_1000_objects(self, tmp_path):
        """Query 1000 objects through the bridge without truncation."""
        sim = AltiumSimulator(str(tmp_path))
        sim.sch_objects.clear()
        for i in range(1000):
            sim.sch_objects.append(
                MockSchObject(25, {"Text": f"NET_{i:04d}", "Location.X": str(i)})
            )
        sim.start()
        try:
            bridge = make_bridge(sim, timeout=15.0)
            result = bridge.send_command("generic.query_objects", {
                "scope": "active_doc",
                "object_type": "eNetLabel",
                "properties": "Text,Location.X",
            }, timeout=15.0)
            assert result["count"] == 1000
            assert len(result["objects"]) == 1000
            texts = {obj["Text"] for obj in result["objects"]}
            assert "NET_0000" in texts
            assert "NET_0999" in texts
        finally:
            sim.stop()

    def test_100kb_parameter_value(self, e2e_bridge):
        """Set a parameter with a 100KB value -- no truncation."""
        big_value = "A" * (100 * 1024)
        result = e2e_bridge.send_command("project.set_parameter", {
            "name": "BigParam",
            "value": big_value,
        }, timeout=10.0)
        assert result["success"] is True
        assert result["value"] == big_value

    def test_1mb_string_in_parameter(self, e2e_bridge):
        """1MB string in a parameter value -- must not crash."""
        mb_string = "X" * (1024 * 1024)
        result = e2e_bridge.send_command("project.set_parameter", {
            "name": "HugeParam",
            "value": mb_string,
        }, timeout=10.0)
        assert result["success"] is True

    def test_deeply_nested_json_50_levels(self, tmp_path):
        """Response with 50 levels of nesting -- bridge parses it."""
        sim = AltiumSimulator(str(tmp_path))

        original_dispatch = sim._dispatch

        def patched_dispatch(command, params, request_id):
            if command == "application.ping":
                inner = '"leaf"'
                for _ in range(50):
                    inner = '{"nested":' + inner + '}'
                return _build_success_response(request_id, inner)
            return original_dispatch(command, params, request_id)

        sim._dispatch = patched_dispatch
        sim.start()
        try:
            bridge = make_bridge(sim)
            result = bridge.send_command("application.ping", timeout=5.0)
            node = result
            for _ in range(50):
                assert "nested" in node
                node = node["nested"]
            assert node == "leaf"
        finally:
            sim.stop()


# =========================================================================
# ENCODING EDGE CASES
# =========================================================================

class TestEncodingEdgeCases:
    """Test the Latin-1 / UTF-8 boundary in the real IPC pipeline."""

    def test_latin1_accented_characters(self, tmp_path):
        """Latin-1 characters (accented letters) survive the IPC round trip."""
        sim = AltiumSimulator(str(tmp_path))

        original_dispatch = sim._dispatch

        def patched_dispatch(command, params, request_id):
            if command == "application.ping":
                text = "\u00e9\u00e8\u00ea\u00eb\u00f1\u00fc\u00e4\u00f6"
                return _build_success_response(request_id, '"' + text + '"')
            return original_dispatch(command, params, request_id)

        sim._dispatch = patched_dispatch
        sim.start()
        try:
            bridge = make_bridge(sim)
            result = bridge.send_command("application.ping", timeout=5.0)
            assert "\u00e9" in result
            assert "\u00f1" in result
        finally:
            sim.stop()

    def test_characters_0xa0_to_0xff(self, tmp_path):
        """Every printable Latin-1 char from 0xA0-0xFF survives the IPC."""
        sim = AltiumSimulator(str(tmp_path))

        original_dispatch = sim._dispatch

        def patched_dispatch(command, params, request_id):
            if command == "application.ping":
                chars = "".join(chr(c) for c in range(0xA0, 0x100))
                from tests.altium_simulator import _escape_json_string
                escaped = _escape_json_string(chars)
                return _build_success_response(request_id, '"' + escaped + '"')
            return original_dispatch(command, params, request_id)

        sim._dispatch = patched_dispatch
        sim.start()
        try:
            bridge = make_bridge(sim)
            result = bridge.send_command("application.ping", timeout=5.0)
            assert len(result) == 96
            assert result[0] == "\u00a0"
            assert result[-1] == "\u00ff"
        finally:
            sim.stop()

    def test_non_latin1_causes_timeout(self, e2e_bridge):
        """CJK characters cannot encode in Latin-1 -- simulator fails to write,
        bridge times out. Mirrors real Altium behavior with AnsiString."""
        with pytest.raises(AltiumTimeoutError):
            e2e_bridge.send_command("project.set_parameter", {
                "name": "CJKParam",
                "value": "\u4f60\u597d\u4e16\u754c",
            }, timeout=2.0)

    def test_null_byte_causes_timeout(self, e2e_bridge):
        """Null bytes crash the Latin-1 write path -- bridge times out gracefully."""
        with pytest.raises(AltiumTimeoutError):
            e2e_bridge.send_command("project.set_parameter", {
                "name": "NullTest",
                "value": "hello\x00world",
            }, timeout=2.0)

    def test_latin1_round_trip_via_set_and_get(self, tmp_path):
        """Set a Latin-1 value, then get it back -- must be identical."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        try:
            bridge = make_bridge(sim)
            latin1_value = "\u00e9l\u00e8ve caf\u00e9 na\u00efve"
            bridge.send_command("project.set_parameter", {
                "name": "French",
                "value": latin1_value,
            }, timeout=5.0)

            params = bridge.send_command("project.get_parameters", timeout=5.0)
            french = next((p for p in params if p["name"] == "French"), None)
            assert french is not None
            assert french["value"] == latin1_value
        finally:
            sim.stop()


# =========================================================================
# STALE RESPONSE HANDLING
# =========================================================================

class TestStaleResponseHandling:
    """The bridge must clean up stale responses from prior commands."""

    def test_stale_response_deleted(self, tmp_path):
        """A leftover response.json with wrong ID is deleted and retried."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        try:
            bridge = make_bridge(sim)
            response_path = sim.workspace_dir / "response.json"

            stale = json.dumps({
                "id": "stale-old-id",
                "success": True,
                "data": "stale_data",
                "error": None,
            })
            response_path.write_text(stale, encoding="latin-1")

            result = bridge.send_command("application.ping", timeout=5.0)
            assert result == "pong"
        finally:
            sim.stop()

    def test_multiple_stale_responses(self, tmp_path):
        """Multiple stale responses in sequence before the correct one arrives."""
        workspace = Path(tmp_path)
        workspace.mkdir(exist_ok=True)
        response_path = workspace / "response.json"
        request_id = str(uuid.uuid4())

        bridge = make_bare_bridge(workspace, timeout=5.0)

        def writer():
            for i in range(3):
                time.sleep(0.05)
                stale = json.dumps({
                    "id": f"stale-{i}",
                    "success": True,
                    "data": f"stale_{i}",
                    "error": None,
                })
                response_path.write_text(stale, encoding="latin-1")

            time.sleep(0.05)
            correct = json.dumps({
                "id": request_id,
                "success": True,
                "data": "correct_response",
                "error": None,
            })
            response_path.write_text(correct, encoding="latin-1")

        t = threading.Thread(target=writer, daemon=True)
        t.start()

        resp = bridge._poll_response(request_id, timeout=5.0)
        t.join(timeout=2.0)
        assert resp.success is True
        assert resp.data == "correct_response"


# =========================================================================
# CORRUPT / MALFORMED RESPONSE RECOVERY
# =========================================================================

class TestRecovery:
    """Bridge must degrade gracefully when the response file is corrupt."""

    def test_corrupt_response_times_out(self, tmp_path):
        """Non-JSON response -- bridge retries until timeout."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=1.0)

        response_path = workspace / "response.json"
        response_path.write_text("THIS IS NOT JSON{{{", encoding="latin-1")

        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response(str(uuid.uuid4()), timeout=1.0)

    def test_response_array_instead_of_object(self, tmp_path):
        """Valid JSON array (not object) -- bridge does not crash."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=1.0)

        response_path = workspace / "response.json"
        response_path.write_text("[1, 2, 3]", encoding="latin-1")

        with pytest.raises((AltiumTimeoutError, AttributeError, TypeError)):
            bridge._poll_response(str(uuid.uuid4()), timeout=1.0)

    def test_response_missing_fields(self, tmp_path):
        """Correct id but missing success/data/error -- defaults applied."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=2.0)

        response_path = workspace / "response.json"
        request_id = str(uuid.uuid4())
        response_path.write_text(json.dumps({"id": request_id}), encoding="latin-1")

        resp = bridge._poll_response(request_id, timeout=2.0)
        assert resp.id == request_id
        assert resp.success is False
        assert resp.data is None

    def test_empty_response_file_times_out(self, tmp_path):
        """Empty response.json -- bridge retries until timeout."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=1.0)

        response_path = workspace / "response.json"
        response_path.write_text("", encoding="latin-1")

        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response(str(uuid.uuid4()), timeout=1.0)

    def test_simulator_stopped_mid_command(self, tmp_path):
        """Simulator stops while bridge is waiting -- bridge times out gracefully."""
        sim = AltiumSimulator(str(tmp_path))
        sim._poll_interval = 0.5
        sim.start()
        bridge = make_bridge(sim, timeout=1.0)
        sim.stop()

        with pytest.raises(AltiumTimeoutError):
            bridge.send_command("application.ping", timeout=1.0)


# =========================================================================
# CONCURRENCY
# =========================================================================

class TestConcurrency:
    """Race conditions and concurrent access to IPC files."""

    def test_partial_write_then_complete(self, tmp_path):
        """Simulate a partially-written response followed by the full one."""
        workspace = Path(tmp_path)
        workspace.mkdir(exist_ok=True)
        response_path = workspace / "response.json"

        bridge = make_bare_bridge(workspace, timeout=5.0)

        request_id = str(uuid.uuid4())

        def delayed_write():
            time.sleep(0.05)
            response_path.write_text(
                '{"id":"' + request_id + '","succ',
                encoding="latin-1",
            )
            time.sleep(0.05)
            response_path.write_text(
                json.dumps({
                    "id": request_id,
                    "success": True,
                    "data": "recovered",
                    "error": None,
                }),
                encoding="latin-1",
            )

        writer = threading.Thread(target=delayed_write, daemon=True)
        writer.start()

        resp = bridge._poll_response(request_id, timeout=5.0)
        writer.join(timeout=2.0)
        assert resp.success is True
        assert resp.data == "recovered"

    def test_corrupt_request_file_cleaned_up(self, tmp_path):
        """Partial/garbage request.json -- simulator cleans up and continues."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        try:
            request_path = sim.workspace_dir / "request.json"
            request_path.write_text('{"id":"abc","comma', encoding="utf-8")
            time.sleep(0.1)

            bridge = make_bridge(sim)
            result = bridge.send_command("application.ping", timeout=5.0)
            assert result == "pong"
        finally:
            sim.stop()


# =========================================================================
# BOUNDARY / EDGE CASES
# =========================================================================

class TestBoundary:
    """Boundary conditions for bridge parameters."""

    def test_zero_timeout_raises_immediately(self, e2e_bridge):
        """Zero timeout -- AltiumTimeoutError without waiting."""
        with pytest.raises(AltiumTimeoutError):
            e2e_bridge.send_command("application.ping", timeout=0.0)

    def test_incrementing_ids_no_collision(self, tmp_path):
        """Auto-generated UUIDs are unique across many CommandRequest objects."""
        seen = set()
        for i in range(200):
            req = CommandRequest(command="application.ping")
            assert req.id not in seen, f"ID collision at {i}: {req.id}"
            seen.add(req.id)

    def test_command_injection_in_command_name(self, e2e_bridge):
        """Crafted command string -- bridge returns error, does not crash."""
        with pytest.raises(AltiumCommandError):
            e2e_bridge.send_command(
                'application.ping","evil":"true', timeout=3.0,
            )

    def test_json_injection_in_value(self, e2e_bridge):
        """JSON-looking content in a parameter value is treated as literal."""
        result = e2e_bridge.send_command("project.set_parameter", {
            "name": "InjTest",
            "value": 'value","injected":"true',
        }, timeout=5.0)
        assert result["success"] is True
        assert result["value"] == 'value","injected":"true'
