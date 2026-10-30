# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Communication bridge between Python and Altium Designer via file-based IPC.

Per-request file scheme: Python writes ``request_<id>.json``, Altium scans the
workspace, processes it, and writes ``response_<id>.json``. Each side polls
only for files matching its own request ID, eliminating the stale-response
race that the older single-file scheme had when keep-alive pings overlapped
user tool calls.
"""

import itertools
import json
import locale
import logging
import os
import re
import sys
import time
import uuid
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from eda_agent.atomicfile import discard, replace_with_retry

# NOTE: no cross-process or publish lock is needed here. Each caller writes
# its own request_<id>.json (staged to .json.tmp, then atomically renamed)
# and polls its own response_<id>.json, so concurrent publishers -- threads
# or separate processes -- never share a filename. A file-locking primitive
# that once guarded a single shared pointer file was removed with that
# design.

from ..config import get_config
from .path_spelling import one_spelling
from .process_manager import AltiumProcessManager
from .exceptions import (
    AltiumNotRunningError,
    AltiumTimeoutError,
    AltiumCommandError,
    AltiumProtocolError,
    AltiumScriptCrashError,
    ScriptNotLoadedError,
    raise_for_code,
)
from .recovery import (
    recovery_guidance,
    recovery_message,
    STUCK_HANDLER,
    DEAD_LOOP,
    CORRUPT_RESPONSE,
)

from ..safety import refuse_command

logger = logging.getLogger("eda_agent.bridge")

# Wire protocol version. Must match scripts/altium/Main.pas:PROTOCOL_VERSION.
# Bump together; mismatch raises AltiumProtocolError on either side.
PROTOCOL_VERSION = 2

# Heartbeat-aware deadline extension cap. The Pascal dispatcher writes a
# progress_<id>.json marker while a handler runs; Python's poll loop extends
# the per-call deadline by another `timeout` window each time it observes
# fresh progress, up to this many extensions. With the default 10 s timeout
# this gives a 300 s ceiling per command -- plenty for heavy emit / compile
# passes while still catching runaway handlers.
_MAX_HEARTBEAT_EXTENSIONS = 30

#: A request still waiting in Altium's queue behind another Claude
#: session's slot. Extended while the queue is visibly alive (queue.json
#: fresh, or another request's progress marker present), up to this many
#: windows, so a queued call is not mistaken for a dead loop.
_MAX_QUEUE_EXTENSIONS = 30
#: How recent queue.json must be to count as a live queue, in seconds.
_QUEUE_FRESH_S = 5.0

#: How often to check whether a modal is what is holding a call up, and
#: how long to wait before the first check.
#:
#: RECURRING, NOT ONE SHOT. A dialog does not only appear at the start: a
#: long handler can raise one part-way through, and a single early probe
#: would miss it and then wait out the whole timeout in silence, which is
#: the failure this exists to remove. Checking on an interval catches it
#: whenever it turns up.
#:
#: The first check is delayed so ordinary calls never pay for it. MEASURED
#: on this workspace, the compile-bound reads are the slowest legitimate
#: handlers and return in about a second, so a 3 second grace leaves
#: headroom. After that the cost is one Win32 window enumeration per
#: interval, against a call that is already stalled.
_DIALOG_PROBE_AFTER = 3.0
_DIALOG_PROBE_EVERY = 3.0

# Thread pool for blocking I/O
_executor = ThreadPoolExecutor(max_workers=1)

#: The command in flight on this thread and any error box answered for it,
#: per thread because the keep-alive pings on its own.
_CALL = threading.local()


def _read_response_json(path: Path) -> Any:
    """A reply file as JSON: UTF-8 (a byte-order mark dropped), else the
    Windows codepage. Older Altium builds (AD 25) can
    write the reply in the system codepage; a whole UTF-8 file always
    decodes as UTF-8, so the fallback only reads what UTF-8 cannot."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        codepage = locale.getencoding()
        if codepage.lower().replace("-", "") in ("utf8", "utf8sig"):
            raise
        text = raw.decode(codepage)
    return json.loads(text)


def _trace_log(workspace_dir: Path, msg: str) -> None:
    """Append a line to workspace/bridge_trace.log. Never raises."""
    try:
        path = workspace_dir / "bridge_trace.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Event-driven response pickup (Windows).
#
# The response-poll loop used to sleep a fixed poll_interval between checks,
# adding up to that interval of latency to every call. FindFirstChangeNotification
# lets us BLOCK until the workspace directory changes, so the instant Pascal
# writes response_<id>.json we wake and read it -- the Python half of the
# round-trip drops to ~0. Pascal still has to poll (it can't block without
# freezing Altium's UI), but this removes our side of the latency.
#
# All of this degrades gracefully: if kernel32 isn't available the helpers
# return None / False and the caller falls back to time.sleep.
# ---------------------------------------------------------------------------

_FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
_WAIT_OBJECT_0 = 0x00000000

_kernel32 = None
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        _kernel32.FindFirstChangeNotificationW.argtypes = [
            wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        _kernel32.FindNextChangeNotification.restype = wintypes.BOOL
        _kernel32.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
        _kernel32.FindCloseChangeNotification.restype = wintypes.BOOL
        _kernel32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        _kernel32.WaitForSingleObject.restype = wintypes.DWORD
        _kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD]
    except Exception as _e:  # pragma: no cover - platform dependent
        _kernel32 = None
        logger.debug("directory-watch unavailable, falling back to sleep: %s", _e)

_INVALID_HANDLE = ctypes.c_void_p(-1).value if _kernel32 is not None else None


def _make_dir_watcher(directory: Path):
    """Open a change-notification handle for ``directory``.

    Returns the handle, or None if change-notification is unavailable
    (the caller then falls back to time.sleep polling).
    """
    if _kernel32 is None:
        return None
    try:
        handle = _kernel32.FindFirstChangeNotificationW(
            str(directory), False, _FILE_NOTIFY_CHANGE_FILE_NAME)
        if not handle or handle == _INVALID_HANDLE:
            return None
        return handle
    except Exception:
        return None


def _wait_dir_change(handle, timeout_ms: float) -> bool:
    """Block until the watched directory changes or the timeout elapses.

    Re-arms the notification on a real change. Returns True on a change,
    False on timeout or error. A False return is harmless -- the caller's
    loop just re-checks the response file and waits again.
    """
    if _kernel32 is None or handle is None:
        return False
    try:
        res = _kernel32.WaitForSingleObject(handle, int(timeout_ms))
        if res == _WAIT_OBJECT_0:
            _kernel32.FindNextChangeNotification(handle)
            return True
        return False
    except Exception:
        return False


def _close_dir_watcher(handle) -> None:
    if _kernel32 is None or handle is None:
        return
    try:
        _kernel32.FindCloseChangeNotification(handle)
    except Exception:
        pass


