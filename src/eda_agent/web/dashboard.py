# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Local web dashboard for the EDA Agent MCP bridge.

Companion to the in-Altium status form. Run with:

    eda-agent dashboard

then open ``http://127.0.0.1:8766`` (or click the "Open Dashboard"
button on the Altium-side status form, which writes a sentinel the
MCP server's keep-alive thread picks up and launches the browser).

The dashboard tails ``workspace/activity.log`` and surfaces:

- Live status pill (in-flight call, elapsed time, pause state).
- Four KPI tiles (uptime, request count, busy time, idle countdown).
- A streaming feed of recent calls with severity tags, request IDs,
  durations, and inline error details that expand on click.
- A per-command performance table you can sort by N / avg / max.
- A free-text filter that scopes both feed and perf table.
- Health probes (script version, version match, IPC liveness).

Server-Sent Events stream from ``/events`` give the browser tab a
sub-second view of every command without polling. Static assets are
inlined into one HTML response so the dashboard works offline and
needs no build tooling.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from eda_agent.atomicfile import replace_with_retry

from flask import Flask, Response, jsonify, send_from_directory, stream_with_context

from eda_agent.config import get_config

logger = logging.getLogger("eda_agent.web.dashboard")


# ---------------------------------------------------------------------------
# Bridge helpers: call MCP tools synchronously from Flask handlers.
# The dashboard runs in the same process as the MCP server, so get_bridge()
# returns the shared singleton. Responses are cached with a short TTL to
# avoid flooding Altium when multiple browser tabs are open.
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
# Per-key fetch locks for single-flight caching. Without these, a burst of
# requests for the same key (the dashboard fires several at once) each see a
# cache miss and each launch their own bridge call -- a thundering herd of
# duplicate work. Single-flight collapses them to one fetch per key.
_keyfetch_locks: dict[str, threading.Lock] = {}

# Last successfully-fetched PCB geometry, kept so a transient fetch failure
# (timeout / Altium busy mid-operation) serves the last good board instead
# of blanking the Drawing/Assembly tabs and wrongly claiming Altium is down.
_last_good_geometry: dict[str, Any] = {}

# Last good project snapshot (focused / nets / bom / messages / ...), kept
# for the same reason: a transient snapshot fetch failure should serve the
# last good data instead of blanking every snapshot-backed tab (Nets, BOM,
# Messages, Project) at once.
_last_good_snapshot: dict[str, Any] = {}

# NOTE: we deliberately do NOT serialise bridge calls with a process-wide
# lock. The IPC scheme is per-request-file (request_<id>.json /
# response_<id>.json), so concurrent callers never collide -- each polls
# only its own response. A global lock here was actively harmful: it held
# a second caller from even WRITING its request file until the first call
# finished, so Pascal couldn't pick up a request that did not yet exist
# (observed as multi-second "pickup" latency). Let every caller publish
# immediately; Pascal serialises the actual processing on its own side.


def _cache_peek(key: str, ttl_seconds: float) -> Any:
    """Return the cached value for key if fresh, else None.

    Unlike _cached this NEVER triggers a fetch -- it is for opportunistic
    reads where a cache miss should just be skipped, not waited on.
    """
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl_seconds:
            return hit[1]
    return None


def _pcb_geometry_cached(ttl_seconds: float = 30.0):
    """Memoized `generic.get_pcb_geometry` fetch.

    The geometry payload is the single most expensive call in the
    dashboard -- on a real board it takes 30-60 s because the Pascal
    side walks every track / arc / pad / via / region / component /
    text on the board. The dashboard fires it from THREE endpoints
    (/api/drawing/pcb, /api/pcb/components, /api/drawing/pcb/layers)
    so without memoization the assembly-tab load cost is 3x that.

    Single-flight collapses concurrent callers to one IPC round-trip
    AND a fresh fetch is bounded to once per TTL window. Refresh
    button explicitly invalidates by calling /api/refresh/pcb.
    """
    val = _cached("generic.get_pcb_geometry", ttl_seconds,
                  lambda: _bridge_call("generic.get_pcb_geometry",
                                        {}, timeout=120.0))
    if isinstance(val, dict):
        _last_good_geometry["v"] = val
        return val
    # Fresh fetch failed (timeout / transient IPC hiccup / Altium busy on a
    # long op so it couldn't answer in time). Serve the last good geometry
    # so the board stays up instead of flashing an error. Returns None only
    # if we've NEVER had a successful fetch this session.
    return _last_good_geometry.get("v")


def _cached(key: str, ttl_seconds: float, fn) -> Any:
    """Single-flight memoize: fn() by key for ttl_seconds.

    On a cache miss only ONE caller runs fn(); concurrent callers for the
    same key block on the key's fetch lock, then reuse the freshly-cached
    value. This collapses request bursts into one bridge round-trip.
    """
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl_seconds:
            return hit[1]
        lk = _keyfetch_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _keyfetch_locks[key] = lk

    # Serialise the fetch per key. Whoever gets here first does the work;
    # the rest wait, then fall through to the re-check below and find the
    # value already cached.
    with lk:
        now = time.time()
        with _cache_lock:
            hit = _cache.get(key)
            if hit and (now - hit[0]) < ttl_seconds:
                return hit[1]
        val = fn()
        with _cache_lock:
            # Never cache a failure (None). Caching it would pin the error
            # for the whole TTL window -- e.g. one timed-out geometry fetch
            # would make the Drawing tab claim "Altium not running" for 30s
            # even after Altium answers again. Leaving None uncached means
            # the next caller retries immediately.
            if val is not None:
                _cache[key] = (time.time(), val)
        return val


def _empty_drawing_svg(message: str) -> str:
    """Produce a tiny placeholder SVG used when the Drawing tab has
    nothing to show (Altium offline, no doc, geometry call failed)."""
    safe = (message or "").replace("&", "&amp;").replace("<", "&lt;")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 200" '
        'preserveAspectRatio="xMidYMid meet">'
        '<rect width="100%" height="100%" fill="#1f2937"/>'
        '<text x="400" y="100" text-anchor="middle" '
        'dominant-baseline="middle" '
        'fill="#9ca3af" font-family="JetBrains Mono, monospace" '
        f'font-size="16">{safe}</text>'
        '</svg>'
    )


def _bridge_call(command: str, params: Optional[dict] = None,
                 timeout: float = 12.0) -> Optional[dict]:
    """Send one MCP command via the shared bridge.

    Returns the response data dict on success, or None if Altium is
    unreachable or the call fails. Errors are logged but not raised --
    a dashboard tab missing data is preferable to a crashed endpoint.
    Not serialised: the per-request-file IPC scheme means concurrent
    callers never collide, and each publishes its request immediately
    so Pascal can pick it up the moment its polling loop is free.
    """
    try:
        from eda_agent.bridge import get_bridge
        bridge = get_bridge()
        if not bridge.is_altium_running():
            return None
        return bridge.send_command(command, params or {}, timeout=timeout)
    except Exception as e:
        logger.debug("bridge_call %s failed: %s", command, e)
        return None


# Footprint-policy sweep: footprints per bulk-geometry page, and the per-page
# timeout. The first page also opens the PcbLib, seconds on a large library.
_GEOMETRY_PAGE = 250
_GEOMETRY_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# activity.log tail
# ---------------------------------------------------------------------------

# Pascal's activity.log line shapes:
#
#   Command line:
#     YYYY-MM-DD HH:MM:SS.mmm,duration_ms,command,tag,response_bytes,<payload...>
#   Session-start:
#     YYYY-MM-DD HH:MM:SS.mmm,0,_session_start,version=X,protocol=N
#   Session-end:
#     YYYY-MM-DD HH:MM:SS.mmm,0,_session_end,requests=N
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}),"
    r"(?P<dur>\d+),"
    r"(?P<cmd>[^,]+),"
    r"(?P<tag>[^,]+),"
    r"(?P<bytes>\d+),"
    r"(?P<payload>.*)$"
)
_SESSION_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}),"
    r"0,(?P<kind>_session_start|_session_end),(?P<rest>.*)$"
)

_ERR_RE = re.compile(
    r'"error"\s*:\s*\{[^{}]*?"code"\s*:\s*"(?P<code>[^"]+)"[^{}]*?'
    r'"message"\s*:\s*"(?P<msg>(?:[^"\\]|\\.)*)"'
)

_ID_RE = re.compile(r'"id"\s*:\s*"(?P<id>[0-9a-f]{8,32})"')


