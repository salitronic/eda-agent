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


#: How many of the loops the board shows a brief read lists.
LOOPS_LISTED = 12

#: A routing job stops here and keeps what it has made (job_control).
DEFAULT_TIME_LIMIT_S = 1800.0


def _loops_found(board, brief) -> list[dict[str, Any]]:
    """The current loops the board's parts and nets show (design/loops.py),
    worst first, with their size as placed: the ones the brief does not name
    yet, as members the AI can give a loop entry."""
    from ..layout.loop_audit import analyze_loops

    named = {frozenset(lp.members) for lp in brief.loops}
    out = []
    try:
        found = analyze_loops(board)
    except Exception as exc:  # noqa: BLE001 - a read still answers without them
        return [{"error": f"loops not found: {exc}"}]
    for m in found:
        if frozenset(m["members"]) in named:
            continue
        size = {k: m[k] for k in ("area_sq_mils", "routed_area_sq_mils", "length_mils",
                                  "routed_length_mils") if k in m}
        out.append({"kind": m["kind"], "members": m["members"], "nets": m["nets"], **size})
        if len(out) >= LOOPS_LISTED:
            break
    return out


def _discipline() -> tuple[str, dict[str, Any]]:
    """The routing discipline the user chose on the Config tab, and what a
    reply says about it when it is not enforced. Read on every call, so a
    change on the tab counts from the next one."""
    from ..preferences import ROUTING, routing_discipline

    level = routing_discipline()
    if level == "enforced":
        return level, {}
    return level, {"routing_discipline": level, "discipline_note": (
        f"Routing discipline is {level} on the Config tab, which only the user "
        f"sets: {ROUTING[level]}. Tell the user what was not asked for; never "
        "change the setting or ask the router for less to match it.")}


def _unbriefed(needed: list[dict[str, Any]], level: str) -> dict[str, Any]:
    """The critical nets a reply names as lacking a brief entry: held back
    when enforced, routed and named when advisory, not named when off."""
    if not needed or level == "off":
        return {}
    return {"brief_needed" if level == "enforced" else "routed_without_brief": needed}


def _routable(board) -> list[str]:
    """The nets the router would join: two pads or more."""
    return sorted(n for n, pads in board.pads_by_net().items() if n and len(pads) >= 2)


def _release(brief, level: str):
    """The inner layers the router may give from plane to routing: those an
    entry of the brief names in its layers (None under the off discipline:
    any it needs)."""
    if level == "off":
        return None
    return sorted({l for e in brief.of.values() if e.layers for l in e.layers})


def _undecided(found: list[dict[str, str]], level: str) -> dict[str, Any]:
    """The nets a reply names as not decided by the AI yet: held back when
    enforced, routed and named when advisory, not named when off."""
    from ..layout.route.brief import UNDECIDED_LISTED

    if not found or level == "off":
        return {}
    key = "decision_needed" if level == "enforced" else "routed_undecided"
    return {key: found[:UNDECIDED_LISTED], f"{key}_count": len(found)}


#: What a reply holding back undecided nets says to do.
def _with_partners(board, nets) -> tuple[list, list]:
    """``nets`` with the other net of every differential pair one of them
    is in, and the nets added: half a pair is routed as a pair or not at
    all."""
    from ..layout.route.diffpair import find_pairs

    partner = {}
    for _name, pos, neg, _src in find_pairs(board):
        partner[pos], partner[neg] = neg, pos
    asked = list(nets)
    added = sorted({partner[n] for n in asked if n in partner} - set(asked))
    return asked + added, added