#: This server's session tag. One eda-agent server runs per Claude session,
#: so the tag tells Altium which session a request came from: it serves one
#: session at a time in slots when several are waiting, and never slots a
#: session that is alone (ScanForRequestFile in Main.pas).
_SESSION_TAG = uuid.uuid4().hex[:8]
_SEQUENCE = itertools.count(1)


def _queue_is_alive(workspace_dir: Path) -> bool:
    """Whether Altium is busy serving another Claude session right now.

    Altium refreshes queue.json about once a second while sessions contend
    between commands, and a progress marker exists while a request's
    handler runs. Either one means a waiting request is queued, not lost.
    Only ANOTHER session's marker counts: this session has one call in
    flight at a time, so a marker of its own is a leftover, and treating
    it as a queue would hide a dead loop from a session working alone.
    """
    try:
        if time.time() - (workspace_dir / "queue.json").stat().st_mtime < _QUEUE_FRESH_S:
            return True
    except OSError:
        pass
    own = f"progress_{_SESSION_TAG}-"
    try:
        return any(not p.name.lower().startswith(own)
                   for p in Path(workspace_dir).glob("progress_*.json"))
    except OSError:
        return False


_VERSION_RE = re.compile(r"SCRIPT_VERSION\s*=\s*'([^']+)'")


def bundled_script_version() -> str:
    """SCRIPT_VERSION of the script bundle on disk now, '' if unreadable."""
    try:
        from ..cli import get_bundled_scripts_path
        text = (get_bundled_scripts_path() / "Main.pas").read_text(
            encoding="utf-8", errors="replace")
    except Exception:                                    # noqa: BLE001
        return ""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else ""


#: The bridge script this server's Python was loaded alongside. A deploy
#: replaces the script in Altium but not the Python of a server that is
#: already running, and an old server drops tool arguments it does not
#: know while reporting success (measured 2026-10-14). See _outdated_note.
STARTED_WITH_VERSION = bundled_script_version()
#: How often a reply repeats the note that this server is out of date.
_OUTDATED_NOTE_EVERY_S = 300.0


def outdated_note(started_with: str, altium: str, on_disk: str) -> str:
    """The note for a server older than the script Altium runs, or ''.

    Only when Altium runs what is on disk now and that is not what this
    server started with: a deploy happened after it started. Altium on an
    OLDER script than the disk is a different problem, which app_ping
    reports.
    """
    if not (started_with and altium and on_disk):
        return ""
    if altium == started_with or altium != on_disk:
        return ""
    return (f"This session's eda-agent server started with bridge "
            f"{started_with}, and Altium now runs {altium}. New or changed "
            f"tool arguments can be dropped by this older server without "
            f"an error. Reconnect it (/mcp in Claude Code) before relying "
            f"on anything changed in that deploy.")


#: Name of the marker ``eda-agent relaunch --restart`` keeps in the workspace
#: while it stops and restarts the loop, and how long one counts as live.
#: A restart takes well under a minute; an older marker is left over from
#: one that died, and must not keep every caller waiting.
MAINTENANCE_FILE = "maintenance.json"
_MAINTENANCE_FRESH_S = 300.0


def maintenance_in_progress(workspace_dir: Path) -> bool:
    """Whether the bridge is being restarted on purpose right now."""
    try:
        age = time.time() - (Path(workspace_dir) / MAINTENANCE_FILE).stat().st_mtime
    except OSError:
        return False
    return age < _MAINTENANCE_FRESH_S


#: Set to 1 to fit the board (or sheet) in view after each command that
#: changes the design, so whoever watches Altium always sees the whole
#: board. Off by default: it moves the view, which a user zoomed in on one
#: corner would not want.
KEEP_IN_VIEW_ENV = "EDA_AGENT_KEEP_BOARD_IN_VIEW"
_KEEP_IN_VIEW_EVERY_S = 2.0
_last_fit = [0.0]

#: The verbs of commands that only read, look or report: no fit after them.
_READ_VERBS = ("get_", "query", "count", "list", "find", "read", "export",
               "audit", "calc", "check", "probe", "ping", "zoom", "focus",
               "screenshot", "render", "status", "highlight", "select",
               "deselect", "explain", "crossref", "compare", "run_drc",
               "dump", "inspect", "measure", "snapshot", "switch_view",
               "run_erc", "run_simulation", "simcfg", "set_grid", "refresh",
               "clear_highlights")


def _wants_board_in_view(command: str, now: Optional[float] = None) -> bool:
    """True when the view should be fitted after ``command``.

    Only with ``EDA_AGENT_KEEP_BOARD_IN_VIEW`` set, only after a board,
    schematic or object edit (``pcb.`` and ``generic.`` commands that are
    not reads), and at most once every two seconds.
    """
    if os.environ.get(KEEP_IN_VIEW_ENV, "").strip().lower() not in ("1", "true", "yes"):
        return False
    domain, _, verb = command.partition(".")
    if domain not in ("pcb", "generic") or not verb:
        return False
    if verb.startswith(_READ_VERBS):
        return False
    now = time.monotonic() if now is None else now
    if now - _last_fit[0] < _KEEP_IN_VIEW_EVERY_S:
        return False
    _last_fit[0] = now
    return True


def _short_id() -> str:
    """Filename-safe request id: ``<session tag>-<sequence>-<random>``.

    The tag and the 8-digit sequence let Altium take each session's
    requests in the order they were made, and take turns between sessions;
    the random tail keeps ids unique across restarts.
    """
    seq = next(_SEQUENCE) % 100_000_000
    return f"{_SESSION_TAG}-{seq:08d}-{uuid.uuid4().hex[:12]}"


@dataclass
class CommandRequest:
    """A command request to be sent to Altium."""

    command: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_short_id)
    #: The document this session last had in front. Altium puts it back
    #: in front before running the command (SessionFocus in StatusForm.pas).
    session_doc: str = ""

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "id": self.id,
            "command": self.command,
        }
        # Before params: Pascal's ExtractJsonValue takes the first match,
        # so the envelope key must come ahead of anything a caller passes.
        if self.session_doc:
            out["session_doc_path"] = self.session_doc
        out["params"] = self.params
        return out


@dataclass
class CommandResponse:
    """A response from Altium."""

    id: str
    success: bool
    protocol_version: int = 0
    data: Any = None
    error: Optional[dict] = None
    # Siblings the Pascal dispatcher appends to EVERY reply, success or
    # error: the follow-up a handler named with NoteNextStep, and a note
    # when the command moved the focused document. They were dropped here,
    # so eight Pascal call sites of advice (a library that could not be
    # flagged for saving, a part that could not be reached) never reached
    # a caller, and a refusal read as a bare "not found".
    next_step: str = ""
    active_document_changed: Optional[dict] = None
    #: What became of this session's document before the command ran:
    #: ``restored`` (put back in front), ``missing`` (closed) or
    #: ``not_restored``. Absent when it was already in front.
    session_document: Optional[dict] = None

    @classmethod
    def from_dict(cls, data: dict) -> "CommandResponse":
        return cls(
            id=data.get("id", ""),
            success=data.get("success", False),
            protocol_version=data.get("protocol_version", 0),
            data=data.get("data"),
            error=data.get("error"),
            next_step=data.get("next_step") or "",
            active_document_changed=data.get("active_document_changed"),
            session_document=data.get("session_document"),
        )


