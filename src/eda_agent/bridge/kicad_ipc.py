# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The connection to a running KiCad's API server.

KiCad listens on ``<temp>/kicad/api.sock``: a named pipe on Windows, a
Unix socket elsewhere. The conversation is request and reply. Each side
opens with an 8-byte greeting naming its role, then every message goes
as a frame: one byte 0x01, an 8-byte big-endian length, and the bytes,
which on a request start with a 4-byte request id that the reply echoes.

Inside a frame is an envelope: a header (the instance token and our
name) and the command, packed as a type name plus its encoded bytes. The
reply carries a status and the response the same way.

Python's standard library is all this needs.
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile
import threading
import time
from typing import Optional

from .kicad_wire import Message

# The greeting: protocol "SP" version 0, then our role (request, 0x30)
# and two reserved bytes. KiCad answers with the reply role, 0x31.
_GREETING = b"\x00SP\x00\x00\x30\x00\x00"
_PEER_ROLE = b"\x00\x31"

_TYPE_PREFIX = "type.googleapis.com/"

# Reply status codes.
STATUS = {0: "unknown", 1: "ok", 2: "timeout", 3: "bad request",
          4: "not ready", 5: "unhandled", 6: "token mismatch", 7: "busy",
          8: "unimplemented"}
OK, UNHANDLED, BUSY = 1, 5, 7


class KiCadNotReachableError(RuntimeError):
    """KiCad is not running, its API server is off, or no board is open."""


class KiCadApiError(RuntimeError):
    """KiCad received a request and refused it."""

    def __init__(self, command: str, status: int, message: str):
        self.command, self.status = command, status
        super().__init__(f"{command}: {STATUS.get(status, status)}"
                         + (f": {message}" if message else ""))


def default_socket_path() -> str:
    """Where KiCad's API server listens, as KiCad works it out."""
    given = os.environ.get("KICAD_API_SOCKET", "")
    if given:
        return given[len("ipc://"):] if given.startswith("ipc://") else given
    base = "/tmp" if sys.platform != "win32" else tempfile.gettempdir()
    return os.path.join(base, "kicad", "api.sock")


class _WindowsPipe:
    """A client end of a named pipe, with reads that give up at a deadline."""

    def __init__(self, path: str, timeout: float):
        import ctypes
        from ctypes import wintypes
        self._k = k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._ct, self._wt = ctypes, wintypes
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
        k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD),
                                    wintypes.LPVOID]
        k.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        name = "\\\\.\\pipe\\" + path
        deadline = time.monotonic() + timeout
        while True:
            handle = k.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)
            if handle and handle != wintypes.HANDLE(-1).value:
                self._h = handle
                return
            err = ctypes.get_last_error()
            # 231: every instance busy; wait for one. 2: nothing listening.
            if err == 231 and time.monotonic() < deadline:
                k.WaitNamedPipeW(name, 200)
                continue
            raise OSError(err, ctypes.FormatError(err).strip(), name)

    def write(self, data: bytes) -> None:
        done = self._wt.DWORD()
        if not self._k.WriteFile(self._h, data, len(data), self._ct.byref(done), None) \
                or done.value != len(data):
            err = self._ct.get_last_error()
            raise OSError(err, "writing to KiCad's pipe failed")

    def read(self, n: int, deadline: float) -> bytes:
        out = bytearray()
        buf = self._ct.create_string_buffer(max(n, 1))
        avail, got = self._wt.DWORD(), self._wt.DWORD()
        spins, pause = 0, 0.0005
        while len(out) < n:
            if not self._k.PeekNamedPipe(self._h, None, 0, None, self._ct.byref(avail), None):
                err = self._ct.get_last_error()
                raise OSError(err, "KiCad closed the connection")
            if avail.value == 0:
                if time.monotonic() > deadline:
                    raise TimeoutError("KiCad did not answer in time")
                # Most replies take well under a millisecond: yield first,
                # then back off so a long command does not burn a core.
                if spins < 200:
                    spins += 1
                    time.sleep(0)
                else:
                    time.sleep(pause)
                    pause = min(pause * 2, 0.02)
                continue
            want = min(n - len(out), avail.value)
            if not self._k.ReadFile(self._h, buf, want, self._ct.byref(got), None):
                err = self._ct.get_last_error()
                raise OSError(err, "reading KiCad's pipe failed")
            out += buf.raw[:got.value]
        return bytes(out)

    def close(self) -> None:
        if getattr(self, "_h", None):
            self._k.CloseHandle(self._h)
            self._h = None