DECIDE_FIRST = ("decide these nets first in pcb_route_brief: an entry with their layers and "
                "the reason (one entry can decide a group of plain nets), with the route you "
                "want in mind; then, in a later call, confirm what you verified on the board "
                "for each net against the to_confirm facts it gives, how sure you are it is "
                "the best choice (confidence 80 or more) and the alternative you weighed; the "
                "router then lays what you decided")


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
        neck_pads: bool = True,
        join_existing: bool = False,
        exclude_layers: Optional[list[str]] = None,
        time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    ) -> dict[str, Any]:
        """Route many nets in one run; to route a board use pcb_route_round.

        Rounds (pcb_route_round) route a group at a time, stop for your
        review and advise on nets left unrouted. This runs as a job.

        Returns a job id at once. Poll design_job_status; design_job_result
        gives the tracks and vias, the DRC verdict, the pours used as planes
        and the same ``review`` a round gives (per-net figures, a picture,
        ``questions``). Nothing is written until pcb_autoroute_apply, which
        refuses while a finding is unanswered. Stages, each fixed
        for the next: differential pairs, plane fanout, IC pin lanes,
        buses, then the rest by priority. Bends are 45 degrees or less and
        no via goes in a passive's pad. Lays only what pcb_route_brief
        decides: a net with no entry giving layers and reason is left out
        (decision_needed), a critical one without a source too
        (brief_needed). The user's Routing discipline (Config tab) can
        relax that.

        Args:
            expect_file: Full path of the board; refused if another board
                is focused.
            nets: Route only these nets (default: every net with 2+ pads).
            use_planes: Inner layers a pour covers are planes (default
                True); routed on only if a brief entry names the layer
                (else summary.plane_declined).
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
                True).
            axis_entry: Reach surface pads only straight out of their centre
                along an axis, turning clear of the pad; an IC's long pin
                only through its ends.
            neck_pads: A net with a wider width rule than most keeps it,
                necking into a narrower pad (default True).
            join_existing: Route only what the board's copper leaves apart;
                a net it joins whole is left alone.
            exclude_layers: Copper layers no route may use (not one a
                net's surface pad is on).
            time_limit_s: Stop then, keeping what is made; 0: none.

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
                                   ("join_existing", bool(join_existing)),
                                   ("exclude_layers", tuple(exclude_layers or ()))) if v}
        asked = any(stages.values()) or any(extra.values())
        if not neck_pads:
            extra["neck_pads"] = False
        # All four off is the router before the stages (StageOptions.coerce
        # False), not the stages' other defaults with four switches flipped.
        if not asked:
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
        # The routing brief is obeyed; a critical net without one is left out.
        from ..layout.route import brief as brief_mod
        brief = brief_mod.load(expect_file)
        chk = brief_mod.check(board, brief, nets)
        if chk["errors"]:
            return {"error": "the routing brief cannot be obeyed as written",
                    "brief_errors": chk["errors"], "fix": "pcb_route_brief"}
        level, said = _discipline()
        unbriefed = _unbriefed(chk["needed"], level)
        asked_for = [n for n in (nets or _routable(board))]
        open_ = brief_mod.undecided(brief, asked_for, skip=[x["net"] for x in chk["needed"]])
        undecided = _undecided(open_, level)
        left_out = ({x["net"] for x in chk["needed"]} | {x["net"] for x in open_}
                    if level == "enforced" else set())
        if left_out:
            nets = [n for n in (nets or board.nets()) if n not in left_out]
            if not nets:
                return {"error": "the router lays only what you decided, and no net asked "
                                 "for is decided yet; nothing was routed",
                        **unbriefed, **undecided,
                        "next_step": DECIDE_FIRST}
        job_id = get_job_store().submit(
            "layout_route", route_job,
            {"board": board, "nets": list(nets) if nets else None, "planes": use_planes,
             "stages": stages, "gloss": bool(gloss), "brief": brief,
             "release": _release(brief, level),
             # Reviewed like a round: the findings wait on an answer before
             # pcb_autoroute_apply writes. Advice on unrouted nets is a round's.
             "review": True, "advise": False},
            time_limit_s=time_limit_s)
        return {
            "job_id": job_id,
            "board": board.name,
            **unbriefed, **undecided, **said,
            "read": {"pads": len(board.pads), "components": len(board.components),
                     "nets": len(board.nets()), "layers": board.copper_layers()},
            "next_step": "call design_job_status(job_id, wait_seconds=90) "
                         "until it is done (nothing notifies you when it "
                         "finishes, so keep calling it; do not end your "
                         "turn), then design_job_result: look at "
                         "review.picture, answer review.questions, then "
                         "pcb_autoroute_apply. For the nets it leaves "
                         "unrouted, and for the rest of the board, route in "
                         "rounds with pcb_route_round",
        }

    @mcp.tool()
    async def pcb_route_brief(
        expect_file: str,
        add: Optional[list[dict[str, Any]]] = None,
        drop: Optional[list[dict[str, Any]]] = None,
        remove: Optional[list[str]] = None,
        replace: Optional[list[dict[str, Any]]] = None,
        areas: Optional[list[dict[str, Any]]] = None,
        remove_areas: Optional[list[str]] = None,
        loops: Optional[list[dict[str, Any]]] = None,
        remove_loops: Optional[list[str]] = None,
        barriers: Optional[list[dict[str, Any]]] = None,
        remove_barriers: Optional[list[str]] = None,
        assembly: Optional[dict[str, Any]] = None,
        orient: Optional[list[dict[str, Any]]] = None,
        remove_orient: Optional[list[str]] = None,
        pdn: Optional[list[dict[str, Any]]] = None,
        remove_pdn: Optional[list[str]] = None,
        thermal: Optional[list[dict[str, Any]]] = None,
        remove_thermal: Optional[list[str]] = None,
        confirm: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Set, change or read the board's routing brief: how the nets that
        matter are to be routed, which the router obeys.

        You decide; the router lays only that: a net is routed once an
        entry gives its layers and reason (one may decide a group) and a
        later call confirms it: confirm [{nets, verified, confidence,
        alternative}], what you checked per net (name it and its parts; a
        big plain group: its check_first net) against the reply's
        to_confirm facts, how sure you are it is the best choice (0-100,
        80 or more to route) and the other choice you weighed. A critical
        net (the reply names them) needs a sourced entry: read the
        datasheet's layout section and the standards first.

        Entry: nets, kind, layers, width (mils, not under the width rule),
        region / keep_out (polygons), max_vias, min_ / max_length, match +
        skew, reference (plane net under every track), return_via (mils: a
        reference via that near each via of the net), impedance or
        diff_impedance + gap, impedance_tol (%), apart_from (nets, classes)
        + spacing, max_parallel + parallel_gap, max_broadside; guard,
        guard_gap, max_unguarded; terminate, terminate_at (end|source),
        driver, max_stub; current (A) + temp_rise (C, IPC-2221); source,
        reason. An impedance or current gets each layer's width.

        Areas bar all nets but those allowed: name, polygons, kind, layers,
        allow, source (an antenna feed needs one). Loops: name, members
        (pads in current order, "C5.1"), kind, max_area (sq mils) or
        max_length, source; a read lists ``loops_found``. Barriers: name,
        nets, apart_from, creepage / clearance, source. Assembly:
        via_to_pad, vias_under_parts, part_gap, source. Orient: name,
        parts, rotation, side, pins ({"D1.K": "VIN"}), source. Pdn: name,
        part, rails, decaps, max_distance, min_vias + via_reach, source.
        Thermal (a power part): name, part, pad (default its largest),
        min_area (sq mils of its net's copper there), min_vias +
        via_reach, power (W) + theta_ja (C/W) + ambient + tj_max (C),
        source (its datasheet).

        Args:
            expect_file: full path of the focused board.
            add: entries to add (a net in another entry moves here).
            drop: [{nets, rules}] rules to take out of those nets' entries.
            remove: nets to take out of the brief.
            replace: the whole brief (an empty list clears it).
            areas, loops, barriers, orient, pdn, thermal: to add; one of the
                same name is replaced. remove_*: names to take out.
            assembly: sets the assembly rule ({} clears it).

        Returns:
            ``brief``, the other rules by name, ``errors`` (nothing is saved
            then), ``needed`` (critical nets with no sourced entry, why,
            what to read), ``undecided`` (and its count), ``to_confirm``,
            ``rounds``, ``file``.
        """
        import asyncio
        from dataclasses import replace as _replace

        from ..layout.read_altium import WrongBoard, read_live_board
        from ..layout.route import brief as brief_mod
        from ..layout.route import review

        try:
            board = await asyncio.to_thread(read_live_board, expect_file)
        except WrongBoard as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"error": f"board read failed: {exc}"}
        b = brief_mod.load(expect_file)
        changed = any(v is not None for v in (add, drop, remove, replace, areas, remove_areas,
                                              loops, remove_loops, barriers,
                                              remove_barriers, assembly, orient,
                                              remove_orient, pdn, remove_pdn, thermal,
                                              remove_thermal, confirm))

        def fresh(d: dict[str, Any]):
            # What was verified is given only by confirm, in a later call.
            return _replace(brief_mod.NetRules.from_dict(d), verified="", verified_for="",
                            confidence=None, alternative="")

        try:
            entries = list(b.entries) if replace is None else [fresh(d) for d in replace]
            if remove:
                gone = set(remove)
                entries = [_replace(e, nets=tuple(n for n in e.nets if n not in gone))
                           for e in entries]
            for d in drop or []:
                nets_d = set(d.get("nets") or [])
                empty = {"layers": None, "width": None, "region": None, "keep_out": [],
                         "max_vias": None, "min_length": None, "max_length": None,
                         "match": "", "skew": None, "reference": "", "impedance": None,
                         "diff_impedance": None, "gap": None, "impedance_tol": None,
                         "apart_from": (), "spacing": None, "max_parallel": None,
                         "parallel_gap": None, "max_broadside": None, "guard": "",
                         "guard_gap": None, "max_unguarded": None, "terminate": "",
                         "terminate_at": "", "driver": "", "max_stub": None,
                         "current": None, "temp_rise": None, "return_via": None}
                bad = [r for r in d.get("rules") or [] if r not in empty]
                if bad:
                    return {"error": f"no rule {bad}; rules are {sorted(empty)}"}
                out = []
                for e in entries:
                    hit = tuple(n for n in e.nets if n in nets_d)
                    if not hit:
                        out.append(e)
                        continue
                    rest = tuple(n for n in e.nets if n not in nets_d)
                    if rest:
                        out.append(_replace(e, nets=rest))
                    out.append(_replace(e, nets=hit, **{r: empty[r] for r in d["rules"]}))
                entries = out
            for d in add or []:
                new = fresh(d)
                entries = [_replace(e, nets=tuple(n for n in e.nets if n not in new.nets))
                           for e in entries] + [new]
            confirm_errors: list[str] = []
            just_set = {n for d in (add or []) + (replace or [])
                        for n in fresh(d).nets}
            for c in confirm or []:
                nets_c = set(c.get("nets") or [])
                at = [i for i, e in enumerate(entries) if e.nets and set(e.nets) == nets_c]
                if not at:
                    confirm_errors.append(f"confirm {sorted(nets_c)[:3]}: no entry rules exactly "
                                          "these nets; confirm an entry by all its nets")
                elif nets_c & just_set:
                    confirm_errors.append(f"confirm {sorted(nets_c)[:3]}: the entry is set in "
                                          "this call; read its facts in the reply, check the "
                                          "decision against them, then confirm in the next call")
                else:
                    e = entries[at[0]]
                    errs = brief_mod.confirm_errors(board, e, str(c.get("verified") or ""),
                                                    c.get("confidence"),
                                                    str(c.get("alternative") or ""))
                    confirm_errors += errs
                    if not errs:
                        entries[at[0]] = _replace(e, verified=str(c["verified"]).strip(),
                                                  verified_for=e.decision_key(),
                                                  confidence=float(c["confidence"]),
                                                  alternative=str(c["alternative"]).strip())
            kept = [a for a in b.areas if a.name not in set(remove_areas or [])]
            for d in areas or []:
                new_a = brief_mod.AreaRule.from_dict(d)
                kept = [a for a in kept if not (new_a.name and a.name == new_a.name)] + [new_a]
            kept_loops = [lp for lp in b.loops if lp.name not in set(remove_loops or [])]
            for d in loops or []:
                new_l = brief_mod.LoopRule.from_dict(d)
                kept_loops = [lp for lp in kept_loops
                              if not (new_l.name and lp.name == new_l.name)] + [new_l]
            kept_bars = [x for x in b.barriers if x.name not in set(remove_barriers or [])]
            for d in barriers or []:
                new_b = brief_mod.BarrierRule.from_dict(d)
                kept_bars = [x for x in kept_bars
                             if not (new_b.name and x.name == new_b.name)] + [new_b]
            kept_asm = b.assembly if assembly is None else                 brief_mod.AssemblyRule.from_dict(assembly)
            kept_or = [x for x in b.orient if x.name not in set(remove_orient or [])]
            for d in orient or []:
                new_o = brief_mod.OrientRule.from_dict(d)
                kept_or = [x for x in kept_or
                           if not (new_o.name and x.name == new_o.name)] + [new_o]
            kept_pdn = [x for x in b.pdn if x.name not in set(remove_pdn or [])]
            for d in pdn or []:
                new_p = brief_mod.PdnRule.from_dict(d)
                kept_pdn = [x for x in kept_pdn
                            if not (new_p.name and x.name == new_p.name)] + [new_p]
            kept_th = [x for x in b.thermal if x.name not in set(remove_thermal or [])]
            for d in thermal or []:
                new_t = brief_mod.ThermalRule.from_dict(d)
                kept_th = [x for x in kept_th
                           if not (new_t.name and x.name == new_t.name)] + [new_t]
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            return {"error": f"an entry is malformed: {exc}"}
        b2 = brief_mod.Brief((e for e in entries if e.nets), kept, kept_loops, kept_bars,
                             kept_asm, kept_or, kept_pdn, kept_th)
        b2 = brief_mod.board_layers(board, b2)
        chk = brief_mod.check(board, b2)
        chk["errors"] = chk["errors"] + confirm_errors
        res: dict[str, Any] = {"brief": b2.as_list(), "areas": b2.areas_list(),
                               "loops": b2.loops_list(), "barriers": b2.barriers_list(),
                               "assembly": b2.assembly_dict(), "orient": b2.orient_list(),
                               "pdn": b2.pdn_list(), "thermal": b2.thermal_list(),
                               "errors": chk["errors"], "needed": chk["needed"]}
        open_ = brief_mod.undecided(b2, _routable(board), skip=[x["net"] for x in chk["needed"]])
        res["undecided"] = open_[:brief_mod.UNDECIDED_LISTED]
        res["undecided_count"] = len(open_)
        # The board's facts for each decision still to confirm, to check it
        # against before confirm.
        todo, room = [], brief_mod.UNDECIDED_LISTED
        for e in b2.entries:
            if e.confirmed or not e.layers or room <= 0:
                continue
            item = {"nets": list(e.nets), "facts": brief_mod.facts(board, e.nets[:room])}
            if len(e.nets) > brief_mod.GROUP_CHECK:
                item["check_first"] = brief_mod.hardest(board, e.nets)
            todo.append(item)
            room -= len(e.nets)
        if todo:
            res["to_confirm"] = todo
        if not changed:
            res["loops_found"] = await asyncio.to_thread(_loops_found, board, b2)
            res["note"] = ("no changes given (add, replace, drop, remove, areas, confirm "
                           "...): this is the brief as saved")
        if chk["errors"]:
            res["saved"] = False
            res["note"] = "nothing was saved: the brief stays as it was"
            return res
        if changed:
            res["file"] = str(brief_mod.save(expect_file, b2))
        res["rounds"] = [{"kind": r["kind"], "nets": len(r["nets"]), "first": r["nets"][:12]}
                         for r in review.rounds(board, b2, chk["critical"])]
        return res

    @mcp.tool()
    async def pcb_route_round(
        expect_file: str,
        nets: Optional[list[str]] = None,
        advise: bool = True,
        use_planes: bool = True,
        time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    ) -> dict[str, Any]:
        """Route the next round of nets under the brief, then stop for review.

        The rounds go: differential pairs, switching nodes, high-current
        paths, clocks, the other nets the brief rules, then the rest. A
        round routes only what the board's copper leaves apart: earlier
        rounds and every route you laid by hand stay as they are. The
        router lays only what you decided: a net in the round no brief
        entry decides (its layers and the reason) stops the round before
        anything is routed (decision_needed), and so does a critical net
        without a sourced entry (brief_needed, with what to read).
        When the user has set Routing discipline to advisory or off on
        the Config tab, the round goes ahead and the reply says so.

        Runs as a job: poll design_job_status, then design_job_result. Its
        ``review`` holds per net the length, vias, layers and detour; a
        picture (PNG) to look at; ``questions`` to answer; and for each net
        left unrouted, ``advice``: options tried by routing them (a part
        moved clear, a net laid before ripped up, a brief rule dropped),
        each with what it gave and the tool call that applies it. Nets
        whose route would break the brief are held back, not laid.

        Look at the picture, answer the questions, then accept with
        pcb_autoroute_apply(job_id), or change the brief or the board and
        route the round again (same nets). Nothing is written before.

        Args:
            expect_file: full path of the focused board.
            nets: route these nets as the round (default: the next round);
                the other net of a pair named comes too.
            advise: try options for nets left unrouted (default True).
            use_planes: inner layers a pour covers are planes (default True);
                one goes to routing only if a brief entry names its layer
                (summary.plane_declined says what it would close).
            time_limit_s: stop at this many seconds and keep what is made
                (summary.stopped_at_time_limit); 0: no limit. Cancel with
                design_job_cancel.

        Returns:
            ``job_id``, ``round`` {kind, nets}, ``rounds_left``, or
            ``brief_needed`` (and no job), or ``done`` when no net is open.
        """
        import asyncio

        from ..design.jobs import get_job_store
        from ..layout.read_altium import WrongBoard, read_live_board
        from ..layout.route import brief as brief_mod
        from ..layout.route import review
        from ..layout.route.job import route_job

        try:
            board = await asyncio.to_thread(read_live_board, expect_file)
        except WrongBoard as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"error": f"board read failed: {exc}"}
        brief = brief_mod.load(expect_file)
        chk = brief_mod.check(board, brief)
        if chk["errors"]:
            return {"error": "the routing brief cannot be obeyed as written",
                    "brief_errors": chk["errors"], "fix": "pcb_route_brief"}
        open_ = await asyncio.to_thread(review.open_nets, board)
        plan = review.rounds(board, brief, chk["critical"], open_)
        partners: list = []
        if nets:
            unknown = sorted(set(nets) - set(board.nets()))
            if unknown:
                return {"error": f"no such nets on the board: {unknown[:10]}"}
            nets, partners = _with_partners(board, nets)
            todo = sorted(set(nets) & open_)
            kind = "asked"
        elif plan:
            todo, kind = plan[0]["nets"], plan[0]["kind"]
        else:
            todo, kind = [], ""
        if not todo:
            return {"done": True, "note": "the board's copper joins every net asked for"}
        level, said = _discipline()
        needed = [x for x in chk["needed"] if x["net"] in todo]
        if needed and level == "enforced":
            return {"round": {"kind": kind, "nets": todo}, "brief_needed": needed,
                    "next_step": "read what each names, give them entries with their "
                                 "source in pcb_route_brief, then call this again"}
        unbriefed = _unbriefed(needed, level)
        undecided = _undecided(brief_mod.undecided(brief, todo, skip=[x["net"] for x in needed]),
                               level)
        if level == "enforced" and undecided:
            return {"round": {"kind": kind, "nets": todo}, **undecided,
                    "next_step": DECIDE_FIRST + ", then call this again"}
        # Pads reached only along their axes, ending on their centres: a
        # round is reviewed, and an angled entry is a finding to answer.
        # Where that leaves a connection unmade the fallback routes it
        # without, and the review names the entries that came of it.
        stages = {"diff_pairs": True, "plane_fanout": False, "pin_lanes": True,
                  "bus_lanes": True, "join_existing": True, "axis_entry": True}
        job_id = get_job_store().submit(
            "layout_route", route_job,
            {"board": board, "nets": todo, "planes": use_planes, "stages": stages,
             "gloss": True, "brief": brief, "release": _release(brief, level),
             "review": True, "advise": bool(advise)},
            time_limit_s=time_limit_s)
        left = [{"kind": r["kind"], "nets": len(r["nets"])} for r in plan
                if not set(r["nets"]) <= set(todo)]
        return {
            "job_id": job_id, "board": board.name, "round": {"kind": kind, "nets": todo},
            **({"pair_partners_added": partners} if partners else {}),
            "rounds_left": left, **unbriefed, **undecided, **said,
            "next_step": "call design_job_status(job_id, wait_seconds=90) until done (keep "
                         "calling, do not end your turn), then design_job_result: look at "
                         "review.picture, answer review.questions, then pcb_autoroute_apply "
                         "to accept, or change the brief or board and route the round again",
        }

    @mcp.tool()
    async def pcb_route_review(
        expect_file: str,
        nets: Optional[list[str]] = None,
        picture: bool = True,
    ) -> dict[str, Any]:
        """Review the copper on the board against the routing brief, without
        routing anything: tracks you laid by hand, an earlier round, or a
        router job's result once applied.

        The same review a round gets, on the board as it is: each brief rule
        measured on the copper (length, skew, return plane and return vias,
        impedance, current against IPC-2221, spacing and parallel runs,
        guards, termination, loops, decoupling, thermal pads and junction
        temperature, barriers, areas, assembly), then the
        judgements (detour, vias, off-45 pieces, pad entries, staircases,
        short tracks). Use it to check your own routing before going on, and
        a finding's ``simulate`` call where a closed form cannot settle it.
        Reads only; nothing waits on an answer here.

        Args:
            expect_file: full path of the focused board.
            nets: the nets to review (default: every net with free copper).
            picture: draw the reviewed nets (PNG, default True).

        Returns:
            ``findings`` (each id, check, text, and measured / limit where it
            has them), ``nets`` (length, vias, layers, detour per net),
            ``open`` (nets in scope the copper does not join yet),
            ``picture`` and ``brief_errors`` when the brief cannot be read
            as written.
        """
        import asyncio

        from ..layout.read_altium import WrongBoard, read_live_board
        from ..layout.route import brief as brief_mod
        from ..layout.route import review

        try:
            board = await asyncio.to_thread(read_live_board, expect_file)
        except WrongBoard as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"error": f"board read failed: {exc}"}
        brief = brief_mod.load(expect_file)

        def run() -> dict[str, Any]:
            chk = brief_mod.check(board, brief)
            free = [t for t in board.tracks if t.net and not t.comp and not t.keepout]
            free_vias = [v for v in board.vias if v.net and not v.comp]
            scope = set(nets) if nets else (
                {t.net for t in free} | {v.net for v in free_vias}
                | {brief_mod.thermal_measure(board, r).get("net") for r in brief.thermal}) - {None, ""}
            unknown = sorted(scope - set(board.nets()))
            if unknown:
                return {"error": f"no such nets on the board: {unknown[:10]}"}
            tracks = [{"x1": t.x1, "y1": t.y1, "x2": t.x2, "y2": t.y2, "width": t.width,
                       "layer": t.layer, "net_name": t.net} for t in free if t.net in scope]
            vias = [{"x": v.x, "y": v.y, "size": v.diameter, "hole_size": v.hole,
                     "low_layer": v.low_layer, "high_layer": v.high_layer, "net": v.net}
                    for v in free_vias if v.net in scope]
            pads_of = board.pads_by_net()
            metrics = review.net_metrics(tracks, vias, pads_of)
            planes = {r.net for r in board.regions if r.net and r.kind == "pour"}
            found = review.findings(board, board, tracks, vias, brief, scope, metrics, {}, {},
                                    {}, planes=planes,
                                    entries=review.pad_entries(tracks, pads_of))
            out: dict[str, Any] = {
                "board": board.name, "reviewed": len(scope), "findings": found,
                "nets": {n: m for n, m in metrics.items() if n in scope},
                "open": sorted(scope & review.open_nets(board))}
            if chk["errors"]:
                out["brief_errors"] = chk["errors"]
            if picture and scope:
                import time as _time
                from pathlib import Path as _Path

                from ..config import get_config
                folder = _Path(get_config().workspace_dir) / "route_reviews"
                name = f"{_Path(board.name).stem}_{_time.strftime('%Y%m%d_%H%M%S')}.png"
                try:
                    out["picture"] = review.picture(board, tracks, vias, sorted(scope), brief,
                                                    [], folder / name)
                except Exception as exc:  # noqa: BLE001 - the picture is a help
                    out["picture"] = f"no picture: {exc}"
            return out

        return await asyncio.to_thread(run)

    @mcp.tool()
    async def pcb_autoroute_apply(
        job_id: str,
        expect_file: str,
        checkpoint: bool = True,
        repour: bool = True,
        accept: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Write a finished ``pcb_autoroute`` job's tracks and vias to the board.

        Refuses while the job has findings (its review's rules measured on
        the copper, and the judgements that are yours) you have not
        answered: route again, or accept each with ``accept``. An answer
        given before on this board to the same finding stands. With
        Routing discipline advisory on the Config tab (the user's choice)
        it writes and lists them in ``unanswered``; off, it writes.

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
            accept: answers to findings, each {id, reason}. {check,
                reason} answers every finding of a judgement check
                (detour, vias, angle, pad_entry) or of unrouted / held; a
                brief rule the copper breaks is answered by id. The
                reason is kept with the board's brief.

        Returns:
            ``{"tracks": {placed, failed}, "vias": {placed, failed},
            "checkpoint": ..., "repoured": bool, "via_in_pad": [...],
            "decisions": [...], "notes": [...]}``, ``{"error": ...,
            "unanswered": [...]}`` or ``{"error": ...}``. ``via_in_pad`` lists the
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
        from ..layout.route import brief as brief_mod
        from ..layout.route import review
        earlier = brief_mod.load_decisions(expect_file)
        found = [f for f in result.get("findings") or []
                 if earlier.get(f["id"], {}).get("text") != f["text"]]
        left, decided = review.unanswered(found, accept or [])
        level, said = _discipline()
        if left and level == "enforced":
            return {"error": f"{len(left)} findings are unanswered; nothing was written. Route "
                             "again, or answer each with accept=[{id, reason}]. {check, "
                             "reason} answers all of one check only for "
                             f"{', '.join(review.GROUP_CHECKS)}; a rule of the brief the "
                             "copper breaks is answered by id.",
                    "unanswered": left}
        bridge = get_bridge()
        head = await bridge.send_command_async("pcb.get_layout_model", {"section": "board"},
                                                timeout=120.0)
        focused = str((head or {}).get("file", ""))
        if not _same_file(focused, expect_file):
            return {"error": f"the focused board is {focused or '(none)'}, not {expect_file}; "
                             "nothing was written"}
        out: dict[str, Any] = {"notes": list(result.get("notes") or []), **said}
        if left and level == "advisory":
            out["unanswered"] = left
        if decided:
            brief_mod.record_decisions(expect_file, decided)
            out["decisions"] = decided
        if checkpoint:
            from ..checkpoint import CheckpointStore, take
            from ..config import get_config
            cp = CheckpointStore(get_config().workspace_dir / "checkpoints")
            info = await take(bridge, cp, Path(expect_file).parent,
                              label=f"before pcb_autoroute_apply {job_id}")
            out["checkpoint"] = info.summary()
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
    async def pcb_route_simulate(
        expect_file: str,
        what: str,
        name: Optional[str] = None,
        net: Optional[str] = None,
        other: Optional[str] = None,
        job_id: Optional[str] = None,
        drive: Optional[dict[str, Any]] = None,
        models: Optional[dict[str, dict[str, Any]]] = None,
        band: Optional[dict[str, Any]] = None,
        target_ohm: Optional[float] = None,
        f_max_ghz: float = 10.0,
    ) -> dict[str, Any]:
        """Simulate what a routing finding asks, on the board's real copper.

        A finding's ``simulate`` gives the call. Give the result file as
        the ``evidence`` of your answer in pcb_autoroute_apply(accept=...).
        The copper is the board's, or, with job_id, the board's plus that
        round's routes before they are written.

        what:
            loop (ngspice): the brief loop ``name`` as the resistance and
                inductance of its routed path, driven by ``drive`` {i (A),
                rise_ns, source}; gives the peak voltage, L di/dt.
            pdn (ngspice): the pdn rule ``name``: each rail at its IC as
                the capacitors serving it, ``models`` {ref: {c_uf, esr_ohm,
                esl_nh, source}} from their datasheets, behind the
                inductance of the track and vias that mount them; swept over
                ``band`` {f_start, f_stop} Hz; gives |Z| max and where it is
                over ``target_ohm``.
            impedance (openEMS): ``net``'s tracks and vias on the stack, its
                brief reference as sheets where it is a plane or pour, ports
                at its end pads (the brief's driver first); S11, S21, line
                impedance.
            crosstalk (openEMS): ``net`` the victim, ``other`` the
                aggressor, ports at each end; near and far end coupling.

        Where a simulator is not installed the reply says so with the
        install offer for the user (never install without them); an
        openEMS model and script are written either way.

        Args:
            expect_file: full path of the focused board.
            f_max_ghz: openEMS's highest frequency (default 10).

        Returns:
            {engine, ok, the figures, inputs (what was measured on the
            copper), result_file or script} or {error}.
        """
        import asyncio

        from ..layout.model import Track, Via
        from ..layout.read_altium import WrongBoard, read_live_board
        from ..layout.route import brief as brief_mod
        from ..layout.route import simulate as sim

        what = (what or "").strip().lower()
        if what not in ("loop", "pdn", "impedance", "crosstalk"):
            return {"error": "what is loop, pdn, impedance or crosstalk"}
        try:
            board = await asyncio.to_thread(read_live_board, expect_file)
        except WrongBoard as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"error": f"board read failed: {exc}"}
        if job_id:
            from ..design.jobs import get_job_store
            rec = get_job_store().get(job_id)
            if rec is None or rec.kind != "layout_route" or rec.status != "done":
                return {"error": f"no finished layout_route job {job_id!r}"}
            got = rec.result or {}
            board.tracks += [Track(t["layer"], t["x1"], t["y1"], t["x2"], t["y2"], t["width"],
                                   net=t["net_name"]) for t in got.get("tracks") or []]
            board.vias += [Via(v["x"], v["y"], v["size"], v["hole_size"], v["low_layer"],
                               v["high_layer"], net=v["net"]) for v in got.get("vias") or []]
        brief = brief_mod.load(expect_file)

        def run():
            if what == "loop":
                if not name or not drive or "i" not in drive or "rise_ns" not in drive:
                    return {"error": "loop needs name and drive {i, rise_ns, source}"}
                return sim.run_ngspice(sim.loop_deck(board, brief, name, drive), board,
                                       "loop", name)
            if what == "pdn":
                if not name or not models:
                    return {"error": "pdn needs name and models {ref: {c_uf, esr_ohm, "
                                     "esl_nh, source}}"}
                return sim.run_ngspice(sim.pdn_deck(board, brief, name, models, band or {}),
                                       board, "pdn", name, target_ohm)
            if not net or (what == "crosstalk" and not other):
                return {"error": f"{what} needs net" + (" and other" if what == "crosstalk"
                                                        else "")}
            return sim.run_openems(board, sim.openems_model(board, brief, what, net,
                                                            other or "", f_max_ghz * 1e9))

        try:
            return await asyncio.to_thread(run)
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"an argument is malformed: {exc}"}

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
            from ..checkpoint import CheckpointStore, take
            from ..config import get_config
            cp = CheckpointStore(get_config().workspace_dir / "checkpoints")
            info = await take(bridge, cp, Path(expect_file).parent,
                              label=f"before pcb_autoplace_apply {job_id}")
            out["checkpoint"] = info.summary()
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
        out["notes"].append("Then decide the routing in pcb_route_brief and route it in "
                            "rounds with pcb_route_round.")
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
