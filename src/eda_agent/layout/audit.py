# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Measured checks on a LayoutBoard: the numbers a design stage ends on.

The autonomy harness closes each layout stage with an exit gate, and a
gate that reads "no overlaps" or "planes poured" is a judgement the
client makes about its own work. Each check here answers with counts
and itemised findings instead, every finding with its coordinates in
mils, so a gate can name a number and the harness can read it back:

* ``placement_audit``: bodies of parts on one side that overlap, pads of
  different nets closer than the board's clearance rule, parts on a
  keepout or a mounting hole, parts off the board;
* ``connectivity_summary``: nets routed, partly routed and not started,
  and every pad copper has not reached yet;
* ``drc``: the exact clearance and mask sliver check of ``drc.verdict``;
* ``corner_audit``: track junctions that bend sharper than 45 degrees;
* ``return_via_audit``: signal vias with no ground or plane via near;
* ``plane_region_audit``: pour and plane copper that has broken into
  islands.

Nothing here measures distance or connectivity in a second way. Clearance
is ``drc.run_drc``, connectivity is ``drc.connectivity`` and shapes are
``geom``: where a check needs a question those answer only indirectly,
the board is reshaped to ask it (a pads-only copy for pad gaps, one layer
at a time with a probe pad on each piece of pour for islands) rather than
answered by new code.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any, Callable, Optional

from ..core.net_naming import is_ground_net
from . import geom
from .drc import TOUCH, connectivity, run_drc, verdict
from .model import BOTTOM, TOP, LayoutBoard, Pad, PadCopper
from .rules import RuleSet

#: Two bodies sharing less area than this (square mils) only touch.
#: Same threshold as the placement benchmark's overlap count.
MIN_OVERLAP = 1.0

#: Findings listed per kind before the list is cut; counts are never cut.
LIMIT = 200

#: Track ends closer than this (mils) are one junction. Altium stores
#: coordinates to 1e-4 mil, so ends that meet are equal well inside it.
JUNCTION_SNAP = 1e-3

#: Slack on angles, in degrees, so a 45 degree bend computed from float
#: coordinates as 134.9999 still passes.
ANGLE_TOL = 0.1

#: Net class names, normalised (lower case, letters and digits only),
#: that mark a class as high speed. "differential" and "clock" are the
#: classes ``design.net_classes`` assigns; "highspeed" is the plain name
#: a person gives one.
HIGH_SPEED_CLASSES = ("differential", "clock", "highspeed")


def _pt(x: float, y: float) -> list[float]:
    return [round(x, 2), round(y, 2)]


def _centre(s: geom.Shape) -> list[float]:
    x0, y0, x1, y1 = s.bbox
    return _pt((x0 + x1) / 2, (y0 + y1) / 2)


def _meet(a: geom.Shape, b: geom.Shape) -> list[float]:
    """Where two shapes meet: the middle of their boxes' overlap."""
    ax0, ay0, ax1, ay1 = a.bbox
    bx0, by0, bx1, by1 = b.bbox
    x0, y0, x1, y1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    if x0 > x1 or y0 > y1:
        return _centre(a)
    return _pt((x0 + x1) / 2, (y0 + y1) / 2)


def _overlaps(a: geom.Shape, b: geom.Shape) -> bool:
    """True when two shapes share area, not when they merely touch.

    With a radius on either side the exact clearance says so: it goes
    negative only when they overlap. Two bare polygons measure 0 apart
    whether they touch or overlap, so for those the shared area decides,
    on convex hulls as ``geom.overlap_area`` documents, which overstates
    the overlap of a concave outline: the safe side for a placement check.
    """
    if not geom.bboxes_near(a, b, 0.0):
        return False
    if a.r > 0 or b.r > 0:
        return geom.clearance(a, b) < -TOUCH
    if a.kind == "poly" and b.kind == "poly":
        return geom.overlap_area(a.pts, b.pts) > MIN_OVERLAP
    return False


def _cut(items: list, limit: int) -> tuple[list, bool]:
    return items[:limit], len(items) > limit


