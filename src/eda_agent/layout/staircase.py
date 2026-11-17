# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Staircases: a slanted or curved run drawn as a chain of tiny steps.

A grid router, or a script stepping along a grid, draws a shallow run as
alternating short pieces, each shorter than the track is wide. The copper
is a ragged band of blobs, hundreds of primitives where two tracks do the
job. ``find`` names them in a list of tracks, in any one unit.

A staircase is four or more tracks of one net and layer, joined end to
end, each shorter than STEP track widths, that change direction at three
or more of their joints. A short stub into a pad or a neck is two or
three pieces and bends once or twice, so it is not one.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

#: A step is a track shorter than this many of its own widths.
STEP = 1.5
#: The fewest steps, and the fewest bends between them, that make one.
MIN_STEPS = 4
MIN_BENDS = 3
_SNAP = 1e-3


def _get(t: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        v = t.get(n) if isinstance(t, dict) else getattr(t, n, None)
        if v is not None:
            return v
    return default


def find(tracks: Iterable[Any], limit: int = 20) -> dict[str, Any]:
    """``staircases`` (each ``net``, ``layer``, ``at`` one end, ``steps``
    and ``length``, longest first, at most ``limit``), ``staircase_count``
    and ``staircase_tracks`` (the tracks in them). Tracks are dicts or
    objects with x1, y1, x2, y2, width, layer and net (or net_name)."""
    return _find(tracks, limit)[0]


def _find(tracks: Iterable[Any], limit: int) -> tuple[dict[str, Any], set[int]]:
    """``find``'s reply, and the places in ``tracks`` of the steps found."""
    short = []
    stepped: set[int] = set()
    for place, t in enumerate(tracks):
        try:
            x1, y1 = float(_get(t, "x1")), float(_get(t, "y1"))
            x2, y2 = float(_get(t, "x2")), float(_get(t, "y2"))
            width = float(_get(t, "width", default=10))
        except (TypeError, ValueError):
            continue
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= _SNAP or width <= 0 or length >= STEP * width:
            continue
        key = (str(_get(t, "net", "net_name", default="")),
               str(_get(t, "layer", default="TopLayer")))
        short.append((key, (x1, y1), (x2, y2), length, place))

    def at(key, p):
        return (key, round(p[0] / _SNAP), round(p[1] / _SNAP))

    ends: dict = {}
    for i, (key, a, b, _l, _p) in enumerate(short):
        ends.setdefault(at(key, a), []).append(i)
        ends.setdefault(at(key, b), []).append(i)
    seen: set[int] = set()
    found = []
    for start in range(len(short)):
        if start in seen:
            continue
        group, todo = [], [start]
        seen.add(start)
        while todo:
            i = todo.pop()
            group.append(i)
            key, a, b, _l, _p = short[i]
            for p in (a, b):
                for j in ends[at(key, p)]:
                    if j not in seen:
                        seen.add(j)
                        todo.append(j)
        if len(group) < MIN_STEPS:
            continue
        bends = 0
        members = set(group)
        for node, here in ends.items():
            mine = [i for i in here if i in members]
            if len(mine) != 2:
                continue
            (_, a1, b1, l1, _), (_, a2, b2, l2, _) = short[mine[0]], short[mine[1]]
            cross = (b1[0] - a1[0]) * (b2[1] - a2[1]) - (b1[1] - a1[1]) * (b2[0] - a2[0])
            if abs(cross) > 0.02 * l1 * l2:
                bends += 1
        if bends < MIN_BENDS:
            continue
        key = short[group[0]][0]
        first = min(group)
        stepped.update(short[i][4] for i in group)
        found.append({"net": key[0], "layer": key[1],
                      "at": [round(short[first][1][0], 3), round(short[first][1][1], 3)],
                      "steps": len(group),
                      "length": round(sum(short[i][3] for i in group), 3)})
    found.sort(key=lambda f: -f["steps"])
    return {"staircases": found[:limit], "staircase_count": len(found),
            "staircase_tracks": sum(f["steps"] for f in found)}, stepped


def shapes(tracks: Iterable[Any], pads: Iterable[Any] = (), vias: Iterable[Any] = (),
           limit: int = 20) -> dict[str, Any]:
    """``find`` and ``short`` together, a staircase's steps counted once,
    as the staircase."""
    tracks = list(tracks)
    stairs, stepped = _find(tracks, limit)
    rest = [t for i, t in enumerate(tracks) if i not in stepped]
    return {**stairs, **short(rest, pads, vias, limit)}


def refusal(found: dict[str, Any], what: str) -> dict[str, Any]:
    """The reply of a tool that will not write ``what`` with staircases in it."""
    worst = found["staircases"][0]
    return {
        "success": False, "error": "STAIRCASE_TRACKS",
        "reason": (
            f"{what} holds {found['staircase_count']} staircase(s), "
            f"{found['staircase_tracks']} tracks: runs drawn as chains of steps "
            f"shorter than {STEP:g} track widths, the longest {worst['steps']} steps "
            f"on {worst['net'] or 'no net'} from ({worst['at'][0]}, {worst['at'][1]}) "
            f"on {worst['layer']}. Nothing was placed. Lay each run as one straight "
            "track and one 45 degree track between its ends (a third where "
            "something is in the way)."),
        **found,
    }


def short(tracks: Iterable[Any], pads: Iterable[Any] = (), vias: Iterable[Any] = (),
          limit: int = 20) -> dict[str, Any]:
    """Tracks shorter than their own width that are not a stub into a pad
    or a via: ``short_tracks`` (each ``net``, ``layer``, ``at``, ``length``
    and ``width``, at most ``limit``) and ``short_track_count``. A track
    with an end on a pad's copper or in a via is a stub, as is one marked
    ``stub`` by whoever lists it. A length under the snap is no track."""
    lands = []
    for p in pads:
        for c in getattr(p, "copper", ()) or ():
            lands.append((c.layer, p.x + c.offset_x, p.y + c.offset_y,
                          math.hypot(c.w, c.h) / 2))
    for v in vias:
        lands.append((None, v.x, v.y, v.diameter / 2))
    cells: dict[tuple[int, int], list] = {}
    for land in lands:
        _, x, y, r = land
        for cx in range(int((x - r) // 100), int((x + r) // 100) + 1):
            for cy in range(int((y - r) // 100), int((y + r) // 100) + 1):
                cells.setdefault((cx, cy), []).append(land)

    def landed(layer: str, x: float, y: float) -> bool:
        return any((on is None or on == layer) and math.hypot(x - lx, y - ly) <= r + _SNAP
                   for on, lx, ly, r in cells.get((int(x // 100), int(y // 100)), ()))

    found = []
    for t in tracks:
        try:
            x1, y1 = float(_get(t, "x1")), float(_get(t, "y1"))
            x2, y2 = float(_get(t, "x2")), float(_get(t, "y2"))
            width = float(_get(t, "width", default=10))
        except (TypeError, ValueError):
            continue
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= _SNAP or length >= width or _get(t, "stub", default=False):
            continue
        layer = str(_get(t, "layer", default="TopLayer"))
        if landed(layer, x1, y1) or landed(layer, x2, y2):
            continue
        found.append({"net": str(_get(t, "net", "net_name", default="")), "layer": layer,
                      "at": [round(x1, 3), round(y1, 3)], "length": round(length, 3),
                      "width": round(width, 3)})
    return {"short_tracks": found[:limit], "short_track_count": len(found)}


def short_refusal(found: dict[str, Any], what: str) -> dict[str, Any]:
    """The reply of a tool that will not write ``what`` with such tracks in it."""
    first = found["short_tracks"][0]
    return {
        "success": False, "error": "SHORT_TRACKS",
        "reason": (
            f"{what} holds {found['short_track_count']} track(s) shorter than their own "
            f"width, the first {first['length']} long and {first['width']} wide on "
            f"{first['net'] or 'no net'} at ({first['at'][0]}, {first['at'][1]}) on "
            f"{first['layer']}: a blob, not a track. Nothing was placed. Join it to "
            "the run it belongs to, or mark a real stub into a pad or a via with "
            '"stub": true.'),
        **found,
    }


def audit(board: Any, limit: int = 20) -> dict[str, Any]:
    """The board's routed tracks judged by shape: staircases, and tracks
    shorter than their width that are no pad or via stub. Footprint
    copper, keep-outs and copper with no net are not judged."""
    routed = [t for t in board.tracks if t.net and not t.comp and not t.keepout]
    got = shapes(routed, board.pads, board.vias, limit)
    return {
        "pass": not got["staircase_count"] and not got["short_track_count"],
        "counts": {"tracks": len(routed), "staircases": got["staircase_count"],
                   "staircase_tracks": got["staircase_tracks"],
                   "short_tracks": got["short_track_count"]},
        "staircases": got["staircases"],
        "short_tracks": got["short_tracks"],
        "truncated": (got["staircase_count"] > len(got["staircases"])
                      or got["short_track_count"] > len(got["short_tracks"])),
    }
