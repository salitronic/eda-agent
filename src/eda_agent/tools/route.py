# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Routing tools: the in-house Manhattan router and DRC-feedback
repair planning. No third-party routing engines.

All computation is pure Python over the board geometry dict the bridge
returns (the ``Gen_GetPcbGeometry`` shape that ``pcb_render_svg`` also
consumes). Every tool accepts its data as arguments so calls are
testable and composable; the bridge is touched ONLY when the explicit
``fetch_geometry`` flag is set. All coordinates are MILS, integers on
the wire.

Intended live sequence (the closed routing loop):

1. Fetch the board: any ``pcb_*`` getter that returns the geometry
   payload, or pass ``fetch_geometry=True`` here.
2. Route offline with ``route_plan`` (grid A*).
3. Apply the resulting ops verbatim: ``tracks`` to
   ``pcb_place_tracks``, each via to ``pcb_place_via``.
4. ``pcb_run_drc``; feed the violation payload to
   ``route_plan_repairs``.
5. Apply the repair actions in order (``rip_and_reroute`` =
   ``pcb_delete_net`` + route that net again; ``nudge`` =
   ``obj_modify`` on the offending primitive; ``widen``/``narrow`` =
   ``pcb_set_track_width``; ``escalate`` = stop and ask the user),
   then re-run DRC and repeat from step 4 until clean.
