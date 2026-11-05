# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read a live Altium board into a LayoutBoard.

The Pascal side (``pcb.get_layout_model``) answers one section per call in
Altium's internal units, 10000 per mil, as integers. This module pages
through the sections and turns the reply into exact mils.

It takes a ``send`` callable rather than a bridge, so the conversion is
tested on canned replies and the same code serves any caller that can
reach the command.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from . import geom
from .model import (
    Arc, Component, DiffPair, Layer, LayoutBoard, Pad, PadCopper, Region,
    Room, Rule, Track, Via, BOTTOM, TOP,
)

Send = Callable[[str, dict], Any]

COORD_PER_MIL = 10000.0
COMMAND = "pcb.get_layout_model"
PAGE = 1500


def _m(c) -> float:
    return float(c) / COORD_PER_MIL


def _raw(mils: float) -> float:
    return mils * COORD_PER_MIL


def _pt(p) -> tuple[float, float]:
    return (_m(p[0]), _m(p[1]))


def _outline_points(raw: list, tol: float = 0.05) -> list[tuple[float, float]]:
    """Altium outline segments to polygon vertices, arcs flattened.

    A line entry is ``[x, y]``. An arc entry is ``[x, y, cx, cy, r, a1, a2]``:
    its vertex, then the arc it runs along. Altium's arcs run
    counter-clockwise from a1 to a2, but which end the vertex sits on is
    not stated, so the chord points are ordered to start at the end
    nearest the vertex. The flattened arc never strays further than
    ``tol`` from the true one.
    """
    pts: list[tuple[float, float]] = []
    for entry in raw:
        vx, vy = _m(entry[0]), _m(entry[1])
        if len(entry) < 7:
            pts.append((vx, vy))
            continue
        cx, cy, r = _m(entry[2]), _m(entry[3]), _m(entry[4])
        arc = geom.arc_points(cx, cy, r, float(entry[5]), float(entry[6]), tol)
        if math.hypot(arc[-1][0] - vx, arc[-1][1] - vy) < math.hypot(
                arc[0][0] - vx, arc[0][1] - vy):
            arc.reverse()
        pts.append((vx, vy))
        pts.extend(arc[1:])
    # Drop consecutive duplicates; they add zero-length edges.
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 1e-6:
        out.pop()
    return out


def _paged(send: Send, section: str, key: str, page: int) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        reply = send(COMMAND, {"section": section, "offset": str(offset),
                               "limit": str(page)})
        chunk = reply.get(key) or []
        items.extend(chunk)
        if not reply.get("more"):
            return items
        offset += int(reply.get("count") or len(chunk) or page)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _layers(board_reply: dict) -> list[Layer]:
    out = []
    for i, l in enumerate(board_reply.get("layers") or []):
        out.append(Layer(name=l["id"], kind=l.get("kind", "signal"),
                         order=int(l.get("order", i)),
                         plane_net=l.get("plane_net", ""),
                         copper_mils=_m(l.get("copper", 0))))
    return out


def _split_planes(board_reply: dict, board: LayoutBoard) -> None:
    """Internal plane copper, from Altium's split plane objects.

    Each split plane is one net's share of a plane layer; a plane that is a
    single net is one split plane covering the layer. A layer with exactly
    one net gets it as its plane net.
    """
    nets_on: dict[str, set[str]] = {}
    for sp in board_reply.get("split_planes") or []:
        layer, net = sp.get("layer", ""), sp.get("net", "")
        nets_on.setdefault(layer, set()).add(net)
        for reg in sp.get("regions") or []:
            outline = [_pt(v) for v in reg.get("pts") or []]
            if len(outline) < 3:
                continue
            holes = [[_pt(v) for v in h] for h in reg.get("holes") or [] if len(h) >= 3]
            board.regions.append(Region(layer, outline, holes, net, "plane",
                                        False, "", "split_plane"))
    for layer in board.layers:
        nets = {n for n in nets_on.get(layer.name, set()) if n}
        if layer.kind == "plane" and len(nets) == 1:
            layer.plane_net = next(iter(nets))


def _courtyard_layers(board_reply: dict) -> set[str]:
    """Mechanical layers that carry courtyards: by kind, else by name."""
    by_kind = {m["id"] for m in board_reply.get("mech_layers") or []
               if "courtyard" in (m.get("kind") or "").lower()}
    if by_kind:
        return by_kind
    return {m["id"] for m in board_reply.get("mech_layers") or []
            if "courtyard" in (m.get("name") or "").lower()}