@dataclass
class LogEntry:
    timestamp: str
    duration_ms: int
    command: str
    tag: str               # OK | WARN | SLOW | ERR  | EXCEPTION | EMPTY
    response_bytes: int
    request_id: str        # extracted from payload's "id" or "" if not found
    error_code: str        # extracted from payload's error.code, "" if none
    error_msg: str         # extracted from payload's error.message, "" if none
    payload_prefix: str    # full prefix as it appeared on the line

    def severity(self) -> str:
        # tag='OK' covers both success:true and success:false responses; the
        # error_code/message extracted from the payload is the authoritative
        # signal for "this call failed" regardless of the Pascal-side tag.
        if self.error_code or self.tag.strip() in ("ERR", "EXCEPTION", "EMPTY"):
            return "error"
        if self.duration_ms >= 500:
            return "slow"
        if self.duration_ms >= 100:
            return "warn"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "dur_ms": self.duration_ms,
            "cmd": self.command,
            "tag": self.tag.strip(),
            "bytes": self.response_bytes,
            "id": self.request_id,
            "err_code": self.error_code,
            "err_msg": self.error_msg,
            "severity": self.severity(),
        }


@dataclass
class SessionEvent:
    """Synthetic event emitted on _session_start / _session_end rows."""
    timestamp: str
    kind: str              # 'session_start' | 'session_end'
    version: str = ""
    requests: int = 0


def _parse_line(line: str) -> Optional[LogEntry | SessionEvent]:
    """Parse one activity.log line. Returns None for unparseable lines."""
    stripped = line.strip()
    if not stripped:
        return None

    # Session events have a different shape (no tag / response_bytes columns).
    sm = _SESSION_RE.match(stripped)
    if sm:
        rest = sm["rest"]
        if sm["kind"] == "_session_start":
            version = ""
            v = re.search(r"version=([^\s,]+)", rest)
            if v:
                version = v.group(1)
            return SessionEvent(
                timestamp=sm["ts"], kind="session_start", version=version,
            )
        # _session_end
        reqs = 0
        v = re.search(r"requests=(\d+)", rest)
        if v:
            reqs = int(v.group(1))
        return SessionEvent(
            timestamp=sm["ts"], kind="session_end", requests=reqs,
        )

    m = _LINE_RE.match(stripped)
    if not m:
        return None

    cmd = m["cmd"].strip()
    payload = m["payload"]

    id_m = _ID_RE.search(payload)
    err_m = _ERR_RE.search(payload)
    return LogEntry(
        timestamp=m["ts"],
        duration_ms=int(m["dur"]),
        command=cmd,
        tag=m["tag"].strip(),
        response_bytes=int(m["bytes"]),
        request_id=(id_m.group("id")[:8] if id_m else ""),
        error_code=(err_m.group("code") if err_m else ""),
        error_msg=(err_m.group("msg") if err_m else ""),
        payload_prefix=payload[:200],
    )