"""

from __future__ import annotations

from typing import Any, Optional

from ..bridge import get_bridge
from ..route import (
    DEFAULT_GRID_PITCH_MILS,
    RouterOptions,
    RoutingProblem,
    route_problem,
)
from ..route.repair import plan_drc_repairs


async def _resolve_geometry(geometry: Any,
                            fetch_geometry: bool) -> tuple[
                                dict[str, Any] | None, str]:
    """The geometry dict and where it came from.

    Fetches from the live board only when ``fetch_geometry`` is set and
    nothing was passed in, which is the default, so the ordinary repeat
    call plans against whatever snapshot the caller still has. That is
    the right default (a fetch is a round trip on a big board) and a
    silent trap: after placing the first net's copper the board has
    moved on, and a second plan built on the old snapshot routes
    straight through the tracks that now exist.

    The source is returned so the reply can say which board state was
    planned against instead of leaving the caller to remember.
    """
    if geometry is None and fetch_geometry:
        bridge = get_bridge()
        geometry = await bridge.send_command_async(
            "generic.get_pcb_geometry", {}, timeout=120.0,
        )
        return (geometry if isinstance(geometry, dict) else None), "live"
    return (geometry if isinstance(geometry, dict) else None), "caller"


def _planned_against(geom: dict[str, Any], source: str) -> dict[str, Any]:
    """What the result says about the board state it used.

    Counts and bbox come straight from the payload, so a caller can
    compare them against a fresh read and see for itself whether the
    plan was built on the current board.
    """
    counts = geom.get("counts") if isinstance(geom.get("counts"), dict) else {}
    out: dict[str, Any] = {
        "source": source,
        "bbox": geom.get("bbox"),
        "counts": counts,
    }
    if source == "caller":
        out["note"] = (
            "planned against the geometry you supplied, which this tool "
            "cannot date. Copper placed since it was read is invisible "
            "here: re-read with fetch_geometry=True before re-planning "
            "after any placement.")
    return out


def register_route_tools(mcp):
    """Register routing tools with the MCP server."""

    @mcp.tool()
    async def route_plan(
        geometry: Optional[dict[str, Any]] = None,
        rules: Optional[dict[str, Any]] = None,
        nets: Optional[list[str]] = None,
        net_classes: Optional[dict[str, str]] = None,
        grid_pitch_mils: int = DEFAULT_GRID_PITCH_MILS,
        bend_penalty: float = 1.0,
        via_cost: float = 10.0,
        max_expansions: int = 200_000,
        fetch_geometry: bool = False,
    ) -> dict[str, Any]:
        """Route the board offline (grid A*) and return placeable ops.

        Pure Python -- no Altium round-trip unless ``fetch_geometry``
        is set. Output ``tracks`` are ``{x1, y1, x2, y2, width, layer,
        net_name}`` (the ``pcb_place_tracks`` item shape) and ``vias``
        are ``{x, y, net, size, hole_size}`` (the ``pcb_place_via``
        params), integer mils, so the result applies verbatim. Per-net
        failure is honest data (status ``failed``), not a tool error.

        Live sequence: fetch geometry -> this tool ->
        ``pcb_place_tracks`` / ``pcb_place_via`` -> ``pcb_run_drc`` ->
        ``route_plan_repairs`` -> apply -> repeat.

        Args:
            geometry: ``Gen_GetPcbGeometry`` payload (bbox / outline /
                pads / tracks / vias, all mils). Pads and copper of
                nets NOT being routed stay in the obstacle map.
            rules: Routing rules, all mils -- ``clearance_mils``,
                ``track_width_mils`` (int, or per-class dict with
                ``"default"``), ``via_size_mils``, ``via_drill_mils``,
                ``layers`` (default TopLayer + BottomLayer). ``None``
                uses defaults.
            nets: Route only these net names; everything else stays a
                static obstacle. Unknown names are reported in
                ``unknown_nets``. ``None`` routes every netted pad
                group.
            net_classes: Net name -> class (``power`` / ``ground`` /
                ``differential`` / ...). Sets routing order and the
                per-class track width. Unlisted nets are ``signal``.
            grid_pitch_mils: Routing grid pitch in mils (default 25).
            bend_penalty: A* corner cost in grid-pitch units.
            via_cost: A* layer-change cost in grid-pitch units.
            max_expansions: Per-connection A* budget so a walled-in
                net fails fast.
            fetch_geometry: When True and ``geometry`` is None, pull
                the live board over the bridge.

        Returns:
            ``{"ok": True, "summary": {nets_total, routed, failed,
            skipped, completion, track_count, via_count,
            total_length_mils}, "order": [...], "nets": {net:
            {status, class, width, tracks, vias, ...}}, "tracks":
            [...], "vias": [...], "validation": {...}}``; with a
            ``nets`` filter also ``requested_nets`` / ``unknown_nets``.
            ``{"ok": False, "reason": ...}`` on malformed input.

            ``geometry`` says which board state was planned against:
            ``source`` is ``live`` when this call read the board and
            ``caller`` when you supplied the dict, plus the ``bbox`` and
            ``counts`` from that payload. A ``caller`` plan is only as
            current as the snapshot behind it, and this tool cannot date
            it: after placing anything, re-read before re-planning or
            the next plan routes through copper it cannot see.
        """
        geom, geom_source = await _resolve_geometry(geometry, fetch_geometry)
        if geom is None:
            return {"ok": False,
                    "reason": "no geometry: pass the geometry dict or set "
                              "fetch_geometry=True"}
        if nets is not None:
            if (not isinstance(nets, list)
                    or not all(isinstance(n, str) and n for n in nets)):
                return {"ok": False,
                        "reason": "nets must be a list of net names"}
        try:
            problem = RoutingProblem.from_geometry(
                geom, rules, net_classes=net_classes,
                grid_pitch_mils=grid_pitch_mils)
            options = RouterOptions(
                bend_penalty=float(bend_penalty),
                via_cost=float(via_cost),
                max_expansions=int(max_expansions))
        except (ValueError, TypeError) as exc:
            return {"ok": False, "reason": str(exc)}

        unknown: list[str] = []
        if nets is not None:
            wanted = set(nets)
            unknown = sorted(wanted - set(problem.terminals))
            problem.terminals = {
                n: t for n, t in problem.terminals.items() if n in wanted
            }
        result = route_problem(problem, options)
        result["geometry"] = _planned_against(geom, geom_source)
        if nets is not None:
            result["requested_nets"] = sorted(set(nets))
            result["unknown_nets"] = unknown
        return result

    @mcp.tool()
    async def route_plan_repairs(
        violations: Any,
        max_rounds: int = 5,
    ) -> dict[str, Any]:
        """Turn a DRC violation payload into an ordered repair plan.

        Pure Python, stateless. Classifies the ``pcb_run_drc`` payload
        (or a bare violation list) into buckets (net_clearance /
        pad_clearance / unrouted / antenna / width / other), then plans
        actions: ``rip_and_reroute`` (worst clearance offender first),
        ``nudge`` {net, dx, dy, x_mils, y_mils} for a lone
        pad-clearance conflict, ``widen``/``narrow`` {net} for width
        violations, ``escalate`` {reason} when the plan cannot converge
        alone. Deltas/coordinates are integer mils.

        Executor contract: apply the actions in order
        (``rip_and_reroute`` = ``pcb_delete_net`` + route that net
        again via ``route_plan`` with the ``nets`` filter or a DSN
        round-trip; ``nudge`` = ``obj_modify`` on the primitive nearest
        (x_mils, y_mils); ``widen``/``narrow`` =
        ``pcb_set_track_width``; ``escalate`` = stop and surface the
        reason). Then ``pcb_run_drc`` again and re-plan from the fresh
        violations -- the loop's outer iteration bound is the caller's.

        Args:
            violations: ``pcb_run_drc`` result ``{violation_count,
                violations}`` or a bare list of violation dicts.
            max_rounds: Rip budget for the clearance worst-offender
                loop, integer >= 0 (0 = escalate-only for clearance).

        Returns:
            ``{"ok": True, "actions": [...], "rounds_used": n,
            "ripped_nets": [...], "counts": {bucket: n}}``;
            ``{"ok": False, "reason": ...}`` on malformed input.
        """
        return plan_drc_repairs(violations, max_rounds=max_rounds)
