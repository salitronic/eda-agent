# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Communication bridge between Python and Altium Designer via file-based IPC.

Per-request file scheme: Python writes ``request_<id>.json``, Altium scans the
workspace, processes it, and writes ``response_<id>.json``. Each side polls
only for files matching its own request ID, eliminating the stale-response
race that the older single-file scheme had when keep-alive pings overlapped
user tool calls.
"""

import contextlib
import functools
import contextvars
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
    AltiumError,
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
#: One look costs about 60 ms (a Toolhelp scan for Altium's pid and a
#: Win32 window enumeration), so it starts after a second and repeats
#: every half second. A dialog counts as blocking the call only when the
#: same one is seen on two looks in a row, so a progress window Altium
#: opens and closes by itself never stops a call.
_DIALOG_PROBE_AFTER = 1.0
_DIALOG_PROBE_EVERY = 0.5


def _blocking_signature(report: Optional[dict], expected: frozenset = frozenset()) -> str:
    """The dialogs blocking Altium, as one comparable string; '' if none.

    A dialog whose title contains one of ``expected`` is one the caller
    is answering itself (proj_sync_pcb and its change order) and is not
    counted.
    """
    if not report or not report.get("blocked"):
        return ""
    dialogs = [d for d in report.get("dialogs") or [] if isinstance(d, dict)]
    if not dialogs:
        return "blocked"
    return "|".join(sorted(
        f"{d.get('hwnd')}:{d.get('title')}" for d in dialogs
        if not any(e.lower() in str(d.get("title") or "").lower() for e in expected)))

# Thread pool for blocking I/O
_executor = ThreadPoolExecutor(max_workers=1)

#: The command in flight on this thread and any error box answered for it,
#: per thread because the keep-alive pings on its own.
_CALL = threading.local()


#: How a request takes its turn in Altium (ScanForRequestFile in Main.pas):
#: inside a tool call its commands keep the turn together; background work
#: such as the dashboard's reads waits while any session has a request.
#: Context variables, so the marks reach the executor thread a call runs on.
_TOOL_CALL: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "eda_tool_call", default=None)
_BACKGROUND: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "eda_background", default=False)
#: following_front() as a context variable too: a thread-local alone was lost
#: when a dashboard action ran an async tool, whose commands go out on the
#: executor thread. Measured 2026-11-05: each of them put the session's own
#: document back in front, Altium switched windows on every command, and the
#: teardrops landed on another open board.
_FOLLOW: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "eda_follow_front", default=False)


@contextlib.contextmanager
def tool_call(name: str = ""):
    """One MCP tool call: its commands to Altium keep the turn between
    them while other sessions wait, and when it returns the turn passes
    on at once (callend_<tag>-<call>.flag) instead of after a grace.
    ``name`` is the tool: a tool that writes marks its commands so, and
    Altium keeps another session's writes off the document (OwnerRefusal
    in StatusForm.pas)."""
    call = {"id": uuid.uuid4().hex[:4], "sent": False, "tool": name}
    token = _TOOL_CALL.set(call)
    try:
        yield call
    finally:
        _TOOL_CALL.reset(token)
        if call["sent"]:
            _end_call(call["id"])


def name_tool_call(name: str) -> None:
    """The tool a proxy (tool_invoke, a family tool) is running inside the
    current call, so its commands are marked by what it does."""
    call = _TOOL_CALL.get()
    if call is not None and name:
        call["tool"] = name


def _end_call(call_id: str) -> None:
    """Tell Altium this session's tool call is over. Never raises."""
    try:
        ws = get_config().workspace_dir
        for old in ws.glob(f"callend_{_SESSION_TAG}-*.flag"):
            discard(old)
        (ws / f"callend_{_SESSION_TAG}-{call_id}.flag").write_text("1", encoding="ascii")
    except Exception:  # noqa: BLE001 - the grace in Altium covers a lost flag
        pass


@contextlib.contextmanager
def background():
    """Requests made here wait in Altium while any session has one."""
    token = _BACKGROUND.set(True)
    try:
        yield
    finally:
        _BACKGROUND.reset(token)


