# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Route a board given as a plain geometry dict, and give the routes back
as the calls that place them.

The geometry dict is the shape the bridge's board geometry read returns
and any backend can build: ``pads`` (x, y, x_size, y_size, rotation,
shape, layer, net, hole_size, comp, name), ``tracks`` (x1, y1, x2, y2,
width, layer, net), ``vias`` (x, y, size, hole_size, net), ``outline`` (a
list of points, or segments with x, y) and ``bbox`` (x1, y1, x2, y2), all
in mils. ``rules``: clearance_mils, track_width_mils (a number, or a dict
of net class to width with "default"), via_size_mils, via_drill_mils and
layers (top first; default TopLayer and BottomLayer).

The board is routed by the board router (``router.route_adaptive``),
with its stages and its clean-up, exactly as a board read from Altium is.
"""

from __future__ import annotations

import math
from typing import Any

from ..model import Layer, LayoutBoard, Pad, PadCopper, Region, Rule, Track, Via

#: Copper round the outline when the geometry gives only pads.
APRON = 200.0
#: Pad layer names that put a pad on every copper layer.
MULTI = {"multilayer", "multi"}
KEEPOUT = {"keepoutlayer", "keepout"}


def _f(d: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            try:
                return float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} is not a number: {v!r}") from None
    return default


def _shape(name: str) -> str:
    s = str(name or "").lower()
    if "round" in s and "rect" in s:
        return "roundrect"
    if "rect" in s or "square" in s:
        return "rect"
    if "oct" in s:
        return "octagon"
    return "round"


def _layers(rules: dict) -> list[str]:
    names = [str(x) for x in (rules.get("layers") or ("TopLayer", "BottomLayer"))]
    if not names:
        raise ValueError("at least one routing layer is needed")
    if len({n.lower() for n in names}) != len(names):
        raise ValueError("a routing layer is named twice")
    return names


def _outline(geometry: dict, pads: list[Pad]) -> list[tuple[float, float]]:
    pts = []
    for p in geometry.get("outline") or []:
        if isinstance(p, dict):
            pts.append((_f(p, "x"), _f(p, "y")))
        else:
            pts.append((float(p[0]), float(p[1])))
    if len(pts) >= 3:
        return pts
    box = geometry.get("bbox") or {}
    try:
        x1, y1, x2, y2 = (float(box[k]) for k in ("x1", "y1", "x2", "y2"))
        if x2 > x1 and y2 > y1:
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    except (KeyError, TypeError, ValueError):
        pass
    if not pads:
        raise ValueError("the geometry has no outline, no usable bbox and no pads")
    xs, ys = [p.x for p in pads], [p.y for p in pads]
    x1, y1, x2, y2 = min(xs) - APRON, min(ys) - APRON, max(xs) + APRON, max(ys) + APRON
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def board_from_geometry(geometry: dict[str, Any], rules: dict[str, Any] | None = None,
                        net_classes: dict[str, str] | None = None) -> LayoutBoard:
    """The geometry dict as a LayoutBoard, its rules as board rules.

    Raises ValueError on malformed input."""
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a dict")
    rules = dict(rules or {})
    names = _layers(rules)
    by_lower = {n.lower(): n for n in names}
    board = LayoutBoard(name=str(geometry.get("name") or "geometry"), source="geometry",
                        layers=[Layer(n, "signal", i) for i, n in enumerate(names)])
    for p in geometry.get("pads") or []:
        lay = str(p.get("layer") or "").lower()
        hole = _f(p, "hole_size", "hole")
        w, h = _f(p, "x_size", "width"), _f(p, "y_size", "height", default=_f(p, "x_size"))
        if w <= 0 or h <= 0:
            continue
        net = str(p.get("net") or "")
        if lay in KEEPOUT:
            continue
        if lay in MULTI or (hole > 0 and lay not in by_lower):
            on = names
        elif lay in by_lower:
            on = [by_lower[lay]]
        else:
            continue          # mask, paste or mechanical: not routing copper
        board.pads.append(Pad(
            comp=str(p.get("comp") or p.get("component") or ""),
            name=str(p.get("name") or p.get("designator") or ""),
            x=_f(p, "x"), y=_f(p, "y"), rotation=_f(p, "rotation"), net=net,
            copper=[PadCopper(l, _shape(p.get("shape")), w, h) for l in on],
            hole=hole if len(on) > 1 else 0.0))
    for t in geometry.get("tracks") or []:
        lay = str(t.get("layer") or "").lower()
        keep = lay in KEEPOUT
        if not keep and lay not in by_lower:
            continue
        for layer in (names if keep else [by_lower[lay]]):
            board.tracks.append(Track(layer, _f(t, "x1"), _f(t, "y1"), _f(t, "x2"), _f(t, "y2"),
                                      _f(t, "width", default=10.0) or 10.0,
                                      "" if keep else str(t.get("net") or t.get("net_name") or ""),
                                      keepout=keep))
    for v in geometry.get("vias") or []:
        d = _f(v, "size", "diameter")
        if d <= 0:
            continue
        board.vias.append(Via(_f(v, "x"), _f(v, "y"), d, _f(v, "hole_size", "hole",
                                                                default=d / 2),
                              names[0], names[-1], str(v.get("net") or "")))
    for r in geometry.get("regions") or []:
        lay = str(r.get("layer") or "").lower()
        pts = [(_f(q, "x"), _f(q, "y")) if isinstance(q, dict) else (float(q[0]), float(q[1]))
               for q in r.get("points") or r.get("outline") or []]
        if len(pts) < 3:
            continue
        if lay in KEEPOUT:
            board.regions.extend(Region(l, pts, kind="keepout", keepout=True) for l in names)
        elif lay in by_lower:
            board.regions.append(Region(by_lower[lay], pts, net=str(r.get("net") or "")))
    board.outline = _outline(geometry, board.pads)
    _rules(board, rules, net_classes or {})
    return board


def _rules(board: LayoutBoard, rules: dict, net_classes: dict[str, str]) -> None:
    clearance = _f(rules, "clearance_mils", default=10.0)
    if clearance < 0:
        raise ValueError("clearance_mils must be >= 0")
    widths = rules.get("track_width_mils", 10.0)
    if isinstance(widths, (int, float)):
        widths = {"default": float(widths)}
    elif isinstance(widths, dict):
        widths = {str(k): float(v) for k, v in widths.items()}
        widths.setdefault("default", 10.0)
    else:
        raise ValueError("track_width_mils must be a number or a dict")
    if any(w <= 0 for w in widths.values()):
        raise ValueError("track widths must be > 0")
    via = _f(rules, "via_size_mils", "via_size", default=50.0)
    drill = _f(rules, "via_drill_mils", "via_drill", "via_hole_size", default=28.0)
    if via <= 0 or drill <= 0:
        raise ValueError("via size and drill must be > 0")
    if drill > via:
        raise ValueError("via drill larger than via size")
    for net, cls in net_classes.items():
        board.net_classes.setdefault(str(cls), []).append(str(net))
    # A rule's kind is read from its descriptor, in Altium's words.
    board.rules.append(Rule("Clearance", "clearance", values={"gap": clearance},
                            descriptor="Clearance Constraint"))
    board.rules.append(Rule("Width", "width", priority=len(widths) + 1,
                            values=_width(widths["default"]), descriptor="Width Constraint"))
    for i, (cls, w) in enumerate(sorted(widths.items())):
        if cls == "default" or cls not in board.net_classes:
            continue
        board.rules.append(Rule(f"Width_{cls}", "width", f"InNetClass('{cls}')", priority=i + 1,
                                values=_width(w), descriptor="Width Constraint"))
    board.rules.append(Rule("RoutingVias", "via", descriptor="Routing Via", values={
        "preferedwidth": via, "minwidth": via, "maxwidth": via,
        "preferredholewidth": drill, "minholewidth": drill, "maxholewidth": drill}))


def _width(w: float) -> dict:
    return {"min": w, "preferred": w, "max": w}


def placement_calls(board: LayoutBoard, routed: LayoutBoard) -> dict[str, list[dict]]:
    """The copper ``routed`` adds to ``board``, as pcb_place_tracks and
    pcb_place_vias items in mils to a thousandth. Rounded to whole mils,
    a short 45 degree piece came out up to a degree off its angle."""
    def m(v):
        return round(float(v), 3)
    tracks = [{"x1": m(t.x1), "y1": m(t.y1), "x2": m(t.x2), "y2": m(t.y2),
               "width": m(t.width), "layer": t.layer, "net_name": t.net}
              for t in routed.tracks[len(board.tracks):]]
    vias = [{"x": m(v.x), "y": m(v.y), "size": m(v.diameter),
             "hole_size": m(v.hole), "net": v.net}
            for v in routed.vias[len(board.vias):]]
    return {"tracks": [t for t in tracks if (t["x1"], t["y1"]) != (t["x2"], t["y2"])],
            "vias": vias}


def segment_length(t: dict) -> float:
    return math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"])


def plan(geometry: dict[str, Any], rules: dict[str, Any] | None = None,
         nets: list[str] | None = None, net_classes: dict[str, str] | None = None,
         log=None) -> dict[str, Any]:
    """Route the geometry and return what to place, checked.

    ``nets``: route only these; every other net's copper stays as it is.
    The reply: ``tracks`` and ``vias`` to place, ``summary`` (connections
    needed and made, nets routed, failed and partly routed, counts and
    length), ``nets`` per net (``routed`` or ``failed`` with the
    connections left unmade), ``unknown_nets`` and ``validation`` (the
    exact DRC on the board with the plan laid: violations and what it
    leaves unrouted)."""
    from ..drc import run_drc
    from ..rules import RuleSet
    from .router import route_adaptive

    board = board_from_geometry(geometry, rules, net_classes)
    known = set(board.nets())
    unknown = sorted(set(nets or ()) - known)
    wanted = [n for n in (nets or ()) if n in known]
    if nets is not None and not wanted:
        return {"ok": False, "reason": "none of the nets asked for is on the board",
                "unknown_nets": unknown}
    r = route_adaptive(board, nets=frozenset(wanted) if nets is not None else None, log=log)
    out = r.apply()
    calls = placement_calls(board, out)
    rep = run_drc(out, RuleSet.from_board(out))
    scope = set(wanted) if nets is not None else known
    per_net = {}
    for net in sorted(scope):
        left = rep.unrouted.get(net, 0)
        per_net[net] = {"status": "failed" if left else "routed",
                        **({"unmade": left} if left else {})}
    needed = sum(max(0, len(p) - 1) for n, p in board.pads_by_net().items() if n in scope)
    unmade = sum(rep.unrouted.get(n, 0) for n in scope)
    return {
        "ok": True,
        "summary": {"connections": needed, "made": needed - unmade,
                    "completion": round(1 - unmade / needed, 4) if needed else 1.0,
                    "routed": sum(1 for v in per_net.values() if v["status"] == "routed"),
                    "failed": sum(1 for v in per_net.values() if v["status"] == "failed"),
                    "tracks": len(calls["tracks"]), "vias": len(calls["vias"]),
                    "length": round(sum(segment_length(t) for t in calls["tracks"]))},
        "nets": per_net,
        **calls,
        **({"unknown_nets": unknown} if nets is not None else {}),
        "validation": {"violations": [v.as_dict() for v in rep.violations[:50]],
                       "violation_count": len(rep.violations)},
    }
