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

def _build_bridge(workspace, poll_interval: float, timeout: float) -> AltiumBridge:
    """Construct a real AltiumBridge via its normal __init__.

    Previously these helpers used ``AltiumBridge.__new__(AltiumBridge)`` to
    skip construction, then assigned a couple of attributes. Every time
    the bridge gained a new field (``_ipc_lock``, ``_attach_time``,
    ``_detach_hint_shown``, ``_keepalive_thread``, ...) this pattern
    silently left it unset and the next send_command raised
    AttributeError. Use the real constructor with get_config patched.
    """
    from unittest.mock import patch

    from eda_agent.config import MCPRuntimeConfig

    test_config = AltiumConfig(
        workspace_dir=workspace,
        runtime=MCPRuntimeConfig(
            py_poll_interval_seconds=poll_interval,
            py_poll_timeout_seconds=timeout,
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
    return bridge


def make_bridge(sim: AltiumSimulator, timeout: float = 5.0) -> AltiumBridge:
    """Build a real AltiumBridge wired to the given simulator."""
    return _build_bridge(sim.workspace_dir, poll_interval=0.005, timeout=timeout)


def make_bare_bridge(workspace, timeout: float = 5.0) -> AltiumBridge:
    """Bridge with no simulator attached -- for tests that hand-write responses."""
    return _build_bridge(workspace, poll_interval=0.01, timeout=timeout)


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

    def test_cjk_round_trips_via_utf8(self, e2e_bridge):
        """CJK characters round-trip cleanly under UTF-8.

        Old behaviour: Latin-1 encoding would have caused a UnicodeEncodeError
        on the write side and a bridge timeout. The new envelope is UTF-8
        on the Python side and \\uXXXX-escaped from Pascal, so any Unicode
        codepoint in a parameter value comes back intact.
        """
        cjk = "\u4f60\u597d\u4e16\u754c"
        result = e2e_bridge.send_command("project.set_parameter", {
            "name": "CJKParam",
            "value": cjk,
        }, timeout=5.0)
        assert result["success"] is True
        assert result["value"] == cjk

    def test_null_byte_causes_timeout(self, e2e_bridge):
        """Null bytes still crash JSON encoders — bridge times out cleanly."""
        with pytest.raises((AltiumTimeoutError, ValueError)):
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

class TestForeignResponseIsolation:
    """With per-request files, another caller's response file is invisible
    to our poll. There is no stale-response handling to test — the file
    we poll for either appears (matching ID) or doesn't."""

    def test_foreign_response_does_not_disturb_us(self, tmp_path):
        """A foreign response file in the workspace does not affect our poll."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        try:
            bridge = make_bridge(sim)
            foreign = sim.workspace_dir / "response_foreigncaller.json"
            foreign.write_text(
                json.dumps({
                    "protocol_version": 2, "id": "foreigncaller",
                    "success": True, "data": "stale", "error": None,
                }),
                encoding="utf-8",
            )

            result = bridge.send_command("application.ping", timeout=5.0)
            assert result == "pong"
            assert foreign.exists(), "Foreign caller's file must be untouched"
        finally:
            sim.stop()


# =========================================================================
# CORRUPT / MALFORMED RESPONSE RECOVERY
# =========================================================================

class TestRecovery:
    """Bridge must degrade gracefully when the response file is corrupt."""

    def test_corrupt_response_times_out(self, tmp_path):
        """Non-JSON response — bridge retries until timeout."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=1.0)

        request_id = uuid.uuid4().hex
        response_path = workspace / f"response_{request_id}.json"
        response_path.write_text("THIS IS NOT JSON{{{", encoding="utf-8")

        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response(request_id, timeout=1.0)

    def test_response_array_instead_of_object(self, tmp_path):
        """Valid JSON array (not object) — bridge does not crash."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=1.0)

        request_id = uuid.uuid4().hex
        response_path = workspace / f"response_{request_id}.json"
        response_path.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises((AltiumTimeoutError, AttributeError, TypeError)):
            bridge._poll_response(request_id, timeout=1.0)

    def test_response_missing_fields(self, tmp_path):
        """Correct id but missing success/data/error — defaults applied."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=2.0)

        request_id = uuid.uuid4().hex
        response_path = workspace / f"response_{request_id}.json"
        response_path.write_text(json.dumps({"id": request_id}), encoding="utf-8")

        resp = bridge._poll_response(request_id, timeout=2.0)
        assert resp.id == request_id
        assert resp.success is False
        assert resp.data is None

    def test_empty_response_file_times_out(self, tmp_path):
        """Empty response file — bridge retries until timeout."""
        workspace = Path(tmp_path)
        bridge = make_bare_bridge(workspace, timeout=1.0)

        request_id = uuid.uuid4().hex
        response_path = workspace / f"response_{request_id}.json"
        response_path.write_text("", encoding="utf-8")

        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response(request_id, timeout=1.0)

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
        """Simulate a partial response followed by the full one (atomic-rename
        contract: partial files exist only as ``.json.tmp``; the final
        ``response_<id>.json`` only ever appears whole)."""
        workspace = Path(tmp_path)
        workspace.mkdir(exist_ok=True)

        bridge = make_bare_bridge(workspace, timeout=5.0)

        request_id = uuid.uuid4().hex
        final_path = workspace / f"response_{request_id}.json"

        def delayed_write():
            time.sleep(0.05)
            # Partial — but written to a tmp suffix that the bridge ignores.
            tmp = final_path.with_suffix(".json.tmp")
            tmp.write_text(
                '{"protocol_version":2,"id":"' + request_id + '","succ',
                encoding="utf-8",
            )
            time.sleep(0.05)
            # Final atomic write — visible to the bridge as one complete file.
            tmp.write_text(
                json.dumps({
                    "protocol_version": 2,
                    "id": request_id,
                    "success": True,
                    "data": "recovered",
                    "error": None,
                }),
                encoding="utf-8",
            )
            tmp.replace(final_path)

        writer = threading.Thread(target=delayed_write, daemon=True)
        writer.start()

        resp = bridge._poll_response(request_id, timeout=5.0)
        writer.join(timeout=2.0)
        assert resp.success is True
        assert resp.data == "recovered"

    def test_corrupt_request_file_cleaned_up(self, tmp_path):
        """Partial/garbage per-request file — simulator cleans up and continues."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        try:
            request_path = sim.workspace_dir / "request_corrupttest.json"
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