class _UnixSocket:
    def __init__(self, path: str, timeout: float):
        self._s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._s.settimeout(timeout)
        self._s.connect(path)

    def write(self, data: bytes) -> None:
        self._s.sendall(data)

    def read(self, n: int, deadline: float) -> bytes:
        out = bytearray()
        while len(out) < n:
            self._s.settimeout(max(deadline - time.monotonic(), 0.001))
            try:
                chunk = self._s.recv(n - len(out))
            except socket.timeout:
                raise TimeoutError("KiCad did not answer in time") from None
            if not chunk:
                raise OSError("KiCad closed the connection")
            out += chunk
        return bytes(out)

    def close(self) -> None:
        self._s.close()


class KiCadIpc:
    """One connection to KiCad's API server; calls are serialised."""

    def __init__(self, socket_path: Optional[str] = None,
                 token: Optional[str] = None, client_name: Optional[str] = None,
                 timeout: float = 30.0):
        self.socket_path = socket_path or default_socket_path()
        self.token = token if token is not None else os.environ.get("KICAD_API_TOKEN", "")
        # KiCad keeps one open commit per client name, so each process
        # gets its own: a commit left open by one cannot block another.
        self.client_name = client_name or f"eda-agent-{os.getpid()}"
        self.timeout = timeout
        self._conn = None
        self._lock = threading.Lock()
        self._request_id = (os.getpid() * 7919) & 0x7FFFFFFF

    def _connect(self):
        try:
            if sys.platform == "win32":
                conn = _WindowsPipe(self.socket_path, self.timeout)
            else:
                conn = _UnixSocket(self.socket_path, self.timeout)
            conn.write(_GREETING)
            peer = conn.read(8, time.monotonic() + self.timeout)
        except (OSError, TimeoutError) as e:
            raise KiCadNotReachableError(
                f"cannot reach KiCad's API server at {self.socket_path} ({e}). "
                "Open KiCad and turn on Preferences > Plugins > Enable KiCad "
                "API.") from None
        if peer[:4] != _GREETING[:4] or peer[4:6] != _PEER_ROLE:
            conn.close()
            raise KiCadNotReachableError(
                f"{self.socket_path} answered, but not as KiCad's API server")
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _exchange(self, payload: bytes, timeout: float) -> bytes:
        if self._conn is None:
            self._conn = self._connect()
        self._request_id = (self._request_id + 1) & 0x7FFFFFFF
        rid = 0x80000000 | self._request_id
        body = struct.pack(">I", rid) + payload
        deadline = time.monotonic() + timeout
        self._conn.write(b"\x01" + struct.pack(">Q", len(body)) + body)
        while True:
            head = self._conn.read(9, deadline)
            size = struct.unpack(">Q", head[1:])[0]
            reply = self._conn.read(size, deadline)
            if len(reply) >= 4 and struct.unpack(">I", reply[:4])[0] == rid:
                return reply[4:]
            # A reply to an earlier request that timed out here: skip it.

    def call(self, command: str, body: Optional[Message] = None,
             timeout: Optional[float] = None) -> Message:
        """Send ``command`` (a full type name such as
        ``kiapi.common.commands.GetVersion``) and return the response.

        Raises KiCadApiError when KiCad refuses it and
        KiCadNotReachableError when the connection fails.
        """
        header = Message().set_str(1, self.token).set_str(2, self.client_name)
        packed = Message().set_str(1, _TYPE_PREFIX + command) \
            .set_bytes(2, body.encode() if body is not None else b"")
        request = Message().set_msg(1, header).set_msg(2, packed).encode()
        with self._lock:
            try:
                raw = self._exchange(request, timeout or self.timeout)
            except (OSError, TimeoutError) as e:
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None
                raise KiCadNotReachableError(f"{command}: {e}") from None
        reply = Message.decode(raw)
        head = reply.msg(1)
        if head is not None and not self.token:
            self.token = head.str(1)  # later calls name this KiCad instance
        status = reply.msg(2) or Message()
        code = status.int(1)
        if code != OK:
            raise KiCadApiError(command, code, status.str(2))
        packed_reply = reply.msg(3)
        return Message.decode(packed_reply.bytes(2)) if packed_reply else Message()