def _with_envelope_notes(data: Any, response: CommandResponse) -> Any:
    """Carry the envelope's notes into a successful reply's data.

    Only a dict can take them without changing the reply's shape, so a
    list or scalar reply is returned as it came. A key the handler already
    set is left alone: the handler's own value is the more specific one.
    """
    if not isinstance(data, dict):
        return data
    if not (response.next_step or response.active_document_changed
            or response.session_document):
        return data
    data = dict(data)
    if response.next_step and not data.get("next_step"):
        data["next_step"] = response.next_step
    if response.active_document_changed and "active_document_changed" not in data:
        data["active_document_changed"] = response.active_document_changed
    if response.session_document and "session_document" not in data:
        data["session_document"] = response.session_document
    return data


#: Commands sent without the session's document: a keep-alive must never
#: move the focus. application.get_active_document is pinned like any other
#: command (tools read it and then act on that document), except when asked
#: with ``front`` for what is really in front, which is how a session finds
#: the document a person has opened (see _unpinned).
_UNPINNED = frozenset({
    "application.ping",
    "application.stop_server",
    "application.get_unit_stamps",
})


def _unpinned(command: str, params: Optional[dict]) -> bool:
    return command in _UNPINNED or (
        command == "application.get_active_document"
        and str((params or {}).get("front", "")).lower() == "true")


def next_session_doc(current: str, command: str, params: dict,
                     response: "CommandResponse") -> str:
    """The session's document after ``response``.

    A command that moved the focus makes the new document the session's;
    focusing a document that was already in front does too, since that is
    the session choosing it. A closed document is forgotten. Anything else
    leaves it as it was.
    """
    moved = response.active_document_changed or {}
    if isinstance(moved, dict) and moved.get("to"):
        return str(moved["to"])
    if command == "application.set_active_document" and response.success:
        data = response.data if isinstance(response.data, dict) else {}
        chosen = data.get("file_path") or (params or {}).get("file_path")
        if chosen:
            return str(chosen)
    note = response.session_document or {}
    if isinstance(note, dict) and note.get("missing"):
        return ""
    return current


