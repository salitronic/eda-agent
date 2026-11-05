# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard's board cockpit: the board in front, kept current, and the
layout checks rerun after every change.

One worker thread polls ``pcb.cockpit_state`` (a stamp of the parts, one of
the copper, and the selection) while a browser has the cockpit open. When a
stamp changes and holds still for a moment, the board is read again, only
the sections that changed, and the fast checks run, then the slower ones.
Every call follows the document in front (``following_front``): the cockpit
never puts a document back or adopts one.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("eda_agent.web.cockpit")

#: Shortest and longest poll period while a browser is watching (the period
#: stretches to keep the cockpit near a tenth of Altium's script time), and
#: how long without a browser request before the worker idles.
POLL_S = 1.5
MAX_POLL_S = 10.0
IDLE_AFTER_S = 20.0

#: Above this many tracks, arcs and vias the copper is not walked for its
#: stamp (five seconds at 11,000); its count, and Refresh, stand in.
WALK_LIMIT = 4000

#: Boards remembered, so switching between them needs no read.
MEMO_BOARDS = 4

#: Checks run straight after a read, then the slow ones.
FAST_CHECKS = ["connectivity_summary", "drc", "corner_audit", "pad_entry_audit",
               "plane_region_audit", "rotation_audit"]
SLOW_CHECKS = ["placement_audit", "loop_audit"]

#: Which read sections each stamp covers; the others are read every time.
_PARTS_SECTIONS = {"pads", "components"}
_COPPER_SECTIONS = {"copper"}


#: Walk the copper for its stamp every this many polls; between walks only
#: its count is read, which is far cheaper on a routed board.
COPPER_EVERY = 4


def merge_copper(state: dict, walked: str) -> tuple[dict, str, bool]:
    """A reply with the copper only counted ("n:<count>") takes the last
    walked stamp while the count is unchanged. Returns the state, the
    walked stamp to keep, and whether to walk at the next poll."""
    stamp = str(state.get("copper_stamp") or "")
    if not stamp.startswith("n:"):
        return state, stamp, False
    count = stamp[2:]
    if walked and walked.split(":", 1)[0] == count:
        return {**state, "copper_stamp": walked}, walked, False
    return state, walked, True


def _send_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


class SectionCache:
    """Raw replies of the board read, kept per request, so a re-read asks
    Altium only for the sections whose stamp moved."""

    def __init__(self) -> None:
        self.replies: dict[str, Any] = {}
        self.file = ""

    def sender(self, send: Callable[[str, dict], Any], fresh: set[str]):
        """A send for read_board that answers from the cache for any
        section not in ``fresh``."""
        def wrapped(command: str, params: dict) -> Any:
            section = str(params.get("section", ""))
            key = _send_key(params)
            if section not in fresh and key in self.replies:
                return self.replies[key]
            reply = send(command, params)
            self.replies[key] = reply
            return reply
        return wrapped


def stale_sections(before: Optional[dict], now: dict) -> Optional[set[str]]:
    """The sections to read again after a state change; None when nothing
    changed. A new document reads every section."""
    if not now.get("board"):
        return None
    if before is None or before.get("file", "").lower() != now.get("file", "").lower() \
            or not before.get("board"):
        return {"board", "pads", "components", "copper", "rules", "classes"}
    fresh: set[str] = set()
    if before.get("parts_stamp") != now.get("parts_stamp"):
        fresh |= _PARTS_SECTIONS
    if before.get("copper_stamp") != now.get("copper_stamp"):
        fresh |= _COPPER_SECTIONS
    if not fresh:
        return None
    return fresh | {"board", "rules", "classes"}


def _at(item: dict) -> Optional[list[float]]:
    at = item.get("at")
    if isinstance(at, (list, tuple)) and len(at) == 2:
        return [float(at[0]), float(at[1])]
    if "x" in item and "y" in item:
        return [float(item["x"]), float(item["y"])]
    return None


