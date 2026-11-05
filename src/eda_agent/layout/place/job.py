# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The placement job: place a board read from the EDA, report the moves.

Runs the placer on a copy of the board, rounds each moved part to the whole
mil the EDA's move command takes, and judges the rounded result with the
exact DRC, so what is reported is what applying the moves would give.
"""

from __future__ import annotations

import time
from typing import Any

from ..bench import copy_board, hpwl, overlaps, strip_routing
from ..drc import verdict
from ..model import LayoutBoard
from .placer import DEFAULT_STRATEGY, Placer
from .transform import set_pose
from ...units import MILS_PER_MM


def place_job(params: dict[str, Any]) -> dict[str, Any]:
    board = params.get("board")
    if isinstance(board, dict):
        board = LayoutBoard.from_dict(board)
    if not isinstance(board, LayoutBoard):
        raise ValueError("layout_place job requires a 'board' (LayoutBoard or its dict)")
    parts = params.get("parts")
    t0 = time.perf_counter()
    placer = Placer(copy_board(board), movable=set(parts) if parts else None,
                    decap_pull=float(params.get("decap_pull") or 0.0),
                    spread_density=float(params.get("spread_density") or 0.0),
                    strategy=params.get("strategy") or DEFAULT_STRATEGY,
                    grid=float(params["grid_mm"]) * MILS_PER_MM if params.get("grid_mm") else None)
    # The live view (design/live.py) shows the board before and after.
    from ...design import live
    fixed = sorted(p.ref for p in placer.parts if p.fixed)
    live.publish_safe(board, f"Placing {len(placer.parts) - len(fixed)} parts; "
                      f"{len(fixed)} stay where they are.", "place", {"fixed": fixed})
    rep = placer.run()
    out = copy_board(board)
    before = {c.ref: c for c in board.components}
    moves = []
    for part in placer.parts:
        if part.fixed:
            continue
        x, y, rot = round(part.x), round(part.y), part.rot % 360.0
        old = before.get(part.ref)
        if old is not None and abs(old.x - x) < 1e-6 and abs(old.y - y) < 1e-6 \
                and abs((old.rotation - rot) % 360.0) < 1e-6:
            continue
        set_pose(out, part.ref, x=x, y=y, rotation=rot)
        moves.append({"designator": part.ref, "x": x, "y": y, "rotation": rot})
    # Judged as applying the moves leaves it: the board's own routing
    # does not move with its parts, so a part put down on it is a short
    # or a clearance violation now, not after routing. Judged with the
    # routing taken away, two moved resistors reported none and the
    # board's batch DRC then found ten.
    placed = strip_routing(out)
    v = verdict(out)
    pub = live.publish_safe(out, f"Placed: {len(moves)} parts moved, {len(rep['failed'])} found "
                      f"no free spot, {len(v['violations'])} violations.",
                      "place", {"fixed": fixed, "blocks": rep.get("blocks") or []})
    moved = {m["designator"] for m in moves}
    blocks = {k: rep[k] for k in ("blocks", "legaliser_moves", "flagged_moves",
                                  "floorplan_packed", "floorplan_overflow", "fallback") if k in rep}
    strategy = rep.get("strategy", placer.strategy)
    return {
        "board": board.name,
        "strategy": strategy,
        # pcb_autoplace_apply republishes this version once it is written.
        "live_version": (pub or {}).get("version"),
        **blocks,
        "summary": {
            "strategy": strategy,
            "parts_moved": len(moves),
            "parts_fixed": sum(1 for p in placer.parts if p.fixed),
            "failed": rep["failed"],
            "body_overlaps": rep["body_overlaps"],
            "hpwl_before": round(hpwl(strip_routing(copy_board(board))), 1),
            "hpwl_after": round(hpwl(placed), 1),
            "overlaps": [list(o) for o in overlaps(placed) if moved & set(o)],
            # pass, fail or incomplete: see drc.verdict.
            "drc_status": v["status"],
            "violations": len(v["violations"]),
            "seconds": round(time.perf_counter() - t0, 1),
        },
        "violations": [x.as_dict() for x in v["violations"][:50]],
        "unevaluated_rules": v["unevaluated_rules"],
        "not_checked": v["not_checked"],
        "moves": moves,
        "notes": _notes(board, moves, rep) + v["notes"],
    }


def _notes(board: LayoutBoard, moves, rep) -> list[str]:
    notes = ["Parts keep their side. Parts whose designator starts like a "
             "connector, mounting hole, fiducial, test point, switch or "
             "battery (J, P, CN, USB, H, MH, FID, TP, T, BTN, SW, K, BT, ANT), "
             "and locked parts, stay where they are unless listed in parts."]
    # Routing is copper with a net: an outline drawn on a mechanical layer
    # was counted here as the board's routing.
    copper = set(board.copper_layers())
    routed = [t for t in board.tracks
              if not t.comp and not t.keepout and t.net and t.layer in copper]
    vias = [v for v in board.vias if not v.comp]
    if (routed or vias) and moves:
        notes.append(f"The board has {len(routed)} tracks and {len(vias)} vias of "
                     "its own; moving parts leaves them where they are. Parts were kept "
                     "clear of them on the outer layers, and the violations count is "
                     "the board with them, as applying the moves leaves it. The moved "
                     "parts' old tracks now end in the air: take them up with pcb_unroute "
                     "(the moved parts' nets) and route again.")
    if rep["failed"]:
        notes.append("These parts found no free spot and were left where spreading put "
                     "them, overlapping others: " + ", ".join(rep["failed"]))
    if rep["body_overlaps"]:
        notes.append("These parts fit only with their bodies over another part's body, "
                     "their copper clear: " + ", ".join(rep["body_overlaps"])
                     + ". Check the heights.")
    if rep.get("fallback"):
        f = rep["fallback"]
        notes.append(f"The blocks did not fit this board: {f['flagged_moves']} parts would have "
                     f"stood far from their blocks ({f['flagged_share']:.0%}), so it was placed "
                     "by the analytic strategy instead.")
    if rep.get("flagged_moves"):
        from .floorplan import FLAG_AT
        notes.append("The floorplan left no room for these parts where their block put "
                     f"them; each was moved further than {FLAG_AT:g} mil: "
                     + ", ".join(m["designator"] for m in rep["flagged_moves"]) + ".")
    return notes
