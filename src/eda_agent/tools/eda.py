# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EDA-agnostic tools: the main flows, backend-independent.

These are the same operation whichever EDA is active -- they pull a normalized
snapshot from the active backend (Altium or KiCad) and run shared logic on it.
An optional ``backend`` argument targets a specific tool when the server runs in
``both`` mode. Failures report ``{"ok": False, "reason": ...}`` (the offline
error family).
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.backends import BackendUnavailableError, resolve_backend
from ..core.review_engine import review_snapshot


async def _snapshot(backend: Optional[str]):
    """Resolve the backend and pull its snapshot, or return a reason string."""
    be = None
    try:
        # Inside the guard: an unrecognised name is refused here rather
        # than quietly resolving to another EDA, and that refusal has to
        # reach the caller as a reason instead of a traceback.
        be = resolve_backend(backend)
        snap = await be.snapshot()
    except BackendUnavailableError as e:
        return None, str(e)
    except Exception as e:  # keep a raw backend traceback out of the response
        # Named from the request when resolution itself failed, since
        # there is no adapter to ask for a name in that case.
        who = be.name if be is not None else (backend or "the active backend")
        return None, f"{who} snapshot failed: {e}"
    return snap, None


def register_eda_tools(mcp) -> None:

    @mcp.tool()
    async def review_design(backend: Optional[str] = None) -> dict[str, Any]:
        """EDA-agnostic design review of the open board.

        Runs the same netlist-level review regardless of the active EDA
        (Altium or KiCad): board stats and net classes, annotation hygiene
        (unannotated, duplicate references, missing values), and connectivity
        (single-pin nets, shorted pins/components, unconnected parts and pads,
        missing decoupling). ``backend`` ("altium" | "kicad") targets one tool
        when the server runs in "both" mode; omit it to use the active backend.

        Geometric DRC (clearances, courtyards) is separate and backend-specific.
        """
        snap, reason = await _snapshot(backend)
        if snap is None:
            return {"ok": False, "reason": reason}
        return review_snapshot(snap)

    @mcp.tool()
    async def get_board_info(backend: Optional[str] = None) -> dict[str, Any]:
        """Object counts and net-class summary for the open board, from the
        active backend (or the one named by ``backend``)."""
        snap, reason = await _snapshot(backend)
        if snap is None:
            return {"ok": False, "reason": reason}
        result = review_snapshot(snap)
        out = {"ok": True, "source": snap.source, "board": snap.board_name,
               "stats": result["stats"], "net_classes": result["net_classes"]}
        if (snap.raw_stats or {}).get("parts_from"):
            out["parts_from"] = snap.raw_stats["parts_from"]
        return out

    @mcp.tool()
    async def list_components(backend: Optional[str] = None) -> dict[str, Any]:
        """Every component on the open board (reference, value, footprint,
        kind), from the active backend (or the one named by ``backend``)."""
        snap, reason = await _snapshot(backend)
        if snap is None:
            return {"ok": False, "reason": reason}
        comps = [{"reference": p.refdes, "value": p.value,
                  "footprint": p.footprint, "kind": p.kind,
                  "dnp": p.dnp} for p in snap.parts]
        out = {"ok": True, "source": snap.source, "count": len(comps),
               "components": comps}
        if (snap.raw_stats or {}).get("parts_from"):
            out["parts_from"] = snap.raw_stats["parts_from"]
        return out

    @mcp.tool()
    async def run_drc(backend: Optional[str] = None) -> dict[str, Any]:
        """Run geometric design-rule check (DRC) on the open board.

        Dispatches to the active backend: Altium runs its native DRC; KiCad
        runs ``kicad-cli pcb drc`` (KiCad's own CLI) on the board file on disk,
        so save pending edits first for current results. Returns violation
        counts and a normalized list. ``backend`` targets one tool in "both"
        mode.
        """
        be = None
        try:
            be = resolve_backend(backend)
            return await be.run_drc()
        except BackendUnavailableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            who = be.name if be is not None else (backend or "the active backend")
            return {"ok": False, "reason": f"{who} DRC failed: {e}"}

    @mcp.tool()
    async def run_erc(backend: Optional[str] = None) -> dict[str, Any]:
        """Run the schematic Electrical Rules Check (ERC) on the open design.

        Dispatches to the active backend: Altium runs its native ERC; KiCad runs
        ``kicad-cli sch erc`` on the schematic file on disk (save pending edits
        first). Returns violation counts and a normalized list. ``backend``
        targets one tool in "both" mode.
        """
        be = None
        try:
            be = resolve_backend(backend)
            return await be.run_erc()
        except BackendUnavailableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            who = be.name if be is not None else (backend or "the active backend")
            return {"ok": False, "reason": f"{who} ERC failed: {e}"}

    @mcp.tool()
    async def list_nets(backend: Optional[str] = None) -> dict[str, Any]:
        """Every net on the open board with pin count and class (power / ground
        / signal), from the active backend (or the one named by ``backend``)."""
        snap, reason = await _snapshot(backend)
        if snap is None:
            return {"ok": False, "reason": reason}
        nets = [{"name": n.name, "pin_count": len(n.pins),
                 "is_power": n.is_power, "is_ground": n.is_ground}
                for n in snap.nets]
        return {"ok": True, "source": snap.source, "count": len(nets),
                "nets": nets}