def _courtyard_polygon(prims: list[dict], cx: float = 0.0, cy: float = 0.0,
                       rotation: float = 0.0) -> list[tuple[float, float]]:
    """A courtyard outline from the primitives drawn on its layer.

    A region is taken as it is. Line and arc work is reduced to the extent
    it covers in the part's own frame (turned by ``rotation`` about
    ``cx, cy``) and that box is turned back, which is exact for the
    rectangular courtyards nearly every footprint uses, at any rotation,
    and conservative for the rest. Measured: the extent taken on the
    board axes made a 45-degree LQFP-48's courtyard a square twice its
    area, and the placement audit reported it overlapping ten neighbours.
    """
    for p in prims:
        if "pts" in p and len(p["pts"]) >= 3:
            return [_pt(v) for v in p["pts"]]
    turn = math.radians(rotation % 360.0)
    if abs(math.sin(2 * turn)) > 1e-9 or abs(math.cos(turn)) < 1e-9:
        local = _courtyard_polygon(
            [_turned(p, cx, cy, -turn) for p in prims])
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        return [(cx + (x - cx) * cos_t - (y - cy) * sin_t,
                 cy + (x - cx) * sin_t + (y - cy) * cos_t) for x, y in local]
    xs: list[float] = []
    ys: list[float] = []
    for p in prims:
        if "t" in p:
            x1, y1, x2, y2, w = (_m(v) for v in p["t"])
            xs += [x1 - w / 2, x2 + w / 2, x1 + w / 2, x2 - w / 2]
            ys += [y1 - w / 2, y2 + w / 2, y1 + w / 2, y2 - w / 2]
        elif "a" in p:
            cx, cy, r = _m(p["a"][0]), _m(p["a"][1]), _m(p["a"][2])
            w = _m(p["a"][5])
            for x, y in geom.arc_points(cx, cy, r, float(p["a"][3]), float(p["a"][4])):
                xs += [x - w / 2, x + w / 2]
                ys += [y - w / 2, y + w / 2]
    if not xs:
        return []
    return _box(min(xs), min(ys), max(xs), max(ys))


def _turned(prim: dict, cx: float, cy: float, turn: float) -> dict:
    """``prim`` (a track or an arc, raw coordinates) turned by ``turn``
    radians about ``cx, cy`` in mils, in the same raw form."""
    cos_t, sin_t = math.cos(turn), math.sin(turn)

    def rot(x_raw, y_raw):
        x, y = _m(x_raw) - cx, _m(y_raw) - cy
        return (_raw(cx + x * cos_t - y * sin_t), _raw(cy + x * sin_t + y * cos_t))

    if "t" in prim:
        x1, y1, x2, y2, w = prim["t"]
        a, b = rot(x1, y1), rot(x2, y2)
        return {"t": [a[0], a[1], b[0], b[1], w]}
    if "a" in prim:
        acx, acy, r, a1, a2, w = prim["a"]
        c = rot(acx, acy)
        deg = math.degrees(turn)
        return {"a": [c[0], c[1], r, float(a1) + deg, float(a2) + deg, w]}
    return prim


def _box(x1, y1, x2, y2) -> list[tuple[float, float]]:
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _components(reply: dict, courtyard_ids: set[str],
                pads_by_comp: dict[str, list[Pad]]) -> list[Component]:
    out = []
    for c in reply.get("components") or []:
        side = BOTTOM if c.get("layer") == "BottomLayer" else TOP
        bodies = [{"outline": _box(*(_m(v) for v in b["bbox"])),
                   "height": _m(b.get("height", 0)),
                   "standoff": _m(b.get("standoff", 0)),
                   "layer": b.get("layer", "")}
                  for b in c.get("bodies") or []]
        yard_prims = [p for p in c.get("mech") or [] if p.get("layer") in courtyard_ids]
        courtyard = _courtyard_polygon(yard_prims, _m(c["x"]), _m(c["y"]),
                                       float(c.get("rotation", 0.0)))
        source = "courtyard" if courtyard else ""
        if not courtyard and bodies:
            xs = [x for b in bodies for x, _ in b["outline"]]
            ys = [y for b in bodies for _, y in b["outline"]]
            courtyard, source = _box(min(xs), min(ys), max(xs), max(ys)), "body"
        if not courtyard and pads_by_comp.get(c["ref"]):
            xs, ys = [], []
            for pad in pads_by_comp[c["ref"]]:
                for layer in pad.layers():
                    s = pad.shape_on(layer)
                    if s is not None:
                        b = s.bbox
                        xs += [b[0], b[2]]
                        ys += [b[1], b[3]]
            if xs:
                courtyard, source = _box(min(xs), min(ys), max(xs), max(ys)), "pads"
        height = _m(c.get("height", 0))
        if not height and bodies:
            height = max(b["height"] for b in bodies)
        out.append(Component(
            ref=c["ref"], footprint=c.get("footprint", ""),
            x=_m(c["x"]), y=_m(c["y"]), rotation=float(c.get("rotation", 0.0)),
            side=side, locked=bool(c.get("locked")), height=height,
            comment=c.get("comment", ""), courtyard=courtyard,
            courtyard_source=source, bodies=bodies))
    return out


