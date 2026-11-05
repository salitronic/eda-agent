# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A routing run as a background job: board in, placeable copper out.

Pure: no bridge. The board comes from the live read (``read_altium``) or
a fixture; what comes back is the copper the router adds, in the item
shapes ``pcb_place_tracks`` and ``pcb_place_vias`` take, plus the exact
DRC's verdict on the routed board. Nothing is applied here.
"""

from __future__ import annotations

import time
from typing import Any

from ..bench import copy_board
from ..drc import usual_via_mask, verdict
from ..model import LayoutBoard
from .router import route_adaptive
from .stages import finish_report


def route_job(params: dict[str, Any]) -> dict[str, Any]:
    board = params.get("board")
    if isinstance(board, dict):
        board = LayoutBoard.from_dict(board)
    if not isinstance(board, LayoutBoard):
        raise ValueError("layout_route job requires a 'board' (LayoutBoard or its dict)")
    nets = params.get("nets")
    board, old_pours = _without_old_pours(board)
    # The live view (design/live.py): the board before, progress while
    # routing (throttled), the routed board after.
    from ...design import live
    live.publish_safe(board, f"Routing {len(nets) if nets else 'all'} nets.", "route")
    t0 = time.perf_counter()
    r = route_adaptive(board, nets=frozenset(nets) if nets else None,
                       planes=bool(params.get("planes", True)),
                       stages=params.get("stages"),
                       log=live.progress_logger("route"))
    out = r.apply()
    # The router's own tracks laid again as the shortest clean 45/90 runs;
    # the board's tracks before it stay as they are (gloss.py).
    glossed = None
    if params.get("gloss", True):
        from .gloss import gloss
        glossed = gloss(out, tracks=out.tracks[len(board.tracks):]).as_dict()
    new_tracks = out.tracks[len(board.tracks):]
    new_vias = _unique_vias(board.vias, out.vias[len(board.vias):])
    mask_note = _mask_like_board(board, out.vias[len(board.vias):])
    # Judged as written: a via on one already there is never sent, and
    # counted it is a hole drilled twice that no board will have.
    out.vias = out.vias[:len(board.vias)] + new_vias
    v = verdict(out)
    drc = v["report"]
    stages = finish_report(r, out)
    pub = live.publish_safe(out, f"Routed: {len(drc.unrouted)} nets still open, "
                      f"{len(drc.violations)} clearance violations, "
                      f"{v['counts']['mask_sliver']} mask slivers.", "route")
    rep = r.report
    wanted = set(nets) if nets else None
    unrouted = {n: k for n, k in drc.unrouted.items() if wanted is None or n in wanted}
    return {
        "board": board.name,
        # pcb_autoroute_apply republishes this version once it is written.
        "live_version": (pub or {}).get("version"),
        "summary": {
            "completion": round(drc.completion, 4),
            "missing_connections": drc.missing_connections,
            "unrouted_nets": len(unrouted),
            # pass, fail or incomplete: see drc.verdict. Zero violations
            # with a rule left unevaluated is incomplete, not a pass.
            "drc_status": v["status"],
            "violations": len(v["violations"]),
            "mask_slivers": v["counts"]["mask_sliver"],
            "unevaluated_rules": len(v["unevaluated_rules"]),
            "tracks": len(new_tracks),
            "vias": len(new_vias),
            "routing_layers": list(r.grid.layers),
            "planes": sorted({f"{p.layer}:{p.net}" for p in r.pour_planes}),
            "pours": sorted({f"{r.grid.layers[p.li]}:{p.net}" for p in r.pours}),
            "released_planes": sorted(r.released),
            "old_pours": old_pours,
            "iterations": rep.iterations,
            "seconds": round(time.perf_counter() - t0, 1),
            "sharp_bends": stages["sharp_bends"],
            "unreached_pads": len(stages["unreached"]),
            "vias_in_pads": len(stages["via_in_pad"]),
            "gloss": glossed,
        },
        "unrouted": unrouted,
        # What the planned stages did (see stages.py): every via in a pad,
        # each pair and bus, and every pad left unreached with its place
        # and the nets in its way.
        "via_in_pad": stages["via_in_pad"],
        "pairs": stages["pairs"],
        "buses": stages["buses"],
        "unreached": stages["unreached"],
        "fanout": stages["fanout"],
        "pin_lanes": stages["pin_lanes"],
        "fallback": stages["fallback"],
        "violations": [x.as_dict() for x in v["violations"][:50]],
        "unevaluated_rules": v["unevaluated_rules"],
        "not_checked": v["not_checked"],
        "tracks": [{"x1": t.x1, "y1": t.y1, "x2": t.x2, "y2": t.y2, "width": t.width,
                    "layer": t.layer, "net_name": t.net} for t in new_tracks],
        "vias": [{"x": v.x, "y": v.y, "size": v.diameter, "hole_size": v.hole,
                  "low_layer": v.low_layer, "high_layer": v.high_layer, "net": v.net}
                 for v in new_vias],
        "notes": _notes(r) + _stage_notes(stages) + v["notes"] + mask_note,
    }


def _stage_notes(stages: dict) -> list[str]:
    notes = []
    if stages["fallback"]:
        notes.append("The planned stages left connections unmade, so the board was "
                     + stages["fallback"] + ".")
    if stages["via_in_pad"]:
        notes.append(f"{len(stages['via_in_pad'])} vias sit in pads (listed in via_in_pad, "
                     "each with its kind): the fabrication notes must ask for them filled "
                     "and capped.")
    if stages["unreached"]:
        notes.append(f"{len(stages['unreached'])} pads could not be reached; unreached gives "
                     "each one's place and the nets in its way.")
    return notes


def _mask_like_board(board: LayoutBoard, new) -> list[str]:
    """Give the router's vias the solder mask most of the board's vias have.

    A via placed later takes its tenting and expansion from Altium, not
    from the router, so the sliver check judges new vias like the board's
    own. With no vias to go by they stay open, which can only find more
    slivers, never fewer.
    """
    usual = usual_via_mask(board)
    if not new or usual is None:
        return []
    if not [x for x in board.vias if not x.comp]:
        return ["New vias were judged with open solder mask on both sides: the board "
                "has no vias of its own to say whether vias are tented."]
    tt, tb, fh, manual = usual
    for x in new:
        x.tent_top, x.tent_bottom, x.mask_from_hole = tt, tb, fh
        x.mask_manual = list(manual) if manual else None
    sides = {(True, True): "tented on both sides", (False, False): "open on both sides",
             (True, False): "tented on top only", (False, True): "tented on the bottom only"}
    return [f"New vias were judged {sides[(tt, tb)]}, like most of the board's vias; check "
            "the mask of the placed vias if Altium's via defaults differ."]


def _unique_vias(existing, new) -> list:
    """The new vias less any that repeat one already there or listed.

    A second (plane fan-out) run on a live take left two vias on one
    spot, which Altium then reported as a short to itself. Not
    reproduced; a via is never wanted twice in the same place, so the
    list sent to the board holds each once.
    """
    seen = {(round(v.x, 3), round(v.y, 3), v.net) for v in existing}
    out = []
    for v in new:
        key = (round(v.x, 3), round(v.y, 3), v.net)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _without_old_pours(board: LayoutBoard) -> tuple[LayoutBoard, list[str]]:
    """The board without its polygons' poured copper.

    A polygon's copper is where its last pour left it, round routing that
    may since have been taken up or moved. The router pours from the
    polygon's outline and the apply step repours, so the old copper is not
    something new routing has to clear. Kept, it was: on a public board,
    live, un-routed and re-placed, new vias found room on the inner layers only
    in the old vias' voids: 137 s and 76 violations. Without it the same
    board routed in 8 s with none.
    """
    outlines = {(r.layer, r.source) for r in board.regions if r.kind == "pour_boundary"}
    old = [r for r in board.regions if r.kind == "pour" and (r.layer, r.source) in outlines]
    if not old:
        return board, []
    out = copy_board(board)
    out.regions = [r for r in board.regions if not any(r is o for o in old)]
    return out, sorted({f"{r.layer}:{r.net}" for r in old})


def _notes(r) -> list[str]:
    notes = []
    if r.pour_planes:
        notes.append(
            "The routes rely on these inner-layer pours as planes: "
            + ", ".join(sorted({f"{p.layer} ({p.net})" for p in r.pour_planes}))
            + ". Nothing was routed on them. Repour the polygons after "
              "applying (pcb_repour_polygons) so they clear the new vias.")
    if r.pours:
        notes.append(
            "These nets are joined by the board's own pours, poured round the "
            "routes; their tracks the pours make redundant were taken up: "
            + ", ".join(sorted({f"{r.grid.layers[p.li]} ({p.net})" for p in r.pours}))
            + ". Their connections hold once the polygons are repoured "
              "(pcb_autoroute_apply does it, or pcb_repour_polygons).")
    return notes