@contextlib.contextmanager
def following_front(background: bool = True):
    """Calls on this thread act on whatever document is in front: they
    neither put the session's document back first nor make a document
    they moved to the session's. For a viewer that follows the person at
    Altium, such as the dashboard's cockpit, which shares the bridge of
    the MCP server it runs in. Its requests are background work, waiting
    in Altium while any session has one, unless ``background`` is False:
    something a person just asked for on the page."""
    before = getattr(_CALL, "follow_front", False)
    _CALL.follow_front = True
    token = _BACKGROUND.set(background)
    follow = _FOLLOW.set(True)
    try:
        yield
    finally:
        _CALL.follow_front = before
        _BACKGROUND.reset(token)
        _FOLLOW.reset(follow)


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


#: Commands polled every few seconds (the dashboard's cockpit): their
#: routine lines would bury everything else in the trace, so only lines
#: about something going wrong are written for them.
_QUIET_COMMANDS = frozenset({"pcb.cockpit_state"})
_ROUTINE_LINES = ("SEND ", "POLL_START ", "POLL_SEEN ", "POLL_MATCH ")


def _trace_log(workspace_dir: Path, msg: str) -> None:
    """Append a line to workspace/bridge_trace.log. Never raises."""
    if getattr(_CALL, "command", "") in _QUIET_COMMANDS and msg.startswith(_ROUTINE_LINES):
        return
    try:
        path = workspace_dir / "bridge_trace.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except OSError:  # a trace line is not worth failing a call over
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
    except (OSError, ctypes.ArgumentError):  # the caller falls back to sleeping
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
    except (OSError, ctypes.ArgumentError):  # the caller re-checks and waits again
        return False


def _close_dir_watcher(handle) -> None:
    if _kernel32 is None or handle is None:
        return
    try:
        _kernel32.FindCloseChangeNotification(handle)
    except (OSError, ctypes.ArgumentError):  # the handle is gone either way
        pass


def _claude_session_tag() -> str:
    """Eight hex digits naming the Claude session this server runs under.

    A Claude session starts more than one eda-agent server (the full and
    the two-tool surface), and the reload proxy restarts its child, so a
    random tag per process gave one session several. Altium then refused
    a session's own write as another session's (measured 2026-11-05: a
    repour through one server, the DRC after it through the other). The
    tag is made from the nearest Claude process above this one, its pid
    and start time, so every server of a session shares it. Random when
    no Claude process is found (a script, a test).
    """
    try:
        import hashlib
        import psutil
        for parent in psutil.Process().parents():
            name = parent.name().lower()
            if name in ("claude.exe", "claude") or (
                    name.startswith("node") and any(
                        "claude" in part.lower() for part in parent.cmdline()[:3])):
                return hashlib.sha1(
                    f"{parent.pid}:{parent.create_time()}".encode()).hexdigest()[:8]
    except Exception:  # noqa: BLE001 - any failure falls back to a random tag
        pass
    return uuid.uuid4().hex[:8]


#: This server's session tag, shared by every eda-agent server of one Claude
#: session. It tells Altium which session a request came from: it serves one
#: session at a time in slots when several are waiting, never slots a session
#: that is alone (ScanForRequestFile in Main.pas), and gives documents to the
#: session that writes them.
_SESSION_TAG = _claude_session_tag()
_SEQUENCE = itertools.count(1)