def _pad(p: dict) -> Pad:
    fallback = {"TopLayer": p.get("top"), "BottomLayer": p.get("bot")}
    copper = []
    for entry in p.get("copper") or []:
        layer, shape, w, h, cr, ox, oy = entry[:7]
        if len(entry) > 7 and entry[7] and p.get("hole"):
            # Altium removed this layer's unconnected pad: the plated
            # barrel is all the copper left, and clearance there is
            # measured from it.
            hole = p["hole"]
            slot = p.get("hole_type") == "slot" and p.get("hole_width")
            shape, w, h, cr, ox, oy = "round", (p["hole_width"] if slot else hole), hole, 0, 0, 0
        if not w and not h:
            # A stack array Altium left empty: use the pad's own top, mid
            # or bottom size for that layer instead.
            alt = fallback.get(layer) or (p.get("top") if p.get("simple") else p.get("mid"))
            if alt:
                shape, w, h = alt[0], alt[1], alt[2]
        if not w and not h:
            continue
        copper.append(PadCopper(layer=layer, shape=shape, w=_m(w), h=_m(h),
                                corner_pct=float(cr or 0), offset_x=_m(ox),
                                offset_y=_m(oy)))
    return _mask(p, Pad(comp=p.get("comp", ""), name=p.get("name", ""),
               x=_m(p["x"]), y=_m(p["y"]), rotation=float(p.get("rotation", 0.0)),
               net=p.get("net", ""), copper=copper, hole=_m(p.get("hole", 0)),
               hole_type=p.get("hole_type", "round"),
               hole_width=_m(p.get("hole_width", 0)),
               hole_rotation=float(p.get("hole_rotation", 0.0)),
               plated=bool(p.get("plated", True))))


def _mask(o: dict, obj):
    """The solder mask the reply gives a pad or via; see LmMaskJson."""
    tent = o.get("tent") or [False, False]
    obj.tent_top, obj.tent_bottom = bool(tent[0]), bool(tent[1])
    obj.mask_from_hole = bool(o.get("from_hole"))
    if o.get("mask"):
        obj.mask_manual = [_m(o["mask"][0]), _m(o["mask"][1])]
    return obj


