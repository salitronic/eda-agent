# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Response-time nudge that steers callers toward bulk tools.

Each singular tool that has a bulk equivalent registers every call
with ``BulkHintTracker``. If the same tool is called more than
``_THRESHOLD`` times inside ``_WINDOW_SEC`` seconds, the next response
carries a ``_hint_bulk`` field pointing at the batch variant. The
nudge fires at most once per window so repeated callers see it once
and then get out of the way.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class BulkHintTracker:
    """Process-wide tracker of per-tool call rates."""

    _WINDOW_SEC = 10.0
    _THRESHOLD = 3

    _lock = threading.Lock()
    _windows: dict[str, deque[float]] = {}
    _last_hint_at: dict[str, float] = {}

    # singular_tool -> (bulk_tool_name, one-line nudge text)
    BULK_EQUIVALENTS: dict[str, tuple[str, str]] = {
        "pcb_move_component": (
            "pcb_move_components",
            "Pass a list of moves to pcb_move_components to batch them in one IPC call.",
        ),
        "pcb_place_track": (
            "pcb_place_tracks",
            "Pass a list of tracks to pcb_place_tracks to batch them in one IPC call.",
        ),
        "modify_objects": (
            "batch_modify",
            "When each target needs a different value, batch_modify does them all in one call.",
        ),
    }

    @classmethod
    def record_and_hint(cls, tool_name: str) -> dict[str, str] | None:
        """Record a call. Return a hint dict if the threshold just tripped."""
        bulk = cls.BULK_EQUIVALENTS.get(tool_name)
        if bulk is None:
            return None

        now = time.monotonic()
        bulk_name, nudge_text = bulk

        with cls._lock:
            dq = cls._windows.setdefault(tool_name, deque())
            dq.append(now)
            cutoff = now - cls._WINDOW_SEC
            while dq and dq[0] < cutoff:
                dq.popleft()
            count = len(dq)

            if count < cls._THRESHOLD:
                return None

            last = cls._last_hint_at.get(tool_name, 0.0)
            if (now - last) < cls._WINDOW_SEC:
                return None
            cls._last_hint_at[tool_name] = now

        return {
            "bulk_tool": bulk_name,
            "hint": (
                f"You called {tool_name} {count} times in the last "
                f"{int(cls._WINDOW_SEC)}s. {nudge_text}"
            ),
        }

    @classmethod
    def reset(cls) -> None:
        """Clear all tracked state. Used by tests."""
        with cls._lock:
            cls._windows.clear()
            cls._last_hint_at.clear()
