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

    # "Expensive" singular tools hint after only 2 calls instead of 3:
    # they're compile-gated or large-response (~700 ms each), so a loop
    # of 10+ of them is minutes of wall time. Nudge toward the bulk
    # variant as soon as a second call arrives in the window.
    _EXPENSIVE_THRESHOLD = 2
    _EXPENSIVE: frozenset[str] = frozenset({
        "get_nets",
        "get_connectivity",
        "get_component_info",
        "get_design_stats",
        "get_bom",
    })

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
        "create_object": (
            "batch_create",
            "Pass a list of create ops to batch_create to bundle them in one IPC call.",
        ),
        "delete_objects": (
            "batch_delete",
            "Pass a list of delete ops to batch_delete to bundle them in one IPC call.",
        ),
        "place_wire": (
            "place_wires",
            "Pass a list of wire endpoints to place_wires to place them all in one call.",
        ),
        "place_sch_component_from_library": (
            "place_sch_components_from_library",
            "Pass a list of placements to place_sch_components_from_library to lay out the BOM in one call.",
        ),
        "sch_attach_spice_primitive": (
            "sch_attach_spice_primitives",
            "Pass a list of attachments to sch_attach_spice_primitives to do the whole readiness set in one call.",
        ),
        "lib_add_pin": (
            "lib_add_pins",
            "Pass a list of pins to lib_add_pins to build the whole pinout in one call.",
        ),
        "get_connectivity": (
            "get_connectivity_many",
            "Pass a list of designators to get_connectivity_many to pull them all in one round-trip instead of ~700 ms per call.",
        ),
        "get_nets": (
            "get_nets",
            "Call get_nets ONCE with no filters (component='', net_name='', raise limit) to pull the entire pin-net table, then filter locally. Each filtered call is ~700 ms and compiles the project.",
        ),
    }

    @classmethod
    def _threshold_for(cls, tool_name: str) -> int:
        return cls._EXPENSIVE_THRESHOLD if tool_name in cls._EXPENSIVE else cls._THRESHOLD

    @classmethod
    def record_and_hint(cls, tool_name: str) -> dict[str, str] | None:
        """Record a call. Return a hint dict if the threshold just tripped."""
        bulk = cls.BULK_EQUIVALENTS.get(tool_name)
        if bulk is None:
            return None

        now = time.monotonic()
        bulk_name, nudge_text = bulk
        threshold = cls._threshold_for(tool_name)

        with cls._lock:
            dq = cls._windows.setdefault(tool_name, deque())
            dq.append(now)
            cutoff = now - cls._WINDOW_SEC
            while dq and dq[0] < cutoff:
                dq.popleft()
            count = len(dq)

            if count < threshold:
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