def _copper(items: list[dict], board: LayoutBoard) -> None:
    for o in items:
        k = o.get("k")
        layer, net, comp = o.get("layer", ""), o.get("net", ""), o.get("comp", "")
        # Poured copper has no net of its own; the reply names its pour's.
        net = net or o.get("pour_net", "")
        # Anything on the Keep-Out layer keeps copper out of every layer,
        # flagged or not. Measured: a ring drawn there round a mounting
        # hole with pcb_place_arc was read as copper on a layer the router
        # does not route, so a signal ran straight through it.
        keepout = bool(o.get("keepout")) or layer == "KeepOutLayer"
        if k == "track":
            x1, y1, x2, y2, w = (_m(v) for v in o["v"])
            board.tracks.append(Track(layer, x1, y1, x2, y2, w, net, comp, keepout))
        elif k == "arc":
            v = o["v"]
            board.arcs.append(Arc(layer, _m(v[0]), _m(v[1]), _m(v[2]),
                                  float(v[3]), float(v[4]), _m(v[5]), net, comp, keepout))
        elif k == "via":
            v = o["v"]
            diameter = _m(v[2])
            # Only the layers that differ: a removed pad falls to the hole.
            sizes = {lay: _m(d) for lay, d in o.get("sizes") or []
                     if abs(_m(d) - diameter) > 1e-6}
            board.vias.append(_mask(o, Via(_m(v[0]), _m(v[1]), diameter, _m(v[3]),
                                  o.get("low", ""), o.get("high", ""), net, sizes, comp)))
        elif k == "region":
            outline = [_pt(v) for v in o.get("pts") or []]
            if len(outline) < 3:
                continue
            holes = [[_pt(v) for v in h] for h in o.get("holes") or [] if len(h) >= 3]
            if o.get("cutout"):
                # Every public board read carries one "cutout" that IS the
                # board shape, to within a few square mils; taken as a
                # hole, it removed the whole board.
                if not _is_board_shape(outline, board.outline):
                    board.cutouts.append(outline)
                continue
            if keepout:
                kind = "keepout"
            elif o.get("copper") is False:
                # A polygon cutout or a named region: drawn on a copper
                # layer, conducting nothing. Read as copper, a cutout
                # overlaps all the foreign copper it was drawn to keep
                # clear.
                kind = "cutout"
            else:
                kind = "pour" if o.get("in_polygon") else "copper"
            # Copper a polygon poured carries that polygon's source even when
            # the polygon has no name, so the router can set it aside and
            # pour again from the outline. Unnamed, it was read as a fixed
            # region: routing +3V3 after the GND pours took 8 minutes and
            # failed, until the agent emptied the pours by hand.
            source = (f"polygon:{o.get('pour', '')}" if o.get("in_polygon") or o.get("pour")
                      else "region")
            board.regions.append(Region(layer, outline, holes, net, kind,
                                        keepout, comp, source))
        elif k == "fill":
            x1, y1, x2, y2 = (_m(v) for v in o["v"][:4])
            rot = float(o["v"][4])
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            shape = geom.pad_shape(cx, cy, abs(x2 - x1), abs(y2 - y1), "rect", rot)
            kind = "keepout" if keepout else ("pour" if o.get("in_polygon") else "copper")
            board.regions.append(Region(layer, list(shape.pts), [], net, kind, keepout, comp,
                                        f"polygon:{o.get('pour', '')}" if o.get("in_polygon")
                                        else "fill"))
        elif k == "polygon":
            outline = _outline_points(o.get("pts") or [])
            if len(outline) >= 3:
                board.regions.append(Region(layer, outline, [], net, "pour_boundary",
                                            False, "", f"polygon:{o.get('name', '')}"))


def clean_cutouts(board: LayoutBoard) -> int:
    """Drop "cutouts" that cannot be holes; returns how many went.

    A board cutout removes board material, so nothing can sit in one: a
    cutout holding a pad is not a cutout. Neither is one as large as the
    board. Both come back from real boards with the cutout kind: a region
    that IS the board shape, and, on a board made of several regions,
    regions that between them cover all of it and hold every pad; taken
    as holes they leave no board to route on.
    """
    if not board.cutouts:
        return 0
    area = abs(_area(board.outline)) if len(board.outline) >= 3 else 0.0
    rings = [r for r in board.cutouts if len(r) >= 3]
    holds_pad = [any(geom.point_in_poly(p.x, p.y, geom.polygon(r)) for p in board.pads)
                 for r in rings]
    total = sum(abs(_area(r)) for r in rings)
    if any(holds_pad) or (area and total >= 0.5 * area):
        # On this board the kind names regions: the ones without a pad
        # are regions too (a flex strip between two rigid ones), and left
        # as holes they cut the routes between the others.
        dropped = len(board.cutouts)
        board.cutouts = []
        return dropped
    # A hole has corners inside the board and a notch has corners outside
    # it; a ring drawn wholly on the board's edge is a piece of the board.
    # A rigid-flex outline can come back as several such rings, which
    # taken as holes cut the parts on them off from everything.
    keep = [r for r in rings if not (area and abs(_area(r)) >= 0.5 * area)
            and not _on_edge(r, board.outline)]
    dropped = len(board.cutouts) - len(keep)
    board.cutouts = keep
    return dropped


EDGE_TOL = 2.0   # mils: the edge and a region are sampled from arcs apart


def _on_edge(ring, outline, tol: float = EDGE_TOL) -> bool:
    """Every vertex of ``ring`` lies on the edge of ``outline``."""
    n = len(outline)
    if n < 3:
        return False

    def near(px, py):
        for k in range(n):
            (ax, ay), (bx, by) = outline[k], outline[(k + 1) % n]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
            if math.hypot(px - ax - t * dx, py - ay - t * dy) <= tol:
                return True
        return False

    return all(near(x, y) for x, y in ring)