class AltiumBridge:
    """Handles communication with Altium Designer via file-based IPC.

    Flow:
        1. Python writes ``request_<id>.json`` (atomic via tmp + rename)
        2. Altium's polling script picks it up, deletes it, processes it
        3. Altium writes ``response_<id>.json`` (atomic via tmp + rename)
        4. Python polls for ``response_<id>.json`` and reads it

    With per-request files there is no shared file across callers, so
    concurrent calls (keep-alive ping + user tool) do not collide.
    """

    KEEPALIVE_INTERVAL = 30  # seconds
    KEEPALIVE_SLOW_INTERVAL = 120  # seconds, while Altium does not answer
    DETACH_HINT_AFTER = 600  # seconds

    def __init__(self):
        self.config = get_config()
        self.process_manager = AltiumProcessManager()
        self._attached = False
        self._keepalive_thread: Optional[threading.Thread] = None
        self._keepalive_stop = threading.Event()
        self._attach_time: Optional[float] = None
        self._detach_hint_shown = False
        # The heavy workspace prep -- pointer file, runtime config, and the
        # Pydantic-schema export -- only needs to run once. Doing it on every
        # _publish_request added 1s+ of disk I/O per IPC call. This flag
        # gates it to a single run; per-request we only ensure the dir.
        self._workspace_prepared = False
        # Set when a DelphiScript fault has been persisted to last_fault.json;
        # lets the success path clear it without touching disk on healthy calls.
        self._fault_recorded = False
        # A fault persisted by a PREVIOUS process leaves last_fault.json on
        # disk while this instance's flag starts False. Sweep it once on the
        # first successful command so a server restart can't strand a stale
        # recovery banner; steady-state healthy calls then touch no disk.
        self._stale_fault_checked = False
        # The last non-ping command and when, for the FRAME_SHOWN trace line.
        self._last_command: Optional[tuple] = None
        # The document this session last had in front, sent with every
        # command so Altium can put it back if another session moved the
        # focus in between. Empty until the session's first command.
        self._session_doc = ""
        # The script version Altium last reported (every ping carries it),
        # and when a reply last said this server is out of date.
        self._loop_script_version = ""
        self._outdated_noted_at = 0.0

    def _note_fault(self, workspace_dir, guidance: dict) -> None:
        """Persist a fault for the dashboard recovery banner (best-effort)."""
        from .fault_state import record_fault
        record_fault(workspace_dir, guidance)
        self._fault_recorded = True

    def _clear_fault_if_any(self, workspace_dir) -> None:
        """A command succeeded: the loop is alive; drop any banner state.

        Clears when THIS instance recorded a fault, and once at first success
        to sweep a stale ``last_fault.json`` left by a previous process (the
        recovery flow can involve restarting the server). After that first
        sweep the in-memory flag alone gates it, so healthy steady-state calls
        touch no disk.
        """
        if self._fault_recorded or not self._stale_fault_checked:
            from .fault_state import clear_fault
            clear_fault(workspace_dir)
            self._fault_recorded = False
        self._stale_fault_checked = True

    def ensure_workspace(self) -> None:
        self.config.ensure_workspace()
        self._sweep_orphan_responses()
        self._workspace_prepared = True

    def _ensure_workspace_fast(self) -> None:
        """Per-request workspace guard. Cheap by design.

        The full ``ensure_workspace`` re-exports JSON schemas and rewrites
        the pointer/config files -- ~1s of disk I/O. That belongs at attach
        time, not on every IPC call. Here we only guarantee the directory
        exists; the one-time heavy prep runs lazily on the first request if
        ``attach()`` somehow hasn't run yet.
        """
        if not self._workspace_prepared:
            try:
                self.ensure_workspace()
            except Exception as e:
                logger.debug("one-time workspace prep failed: %s", e)
                # Fall through to the cheap mkdir so the request can still go.
            self._workspace_prepared = True
        try:
            self.config.workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug("workspace mkdir failed: %s", e)

    def _sweep_orphan_responses(self, max_age_seconds: float = 60.0) -> None:
        """Delete response_*.json files older than ``max_age_seconds``.

        Pascal's CleanupOrphanResponses can't enumerate the workspace
        (DelphiScript's FindFirst is broken), so the orphan-response
        sweep lives here. Python has reliable globbing and runs this on
        every ensure_workspace, typically once per attach + once per
        request. The age filter avoids racing with concurrent in-flight
        callers whose response is being polled right now.
        """
        cutoff = time.time() - max_age_seconds
        try:
            workspace = self.config.workspace_dir
            if not workspace.exists():
                return
            for path in workspace.glob("response_*.json"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    pass
            # Stray .tmp files from older builds that did atomic rename.
            # Age-filtered like the responses above, and for the same
            # reason: an unconditional delete here removes a temp file
            # that another writer is between writing and renaming, which
            # destroys that caller's response and leaves it polling until
            # it times out. Today's Pascal writes responses directly so
            # nothing in production creates these, but "nothing creates
            # them" is a property of the current script, not a guarantee
            # -- and a sweep whose safety depends on the swept file never
            # existing is one build away from being wrong.
            for path in workspace.glob("response_*.json.tmp"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def is_altium_running(self) -> bool:
        return self.process_manager.is_altium_running()

    def get_altium_status(self) -> dict:
        """Which Altium is running, and whether that is certain.

        ``pid`` is None with ``ambiguous`` set when more than one Altium
        is running and none can be identified as the one running
        StartMCPServer; ``reason`` explains and ``candidate_pids`` names
        them all. Picking one silently used to send every UI tool to a
        windowless orphan while bridge calls reached the real instance.
        """
        selection = self.process_manager.select_altium_process()
        common = {
            "candidate_count": len(selection.candidates),
            "candidate_pids": [c.pid for c in selection.candidates],
            "selected_by": selection.selected_by,
        }
        if selection.process:
            return {
                "running": True,
                "pid": selection.process.pid,
                "exe_path": selection.process.exe_path,
                "attached": self._attached,
                **common,
            }
        if selection.candidates:
            return {
                "running": True,
                "pid": None,
                "exe_path": None,
                "attached": self._attached,
                "ambiguous": True,
                "reason": selection.reason,
                **common,
            }
        return {
            "running": False,
            "pid": None,
            "exe_path": None,
            "attached": False,
            **common,
        }

    def attach(self) -> bool:
        if not self.is_altium_running():
            raise AltiumNotRunningError()
        self.ensure_workspace()
        self._attached = True
        self._attach_time = time.monotonic()
        self._start_keepalive()
        logger.info("Attached to Altium Designer (file-based IPC, protocol v%d)", PROTOCOL_VERSION)
        return True

    def detach(self) -> None:
        self._stop_keepalive()
        self._attached = False
        self._attach_time = None
        self._detach_hint_shown = False

    def _ensure_keepalive(self) -> None:
        if self._attached and self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        self._attached = True
        self._attach_time = time.monotonic()
        self._start_keepalive()

    def attach_duration(self) -> float:
        if self._attach_time is None:
            return 0.0
        return time.monotonic() - self._attach_time

    def _maybe_attach_detach_hint(self, command: str, data: Any) -> Any:
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
        self._stop_keepalive()
        # A keep-alive (re)start is also the natural moment to clear orphaned
        # response files: it runs on attach AND whenever a dead thread is
        # revived by the next tool call, so debris from a crashed call gets
        # cleaned without waiting for a full re-attach.
        try:
            self._sweep_orphan_responses()
        except Exception:
            pass
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True, name="altium-keepalive"
        )
        self._keepalive_thread.start()
        logger.debug("Keep-alive thread started (interval=%ds)", self.KEEPALIVE_INTERVAL)

    def _stop_keepalive(self) -> None:
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_stop.set()
            self._keepalive_thread.join(timeout=5)
            logger.debug("Keep-alive thread stopped")
        self._keepalive_thread = None

    def _keepalive_loop(self) -> None:
        # The keep-alive thread also drives the "Open Dashboard" sentinel:
        # the in-Altium status form drops workspace/open_dashboard.url whose
        # contents are a URL, and we open it in the user's default browser.
        # Sentinel checks run on a faster cadence than the 30s keep-alive
        # ping so the button feels responsive (~1s to react).
        sentinel_interval = 1.0
        ticks_per_ping = max(1, int(self.KEEPALIVE_INTERVAL / sentinel_interval))
        tick = 0
        # A failed ping must not END the keep-alive. It used to exit after
        # three in a row, which looked safe and was not: MEASURED
        # 2026-10-22, a bridge restart that took a few minutes killed the
        # keep-alive of every attached session, so once the loop was back
        # nobody pinged it and it shut itself down idle twenty minutes
        # later, with every session still connected. Now the pings slow
        # down while Altium does not answer and return to the normal
        # interval on the first answer, so a restarted loop is kept alive
        # by every session that was attached before.
        consecutive_failures = 0
        slow_after = 3
        slow_ticks = max(ticks_per_ping, int(self.KEEPALIVE_SLOW_INTERVAL
                                             / sentinel_interval))
        while not self._keepalive_stop.wait(sentinel_interval):
            if not self._attached:
                break
            self._maybe_open_dashboard_sentinel()
            tick += 1
            due = slow_ticks if consecutive_failures >= slow_after \
                else ticks_per_ping
            if tick >= due:
                tick = 0
                try:
                    self.send_command("application.ping", timeout=5.0)
                    if consecutive_failures >= slow_after:
                        logger.info("Keep-alive: Altium answers again")
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
                    if consecutive_failures == slow_after:
                        logger.warning(
                            "Keep-alive: %d pings in a row went unanswered; "
                            "the polling loop is likely stopped. Pinging "
                            "every %ds until it is back.", slow_after,
                            self.KEEPALIVE_SLOW_INTERVAL)
                    else:
                        logger.debug("Keep-alive ping failed (%d)",
                                     consecutive_failures)

    def _maybe_open_dashboard_sentinel(self) -> None:
        """If the Altium status form dropped open_dashboard.url, open it.

        The sentinel file's content is a URL (anything starting with http:// or
        https:// is accepted). The file is removed after the open call so the
        same trigger doesn't fire twice. Any error is logged and swallowed --
        a missing sentinel is the common case on every tick.
        """
        sentinel = self.config.workspace_dir / "open_dashboard.url"
        if not sentinel.exists():
            return
        try:
            url = sentinel.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return
        try:
            sentinel.unlink()
        except OSError:
            pass
        if not url.startswith("http://") and not url.startswith("https://"):
            logger.debug("dashboard sentinel had bad URL: %r", url[:80])
            return
        try:
            import webbrowser
            webbrowser.open(url)
            logger.info("opened dashboard URL: %s", url)
        except Exception as e:
            logger.debug("webbrowser.open failed: %s", e)

    def _response_path(self, request_id: str) -> Path:
        return self.config.workspace_dir / f"response_{request_id}.json"

    def _request_path(self, request_id: str) -> Path:
        return self.config.workspace_dir / f"request_{request_id}.json"

    def _progress_path(self, request_id: str) -> Path:
        return self.config.workspace_dir / f"progress_{request_id}.json"

    def _publish_request(self, request: CommandRequest) -> None:
        """Publish a request to its own per-request file.

        Per-request request files (request_<id>.json) plus per-request
        response files (response_<id>.json) eliminate cross-caller races
        entirely; each caller has private filenames on both sides.
        Pascal enumerates request_*.json via FindFiles (the documented
        Altium DelphiScript helper). No lock needed: concurrent publishers
        write to different filenames.

        Atomic write: stage the body to ``.json.tmp`` (which Pascal's
        FindFiles glob ignores) then rename to the final filename. Without
        this, Pascal could pick up a half-written file and hit a sharing
        violation when its Reset() collides with Python's still-open
        write handle.
        """
        self._ensure_workspace_fast()
        request_path = self._request_path(request.id)
        tmp_path = request_path.with_suffix(".json.tmp")
        # Literal characters behind a UTF-8 byte-order mark. Pascal reads
        # the file with TStringList.LoadFromStream, which takes the mark as
        # UTF-8 and reads a file without one in the ANSI codepage. A \uXXXX
        # escape is no way round that: UnescapeJsonString can only turn
        # 0..255 into a character, so a Chinese path arrived as '?'.
        with open(tmp_path, "w", encoding="utf-8-sig") as f:
            json.dump(request.to_dict(), f, indent=2, ensure_ascii=False)
        # The rename can lose a race with a Windows scanner holding the
        # target. Retried, then allowed to raise: a request that is never
        # published leaves the caller waiting out the full poll timeout
        # with nothing to explain the silence, so failing here with the
        # real error is strictly better than failing there without one.
        try:
            replace_with_retry(tmp_path, request_path)
        except PermissionError:
            discard(tmp_path)
            raise
        logger.debug("Published request %s: %s", request.id, request.command)

    def _poll_response(self, request_id: str, timeout: float) -> CommandResponse:
        """Poll for response_<id>.json. Returns when it appears, raises on
        timeout.

        Heartbeat-aware. The Pascal dispatcher writes progress_<id>.json
        before invoking a handler and deletes it after the response is
        written. While progress_<id>.json exists, the polling loop is
        actively working on this request -- a legitimately slow handler is
        not a "polling loop dead" signal. We extend the deadline by another
        ``timeout`` window each time the heartbeat is still ticking, up to
        ``_MAX_HEARTBEAT_EXTENSIONS`` extensions before declaring the
        operation truly stuck. Default ceiling: 30 * 10 s = 5 minutes per
        command, which covers heavy emits / compiles without losing the
        early failure detection of the original 10 s timeout.

        Race window: Pascal writes the response THEN deletes the progress
        file, so at no instant are both absent. When the per-window
        deadline fires we re-check the response one more time after the
        progress check, in case the heartbeat was cleared between the two
        observations.
        """
        response_path = self._response_path(request_id)
        progress_path = self._progress_path(request_id)
        workspace_dir = self.config.workspace_dir
        poll_interval = self.config.poll_interval
        deadline = time.monotonic() + timeout
        start = time.monotonic()

        _trace_log(
            workspace_dir,
            f"POLL_START id={request_id[:8]} timeout={timeout}s "
            f"interval={poll_interval}s",
        )

        # Event-driven wait: wake the instant Pascal writes our response
        # file instead of sleeping a fixed poll_interval. The watcher is
        # closed in the finally below; if it couldn't be created the loop
        # falls back to time.sleep transparently.
        watcher = _make_dir_watcher(workspace_dir)
        try:
            return self._poll_loop(
                request_id, response_path, progress_path, workspace_dir,
                poll_interval, deadline, timeout, start, watcher,
            )
        finally:
            _close_dir_watcher(watcher)

    def _poll_loop(self, request_id, response_path, progress_path,
                   workspace_dir, poll_interval, deadline, timeout,
                   start, watcher) -> CommandResponse:
        """Inner poll loop for _poll_response. Split out so the watcher
        handle can be closed via a single try/finally regardless of which
        exit path (match / timeout / break) the loop takes."""
        extensions = 0
        queue_extensions = 0
        poll_count = 0
        first_appearance: Optional[float] = None
        parse_errors = 0
        next_dialog_probe = start + _DIALOG_PROBE_AFTER

        while True:
            poll_count += 1

            # WATCH FOR A DIALOG THROUGHOUT, ON EVERY CALL, NOT JUST AT
            # TIMEOUT.
            #
            # A modal blocks the single-threaded scripting engine, so the
            # handler cannot answer and cannot say why. Waiting out the
            # full timeout first turns a question a human could answer in
            # a second into a two-minute silence, and that cost most of a
            # working day: nine separate calls hung the whole window while
            # the same "A command is currently active" prompt sat on
            # screen the entire time.
            #
            # Checked on an interval rather than once, because a handler
            # can raise a dialog part-way through and a single early probe
            # would miss exactly that case.
            #
            # The probe is Win32 and does NOT use the bridge, which is why
            # it works precisely when the bridge does not.
            now = time.monotonic()
            if first_appearance is None and now >= next_dialog_probe:
                next_dialog_probe = now + _DIALOG_PROBE_EVERY
                early = self._dialog_probe()
                if early and early.get("blocked") and self._recover_script_crash(
                        request_id, response_path, early) == "continue":
                    continue
                # Another session is clearing a crash or restarting the
                # loop: a request not yet taken waits for the new loop.
                if (early and early.get("blocked")
                        and maintenance_in_progress(workspace_dir)
                        and self._request_path(request_id).exists()):
                    continue
                if early and early.get("blocked"):
                    waited = now - start
                    _trace_log(
                        workspace_dir,
                        f"POLL_DIALOG id={request_id[:8]} "
                        f"after={waited:.1f}s "
                        f"summary={early.get('summary', '')[:120]}",
                    )
                    raise AltiumTimeoutError(
                        f"Altium is showing a modal dialog {waited:.0f}s into "
                        f"this call, so the handler cannot run and waiting for "
                        f"the timeout would tell you nothing more."
                        + self._dialog_suffix(early),
                        details={"dialogs": early,
                                 "blocked_after_seconds": round(waited, 1)},
                    )
            # DO NOT OPEN AN EMPTY RESPONSE. SaveToFile creates the file
            # and then writes it, so polling on existence alone opened a
            # 0-byte file, failed to parse, and came straight back to open
            # it again. Every one of those opens is a handle Altium's
            # exclusive create can collide with, and that collision
            # surfaces as an EFCreateError modal that stalls the polling
            # loop rather than an exception the script can catch.
            # stat() takes no handle, so this costs nothing.
            #
            # first_appearance IS SET ON APPEARANCE, not on the first
            # successful open. The deadline branch below treats a None
            # first_appearance as "never seen it" and loops again, so
            # tying it to a successful parse made a permanently empty
            # response spin forever instead of timing out. That is the
            # exact hazard the comment down there warns about, reached
            # through the other branch.
            appeared = response_path.exists()
            if appeared and first_appearance is None:
                first_appearance = time.monotonic() - start
                _trace_log(
                    workspace_dir,
                    f"POLL_SEEN id={request_id[:8]} "
                    f"after={first_appearance*1000:.0f}ms polls={poll_count}",
                )

            ready = False
            if appeared:
                try:
                    ready = response_path.stat().st_size > 0
                except OSError:
                    ready = False

            if ready:
                try:
                    data = _read_response_json(response_path)
                except (json.JSONDecodeError, IOError, UnicodeDecodeError) as e:
                    parse_errors += 1
                    if parse_errors <= 3 or parse_errors % 50 == 0:
                        _trace_log(
                            workspace_dir,
                            f"POLL_PARSE_ERR id={request_id[:8]} "
                            f"err={type(e).__name__} count={parse_errors}",
                        )
                    # A handful of parse errors is normal while Pascal is
                    # mid-write; hundreds means the file is permanently
                    # corrupt (Pascal died mid-write). Fail fast with an
                    # accurate diagnosis instead of burning the rest of the
                    # deadline and reporting "loop not running".
                    if parse_errors >= 200:
                        try:
                            response_path.unlink()
                        except OSError:
                            pass
                        self._note_fault(
                            workspace_dir, recovery_guidance(CORRUPT_RESPONSE))
                        raise AltiumCommandError(
                            f"Response file for request {request_id[:8]} was "
                            f"present but unparseable after {parse_errors} "
                            f"attempts -- Altium likely crashed mid-write. "
                            f"The corrupt file was removed; retry the call. "
                            + recovery_message(CORRUPT_RESPONSE),
                            details={"recovery": recovery_guidance(CORRUPT_RESPONSE)},
                        )

                    # Fall through to the deadline check so a permanently
                    # corrupt file cannot wedge us in an infinite parse loop.
                else:
                    try:
                        response_path.unlink()
                    except OSError:
                        pass

                    elapsed = (time.monotonic() - start) * 1000
                    _trace_log(
                        workspace_dir,
                        f"POLL_MATCH id={request_id[:8]} elapsed={elapsed:.0f}ms "
                        f"polls={poll_count} parse_errs={parse_errors} "
                        f"extensions={extensions}",
                    )
                    return CommandResponse.from_dict(data)

            if time.monotonic() >= deadline:
                # A handler can halt with no box on screen, the box answered
                # already: its progress file stays, so without this the
                # wait below ran out every extension first.
                if (progress_path.exists() and first_appearance is None
                        and self._recover_script_crash(
                            request_id, response_path, None) == "continue"):
                    continue
                # Window expired. Heartbeat still ticking? extend.
                if progress_path.exists() and extensions < _MAX_HEARTBEAT_EXTENSIONS:
                    extensions += 1
                    elapsed = (time.monotonic() - start) * 1000
                    _trace_log(
                        workspace_dir,
                        f"POLL_EXTEND id={request_id[:8]} "
                        f"extensions={extensions}/{_MAX_HEARTBEAT_EXTENSIONS} "
                        f"elapsed={elapsed:.0f}ms",
                    )
                    deadline = time.monotonic() + timeout
                    continue
                # Still queued behind another Claude session's slot: our
                # request has not been taken and the loop is visibly
                # serving someone else. Only possible while sessions
                # contend, so a lone session never takes this branch.
                # Or the bridge is being restarted on purpose: the request
                # waits for the new loop instead of failing, so nobody has
                # to be asked to pause for a deploy.
                restarting = maintenance_in_progress(workspace_dir)
                if (not progress_path.exists()
                        and queue_extensions < _MAX_QUEUE_EXTENSIONS
                        and self._request_path(request_id).exists()
                        and (restarting or _queue_is_alive(workspace_dir))):
                    queue_extensions += 1
                    _trace_log(
                        workspace_dir,
                        f"POLL_QUEUED id={request_id[:8]} "
                        f"waits={queue_extensions}/{_MAX_QUEUE_EXTENSIONS}"
                        + (" restart" if restarting else ""),
                    )
                    deadline = time.monotonic() + timeout
                    continue
                # Race window: progress was cleared between deletion and
                # our check. Take one more response peek before giving up,
                # but ONLY if we have not already been parsing the file --
                # otherwise a permanently-corrupt response would loop here
                # forever. ``first_appearance`` being non-None means we've
                # already opened the file at least once and the top-of-
                # loop parse check is doing its job; no need to re-enter.
                if first_appearance is None and response_path.exists():
                    continue
                break

            # Wait for the workspace directory to change (Pascal writing
            # our response file is exactly such a change) -- wakes us
            # immediately instead of after a fixed poll_interval. Falls
            # back to a plain sleep when the watcher is unavailable.
            if watcher is not None:
                _wait_dir_change(watcher, poll_interval * 1000.0)
            else:
                time.sleep(poll_interval)

        elapsed = (time.monotonic() - start) * 1000
        _trace_log(
            workspace_dir,
            f"POLL_TIMEOUT id={request_id[:8]} elapsed={elapsed:.0f}ms "
            f"polls={poll_count} parse_errs={parse_errors} "
            f"extensions={extensions} "
            f"first_seen_ms={first_appearance*1000 if first_appearance else -1}",
        )
        # Before blaming the loop, LOOK. A modal produces exactly this
        # silence, and the two diagnoses call for opposite actions:
        # restarting a loop that is merely waiting for a button press
        # throws away whatever the handler was in the middle of.
        probe = self._dialog_probe()

        if extensions >= _MAX_HEARTBEAT_EXTENSIONS:
            self._note_fault(workspace_dir, recovery_guidance(STUCK_HANDLER))
            # PAST TENSE, because that is all the evidence supports. This
            # message used to say "Altium IS responding to keepalives ...
            # likely stuck in an infinite loop", present tense, as a fixed
            # string on this branch. It is inferred from heartbeats seen
            # EARLIER in the wait, and nothing rechecks at timeout.
            #
            # MEASURED: a handler hit an uncatchable DelphiScript fault, the
            # polling loop DIED, app_ping was already failing well before the
            # 300s mark, and this still reported the loop as answering and
            # pointed recovery at a stuck handler rather than a dead one.
            # Those two call for different actions, so the wrong guess costs
            # real time.
            raise AltiumTimeoutError(
                f"Handler exceeded {_MAX_HEARTBEAT_EXTENSIONS} heartbeat "
                f"extensions ({_MAX_HEARTBEAT_EXTENSIONS * timeout:.0f}s "
                f"total). Altium WAS answering keepalives earlier in the "
                f"wait, but that is not rechecked here, so the loop may be "
                f"stuck in a long operation OR may since have died. Confirm "
                f"with app_ping before choosing a recovery. "
                + recovery_message(STUCK_HANDLER)
                + self._dialog_suffix(probe),
                details={"recovery": recovery_guidance(STUCK_HANDLER),
                         "dialogs": probe},
            )
        self._note_fault(workspace_dir, recovery_guidance(DEAD_LOOP))
        from ..config import pointer_mismatch
        elsewhere = pointer_mismatch(workspace_dir)
        raise AltiumTimeoutError(
            f"No response within {timeout}s and no progress heartbeat. "
            + (elsewhere + " " if elsewhere else
               "The Altium polling loop is probably not running. "
               if not probe else "")
            + recovery_message(DEAD_LOOP)
            + self._dialog_suffix(probe),
            details={"recovery": recovery_guidance(DEAD_LOOP),
                     "dialogs": probe},
        )

    def _dialog_probe(self) -> Optional[dict]:
        """What is on Altium's screen, asked WITHOUT the bridge.

        A silent bridge looks identical whether the polling loop is
        dead, the handler is looping, or a modal is blocking the
        scripting engine. Those need opposite responses, and until now
        the timeout guessed: it named a dead loop and told the caller to
        go and look for a dialog themselves.

        This looks instead. It reads the Win32 windows directly, which
        is the one route that still answers while Altium is blocked,
        precisely because it never touches the IPC that is stuck.

        Returns None rather than raising, always. A probe that fails
        must not replace the timeout the caller actually needs to see.
        """
        try:
            from ..ui import dialog_report, windows

            if not windows.available():
                return None
            process = self.process_manager.get_altium_info()
            if not process:
                return None
            report = dialog_report.report(process.pid)
            return report if report.get("dialog_count") else None
        except Exception:                        # pragma: no cover - guard
            return None

    def _recover_script_crash(self, request_id: str, response_path: Path,
                              probe: Optional[dict]) -> Optional[str]:
        """Clear a handler that crashed the script, and restart the loop.

        Only for THIS call's handler: Altium has taken the request and not
        answered. With ``probe`` it acts on a script error box in it; with
        None, on a script halted with no box up. Returns None when there
        is nothing to recover, "continue" when the call should go on
        waiting (the handler survived an error box), and raises
        AltiumScriptCrashError, saying so, after a crash. See
        bridge/script_crash.py.
        """
        from . import script_crash

        workspace_dir = self.config.workspace_dir
        if self._request_path(request_id).exists() or response_path.exists():
            return None
        dialog = script_crash.script_error_dialog(probe) if probe else None
        if probe is not None and dialog is None:
            return None
        try:
            from ..ui import windows

            if not windows.available():
                return None
            process = self.process_manager.get_altium_info()
            if not process:
                return None
            if dialog is None:
                frame = script_crash._main_frame(process.pid)
                if frame is None or not script_crash.title_shows_halt(frame.title):
                    return None
        except Exception:                        # pragma: no cover - guard
            return None

        command = getattr(_CALL, "command", "") or "a command"
        _trace_log(workspace_dir,
                   f"SCRIPT_CRASH_CHECK id={request_id[:8]} cmd={command} "
                   f"box={'yes' if dialog else 'no'}")

        def reply_came(seconds: float) -> bool:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if response_path.exists():
                    return True
                time.sleep(0.1)
            return response_path.exists()

        # Other sessions' calls wait through the recovery, as through a
        # restart, rather than failing on the box this one is answering.
        marker = Path(workspace_dir) / MAINTENANCE_FILE
        try:
            marker.write_text(json.dumps({"reason": "script crash recovery",
                                          "started": time.time()}),
                              encoding="utf-8")
        except OSError:
            pass
        try:
            outcome = script_crash.recover(workspace_dir, process.pid,
                                           command, request_id, dialog,
                                           reply_came)
        finally:
            try:
                marker.unlink()
            except OSError:
                pass
        if not outcome.get("halted"):
            _trace_log(workspace_dir,
                       f"SCRIPT_CRASH_NONE id={request_id[:8]} "
                       f"title={outcome.get('title', '')[:60]}")
            if dialog is None:
                return None
            # OK was pressed on a box the caller never saw: it goes on the
            # reply (see _execute_command), with its screenshot.
            _CALL.answered_box = {
                "title": dialog.get("title"),
                "said": dialog.get("message") or dialog.get("ocr_text") or [],
                "screenshots": outcome.get("screenshots") or [],
                "note": ("Altium showed this error box during the command and "
                         "OK was pressed for it; the command then went on."),
            }
            return "continue"
        script_crash.stale_progress(workspace_dir, request_id)
        restart = outcome.get("restart") or {}
        _trace_log(workspace_dir,
                   f"SCRIPT_CRASH id={request_id[:8]} cmd={command} "
                   f"restarted={restart.get('restarted')} "
                   f"record={outcome.get('record', '')}")
        logger.error("The Altium script crashed in %s; restarted=%s; %s",
                     command, restart.get("restarted"), outcome.get("record"))
        raise AltiumScriptCrashError(script_crash.crash_message(outcome),
                                     details=outcome)

    @staticmethod
    def _dialog_suffix(probe: Optional[dict]) -> str:
        """One sentence naming what is blocking, for the timeout text."""
        if not probe:
            return ""
        return (
            f" A DIALOG IS ON SCREEN, so the loop is blocked rather than "
            f"absent: {probe.get('summary', 'a modal is open')}. Answer it, "
            f"or read it with app_list_open_dialogs and press a button with "
            f"app_press_dialog_button; both work while the bridge does not."
        )

    def _withdraw_or_warn(self, request: CommandRequest,
                          exc: AltiumTimeoutError) -> None:
        """After a timeout, take back our request if Altium never took it.

        A timed-out request used to stay in the queue. When the modal that
        blocked it was dismissed, Altium ran it, minutes later and with
        nobody waiting: 26 stranded reads were seen in one session (issue
        #94), and a stranded WRITE would apply an edit the caller had
        already been told failed. Only this caller's own request file is
        touched, never another session's.

        Not there any more means Altium has read it, and then the edit may
        still land; the error says which of the two happened.
        """
        workspace_dir = self.config.workspace_dir
        request_path = self._request_path(request.id)
        withdrawn = False
        try:
            if request_path.exists():
                request_path.unlink()
                withdrawn = True
        except OSError:
            withdrawn = False
        details = exc.details if isinstance(exc.details, dict) else {}
        details["request_withdrawn"] = withdrawn
        exc.details = details
        if withdrawn:
            note = (" The request was withdrawn before Altium read it, so "
                    "it will not run later.")
        else:
            note = (" Altium had already read the request, so it may still "
                    "finish: read the design back before repeating a change.")
        exc.message = f"{exc.message}{note}"
        exc.args = (exc.message,)
        _trace_log(workspace_dir,
                   f"WITHDRAW id={request.id[:8]} cmd={request.command} "
                   f"withdrawn={withdrawn}")

    def _with_outdated_note(self, command: str, data: Any) -> Any:
        """Say so on a reply when a deploy has made this server out of date.

        Repeated every few minutes rather than on every reply, and never on
        a keep-alive, which nobody reads. The disk is read only once the
        versions already differ, which is rare.
        """
        if command == "application.ping" or not isinstance(data, dict):
            return data
        if not self._loop_script_version or self._loop_script_version == STARTED_WITH_VERSION:
            return data
        now = time.monotonic()
        if self._outdated_noted_at and now - self._outdated_noted_at < _OUTDATED_NOTE_EVERY_S:
            return data
        note = outdated_note(STARTED_WITH_VERSION, self._loop_script_version,
                             bundled_script_version())
        if not note:
            return data
        self._outdated_noted_at = now
        data = dict(data)
        data["server_outdated"] = note
        return data

    def _reshow_hidden_frame(self, command: str, request_id: str) -> list:
        """Show Altium's main window again if it is hidden. Never raises.

        Run after every reply, keep-alive pings included, so a hidden
        window comes back within one ping interval even when no tool is
        called. Only after a reply: the loop answering means Altium is
        neither starting up nor closing, the two times it hides its own
        window. The FRAME_SHOWN line names the last command so the cause
        can be found; UI tools log a UI line of their own.
        """
        if command not in ("application.ping", "application.stop_server"):
            self._last_command = (command, time.time())
        try:
            from ..ui import windows
            shown = windows.reshow_hidden_frames_of(
                self.process_manager.STATUS_FORM_TITLE)
        except Exception:                                # noqa: BLE001
            return []
        if shown:
            last, at = (getattr(self, "_last_command", None)
                        or ("none", time.time()))
            _trace_log(
                self.config.workspace_dir,
                f"FRAME_SHOWN id={request_id[:8]} cmd={command} "
                f"hwnd={','.join(hex(h) for h in shown)} last_cmd={last} "
                f"last_cmd_age={time.time() - at:.0f}s")
            logger.warning("Altium's main window was hidden; shown again "
                           "after %s (last command %s)", command, last)
        return shown

    def _execute_command(self, command: str, params: dict[str, Any], timeout: float) -> Any:
        """Execute a command synchronously (blocking)."""
        request = CommandRequest(
            command=command, params=params,
            session_doc="" if _unpinned(command, params) else self._session_doc)
        workspace_dir = self.config.workspace_dir

        _trace_log(workspace_dir, f"SEND cmd={command} id={request.id[:8]}")
        _CALL.command = command
        _CALL.answered_box = None
        self._publish_request(request)
        logger.info("Sent command: %s (id=%s)", command, request.id[:8])

        response_path = self._response_path(request.id)
        try:
            response = self._poll_response(request.id, timeout)
        except AltiumTimeoutError as exc:
            self._withdraw_or_warn(request, exc)
            raise
        finally:
            # Always sweep our own response file on the way out,_poll_response
            # already deletes it on success, but a timeout or a late-arriving
            # response would otherwise be orphaned forever.
            try:
                if response_path.exists():
                    response_path.unlink()
            except OSError:
                pass

        if response.protocol_version and response.protocol_version != PROTOCOL_VERSION:
            raise AltiumProtocolError(
                client_version=PROTOCOL_VERSION,
                server_version=response.protocol_version,
            )

        if not _unpinned(command, params):
            self._session_doc = next_session_doc(
                self._session_doc, command, params, response)

        shown = self._reshow_hidden_frame(command, request.id)
        answered = getattr(_CALL, "answered_box", None)

        if response.success:
            logger.info("Command %s succeeded", command)
            self._clear_fault_if_any(workspace_dir)
            data = _with_envelope_notes(response.data, response)
            if command == "application.ping" and isinstance(data, dict):
                self._loop_script_version = str(data.get("script_version") or "")
            data = self._with_outdated_note(command, data)
            if shown and isinstance(data, dict):
                data = dict(data)
                data["altium_window_shown_again"] = (
                    "Altium's main window was hidden and has been shown "
                    "again. bridge_trace.log has a FRAME_SHOWN line naming "
                    "what ran before it.")
            if answered and isinstance(data, dict):
                data = dict(data)
                data["error_box_answered"] = answered
            return self._maybe_attach_detach_hint(command, data)

        error = response.error or {}
        code = error.get("code", "UNKNOWN_ERROR")
        message = error.get("message", "Unknown error")
        details = error.get("details")
        # The reason a handler recorded is often the only useful part of a
        # refusal: "not found" for a symbol that exists, when what failed
        # was reaching one of its parts.
        if response.next_step:
            message = f"{message} Next step: {response.next_step}"
            details = dict(details) if isinstance(details, dict) else {}
            details["next_step"] = response.next_step
        if answered:
            message = (f"{message} Altium showed the error box "
                       f"{answered['title']!r} during it; OK was pressed "
                       f"(screenshot: {', '.join(answered['screenshots']) or 'none'}).")
            details = dict(details) if isinstance(details, dict) else {}
            details["error_box_answered"] = answered
        logger.warning("Command %s failed: %s - %s", command, code, message)
        raise_for_code(code, message, details)

    def send_command(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        refusal = refuse_command(command)
        if refusal is not None:
            return {"success": False, "error": {
                "code": "REFUSED_BY_POLICY", "message": refusal}}
        if not self.is_altium_running():
            raise AltiumNotRunningError()
        if timeout is None:
            timeout = self.config.poll_timeout
        self._ensure_keepalive()
        return self._execute_command(command, one_spelling(params or {}), timeout)

    async def send_command_async(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        refusal = refuse_command(command)
        if refusal is not None:
            return {"success": False, "error": {
                "code": "REFUSED_BY_POLICY", "message": refusal}}
        if not self.is_altium_running():
            raise AltiumNotRunningError()
        if timeout is None:
            timeout = self.config.poll_timeout
        self._ensure_keepalive()
        # Logged before the executor, whose one worker runs this server's
        # calls one at a time: a CALL with no SEND after it is a call stuck
        # behind an earlier one of the same session (a cancelled call keeps
        # its worker until its own deadline), not a lost request. Measured
        # 2026-10-23: a call that "never returned" had no SEND at all, and
        # nothing in the trace could say where it was.
        _trace_log(self.config.workspace_dir,
                   f"CALL cmd={command} tag={_SESSION_TAG}")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor,
            self._execute_command,
            command,
            one_spelling(params or {}),
            timeout,
        )
        if _wants_board_in_view(command):
            try:
                await loop.run_in_executor(
                    _executor, self._execute_command,
                    "generic.zoom", {"action": "board"}, 10.0)
            except Exception:  # a view nicety never fails the call it follows
                pass
        return result

    def ping(self) -> bool:
        try:
            self.send_command("application.ping", timeout=3.0)
            return True
        except (AltiumTimeoutError, AltiumCommandError, Exception):
            return False

    def ping_with_version(self) -> Optional[dict[str, Any]]:
        try:
            result = self.send_command("application.ping", timeout=3.0)
        except (AltiumTimeoutError, AltiumCommandError, Exception):
            return None
        if isinstance(result, dict):
            return result
        if result == "pong":
            return {"pong": True, "script_version": ""}
        return None


_bridge: Optional[AltiumBridge] = None


def get_bridge() -> AltiumBridge:
    global _bridge
    if _bridge is None:
        _bridge = AltiumBridge()
    return _bridge


def reset_bridge() -> None:
    global _bridge
    _bridge = None
