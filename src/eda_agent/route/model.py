# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Routing problem model: grid obstacle map built from board geometry.

Consumes the geometry payload the Pascal ``Gen_GetPcbGeometry`` handler
emits (the same dict :mod:`eda_agent.render.pcb_svg` renders): ``bbox``
(mils), ``pads`` (x / y / x_size / y_size / rotation / layer / net),
``tracks`` (x1..y2 / width / layer / net), ``vias`` (x / y / size).
Everything is MILS; the grid quantizes to ``grid_pitch_mils`` cells.

Obstacle semantics: each blocked cell carries the set of *owner* nets
that blocked it. A cell is passable for net N only if every owner equals
N -- so a net can route over its own pads but never within clearance of
foreign copper. Unnetted copper and ``KeepOutLayer`` objects own with
``None``, which matches no net (universal blockers). Inflation is
conservative: clearance + the obstacle's own half-extent + half the
WIDEST class track width, so one map serves every net.

Not modeled: region/polygon pours (route before pouring), board-outline
edge clearance beyond the bbox, and arc tracks (their chords are not in
the payload; dense boards with arc copper should re-extract after
conversion).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

DEFAULT_GRID_PITCH_MILS = 25

# Cap on grid cells so a malformed bbox cannot allocate unbounded memory.
_MAX_CELLS = 4_000_000


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass
class RouteRules:
    """Routing design rules, all dimensions in mils.

    ``track_width_mils`` maps net class -> width; the ``"default"`` key
    covers unlisted classes. ``layers`` are the routing copper layers in
    stack order (vias are through-vias spanning all of them).
    """

    clearance_mils: float = 10.0
    track_width_mils: dict[str, int] = field(
        default_factory=lambda: {"default": 10})
    via_size_mils: int = 50
    via_drill_mils: int = 28
    layers: tuple[str, ...] = ("TopLayer", "BottomLayer")

    @property
    def max_track_halfwidth(self) -> float:
        return max(self.track_width_mils.values()) / 2.0

    def width_for_class(self, net_class: str) -> int:
        w = self.track_width_mils.get(
            net_class, self.track_width_mils.get("default", 10))
        return int(w)


def rules_from_dict(d: dict[str, Any] | None) -> RouteRules:
    """Build :class:`RouteRules` from a plain rules dict.

    Accepted keys: ``clearance_mils``, ``track_width_mils`` (int or
    per-class dict), ``via_size_mils`` / ``via_size``, ``via_drill_mils``
    / ``via_drill`` / ``via_hole_size``, ``layers``. Raises
    :class:`ValueError` on invalid values.
    """
    d = d or {}
    clearance = float(d.get("clearance_mils", 10))
    if clearance < 0:
        raise ValueError("clearance_mils must be >= 0")

    widths_in = d.get("track_width_mils", 10)
    if isinstance(widths_in, (int, float)):
        widths: dict[str, int] = {"default": int(widths_in)}
    elif isinstance(widths_in, dict):
        widths = {str(k): int(v) for k, v in widths_in.items()}
        widths.setdefault("default", 10)
    else:
        raise ValueError("track_width_mils must be a number or a dict")
    if any(w <= 0 for w in widths.values()):
        raise ValueError("track widths must be > 0")

    via_size = int(d.get("via_size_mils", d.get("via_size", 50)))
    via_drill = int(d.get(
        "via_drill_mils", d.get("via_drill", d.get("via_hole_size", 28))))
    if via_size <= 0 or via_drill <= 0:
        raise ValueError("via size and drill must be > 0")
    if via_drill > via_size:
        raise ValueError("via drill larger than via size")

    layers_in = d.get("layers", ("TopLayer", "BottomLayer"))
    layers = tuple(str(l) for l in layers_in)
    if not layers:
        raise ValueError("at least one routing layer required")
    if len(set(layers)) != len(layers):
        raise ValueError("duplicate routing layer")

    return RouteRules(
        clearance_mils=clearance,
        track_width_mils=widths,
        via_size_mils=via_size,
        via_drill_mils=via_drill,
        layers=layers,
    )


# ---------------------------------------------------------------------------
# Geometry helpers (shared with router.validate_solution)
# ---------------------------------------------------------------------------


