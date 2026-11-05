# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Response-time nudge that steers callers toward bulk tools.

Each singular tool that has a bulk equivalent, and every tool that
takes ``items`` (see bulk.py), registers every call with
``BulkHintTracker``. If the same tool is called more than
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
        "proj_get_nets",
        "proj_get_connectivity",
        "proj_get_component_info",
        "proj_get_stats",
        "proj_get_bom",
    })

    _lock = threading.Lock()
    _windows: dict[str, deque[float]] = {}
    _last_hint_at: dict[str, float] = {}

    # singular_tool -> (bulk_tool_name, one-line nudge text)
    BULK_EQUIVALENTS: dict[str, tuple[str, str]] = {
        "obj_modify": (
            "obj_modify",
            "When each target needs a different value, give obj_modify items=[{filter, set}, ...]: one call, one Altium transaction.",
        ),
        "obj_create": (
            "obj_create",
            "Give obj_create items=[{object_type, properties}, ...] to create them all in one call.",
        ),
        "obj_delete": (
            "obj_delete",
            "Give obj_delete items=[{object_type, filter}, ...] to delete them all in one call.",
        ),
        "proj_get_connectivity": (
            "proj_get_connectivity_many",
            "Pass a list of designators to proj_get_connectivity_many to pull them all in one round-trip instead of ~700 ms per call.",
        ),
        "proj_get_component_info": (
            "proj_get_component_info_many",
            "Pass a list of designators to proj_get_component_info_many to pull them all in one round-trip. The compile (when with_pin_nets=True) happens once for the whole batch, not once per designator.",
        ),
        "proj_get_nets": (
            "proj_get_nets",
            "Call proj_get_nets ONCE with no filters (component='', net_name='', raise limit) to pull the entire pin-net table, then filter locally. Each filtered call is ~700 ms and compiles the project.",
        ),
        "lib_add_footprint_pad": (
            "lib_add_footprint_pads",
            "Pass a list of pads to lib_add_footprint_pads to place the whole footprint in one IPC call instead of one per pad. lib_create_standard_footprint emits a full standard package in a single call.",
        ),
        "lib_add_footprint_track": (
            "lib_add_footprint_tracks",
            "Pass a list of tracks to lib_add_footprint_tracks to draw the silkscreen / courtyard in one IPC call instead of one per segment.",
        ),
        # Plan-authoring tools (pure Python -- the cost is LLM round-trips
        # and a full-plan echo per call, not IPC). compose_netlist batches
        # an ordered op list and validates once.
        "design_add_part": (
            "design_compose_netlist",
            "Building several parts / blocks / buses? design_compose_netlist applies a whole ordered list of add_part / add_block / connect_bus ops in one call and validates once -- fewer round-trips than one tool call per element.",
        ),
        "design_add_circuit_block": (
            "design_compose_netlist",
            "Adding several blocks? design_compose_netlist applies a list of {op:add_block,...} (plus add_part / connect_bus) ops in one call and validates once, instead of one round-trip per block.",
        ),
        "design_connect_bus": (
            "design_compose_netlist",
            "Wiring several buses or mixing buses with parts/blocks? design_compose_netlist applies them all in one ordered call and validates once.",
        ),
    }

    @classmethod
    def _threshold_for(cls, tool_name: str) -> int:
        return cls._EXPENSIVE_THRESHOLD if tool_name in cls._EXPENSIVE else cls._THRESHOLD

    @classmethod
    def record_and_hint(cls, tool_name: str,
                        takes_items: bool = False) -> dict[str, str] | None:
        """Record a call. Return a hint dict if the threshold just tripped.

        A tool not listed above is tracked when it ``takes_items``, and its
        hint points at that argument.
        """
        bulk = cls.BULK_EQUIVALENTS.get(tool_name)
        if bulk is None and takes_items:
            bulk = (tool_name,
                    f"Give {tool_name} items=[{{...}}, ...], one dict of its "
                    "arguments per item: one call, one undo step.")
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