def body_outline(comp) -> list[tuple[float, float]]:
    """The outline the placer keeps other bodies out of, in world mils.

    The courtyard, or the 3D body's extent when the footprint has no
    courtyard: exactly what ``place.placer._footprint`` files as "body".
    Empty when the read found only the extent of the pads or primitives;
    the placer then keeps the pads a clearance apart instead, and so does
    this audit, through its pad-gap check.
    """
    if comp.courtyard_source in ("courtyard", "body") and len(comp.courtyard) >= 3:
        return list(comp.courtyard)
    return []


def _pads_of(board: LayoutBoard) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, p in enumerate(board.pads):
        if p.comp:
            out.setdefault(p.comp, []).append(i)
    return out


def _pad_shapes(pad: Pad) -> list[tuple[str, geom.Shape]]:
    out = []
    for c in pad.copper:
        s = pad.shape_on(c.layer)
        if s is not None:
            out.append((c.layer, s))
    return out


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

_PAD_LABEL = re.compile(r"^pad:.*#(\d+)$")


def _pad_gaps(board: LayoutBoard) -> list[tuple[int, int, Any]]:
    """Pairs of pads closer than the rule, as (pad index, pad index,
    violation), from the exact DRC run on the board's pads alone.

    The DRC reports each layer of a pair; the tightest is kept.
    """
    pads_only = LayoutBoard(name=board.name, layers=board.layers,
                            pads=board.pads, net_classes=board.net_classes,
                            diff_pairs=board.diff_pairs, rules=board.rules)
    rep = run_drc(pads_only, RuleSet.from_board(pads_only),
                  limit=max(1000, len(board.pads) ** 2))
    best: dict[tuple[int, int], Any] = {}
    for v in rep.violations:
        ma, mb = _PAD_LABEL.match(v.a), _PAD_LABEL.match(v.b)
        if not ma or not mb:
            continue
        i, j = sorted((int(ma.group(1)), int(mb.group(1))))
        if (i, j) not in best or v.gap < best[(i, j)].gap:
            best[(i, j)] = v
    return [(i, j, v) for (i, j), v in sorted(best.items())]


def _keepouts(board: LayoutBoard) -> list[tuple[str, str, str, geom.Shape]]:
    """Every keepout as (label, layer, owning part, shape)."""
    out = []
    for i, r in enumerate(board.regions):
        if (r.kind == "keepout" or r.keepout) and len(r.outline) >= 3:
            out.append((f"region#{i}", r.layer, r.comp, r.shape()))
    for i, t in enumerate(board.tracks):
        if t.keepout:
            out.append((f"track#{i}", t.layer, t.comp, t.shape()))
    for i, a in enumerate(board.arcs):
        if a.keepout:
            for s in a.shapes():
                out.append((f"arc#{i}", a.layer, a.comp, s))
    return out


def _mounting_holes(board: LayoutBoard, pads_of) -> list[tuple[int, geom.Shape]]:
    """Holes that are not a part's leads: a drilled pad that belongs to no
    part, an unplated hole, or a part that is one drilled pad (a mounting
    hole or a through-hole test point). Shape: its copper, or the drill
    where it has none."""
    single = {ref for ref, ids in pads_of.items() if len(ids) == 1}
    out = []
    for i, p in enumerate(board.pads):
        is_hole = not p.comp or not p.plated or p.comp in single
        if p.hole <= 0 or not is_hole:
            continue
        shapes = [s for _, s in _pad_shapes(p)]
        drill = geom.circle(p.x, p.y, p.hole)
        widest = max(shapes, key=lambda s: (s.bbox[2] - s.bbox[0]) * (s.bbox[3] - s.bbox[1]),
                     default=drill)
        out.append((i, widest))
    return out


def _edge_distance(x: float, y: float, rings) -> float:
    point = geom.Shape("point", ((x, y),))
    best = math.inf
    for ring in rings:
        n = len(ring)
        for k in range(n):
            seg = geom.Shape("segment", (tuple(ring[k]), tuple(ring[(k + 1) % n])))
            best = min(best, geom.core_distance(point, seg))
    return best


