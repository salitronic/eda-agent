# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Differential pairs: two tracks routed as one, coupled at their gap.

The two nets of a pair carry one signal; what matters is that they run
side by side at the gap the impedance was calculated for, turn together,
and arrive at the same time. Routed one after the other, the second goes
wherever the first left room, the gap varies along the way, and the two
lengths drift apart at every corner.

So the pair is routed the way a person drags it: one centreline, searched
at the width of the pair (two tracks and the gap), offset half a pitch
either way. Every corner of the centreline is cut to two 45 degree bends,
so both tracks turn together and P stays on its own side throughout. At
each end the two pins fan in to the pair with 45 degree jogs. What one
track gains on the other at the corners is evened out with trapezoid
bumps on the shorter one, legs at 45 degrees, bulging away from its
partner, on its longest straight runs.

Pairs come from the board's own definitions; a board with none is read
by name (``_P``/``_N``, ``+``/``-``, ``DP``/``DM``). Width and gap come
from the board's differential pair rule for the pair when it has one.
"""

from __future__ import annotations

import math

from .. import geom
from ..rules import ObjectFacts, descriptor_values
from .exact import turn
from .lanes import check_lanes, lane_length, lay_group, plan_group

EPS = 1e-6

#: Name endings that make two nets a pair, positive first.
SUFFIXES = (("_P", "_N"), ("+", "-"), ("DP", "DM"))


def find_pairs(board) -> list[tuple[str, str, str, str]]:
    """(name, positive, negative, where from) for every pair: the board's
    own definitions when it has any, else pairs read from net names, and
    then each pair followed through any pass-through part (see below)."""
    nets = set(board.nets())
    defined = [(dp.name, dp.positive, dp.negative, "defined") for dp in board.diff_pairs
               if dp.positive in nets and dp.negative in nets and dp.positive != dp.negative]
    if defined:
        return defined + _through_parts(board, defined)
    upper = {n.upper(): n for n in nets}
    out = []
    seen = set()
    for n in sorted(nets):
        for pos, neg in SUFFIXES:
            if not n.upper().endswith(pos) or len(n) <= len(pos):
                continue
            stem = n[:-len(pos)]
            partner = upper.get((stem + neg).upper())
            if partner and partner != n and n not in seen and partner not in seen:
                out.append((stem.rstrip("_") or stem, n, partner, "name"))
                seen.update((n, partner))
    return out + _through_parts(board, out)


#: The most pins a pass-through part has: a USBLC6 is 6, a 4-line array 10
#: but carries two pairs on one side each, a common-mode choke 4.
_PASS_THROUGH_PINS = 10


def _pin_number(name: str):
    try:
        return int(str(name).strip())
    except ValueError:
        return None


def _through_parts(board, pairs: list) -> list[tuple[str, str, str, str]]:
    """The pair on the far side of a pass-through part.

    An ESD array in a USB line splits each line into two nets: the round
    LED board's USBLC6 joined USB_DP/USB_DM (to the MCU) on pins 1 and 3
    to the connector's NetJ1_A6/NetJ1_A7 on pins 6 and 4. Only the named
    half was routed as a pair; the other half went as two loose tracks.
    A flow-through part carries a line from pin k to pin N+1-k, its
    mirror across the package, so when a part has both nets of a pair and
    the mirror pins of both carry other nets, those two nets are a pair
    too. Found pairs are followed again, for a line through two parts.
    Only small parts (ESD arrays, common-mode chokes): on an MCU the
    mirror of a USB pin is just another pin.
    """
    by_comp: dict[str, dict[int, str]] = {}
    parts_on: dict[str, set] = {}
    for p in board.pads:
        if p.comp and p.net:
            parts_on.setdefault(p.net, set()).add(p.comp)
        num = _pin_number(p.name)
        if p.comp and num is not None:
            by_comp.setdefault(p.comp, {})[num] = p.net
    taken = {n for _, pos, neg, _ in pairs for n in (pos, neg)}
    out: list[tuple[str, str, str, str]] = []
    todo = list(pairs)
    while todo:
        name, pos, neg, _ = todo.pop(0)
        for comp, pins in sorted(by_comp.items()):
            count = max(pins)
            if count > _PASS_THROUGH_PINS:
                continue
            on_p = [k for k, net in pins.items() if net == pos]
            on_n = [k for k, net in pins.items() if net == neg]
            if len(on_p) != 1 or len(on_n) != 1:
                continue
            far_p = pins.get(count + 1 - on_p[0], "")
            far_n = pins.get(count + 1 - on_n[0], "")
            if (not far_p or not far_n or far_p == far_n
                    or far_p in taken or far_n in taken):
                continue
            # Both lines go on to the same part (the connector), or this
            # is two unrelated pins that happen to mirror.
            if not ((parts_on.get(far_p, set()) - {comp})
                    & (parts_on.get(far_n, set()) - {comp})):
                continue
            found = (f"{name}_{comp}", far_p, far_n, f"through {comp}")
            out.append(found)
            todo.append(found)
            taken.update((far_p, far_n))
    return out


def pair_rule(rules, name: str, pos: str) -> dict:
    """Width and gap from the board's differential pair rule for this pair:
    {"width", "gap"} as far as the rule gives them."""
    facts = ObjectFacts("track", pos, rules.net_classes.get(pos, frozenset()), "", "",
                        False, False, name)
    for rr in rules.rules:
        text = (rr.rule.descriptor or "").strip()
        if not text.lower().startswith("differential pairs") or rr.unknown:
            continue
        if not rules._matches(rr.scope1, facts):
            continue
        low = text.lower()
        k = low.find("width constraints")
        g = low.find("gap constraints")
        out = {}
        if g >= 0:
            vals = descriptor_values(text[g:k if k > g else len(text)])
            out["gap"] = vals.get("prefered") or vals.get("preferred") or vals.get("min")
        if k >= 0:
            vals = descriptor_values(text[k:])
            out["width"] = vals.get("prefered") or vals.get("preferred") or vals.get("min")
        return {key: v for key, v in out.items() if v}
    return {}


def route_pairs(ctx) -> None:
    """Route every pair whose legs run pad to pad, as coupled lanes; lay
    them as fixed copper and report each. A pair this cannot do is left
    to the negotiated router, with the reason."""
    r = ctx.r
    b = r.board
    rep = r.stage_report
    index = {id(p): i for i, p in enumerate(b.pads)}
    for name, pos, neg, source in find_pairs(b):
        entry = {"name": name, "positive": pos, "negative": neg, "source": source,
                 "routed": False, "reason": ""}
        rep.pairs.append(entry)
        if not (ctx.routed(pos) and ctx.routed(neg)):
            entry["reason"] = "not routed here"
            continue
        if pos in ctx.planes or neg in ctx.planes or pos in ctx.pours or neg in ctx.pours:
            entry["reason"] = "a plane or pour net"
            continue
        pp, nn = ctx.pads_of.get(pos, []), ctx.pads_of.get(neg, [])
        if len(pp) < 2 or len(nn) < 2:
            entry["reason"] = "a leg has a single pad"
            continue
        rule = pair_rule(r.rules, name, pos)
        pid, nid = r.grid.net_id[pos], r.grid.net_id[neg]
        width = rule.get("width") or r.options.pair_width or min(ctx.width(pos), ctx.width(neg))
        gap = rule.get("gap") or r.options.pair_gap or r.rr.pair_clearance(pid, nid)
        # The gap can be no less than the clearance between the two nets.
        gap = max(gap, r.rr.pair_clearance(pid, nid), r.rr.pair_clearance(nid, pid))
        entry.update(width=round(width, 4), gap=round(gap, 4))
        if not rule:
            entry["note"] = (
                "no differential pairs rule covers this pair, so its width and "
                "gap are defaults, not an impedance: work them out with "
                "pcb_calc_trace_width_for_impedance from the stack-up, set them "
                "in a differential_pairs rule and route again")
        options = _end_options(b, [index[id(p)] for p in pp], [index[id(p)] for p in nn],
                               max(6.0 * (width + gap), 150.0))
        if not options:
            entry["reason"] = "its pins do not pair up at two ends"
            continue
        plan, why = None, "the four pads share no routing layer"
        for (pa, na), (pb, nb) in options:
            ends = [(pos, pa, pb), (neg, na, nb)]
            for li in _layers(r, [b.pads[i] for i in (pa, pb, na, nb)]):
                got = plan_group(ctx, ends, width, width + gap, li)
                if isinstance(got, str):
                    why = "polarity swap needed" if "cross" in got else got
                    continue
                plan = got
                break
            if plan is not None:
                break
        if plan is None:
            entry["reason"] = why
            continue
        bumps = even_skew(ctx, plan, r.options.pair_skew)
        lp = lane_length(plan.lines[plan.nets.index(pos)])
        ln = lane_length(plan.lines[plan.nets.index(neg)])
        lay_group(ctx, plan, tag=("pair", name))
        for net, start, end, pads in ((pos, pa, pb, pp), (neg, na, nb, nn)):
            if len(pads) == 2:
                r.laid_nets.add(net)
            else:
                # The pair joins its two end pads; the leg's other pads (an
                # ESD part, a test point) join it anywhere along its copper.
                r.laid_joined.add(end)
                r.laid_cells[start] = lane_cells(r, plan.lines[plan.nets.index(net)],
                                                 plan.li, plan.width)
        entry.update(routed=True, layer=plan.layer, length_p=round(lp, 2), length_n=round(ln, 2),
                     skew=round(abs(lp - ln), 3), bumps=bumps, layer_changes=0,
                     branches=len(pp) + len(nn) - 4)
        ref = reference_plane(b, plan.layer)
        entry["reference"] = ref
        if ref and not any("GND" in str(r.get("net", "")).upper() for r in ref):
            entry["reference_note"] = (
                "no ground plane next to this layer: the impedance was worked out "
                "over a reference, so check which net the plane beside the pair "
                "carries (the 07b board's pair ran over +5 V)")


def reference_plane(board, layer: str) -> list[dict]:
    """The plane layers next to ``layer`` and their nets, nearest first.

    A pair's impedance is set by the plane beside it. On the round LED
    board the planes came out IP1 = GND, IP2 = +5 V, so the bottom-layer
    USB pair ran over +5 V while the agent's summary said ground; nothing
    in the report said which plane the pair actually had.
    """
    ordered = sorted(board.layers, key=lambda l: l.order)
    names = [l.name for l in ordered]
    if layer not in names:
        return []
    at = names.index(layer)
    out = []
    for step in (1, -1):
        k = at + step
        while 0 <= k < len(ordered):
            if ordered[k].kind == "plane":
                out.append({"layer": ordered[k].name, "net": ordered[k].plane_net,
                            "layers_away": abs(k - at)})
                break
            if ordered[k].kind == "signal":
                break                       # a signal layer in between: not this pair's reference
            k += step
    return sorted(out, key=lambda r: r["layers_away"])


#: How many choices of the two ends are planned before the pair is given up.
_END_TRIES = 12


def _end_options(b, ps: list[int], ns: list[int], limit: float) -> list:
    """The choices of the pair's two ends, best first.

    A couple is a P pad and an N pad of one part no further apart than
    ``limit``. The two ends are couples on two different parts, the
    furthest-apart parts first and, between the same two parts, the
    tightest couples first. A USB-C receptacle carries each line twice,
    interleaved (B7 D-, A6 D+, A7 D-, B6 D+), so its pads couple more than
    one way and both ends could otherwise land on the connector; the pads
    not chosen join the pair's copper as branches.
    """
    d = lambda i, j: math.hypot(b.pads[i].x - b.pads[j].x, b.pads[i].y - b.pads[j].y)  # noqa: E731
    couples = [(i, j) for i in ps for j in ns
               if b.pads[i].comp == b.pads[j].comp and d(i, j) <= limit]
    if not couples:
        return []

    def mid(cp):
        return ((b.pads[cp[0]].x + b.pads[cp[1]].x) / 2, (b.pads[cp[0]].y + b.pads[cp[1]].y) / 2)

    options = [(x, y) for k, x in enumerate(couples) for y in couples[k + 1:]
               if b.pads[x[0]].comp != b.pads[y[0]].comp or not b.pads[x[0]].comp]
    options = [(x, y) for x, y in options if not set(x) & set(y)]
    # Parts a whole pad apart rank by distance; within that, tight couples.
    options.sort(key=lambda xy: (-round(math.dist(mid(xy[0]), mid(xy[1])) / 50.0),
                                 d(*xy[0]) + d(*xy[1])))
    return options[:_END_TRIES]


def lane_cells(r, pts, li: int, width: float) -> list:
    """The grid cells (layer, y, x) a branch can end on and meet this
    polyline of copper: cell centres well inside its width."""
    spec = r.grid.spec
    out = set()
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(math.hypot(bx - ax, by - ay) / (r.pitch / 2))))
        for k in range(n + 1):
            x, y = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
            i, j = spec.cell(x, y)
            if not (0 <= i < spec.nx and 0 <= j < spec.ny):
                continue
            cx, cy = float(spec.x(i)), float(spec.y(j))
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0.0 if ll == 0 else max(0.0, min(1.0, ((cx - ax) * dx + (cy - ay) * dy) / ll))
            if math.hypot(cx - ax - t * dx, cy - ay - t * dy) <= width / 2 - 0.25:
                out.add((li, j, i))
    return sorted(out)


def _layers(r, pads) -> list[int]:
    common = None
    for p in pads:
        ls = {r.grid.layer_index[c.layer] for c in p.copper if c.layer in r.grid.layer_index}
        common = ls if common is None else common & ls
    return sorted(common or [])


def even_skew(ctx, plan, budget: float) -> int:
    """Lengthen the shorter track with trapezoid bumps until the two differ
    by no more than ``budget``; returns how many went in.

    A bump rises at 45 degrees away from the partner, runs flat, and comes
    back at 45 degrees: four bends of 45 degrees, none at a right angle.
    One adds 2 h (sqrt 2 - 1) for a height h. Bumps go on the shorter
    track's longest straight runs, as many as the skew needs at up to
    twice the pair's pitch high, each checked exactly; a bump that does
    not clear is not used.
    """
    r = ctx.r
    if len(plan.lines) != 2:
        return 0
    la, lb = lane_length(plan.lines[0]), lane_length(plan.lines[1])
    skew = abs(la - lb)
    if skew <= budget:
        return 0
    short = 0 if la < lb else 1
    pts = list(plan.lines[short])
    partner = plan.lines[1 - short]
    nid = r.grid.net_id[plan.nets[short]]
    w = plan.width
    gain = 2.0 * (math.sqrt(2.0) - 1.0)          # length added per mil of height
    h_max = 2.0 * plan.pitch
    top = 3.0 * w
    count = 0
    want = skew
    for _ in range(16):
        if want <= budget / 2:
            break
        best = _longest_run(pts)
        if best is None:
            break
        k, length = best
        a, b = pts[k], pts[k + 1]
        u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        side = _away(a, b, partner)
        o = (side * -u[1], side * u[0])
        placed = False
        h = min(h_max, want / gain)
        while h >= 0.5:
            foot = 2 * h + top
            if foot + 2 * w > length:
                h = (length - 2 * w - top) / 2
                if h < 0.5:
                    break
                continue
            s = (length - foot) / 2
            p0 = (a[0] + s * u[0], a[1] + s * u[1])
            p1 = (p0[0] + h * (u[0] + o[0]), p0[1] + h * (u[1] + o[1]))
            p2 = (p1[0] + top * u[0], p1[1] + top * u[1])
            p3 = (p2[0] + h * (u[0] - o[0]), p2[1] + h * (u[1] - o[1]))
            new = [p0, p1, p2, p3]
            segs = [geom.capsule(x[0], x[1], y[0], y[1], w) for x, y in zip(new, new[1:])]
            other = [geom.capsule(x[0], x[1], y[0], y[1], w) for x, y in zip(partner, partner[1:])]
            need = r.rr.pair_clearance(nid, r.grid.net_id[plan.nets[1 - short]])
            ok = all(ctx.clears(nid, s_, plan.li) for s_ in segs) and all(
                geom.clearance(s_, t_) >= need - 1e-3 for s_ in segs for t_ in other)
            if ok:
                pts[k + 1:k + 1] = new
                want -= gain * h
                count += 1
                placed = True
                break
            h *= 0.7
        if not placed:
            # Split the run so the next search looks at the others first.
            if len(pts) > 512 or length < 4 * w:
                break
            pts[k + 1:k + 1] = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)]
    if not count:
        return 0
    before = plan.lines[short]
    plan.lines[short] = [pts[0]] + [q for p, q, s in zip(pts, pts[1:], pts[2:])
                                    if turn(p, q, s) > 1e-6] + [pts[-1]]
    if check_lanes(ctx, plan):
        plan.lines[short] = before
        return 0
    return count


def _longest_run(pts):
    """The longest segment of the polyline that is not its first or last
    (those leave and reach the pads)."""
    best = None
    for k in range(1, len(pts) - 2):
        a, b = pts[k], pts[k + 1]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if best is None or length > best[1]:
            best = (k, length)
    return best


def _away(a, b, partner) -> float:
    """+1 when the partner lies to the right of the segment a -> b (so a
    bump to the left moves away from it), -1 when it lies to the left."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    best = None
    for p, q in zip(partner, partner[1:]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        ll = dx * dx + dy * dy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((mx - p[0]) * dx + (my - p[1]) * dy) / ll))
        cx, cy = p[0] + t * dx, p[1] + t * dy
        d = math.hypot(mx - cx, my - cy)
        if best is None or d < best[0]:
            best = (d, cx, cy)
    _, cx, cy = best
    cross = (b[0] - a[0]) * (cy - a[1]) - (b[1] - a[1]) * (cx - a[0])
    return 1.0 if cross < 0 else -1.0