def dist_point_seg(px: float, py: float,
                   x1: float, y1: float, x2: float, y2: float) -> float:
    """Distance from point to segment, all mils."""
    dx = x2 - x1
    dy = y2 - y1
    L2 = dx * dx + dy * dy
    if L2 <= 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def dist_point_rect(px: float, py: float,
                    cx: float, cy: float, hw: float, hh: float) -> float:
    """Distance from point to an axis-aligned rect (center + half-extents),
    0 inside, all mils."""
    dx = max(0.0, abs(px - cx) - hw)
    dy = max(0.0, abs(py - cy) - hh)
    return math.hypot(dx, dy)


def _orient(ax: float, ay: float, bx: float, by: float,
            cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _segs_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) -> bool:
    d1 = _orient(bx1, by1, bx2, by2, ax1, ay1)
    d2 = _orient(bx1, by1, bx2, by2, ax2, ay2)
    d3 = _orient(ax1, ay1, ax2, ay2, bx1, by1)
    d4 = _orient(ax1, ay1, ax2, ay2, bx2, by2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    # Collinear / endpoint touching cases are handled by the endpoint
    # distance terms in dist_seg_seg (they yield distance 0 there).
    return False


def dist_seg_seg(ax1: float, ay1: float, ax2: float, ay2: float,
                 bx1: float, by1: float, bx2: float, by2: float) -> float:
    """Minimum distance between two segments (centerlines), all mils."""
    if _segs_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
        return 0.0
    return min(
        dist_point_seg(ax1, ay1, bx1, by1, bx2, by2),
        dist_point_seg(ax2, ay2, bx1, by1, bx2, by2),
        dist_point_seg(bx1, by1, ax1, ay1, ax2, ay2),
        dist_point_seg(bx2, by2, ax1, ay1, ax2, ay2),
    )


def dist_seg_rect(x1: float, y1: float, x2: float, y2: float,
                  cx: float, cy: float, hw: float, hh: float) -> float:
    """Minimum distance from a segment to an axis-aligned rect, 0 if the
    segment touches or enters the rect, all mils."""
    if dist_point_rect(x1, y1, cx, cy, hw, hh) == 0.0:
        return 0.0
    if dist_point_rect(x2, y2, cx, cy, hw, hh) == 0.0:
        return 0.0
    corners = (
        (cx - hw, cy - hh), (cx + hw, cy - hh),
        (cx + hw, cy + hh), (cx - hw, cy + hh),
    )
    best = math.inf
    for i in range(4):
        ex1, ey1 = corners[i]
        ex2, ey2 = corners[(i + 1) % 4]
        best = min(best, dist_seg_seg(x1, y1, x2, y2, ex1, ey1, ex2, ey2))
    return best


# ---------------------------------------------------------------------------
# Problem
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Terminal:
    """One routable pad attachment point.

    ``x``/``y`` are the exact pad center in mils; ``cell`` the snapped
    grid cell; ``layers`` the routing-layer indices the pad copper is
    reachable on (all of them for a through-hole / MultiLayer pad).
    """

    x: int
    y: int
    cell: tuple[int, int]
    layers: tuple[int, ...]


class RoutingProblem:
    """Per-layer grid obstacle map + net terminals, in mils.

    Build with :meth:`from_geometry`. The router mutates the grid via
    :meth:`add_route_obstacles` as nets complete, so each routed net
    blocks the rest.
    """

    def __init__(self, rules: RouteRules,
                 net_classes: dict[str, str] | None,
                 x0: int, y0: int, nx: int, ny: int, pitch: int) -> None:
        self.rules = rules
        self.net_classes = dict(net_classes or {})
        self.x0 = x0
        self.y0 = y0
        self.nx = nx
        self.ny = ny
        self.pitch = pitch
        self.layers: tuple[str, ...] = tuple(rules.layers)
        # Per layer index: (ix, iy) -> set of owner nets (None = universal).
        # ``blocked`` is inflated for a track centerline (clearance + the
        # widest half-width); ``via_blocked`` for a via barrel (clearance
        # + via radius), which is wider -- a cell can take a track but
        # not a via.
        self.blocked: list[dict[tuple[int, int], set[str | None]]] = [
            {} for _ in self.layers]
        self.via_blocked: list[dict[tuple[int, int], set[str | None]]] = [
            {} for _ in self.layers]
        # Cells whose centre sits ON pad copper, per layer, with no
        # clearance inflation: this is the pad itself, not its keep-out.
        # A via here is a via IN a pad, which wicks solder off the joint
        # and turns the board into a filled-and-capped process. Kept
        # apart from ``via_blocked`` because that map is about what a
        # via would collide with, and this one is about what it would
        # sit inside, which is a manufacturing question rather than a
        # geometric one.
        self.pad_cells: list[set[tuple[int, int]]] = [
            set() for _ in self.layers]
        self._margin_track = rules.clearance_mils + rules.max_track_halfwidth
        self._margin_via = rules.clearance_mils + rules.via_size_mils / 2.0
        # net -> terminals (pad centers).
        self.terminals: dict[str, list[Terminal]] = {}
        # Static obstacle geometry retained for validate_solution():
        # {"kind": "rect"|"seg"|"circle", "layer": index|None(all),
        #  "net": str|None, ...shape fields...}
        self.geoms: list[dict[str, Any]] = []

    # -- coordinates ------------------------------------------------------

    def cell_center(self, ix: int, iy: int) -> tuple[int, int]:
        """Grid cell -> mils point."""
        return (self.x0 + ix * self.pitch, self.y0 + iy * self.pitch)

    def snap_cell(self, x: float, y: float) -> tuple[int, int]:
        """Mils point -> nearest in-bounds grid cell."""
        ix = int(round((x - self.x0) / self.pitch))
        iy = int(round((y - self.y0) / self.pitch))
        return (max(0, min(self.nx - 1, ix)), max(0, min(self.ny - 1, iy)))

    # -- queries -----------------------------------------------------------

    def passable(self, layer: int, ix: int, iy: int, net: str) -> bool:
        """True if ``net`` may put its track centerline on this cell."""
        if not (0 <= ix < self.nx and 0 <= iy < self.ny):
            return False
        owners = self.blocked[layer].get((ix, iy))
        if not owners:
            return True
        return all(o == net for o in owners)

    def via_ok(self, ix: int, iy: int, net: str,
               allow_in_pad: bool = False) -> bool:
        """True if a through-via for ``net`` may land on this cell. The
        barrel spans every routing layer, so the cell must clear the
        wider via inflation on all of them (plus be track-passable for
        the entry/exit centerlines).

        NOT INSIDE A PAD unless the caller asks. The clearance maps let
        a net sit on its own copper, which is right for a track and
        wrong for a via: the result is a via in the pad, which wicks
        solder off the joint and has to be filled and capped. The via
        belongs beside the pad, and there is almost always a cell there.
        ``allow_in_pad`` is for the cases that genuinely need it, BGA
        fanout and a thermal pad stitched to a plane.
        """
        if not (0 <= ix < self.nx and 0 <= iy < self.ny):
            return False
        if not allow_in_pad:
            for li in range(len(self.layers)):
                if (ix, iy) in self.pad_cells[li]:
                    return False
        for li in range(len(self.layers)):
            if not self.passable(li, ix, iy, net):
                return False
            owners = self.via_blocked[li].get((ix, iy))
            if owners and not all(o == net for o in owners):
                return False
        return True

    def width_for_net(self, net: str) -> int:
        """Track width in mils for ``net`` via its class (default class
        ``signal``)."""
        return self.rules.width_for_class(
            self.net_classes.get(net, "signal"))

    def class_of(self, net: str) -> str:
        return self.net_classes.get(net, "signal")

    # -- construction ------------------------------------------------------

    @classmethod
    def from_geometry(cls, geometry: dict[str, Any],
                      rules: RouteRules | dict[str, Any] | None,
                      net_classes: dict[str, str] | None = None,
                      grid_pitch_mils: int = DEFAULT_GRID_PITCH_MILS,
                      ) -> "RoutingProblem":
        """Build the problem from the board geometry dict (mils).

        ``net_classes`` maps net name -> class (e.g.
        ``classify_nets(plan).by_net``); unlisted nets are ``signal``.
        Raises :class:`ValueError` on malformed input.
        """
        if not isinstance(geometry, dict):
            raise ValueError("geometry must be a dict")
        if isinstance(rules, dict) or rules is None:
            rules = rules_from_dict(rules)
        pitch = int(grid_pitch_mils)
        if pitch <= 0:
            raise ValueError("grid_pitch_mils must be > 0")

        x1, y1, x2, y2 = _board_bounds(geometry)
        x0 = int(math.floor(x1))
        y0 = int(math.floor(y1))
        nx = int(math.ceil((x2 - x0) / pitch)) + 1
        ny = int(math.ceil((y2 - y0) / pitch)) + 1
        if nx < 2 or ny < 2:
            raise ValueError("board area degenerate")
        if nx * ny > _MAX_CELLS:
            raise ValueError(
                f"grid too large ({nx}x{ny} cells); raise grid_pitch_mils")

        prob = cls(rules, net_classes, x0, y0, nx, ny, pitch)
        all_idx = tuple(range(len(prob.layers)))
        layer_idx = {name.lower(): i for i, name in enumerate(prob.layers)}

        for p in geometry.get("pads") or []:
            cx = float(p.get("x", 0))
            cy = float(p.get("y", 0))
            hw, hh = _pad_half_extents(p)
            lay = str(p.get("layer", "") or "").lower()
            net = str(p.get("net", "") or "") or None
            owner = net  # None (unnetted) blocks every net.
            if lay == "multilayer":
                indices: tuple[int, ...] = all_idx
            elif lay == "keepoutlayer":
                indices, owner = all_idx, None
            elif lay in layer_idx:
                indices = (layer_idx[lay],)
            else:
                continue  # mask/paste/mech artwork: not routing copper
            prob._block_rect(indices, cx, cy, hw, hh, owner)
            if lay != "keepoutlayer":
                prob._mark_pad_cells(indices, cx, cy, hw, hh)
            prob.geoms.append({
                "kind": "rect",
                "layer": None if len(indices) > 1 else indices[0],
                "net": net, "cx": cx, "cy": cy, "hw": hw, "hh": hh,
            })
            if net:
                term = Terminal(
                    x=int(round(cx)), y=int(round(cy)),
                    cell=prob.snap_cell(cx, cy), layers=indices)
                prob.terminals.setdefault(net, []).append(term)

        for t in geometry.get("tracks") or []:
            tx1 = float(t.get("x1", 0))
            ty1 = float(t.get("y1", 0))
            tx2 = float(t.get("x2", 0))
            ty2 = float(t.get("y2", 0))
            thw = float(t.get("width", 10) or 10) / 2.0
            lay = str(t.get("layer", "") or "").lower()
            net = str(t.get("net", "") or "") or None
            owner = net
            if lay == "keepoutlayer":
                indices, owner = all_idx, None
            elif lay in layer_idx:
                indices = (layer_idx[lay],)
            else:
                continue
            prob._block_seg(indices, tx1, ty1, tx2, ty2, thw, owner)
            prob.geoms.append({
                "kind": "seg",
                "layer": None if len(indices) > 1 else indices[0],
                "net": net, "x1": tx1, "y1": ty1, "x2": tx2, "y2": ty2,
                "hw": thw,
            })

        for v in geometry.get("vias") or []:
            vx = float(v.get("x", 0))
            vy = float(v.get("y", 0))
            r = float(v.get("size", 0) or 0) / 2.0
            net = str(v.get("net", "") or "") or None
            prob._block_circle(all_idx, vx, vy, r, net)
            prob.geoms.append({
                "kind": "circle", "layer": None, "net": net,
                "x": vx, "y": vy, "r": r,
            })

        for net in prob.terminals:
            prob.terminals[net].sort(key=lambda t: (t.x, t.y, t.layers))
        return prob

    # -- mutation ----------------------------------------------------------

    def add_route_obstacles(self, net: str,
                            tracks: Iterable[dict[str, Any]],
                            vias: Iterable[dict[str, Any]]) -> None:
        """Register a routed net's copper so later nets avoid it. Tracks
        carry their own ``width``; vias span all layers."""
        all_idx = tuple(range(len(self.layers)))
        layer_idx = {name.lower(): i for i, name in enumerate(self.layers)}
        for t in tracks:
            li = layer_idx[str(t["layer"]).lower()]
            self._block_seg(
                (li,), float(t["x1"]), float(t["y1"]),
                float(t["x2"]), float(t["y2"]),
                float(t["width"]) / 2.0, net)
        for v in vias:
            self._block_circle(
                all_idx, float(v["x"]), float(v["y"]),
                float(v["size"]) / 2.0, net)

    # -- rasterization -----------------------------------------------------
    #
    # Each obstacle is written to BOTH maps: ``blocked`` with the track
    # margin and ``via_blocked`` with the (wider) via margin. A cell is
    # blocked when its center is closer to the obstacle copper than
    # margin + the obstacle's own half-extent.

    def _cells_in_window(self, x_lo: float, y_lo: float,
                         x_hi: float, y_hi: float):
        ix_lo = max(0, int(math.floor((x_lo - self.x0) / self.pitch)))
        ix_hi = min(self.nx - 1, int(math.ceil((x_hi - self.x0) / self.pitch)))
        iy_lo = max(0, int(math.floor((y_lo - self.y0) / self.pitch)))
        iy_hi = min(self.ny - 1, int(math.ceil((y_hi - self.y0) / self.pitch)))
        for ix in range(ix_lo, ix_hi + 1):
            for iy in range(iy_lo, iy_hi + 1):
                yield ix, iy

    def _maps(self) -> tuple[tuple[list, float], ...]:
        return ((self.blocked, self._margin_track),
                (self.via_blocked, self._margin_via))

    def _mark_pad_cells(self, indices: Iterable[int], cx: float, cy: float,
                        hw: float, hh: float) -> None:
        """Record the cells the pad copper actually covers.

        No margin: a cell beside a pad is a fine place for a via, and
        that is where a via belongs when a pad needs one.
        """
        for li in indices:
            for ix, iy in self._cells_in_window(cx - hw, cy - hh,
                                                cx + hw, cy + hh):
                x, y = self.cell_center(ix, iy)
                if abs(x - cx) <= hw and abs(y - cy) <= hh:
                    self.pad_cells[li].add((ix, iy))

    def _block_rect(self, indices: Iterable[int], cx: float, cy: float,
                    hw: float, hh: float, owner: str | None) -> None:
        indices = tuple(indices)
        r_max = max(self._margin_track, self._margin_via)
        for ix, iy in self._cells_in_window(
                cx - hw - r_max, cy - hh - r_max,
                cx + hw + r_max, cy + hh + r_max):
            px, py = self.cell_center(ix, iy)
            d = dist_point_rect(px, py, cx, cy, hw, hh)
            for grid, margin in self._maps():
                if d < margin:
                    for li in indices:
                        grid[li].setdefault((ix, iy), set()).add(owner)

    def _block_seg(self, indices: Iterable[int],
                   x1: float, y1: float, x2: float, y2: float,
                   hw: float, owner: str | None) -> None:
        indices = tuple(indices)
        r_max = hw + max(self._margin_track, self._margin_via)
        for ix, iy in self._cells_in_window(
                min(x1, x2) - r_max, min(y1, y2) - r_max,
                max(x1, x2) + r_max, max(y1, y2) + r_max):
            px, py = self.cell_center(ix, iy)
            d = dist_point_seg(px, py, x1, y1, x2, y2)
            for grid, margin in self._maps():
                if d < hw + margin:
                    for li in indices:
                        grid[li].setdefault((ix, iy), set()).add(owner)

    def _block_circle(self, indices: Iterable[int], x: float, y: float,
                      radius: float, owner: str | None) -> None:
        indices = tuple(indices)
        r_max = radius + max(self._margin_track, self._margin_via)
        for ix, iy in self._cells_in_window(
                x - r_max, y - r_max, x + r_max, y + r_max):
            px, py = self.cell_center(ix, iy)
            d = math.hypot(px - x, py - y)
            for grid, margin in self._maps():
                if d < radius + margin:
                    for li in indices:
                        grid[li].setdefault((ix, iy), set()).add(owner)


def _pad_half_extents(p: dict[str, Any]) -> tuple[float, float]:
    """Pad copper half-extents in mils, axis-aligned. 90/270 rotations
    swap the axes; arbitrary rotations use the enclosing AABB
    (conservative)."""
    hw = float(p.get("x_size", 0) or 0) / 2.0
    hh = float(p.get("y_size", 0) or 0) / 2.0
    rot = float(p.get("rotation", 0) or 0) % 180.0
    if abs(rot - 90.0) < 1e-6:
        return hh, hw
    if abs(rot) < 1e-6:
        return hw, hh
    a = math.radians(rot)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return hw * c + hh * s, hw * s + hh * c


def _board_bounds(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    """Routing area bounds in mils: the bbox if present, else the pad
    extent plus a 200 mil apron."""
    bbox = geometry.get("bbox") or {}
    try:
        x1 = float(bbox["x1"])
        y1 = float(bbox["y1"])
        x2 = float(bbox["x2"])
        y2 = float(bbox["y2"])
        if x2 > x1 and y2 > y1:
            return x1, y1, x2, y2
    except (KeyError, TypeError, ValueError):
        pass
    pads = geometry.get("pads") or []
    xs = [float(p.get("x", 0)) for p in pads]
    ys = [float(p.get("y", 0)) for p in pads]
    if not xs:
        raise ValueError("geometry has neither a usable bbox nor pads")
    return min(xs) - 200, min(ys) - 200, max(xs) + 200, max(ys) + 200


__all__ = [
    "DEFAULT_GRID_PITCH_MILS",
    "RouteRules",
    "RoutingProblem",
    "Terminal",
    "dist_point_rect",
    "dist_point_seg",
    "dist_seg_rect",
    "dist_seg_seg",
    "rules_from_dict",
]
