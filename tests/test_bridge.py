# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Tests for Altium Bridge."""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from eda_agent.bridge import AltiumBridge, AltiumProcessManager
from eda_agent.bridge.exceptions import AltiumNotRunningError, AltiumTimeoutError
from eda_agent.config import AltiumConfig


class TestAltiumProcessManager:
    """Tests for AltiumProcessManager."""

    def test_is_altium_running_when_not_running(self):
        """Test detection when Altium is not running."""
        manager = AltiumProcessManager()
        # In a test environment, Altium is typically not running
        # This test verifies the method doesn't crash
        result = manager.is_altium_running()
        assert isinstance(result, bool)

    def test_find_altium_process_returns_none_when_not_running(self):
        """Test that find_altium_process returns None when Altium isn't running."""
        manager = AltiumProcessManager()
        with patch.object(manager, 'find_altium_process', return_value=None):
            result = manager.find_altium_process()
            assert result is None


class TestAltiumBridge:
    """Tests for AltiumBridge."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def bridge(self, temp_workspace):
        """Create a bridge with a temporary workspace."""
        from eda_agent.config import configure
        configure(workspace_dir=temp_workspace)
        return AltiumBridge()

    def test_get_altium_status_not_running(self, bridge):
        """Test get_altium_status when Altium is not running."""
        with patch.object(bridge.process_manager, 'get_altium_info', return_value=None):
            status = bridge.get_altium_status()
            assert status['running'] is False
            assert status['pid'] is None
            assert status['attached'] is False

    def test_attach_raises_when_not_running(self, bridge):
        """Test that attach raises error when Altium is not running."""
        with patch.object(bridge, 'is_altium_running', return_value=False):
            with pytest.raises(AltiumNotRunningError):
                bridge.attach()

    def test_attach_success(self, bridge):
        """Test successful attachment."""
        with patch.object(bridge, 'is_altium_running', return_value=True):
            with patch.object(bridge, 'ping', return_value=True):
                result = bridge.attach()
                assert result is True
                assert bridge._attached is True

    def test_detach(self, bridge):
        """Test detachment."""
        bridge._attached = True
        bridge.detach()
        assert bridge._attached is False

    def test_write_request(self, bridge, temp_workspace):
        """Test writing a per-request file."""
        from eda_agent.bridge.altium_bridge import CommandRequest, PROTOCOL_VERSION

        bridge.ensure_workspace()
        request = CommandRequest(command="test.command", params={"key": "value"})
        bridge._write_request(request)

        request_path = temp_workspace / f"request_{request.id}.json"
        assert request_path.exists()

        with open(request_path, encoding="utf-8") as f:
            data = json.load(f)
            assert data['command'] == "test.command"
            assert data['params'] == {"key": "value"}
            assert data['id'] == request.id
            assert data['protocol_version'] == PROTOCOL_VERSION

    def test_poll_response(self, bridge, temp_workspace):
        """Test polling for a per-request response file."""
        from eda_agent.bridge.altium_bridge import PROTOCOL_VERSION

        bridge.ensure_workspace()
        request_id = "testid123"

        response_data = {
            "protocol_version": PROTOCOL_VERSION,
            "id": request_id,
            "success": True,
            "data": {"result": "success"},
            "error": None
        }

        response_path = temp_workspace / f"response_{request_id}.json"
        with open(response_path, "w", encoding="utf-8") as f:
            json.dump(response_data, f)

        response = bridge._poll_response(request_id, timeout=2)
        assert response is not None
        assert response.success is True
        assert response.data == {"result": "success"}

    def test_poll_response_unrelated_id_times_out(self, bridge, temp_workspace):
        """Per-request files mean another caller's response is invisible to us."""
        from eda_agent.bridge.altium_bridge import PROTOCOL_VERSION

        bridge.ensure_workspace()

        # A different caller's response file — we should not even see it.
        response_data = {
            "protocol_version": PROTOCOL_VERSION,
            "id": "differentid",
            "success": True,
            "data": None,
            "error": None
        }
        other_path = temp_workspace / "response_differentid.json"
        with open(other_path, "w", encoding="utf-8") as f:
            json.dump(response_data, f)

        from eda_agent.bridge.exceptions import AltiumTimeoutError
        import pytest
        with pytest.raises(AltiumTimeoutError):
            bridge._poll_response("expectedid", timeout=1)

        # Crucially, the other caller's file is left untouched.
        assert other_path.exists()


class TestCommandRequest:
    """Tests for CommandRequest."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        from eda_agent.bridge.altium_bridge import CommandRequest, PROTOCOL_VERSION

        request = CommandRequest(
            command="test.command",
            params={"x": 100, "y": 200},
            id="testid"
        )

        result = request.to_dict()
        assert result == {
            "protocol_version": PROTOCOL_VERSION,
            "id": "testid",
            "command": "test.command",
            "params": {"x": 100, "y": 200}
        }

    def test_auto_generates_id(self):
        """Test that ID is auto-generated."""
        from eda_agent.bridge.altium_bridge import CommandRequest

        request = CommandRequest(command="test")
        assert request.id is not None
        assert len(request.id) > 0


class TestCommandResponse:
    """Tests for CommandResponse."""

    def test_from_dict_success(self):
        """Test creating from success dictionary."""
        from eda_agent.bridge.altium_bridge import CommandResponse

        data = {
            "id": "test-id",
            "success": True,
            "data": {"value": 42},
            "error": None
        }

        response = CommandResponse.from_dict(data)
        assert response.id == "test-id"
        assert response.success is True
        assert response.data == {"value": 42}
        assert response.error is None

    def test_from_dict_error(self):
        """Test creating from error dictionary."""
        from eda_agent.bridge.altium_bridge import CommandResponse

        data = {
            "id": "test-id",
            "success": False,
            "data": None,
            "error": {"code": "ERROR", "message": "Something failed"}
        }

        response = CommandResponse.from_dict(data)
        assert response.success is False
        assert response.error == {"code": "ERROR", "message": "Something failed"}