class ActivityTailer:
    """Background thread that tails activity.log and feeds a deque + SSE queue.

    Designed to survive log truncation and rotation: if the file shrinks
    below our last-seen offset, we seek to the start and replay. Each
    listener gets its own threading.Event + queue so SSE can fan out
    without blocking the tailer.
    """

    BUFFER_LINES = 2000

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.entries: deque[LogEntry] = deque(maxlen=self.BUFFER_LINES)
        self.session: Optional[SessionEvent] = None
        self._listeners: list[threading.Event] = []
        self._listener_queues: dict[int, deque[dict]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="dashboard-tailer", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def subscribe(self) -> tuple[threading.Event, deque[dict]]:
        """Register an SSE listener. Returns (wake-event, queue)."""
        wake = threading.Event()
        q: deque[dict] = deque(maxlen=200)
        key = id(wake)
        with self._lock:
            self._listeners.append(wake)
            self._listener_queues[key] = q
        return wake, q

    def unsubscribe(self, wake: threading.Event) -> None:
        with self._lock:
            try:
                self._listeners.remove(wake)
            except ValueError:
                pass
            self._listener_queues.pop(id(wake), None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            session = self.session
            return {
                "session": {
                    "version": session.version if session else "",
                    "kind": session.kind if session else "",
                    "ts": session.timestamp if session else "",
                },
                "entries": [e.to_dict() for e in reversed(self.entries)],
            }

    def _broadcast(self, payload: dict) -> None:
        with self._lock:
            for wake in self._listeners:
                q = self._listener_queues.get(id(wake))
                if q is not None:
                    q.append(payload)
                wake.set()

    def _ingest(self, line: str) -> None:
        parsed = _parse_line(line)
        if parsed is None:
            return
        if isinstance(parsed, SessionEvent):
            with self._lock:
                self.session = parsed
            self._broadcast({
                "type": "session",
                "kind": parsed.kind,
                "version": parsed.version,
                "ts": parsed.timestamp,
            })
            return
        # LogEntry
        with self._lock:
            self.entries.append(parsed)
        self._broadcast({"type": "entry", "entry": parsed.to_dict()})

    def _run(self) -> None:
        offset = 0
        # Initial backfill: read everything that's there so the UI has
        # session context immediately on first paint.
        try:
            if self.log_path.exists():
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        self._ingest(line.rstrip("\n"))
                    offset = f.tell()
        except OSError as e:
            logger.debug("dashboard tailer initial read failed: %s", e)

        while not self._stop.is_set():
            try:
                if not self.log_path.exists():
                    time.sleep(0.5)
                    continue
                size = self.log_path.stat().st_size
                if size < offset:
                    # Truncated or rotated. Replay from scratch.
                    offset = 0
                if size == offset:
                    time.sleep(0.3)
                    continue
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    for line in f:
                        if not line.endswith("\n"):
                            # Partial line, keep position before it and retry.
                            break
                        self._ingest(line.rstrip("\n"))
                        offset = f.tell()
            except OSError as e:
                logger.debug("dashboard tailer read failed: %s", e)
                time.sleep(0.5)


# ---------------------------------------------------------------------------
# Stats aggregation
# ---------------------------------------------------------------------------

def _aggregate(entries: Iterable[LogEntry]) -> list[dict[str, Any]]:
    """Compute per-command N / total_ms / max_ms / avg_ms from a stream."""
    by_cmd: dict[str, dict[str, int]] = {}
    for e in entries:
        slot = by_cmd.setdefault(e.command, {"n": 0, "total": 0, "max": 0})
        slot["n"] += 1
        slot["total"] += e.duration_ms
        if e.duration_ms > slot["max"]:
            slot["max"] = e.duration_ms
    out: list[dict[str, Any]] = []
    for cmd, s in by_cmd.items():
        avg = (s["total"] // s["n"]) if s["n"] else 0
        out.append({
            "command": cmd, "n": s["n"],
            "avg_ms": avg, "max_ms": s["max"], "total_ms": s["total"],
        })
    out.sort(key=lambda r: r["max_ms"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Artifacts (recent files produced by tools)
# ---------------------------------------------------------------------------

# Extensions we want to surface in the dashboard. Each tool that produces a
# file uses one of these: SVG preview from design_preview_plan, PDF/STEP/DXF
# from the export_* tools, PNG screenshots from export_image, JSON snapshots
# from design_review_snapshot, etc.
_ARTIFACT_EXTS = {
    ".svg", ".pdf", ".png", ".jpg", ".jpeg", ".step", ".stp", ".dxf",
    ".json", ".jsonl", ".csv", ".html", ".txt",
}
_ARTIFACT_MAX_ROWS = 60


def _scan_artifacts(workspace_dir: Path) -> list[dict[str, Any]]:
    """Return recent artifact files near the workspace.

    Scans workspace_dir + its __Previews sibling (if present) plus the
    `<repo>/.symbol_cache/` directory where the design preview SVGs land.
    Filters to known interesting extensions, returns newest-first.
    """
    candidates: list[Path] = []
    roots = [workspace_dir, workspace_dir / "__Previews"]
    # Repo-level .symbol_cache where design previews go by default.
    try:
        repo_root = Path(__file__).resolve().parents[3]
        cache = repo_root / ".symbol_cache"
        if cache.exists():
            roots.append(cache)
    except (IndexError, OSError):
        pass

    for root in roots:
        try:
            if not root.exists():
                continue
            for p in root.iterdir():
                if not p.is_file():
                    continue
                if p.suffix.lower() not in _ARTIFACT_EXTS:
                    continue
                if p.name in (
                    "activity.log", "bridge_trace.log", "mcp_config.json",
                    "intent.txt", "open_dashboard.url",
                ):
                    continue
                # Skip noisy per-request IPC files.
                if p.name.startswith(("request_", "response_", "progress_")):
                    continue
                candidates.append(p)
        except OSError:
            continue

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = candidates[:_ARTIFACT_MAX_ROWS]

    out: list[dict[str, Any]] = []
    for p in candidates:
        try:
            st = p.stat()
            out.append({
                "path": str(p),
                "name": p.name,
                "dir":  str(p.parent),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "ext": p.suffix.lower().lstrip("."),
            })
        except OSError:
            continue
    return out


def _safe_artifact_path(target: str, workspace_dir: Path) -> bool:
    """Whitelist gate: only let the dashboard open files we listed."""
    try:
        p = Path(target).resolve()
    except (OSError, ValueError):
        return False
    if not p.exists() or not p.is_file():
        return False
    if p.suffix.lower() not in _ARTIFACT_EXTS:
        return False
    # Must live under one of the scanned roots.
    try:
        repo_root = Path(__file__).resolve().parents[3]
    except (IndexError, OSError):
        repo_root = workspace_dir
    allowed_roots = [
        workspace_dir.resolve(),
        (workspace_dir / "__Previews").resolve() if (workspace_dir / "__Previews").exists() else None,
        (repo_root / ".symbol_cache").resolve() if (repo_root / ".symbol_cache").exists() else None,
    ]
    for root in allowed_roots:
        if root is None:
            continue
        try:
            p.relative_to(root)
            return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

_HTML_PATH = Path(__file__).resolve().parent / "dashboard_static" / "index.html"


def _watch_open_dashboard_sentinel(workspace_dir: Path, stop: threading.Event) -> None:
    """Watch workspace/open_dashboard.url and open the browser when it appears.

    The Pascal-side "Open Dashboard" button writes the URL to this file. The
    bridge keep-alive thread also watches the same sentinel, but that only
    runs after the first MCP call attaches the bridge. The dashboard server
    is typically already running long before MCP attaches, so this watcher
    is what actually makes the button respond promptly on first click.
    """
    sentinel = workspace_dir / "open_dashboard.url"
    while not stop.wait(0.5):
        if not sentinel.exists():
            continue
        try:
            url = sentinel.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        try:
            sentinel.unlink()
        except OSError:
            pass
        if not url.startswith("http://") and not url.startswith("https://"):
            continue
        try:
            import webbrowser
            webbrowser.open(url)
            logger.info("opened dashboard URL via sentinel: %s", url)
        except Exception as e:
            logger.debug("webbrowser.open failed: %s", e)


def _hot_reload_render_modules() -> None:
    """Reload eda_agent.render submodules if their .py files changed on disk.

    Eliminates the "/mcp reconnect to see a renderer change" friction --
    the dashboard's drawing endpoints call this once per request, and if
    sch_svg.py or pcb_svg.py have been touched since the last import,
    the in-process module cache gets refreshed. Cheap stat() per request,
    and a full import only when the file actually changed.
    """
    import importlib
    import os
    try:
        from .. import render as render_pkg
    except ImportError:
        return
    # Track per-module last-seen mtime on the package itself so we don't
    # need a global -- the dict survives as long as the module is imported.
    if not hasattr(render_pkg, "_hot_mtimes"):
        render_pkg._hot_mtimes = {}
    for sub in ("sch_svg", "pcb_svg"):
        full = f"eda_agent.render.{sub}"
        mod = sys.modules.get(full)
        if mod is None or not getattr(mod, "__file__", None):
            continue
        try:
            mt = os.path.getmtime(mod.__file__)
        except OSError:
            continue
        prev = render_pkg._hot_mtimes.get(full)
        if prev is None:
            render_pkg._hot_mtimes[full] = mt
            continue
        if mt > prev:
            try:
                importlib.reload(mod)
                # Re-export the public names through the package so callers
                # using `from ..render import render_sch_svg` get the new
                # bindings on the next import.
                importlib.reload(render_pkg)
                render_pkg._hot_mtimes[full] = mt
                logger.info("hot-reloaded %s (mtime change)", full)
            except Exception as e:
                logger.warning("hot-reload of %s failed: %s", full, e)


#: Hostnames a browser may legitimately use to reach a loopback bind.
#: Anything else in the Host header means the request arrived through a
#: name that resolves here without belonging here, which is what DNS
#: rebinding is.
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", ""})

#: Escape hatch for a deliberate non-loopback bind. Comma separated
#: hostnames, no ports. Sharing the dashboard is already gated behind an
#: explicit --host and a printed warning; this lets that case name the
#: address it will actually be reached by.
_ALLOWED_HOSTS_ENV = "EDA_AGENT_DASHBOARD_ALLOWED_HOSTS"


def _hostname_of(value: str) -> str:
    """The host part of a Host or Origin header, lowercased, no port.

    Handles the bracketed IPv6 form, where the colons inside the
    brackets are not a port separator.
    """
    value = (value or "").strip().lower()
    if value.startswith("http://"):
        value = value[7:]
    elif value.startswith("https://"):
        value = value[8:]
    value = value.split("/")[0]
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[:end + 1]
        return value
    return value.split(":")[0]


def _allowed_hostnames() -> frozenset:
    import os

    extra = os.environ.get(_ALLOWED_HOSTS_ENV, "")
    names = {n.strip().lower() for n in extra.split(",") if n.strip()}
    return frozenset(_LOOPBACK_NAMES | names)


def install_origin_guard(app) -> None:
    """Refuse requests that did not come from a loopback name.

    WHY THIS EXISTS. Every endpoint here is unauthenticated, and
    /api/tool/run invokes any registered tool with caller-supplied
    arguments. On this project that means reading and mutating client
    designs under NDA, so the only thing standing between a web page and
    the board is that the server listens on loopback.

    Loopback is not the protection it looks like. Ordinary CSRF is
    already blocked, because the endpoint requires a JSON content type
    and that forces a preflight the server never approves. DNS
    REBINDING is not: an attacker page served from a domain whose DNS
    then answers 127.0.0.1 is same-origin as far as the browser is
    concerned, so no preflight applies and the request arrives looking
    entirely ordinary.

    MEASURED before this guard: a JSON POST carrying
    Host: evil.example.com was accepted and ran a command against a live
    Altium, and GET /api/tools listed all 412 tools to the same caller.

    What separates the two cases is the Host header. A browser sends the
    name the user typed, so a rebound request carries the attacker's
    domain while a genuine one carries a loopback name. Origin is
    checked too when present, which costs nothing and catches a plain
    cross-site attempt earlier.
    """
    from flask import jsonify, request

    @app.before_request
    def _reject_foreign_origin():
        allowed = _allowed_hostnames()

        host = _hostname_of(request.headers.get("Host", ""))
        if host not in allowed:
            return jsonify({
                "ok": False,
                "reason": (
                    f"refusing a request addressed to {host!r}. This "
                    f"dashboard is unauthenticated and reachable only as "
                    f"localhost, because every endpoint can drive the "
                    f"EDA application. A Host that is not a loopback name "
                    f"is how a DNS rebinding attack arrives. Set "
                    f"{_ALLOWED_HOSTS_ENV} if you are deliberately serving "
                    f"this to another machine."),
            }), 403

        # Origin is absent on same-origin GETs and on direct navigation,
        # so only a PRESENT and foreign one is a refusal. Treating a
        # missing Origin as hostile would break the page itself.
        origin = request.headers.get("Origin")
        if origin and _hostname_of(origin) not in allowed:
            return jsonify({
                "ok": False,
                "reason": (
                    f"refusing a cross-origin request from {origin!r}. "
                    f"This dashboard has no authentication and its "
                    f"endpoints drive the EDA application."),
            }), 403

        return None


def create_app(workspace_dir: Optional[Path] = None) -> Flask:
    if workspace_dir is None:
        workspace_dir = get_config().workspace_dir
    log_path = workspace_dir / "activity.log"

    tailer = ActivityTailer(log_path)
    tailer.start()

    sentinel_stop = threading.Event()
    sentinel_thread = threading.Thread(
        target=_watch_open_dashboard_sentinel,
        args=(workspace_dir, sentinel_stop),
        name="dashboard-sentinel-watch",
        daemon=True,
    )
    sentinel_thread.start()

    # Heartbeat: write Unix epoch seconds to workspace/dashboard.heartbeat
    # every 3s. The Pascal StatusForm reads this to decide whether the
    # "Open Dashboard" button is meaningfully enabled (fresh heartbeat =
    # dashboard process is alive and reachable, stale = nobody home).
    # Cheap (one tiny file write per 3s); independent of MCP heartbeat
    # so a standalone dashboard run also keeps the button live.
    heartbeat_stop = threading.Event()
    heartbeat_path = workspace_dir / "dashboard.heartbeat"
    heartbeat_tmp = workspace_dir / "dashboard.heartbeat.tmp"
    def _heartbeat_loop() -> None:
        # IMPORTANT: write a local-naive epoch (seconds since 1970-01-01
        # measured against the local clock), NOT time.time() which is
        # UTC. Pascal reads with (Now - 25569) * 86400 where Now is local,
        # so a UTC value here would skew the comparison by the user's
        # timezone offset (e.g. UTC+2 = 7200s skew >> the 15s freshness
        # window, button stays grey even when the dashboard is alive).
        from datetime import datetime as _dt
        _epoch = _dt(1970, 1, 1)
        while not heartbeat_stop.is_set():
            try:
                ts = (_dt.now() - _epoch).total_seconds()
                # Atomic write: stage to a temp file, then os.replace onto
                # the final name. A plain write_text holds dashboard.heartbeat
                # open for write every 3s; when the Altium polling loop opens
                # it for read in that window it hits a Windows sharing
                # violation, which the script engine surfaces as a modal that
                # stalls the loop. Writing to a temp + atomic rename means the
                # reader only ever opens a complete, closed file. If the
                # rename loses a race with the reader holding the old file,
                # os.replace raises -> we skip this tick and refresh on the
                # next one (staleness window is well above 3s).
                heartbeat_tmp.write_text(str(ts), encoding="utf-8")
                replace_with_retry(heartbeat_tmp, heartbeat_path)
            except OSError:
                pass
            heartbeat_stop.wait(3.0)
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="dashboard-heartbeat", daemon=True,
    )
    heartbeat_thread.start()
    def _remove_heartbeat() -> None:
        heartbeat_stop.set()
        try: heartbeat_path.unlink()
        except OSError: pass
    import atexit as _atexit
    _atexit.register(_remove_heartbeat)

    app = Flask("eda-agent-dashboard")
    # Before any route, and before anything reads the body.
    install_origin_guard(app)
    app.config["WORKSPACE_DIR"] = str(workspace_dir)
    app.config["TAILER"] = tailer

    @app.route("/")
    def index() -> Response:
        if not _HTML_PATH.exists():
            return Response(
                "<h1>dashboard_static/index.html missing</h1>",
                status=500, mimetype="text/html",
            )
        resp = Response(_HTML_PATH.read_text(encoding="utf-8"),
                        mimetype="text/html")
        # Local dev dashboard: every page load must read fresh from disk.
        # Without these headers some browsers cache index.html for the
        # full session, so iteration on the JS / HTML looks like it has
        # no effect even though Flask reads the file every request.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.after_request
    def _no_cache_api(resp: Response) -> Response:
        # All JSON endpoints need fresh reads too -- they read either the
        # live workspace or a short-TTL bridge cache. Tagging them no-store
        # stops browsers / proxies from holding onto stale snapshots.
        from flask import request as _req
        if _req.path.startswith("/api/") or _req.path == "/":
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    @app.route("/api/snapshot")
    def snapshot() -> Response:
        return jsonify(tailer.snapshot())

    @app.route("/api/recovery")
    def recovery() -> Response:
        """Current DelphiScript-fault state for the recovery banner.

        Returns ``{"fault": null}`` when the polling loop is healthy, or the
        recorded diagnosis + numbered recovery steps when a fault is pending
        (cleared automatically once a command succeeds again).
        """
        from ..bridge.fault_state import read_fault
        payload = read_fault(Path(app.config["WORKSPACE_DIR"]))
        if payload is None:
            return jsonify({"fault": None})
        return jsonify(payload)

    @app.route("/api/footprint-policy")
    def footprint_policy() -> Response:
        """Audit the focused PcbLib for inconsistent footprint policies.

        Sweeps every footprint, infers the library's own conventions, and
        returns the deviations: the read-only view of
        ``lib_audit_footprint_policies``. Geometry comes from the bulk
        ``library.get_library_geometry`` command, paged so a thousand-footprint
        library is a few round trips rather than a thousand. Returns
        ``{available: false}`` when no PcbLib is open (or Altium is
        unreachable), so the panel can show a hint instead of an error.
        """
        from ..design.footprint_policy import (
            audit_footprint_library,
            plan_footprint_fixes,
        )

        footprints: list = []
        library_path = None
        offset, total = 0, None
        while total is None or offset < total:
            page = _bridge_call("library.get_library_geometry",
                                {"offset": offset, "limit": _GEOMETRY_PAGE},
                                timeout=_GEOMETRY_TIMEOUT)
            if not isinstance(page, dict):
                break
            library_path = library_path or page.get("library_path")
            total = page.get("total") or 0
            batch = page.get("footprints") or []
            footprints.extend(batch)
            if not batch:
                break
            offset += len(batch)

        if not footprints:
            return jsonify({"available": False,
                            "reason": "no PcbLib open, or Altium unreachable"})
        report = audit_footprint_library(footprints)
        report["fixes"] = plan_footprint_fixes(report)
        report["available"] = True
        report["library_path"] = library_path
        return jsonify(report)

    @app.route("/api/altium/version")
    def altium_version() -> Response:
        """Live script-version probe.

        The activity-log session entry only updates on a Pascal restart,
        so the Status tab's "script version" KPI was lying about the
        running build whenever the user redeployed without restarting
        Altium's polling loop. This endpoint pings the live Pascal
        script (via `application.ping` which returns SCRIPT_VERSION)
        and ALSO reads the on-disk Main.pas to show the deployed
        version. Mismatch = the user needs to Ctrl+F3 + restart.

        Short TTL (3s) so the dashboard's periodic poll doesn't hammer
        Altium but the readout still feels live.
        """
        from ..tools.application import _bundled_script_version
        deployed = _bundled_script_version() or ""
        # Live ping. Cached briefly (3s) so a 1-Hz dashboard poll
        # doesn't round-trip Altium every second.
        ping = _cached("altium.ping", 3.0,
                       lambda: _bridge_call("application.ping", {},
                                            timeout=4.0))
        running = ""
        altium_up = False
        if isinstance(ping, dict):
            altium_up = bool(ping.get("pong"))
            running = ping.get("script_version") or ""
        stale = bool(running and deployed and running != deployed)
        return jsonify({
            "ok": True,
            "running": running,
            "deployed": deployed,
            "altium_up": altium_up,
            "stale": stale,
        })

    @app.route("/api/stats")
    def stats() -> Response:
        with tailer._lock:
            entries = list(tailer.entries)
        return jsonify({"commands": _aggregate(entries)})

    @app.route("/api/lint", methods=["GET", "POST"])
    def lint() -> Response:
        """Run the bundled design-lint sweep and return a consolidated report.

        Wraps the same chain ``design_lint_report`` runs from MCP: each
        ``audit.*`` check fires in sequence, the responses are collected
        under their section name, plus a per-section ``{checked,
        violations}`` summary and a top-level total. The dashboard's
        Lint panel pulls from here for in-browser drill-down without
        requiring an agent call.

        Query params (all optional):
          - ``bad_connection_tolerance_mils`` (default 1.0): forwarded
            to ``audit.find_bad_connections``.
          - ``run_drc`` (default false): also runs Altium's DRC.

        Returns the same shape as the ``design_lint_report`` MCP tool
        (summary / sections / totals / _failed).
        """
        from flask import request as _req
        try:
            tol = float(_req.args.get("bad_connection_tolerance_mils", "1.0"))
        except (TypeError, ValueError):
            tol = 1.0
        run_drc = (_req.args.get("run_drc", "false").lower()
                   in ("true", "1", "yes"))

        # Shared lint state (lives in tools.review at module scope so
        # this endpoint and the MCP design_lint_report agree -- single
        # source of truth for which audits run, in what order, with what
        # severity).
        try:
            from ..tools.review import (
                LINT_SEVERITY as _LINT_SEVERITY,
                LINT_AUDIT_LIST as _LINT_AUDIT_LIST,
            )
        except Exception:
            _LINT_SEVERITY = {}
            _LINT_AUDIT_LIST = []

        sections: dict[str, Any] = {}
        summary: dict[str, dict[str, int]] = {}
        failed: list[str] = []

        def _run(name: str, command: str, params: dict[str, Any] | None = None):
            try:
                data = _bridge_call(command, params or {}, timeout=30)
            except Exception as e:
                failed.append(f"{name}: {e}")
                sections[name] = {"ok": False, "error": str(e)}
                return
            if data is None:
                failed.append(f"{name}: altium-not-running")
                sections[name] = {"ok": False, "error": "altium-not-running"}
                return
            sections[name] = data
            if isinstance(data, dict):
                ch = data.get("checked")
                vi = data.get("violations")
                if isinstance(ch, int) or isinstance(vi, int):
                    summary[name] = {
                        "checked": int(ch) if isinstance(ch, int) else 0,
                        "violations": int(vi) if isinstance(vi, int) else 0,
                        "severity": _LINT_SEVERITY.get(name, "info"),
                    }

        # Iterate the shared LINT_AUDIT_LIST. find_bad_connections is the
        # only audit that takes a param; special-case it.
        for name, command in _LINT_AUDIT_LIST:
            if name == "find_bad_connections":
                _run(name, command, {"tolerance_mils": str(tol)})
            else:
                _run(name, command)

        # Python-side BOM checks (no Pascal handler). Fetch BOM once,
        # share across the three helpers. Kept separate from
        # LINT_AUDIT_LIST because the call shape differs.
        try:
            from eda_agent.tools.audit import (
                find_unconnected_ic_pins_from_bom,
                find_pin_net_name_mismatches_from_bom,
                find_missing_decoupling_from_bom,
            )
            bom = _bridge_call("project.get_bom",
                                {"limit": "5000"}, timeout=20)
            for name, fn in [
                ("find_unconnected_ic_pins",
                 find_unconnected_ic_pins_from_bom),
                ("find_pin_net_name_mismatches",
                 find_pin_net_name_mismatches_from_bom),
                ("find_missing_decoupling",
                 find_missing_decoupling_from_bom),
            ]:
                data = fn(bom or {})
                sections[name] = data
                summary[name] = {
                    "checked": data.get("checked", 0),
                    "violations": data.get("violations", 0),
                    "severity": _LINT_SEVERITY.get(name, "info"),
                }
        except Exception as e:
            failed.append(f"bom-side audits: {e}")

        if run_drc:
            _run("drc", "pcb.run_drc")

        sev_buckets: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        for s in summary.values():
            sev = s.get("severity", "info")
            sev_buckets[sev] = sev_buckets.get(sev, 0) + s.get("violations", 0)
        totals = {
            "violations": sum(s.get("violations", 0)
                              for s in summary.values()),
            "violations_by_severity": sev_buckets,
            "checks_run": len(summary),
            "checks_failed": len(failed),
        }
        return jsonify({
            "summary": summary,
            "sections": sections,
            "totals": totals,
            "_failed": failed,
        })

    @app.route("/api/health")
    def health() -> Response:
        """Run the doctor preflight, return as JSON.

        Doctor checks talk to Altium (ping, version, save_all) so this
        endpoint can take a second or two. The dashboard calls it on
        demand, not on every render.
        """
        try:
            from eda_agent.diag.doctor import run_doctor_checks
            checks = run_doctor_checks(library_paths=[])
            return jsonify({
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status.value,
                        "message": c.message,
                        "fix": c.fix,
                        "severity": c.severity.value,
                    }
                    for c in checks
                ],
                "ok": all(c.status.value in ("pass", "skip") for c in checks),
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "checks": []})

    @app.route("/api/artifacts")
    def artifacts() -> Response:
        """List recent files produced by tools (preview SVGs, exports).

        Pure filesystem scan against the workspace and its __Previews
        sibling. Returns newest-first. The dashboard turns each entry
        into a clickable row that opens the file via the OS handler
        (browsers refuse to open file:// links from http origins; the
        click goes back through /api/artifacts/open).
        """
        files = _scan_artifacts(workspace_dir)
        return jsonify({"artifacts": files})

    @app.route("/api/artifacts/open", methods=["POST"])
    def artifacts_open() -> Response:
        """Open an artifact via the OS default handler."""
        from flask import request as _req
        body = _req.get_json(silent=True) or {}
        target = body.get("path", "")
        if not _safe_artifact_path(target, workspace_dir):
            return jsonify({"ok": False, "error": "path not allowed"}), 400
        try:
            import os as _os
            _os.startfile(target)  # type: ignore[attr-defined]
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/intent")
    def intent() -> Response:
        """Read the current conversation intent the planner set."""
        intent_path = workspace_dir / "intent.txt"
        text = ""
        try:
            if intent_path.exists():
                text = intent_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        return jsonify({"intent": text})

    # -----------------------------------------------------------------
    # Design-centric proxy endpoints. Each one calls the bridge with a
    # short cache TTL so multiple browser tabs don't hammer Altium.
    # Errors return ``{"ok": False, "reason": "..."}`` so the UI can
    # render a meaningful empty state instead of a JS exception.
    # -----------------------------------------------------------------

    def _proxy(command: str, params: Optional[dict] = None,
               ttl: float = 8.0, timeout: float = 12.0,
               cache_key: Optional[str] = None) -> dict:
        key = cache_key or f"{command}:{json.dumps(params or {}, sort_keys=True)}"
        def call():
            try:
                data = _bridge_call(command, params, timeout=timeout)
            except Exception as e:
                return {"ok": False, "reason": str(e)}
            if data is None:
                return {"ok": False, "reason": "altium-not-running"}
            return {"ok": True, "data": data}
        return _cached(key, ttl, call)

    def _project_snapshot() -> dict:
        """One bundled IPC call -> focused / documents / stats / bom / nets /
        messages / path. The Pascal `project.dashboard_snapshot` handler
        gathers all of it server-side so the dashboard pays the poll + IO
        round-trip ONCE instead of 7x. Cached 15s; all /api/project/* and
        the review-summary endpoints read from this single entry.
        """
        def call():
            # Return None (NOT an {"ok":False} dict) on failure so _cached
            # doesn't pin the failure for the whole TTL -- otherwise one
            # flaky fetch blanks Nets/BOM/Messages for 15s. None => the next
            # caller retries immediately.
            try:
                data = _bridge_call("project.dashboard_snapshot", {},
                                    timeout=45.0)
            except Exception:
                return None
            return {"ok": True, "data": data} if data is not None else None
        val = _cached("project.dashboard_snapshot", 15.0, call)
        if val is not None:
            _last_good_snapshot["v"] = val
            return val
        # Transient failure: serve the last good snapshot so the panels keep
        # their data. Only report not-running if we've never had one.
        last = _last_good_snapshot.get("v")
        if last is not None:
            return last
        return {"ok": False, "reason": "altium-not-running"}

    def _snapshot_section(section: str) -> dict:
        """Pull one section out of the bundled snapshot, shaped as the
        individual endpoints used to return: {"ok": bool, "data": ...}."""
        snap = _project_snapshot()
        if not snap.get("ok"):
            return snap
        bundle = snap.get("data") or {}
        return {"ok": True, "data": bundle.get(section)}

    @app.route("/api/project/info")
    def project_info() -> Response:
        """Focused project + documents + design stats -- one bundled call."""
        return jsonify({
            "focused":   _snapshot_section("focused"),
            "documents": _snapshot_section("documents"),
            "stats":     _snapshot_section("stats"),
            "path":      _snapshot_section("path"),
        })

    @app.route("/api/project/components")
    def project_components() -> Response:
        """Live BOM, served from the bundled snapshot."""
        return jsonify(_snapshot_section("bom"))

    @app.route("/api/project/nets")
    def project_nets() -> Response:
        """Net inventory, served from the bundled snapshot."""
        return jsonify(_snapshot_section("nets"))

    @app.route("/api/project/messages")
    def project_messages() -> Response:
        """Compiler / ERC messages, served from the bundled snapshot."""
        return jsonify(_snapshot_section("messages"))

    @app.route("/api/libraries")
    def libraries_inventory() -> Response:
        """Library inventory written by design.snapshot_inventory.

        Looks for ``workspace/inventory.json`` (a cached snapshot the agent
        writes when it calls ``design_snapshot_inventory``). When absent,
        returns an empty state hint so the UI can prompt the user to run
        the snapshot from the conversation side.
        """
        inv_path = workspace_dir / "inventory.json"
        if inv_path.exists():
            try:
                data = json.loads(inv_path.read_text(encoding="utf-8"))
                return jsonify({"ok": True, "data": data,
                                "mtime": inv_path.stat().st_mtime,
                                "source": str(inv_path)})
            except (OSError, json.JSONDecodeError) as e:
                return jsonify({"ok": False, "reason": f"inventory.json unreadable: {e}"})
        return jsonify({"ok": False, "reason": "no-inventory-cached",
                        "hint": "Run design_snapshot_inventory to populate."})

    @app.route("/api/plan")
    def design_plan() -> Response:
        """Current DesignPlan if one is cached in the workspace.

        Reads ``workspace/plan.json`` (written by tools that hand a plan
        to execute_plan). Falls back to any ``<workspace>/*.canvas.json``
        in the workspace dir, which design_execute_plan writes alongside
        the project. Empty state means "no plan in flight".
        """
        plan_path = workspace_dir / "plan.json"
        if plan_path.exists():
            try:
                data = json.loads(plan_path.read_text(encoding="utf-8"))
                return jsonify({"ok": True, "data": data,
                                "mtime": plan_path.stat().st_mtime,
                                "source": str(plan_path)})
            except (OSError, json.JSONDecodeError) as e:
                return jsonify({"ok": False, "reason": f"plan.json unreadable: {e}"})
        # Last-ditch: look for a canvas snapshot next to any open project.
        canvas_candidates = sorted(workspace_dir.glob("*.canvas.json"),
                                   key=lambda p: p.stat().st_mtime,
                                   reverse=True)
        if canvas_candidates:
            cp = canvas_candidates[0]
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                return jsonify({"ok": True, "data": data,
                                "mtime": cp.stat().st_mtime,
                                "source": str(cp),
                                "kind": "canvas"})
            except (OSError, json.JSONDecodeError):
                pass
        return jsonify({"ok": False, "reason": "no-plan-cached",
                        "hint": "design_preview_plan / design_execute_plan write the cached snapshot."})

    @app.route("/api/component/<designator>")
    def component_detail(designator: str) -> Response:
        """Single-component drill-in: parameters, pins, datasheet.

        Speed-critical: this fires on every BOM-row / issue-row click.
        We deliberately pass ``with_pin_nets=false`` so the batch handler
        SKIPS SmartCompile -- a recompile on a multi-sheet project costs
        5-15s and would be paid on every single click. The component's
        pins-with-nets come from the cached project snapshot instead
        (get_bom already compiled the project when the snapshot was
        built), so the drawer still shows each pin's net for free.
        """
        from flask import abort
        des = designator.strip()
        if not des:
            abort(400)
        info = _bridge_call(
            "project.get_component_info_batch",
            {"designators": des, "with_pin_nets": "false",
             "with_parameters": "true"},
            timeout=15,
        )
        if info is None:
            return jsonify({"ok": False, "reason": "altium-not-running"})
        comps = info.get("components") if isinstance(info, dict) else None
        if not comps:
            return jsonify({"ok": False, "reason": "not-found"})
        comp = comps[0]
        # Graft pins-with-nets from the snapshot BOM IF it is already
        # cached -- peek only, never trigger a fetch. A cold snapshot
        # would mean an 8-12s compile here; not worth it for a drawer.
        # When the snapshot is warm (the common case -- the user got to
        # this drawer from a tab that already loaded it) the pins show
        # their nets; when cold the drawer just shows pin number/name.
        try:
            snap = _cache_peek("project.dashboard_snapshot", 15.0)
            if snap and snap.get("ok"):
                bom = (snap.get("data") or {}).get("bom") or {}
                for c in (bom.get("components") or []):
                    if isinstance(c, dict) and c.get("designator") == des:
                        if c.get("pins"):
                            comp["pins"] = c["pins"]
                        break
        except Exception as e:
            logger.debug("snapshot pin-graft failed for %s: %s", des, e)
        return jsonify({"ok": True, "data": comp})

    # -----------------------------------------------------------------
    # Actions: cross-probe + highlight + clear. Each proxies one MCP
    # command. Never cached (the user is asking for an interactive jump).
    # -----------------------------------------------------------------

    @app.route("/api/action/cross_probe", methods=["POST"])
    def action_cross_probe() -> Response:
        from flask import request as _req
        body = _req.get_json(silent=True) or {}
        designator = (body.get("designator") or "").strip()
        target = (body.get("target") or "schematic").strip()
        if not designator:
            return jsonify({"ok": False, "reason": "designator required"}), 400
        if target not in ("schematic", "pcb"):
            target = "schematic"
        try:
            data = _bridge_call("project.cross_probe",
                                {"designator": designator, "target": target},
                                timeout=10)
        except Exception as e:
            return jsonify({"ok": False, "reason": str(e)})
        if data is None:
            return jsonify({"ok": False, "reason": "altium-not-running"})
        return jsonify({"ok": True, "data": data})

    @app.route("/api/action/highlight_net", methods=["POST"])
    def action_highlight_net() -> Response:
        from flask import request as _req
        body = _req.get_json(silent=True) or {}
        name = (body.get("net_name") or "").strip()
        clear = body.get("clear_existing", True)
        if not name:
            return jsonify({"ok": False, "reason": "net_name required"}), 400
        try:
            data = _bridge_call("generic.highlight_net",
                                {"net_name": name,
                                 "clear_existing": "true" if clear else "false"},
                                timeout=10)
        except Exception as e:
            return jsonify({"ok": False, "reason": str(e)})
        if data is None:
            return jsonify({"ok": False, "reason": "altium-not-running"})
        return jsonify({"ok": True, "data": data})

    @app.route("/api/action/clear_highlights", methods=["POST"])
    def action_clear_highlights() -> Response:
        try:
            data = _bridge_call("generic.clear_highlights", {}, timeout=5)
        except Exception as e:
            return jsonify({"ok": False, "reason": str(e)})
        if data is None:
            return jsonify({"ok": False, "reason": "altium-not-running"})
        return jsonify({"ok": True, "data": data})

    # -----------------------------------------------------------------
    # Tool catalog. Every registered MCP tool is exposed to the Actions
    # tab so the user can manually call anything Claude can. Mutating
    # heuristic uses an allowlist of read-only prefixes / exact names;
    # everything else is badged "mutates" so the user sees what changes
    # design state. The badge is informational, not a safety gate.
    # -----------------------------------------------------------------

    # Every tool name is `ns_verb_object`, so read-only classification keys
    # off the verb (the token after the namespace). Pure inspection / export
    # verbs are read-only; everything else mutates. A short exact list covers
    # the actions whose verb is ambiguous (compile, ERC/DRC, view navigation).
    _READ_ONLY_VERBS = frozenset({
        "get", "list", "find", "query", "compare", "crossref", "check",
        "export", "search", "diff", "audit", "preview", "validate",
        "snapshot", "review", "learn", "lint", "datasheet", "calc",
        "plan", "visual", "render", "ping", "diag", "count",
    })
    _READ_ONLY_EXACT = frozenset({
        "proj_compile", "proj_force_recompile", "proj_run_erc", "pcb_run_drc",
        "proj_run_output", "proj_cross_probe", "app_attach", "app_detach",
        # app_run_menu is NOT here: it dispatches an arbitrary menu path,
        # and "File|Save All" writes to disk. Treating it as read-only
        # left the project cache stale after it changed something.
        "app_set_intent", "obj_highlight_net",
        "obj_clear_highlights", "obj_deselect_all", "obj_select", "obj_zoom",
        "obj_switch_view", "obj_refresh_document",
    })

    def _is_mutating_tool(name: str) -> bool:
        if name in _READ_ONLY_EXACT:
            return False
        parts = name.split("_")
        if parts[0] == "audit":          # every audit_* tool only inspects
            return False
        verb = parts[1] if len(parts) >= 2 else parts[0]
        return verb not in _READ_ONLY_VERBS

    @app.route("/api/tools")
    def tools_catalog() -> Response:
        """Catalog every registered MCP tool: name, namespace,
        description, JSON-schema parameters, and a mutating flag the
        UI uses to badge edit-class tools.
        """
        try:
            from eda_agent.server import mcp
        except Exception as e:
            return jsonify({"ok": False, "reason": f"mcp unavailable: {e}"})
        reg = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
        if not isinstance(reg, dict):
            return jsonify({"ok": False, "reason": "tool registry unavailable"})
        out = []
        for name, tool in sorted(reg.items()):
            ns = name.split("_", 1)[0] if "_" in name else name
            out.append({
                "name": name,
                "namespace": ns,
                "description": (getattr(tool, "description", "") or "").strip(),
                "schema": getattr(tool, "parameters", {}) or {},
                "is_async": bool(getattr(tool, "is_async", False)),
                "mutates": _is_mutating_tool(name),
            })
        return jsonify({"ok": True, "tools": out, "count": len(out)})

    @app.route("/api/tool/run", methods=["POST"])
    def tool_run() -> Response:
        """Invoke one registered MCP tool by name with a kwargs dict.

        Returns {ok, data, elapsed_ms} on success; {ok:false, reason,
        elapsed_ms} on a raised exception. Mutating tools clear the
        project snapshot cache so the rest of the dashboard re-reads.
        """
        import asyncio
        import time as _time
        from flask import request as _req

        body = _req.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        args = body.get("args") or {}
        if not name:
            return jsonify({"ok": False, "reason": "name is required"}), 400
        if not isinstance(args, dict):
            return jsonify({"ok": False, "reason": "args must be an object"}), 400
        try:
            from eda_agent.server import mcp
        except Exception as e:
            return jsonify({"ok": False, "reason": f"mcp unavailable: {e}"}), 500
        reg = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
        if not isinstance(reg, dict) or name not in reg:
            return jsonify({"ok": False, "reason": f"unknown tool: {name}"}), 404
        tool = reg[name]
        fn = getattr(tool, "fn", None)
        if fn is None:
            return jsonify({"ok": False, "reason": "tool function missing"}), 500
        t0 = _time.monotonic()
        try:
            if getattr(tool, "is_async", False):
                data = asyncio.run(fn(**args))
            else:
                data = fn(**args)
        except Exception as e:
            elapsed = int((_time.monotonic() - t0) * 1000)
            return jsonify({"ok": False, "reason": f"{type(e).__name__}: {e}",
                            "elapsed_ms": elapsed})
        elapsed = int((_time.monotonic() - t0) * 1000)
        if _is_mutating_tool(name):
            with _cache_lock:
                for k in list(_cache.keys()):
                    if k.startswith("project."):
                        _cache.pop(k, None)
        return jsonify({"ok": True, "data": data, "elapsed_ms": elapsed})

    # -----------------------------------------------------------------
    # Drawing tab: inline schematic + PCB SVG views, sheet picker.
    # -----------------------------------------------------------------

    @app.route("/api/drawing/sheets")
    def drawing_sheets() -> Response:
        """List schematic sheets in the focused project so the Drawing
        tab can build a picker. Filters get_documents to the .SchDoc
        documents.
        """
        docs = _bridge_call("project.get_documents", {}, timeout=12.0)
        if docs is None:
            return jsonify({"ok": False, "reason": "altium-not-running"})
        # The bridge returns the document list in one of two shapes:
        #   list                           -- newer handler returns array directly
        #   {"documents": [list]}          -- older wrapped form
        # Accept either so the picker works regardless of which shape
        # the Pascal layer happens to emit.
        if isinstance(docs, list):
            doc_list = docs
        elif isinstance(docs, dict):
            doc_list = docs.get("documents") or docs.get("docs") or []
        else:
            doc_list = []
        sheets = []
        for d in doc_list:
            if not isinstance(d, dict):
                continue
            kind = (d.get("document_kind") or "").upper()
            if kind != "SCH":
                continue
            sheets.append({
                "file_name": d.get("file_name") or d.get("FileName"),
                "file_path": d.get("file_path") or d.get("FullPath"),
            })
        return jsonify({"ok": True, "sheets": sheets})

    @app.route("/api/drawing/sch")
    def drawing_sch() -> Response:
        """Return the active SchDoc as an inline SVG.

        Pulls geometry via the bridge then runs the in-house renderer.
        Optional ?doc=<path> focuses a specific .SchDoc before reading
        (so the picker can switch sheets without the user having to
        click the tab in Altium).
        """
        from flask import request as _req
        doc = (_req.args.get("doc") or "").strip()
        if doc:
            # Confirm the switch actually happened before reading
            # geometry, otherwise a NOT_LOADED error gets swallowed and
            # we render whatever was already active -- exactly the
            # "change sheet doesn't render selected sheet" bug.
            sw = _bridge_call("application.set_active_document",
                              {"file_path": doc}, timeout=10.0)
            if isinstance(sw, dict) and sw.get("error"):
                msg = sw.get("error") or "could not switch document"
                return Response(_empty_drawing_svg(
                    "Could not switch to " + doc + ": " + str(msg)),
                    mimetype="image/svg+xml", status=200)
        geom = _bridge_call("generic.get_sch_geometry", {}, timeout=60.0)
        if geom is None:
            return Response(_empty_drawing_svg("Altium is not running"),
                            mimetype="image/svg+xml", status=200)
        if not isinstance(geom, dict):
            return Response(_empty_drawing_svg("no geometry returned"),
                            mimetype="image/svg+xml", status=200)
        _hot_reload_render_modules()
        from ..render import render_sch_svg, SchRenderOptions
        svg = render_sch_svg(geom, SchRenderOptions())
        resp = Response(svg, mimetype="image/svg+xml")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    @app.route("/api/drawing/pcb")
    def drawing_pcb() -> Response:
        """Return the active PcbDoc as an inline SVG.

        ``?nolegend=1`` suppresses the inline foreignObject layer-toggle
        legend -- the dashboard's Drawing tab uses a proper sidebar
        (outside the SVG) so the floating panel would just duplicate
        controls. The open-in-tab path keeps the legend so the standalone
        SVG file remains self-contained and usable on its own.

        Failures (bridge timeout, renderer crash on a degenerate
        geometry shape, hot-reload import error) are caught and
        downgraded to a 200 with an explanatory empty-SVG. A 500 here
        would break both the Drawing tab and the Assembly tab, both of
        which expect an SVG response by Content-Type.
        """
        from flask import request as _req
        try:
            geom = _pcb_geometry_cached()
        except Exception as e:
            return Response(
                _empty_drawing_svg(f"PCB geometry fetch failed: {e}"),
                mimetype="image/svg+xml", status=200)
        if geom is None:
            # geom is None ONLY when we've never had a good fetch this
            # session (transient failures now serve the last good board).
            # Use the reliable PROCESS check to word the message correctly
            # instead of crying "Altium not running" on every hiccup.
            try:
                from eda_agent.bridge import get_bridge
                alive = get_bridge().is_altium_running()
            except Exception:
                alive = False
            msg = ("No PCB geometry yet -- open a PcbDoc and refresh"
                   if alive else "Altium is not running")
            return Response(_empty_drawing_svg(msg),
                            mimetype="image/svg+xml", status=200)
        if not isinstance(geom, dict):
            return Response(_empty_drawing_svg("no PCB geometry returned"),
                            mimetype="image/svg+xml", status=200)
        try:
            _hot_reload_render_modules()
            from ..render import render_pcb_svg, PcbRenderOptions
            no_legend = _req.args.get("nolegend", "").strip() in (
                "1", "true", "yes")
            # ?view=bottom requests the bottom-side render: top-side
            # layers fade to the back, bottom-side becomes prominent, AND
            # the SVG is mirrored in X so the rendered board matches a
            # physically-flipped board. Default "top" keeps the historical
            # behaviour.
            view = (_req.args.get("view") or "top").strip().lower()
            if view not in ("top", "bottom"):
                view = "top"
            opts = PcbRenderOptions(
                interactive_legend=not no_legend,
                view_side=view,
            )
            svg = render_pcb_svg(geom, opts)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            # Log the full trace to the Flask logger so the user can
            # see it in their MCP server console -- the empty SVG only
            # carries the short summary.
            try:
                app.logger.error("render_pcb_svg failed: %s\n%s", e, tb)
            except Exception:
                pass
            return Response(
                _empty_drawing_svg(f"PCB render failed: {e}"),
                mimetype="image/svg+xml", status=200)
        resp = Response(svg, mimetype="image/svg+xml")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    @app.route("/api/pcb/components")
    def pcb_components_positions() -> Response:
        """PCB components enriched with position + side data.

        The bundled snapshot's `bom` section comes from the SCH side
        (project.get_bom) and lacks x/y/side -- that information lives
        on the placed components on the PCB. The Assembly tab needs
        the spatial data to sort by side and highlight on the SVG.

        Pulls from the same `generic.get_pcb_geometry` payload the SVG
        renderer uses (heavy but cached by the bridge / snapshot
        layer), projecting components to a compact shape:
        ``{designator, x, y, layer, side, rotation, footprint}``.
        Coords are in Altium internal mils (same units the SVG uses
        for ``viewBox``).
        """
        geom = _pcb_geometry_cached()
        if not isinstance(geom, dict):
            return jsonify({"ok": False, "reason": "no PCB geometry"})

        # Group pads by owning component so we can MEASURE each part's real
        # footprint extent from copper rather than trusting the (often
        # wrong/empty) footprint string. The geometry payload tags every
        # pad with `comp` = its component designator + x/y/x_size/y_size.
        pads_by_comp: dict[str, list[dict[str, Any]]] = {}
        for p in (geom.get("pads") or []):
            if not isinstance(p, dict):
                continue
            cn = str(p.get("comp") or "")
            if cn:
                pads_by_comp.setdefault(cn, []).append(p)

        def _fp_extent(des: str) -> tuple[float, float, int]:
            """(length_mils, width_mils, pad_count) from the pad bbox."""
            pads = pads_by_comp.get(des) or []
            if not pads:
                return (0.0, 0.0, 0)
            xmn = ymn = 1e18
            xmx = ymx = -1e18
            for p in pads:
                px = float(p.get("x") or 0)
                py = float(p.get("y") or 0)
                hw = float(p.get("x_size") or 0) / 2.0
                hh = float(p.get("y_size") or 0) / 2.0
                xmn = min(xmn, px - hw); xmx = max(xmx, px + hw)
                ymn = min(ymn, py - hh); ymx = max(ymx, py + hh)
            w = xmx - xmn
            h = ymx - ymn
            return (max(w, h), min(w, h), len(pads))

        # Imperial 2-terminal chip case codes by overall pad-span length
        # (mils). Upper bound per code; a 2-pad part's measured span lands
        # in exactly one bucket. Tuned to typical land patterns (pads
        # extend a little past the body, so spans run slightly long).
        _CASE_BY_LEN = [
            (42, "0201"), (62, "0402"), (84, "0603"), (114, "0805"),
            (165, "1206"), (205, "1210"), (265, "1812"), (1e9, "2512"),
        ]

        def _case_code(length_mils: float, pad_count: int) -> str:
            if pad_count != 2 or length_mils <= 0:
                return ""
            for lim, code in _CASE_BY_LEN:
                if length_mils <= lim:
                    return code
            return ""

        out: list[dict[str, Any]] = []
        for c in (geom.get("components") or []):
            if not isinstance(c, dict):
                continue
            des = c.get("des") or c.get("designator")
            if not des:
                continue
            layer = str(c.get("layer") or "")
            # Components on bottom mounting belong to the "Bottom" side.
            # Altium's `BottomLayer` covers the placed-mount side; anything
            # else (TopLayer or no layer) defaults to "Top".
            side = "Bottom" if layer.lower() == "bottomlayer" else "Top"
            length_mils, width_mils, pad_count = _fp_extent(str(des))
            out.append({
                "designator": str(des),
                "x": float(c.get("x") or 0),
                "y": float(c.get("y") or 0),
                "layer": layer,
                "side": side,
                "rotation": float(c.get("rotation") or 0),
                "footprint": str(c.get("footprint") or ""),
                "comment": str(c.get("comment") or c.get("value") or ""),
                # Measured-from-copper extent + derived chip case code (2-pad
                # passives only); empty case_code for ICs/multi-pad parts.
                "fp_length_mils": round(length_mils, 1),
                "fp_width_mils": round(width_mils, 1),
                "case_code": _case_code(length_mils, pad_count),
            })
        return jsonify({"ok": True, "data": {"components": out,
                                              "count": len(out)}})

    @app.route("/api/drawing/pcb/layers")
    def drawing_pcb_layers() -> Response:
        """Return just the layer list for the PCB sidebar.

        Cheap proxy around the same geometry call -- pulls the distinct
        ``layer`` values from objects + the board outline + the
        designators pseudo-layer. The dashboard sidebar uses this to
        build its checkbox list without re-rendering an entire PCB SVG.
        """
        geom = _pcb_geometry_cached()
        if not isinstance(geom, dict):
            return jsonify({"ok": False, "reason": "no PCB geometry"})
        layers: set[str] = set()
        for kind in ("tracks", "arcs", "pads", "vias", "texts",
                     "regions", "components"):
            for it in (geom.get(kind) or []):
                if isinstance(it, dict) and it.get("layer"):
                    layers.add(str(it["layer"]))
        # Synthetic pseudo-layers the renderer attaches.
        layers.add("Outline")
        layers.add("Designators")
        # Stable display order: copper first, then silk/solder, others.
        order = ["TopLayer", "BottomLayer",
                 "TopOverlay", "BottomOverlay",
                 "TopSolder", "BottomSolder",
                 "TopPaste", "BottomPaste",
                 "MultiLayer", "KeepOutLayer",
                 "Outline", "Designators"]
        ordered: list[str] = [l for l in order if l in layers]
        ordered += sorted(l for l in layers if l not in ordered)
        return jsonify({"ok": True, "layers": ordered})

    @app.route("/api/refresh/<topic>", methods=["POST"])
    def force_refresh(topic: str) -> Response:
        """Invalidate cached entries for one topic so the next GET re-fetches.

        Project / components / nets / messages all read from one bundled
        `project.dashboard_snapshot` cache entry, so they share an
        invalidation. ``pcb`` invalidates the PCB geometry cache used by
        the Drawing tab, the Assembly tab, and the layer sidebar -- one
        fetch costs 30-60 s on a real board so the explicit refresh
        button gives the user control.
        """
        if topic not in ("project", "components", "nets", "messages", "pcb"):
            return jsonify({"ok": False, "reason": "unknown topic"}), 400
        if topic == "pcb":
            drop_prefixes = ("generic.get_pcb_geometry",)
        else:
            drop_prefixes = ("project.dashboard_snapshot",)
        with _cache_lock:
            for k in list(_cache.keys()):
                if any(k.startswith(p) for p in drop_prefixes):
                    _cache.pop(k, None)
        return jsonify({"ok": True})

    @app.route("/api/entry/<rid>")
    def entry_detail(rid: str) -> Response:
        """Return the full payload prefix for one log entry.

        The activity.log truncates each response to 200 chars (Pascal-side
        ``Copy(ResponseContent, 1, 200)``). For richer inspection we also
        peek at bridge_trace.log around the request_id to confirm the
        full IPC story (POLL_SEEN / POLL_MATCH / extensions / timing).
        """
        entry = None
        with tailer._lock:
            for e in tailer.entries:
                if e.request_id == rid[:8]:
                    entry = e
                    break
        if entry is None:
            return jsonify({"ok": False, "error": "not found"}), 404

        trace_lines: list[str] = []
        try:
            trace_path = workspace_dir / "bridge_trace.log"
            if trace_path.exists():
                short = rid[:8]
                with open(trace_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if short in line:
                            trace_lines.append(line.rstrip())
                            if len(trace_lines) >= 40:
                                break
        except OSError:
            pass

        return jsonify({
            "ok": True,
            "entry": entry.to_dict(),
            "payload_prefix": entry.payload_prefix,
            "trace": trace_lines,
        })

    @app.route("/events")
    def events() -> Response:
        wake, q = tailer.subscribe()

        def stream():
            # Send initial snapshot so a freshly-opened tab is populated.
            yield f"event: snapshot\ndata: {json.dumps(tailer.snapshot())}\n\n"
            try:
                while True:
                    wake.wait(timeout=15.0)
                    wake.clear()
                    drained: list[dict] = []
                    while q:
                        drained.append(q.popleft())
                    if drained:
                        yield f"data: {json.dumps(drained)}\n\n"
                    else:
                        # keep-alive comment
                        yield ": ping\n\n"
            finally:
                tailer.unsubscribe(wake)

        return Response(
            stream_with_context(stream()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return app


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eda-agent dashboard",
        description="Local web dashboard for the EDA Agent MCP bridge.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace directory (default: from get_config()).",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "binding to %s exposes the dashboard (live design data, and "
            "endpoints that drive Altium) to the network with no "
            "authentication; use 127.0.0.1 unless you mean to share it.",
            args.host,
        )

    app = create_app(workspace_dir=args.workspace)

    # PID file so `eda-agent stop-dashboard` can find and kill us.
    # Cleared on graceful exit; stale PIDs after a crash get overwritten
    # on the next start.
    from ..config import get_config as _get_cfg
    pid_path = (args.workspace or _get_cfg().workspace_dir) / "dashboard.pid"
    import os as _os, atexit
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(_os.getpid()), encoding="utf-8")
        def _remove_pid():
            try: pid_path.unlink()
            except OSError: pass
        atexit.register(_remove_pid)
    except OSError as e:
        logger.warning("could not write dashboard PID file (%s): %s",
                       pid_path, e)

    logger.info("dashboard serving on http://%s:%s/ pid=%s",
                args.host, args.port, _os.getpid())
    # Use werkzeug.serving.make_server directly instead of app.run() so
    # we don't get the "development server -- do not use in production"
    # warning. For a local single-user dashboard the warning is just
    # noise. threaded=True so SSE doesn't lock out other requests.
    from werkzeug.serving import make_server
    import logging as _log
    _log.getLogger("werkzeug").setLevel(_log.WARNING)
    srv = make_server(args.host, args.port, app, threaded=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try: srv.shutdown()
        except Exception: pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
