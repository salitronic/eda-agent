# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Precondition declarations for MCP tools.

Tools declare what state Altium must be in before they can usefully run.
The checker queries Altium's actual state via existing snapshot tools and
raises a PreconditionError with concrete guidance if the requirement isn't
met — replacing unhelpful generic errors like "No PCB document is active"
that didn't tell the caller how to recover.
"""

import enum
import time
from typing import Any, Optional

from .bridge.exceptions import PreconditionError


class Precondition(enum.Enum):
    """Required Altium state for a tool to run.

    Each precondition has a human-readable hint describing how the user
    can satisfy it. The hint is bundled into the PreconditionError details
    so MCP clients can surface actionable guidance.
    """

    HAS_PROJECT = "An Altium project must be open. Open one via File > Open in Altium Designer."
    HAS_FOCUSED_PROJECT = "An Altium project must be focused. Click on a .PrjPcb in the Projects panel."
    HAS_PCB = (
        "A PCB document must be loaded into the project. "
        "Add a .PcbDoc to the project or open one via File > Open."
    )
    HAS_SCHEMATIC = (
        "A schematic document must be loaded into the project. "
        "Add a .SchDoc or open one via File > Open."
    )
    HAS_PCB_LIB = "A PCB library (.PcbLib) document must be active. Open one in the editor."
    HAS_SCH_LIB = "A schematic library (.SchLib) document must be active. Open one in the editor."
    HAS_FOCUSED_DOCUMENT = "A document must be focused (active tab) in Altium Designer."


class _StateCache:
    """Per-bridge cache of Altium state snapshots.

    Snapshots cost one IPC round-trip (~50-100 ms typical), so we cache
    for a short window. The TTL is intentionally tight: a user can change
    Altium state by clicking around, and we want preconditions to reflect
    reality, not a stale snapshot. The cache is invalidated explicitly by
    callers that perform open/close/focus operations.
    """

    DEFAULT_TTL_SECONDS = 1.0

    def __init__(self) -> None:
        self._snapshot: Optional[dict[str, Any]] = None
        self._fetched_at: float = 0.0

    def get(self, ttl: float = DEFAULT_TTL_SECONDS) -> Optional[dict[str, Any]]:
        if self._snapshot is None:
            return None
        if (time.monotonic() - self._fetched_at) > ttl:
            return None
        return self._snapshot

    def set(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot
        self._fetched_at = time.monotonic()

    def invalidate(self) -> None:
        self._snapshot = None
        self._fetched_at = 0.0


_state_cache = _StateCache()


def invalidate_state_cache() -> None:
    """Drop the cached snapshot — call after open/close/focus operations."""
    _state_cache.invalidate()


async def _fetch_state(bridge) -> dict[str, Any]:
    """Build a state snapshot from existing snapshot tools.

    No new Pascal handler is needed: ``application.get_open_documents``
    already returns enough to derive every precondition flag.
    """
    docs = await bridge.send_command_async(
        "application.get_open_documents", {}, timeout=5.0
    )
    docs_list = docs if isinstance(docs, list) else []

    has_project = False
    has_pcb = False
    has_sch = False
    has_pcb_lib = False
    has_sch_lib = False

    for d in docs_list:
        kind = (d.get("document_kind") or "").upper()
        if kind == "PCB":
            has_pcb = True
        elif kind == "SCH":
            has_sch = True
        elif kind == "PCBLIB":
            has_pcb_lib = True
        elif kind == "SCHLIB":
            has_sch_lib = True
        # Any logical document implies a project
        has_project = True

    snapshot = {
        "documents": docs_list,
        "has_project": has_project,
        "has_pcb": has_pcb,
        "has_schematic": has_sch,
        "has_pcb_lib": has_pcb_lib,
        "has_sch_lib": has_sch_lib,
    }
    return snapshot


async def check_preconditions(
    bridge,
    *required: Precondition,
    cache_ttl: float = _StateCache.DEFAULT_TTL_SECONDS,
) -> None:
    """Verify each requirement is satisfied by Altium's current state.

    Raises PreconditionError on the first unmet requirement with a details
    payload that lists every required precondition and which ones failed,
    plus a per-failure hint explaining how the caller can fix the state.
    """
    if not required:
        return

    snapshot = _state_cache.get(cache_ttl)
    if snapshot is None:
        snapshot = await _fetch_state(bridge)
        _state_cache.set(snapshot)

    missing: list[dict[str, str]] = []
    for req in required:
        if req == Precondition.HAS_PROJECT and not snapshot["has_project"]:
            missing.append({"precondition": req.name, "hint": req.value})
        elif req == Precondition.HAS_FOCUSED_PROJECT and not snapshot["has_project"]:
            missing.append({"precondition": req.name, "hint": req.value})
        elif req == Precondition.HAS_PCB and not snapshot["has_pcb"]:
            missing.append({"precondition": req.name, "hint": req.value})
        elif req == Precondition.HAS_SCHEMATIC and not snapshot["has_schematic"]:
            missing.append({"precondition": req.name, "hint": req.value})
        elif req == Precondition.HAS_PCB_LIB and not snapshot["has_pcb_lib"]:
            missing.append({"precondition": req.name, "hint": req.value})
        elif req == Precondition.HAS_SCH_LIB and not snapshot["has_sch_lib"]:
            missing.append({"precondition": req.name, "hint": req.value})
        elif req == Precondition.HAS_FOCUSED_DOCUMENT and not snapshot["documents"]:
            missing.append({"precondition": req.name, "hint": req.value})

    if not missing:
        return

    summary = "; ".join(m["hint"] for m in missing)
    raise PreconditionError(
        message=f"Required Altium state not available: {summary}",
        code="PRECONDITION_FAILED",
        details={
            "required": [req.name for req in required],
            "missing": missing,
            "snapshot": {
                k: v for k, v in snapshot.items() if k != "documents"
            },
        },
    )


def precondition(*required: Precondition):
    """Decorator: prepend a precondition check before invoking an MCP tool.

    Usage::

        @mcp.tool()
        @precondition(Precondition.HAS_PCB)
        async def pcb_get_components(): ...

    The decorated function must accept no positional args (typical for
    MCP tools) and is awaited normally. PreconditionError propagates to
    the MCP client as a structured error.
    """
    from functools import wraps

    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            from .bridge.altium_bridge import get_bridge
            bridge = get_bridge()
            await check_preconditions(bridge, *required)
            return await fn(*args, **kwargs)

        wrapper.__preconditions__ = list(required)
        return wrapper

    return decorator