def dialog_owner(workspace_dir: Path) -> Optional[dict[str, Any]]:
    """Whose call raised the dialog Altium is showing, from what is on disk.

    The handler that raised a modal is still running, so its progress
    marker is there, named by its request id, which starts with the
    session's tag; the shared trace holds that session's last command.
    None when no marker says.
    """
    try:
        markers = sorted(Path(workspace_dir).glob("progress_*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    if not markers:
        return None
    request_id = markers[0].name[len("progress_"):-len(".json")]
    tag = request_id.split("-", 1)[0].lower()
    command = ""
    try:
        trace = Path(workspace_dir) / "bridge_trace.log"
        with open(trace, "rb") as f:
            f.seek(max(0, trace.stat().st_size - 400_000))
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            m = re.search(r" SEND cmd=(\S+) id=" + re.escape(tag[:8]) + r"\b", line)
            if m and not m.group(1).startswith("application.ping"):
                command = m.group(1)
                break
    except OSError:
        pass
    return {"session": tag, "yours": tag == _SESSION_TAG.lower(), "command": command}


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

    Only with ``EDA_AGENT_KEEP_BOARD_IN_VIEW`` set or Keep board in view on
    the panel's Config tab, only after a board,
    schematic or object edit (``pcb.`` and ``generic.`` commands that are
    not reads), and at most once every two seconds.
    """
    if os.environ.get(KEEP_IN_VIEW_ENV, "").strip().lower() not in ("1", "true", "yes"):
        from ..preferences import keep_in_view
        if not keep_in_view():
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
    tail = uuid.uuid4().hex
    call = _TOOL_CALL.get()
    if _BACKGROUND.get():
        tail = "q" + tail
    elif call is not None:
        call["sent"] = True
        tail = "k" + call["id"] + tail
    return f"{_SESSION_TAG}-{seq:08d}-{tail[:12]}"


@dataclass
class CommandRequest:
    """A command request to be sent to Altium."""

    command: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_short_id)
    #: The document this session last had in front. Altium puts it back
    #: in front before running the command (SessionFocus in StatusForm.pas).
    session_doc: str = ""
    #: Sent by a tool that writes: Altium refuses it on a document another
    #: session is editing, and gives the document to this session after.
    writes: bool = False
    #: "user" for the person at the dashboard, never refused.
    actor: str = ""

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
        if self.actor:
            out["actor"] = self.actor
        if self.writes:
            out["writes"] = "1"
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
    # The dashboard's cockpit polls it to follow the person at Altium.
    "pcb.cockpit_state",
})


def _actor() -> str:
    """'user' for the dashboard, which acts for the person at Altium."""
    return "user" if (getattr(_CALL, "follow_front", False) or _FOLLOW.get()) else ""


@functools.lru_cache(maxsize=None)
def _tool_writes(name: str) -> bool:
    if not name:
        return False
    try:
        from ..tools.metadata import READONLY, interaction_of
        return interaction_of(name) != READONLY
    except Exception:  # noqa: BLE001  unknown: let it through unmarked
        return False


def _call_writes() -> bool:
    """Whether the tool call these commands belong to can change a design."""
    call = _TOOL_CALL.get()
    return bool(call) and _tool_writes(str(call.get("tool") or ""))


def _unpinned(command: str, params: Optional[dict]) -> bool:
    return (getattr(_CALL, "follow_front", False) or _FOLLOW.get()
            or command in _UNPINNED) or (
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
        # A report opened in Altium's browser (file:///...html) is not a
        # document; adopting it made every later call reopen the address
        # with another file:/// in front.
        if "://" in str(moved["to"]):
            return current
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
        #: Titles of dialogs a caller is answering itself while its call
        #: runs; the poll loop does not stop for them. See expecting().
        self.expected_dialogs: frozenset = frozenset()
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
        except OSError as e:  # leftover files cost nothing now; say so in the trace
            _trace_log(self.config.workspace_dir, f"orphan sweep failed: {e}")
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
        #: The blocking dialogs the last look saw; see _DIALOG_PROBE_EVERY.
        last_blocked = ""

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
                seen = _blocking_signature(early, self.expected_dialogs)
                confirmed = bool(seen) and seen == last_blocked
                last_blocked = seen
                if confirmed:
                    waited = now - start
                    _trace_log(
                        workspace_dir,
                        f"POLL_DIALOG id={request_id[:8]} "
                        f"after={waited:.1f}s "
                        f"summary={early.get('summary', '')[:120]}",
                    )
                    owner = dialog_owner(workspace_dir)
                    raise AltiumTimeoutError(
                        f"Altium is showing a modal dialog {waited:.0f}s into "
                        f"this call, so the handler cannot run and waiting for "
                        f"the timeout would tell you nothing more."
                        + self._dialog_suffix(early, owner),
                        details={"dialogs": early, "raised_by": owner,
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
                + self._dialog_suffix(probe, dialog_owner(workspace_dir) if probe else None),
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
            + self._dialog_suffix(probe, dialog_owner(workspace_dir) if probe else None),
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
        def drop_marker() -> None:
            try:
                marker.unlink()
            except OSError:
                pass

        # The restart runs on in the background and drops the marker when
        # it is over; this call reports the crash at once (see recover).
        outcome = None
        try:
            outcome = script_crash.recover(workspace_dir, process.pid,
                                           command, request_id, dialog,
                                           reply_came,
                                           on_restarted=drop_marker)
        finally:
            restarting = bool(outcome and (outcome.get("restart") or {})
                              .get("in_background"))
            if not restarting:
                drop_marker()
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
    def _dialog_suffix(probe: Optional[dict], owner: Optional[dict] = None) -> str:
        """One sentence naming what is blocking, for the timeout text."""
        if not probe:
            return ""
        text = (
            f" A DIALOG IS ON SCREEN, so the loop is blocked rather than "
            f"absent: {probe.get('summary', 'a modal is open')}.")
        if owner and not owner.get("yours"):
            return text + (
                f" ANOTHER CLAUDE SESSION'S CALL raised it (session "
                f"{owner['session']}"
                + (f", {owner['command']}" if owner.get("command") else "")
                + "): leave it to that session, which is waiting on it too, "
                  "and try again later; answer it only if the user says so.")
        if owner and owner.get("yours"):
            text += (" This session's own call raised it"
                     + (f" ({owner['command']})" if owner.get("command") else "")
                     + ".")
        return text + (
            " Answer it, or read it with app_list_open_dialogs and press a "
            "button with app_press_dialog_button; both work while the bridge "
            "does not.")

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
            session_doc="" if _unpinned(command, params) else self._session_doc,
            writes=_call_writes(), actor=_actor())
        workspace_dir = self.config.workspace_dir

        _CALL.command = command
        _CALL.answered_box = None
        _trace_log(workspace_dir, f"SEND cmd={command} id={request.id[:8]}")
        self._publish_request(request)
        note = logger.debug if command in _QUIET_COMMANDS else logger.info
        note("Sent command: %s (id=%s)", command, request.id[:8])

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
            note("Command %s succeeded", command)
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
        refusal = refuse_command(command, params)
        if refusal is not None:
            return {"success": False, "error": {
                "code": "REFUSED_BY_POLICY", "message": refusal}}
        if not self.is_altium_running():
            raise AltiumNotRunningError()
        if timeout is None:
            timeout = self.config.poll_timeout
        self._ensure_keepalive()
        return self._execute_command(command, one_spelling(params or {}), timeout)

    @contextlib.contextmanager
    def expecting(self, title: str):
        """While inside, a dialog whose title contains ``title`` does not
        stop a call: the caller is answering it itself."""
        before = self.expected_dialogs
        self.expected_dialogs = before | {title}
        try:
            yield
        finally:
            self.expected_dialogs = before

    async def send_command_async(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        # An item of a bulk call: its request waits with the others' and
        # goes to Altium in one bulk request. See bridge/bulk.py.
        from . import bulk
        collector = bulk.current()
        if collector is not None and refuse_command(command, params) is None:
            return await collector.submit(command, params or {}, timeout)
        try:
            return await self.send_command_direct(command, params, timeout)
        except AltiumError as exc:
            path = await self._library_to_focus(command, exc)
            if not path:
                raise
        # A library command with the other kind of library in front: with
        # exactly one library of the kind it needs open, that is the one it
        # meant. The agent otherwise focused libraries by hand, and a
        # library focused early shows its last part until the next exists.
        await self.send_command_direct(
            "application.set_active_document", {"file_path": path})
        return await self.send_command_direct(command, params, timeout)

    async def _library_to_focus(self, command: str, exc: Exception) -> str:
        """The only open library of the kind a refused ``library.*``
        command needs, else empty."""
        ext = {"NO_PCBLIB": ".pcblib", "NO_SCHLIB": ".schlib"}.get(
            str(getattr(exc, "code", "") or ""))
        if not ext or not command.startswith("library."):
            return ""
        try:
            docs = await self.send_command_direct("application.get_open_documents", {})
        except AltiumError:
            return ""
        docs = docs if isinstance(docs, list) else (docs or {}).get("documents") or []
        paths = sorted({str(d.get("file_path") or "") for d in docs
                        if isinstance(d, dict)
                        and str(d.get("file_path") or "").lower().endswith(ext)})
        return paths[0] if len(paths) == 1 else ""

    async def send_bulk_async(
        self,
        command: str,
        items: list,
        timeout: Optional[float] = None,
        keep_going: bool = False,
    ) -> list:
        """Run one command for many items in ONE request, and so in one
        Altium edit (ProcessBulk in StatusForm.pas).

        Returns one (ok, value) per item, in order: the item's data, or
        the exception its own call would have raised. Items after a failed
        one are not run unless ``keep_going``.
        """
        params: dict[str, Any] = {"bulk_count": len(items),
                                  "bulk_continue": bool(keep_going)}
        for index, item in enumerate(items):
            params[f"bulk_item_{index}"] = item or {}
        data = await self.send_command_direct(command, params, timeout)
        envelopes = data.get("items") if isinstance(data, dict) else None
        if not isinstance(envelopes, list):
            raise AltiumCommandError(
                f"{command} answered a bulk request without per-item replies; "
                f"the running script may predate bulk calls: deploy it.",
                code="BULK_UNSUPPORTED", details={"reply": data})
        out = [self._bulk_item_outcome(env) for env in envelopes]
        while len(out) < len(items):
            out.append((False, AltiumCommandError(
                "No reply for this item.", code="BULK_ITEM_MISSING")))
        return out

    @staticmethod
    def _bulk_item_outcome(envelope: Any) -> tuple:
        """(ok, data or exception) for one item's reply in a bulk reply."""
        if not isinstance(envelope, dict):
            return False, AltiumCommandError("Unreadable item reply.",
                                             code="BULK_ITEM_UNREADABLE")
        if envelope.get("skipped"):
            return False, AltiumCommandError(
                "Not run: an earlier item in the same bulk call failed.",
                code="SKIPPED")
        response = CommandResponse.from_dict(envelope)
        if response.success:
            return True, _with_envelope_notes(response.data, response)
        error = response.error or {}
        message = error.get("message", "Unknown error")
        if response.next_step:
            message = f"{message} Next step: {response.next_step}"
        try:
            raise_for_code(error.get("code", "UNKNOWN_ERROR"), message,
                           error.get("details"))
        except AltiumError as exc:
            return False, exc
        return False, AltiumCommandError(message)

    async def send_command_direct(
        self,
        command: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send one request now, collector or not."""
        refusal = refuse_command(command, params)
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
        # The caller's context, so the request carries its turn mark.
        result = await loop.run_in_executor(
            _executor,
            contextvars.copy_context().run,
            self._execute_command,
            command,
            one_spelling(params or {}),
            timeout,
        )
        if _wants_board_in_view(command):
            try:
                await loop.run_in_executor(
                    _executor, self._execute_command,
                    "generic.zoom", {"action": "fit"}, 10.0)
            except Exception:  # a view nicety never fails the call it follows
                pass
        return result

    def ping(self) -> bool:
        try:
            self.send_command("application.ping", timeout=3.0)
            return True
        except Exception:  # any failure is the answer: Altium did not reply
            return False

    def ping_with_version(self) -> Optional[dict[str, Any]]:
        try:
            result = self.send_command("application.ping", timeout=3.0)
        except Exception:  # any failure is the answer: Altium did not reply
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