def _is_board_shape(ring, outline) -> bool:
    if len(outline) < 3:
        return False
    a, b = abs(_area(ring)), abs(_area(outline))
    return b > 0 and abs(a - b) <= 0.01 * b


def _area(pts) -> float:
    """Signed shoelace area of a closed ring."""
    n = len(pts)
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                     for i in range(n))


#: How far outside a pour boundary a pour vertex may sit and still count as
#: on it: float noise on a shared edge, far below any clearance.
_ON_EDGE = 0.01


def _on_or_in(x: float, y: float, shape: geom.Shape) -> bool:
    return geom.core_distance(geom.Shape("point", ((x, y),)), shape) <= _ON_EDGE


def _pour_nets(board: LayoutBoard) -> None:
    """Give poured copper the net of the pour that made it.

    Altium reports a pour's net on the polygon, and the regions it pours
    arrive with no net of their own. Left netless, a ground plane joins
    nothing and every pad sitting in it reads as a clearance violation.
    Measured on the first public board read: GND short 32 links and 74
    false violations, all of them pads inside the inner-layer ground pour.

    The pour a region belongs to is the boundary on its layer that
    contains the most of its vertices; on a tie, the smallest. The tie is
    the nested case: a small pour inside a big one of another net lies
    wholly within both boundaries, and taking the first gave it the outer
    net, shorting the two and splitting its own.

    A vertex ON the boundary counts as inside. Where a pour meets nothing
    its edge IS its boundary's edge, and a crossing test says in or out of
    such a point more or less at random: the inner pour of the first
    nested pair read scored fewer hits in its own boundary than in the
    board-sized one around it.
    """
    bounds = [(r, r.shape(), abs(_area(r.outline)))
              for r in board.regions if r.kind == "pour_boundary" and r.net]
    if not bounds:
        return
    for r in board.regions:
        if r.kind != "pour" or r.net:
            continue
        best, hits, best_area = None, 0, 0.0
        for b, shape, area in bounds:
            if b.layer != r.layer:
                continue
            n = sum(1 for x, y in r.outline if _on_or_in(x, y, shape))
            if n > hits or (n == hits and n and area < best_area):
                best, hits, best_area = b, n, area
        if best is not None:
            r.net = best.net
            r.source = best.source