def _off_board(s: geom.Shape, outline: geom.Shape) -> bool:
    """Any part of the shape beyond the outline or over a cutout."""
    rings = [outline.pts] + list(outline.holes)
    if s.kind != "poly" or s.r > 0:
        return geom.shape_to_outline_clearance(s, outline) < -TOUCH
    for x, y in s.pts:
        if not geom.point_in_poly(x, y, outline) and _edge_distance(x, y, rings) > TOUCH:
            return True
    # Every corner on the board and the body can still hang over a notch
    # in the outline or a cutout: then a corner of the board lies inside
    # the body, or an edge of the board crosses one of the body's.
    for ring in rings:
        for x, y in ring:
            if geom.point_in_poly(x, y, s) and _edge_distance(x, y, [s.pts]) > TOUCH:
                return True
    n = len(s.pts)
    for k in range(n):
        a0, a1 = s.pts[k], s.pts[(k + 1) % n]
        for ring in rings:
            m = len(ring)
            for q in range(m):
                if geom._segs_cross(a0, a1, tuple(ring[q]), tuple(ring[(q + 1) % m])):
                    return True
    return False


def placement_audit(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """Overlaps, pad gaps, keepouts, mounting holes and off-board parts.

    ``overlaps`` are pairs of parts on the same side whose bodies (see
    ``body_outline``) share area. ``pad_gaps_below_rule`` are pairs of
    pads of different nets, on different parts, closer than the clearance
    rule the board resolves for them; pairs inside one footprint are
    listed apart as ``within_footprint_pad_gaps`` and do not fail the
    check, since moving the part cannot fix them. ``on_keepouts`` pairs a
    part with a keepout its body or pads overlap (a keepout on the
    KeepOutLayer applies to every layer, one on a copper layer to that
    layer and to bodies on that side). ``on_mounting_holes`` pairs a part
    with a mounting hole its body or pads overlap, on either side.
    ``off_board`` lists parts any of whose outline (body, or pads when it
    has no body) leaves the board outline or sits over a cutout. A part
    the placer holds fixed (locked, or mechanical by its designator, see
    ``bench.is_fixed``) is listed in ``off_board_fixed`` instead and does
    not fail the check: an edge connector overhangs the edge on purpose,
    and placement did not put it there.
    """
    from .bench import is_fixed
    from .route.grid import KEEPOUT_LAYER

    pads_of = _pads_of(board)
    copper = board.copper_layers()
    side_layer = {TOP: copper[0] if copper else "TopLayer",
                  BOTTOM: copper[-1] if copper else "BottomLayer"}
    notes: list[str] = []

    footprint: dict[str, list[geom.Shape]] = {}    # part -> body, else pads
    body_of: dict[str, geom.Shape] = {}
    bodies = []
    padded = 0
    for c in board.components:
        outline = body_outline(c)
        if outline:
            body = geom.polygon(outline)
            bodies.append((c, body, outline))
            body_of[c.ref] = body
            footprint[c.ref] = [body]
        else:
            padded += 1
            footprint[c.ref] = [s for i in pads_of.get(c.ref, [])
                                for _, s in _pad_shapes(board.pads[i])]
    if padded:
        notes.append(f"{padded} part(s) have no courtyard or body outline and "
                     "are judged by their pads alone")

    overlaps = []
    for i in range(len(bodies)):
        ca, sa, oa = bodies[i]
        for j in range(i + 1, len(bodies)):
            cb, sb, ob = bodies[j]
            if ca.ref == cb.ref or ca.side != cb.side or not geom.bboxes_near(sa, sb, 0.0):
                continue
            area = geom.overlap_area(oa, ob)
            if area > MIN_OVERLAP:
                overlaps.append({"a": ca.ref, "b": cb.ref, "side": ca.side,
                                 "area_sq_mils": round(area, 1), "at": _meet(sa, sb)})

    gaps, within = [], []
    for i, j, v in _pad_gaps(board):
        pa, pb = board.pads[i], board.pads[j]
        row = {"a": pa.key, "b": pb.key, "nets": [pa.net, pb.net], "layer": v.layer,
               "gap": round(v.gap, 3), "required": round(v.required, 3),
               "at": _pt((pa.x + pb.x) / 2, (pa.y + pb.y) / 2)}
        (within if pa.comp and pa.comp == pb.comp else gaps).append(row)

    keeps = _keepouts(board)
    on_keepouts = []
    for c in board.components:
        hit: dict[str, dict] = {}
        tests = ([(side_layer.get(c.side, side_layer[TOP]), body_of[c.ref])]
                 if c.ref in body_of else [])
        tests += [(layer, s) for i in pads_of.get(c.ref, [])
                  for layer, s in _pad_shapes(board.pads[i])]
        for label, layer, owner, ks in keeps:
            if label in hit or (owner and owner == c.ref):
                continue
            for on, s in tests:
                if layer not in (KEEPOUT_LAYER, on):
                    continue
                if _overlaps(s, ks):
                    hit[label] = {"part": c.ref, "keepout": label, "layer": layer,
                                  "at": _meet(s, ks)}
                    break
        on_keepouts.extend(hit.values())

    holes = _mounting_holes(board, pads_of)
    # A part made of nothing but such holes is a mounting hole itself. A
    # connector with an unplated locating hole is not, and its body over
    # somebody else's hole still counts.
    hole_idx = {i for i, _ in holes}
    hole_parts = {ref for ref, ids in pads_of.items() if all(i in hole_idx for i in ids)}
    on_holes = []
    for c in board.components:
        if c.ref in hole_parts:
            continue
        for i, hs in holes:
            hp = board.pads[i]
            if hp.comp and hp.comp == c.ref:
                continue
            if any(_overlaps(s, hs) for s in footprint.get(c.ref, [])):
                on_holes.append({"part": c.ref, "hole": hp.key, "at": _pt(hp.x, hp.y)})

    off, off_fixed = [], []
    if len(board.outline) >= 3:
        edge = board.outline_shape()
        for c in board.components:
            if any(_off_board(s, edge) for s in footprint.get(c.ref, [])):
                (off_fixed if is_fixed(c) else off).append(
                    {"part": c.ref, "side": c.side, "at": _pt(c.x, c.y)})
    else:
        notes.append("the board has no outline: off-board parts were not checked")

    counts = {"parts": len(board.components), "overlaps": len(overlaps),
              "pad_gaps_below_rule": len(gaps), "on_keepouts": len(on_keepouts),
              "on_mounting_holes": len(on_holes), "off_board": len(off),
              "off_board_fixed": len(off_fixed),
              "within_footprint_pad_gaps": len(within)}
    out: dict[str, Any] = {
        "pass": not (overlaps or gaps or on_keepouts or on_holes or off),
        "counts": counts,
    }
    truncated = False
    for key, items in (("overlaps", overlaps), ("pad_gaps", gaps),
                       ("on_keepouts", on_keepouts), ("on_mounting_holes", on_holes),
                       ("off_board", off), ("off_board_fixed", off_fixed),
                       ("within_footprint_pad_gaps", within)):
        out[key], cut = _cut(items, limit)
        truncated |= cut
    out["truncated"] = truncated
    out["notes"] = notes
    return out


# ---------------------------------------------------------------------------
# Connectivity and DRC
# ---------------------------------------------------------------------------

def connectivity_summary(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """How far routing has got, from the copper that joins each net's pads.

    A net counts when it has two pads or more. ``routed`` nets have every
    pad joined; ``not_started`` nets have no two pads joined; the rest are
    ``partly_routed``. Judged by pads, so a track hanging off one pad that
    reaches no other leaves its net not started. Every pad outside its
    net's largest joined group is listed as unreached, with its position.
    """
    groups = connectivity(board)
    ids_by_net: dict[str, list[int]] = {}
    for i, p in enumerate(board.pads):
        if p.net:
            ids_by_net.setdefault(p.net, []).append(i)
    nets = routed = missing = 0
    partly, not_started, unreached = [], [], []
    for net in sorted(ids_by_net):
        ids = ids_by_net[net]
        if len(ids) < 2:
            continue
        nets += 1
        parts = [set(g) for g in groups.get(net, [])]
        seen = set().union(*parts) if parts else set()
        # A pad with no copper on any layer is in no group: on its own.
        parts += [{i} for i in ids if i not in seen]
        if len(parts) == 1:
            routed += 1
            continue
        missing += len(parts) - 1
        main = max(parts, key=lambda g: (len(g), -min(g)))
        (not_started if all(len(g) == 1 for g in parts) else partly).append(net)
        for g in parts:
            if g is main:
                continue
            for i in sorted(g):
                p = board.pads[i]
                unreached.append({"net": net, "pad": p.key, "layers": p.layers(),
                                  "at": _pt(p.x, p.y)})
    items, truncated = _cut(unreached, limit)
    return {
        "pass": routed == nets,
        "counts": {"nets": nets, "routed": routed, "partly_routed": len(partly),
                   "not_started": len(not_started), "unreached_pads": len(unreached),
                   "missing_connections": missing},
        "partly_routed": partly[:limit],
        "not_started": not_started[:limit],
        "unreached": items,
        "truncated": truncated or len(partly) > limit or len(not_started) > limit,
    }


def drc_summary(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """The exact DRC: clearance and solder mask slivers, counted in full.

    ``status`` is pass, fail or incomplete (``drc.verdict``); ``pass`` is
    True only for a pass, so an unevaluated rule is never read as one."""
    v = verdict(board, limit=10 ** 7)
    items, truncated = _cut([x.as_dict() for x in v["violations"]], limit)
    return {
        "pass": v["status"] == "pass",
        "status": v["status"],
        "counts": v["counts"],
        "violations": items,
        "unevaluated_rules": v["unevaluated_rules"][:limit],
        "not_checked": v["not_checked"],
        "truncated": truncated,
        "notes": v["notes"],
    }


# ---------------------------------------------------------------------------
# Corners
# ---------------------------------------------------------------------------

def _angle(ax: float, ay: float, bx: float, by: float) -> float:
    """Angle between two directions, 0 to 180 degrees."""
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    c = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
    return math.degrees(math.acos(c))


def corner_audit(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """Track junctions that bend sharper than 45 degrees.

    A junction is the shared end of tracks of one net on one signal
    layer. The angle between two tracks there is 180 when they run
    straight on and 135 at a 45 degree bend, both of which pass; 90 is a
    right-angle corner and anything less is acute, and those fail, as
    does any bend between 45 and 90. Where three or more tracks meet the
    junction is a branch, and a branch leaves at a right angle by
    design, so there only an acute angle fails. Footprint copper,
    keepouts, netless copper and arcs are not judged, nor is a track that
    ends on the middle of another.
    """
    signal = set(board.signal_layers())
    ends: dict[tuple, list] = {}
    for i, t in enumerate(board.tracks):
        if t.keepout or t.comp or not t.net or t.layer not in signal:
            continue
        if math.hypot(t.x2 - t.x1, t.y2 - t.y1) <= JUNCTION_SNAP:
            continue
        for (x, y), (ox, oy) in (((t.x1, t.y1), (t.x2, t.y2)),
                                 ((t.x2, t.y2), (t.x1, t.y1))):
            key = (t.layer, t.net, round(x / JUNCTION_SNAP), round(y / JUNCTION_SNAP))
            ends.setdefault(key, []).append((i, x, y, ox - x, oy - y))
    junctions = 0
    found = []
    for (layer, net, _, _), members in sorted(ends.items(), key=lambda kv: kv[0]):
        if len(members) < 2:
            continue
        junctions += 1
        worst = None
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                ang = _angle(members[a][3], members[a][4], members[b][3], members[b][4])
                if worst is None or ang < worst[0]:
                    worst = (ang, members[a][0], members[b][0])
        ang, ta, tb = worst
        floor = 135.0 if len(members) == 2 else 90.0
        if ang >= floor - ANGLE_TOL:
            continue
        kind = ("acute" if ang < 90.0 - ANGLE_TOL
                else "right_angle" if ang <= 90.0 + ANGLE_TOL else "steep")
        x, y = members[0][1], members[0][2]
        found.append({"net": net, "layer": layer, "at": _pt(x, y),
                      "angle": round(ang, 2), "bend": round(180.0 - ang, 2),
                      "kind": kind, "tracks": [ta, tb], "branch": len(members) > 2})
    items, truncated = _cut(found, limit)
    return {
        "pass": not found,
        "counts": {"junctions": junctions, "sharper_than_45": len(found),
                   "right_angle": sum(f["kind"] == "right_angle" for f in found),
                   "acute": sum(f["kind"] == "acute" for f in found)},
        "corners": items,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Return vias
# ---------------------------------------------------------------------------

def _is_high_speed_class(name: str) -> bool:
    norm = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return any(norm.startswith(w) for w in HIGH_SPEED_CLASSES)


def return_nets(board: LayoutBoard) -> set[str]:
    """Nets a return current can use: ground by its name, and every net
    that has a plane layer, a split plane or a pour."""
    nets = {n for n in board.nets() if is_ground_net(n)}
    nets |= {l.plane_net for l in board.layers if l.plane_net}
    nets |= {r.net for r in board.regions if r.kind in ("plane", "pour") and r.net}
    return nets


def return_via_audit(board: LayoutBoard, nets: Optional[list[str]] = None,
                     max_distance_mils: float = 40.0, limit: int = LIMIT) -> dict:
    """Signal vias with no ground or plane via within ``max_distance_mils``.

    Which signal vias: those on ``nets`` when given; otherwise those on
    the nets of the board's differential pairs and of its high-speed net
    classes (see ``HIGH_SPEED_CLASSES``); and when the board names
    neither, every signal via, which ``scope`` says. A signal via is one
    on a net that is not a return net (see ``return_nets``). Distance is
    centre to centre. Each exception gives the nearest return via's
    distance, or None when the board has none.
    """
    returns = return_nets(board)
    if nets:
        scope_nets: Optional[set[str]] = set(nets)
        scope = "the nets asked for"
    else:
        pairs = {n for d in board.diff_pairs for n in (d.positive, d.negative) if n}
        fast = {n for cname, members in board.net_classes.items()
                if _is_high_speed_class(cname) for n in members}
        if pairs or fast:
            scope_nets = pairs | fast
            scope = "nets of differential pairs and high-speed classes"
        else:
            scope_nets = None
            scope = ("every signal via: the board names no differential pair "
                     "or high-speed class")
    anchors = [(v.x, v.y) for v in board.vias if v.net in returns]
    checked = 0
    exceptions = []
    for v in board.vias:
        if not v.net or v.net in returns:
            continue
        if scope_nets is not None and v.net not in scope_nets:
            continue
        checked += 1
        near = min((math.hypot(v.x - x, v.y - y) for x, y in anchors), default=None)
        if near is None or near > max_distance_mils + 1e-9:
            exceptions.append({"net": v.net, "at": _pt(v.x, v.y),
                               "nearest_return_mils": None if near is None else round(near, 2)})
    items, truncated = _cut(exceptions, limit)
    return {
        "pass": not exceptions,
        "counts": {"signal_vias": checked, "with_return": checked - len(exceptions),
                   "exceptions": len(exceptions)},
        "scope": scope,
        "max_distance_mils": max_distance_mils,
        "return_nets": sorted(returns)[:limit],
        "exceptions": items,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Pours and planes
# ---------------------------------------------------------------------------

#: A probe pad is a dot of copper at a corner of one piece of pour. Its
#: only job is to put that piece into ``drc.connectivity``'s answer, which
#: groups pads and nothing else.
_PROBE = 1e-3
_PROBE_NAME = "\x00probe"


def _pieces(board: LayoutBoard) -> list[int]:
    """Indices of the regions that are pour or plane copper of a net, on a
    layer where ``drc.conductive_items`` treats them as copper."""
    copper = set(board.copper_layers())
    planes = {l.name for l in board.layers if l.kind == "plane"}
    out = []
    for i, r in enumerate(board.regions):
        if r.kind not in ("pour", "plane") or not r.net or len(r.outline) < 3:
            continue
        if r.layer not in copper or (r.layer in planes and r.kind != "plane"):
            continue
        out.append(i)
    return out


def _probe(board: LayoutBoard, ridx: int) -> Pad:
    r = board.regions[ridx]
    x, y = r.outline[0]
    return Pad(comp="", name=f"{_PROBE_NAME}{ridx}", x=x, y=y, net=r.net,
               copper=[PadCopper(r.layer, "round", _PROBE, _PROBE)])


def _probe_groups(board: LayoutBoard, probes: dict[int, Pad]) -> dict[int, int]:
    """For each probed region, an id of the copper group it is in."""
    first = len(board.pads)
    order = list(probes)
    work = replace(board, pads=list(board.pads) + [probes[i] for i in order])
    out: dict[int, int] = {}
    gid = 0
    for groups in connectivity(work).values():
        for g in groups:
            for idx in g:
                if idx >= first:
                    out[order[idx - first]] = gid
            gid += 1
    return out


def _one_layer(board: LayoutBoard, layer: str) -> LayoutBoard:
    """The board's copper on one layer and nothing else.

    A via becomes a via on this layer alone and a pad keeps only its
    copper here, so nothing joins two pieces through another layer. The
    layer's plane net is cleared: ``drc.connectivity`` joins every barrel
    of a plane's net as one sheet, which is the question being asked, not
    an answer to it.
    """
    lay = next(l for l in board.layers if l.name == layer)
    pads = [replace(p, copper=[c for c in p.copper if c.layer == layer])
            for p in board.pads if any(c.layer == layer for c in p.copper)]
    vias = [replace(v, low_layer=layer, high_layer=layer) for v in board.vias
            if layer in board.layers_between(v.low_layer, v.high_layer)]
    return LayoutBoard(name=board.name, layers=[replace(lay, plane_net="")], pads=pads,
                       tracks=[t for t in board.tracks if t.layer == layer],
                       arcs=[a for a in board.arcs if a.layer == layer], vias=vias,
                       regions=[r for r in board.regions if r.layer == layer])


def _area(r) -> float:
    return geom.ring_area(r.outline) - sum(geom.ring_area(h) for h in r.holes if len(h) >= 3)


def plane_region_audit(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """Whether each pour or plane net is one piece of copper on each layer.

    The pieces of a net's pour or plane copper on a layer are one when
    copper on that same layer joins them: touching, or bridged by tracks,
    arcs, pads or vias of the net there. Every other group is an island,
    listed with its area, where it is, and ``joined_elsewhere``: whether
    it still reaches the main piece through other layers (stitching vias,
    a plane, through-hole pads). An island joined nowhere is dead copper
    and is counted again in ``dead_islands``.
    """
    pieces = _pieces(board)
    by_layer: dict[str, list[int]] = {}
    for i in pieces:
        by_layer.setdefault(board.regions[i].layer, []).append(i)
    probes = {i: _probe(board, i) for i in pieces}
    whole = _probe_groups(board, probes) if pieces else {}

    summary, islands = [], []
    for layer in sorted(by_layer):
        sub = _one_layer(board, layer)
        local = _probe_groups(sub, {i: probes[i] for i in by_layer[layer]})
        by_net: dict[str, dict[int, list[int]]] = {}
        for i in by_layer[layer]:
            by_net.setdefault(board.regions[i].net, {}).setdefault(local.get(i, -1 - i), []).append(i)
        for net in sorted(by_net):
            clusters = list(by_net[net].values())
            main = max(clusters, key=lambda c: (sum(_area(board.regions[i]) for i in c), -min(c)))
            summary.append({"layer": layer, "net": net,
                            "pieces": sum(len(c) for c in clusters),
                            "one_piece": len(clusters) == 1})
            for c in clusters:
                if c is main:
                    continue
                shapes = [board.regions[i].shape() for i in c]
                x0 = min(s.bbox[0] for s in shapes)
                y0 = min(s.bbox[1] for s in shapes)
                x1 = max(s.bbox[2] for s in shapes)
                y1 = max(s.bbox[3] for s in shapes)
                joined = (c[0] in whole and main[0] in whole
                          and whole[c[0]] == whole[main[0]])
                islands.append({"layer": layer, "net": net, "regions": sorted(c),
                                "area_sq_mils": round(sum(_area(board.regions[i]) for i in c), 1),
                                "at": _pt((x0 + x1) / 2, (y0 + y1) / 2),
                                "joined_elsewhere": joined})
    items, truncated = _cut(islands, limit)
    return {
        "pass": not islands,
        "counts": {"pour_nets": len(summary), "pieces": len(pieces),
                   "islands": len(islands),
                   "dead_islands": sum(not i["joined_elsewhere"] for i in islands)},
        "nets": summary[:limit],
        "islands": items,
        "truncated": truncated or len(summary) > limit,
    }


# ---------------------------------------------------------------------------

#: Every check ``run_audits`` knows, by the name a gate uses for it.
def _loop_audit(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """Circuit loops (hot, gate drive, decoupling, crystal, feedback, Kelvin)
    measured on the placement and ranked; advisory, see layout/loop_audit."""
    from eda_agent.layout.loop_audit import loop_audit
    return loop_audit(board, limit=limit)


def _pad_entry_audit(board: LayoutBoard, limit: int = LIMIT) -> dict:
    """How traces enter pads: off-axis, off-centre, wider than the pad,
    crowded, crossing a pad, unbalanced two-pin parts; see
    layout/pad_entry."""
    from eda_agent.layout.pad_entry import pad_entry_audit
    return pad_entry_audit(board, limit=limit)


def _rotation_audit(board: LayoutBoard, limit: int = LIMIT) -> dict:
    from .rotation_audit import rotation_audit
    return rotation_audit(board, limit=limit)


CHECKS: dict[str, Callable[..., dict]] = {
    "placement_audit": placement_audit,
    "connectivity_summary": connectivity_summary,
    "drc": drc_summary,
    "corner_audit": corner_audit,
    "return_via_audit": return_via_audit,
    "plane_region_audit": plane_region_audit,
    "loop_audit": _loop_audit,
    "pad_entry_audit": _pad_entry_audit,
    "rotation_audit": _rotation_audit,
}


def run_audits(board: LayoutBoard, checks: Optional[list[str]] = None, *,
               nets: Optional[list[str]] = None, max_distance_mils: float = 40.0,
               limit: int = LIMIT) -> dict:
    """Run the named checks (all of ``CHECKS`` when None) on one board.

    Returns ``{"board", "pass", "status", "summary", "checks"}``: ``pass``
    is True when every check run passed, ``status`` is fail, incomplete
    (nothing found, but a check could not apply all its rules) or pass,
    ``summary`` gives each check's pass, status and counts, and ``checks``
    each check's full result. ``nets`` and
    ``max_distance_mils`` go to ``return_via_audit``. Raises ValueError
    naming any check it does not know.
    """
    names = list(checks) if checks else list(CHECKS)
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        raise ValueError(f"unknown check(s) {unknown}; known: {list(CHECKS)}")
    import logging
    import time

    results: dict[str, dict] = {}
    seconds: dict[str, float] = {}
    for name in dict.fromkeys(names):
        t0 = time.perf_counter()
        if name == "return_via_audit":
            results[name] = return_via_audit(board, nets, max_distance_mils, limit)
        else:
            results[name] = CHECKS[name](board, limit=limit)
        seconds[name] = round(time.perf_counter() - t0, 2)
    # A take's audit of the round LED board took 4.5 min in the server; the
    # same board read back takes 4.5 s outside it. Logged per check so the
    # next slow run says where the time went.
    logging.getLogger(__name__).info("layout audit %s: %s", board.name, seconds)
    statuses = {n: r.get("status") or ("pass" if r["pass"] else "fail")
                for n, r in results.items()}
    return {
        "board": board.name,
        "pass": all(r["pass"] for r in results.values()),
        # fail before incomplete: a found defect outranks a check that
        # could not finish; pass only when every check ran in full.
        "status": ("fail" if "fail" in statuses.values() else
                   "incomplete" if "incomplete" in statuses.values() else "pass"),
        "summary": {n: {"pass": r["pass"], "status": statuses[n], **r["counts"]}
                    for n, r in results.items()},
        "checks": results,
        "seconds": seconds,
    }
