# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Communication bridge between Python and Altium Designer via file-based IPC.

The Altium script polls for request.json, processes commands, writes response.json.
Python writes request.json and polls for a matching response.json.
"""

import json
import logging
import time
import uuid
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from ..config import get_config
from .process_manager import AltiumProcessManager
from .exceptions import (
    AltiumNotRunningError,
    AltiumTimeoutError,
    AltiumCommandError,
    ScriptNotLoadedError,
)

logger = logging.getLogger("eda_agent.bridge")

# Thread pool for blocking I/O
_executor = ThreadPoolExecutor(max_workers=1)


def _trace_log(workspace_dir: Path, msg: str) -> None:
    """Append a line to workspace/bridge_trace.log. Never raises."""
    try:
        path = workspace_dir / "bridge_trace.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:
        pass


@dataclass
class CommandRequest:
    """A command request to be sent to Altium."""

    command: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "params": self.params,
        }


@dataclass
class CommandResponse:
    """A response from Altium."""

    id: str
    success: bool
    data: Any = None
    error: Optional[dict] = None

    @classmethod
    def from_dict(cls, data: dict) -> "CommandResponse":
        return cls(
            id=data.get("id", ""),
            success=data.get("success", False),
            data=data.get("data"),
            error=data.get("error"),
        )


class AltiumBridge:
    """Handles communication with Altium Designer via file-based IPC.

    The communication flow:
    1. Python writes request.json
    2. Altium's polling script picks it up, deletes it, processes it
    3. Altium writes response.json
    4. Python polls for response.json with matching request ID
    """

    # Keep-alive interval — must be less than Altium's AUTO_SHUTDOWN_MS (60s)
    KEEPALIVE_INTERVAL = 30  # seconds

    # Inject a detach-reminder into responses after this many seconds of
    # continuous attachment without the client calling detach_from_altium.
    # The hint is added to the response payload so the calling client can
    # surface it to the user.
    DETACH_HINT_AFTER = 600  # 10 minutes

    def __init__(self):
        self.config = get_config()
        self.process_manager = AltiumProcessManager()
        self._attached = False
        self._keepalive_thread: Optional[threading.Thread] = None
        self._keepalive_stop = threading.Event()
        self._attach_time: Optional[float] = None
        self._detach_hint_shown = False
        # Serialize all IPC so the keep-alive thread and user tool calls
        # don't race on response.json (bug: concurrent pollers delete each
        # other's responses as "stale").
        self._ipc_lock = threading.Lock()

    def ensure_workspace(self) -> None:
        """Ensure the workspace directory exists."""
        self.config.ensure_workspace()

    def is_altium_running(self) -> bool:
        """Check if Altium Designer is running."""
        return self.process_manager.is_altium_running()

    def get_altium_status(self) -> dict:
        """Get the status of Altium Designer."""
        process = self.process_manager.get_altium_info()
        if process:
            return {
                "running": True,
                "pid": process.pid,
                "exe_path": process.exe_path,
                "attached": self._attached,
            }
        return {
            "running": False,
            "pid": None,
            "exe_path": None,
            "attached": False,
        }

    def attach(self) -> bool:
        """Mark as attached to Altium (verifies process is running).

        Returns:
            True if Altium is running and workspace is ready.
        """
        if not self.is_altium_running():
            raise AltiumNotRunningError()

        self.ensure_workspace()
        self._attached = True
        self._attach_time = time.monotonic()
        self._start_keepalive()
        logger.info("Attached to Altium Designer (file-based IPC)")
        return True

    def detach(self) -> None:
        """Detach from Altium instance."""
        self._stop_keepalive()
        self._attached = False
        self._attach_time = None
        self._detach_hint_shown = False

    def _ensure_keepalive(self) -> None:
        """Start the keep-alive thread if it isn't already running.

        Called from send_command so the first tool call auto-starts the
        ping loop. The MCP client doesn't have to call attach_to_altium
        explicitly — any command that reaches Altium kicks the keepalive
        on so subsequent idle gaps between tool calls longer than Altium's
        60 s auto-shutdown window don't drop the polling loop.
        """
        if self._attached and self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        self._attached = True
        self._attach_time = time.monotonic()
        self._start_keepalive()

    def attach_duration(self) -> float:
        """Seconds since the bridge started keeping Altium alive. 0 if detached."""
        if self._attach_time is None:
            return 0.0
        return time.monotonic() - self._attach_time

    def _maybe_attach_detach_hint(self, command: str, data: Any) -> Any:
        """Add a one-time _hint_detach field to the response dict when the
        bridge has been holding Altium's scripting engine for longer than
        DETACH_HINT_AFTER without the client calling detach_from_altium.

        Surfaces a reminder in the response payload to call detach_from_altium
        when done. Only fires once per session; clears on actual detach.
        Silent if the response is not a dict (can't inject) or if the command
        itself is a keep-alive / detach (no point nagging on those).
        """
        if command in ("application.ping", "application.stop_server"):
            return data
        if self._detach_hint_shown:
            return data
        if self.attach_duration() < self.DETACH_HINT_AFTER:
            return data
        if not isinstance(data, dict):
            return data
        self._detach_hint_shown = True
        data = dict(data)
        data["_hint_detach"] = (
            "This MCP server has been holding Altium's scripting engine for "
            "over 10 minutes. When your Altium work is done, call "
            "detach_from_altium so Altium's own UI commands become responsive "
            "again. The user will have to re-launch StartMCPServer in Altium "
            "to run more MCP tools afterwards."
        )
        return data

    def _start_keepalive(self) -> None:
        """Start background thread that pings Altium to prevent auto-shutdown."""
        self._stop_keepalive()
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True, name="altium-keepalive"
        )
        self._keepalive_thread.start()
        logger.debug("Keep-alive thread started (interval=%ds)", self.KEEPALIVE_INTERVAL)

    def _stop_keepalive(self) -> None:
        """Stop the keep-alive thread."""
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_stop.set()
            self._keepalive_thread.join(timeout=5)
            logger.debug("Keep-alive thread stopped")
        self._keepalive_thread = None

    def _keepalive_loop(self) -> None:
        """Background loop that sends periodic pings to reset Altium's idle timer."""
        while not self._keepalive_stop.wait(self.KEEPALIVE_INTERVAL):
            if not self._attached:
                break
            try:
                self.send_command("application.ping", timeout=5.0)
            except Exception:
                # Altium may have closed or script stopped — that's OK
                logger.debug("Keep-alive ping failed — Altium may have stopped")
                break

    def _write_request(self, request: CommandRequest) -> None:
        """Write a command request to the request file."""
        self.ensure_workspace()
        request_path = self.config.request_path

        logger.debug("Writing request %s: %s", request.id, request.command)

        # Write atomically: tmp file then rename
        temp_path = request_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(request.to_dict(), f, indent=2)
        temp_path.replace(request_path)

    def _poll_response(self, request_id: str, timeout: float) -> CommandResponse:
        """Poll for a response file with matching request ID.

        The Altium script deletes request.json after reading it and writes
        response.json when done. We poll for response.json containing our ID.
        """
        response_path = self.config.response_path
        workspace_dir = self.config.workspace_dir
        deadline = time.monotonic() + timeout
        poll_interval = self.config.poll_interval
        poll_count = 0
        first_appearance: Optional[float] = None
        stale_deletions = 0
        parse_errors = 0
        start = time.monotonic()

        _trace_log(workspace_dir, f"POLL_START id={request_id[:8]} timeout={timeout}s interval={poll_interval}s")

        while time.monotonic() < deadline:
            poll_count += 1
            if not response_path.exists():
                time.sleep(poll_interval)
                continue

            if first_appearance is None:
                first_appearance = time.monotonic() - start
                _trace_log(workspace_dir, f"POLL_SEEN id={request_id[:8]} after={first_appearance*1000:.0f}ms polls={poll_count}")

            try:
                # Altium writes Latin-1 (Windows default), not UTF-8
                with open(response_path, "r", encoding="latin-1") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                parse_errors += 1
                if parse_errors <= 3 or parse_errors % 50 == 0:
                    _trace_log(workspace_dir, f"POLL_PARSE_ERR id={request_id[:8]} err={type(e).__name__} count={parse_errors}")
                # File might be partially written
                time.sleep(poll_interval)
                continue

            response_id = data.get("id")
            if response_id != request_id:
                stale_deletions += 1
                _trace_log(workspace_dir, f"POLL_STALE_DELETE got={str(response_id)[:8]} want={request_id[:8]} count={stale_deletions}")
                logger.warning(
                    "Deleting stale response (id=%s, expected=%s)",
                    response_id,
                    request_id,
                )
                try:
                    response_path.unlink()
                except OSError:
                    pass
                time.sleep(poll_interval)
                continue

            # Matching response received — clean up
            try:
                response_path.unlink()
            except OSError:
                pass

            elapsed = (time.monotonic() - start) * 1000
            _trace_log(workspace_dir, f"POLL_MATCH id={request_id[:8]} elapsed={elapsed:.0f}ms polls={poll_count} stale_deletes={stale_deletions} parse_errs={parse_errors}")
            return CommandResponse.from_dict(data)

        elapsed = (time.monotonic() - start) * 1000
        _trace_log(workspace_dir, f"POLL_TIMEOUT id={request_id[:8]} elapsed={elapsed:.0f}ms polls={poll_count} stale_deletes={stale_deletions} parse_errs={parse_errors} first_seen_ms={first_appearance*1000 if first_appearance else -1}")
        raise AltiumTimeoutError(
            f"No response within {timeout}s — is the Altium script running?"
        )

    def _execute_command(self, command: str, params: dict[str, Any], timeout: float) -> Any:
        """Execute a command synchronously (blocking).

        1. Write request.json
        2. Poll for response.json with matching ID

        Serialized via _ipc_lock so concurrent calls (e.g. keep-alive ping +
        user tool call) don't race on response.json and delete each other's
        responses as stale.
        """
        with self._ipc_lock:
            request = CommandRequest(command=command, params=params)
            workspace_dir = self.config.workspace_dir

            # Clear stale response before writing new request
            response_path = self.config.response_path
            pre_existed = False
            try:
                if response_path.exists():
                    pre_existed = True
                    response_path.unlink()
            except OSError:
                pass

            _trace_log(workspace_dir, f"SEND cmd={command} id={request.id[:8]} stale_resp_cleared={pre_existed}")
            self._write_request(request)

            logger.info("Sent command: %s (waiting for response)", command)
            response = self._poll_response(request.id, timeout)

        if response.success:
            logger.info("Command %s succeeded", command)
            return self._maybe_attach_detach_hint(command, response.data)
        else:
            error = response.error or {}
            logger.warning(
                "Command %s failed: %s - %s",
                command,
                error.get("code"),
                error.get("message"),
            )
            raise AltiumCommandError(
                message=error.get("message", "Unknown error"),
                code=error.get("code", "UNKNOWN_ERROR"),
            )

    def send_command(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send a command to Altium and wait for response.

        Args:
            command: The command name (e.g., "schematic.place_component").
            params: Command parameters.
            timeout: Timeout in seconds.

        Returns:
            The response data from Altium.
        """
        if not self.is_altium_running():
            raise AltiumNotRunningError()

        if timeout is None:
            timeout = self.config.poll_timeout

        self._ensure_keepalive()
        return self._execute_command(command, params or {}, timeout)

    async def send_command_async(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Async version of send_command. Runs polling in a thread executor."""
        if not self.is_altium_running():
            raise AltiumNotRunningError()

        if timeout is None:
            timeout = self.config.poll_timeout

        self._ensure_keepalive()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            self._execute_command,
            command,
            params or {},
            timeout,
        )

    def ping(self) -> bool:
        """Check if the Altium script is responding.

        Returns:
            True if Altium responds to ping within 3 seconds.
        """
        try:
            self.send_command("application.ping", timeout=3.0)
            return True
        except (AltiumTimeoutError, AltiumCommandError, Exception):
            return False

    def ping_with_version(self) -> Optional[dict[str, Any]]:
        """Ping and return the raw response dict (including script_version).

        Returns:
            Dict like {"pong": True, "script_version": "..."} on success.
            None on timeout/error. Legacy scripts that returned the string
            "pong" are normalised to {"pong": True, "script_version": ""}.
        """
        try:
            result = self.send_command("application.ping", timeout=3.0)
        except (AltiumTimeoutError, AltiumCommandError, Exception):
            return None
        if isinstance(result, dict):
            return result
        if result == "pong":
            return {"pong": True, "script_version": ""}
        return None


# Global bridge instance
_bridge: Optional[AltiumBridge] = None


def get_bridge() -> AltiumBridge:
    """Get the global bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = AltiumBridge()
    return _bridge


def reset_bridge() -> None:
    """Reset the global bridge instance.

    Called when configuration changes so the next get_bridge()
    creates a fresh instance with the updated config.
    """
    global _bridge
    _bridge = None
