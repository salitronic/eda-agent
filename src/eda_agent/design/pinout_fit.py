# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each pad's number against where the datasheet's pin-layout figure puts it.

The other checks tie a pin's number to its name (the pin table), the lands
to their sizes and gaps (the land-pattern drawing) and the symbol's set of
numbers to the footprint's. None ties a pad's NUMBER to its POSITION: a
land-pattern drawing often labels pin 1 only, so a footprint whose pins 25
to 35 run top to bottom where the part's run bottom to top passes them all.

A pin-layout figure prints every pin's number beside the pin. The figure's
labels are fitted onto the pads, one scale and one shift per axis, from the
numbers that appear once in both, and then every pad's nearest label must
carry the pad's own number. The fit also says which way the figure is
seen: a figure drawn from above cannot fit a footprint mirrored left to
right.
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

#: Fewer shared numbers than this cannot place a figure on a footprint.
_MIN_SHARED = 4
#: A label this many times the median misfit away is not that pad's label.
_OUTLIER = 4.0


def _line(us: list[float], xs: list[float]) -> Optional[tuple[float, float]]:
    """x = a * u + b by least squares, or None when u does not vary."""
    n = len(us)
    mu, mx = sum(us) / n, sum(xs) / n
    var = sum((u - mu) ** 2 for u in us)
    if var < 1e-12:
        return None
    a = sum((u - mu) * (x - mx) for u, x in zip(us, xs)) / var
    return a, mx - a * mu


def _fit(pairs: list[tuple[tuple[float, float], tuple[float, float]]]
         ) -> Optional[tuple[tuple[float, float], tuple[float, float], list[int]]]:
    """Scale and shift per axis from (figure point, pad point) pairs, the
    pairs that do not follow the rest left out. Returns the two lines and
    the indices of the pairs kept."""
    keep = list(range(len(pairs)))
    for _ in range(len(pairs)):
        fx = _line([pairs[i][0][0] for i in keep], [pairs[i][1][0] for i in keep])
        fy = _line([pairs[i][0][1] for i in keep], [pairs[i][1][1] for i in keep])
        if fx is None or fy is None:
            return None
        miss = [abs(fx[0] * pairs[i][0][0] + fx[1] - pairs[i][1][0])
                + abs(fy[0] * pairs[i][0][1] + fy[1] - pairs[i][1][1]) for i in keep]
        worst = max(range(len(keep)), key=lambda k: miss[k])
        typical = statistics.median(miss)
        if len(keep) <= _MIN_SHARED or miss[worst] <= max(_OUTLIER * typical, 1e-6):
            return fx, fy, keep
        del keep[worst]
    return None


def _spacing(points: list[tuple[float, float]]) -> float:
    """The usual distance from a pad to its nearest neighbour."""
    near = []
    for i, (x, y) in enumerate(points):
        d = min((((x - a) ** 2 + (y - b) ** 2) ** 0.5
                 for j, (a, b) in enumerate(points) if j != i), default=0.0)
        if d > 0:
            near.append(d)
    return statistics.median(near) if near else 1.0


