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


#: Items per bridge call when applying routes: one PreProcess and one
#: broadcast per call, and a request small enough to pass comfortably.
APPLY_CHUNK = 300


def _fmt(v: Any) -> str:
    x = float(v)
    return str(int(x)) if x.is_integer() else f"{x:.4f}".rstrip("0").rstrip(".")


def register_route_tools(mcp):
    """Register routing tools with the MCP server."""

    @mcp.tool()
    async def pcb_autoroute(
        expect_file: str,
        nets: Optional[list[str]] = None,
        use_planes: bool = True,
        diff_pairs: bool = True,
        plane_fanout: bool = False,
        pin_lanes: bool = True,
        bus_lanes: bool = True,
        gloss: bool = True,
        axis_entry: bool = False,
        neck_pads: bool = False,
        join_existing: bool = False,
        exclude_layers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Route the focused board with the in-house engine, as a job.

        Reads the board exactly, routes in the background and returns a job
        id at once. Poll design_job_status; design_job_result gives the
        tracks and vias, the DRC verdict and the pours used as planes.
        Nothing is written until pcb_autoroute_apply. Stages, each fixed
        for the next: differential pairs, plane fanout, IC pin lanes,
        buses, then the rest by priority. Bends are 45 degrees or less and
        no via goes in a passive's pad.

        Args:
            expect_file: Full path of the board; refused if another board
                is focused.
            nets: Route only these nets (default: every net with 2+ pads).
            use_planes: Use inner layers a pour covers as planes (default
                True).
            diff_pairs: Route differential pairs (defined, else _P/_N,
                +/-, DP/DM names) first, coupled (default True).
            plane_fanout: Dog-bone via for every plane-net surface pad
                first (default False; it cost completion).
            pin_lanes: Keep 0.8 mm straight out of IC pins at 0.65 mm pitch
                or finer (default True).
            bus_lanes: Route 3+ nets between the same two parts as nested
                lanes (default True).
            gloss: Lay the routed tracks again as the shortest clean 45/90
                runs, pad entries straight along a pad axis (default
                True); the board's own tracks are left as they are.
            axis_entry: Reach surface pads only straight out of their centre
                along an axis, turning clear of the pad; an IC's long pin
                only through its ends.
            neck_pads: Route a net at its rule width, narrowing only on the
                stub into a pad narrower than the track.
            join_existing: Route only what the board's copper leaves apart;
                a net it joins whole is left alone.
            exclude_layers: Copper layers no route may use (not one a
                net's surface pad is on).

        Returns:
            {"job_id", "board", "read": {pads, components, nets, layers}}
            or {"error"}. The job result adds ``via_in_pad`` (for fab
            notes), ``pairs`` (lengths, skew), ``buses`` and ``unreached``
            (pads left unrouted and what blocks them).
        """
        import asyncio

        from ..design.jobs import get_job_store
        from ..layout.read_altium import WrongBoard, read_live_board
        from ..layout.route.job import route_job

        try:
            board = await asyncio.to_thread(read_live_board, expect_file)
        except WrongBoard as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"error": f"board read failed: {exc}"}
        stages = {"diff_pairs": bool(diff_pairs), "plane_fanout": bool(plane_fanout),
                  "pin_lanes": bool(pin_lanes), "bus_lanes": bool(bus_lanes)}
        # Only what was asked for: the rest keep the router's own defaults.
        extra = {k: v for k, v in (("axis_entry", bool(axis_entry)),
                                   ("neck_pads", bool(neck_pads)),
                                   ("join_existing", bool(join_existing)),
                                   ("exclude_layers", tuple(exclude_layers or ()))) if v}
        # All four off is the router before the stages (StageOptions.coerce
        # False), not the stages' other defaults with four switches flipped.
        if not any(stages.values()) and not any(extra.values()):
            stages = False
        else:
            stages.update(extra)
        bad = set(extra.get("exclude_layers", ())) - set(board.copper_layers())
        if bad:
            return {"error": f"exclude_layers names no copper layer: {sorted(bad)}",
                    "layers": board.copper_layers()}
        padded = {c.layer for p in board.pads if p.net and p.is_smd for c in p.copper}
        if set(extra.get("exclude_layers", ())) & padded:
            return {"error": "exclude_layers takes a layer no net's surface pad is on: "
                             f"{sorted(set(extra['exclude_layers']) & padded)} carry some"}
        job_id = get_job_store().submit(
            "layout_route", route_job,
            {"board": board, "nets": list(nets) if nets else None, "planes": use_planes,
             "stages": stages, "gloss": bool(gloss)})
        return {
            "job_id": job_id,
            "board": board.name,
            "read": {"pads": len(board.pads), "components": len(board.components),
                     "nets": len(board.nets()), "layers": board.copper_layers()},
            "next_step": "call design_job_status(job_id, wait_seconds=90) "
                         "until it is done (nothing notifies you when it "
                         "finishes, so keep calling it; do not end your "
                         "turn), then design_job_result; apply with "
                         "pcb_autoroute_apply",
        }

    @mcp.tool()
    async def pcb_autoroute_apply(
        job_id: str,
        expect_file: str,
        checkpoint: bool = True,
        repour: bool = True,
    ) -> dict[str, Any]:
        """Write a finished ``pcb_autoroute`` job's tracks and vias to the board.

        Refuses unless the focused board is ``expect_file``. With
        ``checkpoint`` (default), first snapshots the board's folder as it
        is ON DISK, so it can be restored with ``app_restore_checkpoint``.
        Nothing is saved first: unsaved edits in the editor are not in the
        checkpoint, so save the board yourself beforehand if it has any.
        Coordinates, widths and via sizes keep their decimals.

        Args:
            job_id: a ``layout_route`` job that has finished.
            expect_file: full path of the board the job routed.
            checkpoint: take a checkpoint first (default True).
            repour: repour the board's polygons afterwards when the job
                relied on them, as planes or as pours joining a net, or
                routed through their old poured copper (default True). A
                poured net's connections are not there, and the new copper
                is not cleared, until the polygons are repoured round it.

        Returns:
            ``{"tracks": {placed, failed}, "vias": {placed, failed},
            "checkpoint": ..., "repoured": bool, "via_in_pad": [...],
            "notes": [...]}`` or ``{"error": ...}``. ``via_in_pad`` lists the
            vias written into pads (BGA balls, exposed pads), which the
            fabrication notes must ask to be filled and capped.
        """
        from pathlib import Path

        from ..design.jobs import get_job_store
        from ..layout.read_altium import _same_file

        store = get_job_store()
        rec = store.get(job_id)
        if rec is None or rec.kind != "layout_route":
            return {"error": f"no layout_route job {job_id!r}"}
        if rec.status != "done":
            return {"error": f"job {job_id} is {rec.status}, not done", "job": rec.summary()}
        result = rec.result or {}
        bridge = get_bridge()
        head = await bridge.send_command_async("pcb.get_layout_model", {"section": "board"},
                                                timeout=120.0)
        focused = str((head or {}).get("file", ""))
        if not _same_file(focused, expect_file):
            return {"error": f"the focused board is {focused or '(none)'}, not {expect_file}; "
                             "nothing was written"}
        out: dict[str, Any] = {"notes": list(result.get("notes") or [])}
        if checkpoint:
            from ..checkpoint import CheckpointStore
            from ..config import get_config
            cp = CheckpointStore(get_config().workspace_dir / "checkpoints")
            info = cp.create(Path(expect_file).parent, label=f"before pcb_autoroute_apply {job_id}")
            out["checkpoint"] = info.summary()
            out["notes"].append("The checkpoint holds the board folder as it was on disk; "
                                "unsaved editor changes are not in it.")
        placed = {"tracks": {"placed": 0, "failed": 0}, "vias": {"placed": 0, "failed": 0}}
        tracks = result.get("tracks") or []
        for i in range(0, len(tracks), APPLY_CHUNK):
            parts = [",".join([_fmt(t["x1"]), _fmt(t["y1"]), _fmt(t["x2"]), _fmt(t["y2"]),
                               _fmt(t["width"]), str(t["layer"]), str(t.get("net_name", ""))])
                     for t in tracks[i:i + APPLY_CHUNK]]
            r = await bridge.send_command_async("pcb.place_tracks", {"tracks": "|".join(parts)},
                                                timeout=120.0)
            placed["tracks"]["placed"] += int((r or {}).get("placed", 0))
            placed["tracks"]["failed"] += int((r or {}).get("failed", 0))
        vias = result.get("vias") or []
        for i in range(0, len(vias), APPLY_CHUNK):
            parts = [",".join([_fmt(v["x"]), _fmt(v["y"]), _fmt(v["size"]), _fmt(v["hole_size"]),
                               str(v["low_layer"]), str(v["high_layer"]), str(v.get("net", ""))])
                     for v in vias[i:i + APPLY_CHUNK]]
            r = await bridge.send_command_async("pcb.place_vias", {"vias": "|".join(parts)},
                                                timeout=120.0)
            placed["vias"]["placed"] += int((r or {}).get("placed", 0))
            placed["vias"]["failed"] += int((r or {}).get("failed", 0))
        out.update(placed)
        out["expected"] = {"tracks": len(tracks), "vias": len(vias)}
        summary = result.get("summary") or {}
        relied = bool(summary.get("pours") or summary.get("planes")
                      or summary.get("old_pours"))
        out["repoured"] = False
        if repour and relied:
            await bridge.send_command_async("pcb.repour_polygons", {}, timeout=600.0)
            out["repoured"] = True
        # The vias written into pads (BGA balls, exposed pads): the fab
        # notes must ask for each to be filled and capped.
        out["via_in_pad"] = list(result.get("via_in_pad") or [])
        out["notes"].append("Run pcb_run_drc to check the board with Altium's own rules.")
        # The live view: the board this job routed, now written to Altium.
        from ..design import live
        pub = live.publish_applied(
            result.get("live_version"),
            f"Applied to Altium: {placed['tracks']['placed']} of {len(tracks)} tracks, "
            f"{placed['vias']['placed']} of {len(vias)} vias"
            + ("; pours rebuilt." if out["repoured"] else "."))
        out["live_view"] = ({"version": pub["version"]} if pub else
                            "not updated: this server no longer holds the job's board")
        return out

    @mcp.tool()
    async def pcb_autoplace(
        expect_file: str,
        parts: Optional[list[str]] = None,
        compact: bool = False,
        strategy: Optional[str] = None,
    ) -> dict[str, Any]:
        """Place the focused board's parts with the in-house placer, as a job.

        Returns a job id at once; poll ``design_job_status``, and
        ``design_job_result`` gives the moves, wirelength before and after,
        overlaps and the DRC verdict. Nothing moves until
        ``pcb_autoplace_apply``. Parts keep their side; locked parts and
        mechanical ones (connectors, mounting holes, fiducials, test
        points, switches, batteries) stay unless named in ``parts``.
        Routing does not follow its parts: place first, or ``pcb_unroute``.

        Args:
            expect_file: Full path of the board; refused if another is
                focused.
            parts: Designators to move (default: all movable parts).
            compact: Use only the room the parts need (small circuit on a
                big board; analytic only).
            strategy: "blocks" (default: by sub-circuit, decoupling round
                each IC, repeated channels tiled, signal-flow order) or
                "analytic" (wirelength only). "blocks" falls back to
                "analytic" when its floorplan would push over a quarter of
                the parts (``fallback``).

        Returns:
            {"job_id", "board", "strategy", "read"} or {"error"}.
        """
        import asyncio

        from ..design.jobs import get_job_store
        from ..layout.place.placer import DEFAULT_STRATEGY, STRATEGIES
        from ..layout.read_altium import WrongBoard, read_live_board

        strategy = strategy or DEFAULT_STRATEGY
        if strategy not in STRATEGIES:
            return {"error": f"unknown strategy {strategy!r}; one of {', '.join(STRATEGIES)}"}
        try:
            board = await asyncio.to_thread(read_live_board, expect_file)
        except WrongBoard as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"error": f"board read failed: {exc}"}
        if parts:
            known = {c.ref for c in board.components}
            unknown = [p for p in parts if p not in known]
            if unknown:
                return {"error": f"no such parts on the board: {', '.join(unknown)}"}
        from ..layout.place.job import place_job
        from ..layout.place.placer import SPREAD_DENSITY
        job_id = get_job_store().submit(
            "layout_place", place_job, {"board": board, "parts": list(parts) if parts else None,
                                        "spread_density": SPREAD_DENSITY if compact else 0.0,
                                        "strategy": strategy})
        return {
            "job_id": job_id,
            "board": board.name,
            "strategy": strategy,
            "read": {"pads": len(board.pads), "components": len(board.components),
                     "nets": len(board.nets()), "layers": board.copper_layers()},
            "next_step": "poll design_job_status, then design_job_result; "
                         "apply with pcb_autoplace_apply",
        }

    @mcp.tool()
    async def pcb_autoplace_apply(
        job_id: str,
        expect_file: str,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        """Move the parts of a finished ``pcb_autoplace`` job on the board.

        Refuses unless the focused board is ``expect_file``. With
        ``checkpoint`` (default), first snapshots the board's folder as it is
        ON DISK, so it can be restored with ``app_restore_checkpoint``;
        unsaved edits in the editor are not in it, so save first if there
        are any. Positions are whole mils, as the job reported and judged
        them.

        Args:
            job_id: a ``layout_place`` job that has finished.
            expect_file: full path of the board the job placed.
            checkpoint: take a checkpoint first (default True).

        Returns:
            ``{"moved", "failed", "expected", "checkpoint", "notes"}`` or
            ``{"error": ...}``.
        """
        from pathlib import Path

        from ..design.jobs import get_job_store
        from ..layout.read_altium import _same_file

        store = get_job_store()
        rec = store.get(job_id)
        if rec is None or rec.kind != "layout_place":
            return {"error": f"no layout_place job {job_id!r}"}
        if rec.status != "done":
            return {"error": f"job {job_id} is {rec.status}, not done", "job": rec.summary()}
        result = rec.result or {}
        bridge = get_bridge()
        head = await bridge.send_command_async("pcb.get_layout_model", {"section": "board"},
                                                timeout=120.0)
        focused = str((head or {}).get("file", ""))
        if not _same_file(focused, expect_file):
            return {"error": f"the focused board is {focused or '(none)'}, not {expect_file}; "
                             "nothing was moved"}
        out: dict[str, Any] = {"notes": list(result.get("notes") or [])}
        if checkpoint:
            from ..checkpoint import CheckpointStore
            from ..config import get_config
            cp = CheckpointStore(get_config().workspace_dir / "checkpoints")
            info = cp.create(Path(expect_file).parent, label=f"before pcb_autoplace_apply {job_id}")
            out["checkpoint"] = info.summary()
            out["notes"].append("The checkpoint holds the board folder as it was on disk; "
                                "unsaved editor changes are not in it.")
        moves = result.get("moves") or []
        moved = failed = 0
        for i in range(0, len(moves), APPLY_CHUNK):
            ops = []
            for m in moves[i:i + APPLY_CHUNK]:
                d = str(m["designator"])
                if "," in d or "|" in d:
                    failed += 1
                    continue
                ops.append(f"{d},{int(m['x'])},{int(m['y'])},{_fmt(m['rotation'])}")
            if not ops:
                continue
            r = await bridge.send_command_async("pcb.batch_move_components",
                                                {"moves": "|".join(ops)}, timeout=120.0)
            r = r or {}
            moved += int(r.get("moves_applied", 0))
            failed += int(r.get("failed", 0))
        out.update(moved=moved, failed=failed, expected=len(moves))
        out["notes"].append("Then pcb_autoroute to route the placed board.")
        # The live view: the board this job placed, now written to Altium.
        from ..design import live
        pub = live.publish_applied(result.get("live_version"),
                                   f"Applied to Altium: {moved} of {len(moves)} moves"
                                   + (f", {failed} failed." if failed else "."))
        out["live_view"] = ({"version": pub["version"]} if pub else
                            "not updated: this server no longer holds the job's board")
        return out

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

        Pure Python unless ``fetch_geometry``. ``tracks`` come in the
        pcb_place_tracks shape and ``vias`` in the pcb_place_via shape,
        integer mils, ready to apply. A net that fails is reported, not
        raised. Loop: plan, place, DRC, route_plan_repairs, repeat.

        Args:
            geometry: Board geometry payload (bbox, outline, pads, tracks,
                vias, mils). Unrouted nets' copper stays an obstacle.
            rules: clearance_mils, track_width_mils (int or per-class dict
                with "default"), via_size_mils, via_drill_mils, layers
                (default top and bottom).
            nets: Route only these (unknown ones in ``unknown_nets``).
            net_classes: net -> class (power, ground, differential...),
                setting order and width; others are "signal".
            grid_pitch_mils: Grid pitch (default 25).
            bend_penalty: Corner cost, in grid pitches.
            via_cost: Layer-change cost, in grid pitches.
            max_expansions: Search budget per connection.
            fetch_geometry: Read the live board when geometry is None.

        Returns:
            {"ok", "summary" (routed, failed, completion, counts,
            length), "order", "nets" {net: {status, class, width, ...}},
            "tracks", "vias", "validation", "geometry" (source "live" or
            "caller", bbox, counts)}. A caller-supplied geometry is only
            as fresh as its snapshot: re-read after placing anything.
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
        if problem.off_grid_terminals:
            result["off_grid_terminals"] = problem.off_grid_terminals
            result["grid_hint"] = (
                f"{problem.off_grid_terminals} blocked pad(s) have no "
                f"{grid_pitch_mils} mil grid point on their copper; a "
                "grid_pitch_mils under half the pad width can reach them")
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

        Pure Python. Apply the actions in order, then run
        ``pcb_run_drc`` and plan again:
          ``rip_and_reroute`` {net}: ``pcb_delete_net``, then route it
            again (``route_plan`` with ``nets``); worst offender first.
          ``nudge`` {net, dx, dy, x_mils, y_mils}: ``obj_modify`` on the
            primitive nearest (x_mils, y_mils).
          ``widen`` / ``narrow`` {net}: ``pcb_set_track_width``.
          ``escalate`` {reason}: stop and report it.
        Coordinates and deltas are integer mils.

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