def _foreign_copper(board: LayoutBoard, region: Region, net: str) -> bool:
    """Whether copper of a net other than ``net`` (or of none) sits in the
    body of ``region``: a via spanning its layer, a pad on it (a plated
    hole counts on every layer) or a track on it, by its midpoint."""
    body = region.shape()
    x0, y0, x1, y1 = body.bbox
    layer = region.layer

    def inside(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1 and geom.point_in_poly(x, y, body)

    for v in board.vias:
        if v.net != net and layer in board.layers_between(v.low_layer, v.high_layer) \
                and inside(v.x, v.y):
            return True
    for p in board.pads:
        if p.net != net and (layer in p.layers() or (p.hole > 0 and p.plated)) \
                and inside(p.x, p.y):
            return True
    for t in board.tracks:
        if t.net != net and t.layer == layer and not t.keepout \
                and inside((t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2):
            return True
    return False


def repair_pour_nets(board: LayoutBoard) -> int:
    """Give poured copper the net its contents prove, where the pour it
    was read with cannot have made it; returns how many regions changed.

    A pour leaves a void round every piece of foreign copper and none round
    its own net's, so another net's via standing in a poured region's body
    says the region is not that net's. On two public boards with a small
    pour nested in a larger one of another net, the inner pour's copper
    came back named for the outer pour: the inner net's vias and tracks
    stood in it as overlaps (32 and 6 false violations) and the inner net
    read as unjoined. The region goes to the boundary on its layer that
    holds all of it and whose net leaves no foreign copper in it, when
    exactly one net's boundaries do; else it stays as read."""
    signal = set(board.signal_layers())
    bounds = [(b, b.shape(), abs(_area(b.outline))) for b in board.regions
              if b.kind == "pour_boundary" and b.net]
    changed = 0
    for r in board.regions:
        if r.kind != "pour" or not r.net or r.layer not in signal or len(r.outline) < 3:
            continue
        if not _foreign_copper(board, r, r.net):
            continue
        fits: dict[str, tuple] = {}
        for b, shape, area in bounds:
            if b.layer != r.layer or b.net == r.net:
                continue
            if all(_on_or_in(x, y, shape) for x, y in r.outline) \
                    and (b.net not in fits or area < fits[b.net][1]):
                fits[b.net] = (b, area)
        good = [b for b, _ in fits.values() if not _foreign_copper(board, r, b.net)]
        if len(good) == 1:
            r.net, r.source = good[0].net, good[0].source
            changed += 1
    return changed


def _rules(reply: dict) -> tuple[list[Rule], list[Room]]:
    typed: dict[str, dict] = {}
    for t in reply.get("typed") or []:
        typed.setdefault(t["name"], {}).update(
            {k: _m(v) for k, v in t.items() if k != "name"})
    rules = []
    for r in reply.get("rules") or []:
        rules.append(Rule(
            name=r["name"], kind=str(r.get("kind", "")),
            scope1=r.get("scope1", "All"), scope2=r.get("scope2", "All"),
            priority=int(r.get("priority", 1)), enabled=bool(r.get("enabled", True)),
            values=typed.get(r["name"], {}), descriptor=r.get("descriptor", "")))
    rooms = [Room(name=r["name"], outline=_box(*(_m(v) for v in r["bbox"])),
                  scope=r.get("scope", ""))
             for r in reply.get("rooms") or []]
    return rules, rooms


# ---------------------------------------------------------------------------

class WrongBoard(RuntimeError):
    """The board Altium answered from is not the one that was asked for."""


def _same_file(a: str, b: str) -> bool:
    norm = lambda s: s.replace("\\", "/").rstrip("/").lower()  # noqa: E731
    return bool(a) and bool(b) and norm(a) == norm(b)


def read_board(send: Send, page: int = PAGE,
               expect_file: str = "") -> LayoutBoard:
    """Every section of the active board, as one LayoutBoard.

    ``expect_file`` names the board this read is for. The Pascal side reads
    whichever board is focused, and in a workspace with other projects open
    that can be somebody else's design. Given a name, the read refuses on the
    FIRST reply, before a single pad of the wrong board has been fetched, so
    a fixture can never carry data from a board nobody meant to read.
    """
    head = send(COMMAND, {"section": "board"})
    if expect_file and not _same_file(str(head.get("file", "")), expect_file):
        raise WrongBoard(
            f"asked to read {expect_file} but the focused board is "
            f"{head.get('file', '(none)')}; nothing further was read")
    board = LayoutBoard(name=str(head.get("file", "")).replace("\\", "/").rsplit("/", 1)[-1],
                        source=head.get("file", ""))
    board.outline = _outline_points(head.get("outline") or [])
    board.layers = _layers(head)
    _split_planes(head, board)
    board.meta["origin"] = [_m(v) for v in head.get("origin") or [0, 0]]
    board.meta["mech_layers"] = head.get("mech_layers") or []

    raw_pads = _paged(send, "pads", "pads", page)
    board.pads = [_pad(p) for p in raw_pads]
    by_comp: dict[str, list[Pad]] = {}
    for p in board.pads:
        if p.comp:
            by_comp.setdefault(p.comp, []).append(p)

    comps = send(COMMAND, {"section": "components"})
    board.components = _components(comps, _courtyard_layers(head), by_comp)

    raw_copper = _paged(send, "copper", "copper", page)
    _copper(raw_copper, board)
    # A script older than the mask read sends no tenting: the sliver check
    # then says it did not run rather than take every via as open. Pads or
    # vias, whichever the board has: a board of vias alone read as unread.
    board.meta["mask_read"] = (any("tent" in p for p in raw_pads)
                               or any("tent" in o for o in raw_copper if o.get("k") == "via"))
    _pour_nets(board)
    repair_pour_nets(board)
    clean_cutouts(board)

    board.rules, board.rooms = _rules(send(COMMAND, {"section": "rules"}))

    classes = send(COMMAND, {"section": "classes"})
    board.net_classes = {c["name"]: list(c.get("nets") or [])
                         for c in classes.get("net_classes") or []}
    board.diff_pairs = [DiffPair(d["name"], d.get("positive", ""), d.get("negative", ""))
                        for d in classes.get("diff_pairs") or []]
    return board


def read_live_board(expect_file: str, page: int = PAGE) -> LayoutBoard:
    """The named board, read from the running Altium through the bridge.

    ``expect_file`` is required here, not optional: see ``read_board``.
    """
    from eda_agent.bridge.altium_bridge import get_bridge

    if not expect_file:
        raise ValueError("expect_file is required for a live read")
    bridge = get_bridge()
    return read_board(lambda cmd, params: bridge.send_command(cmd, params,
                                                              timeout=120.0),
                      page, expect_file)
