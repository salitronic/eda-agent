# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Orthogonal wire routing primitives for schematic generation.

Pure-functional helpers used by the executor to draw Manhattan wires
between pin endpoints. Split out of ``executor.py`` to keep that file
focused on orchestration + Altium IPC.

Layers in this module:

- **Pin stubs** -- ``pin_direction_vector``, ``adaptive_stub_length``,
  ``stub_endpoints``. Compute where the first wire segment leaves a
  pin and how long it extends. Adaptive clipping prevents the stub
  from drawing through a neighbouring component's body when parts
  sit at the placement engine's minimum 950-mil center spacing.

- **Segment-vs-rectangle geometry** -- ``segment_crosses_rect``,
  ``_l_path_collisions``, ``_path_collisions``, ``_path_length``.
  Used by both the router and the offline audit functions.

- **Two-point routing** -- ``route_l_path``, ``route_s_bend``.
  ``route_l_path`` tries both L-orderings and falls back to a
  3-segment S-bend (mid-coordinate from obstacle edges) when both
  L-paths collide.

- **Multi-pin routing** -- ``route_signal_pins``. Picks the cheaper
  of chain (consecutive L-paths in x- or y-sort order) and star
  (every pin to a shared hub) topology, with the hub candidates
  including the centroid, each pin, obstacle-pushed centroids, and
  the four corners of the stub-ends bounding box.

All segments are axis-aligned; the router never emits diagonals.
Coordinates are mils, snapped to a 100-mil grid by the caller.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Pin stubs
# ---------------------------------------------------------------------------

# Stub wire length between a pin's electrical hot end and the net label /
# power port that attaches to it. ERC reports "Floating net labels" when a
# label sits exactly on a pin endpoint without an intervening wire, so
# every label/port gets pulled out along the pin's vector by this much.
#
# THE COMMENT ABOVE SAID "100-mil stub" WHILE THIS SAID 300 for as long
# as both have existed. The value is what runs; the text has been fixed
# to stop asserting otherwise.
#
# 100 WOULD SCORE BETTER AND IS NOT A TUNING CHANGE. Measured on 10565
# human wire segments leaving a pin across the KiCad demo sheets, the
# median is 250 mils but the distribution is bimodal with modes at 50
# (2325 segments) and 100 (1638), together 37% of them: people turn
# much sooner than 300. Replicated on 79634 stubs from 172 public
# hardware repositories: median 200, with the same three modes at 50,
# 100 and 150. The demo sheets and real production boards agree.
#
# It costs the engine, because a stub is paid
# twice whenever a route doubles back -- on rectifier, signal_in leaves
# a left-facing pin 300 mils leftward and then runs east for the whole
# span, 2500 mils where 1900 was available. Over eight sheets, 300 to
# 100 cut wire 133000 to 110400 mils (17%) and the score 3036 to 2849
# with no change in crossings. (200 was worse than either, buying an
# extra crossing, so the response is not monotonic.)
#
# It is left at 300 because of what 100 does to the code around it.
# _stub_endpoints clips a stub that would hit an obstacle and floors
# the result at _STUB_MIN_LEN_MILS with _STUB_CLEARANCE_MILS to spare,
# so clipping can only yield a length between the floor and this
# default, which needs default > min + clearance = 150. At 300 that
# range is 150 to 300 mils. At 100 it is EMPTY: every path returns 100,
# the obstacle argument stops mattering, and an adaptive routine
# quietly becomes a constant. Six tests in test_executor assert the
# clipping behaviour that would become unreachable.
#
# Taking the 17% therefore means deciding that stub clipping is not
# worth having, or moving the floor and clearance with it. That is a
# design call, not a constant to nudge.
#
# Worth knowing while deciding: pipeline._REPAIR_STUB_LEN_MILS is 200
# and its own comment calls that "short on purpose ... the pin, tick,
# glyph the hand-drawn convention uses, not a route". The repair path
# already reaches for the convention the human measurement above
# supports; the routing path does not, and the two have never agreed.
_STUB_LEN_MILS = 300
# Minimum stub length when an obstacle clips the default 300-mil
# extension. ERC flags net labels and power ports that sit exactly on
# a pin endpoint, so even a clipped stub must leave SOME room.
_STUB_MIN_LEN_MILS = 100
# Margin between the clipped stub's far end and the closest obstacle
# so the wire doesn't visually kiss the next component's body.
_STUB_CLEARANCE_MILS = 50


