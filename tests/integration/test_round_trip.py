# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Real-Altium round-trip integration tests.

These tests exercise the full Python <-> Pascal IPC contract against a
running Altium instance. They are the only honest way to validate the
Pascal handlers — the simulator-based tests in tests/test_*.py prove that
our Python re-implementation of Pascal logic matches itself, not that the
Pascal code actually behaves as expected.

Run with:

    pytest tests/integration/ -v

The tests skip cleanly if Altium isn't running. Set EDA_AGENT_INTEGRATION=1
to make missing preconditions hard-fail (CI mode).
"""

import pytest

from eda_agent.bridge.altium_bridge import PROTOCOL_VERSION
from eda_agent.bridge.exceptions import (
    AltiumCommandError,
    PreconditionError,
    InvalidParameterError,
)


# ---------------------------------------------------------------------------
# Protocol-level contracts
# ---------------------------------------------------------------------------

class TestProtocolContract:

    def test_ping_returns_protocol_version(self, real_bridge):
        info = real_bridge.ping_with_version()
        assert info is not None
        assert info.get("protocol_version") == PROTOCOL_VERSION

    def test_ping_returns_script_version(self, real_bridge):
        info = real_bridge.ping_with_version()
        assert info is not None
        assert info.get("script_version")  # non-empty string

    def test_unknown_command_returns_structured_error(self, real_bridge):
        with pytest.raises(InvalidParameterError) as exc_info:
            real_bridge.send_command("bogus.action", timeout=5.0)
        assert exc_info.value.code in (
            "UNKNOWN_COMMAND", "UNKNOWN_ACTION", "INVALID_PARAMETER"
        )


# ---------------------------------------------------------------------------
# Application surface
# ---------------------------------------------------------------------------

class TestApplicationSurface:

    def test_get_open_documents(self, real_bridge):
        docs = real_bridge.send_command("application.get_open_documents", timeout=5.0)
        assert isinstance(docs, list)
        for d in docs:
            assert "file_name" in d
            assert "document_kind" in d

    def test_get_active_document(self, real_bridge):
        active = real_bridge.send_command("application.get_active_document", timeout=5.0)
        assert isinstance(active, dict)


# ---------------------------------------------------------------------------
# Project surface (requires fixture project loaded)
# ---------------------------------------------------------------------------

class TestProjectSurface:

    def test_get_focused_project_after_open(self, real_bridge, fixture_project_loaded):
        focused = real_bridge.send_command("project.get_focused", timeout=5.0)
        assert focused is not None
        assert "EDAAgentTest" in (focused.get("project_name") or "")

    def test_compile_project(self, real_bridge, fixture_project_loaded):
        # SmartCompile cache means the second call is fast; both must succeed.
        first = real_bridge.send_command("project.compile", timeout=30.0)
        second = real_bridge.send_command("project.compile", timeout=10.0)
        assert first is not None
        assert second is not None


# ---------------------------------------------------------------------------
# Per-request file isolation under concurrency
# ---------------------------------------------------------------------------

class TestConcurrentRequests:

    def test_concurrent_pings_dont_collide(self, real_bridge):
        """Each caller's response_<id>.json is independent."""
        import threading

        results: dict[int, dict] = {}
        errors: dict[int, Exception] = {}

        def fire(tag: int) -> None:
            try:
                results[tag] = real_bridge.ping_with_version()
            except Exception as e:
                errors[tag] = e

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert not errors, f"Concurrent pings failed: {errors}"
        assert len(results) == 8
        for r in results.values():
            assert r is not None
            assert r.get("protocol_version") == PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Error code → exception subclass mapping
# ---------------------------------------------------------------------------

class TestErrorCodeMapping:
    """Validates the structured error-code dispatch end-to-end:
    each Pascal-side error code surfaces as the right Python exception class.
    """

    def test_no_pcb_loaded_raises_precondition_error(self, real_bridge):
        """When no PCB is loaded, PCB-specific commands raise PreconditionError."""
        # This test only meaningfully runs when there is no .PcbDoc loaded.
        docs = real_bridge.send_command("application.get_open_documents", timeout=5.0)
        if any((d.get("document_kind") or "").upper() == "PCB" for d in docs):
            pytest.skip("A PCB document is loaded — can't test the no-PCB error path.")

        with pytest.raises(PreconditionError) as exc_info:
            real_bridge.send_command("pcb.get_components", timeout=5.0)
        assert exc_info.value.code in ("NO_PCB", "PRECONDITION_FAILED")