def findings_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Every located finding of an audit run, one row each: ``check``,
    ``kind``, ``severity``, ``text``, ``at`` (mils) and ``refs`` (parts and
    nets it names)."""
    out: list[dict[str, Any]] = []
    checks = result.get("checks") or {}

    def add(check, kind, severity, text, item, refs=()):
        out.append({"check": check, "kind": kind, "severity": severity,
                    "text": text, "at": _at(item), "refs": [x for x in refs if x]})

    pl = checks.get("placement_audit") or {}
    for o in pl.get("overlaps") or []:
        add("placement", "overlap", "error",
            f"{o.get('a')} overlaps {o.get('b')} ({o.get('area_sq_mils', 0):.0f} sq mil)",
            o, [o.get("a"), o.get("b")])
    for o in pl.get("pad_gaps") or []:
        add("placement", "pad_gap", "error", f"pads too close: {o.get('a')} / {o.get('b')}",
            o, [o.get("a"), o.get("b")])
    for key, kind, text in (("on_keepouts", "on_keepout", "on a keepout"),
                            ("on_mounting_holes", "on_hole", "on a mounting hole"),
                            ("off_board", "off_board", "off the board")):
        for o in pl.get(key) or []:
            add("placement", kind, "error", f"{o.get('part')} {text}", o, [o.get("part")])
    for v in (checks.get("drc") or {}).get("violations") or []:
        add("drc", v.get("rule", "drc"), "error",
            f"{v.get('rule')}: {v.get('a')} / {v.get('b')} "
            f"{v.get('gap', 0):.2f} mil, needs {v.get('required', 0):.2f}",
            v, [v.get("a"), v.get("b")])
    for c in (checks.get("corner_audit") or {}).get("corners") or []:
        add("corners", c.get("kind", "corner"),
            "error" if c.get("kind") == "acute" else "warning",
            f"{c.get('kind', 'corner').replace('_', ' ')} bend on {c.get('net')} "
            f"({c.get('layer')})", c, [c.get("net")])
    for f in (checks.get("pad_entry_audit") or {}).get("findings") or []:
        add("pad_entry", f.get("kind", "entry"),
            "error" if f.get("severity") == "high" else "warning",
            f"{f.get('pad')}: {f.get('detail', f.get('kind'))}", f,
            [f.get("pad", "").split(".")[0], f.get("net")])
    conn = checks.get("connectivity_summary") or {}
    for key in ("partly_routed", "not_started", "unreached"):
        for n in conn.get(key) or []:
            item = n if isinstance(n, dict) else {"net": n}
            add("connectivity", key, "warning",
                f"{item.get('net', n)}: {key.replace('_', ' ')}", item, [item.get("net")])
    for i in (checks.get("plane_region_audit") or {}).get("islands") or []:
        add("planes", "island", "warning", f"pour island on {i.get('net')}", i, [i.get("net")])
    for s in (checks.get("rotation_audit") or {}).get("suggestions") or []:
        add("rotation", "rotation", "info",
            f"{s.get('ref')}: {s.get('mils_saved', 0):.0f} mil shorter turned "
            f"{s.get('turn_by', 0):.0f} degrees", s, [s.get("ref")])
    # Loops are advisory and unlocated: the worst of each kind, found by its parts.
    for kind, lp in ((checks.get("loop_audit") or {}).get("worst_by_kind") or {}).items():
        add("loops", kind, "info",
            f"largest {kind} loop: {', '.join(lp.get('parts') or [])}, "
            f"{lp.get('area_sq_mils', 0):.0f} sq mil", lp, lp.get("parts") or [])
    return out


class BoardMemo:
    """What the cockpit knows of one board: the state it was read at, its
    section cache, view and findings. Kept per board, so switching back to
    a board that has not changed costs no read."""

    def __init__(self, file: str) -> None:
        self.file = file
        self.cache = SectionCache()
        self.cache.file = file
        self.read_state: Optional[dict] = None
        self.view: Optional[dict] = None
        self.findings: list[dict] = []
        self.summary: dict[str, Any] = {}
        self.walk_stamp = ""
        self.read_seconds = 0.0
        self.diff: Optional[dict] = None
        self.view_time = 0.0


class Cockpit:
    """The worker and what it last saw. ``touch()`` on every browser
    request keeps it running; it idles on its own when nobody looks.

    It stays on the board chosen on the page (``pinned``), whatever comes
    to the front in Altium: the first board it sees is chosen until the
    person picks another. That board is kept current while it is in front;
    ``load`` reads it when it is not, in one turn that puts the document
    the person had in front back. ``follow`` makes it follow the front
    instead."""

    def __init__(self, send: Optional[Callable[[str, dict, float], Any]] = None) -> None:
        self._send = send
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._seen = 0.0
        self._memos: dict[str, BoardMemo] = {}
        self.memo: Optional[BoardMemo] = None      # the board shown
        self.pinned = ""
        self.follow = False
        self.loading = ""                          # the board being loaded
        self.front = ""                            # the document in front
        self.state: Optional[dict] = None          # last pcb.cockpit_state
        self.view_version = 0
        self.checks_version = 0
        self.checks_pending: list[str] = []
        self.activity = "idle"
        self.error = ""
        self.poll_seconds = POLL_S
        self.refresh_asked = False
        self._polls = 0
        self._walk_next = True
        self._pending: Optional[dict] = None

    # -- what the browser reads ----------------------------------------------

    @property
    def view(self) -> Optional[dict]:
        return self.memo.view if self.memo else None

    @property
    def findings(self) -> list[dict]:
        return self.memo.findings if self.memo else []

    @property
    def summary(self) -> dict[str, Any]:
        return self.memo.summary if self.memo else {}

    # -- the bridge, following the front ------------------------------------

    def send(self, command: str, params: dict, timeout: float = 30.0,
             urgent: bool = False) -> Any:
        """One command on the board in front. The cockpit's own reads are
        background work in Altium; ``urgent`` is for what a person just
        asked for on the page."""
        if self._send is not None:
            return self._send(command, params, timeout)
        from ..bridge.altium_bridge import following_front, get_bridge
        with following_front(background=not urgent):
            return get_bridge().send_command(command, params, timeout=timeout)

    # -- lifecycle -----------------------------------------------------------

    def touch(self) -> None:
        with self._lock:
            self._seen = time.time()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="cockpit",
                                                daemon=True)
                self._thread.start()

    def pin(self, file: str) -> None:
        """Stay on this board ("" takes the next board seen in front)."""
        self.pinned = file or ""

    def load(self, file: str, done: Optional[Callable[[], None]] = None) -> dict[str, Any]:
        """Show this board: read it now, in one turn in Altium that brings
        it to the front, reads it and puts back the document that was in
        front. Runs on its own thread; ``loading`` names the board."""
        if self.loading:
            return {"ok": False, "reason": f"{self.loading} is still loading"}
        self.pin(file)
        self.loading = file
        self.touch()

        def work():
            from contextlib import nullcontext
            turn = nullcontext()
            if self._send is None:
                from ..bridge.altium_bridge import tool_call
                turn = tool_call()
            try:
                with turn:
                    self._load(file)
            except Exception as exc:                      # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                self.loading = ""
                if done is not None:
                    done()
        threading.Thread(target=work, name="cockpit-load", daemon=True).start()
        return {"ok": True, "loading": file}

    def _load(self, file: str) -> None:
        self.activity = "bringing the board to the front"
        before = self.send("application.get_active_document", {"front": "true"}, 15.0,
                           urgent=True)
        prev = before.get("file_path", "") if isinstance(before, dict) else ""
        self.send("pcb.focus_board", {"board_path": file}, 30.0, urgent=True)
        state = self.send("pcb.cockpit_state", {"copper": "true"}, 30.0, urgent=True)
        if not isinstance(state, dict) or not _same(state.get("file", ""), file):
            raise RuntimeError(f"{file} did not come to the front in Altium")
        memo = self._memo_for(file)
        if memo is not self.memo:
            self.memo = memo
            self.view_version += 1
            self.checks_version += 1
        state, memo.walk_stamp, self._walk_next = merge_copper(state, memo.walk_stamp)
        self.state = state

        def give_back():
            if prev and not _same(prev, file):
                self.send("application.set_active_document", {"file_path": prev}, 30.0,
                          urgent=True)
        self.refresh(memo, state, force=True, after_read=give_back)

    def ask_refresh(self) -> None:
        """Walk the copper and read the whole board at the next poll."""
        self.refresh_asked = True
        self._walk_next = True

    def _run(self) -> None:
        while time.time() - self._seen < IDLE_AFTER_S:
            try:
                self.step()
            except Exception as exc:                      # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                logger.debug("cockpit step failed", exc_info=True)
            time.sleep(self.poll_seconds)
        self.activity = "idle"

    def _memo_for(self, file: str) -> BoardMemo:
        key = file.lower()
        memo = self._memos.get(key)
        if memo is None:
            memo = self._memos[key] = BoardMemo(file)
            while len(self._memos) > MEMO_BOARDS:
                self._memos.pop(next(iter(self._memos)))
        return memo

    def step(self) -> None:
        """One poll. A change is read once it has held still for one poll,
        so a drag in progress is not chased."""
        self._polls += 1
        big = bool(self.state and (self.state.get("copper") or 0) > WALK_LIMIT)
        walk = self._walk_next or (not big and self._polls % COPPER_EVERY == 0)
        t0 = time.time()
        state = self.send("pcb.cockpit_state", {"copper": "true" if walk else "false"}, 15.0)
        took = time.time() - t0
        # Keep the cockpit near a tenth of Altium's script time, judged on
        # the counting polls: a walk is occasional and slower by design.
        if not walk:
            self.poll_seconds = max(POLL_S, min(MAX_POLL_S, took * 10))
        if not isinstance(state, dict):
            self.error = "Altium did not answer"
            return
        self.error = ""
        self.front = str(state.get("file", ""))
        if self.loading:
            return
        if not state.get("board"):
            self.state = dict(self.state or {}, selected={"parts": [], "nets": [], "count": 0})
            self.activity = "not in front" if self.pinned else "no board in front"
            return
        if self.follow or not self.pinned:
            self.pinned = state["file"]
        if not _same(state.get("file", ""), self.pinned):
            self.activity = "not in front"
            return
        self.state = state
        memo = self._memo_for(state["file"])
        if memo is not self.memo:
            self.memo = memo
            self.view_version += 1
            self.checks_version += 1
        state, memo.walk_stamp, self._walk_next = merge_copper(state, memo.walk_stamp)
        self.state = state
        if self.refresh_asked:
            self.refresh_asked = False
            self.refresh(memo, state, force=True)
            return
        if stale_sections(memo.read_state, state) is None:
            self.activity = "up to date"
            self._pending = None
            return
        held = self._pending is not None and all(
            self._pending.get(k) == state.get(k)
            for k in ("file", "parts_stamp", "copper_stamp"))
        if not held:
            self.activity = "waiting for the change to settle"
            self._pending = state
            return
        self._pending = None
        self.refresh(memo, state)

    def refresh(self, memo: BoardMemo, state: dict, force: bool = False,
                after_read: Optional[Callable[[], None]] = None) -> None:
        """Read the changed sections, publish the view, run the checks."""
        from ..layout import read_altium
        from ..layout.audit import run_audits
        every = {"board", "pads", "components", "copper", "rules", "classes"}
        fresh = every if force else (stale_sections(memo.read_state, state) or set())
        self.activity = "reading " + ", ".join(
            sorted(fresh & {"pads", "components", "copper"}) or ["board"])
        t0 = time.time()
        send = memo.cache.sender(lambda c, p: self.send(c, p, 120.0), fresh)
        board = read_altium.read_board(send, read_altium.PAGE, state["file"])
        memo.read_seconds = round(time.time() - t0, 1)
        memo.read_state = state
        if after_read is not None:
            after_read()
        from ..design import live
        snap = live.snapshot(board)
        memo.diff = live.diff_snapshots(memo.view, snap) if memo.view else None
        memo.view = snap
        memo.view_time = time.time()
        self.view_version += 1
        for names in (FAST_CHECKS, SLOW_CHECKS):
            self.activity = "checking " + ", ".join(names)
            self.checks_pending = list(names)
            result = run_audits(board, names, limit=200)
            with self._lock:
                groups = _check_groups(names)
                memo.findings = [f for f in memo.findings if f["check"] not in groups] \
                    + findings_of(result)
                memo.summary.update(result.get("summary") or {})
                self.checks_version += 1
        self.checks_pending = []
        self.activity = "up to date"

    def snapshot(self) -> dict[str, Any]:
        s = self.state or {}
        copper = s.get("copper") or 0
        return {"activity": self.activity, "error": self.error,
                "file": s.get("file", ""), "kind": s.get("kind", ""),
                "board": bool(s.get("board")), "pinned": self.pinned,
                "front": self.front, "follow": self.follow, "loading": self.loading,
                "in_front": bool(self.memo) and _same(self.front, self.memo.file),
                "shown": self.memo.file if self.memo else "",
                "selected": s.get("selected") or {"parts": [], "nets": [], "count": 0},
                "view_version": self.view_version, "checks_version": self.checks_version,
                "checks_pending": self.checks_pending,
                "read_seconds": self.memo.read_seconds if self.memo else 0.0,
                "poll_seconds": round(self.poll_seconds, 1),
                "copper_walked": copper <= WALK_LIMIT,
                "parts": s.get("parts"), "copper": copper}


def _same(a: str, b: str) -> bool:
    return bool(a) and str(a).replace("/", "\\").lower() == str(b).replace("/", "\\").lower()


def _check_groups(names: list[str]) -> set[str]:
    """findings_of's ``check`` labels for these audit names."""
    labels = {"placement_audit": "placement", "drc": "drc", "corner_audit": "corners",
              "pad_entry_audit": "pad_entry", "connectivity_summary": "connectivity",
              "plane_region_audit": "planes", "rotation_audit": "rotation",
              "loop_audit": "loops"}
    return {labels[n] for n in names if n in labels}


_COCKPIT: Optional[Cockpit] = None


def get_cockpit() -> Cockpit:
    global _COCKPIT
    if _COCKPIT is None:
        _COCKPIT = Cockpit()
    return _COCKPIT