def _pin_direction_vector(orientation: int) -> tuple[int, int]:
    """Map Altium's TRotationBy90 pin orientation to a unit vector.

    0=right (+x), 1=up (+y), 2=left (-x), 3=down (-y). Matches what
    Pascal's ``Gen_GetSchComponentPins`` returns from ``Pin.Orientation``.
    Unknown values fall through as (1, 0) so the stub still draws.
    """
    if orientation == 1:
        return (0, 1)
    if orientation == 2:
        return (-1, 0)
    if orientation == 3:
        return (0, -1)
    return (1, 0)


def _adaptive_stub_length(
    pin_x: int,
    pin_y: int,
    dx: int,
    dy: int,
    obstacles: list[tuple[int, int, int, int]],
    base_length: int = _STUB_LEN_MILS,
    hard: bool = False,
) -> int:
    """Maximum stub length that doesn't enter another component's bbox.

    Walks from ``(pin_x, pin_y)`` along ``(dx, dy)`` and finds the
    first obstacle (other than the pin's owner bbox) the ray would
    enter. Returns the distance to that obstacle minus a small
    clearance, clipped to ``[_STUB_MIN_LEN_MILS, base_length]``.
    The owner bbox (which contains the pin) is excluded by checking
    "pin inside obstacle" -- the stub may legitimately exit the
    owner's body.

    ``base_length`` defaults to ``_STUB_LEN_MILS`` (300). Callers can
    request a longer base when staggering multiple same-direction
    stubs from the same component so their bends don't share a
    column.

    ``hard`` makes ``_STUB_MIN_LEN_MILS`` yield. The floor exists so a stub
    stays long enough to hang a label on, and against a BODY that is the
    right trade: a stub reaching a little way into a neighbour's courtyard
    is untidy, not wrong. Against a PIN it is wrong: the stub ends on
    another net's pin and Altium merges the two nets. A pin exactly one grid
    step away is the common case, and the floor lands the stub precisely on
    it. With ``hard`` the length is allowed down to 0, and a zero-length
    stub simply means the pin's own hotspot is the stub end.
    """
    if (dx, dy) == (0, 0):
        return base_length
    max_len = base_length
    for rx1, ry1, rx2, ry2 in obstacles:
        # Skip the obstacle that contains the pin (owner).
        if rx1 <= pin_x <= rx2 and ry1 <= pin_y <= ry2:
            continue
        # Ray-vs-rectangle entry distance for axis-aligned rays.
        if dx > 0:  # +x
            if pin_y < ry1 or pin_y > ry2 or rx1 <= pin_x:
                continue
            entry = rx1 - pin_x
        elif dx < 0:  # -x
            if pin_y < ry1 or pin_y > ry2 or rx2 >= pin_x:
                continue
            entry = pin_x - rx2
        elif dy > 0:  # +y
            if pin_x < rx1 or pin_x > rx2 or ry1 <= pin_y:
                continue
            entry = ry1 - pin_y
        else:  # dy < 0
            if pin_x < rx1 or pin_x > rx2 or ry2 >= pin_y:
                continue
            entry = pin_y - ry2
        floor = 0 if hard else _STUB_MIN_LEN_MILS
        clipped = max(floor, entry - _STUB_CLEARANCE_MILS)
        # SNAPPED DOWN to the wire grid. ``entry`` is measured to a body
        # EDGE, and a symbol's graphics are drawn in millimetres, so that
        # edge need not sit on the grid: the clipped length inherits the
        # offset and the stub END becomes an off-grid wire endpoint,
        # which is how a connection silently fails to form in Altium.
        #
        # Found on real hardware, not on the demos. The KiCad demo set
        # produced one off-grid case, in the star-hub generator; a sweep
        # over 203 sheets from public repositories produced 38 more, and
        # tracing them landed here. Downward, so rounding can only add
        # clearance, never eat into it.
        clipped = max(floor, _grid_low(clipped))
        if clipped < max_len:
            max_len = clipped
    return max_len