def check_pinout(pads: list[dict[str, Any]], labels: list[dict[str, Any]],
                 view: str = "top") -> dict[str, Any]:
    """``{ok, view, checked, mismatches, unlabelled, fit, reason?}``.

    ``pads``: ``{designator, x, y}``, the footprint as built, seen from
    above with y up. ``labels``: ``{value, at: [left, top]}``, the
    figure's printed labels as fractions of its picture (what
    render_region gives). ``view``: "top" or "bottom", the side the figure
    is drawn from; a bottom view is the footprint mirrored left to right.

    Pads that share a number (a thermal pad drawn as several lands) are
    checked as one, at their middle.
    """
    view = (view or "top").strip().lower()
    if view not in ("top", "bottom"):
        return {"ok": False, "reason": "view is 'top' or 'bottom': the side the figure is drawn from"}
    groups: dict[str, list[tuple[float, float]]] = {}
    for pad in pads or []:
        name = str(pad.get("designator", "")).strip()
        if name:
            groups.setdefault(name, []).append((float(pad["x"]), float(pad["y"])))
    if not groups:
        return {"ok": False, "reason": "give the footprint's pads: designator, x, y"}
    spots = {name: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
             for name, pts in groups.items()}
    marks = [(str(lb.get("value", "")).strip(), (float(lb["at"][0]), float(lb["at"][1])))
             for lb in labels or [] if lb.get("at")]
    marks = [m for m in marks if m[0] in spots]
    if not marks:
        return {"ok": False, "view": view, "reason": (
            "the figure prints none of the footprint's pad numbers as text (a "
            "picture, or another figure): give the page and region of the "
            "pin-layout figure, or compare the numbering by eye")}
    count: dict[str, int] = {}
    for value, _ in marks:
        count[value] = count.get(value, 0) + 1
    shared = [(at, spots[value]) for value, at in marks if count[value] == 1]
    if len(shared) < _MIN_SHARED:
        return {"ok": False, "view": view, "reason": (
            f"only {len(shared)} pad number(s) are printed exactly once in the "
            f"figure; {_MIN_SHARED} are needed to place the figure on the "
            "footprint. Narrow the region to the pin-layout figure alone")}
    fitted = _fit(shared)
    if fitted is None:
        return {"ok": False, "view": view, "reason": (
            "the figure's labels lie on one line: they cannot place the figure "
            "on the footprint in both directions")}
    (ax, bx), (ay, by), kept = fitted
    # A picture's top is 0 and y grows down; the footprint's y grows up. So a
    # figure that fits has y turned over, and x too when it is seen from below.
    want_x = 1 if view == "top" else -1
    turned = []
    if ax * want_x < 0:
        turned.append("left to right")
    if ay > 0:
        turned.append("top to bottom")
    placed = [(value, (ax * u + bx, ay * v + by)) for value, (u, v) in marks]
    reach = _spacing(list(spots.values())) * 0.75
    mismatches, unlabelled, checked = [], [], 0
    worst = 0.0
    for name, (x, y) in sorted(spots.items(), key=lambda kv: (len(kv[0]), kv[0])):
        value, (lx, ly) = min(placed, key=lambda m: (m[1][0] - x) ** 2 + (m[1][1] - y) ** 2)
        distance = ((lx - x) ** 2 + (ly - y) ** 2) ** 0.5
        if distance > reach:
            unlabelled.append(name)
            continue
        checked += 1
        if value != name:
            mismatches.append({"pad": name, "figure_has": value, "distance_mm": round(distance, 3)})
        else:
            worst = max(worst, distance)
    out: dict[str, Any] = {
        "ok": not mismatches and not unlabelled and not turned,
        "view": view, "pads": len(spots), "checked": checked,
        "mismatches": mismatches[:40], "unlabelled": unlabelled[:40],
        "fit": {"mm_per_width": round(ax, 4), "mm_per_height": round(ay, 4),
                "labels_used": len(kept), "labels_left_out": len(shared) - len(kept),
                "max_deviation_mm": round(worst, 3)},
    }
    if turned:
        out["reason"] = (
            f"the figure fits the footprint only turned over {' and '.join(turned)}: "
            f"seen from the {view} the numbering runs the other way. Check the view "
            "the figure is drawn from; if it is right, the footprint's pads are "
            "numbered mirrored")
    elif mismatches:
        out["reason"] = (
            f"{len(mismatches)} pad(s) sit where the figure prints another number: "
            + "; ".join(f"pad {m['pad']} where the figure has {m['figure_has']}"
                        for m in mismatches[:10])
            + ". Renumber the pads or correct the positions; do not build on it")
    elif unlabelled:
        out["reason"] = (
            f"{len(unlabelled)} pad(s) have no number printed near them in the figure "
            f"({', '.join(unlabelled[:10])}): they are NOT checked. Widen the region, "
            "or say plainly that these were not verified")
    return out
