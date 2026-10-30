# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Serve MCP through a proxy that restarts the server when its code changes.

For development. An MCP client starts the server once and keeps it, so a
change to the Python took effect only after the client reconnected
(``/mcp`` in Claude Code), every time, in every session.

With ``EDA_AGENT_RELOAD=1`` (or ``eda-agent --reload``) the client talks
to this proxy instead, which runs the real server as a child process and
passes messages through unchanged. When a ``.py`` file under the package
changes and no call is in flight, it starts a new child, replays the
client's ``initialize`` to it, and tells the client the tool list changed.
The client's connection never drops.

A child that fails to start (a half-written edit) answers every call with
the error it printed, and the next change tries again.
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .noconsole import no_window

RELOAD_ENV = "EDA_AGENT_RELOAD"

#: How often the source is checked, and how long it must stay unchanged
#: before a restart, so a run of edits restarts once.
POLL_S = 1.0
SETTLE_S = 1.5
#: Longest a restart waits for calls in flight to finish.
MAX_WAIT_FOR_CALLS_S = 600.0

_PACKAGE = Path(__file__).resolve().parent


def _log(text: str) -> None:
    sys.stderr.write(f"[eda-agent reload] {text}\n")
    sys.stderr.flush()


def source_stamp(root: Path = _PACKAGE) -> dict:
    """Modification time of every Python file in the package."""
    stamps = {}
    for path in root.rglob("*.py"):
        try:
            stamps[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return stamps


class Proxy:
    def __init__(self, child_args: list, stdin, stdout,
                 child_command: Optional[list] = None,
                 root: Path = _PACKAGE) -> None:
        self.child_args = child_args
        #: The server a user runs; a test passes a stand-in.
        self.child_command = child_command or [
            sys.executable, "-m", "eda_agent.server"]
        self.root = root
        self.stdin = stdin
        self.stdout = stdout
        self.write_lock = threading.Lock()
        self.state = threading.Condition()
        self.child: Optional[subprocess.Popen] = None
        self.ready = False
        self.dead_reason = ""
        self.pending: set = set()
        self.queue: list = []
        self.swallow: set = set()
        self.init_line: Optional[bytes] = None
        self.initialized_line: Optional[bytes] = None
        self.client_init_id = None
        self.generation = 0
        self.restarting = False
        self.stderr_tail: collections.deque = collections.deque(maxlen=30)

    # -- client side -------------------------------------------------------

    def to_client(self, message: dict) -> None:
        self._write_client(json.dumps(message).encode("utf-8") + b"\n")

    def _write_client(self, line: bytes) -> None:
        with self.write_lock:
            self.stdout.write(line)
            self.stdout.flush()

    def read_client(self) -> None:
        for line in iter(self.stdin.readline, b""):
            if not line.strip():
                continue
            message = _parse(line)
            method = message.get("method") if message else None
            if method == "initialize":
                self.init_line = line
                self.client_init_id = message.get("id")
            elif method == "notifications/initialized":
                self.initialized_line = line
            self._forward(line, message)
        self.stop()

    def _forward(self, line: bytes, message: Optional[dict]) -> None:
        with self.state:
            if self.dead_reason and message and "id" in message \
                    and message.get("method"):
                self.to_client({"jsonrpc": "2.0", "id": message["id"],
                                "error": {"code": -32603,
                                          "message": self.dead_reason}})
                return
            if not self.ready:
                self.queue.append(line)
                return
            self._dispatch(line, message)

    def _dispatch(self, line: bytes, message: Optional[dict]) -> None:
        """Send to the child, counting a request as in flight. Called with
        ``state`` held."""
        if message and message.get("method") and "id" in message:
            self.pending.add(_key(message["id"]))
        self._send_child(line)

    def _send_child(self, line: bytes) -> None:
        try:
            self.child.stdin.write(line)
            self.child.stdin.flush()
        except (OSError, ValueError, AttributeError):
            pass

    # -- child side --------------------------------------------------------

    def start_child(self) -> None:
        env = dict(os.environ)
        env.pop(RELOAD_ENV, None)
        env["PYTHONUNBUFFERED"] = "1"
        self.generation += 1
        generation = self.generation
        child = subprocess.Popen(
            [*self.child_command, *self.child_args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, **no_window())
        with self.state:
            self.child = child
            self.dead_reason = ""
            self.ready = False
            self.restarting = False
        threading.Thread(target=self._read_child, args=(child, generation),
                         daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(child,),
                         daemon=True).start()
        with self.state:
            if self.init_line is not None:
                # A restart: the new child is initialised as the client
                # initialised the first, and its answer is not passed on.
                replay = _parse(self.init_line) or {}
                replay["id"] = f"eda-agent-reload-{generation}"
                self.swallow.add(_key(replay["id"]))
                self._send_child(json.dumps(replay).encode("utf-8") + b"\n")
                if self.initialized_line is not None:
                    self._send_child(self.initialized_line)
            self.ready = True
            queued, self.queue = self.queue, []
            for line in queued:
                self._dispatch(line, _parse(line))

    def _read_child(self, child: subprocess.Popen, generation: int) -> None:
        for line in iter(child.stdout.readline, b""):
            message = _parse(line)
            if message and "id" in message and not message.get("method"):
                key = _key(message["id"])
                with self.state:
                    if key in self.swallow:
                        self.swallow.discard(key)
                        continue
                    self.pending.discard(key)
                    self.state.notify_all()
                if message.get("id") == self.client_init_id:
                    line = _announce_list_changes(message, line)
            self._write_client(line)
        child.wait()
        with self.state:
            # Stopped on purpose for a restart: the queue waits for the new
            # child instead of being answered with this one's death.
            if generation != self.generation or self.restarting:
                return
            tail = " | ".join(list(self.stderr_tail)[-5:])
            self.dead_reason = (
                f"the eda-agent server stopped (exit {child.returncode}): "
                f"{tail or 'no message'}. It restarts on the next source "
                f"change.")
            self.ready = False
            failed, self.pending = list(self.pending), set()
            queued, self.queue = self.queue, []
        _log(self.dead_reason)
        for key in failed:
            self.to_client({"jsonrpc": "2.0", "id": _unkey(key),
                            "error": {"code": -32603,
                                      "message": self.dead_reason}})
        for line in queued:
            self._forward(line, _parse(line))

    def _read_stderr(self, child: subprocess.Popen) -> None:
        for line in iter(child.stderr.readline, b""):
            text = line.decode("utf-8", "replace").rstrip()
            self.stderr_tail.append(text)
            sys.stderr.write(text + "\n")
        sys.stderr.flush()

    # -- reloading ---------------------------------------------------------

    def restart(self) -> None:
        with self.state:
            # New calls wait in the queue from here; those already sent
            # finish on the old child first.
            self.ready = False
            deadline = time.monotonic() + MAX_WAIT_FOR_CALLS_S
            while self.pending and time.monotonic() < deadline:
                self.state.wait(1.0)
            self.restarting = True
            old = self.child
        if old is not None and old.poll() is None:
            old.terminate()
            try:
                old.wait(10)
            except subprocess.TimeoutExpired:
                old.kill()
        _log("source changed; restarting the server")
        self.start_child()
        if self.initialized_line is not None:
            self.to_client({"jsonrpc": "2.0",
                            "method": "notifications/tools/list_changed"})

    def watch(self) -> None:
        stamp = source_stamp(self.root)
        changed_at = None
        while True:
            time.sleep(POLL_S)
            now = source_stamp(self.root)
            if now != stamp:
                stamp = now
                changed_at = time.monotonic()
                continue
            if changed_at and time.monotonic() - changed_at >= SETTLE_S:
                changed_at = None
                self.restart()

    def stop(self) -> None:
        child = self.child
        if child is not None and child.poll() is None:
            child.terminate()
        if self.exit_on_eof:
            os._exit(0)

    exit_on_eof = True

    def run(self) -> int:
        self.start_child()
        threading.Thread(target=self.watch, daemon=True).start()
        self.read_client()
        return 0


def _parse(line: bytes) -> Optional[dict]:
    try:
        message = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return message if isinstance(message, dict) else None


def _key(request_id) -> str:
    return json.dumps(request_id)


def _unkey(key: str):
    return json.loads(key)


def _announce_list_changes(message: dict, line: bytes) -> bytes:
    """The initialize answer, saying the tool list can change, so the
    client listens for the notice a restart sends."""
    try:
        tools = message["result"]["capabilities"].setdefault("tools", {})
        tools["listChanged"] = True
    except (KeyError, TypeError, AttributeError):
        return line
    return json.dumps(message).encode("utf-8") + b"\n"


def main(child_args: list) -> int:
    _log(f"serving through a reloading proxy; watching {_PACKAGE}")
    return Proxy(child_args, sys.stdin.buffer, sys.stdout.buffer).run()