def _stub_endpoints(
    pin_x: int,
    pin_y: int,
    orientation: int,
    pin_length_mils: int,  # retained for ABI compat; ignored
    obstacles: Optional[list[tuple[int, int, int, int]]] = None,
    extra_length_mils: int = 0,
    hard_obstacles: Optional[list[tuple[int, int, int, int]]] = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Compute (stub_start, stub_end) for a pin.

    ``ISch_Pin.Location`` returns the ELECTRICAL endpoint of the pin
    (the point where a wire attaches), NOT the body-side end. Verified
    empirically: U1 pin 1 BOOT of a placed TPS54331D symbol returned
    location (3500, 4400) when the IC body's left edge was at x=3700;
    the leftmost (electrical) end of the pin graphic at x=3500 is what
    Pin.Location reports.

    The stub starts AT Pin.Location and extends outward along the
    pin's orientation vector. The extension length is
    ``_STUB_LEN_MILS`` (300 mil) by default, plus
    ``extra_length_mils`` for staggering when several same-direction
    stubs leave the same component (so their bends don't share a
    column). The total is then clipped by ``_adaptive_stub_length``
    when an ``obstacles`` list is supplied so the stub doesn't draw
    through a neighbouring component's body at the placement engine's
    minimum 950-mil center spacing.
    """
    dx, dy = _pin_direction_vector(orientation)
    hot_x = pin_x
    hot_y = pin_y
    base = _STUB_LEN_MILS + max(0, extra_length_mils)
    length = (
        _adaptive_stub_length(
            pin_x, pin_y, dx, dy, obstacles, base_length=base
        )
        if obstacles
        else base
    )
    # Obstacles the minimum length must not override: another net's pins.
    # Clipping against a body may stop at the floor, clipping against a pin
    # may not, so the two are measured separately and the shorter wins.
    if hard_obstacles:
        length = min(length, _adaptive_stub_length(
            pin_x, pin_y, dx, dy, hard_obstacles, base_length=base, hard=True))
    end_x = hot_x + dx * length
    end_y = hot_y + dy * length
    return ((hot_x, hot_y), (end_x, end_y))


# ---------------------------------------------------------------------------
# Segment-vs-rectangle geometry
# ---------------------------------------------------------------------------


def _segment_crosses_rect(
    x1: int, y1: int, x2: int, y2: int,
    rx1: int, ry1: int, rx2: int, ry2: int,
) -> bool:
    """True iff axis-aligned segment (x1,y1)->(x2,y2) crosses the interior
    of the axis-aligned rectangle [rx1,rx2] x [ry1,ry2]. Endpoints sitting
    on the boundary count as crossings only when the segment continues
    into the interior.
    """
    rx1, rx2 = (rx1, rx2) if rx1 <= rx2 else (rx2, rx1)
    ry1, ry2 = (ry1, ry2) if ry1 <= ry2 else (ry2, ry1)
    if y1 == y2:  # horizontal segment
        if y1 <= ry1 or y1 >= ry2:
            return False
        seg_x_lo, seg_x_hi = min(x1, x2), max(x1, x2)
        return seg_x_lo < rx2 and seg_x_hi > rx1
    if x1 == x2:  # vertical segment
        if x1 <= rx1 or x1 >= rx2:
            return False
        seg_y_lo, seg_y_hi = min(y1, y2), max(y1, y2)
        return seg_y_lo < ry2 and seg_y_hi > ry1
    return False  # only orthogonal segments here


def _l_path_collisions(
    x1: int, y1: int, x2: int, y2: int,
    horiz_first: bool,
    obstacles: list[tuple[int, int, int, int]],
    skip_at: tuple[tuple[int, int], ...] = (),
) -> int:
    """Count how many obstacle rects an L-path (x1,y1)->(x2,y2) crosses.

    Obstacles are (rx1, ry1, rx2, ry2) bboxes. Skip rects that contain
    either endpoint (those are the pin's own component or the centroid's
    host part -- wires must enter / leave SOME bbox to connect).
    """
    if horiz_first:
        segs = [(x1, y1, x2, y1), (x2, y1, x2, y2)]
    else:
        segs = [(x1, y1, x1, y2), (x1, y2, x2, y2)]
    # NEITHER TEST DEPENDS ON THE SEGMENT, and one depends on neither
    # loop. The skip_at check reads only the path's own endpoints, so
    # when it holds the answer is zero and the nested walk over segments
    # and obstacles discovers that the expensive way.
    #
    # Same defect as _path_collisions below, which was hoisted first;
    # this is the twin that was missed on that pass.
    if (x1, y1) in skip_at or (x2, y2) in skip_at:
        return 0

    # Obstacles owning an endpoint are skipped, and ownership is a
    # property of the obstacle and the path, not of the segment.
    live = [
        (rx1, ry1, rx2, ry2) for rx1, ry1, rx2, ry2 in obstacles
        if not ((rx1 <= x1 <= rx2 and ry1 <= y1 <= ry2)
                or (rx1 <= x2 <= rx2 and ry1 <= y2 <= ry2))
    ]

    n = 0
    for (sx1, sy1, sx2, sy2) in segs:
        for rx1, ry1, rx2, ry2 in live:
            if _segment_crosses_rect(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2):
                n += 1
                break
    return n


def _path_collisions(
    segs: list[tuple[int, int, int, int]],
    obstacles: list[tuple[int, int, int, int]],
    skip_endpoints: tuple[tuple[int, int], ...],
) -> int:
    """Count obstacles crossed by an arbitrary axis-aligned path.

    ``skip_endpoints`` lists points (the pin's home, the centroid) that
    sit inside an obstacle by construction; obstacles containing any
    of those points don't count as a crossing.
    """
    # HOISTED OUT OF THE SEGMENT LOOP. Whether an obstacle contains one
    # of the skip points depends on the OBSTACLE alone, so evaluating it
    # per segment-obstacle pair recomputed the same answer once for
    # every segment.
    #
    # MEASURED on a 2-part, 20-net sheet: 4.5 million calls to any() and
    # 13.5 million generator steps, 41% of an 11-second layout. Filtering
    # once is the same arithmetic, done as many times as there are
    # obstacles instead of obstacles times segments.
    # NORMALISED HERE, not per pair. _segment_crosses_rect orders each
    # rectangle's corners defensively on every call; doing it once per
    # obstacle is the same arithmetic done as many times as there are
    # obstacles instead of obstacles times segments.
    live = []
    for rx1, ry1, rx2, ry2 in obstacles:
        if rx1 > rx2:
            rx1, rx2 = rx2, rx1
        if ry1 > ry2:
            ry1, ry2 = ry2, ry1
        if any(rx1 <= ex <= rx2 and ry1 <= ey <= ry2
               for ex, ey in skip_endpoints):
            continue
        live.append((rx1, ry1, rx2, ry2))

    # INLINED, and only for the call count. _segment_crosses_rect is four
    # comparisons wrapped in a function, and this loop reached it 226
    # MILLION times on one demo sheet, where the call overhead dwarfs the
    # arithmetic. The conditions below are that function's, unrolled per
    # orientation so the branch is taken once per segment rather than
    # once per pair. Kept in sync by test_router_collisions, which checks
    # the two agree on random geometry.
    n = 0
    for sx1, sy1, sx2, sy2 in segs:
        if sy1 == sy2:                                   # horizontal
            lo, hi = (sx1, sx2) if sx1 <= sx2 else (sx2, sx1)
            for rx1, ry1, rx2, ry2 in live:
                if ry1 < sy1 < ry2 and lo < rx2 and hi > rx1:
                    n += 1
                    break
        elif sx1 == sx2:                                 # vertical
            lo, hi = (sy1, sy2) if sy1 <= sy2 else (sy2, sy1)
            for rx1, ry1, rx2, ry2 in live:
                if rx1 < sx1 < rx2 and lo < ry2 and hi > ry1:
                    n += 1
                    break
    return n


def _path_length(segs: list[tuple[int, int, int, int]]) -> int:
    """Manhattan length of a path."""
    return sum(abs(sx2 - sx1) + abs(sy2 - sy1) for sx1, sy1, sx2, sy2 in segs)


# ---------------------------------------------------------------------------
# Two-point routing
# ---------------------------------------------------------------------------


_S_BEND_MARGIN_MILS = 100  # one grid cell of clearance past an obstacle edge
# Wire bends land on this grid. A body box can be off-grid; a wire must
# not be, or Altium fails to make the connection at that endpoint.
_BEND_GRID_MILS = 50


def _route_s_bend(
    x1: int, y1: int, x2: int, y2: int,
    obstacles: list[tuple[int, int, int, int]],
) -> Optional[list[tuple[int, int, int, int]]]:
    """3-segment S-bend that tries to route AROUND obstacles.

    Two variants:
      - HVH: horizontal at y1, vertical at ``x_mid``, horizontal at y2.
      - VHV: vertical at x1, horizontal at ``y_mid``, vertical at x2.

    For each variant we try a set of candidate mid-coordinates: the
    geometric midpoint plus the edges of every obstacle (with a
    margin), since detours typically need to pass just before or just
    after an obstacle. Returns the shortest fully-clean route, or
    ``None`` if no clean S-bend exists -- caller falls back to the
    less-bad L-path.
    """
    if x1 == x2 or y1 == y2:
        return None  # endpoints already share an axis -> single segment
    skip = ((x1, y1), (x2, y2))

    # FILTERED ONCE, HERE. Both obstacles and skip are the same for every
    # candidate mid-coordinate below, and this routine tries one per
    # obstacle edge, so letting _path_collisions re-filter each time made
    # the filtering itself quadratic in the obstacle count for every net.
    # The candidates are then measured against an already-clean list.
    live = [
        (rx1, ry1, rx2, ry2) for rx1, ry1, rx2, ry2 in obstacles
        if not any(rx1 <= ex <= rx2 and ry1 <= ey <= ry2 for ex, ey in skip)
    ]

    candidates: list[tuple[int, list[tuple[int, int, int, int]]]] = []

    # IN-SPAN CANDIDATES ARE TRIED FIRST, AND SETTLE IT WHEN ONE IS
    # CLEAN. An HVH route measures |x1-xm| + |y1-y2| + |xm-x2|, which is
    # exactly the Manhattan minimum for ANY xm between the endpoints and
    # strictly greater outside; VHV is the same statement in y. So an
    # out-of-span detour can never beat a clean in-span route, and
    # testing one when an in-span route is already clean is work whose
    # result is known in advance.
    #
    # This routine tries one candidate per obstacle EDGE and tests each
    # against every obstacle, so the saving is quadratic in the obstacle
    # count for the common case. It is not an approximation: the set the
    # winner is chosen from is unchanged whenever any in-span route is
    # clean, and identical to before when none is.
    x_lo, x_hi = (x1, x2) if x1 <= x2 else (x2, x1)
    y_lo, y_hi = (y1, y2) if y1 <= y2 else (y2, y1)

    def hvh(x_mid):
        return [(x1, y1, x_mid, y1), (x_mid, y1, x_mid, y2),
                (x_mid, y2, x2, y2)]

    def vhv(y_mid):
        return [(x1, y1, x1, y_mid), (x1, y_mid, x2, y_mid),
                (x2, y_mid, x2, y2)]

    # Bend candidates derived from an obstacle EDGE inherit that edge's
    # coordinate, and a symbol's drawn body box is under no obligation
    # to sit on the wire grid: measured on royer1, one bend landed at
    # x=4480 and put three wire coordinates off a 25-mil grid, the only
    # off-grid geometry the engine produced across 2016 coordinates on
    # 21 sheets. The human sheets have none, and in Altium an off-grid
    # endpoint is how a connection silently fails to form.
    #
    # Snapped AWAY from the obstacle, never toward it, so rounding can
    # only add clearance.
    def _nearest(v: int) -> int:
        return int(round(v / _BEND_GRID_MILS)) * _BEND_GRID_MILS

    # The geometric midpoint is off-grid whenever the two ends sum to an
    # odd number, which they do as soon as one end came from a body edge:
    # symbol graphics are drawn in millimetres and 43 body edges on
    # royer1 alone sit off a 25-mil grid. Snapping only the obstacle
    # candidates below moved the problem here rather than fixing it.
    x_mids: set[int] = {_nearest((x1 + x2) // 2)}
    y_mids: set[int] = {_nearest((y1 + y2) // 2)}
    for rx1, ry1_o, rx2, ry2_o in obstacles:
        x_mids.add(_grid_low(rx1 - _S_BEND_MARGIN_MILS))
        x_mids.add(_grid_high(rx2 + _S_BEND_MARGIN_MILS))
        y_mids.add(_grid_low(ry1_o - _S_BEND_MARGIN_MILS))
        y_mids.add(_grid_high(ry2_o + _S_BEND_MARGIN_MILS))

    for in_span in (True, False):
        for x_mid in x_mids:
            if x_mid == x1 or x_mid == x2:
                continue
            if (x_lo <= x_mid <= x_hi) != in_span:
                continue
            segs = hvh(x_mid)
            if _path_collisions(segs, live, ()) == 0:
                candidates.append((_path_length(segs), segs))
        for y_mid in y_mids:
            if y_mid == y1 or y_mid == y2:
                continue
            if (y_lo <= y_mid <= y_hi) != in_span:
                continue
            segs = vhv(y_mid)
            if _path_collisions(segs, live, ()) == 0:
                candidates.append((_path_length(segs), segs))
        if candidates:
            break          # in-span is optimal; out-of-span cannot win

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _route_l_path(
    x1: int, y1: int, x2: int, y2: int,
    obstacles: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Manhattan route from (x1, y1) to (x2, y2) avoiding obstacles.

    Strategy:
      1. Try both L-orderings (H-then-V, V-then-H); if either is
         collision-free, return it.
      2. If both L-paths collide, try a 3-segment S-bend that routes
         around the obstacles via a mid-coordinate chosen from
         obstacle edges and the geometric midpoint.
      3. Fall back to the less-bad L-path if no clean S-bend exists.

    Step 2 catches the common "two adjacent components both block
    H-first AND V-first" case without escalating to a full A* router.
    """
    if x1 == x2 and y1 == y2:
        return []
    if x1 == x2 or y1 == y2:
        return [(x1, y1, x2, y2)]
    h_first = _l_path_collisions(x1, y1, x2, y2, True, obstacles)
    v_first = _l_path_collisions(x1, y1, x2, y2, False, obstacles)
    if h_first == 0 or v_first == 0:
        if v_first < h_first:
            return [(x1, y1, x1, y2), (x1, y2, x2, y2)]
        return [(x1, y1, x2, y1), (x2, y1, x2, y2)]
    # Both L-paths collide -- try the S-bend rescue.
    s_segs = _route_s_bend(x1, y1, x2, y2, obstacles)
    if s_segs is not None:
        return s_segs
    # Final fallback: the less-bad L.
    if v_first < h_first:
        return [(x1, y1, x1, y2), (x1, y2, x2, y2)]
    return [(x1, y1, x2, y1), (x2, y1, x2, y2)]


# ---------------------------------------------------------------------------
# Multi-pin routing
# ---------------------------------------------------------------------------


def _count_bends(segs: list[tuple[int, int, int, int]]) -> int:
    """Number of CORNER points in a segment set (the readability metric).

    A corner is a point where a horizontal and a vertical segment meet at a
    shared ENDPOINT -- the wire visibly changes direction. A pin tapping into
    a trunk mid-span is a T-junction, not a corner, and is correctly NOT
    counted (the trunk is one long segment, so the tap point is interior to
    it). Fewer corners reads cleaner.
    """
    incident: dict[tuple[int, int], list[bool]] = {}
    for x1, y1, x2, y2 in segs:
        horiz = y1 == y2
        for p in ((x1, y1), (x2, y2)):
            incident.setdefault(p, []).append(horiz)
    bends = 0
    for flags in incident.values():
        if any(flags) and not all(flags):
            bends += 1
    return bends


def _net_obstacle_crossings(
    segs: list[tuple[int, int, int, int]],
    stub_ends: list[tuple[int, int]],
    obstacles: list[tuple[int, int, int, int]],
) -> int:
    """Segments crossing a component body, exempting a segment whose own
    endpoint is a net pin sitting inside that body (a legitimate stub start)."""
    pin_set = set(stub_ends)
    count = 0
    for sx1, sy1, sx2, sy2 in segs:
        for rx1, ry1, rx2, ry2 in obstacles:
            exempt = any(
                (px, py) in pin_set and rx1 <= px <= rx2 and ry1 <= py <= ry2
                for (px, py) in ((sx1, sy1), (sx2, sy2))
            )
            if exempt:
                continue
            if _segment_crosses_rect(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2):
                count += 1
                break
    return count


def _grid_low(v: int) -> int:
    """Round DOWN to the wire grid: away from an obstacle on its low side."""
    return (v // _BEND_GRID_MILS) * _BEND_GRID_MILS


def _grid_high(v: int) -> int:
    """Round UP to the wire grid: away from an obstacle on its high side."""
    return -((-v) // _BEND_GRID_MILS) * _BEND_GRID_MILS


def _trunk_candidates(
    stub_ends: list[tuple[int, int]],
) -> list[list[tuple[int, int, int, int]]]:
    """Trunk-and-stub (single-spine rectilinear Steiner) routings.

    A straight TRUNK at the MEDIAN coordinate (the 1-D Steiner-optimal spine
    position) with each pin tapping in via one perpendicular stub. Returns the
    horizontal-trunk and vertical-trunk variants; the caller scores both
    against obstacles and the other topologies. This is the canonical clean
    schematic routing for a shared net -- minimal corners, short total wire.
    """
    xs = [p[0] for p in stub_ends]
    ys = [p[1] for p in stub_ends]

    def _median(vals: list[int]) -> int:
        s = sorted(vals)
        return (s[len(s) // 2] // 100) * 100

    out: list[list[tuple[int, int, int, int]]] = []
    # Horizontal trunk at median y; vertical stubs.
    ty = _median(ys)
    h: list[tuple[int, int, int, int]] = [(min(xs), ty, max(xs), ty)]
    h += [(x, y, x, ty) for (x, y) in stub_ends if y != ty]
    out.append(h)
    # Vertical trunk at median x; horizontal stubs.
    tx = _median(xs)
    v: list[tuple[int, int, int, int]] = [(tx, min(ys), tx, max(ys))]
    v += [(x, y, tx, y) for (x, y) in stub_ends if x != tx]
    out.append(v)
    return out


def _route_signal_pins(
    stub_ends: list[tuple[int, int]],
    obstacles: list[tuple[int, int, int, int]] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Manhattan-route wires connecting the stub ends of pins on a signal net.

    Within-block schematic convention: connect same-net pins with real
    wires rather than relying on per-pin net labels. Power and ground
    nets are exempt (the rail symbology is the connection).

    For 2 pins: a 2-segment L-path between the two stub ends.
    For 3+ pins: try CHAIN (consecutive in x-sort and y-sort) and STAR
    (every pin to a shared hub) topologies; pick whichever has fewer
    bbox crossings. Star hub candidates: each pin, the centroid, the
    centroid pushed out of any obstacle it lands inside, and the four
    corners of the stub-ends bounding box.

    All segments are axis-aligned (horizontal OR vertical), grid-snapped.
    When ``obstacles`` (component-body bboxes) are supplied each L-path
    picks the ordering that crosses fewer of them.
    """
    obstacles = obstacles or []
    if len(stub_ends) < 2:
        return []
    segs: list[tuple[int, int, int, int]] = []
    if len(stub_ends) == 2:
        (x1, y1), (x2, y2) = stub_ends
        segs.extend(_route_l_path(x1, y1, x2, y2, obstacles))
        return segs

    # 3+ pins: enumerate CHAIN (consecutive pins), STAR (shared hub) and
    # TRUNK (median spine, pins tap in) topologies, then pick the one with the
    # fewest body crossings, then the fewest CORNERS (the readability metric),
    # then the shortest wire. Trunk-and-stub is the canonical clean schematic
    # form and usually wins; chain/star are kept because one of them can route
    # cleanly around obstacles that a straight trunk would cut through.
    candidate_sets: list[list[tuple[int, int, int, int]]] = []

    # Chain in x-then-y and y-then-x pin orders.
    by_x = sorted(stub_ends, key=lambda p: (p[0], p[1]))
    by_y = sorted(stub_ends, key=lambda p: (p[1], p[0]))
    for chain in (by_x, by_y):
        cs: list[tuple[int, int, int, int]] = []
        for i in range(len(chain) - 1):
            cs.extend(_route_l_path(
                chain[i][0], chain[i][1], chain[i + 1][0], chain[i + 1][1],
                obstacles))
        candidate_sets.append(cs)

    # Star hubs: centroid, each pin, centroid pushed out of any obstacle it
    # sits in, and the stub-ends bounding-box corners (a wrap-around fallback).
    raw_cx = (sum(p[0] for p in stub_ends) // len(stub_ends) // 100) * 100
    raw_cy = (sum(p[1] for p in stub_ends) // len(stub_ends) // 100) * 100
    hubs: list[tuple[int, int]] = [(raw_cx, raw_cy), *stub_ends]
    for rx1, ry1, rx2, ry2 in obstacles:
        if rx1 < raw_cx < rx2 and ry1 < raw_cy < ry2:
            # Snapped off the obstacle EDGE, which is body geometry and
            # need not sit on the wire grid: symbol graphics are drawn
            # in millimetres, and royer1 alone has 43 off-grid body
            # edges. An unsnapped hub here was the last source of
            # off-grid wire coordinates the engine produced, and it
            # survived snapping the same expression in _best_s_bend
            # because this is a different generator.
            hubs += [(_grid_low(rx1 - 100), raw_cy),
                     (_grid_high(rx2 + 100), raw_cy),
                     (raw_cx, _grid_low(ry1 - 100)),
                     (raw_cx, _grid_high(ry2 + 100))]
    bb_xmin = (min(p[0] for p in stub_ends) // 100) * 100
    bb_xmax = (max(p[0] for p in stub_ends) // 100) * 100
    bb_ymin = (min(p[1] for p in stub_ends) // 100) * 100
    bb_ymax = (max(p[1] for p in stub_ends) // 100) * 100
    hubs += [(bb_xmin, bb_ymin), (bb_xmax, bb_ymin),
             (bb_xmin, bb_ymax), (bb_xmax, bb_ymax)]
    for (hx, hy) in hubs:
        spokes: list[tuple[int, int, int, int]] = []
        for (x, y) in stub_ends:
            if (x, y) != (hx, hy):
                spokes.extend(_route_l_path(x, y, hx, hy, obstacles))
        if spokes:
            candidate_sets.append(spokes)

    # Trunk-and-stub (median spine), horizontal and vertical.
    candidate_sets.extend(_trunk_candidates(stub_ends))

    best_key: tuple[int, int, int] | None = None
    best_segs: list[tuple[int, int, int, int]] = []
    for cand in candidate_sets:
        if not cand:
            continue
        key = (
            _net_obstacle_crossings(cand, stub_ends, obstacles),
            _count_bends(cand),
            _path_length(cand),
        )
        if best_key is None or key < best_key:
            best_key = key
            best_segs = cand
    return best_segs


__all__ = [
    "_STUB_LEN_MILS",
    "_STUB_MIN_LEN_MILS",
    "_STUB_CLEARANCE_MILS",
    "_S_BEND_MARGIN_MILS",
    "_adaptive_stub_length",
    "_l_path_collisions",
    "_path_collisions",
    "_path_length",
    "_pin_direction_vector",
    "_route_l_path",
    "_route_s_bend",
    "_route_signal_pins",
    "_segment_crosses_rect",
    "_stub_endpoints",
]
