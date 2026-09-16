# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Force-directed placement + audit-aware hard-shove pass.

Split out of ``layout.py`` so the spring/repulsion solver and its
post-pass collision repair live in one focused module. The orchestrator
in ``layout.py`` calls into here when a plan has no anchor roles (the
fallback path for "constellation"-style graphs without clear signal
flow); Sugiyama placement covers anchored plans.

Layers:

- **Placement primitives** -- ``PlacedPart`` dataclass, sheet
  constants, ``_pin_count_per_part``, ``_bbox_half``, ``_mass``,
  ``_rotation_for_part``. Shared with ``layout.py``'s orchestrator
  and audit functions.
- **Plan-derived inputs** -- ``_zone_role``, ``_edge_for_part``,
  ``_net_members``, ``_connected_pairs``, ``_zone_for_refdes``,
  ``_initial_positions``.
- **Spring/repulsion solver** -- ``_force_directed_layout``.
  Iterative; converges in ``_MAX_ITERATIONS`` with cooling.
- **Hard-shove pass** -- ``_overlap_pair``, ``_shove_split``,
  ``_hard_shove_pass``. Two-phase: float-space Jacobi shove, then a
  post-snap integer-grid shove to absorb the rounding nudge.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from eda_agent.design.plan import DesignPlan, Net, Part, Zone


# Sheet: A4 landscape, usable area starting from a small margin.
SHEET_ORIGIN_X_MILS = 1000
SHEET_ORIGIN_Y_MILS = 1000
SHEET_MAX_X_MILS = 10500
SHEET_MAX_Y_MILS = 7500
#: A4 as this package measures it. The constants above are A4 less the
#: frame margins, and every other paper size is offset by the same absolute
#: amounts, so A4 reproduces them exactly.
_A4_WIDTH_MILS = 11500
_A4_HEIGHT_MILS = 7600


def sheet_bounds(plan, sheet_name=None) -> tuple[int, int, int, int]:
    """``(min_x, min_y, max_x, max_y)`` for the sheet the plan declares.

    The module constants are A4 less a margin, and every placement pass
    clamped to them whatever paper the plan asked for. MEASURED on the
    corpus: 272 of 1465 sheets (19%) are larger than A4 -- 166 A3, 27 B, 24
    A2, 5 A1 -- and every one of them was confined to the A4 window.

    That is not only wasted room. On an A3 RF board carrying six modules
    7600 mils wide, the A4 window leaves a usable band 1900 mils across, so
    the parts CANNOT be separated and the shove left four of them stacked
    exactly on top of one another; the same sheet drawn by hand has none.

    Falls back to the constants when the plan says nothing, so a caller
    that does not pass a plan behaves exactly as before.
    """
    try:
        sheets = list(getattr(plan, "sheets", ()) or ())
        chosen = None
        for sh in sheets:
            if sheet_name is None or sh.name == sheet_name:
                chosen = sh
                break
        if chosen is None:
            chosen = sheets[0] if sheets else None
        if chosen is None:
            raise ValueError
        from eda_agent.design.canvas import Sheet as _CanvasSheet

        dims = _CanvasSheet(name=chosen.name, size=chosen.size)
        w, h = int(dims.width_mils), int(dims.height_mils)
        if w <= 0 or h <= 0:
            raise ValueError
    except Exception:                   # noqa: BLE001 - best effort
        return (SHEET_ORIGIN_X_MILS, SHEET_ORIGIN_Y_MILS,
                SHEET_MAX_X_MILS, SHEET_MAX_Y_MILS)
    # THE SAME RULE SUGIYAMA ALREADY USED. ``sugiyama._layout_max`` had
    # been sheet-aware all along, so the base placer sized to the paper
    # while this module did not. A flat margin here kept A4 identical but
    # disagreed with it everywhere else (y to 10690 on A3 where sugiyama
    # allows 11590), and two notions of "the sheet" in one pipeline lets
    # the placer put a part exactly where the shove then drags it back.
    # A4 reproduces the constants by construction; A5 correctly gets
    # SMALLER bounds, which a floor at the constants would not.
    return (SHEET_ORIGIN_X_MILS, SHEET_ORIGIN_Y_MILS,
            w - (_A4_WIDTH_MILS - SHEET_MAX_X_MILS),
            h - (_A4_HEIGHT_MILS - SHEET_MAX_Y_MILS))

# Snap grid for final coordinates. Altium's default schematic snap is
# 100 mil; sticking to it keeps placed pins on Altium's electrical grid.
SNAP_GRID_MILS = 100

# Force-directed parameters. Tuned so connected parts settle ~2200 mils
# centre-to-centre and unconnected ones stay well separated on A4.
_SPRING_K = 0.05           # spring stiffness
_SPRING_REST_MILS = 1400   # ideal centre-to-centre for parts sharing a net
# Pin-attractor rest length: how far a discrete settles from the IC pin it
# wires to. Short, so the part hugs its pin's SIDE of the IC (the repulsion
# term keeps it from landing on the body).
_SPRING_REST_PIN_MILS = 500
# Pin attractors are stiffer than the generic centroid spring so the
# which-side-of-the-IC signal beats the diffuse repulsion that otherwise
# sprawls the constellation. (The centroid spring stays soft so unrelated
# parts still spread out.) FD is chaotic in this stiffness, so the pipeline's
# best-of sweeps a few values and scores each rather than trusting one.
_SPRING_K_PIN = 0.08
_REPEL_K = 6_000_000.0     # repulsion strength (Coulomb-style 1/r^2)
_REPEL_CUTOFF_MILS = 3000  # ignore repulsion beyond this distance
_EDGE_PULL_K = 0.35        # bias force toward the chosen sheet edge
_BOUNDARY_K = 0.25         # corrective force pulling parts back into the sheet
_DAMPING = 0.82
_DT = 1.0
_COOLING = 0.995
_MAX_ITERATIONS = 500

# Hard-shove (audit-aware second pass) parameters.
# The solver above is a local-minimum-seeker; on dense plans (e.g. the
# 14-part buck) the converged state still contains pairwise bbox
# overlaps. The shove pass below runs after convergence, audits every
# part pair against `_bbox_half(pin_count)`, and pushes overlapping
# pairs apart along their minimum-overlap axis until the audit is
# clean (or the cap is hit). This is what physical place-and-route
# tools do as a post-pass; spring relaxation alone is too soft at
# close range.
_SHOVE_MAX_ROUNDS = 50
# Extra clearance added on top of bbox-sum so the audit's own margin
# (OVERLAP_MARGIN_MILS == 25) doesn't immediately re-flag a kissing
# pair. A few mils of slop is cheaper than re-running the solver.
_SHOVE_CLEARANCE_MILS = 50

#: Room to leave BETWEEN two drawn bodies, mils. MEASURED over 528 human
#: sheets and 103802 part pairs: the gap between two drawn bodies is never
#: negative (0.0% of pairs overlap) and its 5th percentile is 320 mils.
#: This is the ONE knob for how tightly a sheet packs, and it only became
#: one knob once `bodies_overlap` replaced the six separate answers -- the
#: resnaps used to ask for no clearance at all and leaned on `_bbox_half`
#: carrying a 366-mil pad on a body that measures 84.
_BODY_CLEARANCE_MILS = 300

# Mass-weighted split: lighter mass moves more. The bias is capped so
# an extremely heavy IC against a very light passive still moves a
# little (avoids degenerate cases where one part is pinned and the
# other can't reach it because of a wall).
_SHOVE_IC_SPLIT = 0.8       # heavy part moves 20%, light part 80%
_SHOVE_EDGE_SPLIT = 0.8     # edge-biased part moves 20%, the other 80%


# Part-role hints map onto a preferred sheet edge for layout bias.
# These are generic conventions: power inputs land left, power outputs
# right, anything else is unbiased. Recognised on both ``Part.role``
# and ``Zone.role`` (which the planner can attach to a part via its
# zone field).
_EDGE_BIAS_BY_ROLE: dict[str, str] = {
    "power_in": "left",
    "vin": "left",
    "vin_conn": "left",
    "input": "left",
    "power_out": "right",
    "vout": "right",
    "vout_conn": "right",
    "output": "right",
}

# Conservative per-part bbox half-extents in mils. These intentionally
# overestimate the real symbol body so labels, stub wires, and ground /
# rail port glyphs that flank the part still have breathing room without
# colliding with neighbouring parts. The values include a typical
# stub-length + label-height pad.
#
# THESE ARE NOW THE FALLBACK, not the sizing. ``_half_map`` takes a
# per-refdes measured extent and the pipeline supplies one for every part
# whose symbol it could read; a part with no readable symbol lands here.
# Retuned to what a person actually leaves, measured over 528 human
# sheets and 103802 part pairs: the gap between two DRAWN bodies is never
# negative (0.0% of pairs overlap) and its 5th percentile is 320 mils for
# the pair, about 150 each, so the keep-out is the drawn body plus 150.
# Applied to the median drawn body per bucket that gives the values
# below, and they agree with the separations those pairs are actually
# drawn at (2+2 430 mils, 3+3 500, 4-15 850, 16+ 1100) to within a grid
# step. Kept on the 100-mil grid: a half that is not a whole number of
# grid steps puts the sheet-edge limit (MAX_X - half) between two grid
# lines and the shove snaps the part to the wrong side of its own bound.
# THE DRAWN BODY, and only that. Medians of the real half-extent over
# 9084 human-drawn symbols, rounded up to the 100-mil grid (a half that is
# not a whole number of grid steps puts the sheet-edge limit at
# MAX_X - half between two grid lines, and the shove then snaps the part
# to the wrong side of its own bound). The room to leave AROUND a body is
# `_BODY_CLEARANCE_MILS`, which is a separate question and now has a
# single answer; these values used to carry it as a hidden pad, five
# times over on a two-pin passive and not at all on a large IC.
_BBOX_HALF_2PIN_MILS = 100    # measured median 80
_BBOX_HALF_3PIN_MILS = 200    # measured median 150
_BBOX_HALF_ICMIN_MILS = 300   # measured median 300
_BBOX_HALF_ICBIG_MILS = 1000  # measured median 1000

# Reproducible jitter so the layout is deterministic across runs.
_RANDOM_SEED = 0x5EDA_A6EE


@dataclass(frozen=True)
class PlacedPart:
    """Computed placement for one part."""

    refdes: str
    sheet: str
    x_mils: int
    y_mils: int
    rotation: int = 0


def _pin_count_per_part(plan: DesignPlan) -> dict[str, int]:
    counts: dict[str, int] = {p.refdes: 0 for p in plan.parts}
    for net in plan.nets:
        for pin in net.pins:
            counts[pin.refdes] = counts.get(pin.refdes, 0) + 1
    return counts


def _is_series_flow_part(part: Part, plan_nets: list[Net]) -> bool:
    """True for a 2-pin series element in the power flow (L / FB / F
    kind, touching a rail, no ground pin). These draw HORIZONTAL by
    convention, and that decision is deliberate -- downstream
    neighbour-geometry refinements must not verticalize them.
    """
    from eda_agent.design.motifs import _kind_from_refdes
    if _kind_from_refdes(part.refdes) not in ("L", "FB", "F"):
        return False
    nets_for_part = [
        n for n in plan_nets if any(p.refdes == part.refdes for p in n.pins)
    ]
    pins_used: set[str] = set()
    for n in nets_for_part:
        for pin in n.pins:
            if pin.refdes == part.refdes:
                pins_used.add(pin.pin)
    if len(pins_used) != 2:
        return False
    if any(n.is_ground for n in nets_for_part):
        return False
    return any(n.is_power for n in nets_for_part)


def _rotation_for_part(part: Part, plan_nets: list[Net]) -> int:
    """Pick a rotation (0/90/180/270) from the part's net categories.

    Schematic conventions:
    - 2-pin part with at least one pin on a power or ground rail
      -> VERTICAL (90 deg CCW). Covers decoupling caps, pull-up /
      pull-down resistors, bulk caps, terminating resistors, etc.
    - 2-pin part with both pins on plain signal nets -> HORIZONTAL
      (rotation 0). Covers inline series resistors, AC-coupling caps.
    - Anything else (single-pin parts, ICs with 3+ pins, parts not
      participating in any net) -> rotation 0 (library-native).

    This rule uses ONLY net classification (is_power / is_ground), no
    part-type or refdes-prefix assumptions. It is therefore generic
    across topologies. The actual direction (power up vs power down)
    depends on which physical pin of the symbol the library puts
    where; a follow-up pass can flip 90 -> 270 after reading the
    placed pin coordinates if the polarity ends up inverted.
    """
    nets_for_part = [
        n for n in plan_nets if any(p.refdes == part.refdes for p in n.pins)
    ]
    pins_used: set[str] = set()
    for n in nets_for_part:
        for pin in n.pins:
            if pin.refdes == part.refdes:
                pins_used.add(pin.pin)
    if len(pins_used) != 2:
        return 0
    has_power = any(n.is_power for n in nets_for_part)
    has_ground = any(n.is_ground for n in nets_for_part)
    if has_power or has_ground:
        # Series-vs-shunt refinement. The vertical convention is for
        # SHUNT parts hanging off a rail (decap, pull-up, divider leg).
        # A series element in the power FLOW -- inductor between switch
        # node and output, ferrite bead, fuse -- belongs HORIZONTAL in
        # the left-to-right flow; drawing a buck's inductor vertical is
        # an instant amateur tell and tangles every net around the
        # switch node. Net flags alone cannot make the distinction (a
        # decap also touches a rail), so use the part KIND via the same
        # refdes-prefix classifier the motif catalogue is built on.
        # Ground-connected L/FB/F (an RF choke to ground) stays
        # vertical like any shunt. NOTE: callers that refine a 0 result
        # with neighbour geometry must exempt series-flow parts (see
        # _is_series_flow_part) -- this 0 is a deliberate convention,
        # not a default.
        if _is_series_flow_part(part, plan_nets):
            return 0
        # 270 (CCW = 90 CW) is empirically correct for libraries whose
        # 2-pin parts have pin 1 on the RIGHT in native orientation
        # (e.g. common passive libraries). A library-aware pre-place pass
        # (query lib_get_pin_list, pick rotation based on which pin
        # ends up on top) is the generic fix; this is the interim.
        return 270
    return 0


def _half_map(pin_count: dict, body_half=None) -> dict:
    """Per-refdes half-extent: the real drawn body when it is known.

    ``body_half`` maps refdes to a measured half-extent, either a scalar
    or an ``(hx, hy)`` pair; the result is always a pair. Anything missing
    falls back to the pin-count estimate, so a caller that knows nothing
    behaves exactly as before.

    PER AXIS, because a single value has to be the LARGER of the two to be
    safe, and that separates a tall thin IC horizontally by its height.
    Measured: feeding the shove one scalar `max(hx, hy)` cleared the
    containment on the sheet it was tested against and broke three
    placement tests by over-separating; per-axis clears the same
    containment and breaks none.

    NOTHING IN THE PIPELINE SUPPLIES IT TODAY, deliberately, and the
    reason is worth having written down because it is not obvious.

    Placing with real bodies as one extra candidate won zero of twenty
    demo sheets. Tighter sizing DOES help, but only when the WHOLE
    pipeline is placed with it: patching these constants globally and
    letting every candidate compete under them took buck_conv from 299
    to 217 against a human 213, and complex_hierarchy from six
    horizontal bands to three where the human drew two. It pays because
    it raises the ceiling on BANDING, which is what wants tight
    packing.

    ONE CANDIDATE CANNOT REPRODUCE THAT, and three attempts to make it
    are what established why. The global patch let EVERY candidate
    compete under tight sizing (base, force-directed, pin-side,
    aspect, compaction, shared-axis, banded) and the 217 was the best
    of that search. Adding one tightly-placed candidate to a search
    whose other candidates are still loose changes nothing, because one
    of the loose ones still wins.

    Where the sizing bites also depends on the plan, which is what made
    the first two attempts look like the parameter was being ignored.
    Measured spreads with and without it: royer1 6100x5300 to
    3400x5000 and complex_hierarchy 3000x3400 to 2300x2300, both
    force-directed. An ANCHORED plan goes through Sugiyama, which does
    not consult sizing at all, so buck_conv barely moves (5000x2000 to
    5100x2000) and its gain comes from the overlap shove in
    build_canvas_from_plan instead. Supply it to one placer and not the
    other and nothing happens.

    So capturing this means running the whole candidate search twice,
    once under each sizing, and taking the best. That is a second full
    layout, not a second candidate, and it roughly doubles the time an
    interactive layout takes. Worth about 27% on buck_conv, so somebody
    should decide whether that trade is wanted before building it.

    AND IT IS NOT ONLY A SCORE TRADE. Widening the benchmark's caps
    surfaced power-supply-2, where the estimate undersizes IC31 by more
    than half (1200 assumed against 2800 real), the shove separates
    small parts to 1650 and leaves five of them INSIDE the IC, and the
    emitted sheet carries illegal geometry rather than a poor score.
    Two substitutions were tried and both failed:

      * measured extents outright fixed those five overlaps and cut the
        sheet 11498 to 3570, and orphaned two power glyphs on the 555
        blinker, because tighter passive spacing changed which port
        spokes survive the cross-net guard. Trading body overlaps for
        floating power objects is not a fix.

      * max(estimate, measured), which can only ever enlarge a part and
        so cannot move passives closer, made power-supply-2
        UNLAYOUTABLE: the engine declines it entirely. No output is
        worse than flawed output.

    The sizing is entangled with port-spoke survival and with placement
    feasibility, and a substitution reaches both. Whatever fixes this
    has to hold overlaps, spokes and feasibility at once.

    The override survives because it is what that change needs and
    because it collapses three copies of the same sizing expression
    into one. The measurement that says the estimate is wrong in both
    directions is above _BBOX_HALF_2PIN_MILS.

    Why it has to be per part rather than a better bucket: the count
    this keys on is the PLAN's, so a 40-pin connector wired on 8 pins
    is sized as a small part. See the table above _BBOX_HALF_2PIN_MILS.
    """
    known = body_half or {}
    out = {}
    for refdes in pin_count:
        measured = known.get(refdes)
        if isinstance(measured, (tuple, list)):
            # A degenerate axis falls back on its own: a net tie measures 0
            # wide and must not be sized to nothing on that axis.
            est = _bbox_half(pin_count.get(refdes, 2))
            out[refdes] = (int(measured[0]) or est, int(measured[1]) or est)
        elif measured:
            out[refdes] = (int(measured), int(measured))
        else:
            # None, 0 or an empty pair: no usable measurement. Falsy rather
            # than `is None` on purpose, so a degenerate body (a net tie, or
            # graphics this reader does not understand) keeps the estimate
            # instead of collapsing to zero.
            est = _bbox_half(pin_count.get(refdes, 2))
            out[refdes] = (est, est)
    return out


def _bbox_half(pin_count: int) -> int:
    if pin_count >= 16:
        return _BBOX_HALF_ICBIG_MILS
    if pin_count >= 4:
        return _BBOX_HALF_ICMIN_MILS
    if pin_count == 3:
        return _BBOX_HALF_3PIN_MILS
    return _BBOX_HALF_2PIN_MILS


def _mass(pin_count: int) -> float:
    # ICs are anchors; passives drift around them. Mass scales with pin count.
    return max(1.0, pin_count / 2.0)


def _zone_role(plan: DesignPlan, zone_name: str | None) -> str | None:
    if not zone_name:
        return None
    for z in plan.zones:
        if z.name == zone_name:
            return z.role
    return None


def _edge_for_part(plan: DesignPlan, part: Part) -> str | None:
    """Pick a preferred sheet edge based on Part.role or its zone's role.

    Returns 'left', 'right', or None. Part.role wins over zone role
    when both are present.
    """
    role = (part.role or "").strip().lower()
    if role in _EDGE_BIAS_BY_ROLE:
        return _EDGE_BIAS_BY_ROLE[role]
    zone_role = (_zone_role(plan, part.zone) or "").strip().lower()
    return _EDGE_BIAS_BY_ROLE.get(zone_role)


def _net_members(net: Net) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for pin in net.pins:
        if pin.refdes not in seen_set:
            seen.append(pin.refdes)
            seen_set.add(pin.refdes)
    return seen


def _connected_pairs(plan: DesignPlan) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for net in plan.nets:
        members = _net_members(net)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                lo, hi = sorted((a, b))
                pairs.add((lo, hi))
    return pairs


def _zone_for_refdes(plan: DesignPlan) -> dict[str, str | None]:
    return {p.refdes: p.zone for p in plan.parts}


def _initial_positions(
    plan: DesignPlan,
    pin_count: dict[str, int],
) -> dict[str, list[float]]:
    """Sparse grid with a small jitter -- converges fast and avoids
    degenerate co-located starting points."""
    rng = random.Random(_RANDOM_SEED)
    parts = list(plan.parts)
    n = len(parts)
    if n == 0:
        return {}
    cols = max(1, int(math.ceil(math.sqrt(n))))
    pitch_x = 1800
    pitch_y = 1500
    sheet_w = SHEET_MAX_X_MILS - SHEET_ORIGIN_X_MILS
    sheet_h = SHEET_MAX_Y_MILS - SHEET_ORIGIN_Y_MILS
    grid_w = (cols - 1) * pitch_x
    grid_h = ((n - 1) // cols) * pitch_y
    x0 = SHEET_ORIGIN_X_MILS + max(0, (sheet_w - grid_w) // 2)
    y0 = SHEET_ORIGIN_Y_MILS + max(0, (sheet_h - grid_h) // 2)

    pos: dict[str, list[float]] = {}
    anchors = sorted(
        (p.refdes for p in parts if pin_count.get(p.refdes, 0) >= 4),
        key=lambda r: -pin_count[r],
    )
    others = [p.refdes for p in parts if p.refdes not in set(anchors)]

    placement_order = anchors + others
    for i, refdes in enumerate(placement_order):
        row = i // cols
        col = i % cols
        jx = rng.uniform(-40, 40)
        jy = rng.uniform(-40, 40)
        pos[refdes] = [
            x0 + col * pitch_x + jx,
            y0 + row * pitch_y + jy,
        ]
    return pos


def _force_directed_layout(
    plan: DesignPlan,
    ic_pin_offsets: dict[str, dict[str, tuple[int, int]]] | None = None,
    pin_attract_k: float = _SPRING_K_PIN,
    body_half=None,
) -> list[PlacedPart]:
    """Run the spring/repulsion solver and return PlacedPart per part.

    ``ic_pin_offsets`` maps an IC's refdes to ``{pin_id: (dx, dy)}`` -- each
    pin's wire-end offset from the IC's centre in the symbol's native (rotation
    0) frame. When supplied, a discrete wired to an IC on a SIGNAL net is pulled
    toward that specific pin (centre + offset) instead of toward the IC's
    centre, so it settles on the pin's SIDE of the IC. This is the difference
    between "near the chip" and "near the chip's OUT pin"; it makes the placer
    pin-aware so the output stage lands by OUT and the timing network by the
    DISCH/THRES pins, without any post-hoc repositioning. ``None`` reproduces
    the centroid-only behaviour exactly.
    """
    parts = list(plan.parts)
    if not parts:
        return []

    pin_count = _pin_count_per_part(plan)
    mass = {r: _mass(pin_count.get(r, 2)) for r in pin_count}
    bbox_half = _half_map(pin_count, body_half)

    pos = _initial_positions(plan, pin_count)
    velocity = {r: [0.0, 0.0] for r in pos}

    # Pin attractors: for each discrete, the IC pins it shares a SIGNAL net with
    # (power / ground become port glyphs, not routed wires, so they must not
    # pull placement). Each entry is (ic_refdes, off_x, off_y).
    offsets = ic_pin_offsets or {}
    ic_set = set(offsets)
    pin_attract: dict[str, list[tuple[str, int, int]]] = {}
    attract_pairs: set[tuple[str, str]] = set()
    for net in plan.nets:
        if net.is_power or net.is_ground:
            continue
        ic_pins = [(p.refdes, str(p.pin)) for p in net.pins if p.refdes in ic_set]
        discretes = [p.refdes for p in net.pins if p.refdes not in ic_set]
        for ic, pin in ic_pins:
            off = offsets.get(ic, {}).get(pin)
            if off is None:
                continue
            for d in discretes:
                pin_attract.setdefault(d, []).append((ic, off[0], off[1]))
                attract_pairs.add(tuple(sorted((d, ic))))

    # Centroid springs for every connected pair EXCEPT those an IC-pin
    # attractor already handles (so a discrete isn't pulled to both the IC's
    # centre and its pin -- the pin wins).
    # SORTED, not a set. A set of refdes tuples iterates in string-hash
    # order, which Python randomises per process, and this solver SUMS
    # forces over the pairs in iteration order. Float addition is not
    # associative, so the same plan settled into a different layout from
    # one run to the next: a sweep run twice gave 67 clean sheets and then
    # 74. A single pytest process cannot see it, because the hash seed is
    # fixed for the life of the process.
    spring_pairs = sorted(
        pair for pair in _connected_pairs(plan) if pair not in attract_pairs
    )

    refdes_list = list(pos.keys())
    dt = _DT
    for _ in range(_MAX_ITERATIONS):
        forces = {r: [0.0, 0.0] for r in pos}

        for a, b in spring_pairs:
            if a not in pos or b not in pos:
                continue
            dx = pos[b][0] - pos[a][0]
            dy = pos[b][1] - pos[a][1]
            dist = math.hypot(dx, dy) + 1e-3
            disp = dist - _SPRING_REST_MILS
            f = disp * _SPRING_K
            ux = dx / dist
            uy = dy / dist
            forces[a][0] += f * ux
            forces[a][1] += f * uy
            forces[b][0] -= f * ux
            forces[b][1] -= f * uy

        # Pin attractors: pull each discrete toward the specific IC pin it
        # wires to (IC centre + the pin's offset), so it settles on that pin's
        # side of the IC. The reaction on the (heavy) IC is small.
        for d, attractors in pin_attract.items():
            if d not in pos:
                continue
            for ic, off_x, off_y in attractors:
                if ic not in pos:
                    continue
                tx = pos[ic][0] + off_x
                ty = pos[ic][1] + off_y
                dx = tx - pos[d][0]
                dy = ty - pos[d][1]
                dist = math.hypot(dx, dy) + 1e-3
                f = (dist - _SPRING_REST_PIN_MILS) * pin_attract_k
                ux = dx / dist
                uy = dy / dist
                forces[d][0] += f * ux
                forces[d][1] += f * uy
                forces[ic][0] -= f * ux
                forces[ic][1] -= f * uy

        n = len(refdes_list)
        for i in range(n):
            a = refdes_list[i]
            for j in range(i + 1, n):
                b = refdes_list[j]
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                dist2 = dx * dx + dy * dy + 1.0
                if dist2 > _REPEL_CUTOFF_MILS * _REPEL_CUTOFF_MILS:
                    continue
                dist = math.sqrt(dist2)
                min_sep = max(bbox_half[a]) + max(bbox_half[b]) + 200
                if dist < min_sep:
                    f = (min_sep - dist) * 0.25
                else:
                    f = _REPEL_K / dist2
                ux = dx / dist
                uy = dy / dist
                forces[a][0] -= f * ux
                forces[a][1] -= f * uy
                forces[b][0] += f * ux
                forces[b][1] += f * uy

        for part in parts:
            edge = _edge_for_part(plan, part)
            if edge == "left":
                target_x = SHEET_ORIGIN_X_MILS + 500
                forces[part.refdes][0] += (target_x - pos[part.refdes][0]) * _EDGE_PULL_K
            elif edge == "right":
                target_x = SHEET_MAX_X_MILS - 500
                forces[part.refdes][0] += (target_x - pos[part.refdes][0]) * _EDGE_PULL_K

        for r in pos:
            x, y = pos[r]
            half, half_y = bbox_half[r]
            if x < SHEET_ORIGIN_X_MILS + half:
                forces[r][0] += (SHEET_ORIGIN_X_MILS + half - x) * _BOUNDARY_K
            elif x > SHEET_MAX_X_MILS - half:
                forces[r][0] -= (x - (SHEET_MAX_X_MILS - half)) * _BOUNDARY_K
            if y < SHEET_ORIGIN_Y_MILS + half:
                forces[r][1] += (SHEET_ORIGIN_Y_MILS + half - y) * _BOUNDARY_K
            elif y > SHEET_MAX_Y_MILS - half:
                forces[r][1] -= (y - (SHEET_MAX_Y_MILS - half)) * _BOUNDARY_K

        for r in pos:
            ax = forces[r][0] / mass[r]
            ay = forces[r][1] / mass[r]
            velocity[r][0] = (velocity[r][0] + ax * dt) * _DAMPING
            velocity[r][1] = (velocity[r][1] + ay * dt) * _DAMPING
            pos[r][0] += velocity[r][0] * dt
            pos[r][1] += velocity[r][1] * dt

        dt *= _COOLING

    # Snap and clamp. Rotation is applied as a separate pass in
    # layout.compute_layout so Sugiyama and FD paths share one rotation
    # decision rule.
    placed: list[PlacedPart] = []
    for part in parts:
        x = pos[part.refdes][0]
        y = pos[part.refdes][1]
        x = int(round(x / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        y = int(round(y / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        hx, hy = bbox_half[part.refdes]
        x = max(SHEET_ORIGIN_X_MILS + hx, min(SHEET_MAX_X_MILS - hx, x))
        y = max(SHEET_ORIGIN_Y_MILS + hy, min(SHEET_MAX_Y_MILS - hy, y))
        placed.append(
            PlacedPart(
                refdes=part.refdes,
                sheet=part.sheet,
                x_mils=x,
                y_mils=y,
                rotation=0,
            )
        )
    return placed


def bodies_overlap(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    half_a,
    half_b,
    clearance: int = _BODY_CLEARANCE_MILS,
) -> bool:
    """True when two bodies are closer than they are allowed to be.

    THE ONE PLACE this question is answered, and it needed to be: it was
    answered in six, each with its own arithmetic and its own idea of how
    much room to leave. The shove used the bbox sum plus 50 mils, the
    motif splat the same, the priors resnaps and the array pass the bbox
    sum plus NOTHING, and the crystal cluster the sum plus 200.

    That mattered more than a tidy-up, because the zero-clearance callers
    are only safe while ``_bbox_half`` carries an implicit pad -- it
    holds a 2-pin passive 450 mils out when its body measures 84 -- so
    the resnaps are relying on a number that has nothing to do with the
    question they are asking. Sizing parts by their real drawn body,
    which is a large measured improvement on every other count, made
    every one of those checks touch-tight and put six pairs of bodies
    into contact on the big-body sample. Whatever the sizing becomes,
    it has to reach all of these together.

    Half-extents may be a scalar or an ``(hx, hy)`` pair; a scalar is
    taken as square. Strict inequality, so a KISSING pair (delta equals
    the sum) is not an overlap: that mirrors the audit's semantics and
    is what makes a grid snap onto the boundary safe.
    """
    ahx, ahy = half_a if isinstance(half_a, (tuple, list)) else (half_a, half_a)
    bhx, bhy = half_b if isinstance(half_b, (tuple, list)) else (half_b, half_b)
    return (abs(ax - bx) < ahx + bhx + clearance
            and abs(ay - by) < ahy + bhy + clearance)


def _overlap_pair(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    half_a,
    half_b,
    clearance: int = _BODY_CLEARANCE_MILS,
) -> tuple[float, float] | None:
    """Return the (dx, dy) overlap-along-axis distances, or None.

    AABB overlap test, PER AXIS. Each axis returns
    ``(ha + hb + clearance) - |delta|`` using that axis's half-extents;
    if either axis returns <= 0 the parts do not overlap and ``None`` is
    returned. When both are positive the smaller of the two indicates
    the cheapest direction to separate.

    Same test as :func:`bodies_overlap`, which is what every other pass
    asks; this one exists because the shove needs the DISTANCES and not
    just the answer. The two must agree, so change them together.
    """
    ax_half = half_a if isinstance(half_a, (tuple, list)) else (half_a, half_a)
    bx_half = half_b if isinstance(half_b, (tuple, list)) else (half_b, half_b)
    overlap_x = ax_half[0] + bx_half[0] + clearance - abs(ax - bx)
    overlap_y = ax_half[1] + bx_half[1] + clearance - abs(ay - by)
    if overlap_x <= 0 or overlap_y <= 0:
        return None
    return overlap_x, overlap_y


def _axis_fits(half_a, half_b, min_x, min_y, max_x, max_y,
               clearance: int = _BODY_CLEARANCE_MILS) -> tuple[bool, bool]:
    """Can this pair be separated along x, along y, inside the sheet?

    Putting one part at each wall gives the widest gap the paper allows,
    ``(max - min) - ha - hb``, and the pair needs ``ha + hb + clearance``
    of it. So an axis works only when the sheet spans at least
    ``2 * (ha + hb) + clearance``.

    Asking this at all is what MEASURING the real body exposed. On an A3
    RF board carrying six modules each drawn 7600 mils wide, separating
    two of them on x needs 7900 and six of them 39500, where the paper
    offers 14540: x is arithmetically impossible. Stacked on y the same
    six fit easily, 6 x 1424 against 9590, and that is what the person
    drew. The shove used to pick its axis by overlap depth alone, push on
    the impossible one, fail, and leave two modules on top of each other.
    Undersized halves hid it: the old requirement was 2450 and always
    fit.

    Both False means the pair cannot be separated on this paper at all;
    the caller leaves them where they are and the residual count reports
    it.
    """
    ahx, ahy = half_a if isinstance(half_a, (tuple, list)) else (half_a, half_a)
    bhx, bhy = half_b if isinstance(half_b, (tuple, list)) else (half_b, half_b)
    return (
        (max_x - min_x) >= 2 * (ahx + bhx) + clearance,
        (max_y - min_y) >= 2 * (ahy + bhy) + clearance,
    )


def _cheapest_feasible_push(ox: float, oy: float,
                            fits: tuple[bool, bool]) -> float:
    """Shallowest overlap AMONG THE AXES THE SHEET CAN ACCOMMODATE.

    The shove gives the cheapest axis a full push and the other a half
    one; choosing by depth alone spends the full push on an axis that
    cannot work. Returns the PUSH VALUE rather than an axis index so
    that a tie still gives BOTH axes full weight, which is what a pair
    sitting exactly on top of another needs to come apart diagonally.
    Returns ``inf`` when neither axis fits, so no axis is favoured and
    the caller skips both.
    """
    candidates = [v for v, ok in ((ox, fits[0]), (oy, fits[1])) if ok]
    return min(candidates) if candidates else float("inf")


def _shove_split(
    plan: DesignPlan,
    part_by_refdes: dict[str, Part],
    mass: dict[str, float],
    pin_count: dict[str, int],
    a: str,
    b: str,
) -> tuple[float, float]:
    """Pick the (frac_a, frac_b) split for a shove between two parts.

    ``frac_a + frac_b == 1.0``. The part that should move LESS gets the
    smaller fraction. Priority order:

    1. If exactly one side is edge-biased (``power_in`` / ``power_out``
       via Part.role or zone), it stays put -- the other side absorbs
       80% of the push.
    2. Else if exactly one side is an IC (4+ pins), the IC absorbs 20%
       of the push.
    3. Else split inversely by mass so lighter parts drift more, but
       guarantee both sides move at least 30% so we don't stall.
    """
    is_edge_a = _edge_for_part(plan, part_by_refdes[a]) is not None
    is_edge_b = _edge_for_part(plan, part_by_refdes[b]) is not None
    if is_edge_a and not is_edge_b:
        return (1.0 - _SHOVE_EDGE_SPLIT, _SHOVE_EDGE_SPLIT)
    if is_edge_b and not is_edge_a:
        return (_SHOVE_EDGE_SPLIT, 1.0 - _SHOVE_EDGE_SPLIT)

    pin_a = pin_count.get(a, 2)
    pin_b = pin_count.get(b, 2)
    a_is_ic = pin_a >= 4
    b_is_ic = pin_b >= 4
    if a_is_ic and not b_is_ic:
        return (1.0 - _SHOVE_IC_SPLIT, _SHOVE_IC_SPLIT)
    if b_is_ic and not a_is_ic:
        return (_SHOVE_IC_SPLIT, 1.0 - _SHOVE_IC_SPLIT)

    total_inv = (1.0 / mass[a]) + (1.0 / mass[b])
    frac_a = (1.0 / mass[a]) / total_inv
    frac_a = max(0.3, min(0.7, frac_a))
    return (frac_a, 1.0 - frac_a)


def _same_face_axis(a: str, b: str, face_of) -> int | None:
    """The one axis two parts beside the same IC face may be pushed along.

    ``face_of`` maps a part to the (IC refdes, face) it belongs beside, the
    face being "L", "R", "T" or "B". Two parts bound to the same face of the
    same IC separate along that face: y for a left or right face, x for a top
    or bottom one. Any other pair gets None, meaning no restriction.
    """
    if not face_of:
        return None
    face_a, face_b = face_of.get(a), face_of.get(b)
    if face_a is None or face_a != face_b:
        return None
    return 1 if face_a[1] in ("L", "R") else 0


def _hard_shove_pass(
    plan: DesignPlan,
    placed: list[PlacedPart],
    body_half=None,
    face_of=None,
) -> tuple[list[PlacedPart], int]:
    """Audit-aware deterministic shove.

    Detects pairwise bbox overlaps against ``_bbox_half(pin_count)`` and
    pushes each overlapping pair apart along the cheaper axis until the
    audit is clean or ``_SHOVE_MAX_ROUNDS`` is reached. Splits the push
    by mass / role (see :func:`_shove_split`). Respects sheet bounds:
    if one side would breach a wall, the other side absorbs the full
    push instead.

    ``face_of`` maps a small part to the (IC refdes, face) it belongs beside
    (``pipeline._satellite_faces``). Two parts bound to the SAME face of the
    same IC are pushed apart only ALONG that face, so crowded neighbours slide
    past each other instead of one being pushed round the IC's corner.
    MEASURED on the KiCad 10 demo sheets: a stage trace of the seven worst
    sheets lost 11 of 12 parts' sides in this pass, most of them small parts
    crowded against each other beside a large IC. With the rule, signal
    parts beside their pins' face rose from 71% to 76% over 27 sheets, with
    crossings 76 to 67 and no more overlaps or wires through bodies. A
    second rule, a part and its own IC separating only along the face
    normal, was measured and dropped: on the 555 test board it put three
    wires through bodies. ``None`` shoves exactly as before.

    Returns the new placement list and the residual overlap count
    after the final round (0 means clean).
    """
    if len(placed) < 2:
        return list(placed), 0

    pin_count = _pin_count_per_part(plan)
    mass = {r: _mass(pin_count.get(r, 2)) for r in pin_count}
    bbox_half = _half_map(pin_count, body_half)
    # THE SHEET THE PLAN ASKED FOR. The constants are A4 less a margin,
    # and clamping an A3 board into them leaves too little room to
    # separate anything: on an A3 RF board with six modules 7600 mils
    # wide the usable band is 1900 across, and this pass left four of
    # them stacked exactly on top of one another. 272 of 1465 corpus
    # sheets are larger than A4.
    min_x, min_y, max_x, max_y = sheet_bounds(plan)
    part_by_refdes = {p.refdes: p for p in plan.parts}

    pos: dict[str, list[float]] = {
        p.refdes: [float(p.x_mils), float(p.y_mils)] for p in placed
    }
    sheet_of = {p.refdes: p.sheet for p in placed}
    rot_of = {p.refdes: p.rotation for p in placed}

    refdes_list = [p.refdes for p in placed]
    residual = 0
    for _ in range(_SHOVE_MAX_ROUNDS):
        overlaps_this_round = 0
        delta: dict[str, list[float]] = {r: [0.0, 0.0] for r in pos}
        for i in range(len(refdes_list)):
            a = refdes_list[i]
            for j in range(i + 1, len(refdes_list)):
                b = refdes_list[j]
                if sheet_of[a] != sheet_of[b]:
                    continue
                ax, ay = pos[a]
                bx, by = pos[b]
                ovl = _overlap_pair(
                    ax, ay, bx, by,
                    bbox_half[a], bbox_half[b],
                )
                if ovl is None:
                    continue
                overlaps_this_round += 1
                ox, oy = ovl

                frac_a, frac_b = _shove_split(
                    plan, part_by_refdes, mass, pin_count, a, b
                )
                fits = _axis_fits(bbox_half[a], bbox_half[b],
                                  min_x, min_y, max_x, max_y)
                cheap = _cheapest_feasible_push(ox, oy, fits)
                only_axis = _same_face_axis(a, b, face_of)
                for axis, push in ((0, ox), (1, oy)):
                    if push <= 0 or not fits[axis]:
                        continue
                    if only_axis is not None and axis != only_axis:
                        continue
                    if axis == 0:
                        sign_v = 1.0 if (bx - ax) >= 0 else -1.0
                        if (bx - ax) == 0:
                            sign_v = 1.0 if a < b else -1.0
                    else:
                        sign_v = 1.0 if (by - ay) >= 0 else -1.0
                        if (by - ay) == 0:
                            sign_v = 1.0 if a < b else -1.0
                    weight = 1.0 if push == cheap else 0.5
                    lo_a = (min_x if axis == 0 else min_y) + bbox_half[a][axis]
                    hi_a = (max_x if axis == 0 else max_y) - bbox_half[a][axis]
                    lo_b = (min_x if axis == 0 else min_y) + bbox_half[b][axis]
                    hi_b = (max_x if axis == 0 else max_y) - bbox_half[b][axis]
                    a_blocked = (
                        pos[a][axis] <= lo_a + 0.5
                        if sign_v > 0
                        else pos[a][axis] >= hi_a - 0.5
                    )
                    b_blocked = (
                        pos[b][axis] >= hi_b - 0.5
                        if sign_v > 0
                        else pos[b][axis] <= lo_b + 0.5
                    )
                    fa, fb = frac_a, frac_b
                    if a_blocked and not b_blocked:
                        fa, fb = 0.0, 1.0
                    elif b_blocked and not a_blocked:
                        fa, fb = 1.0, 0.0
                    elif a_blocked and b_blocked:
                        continue
                    delta[a][axis] += -sign_v * push * fa * weight
                    delta[b][axis] += sign_v * push * fb * weight

        for r in pos:
            for axis in (0, 1):
                lo = (min_x if axis == 0 else min_y) + bbox_half[r][axis]
                hi = (max_x if axis == 0 else max_y) - bbox_half[r][axis]
                new = pos[r][axis] + delta[r][axis]
                # A part WIDER than the sheet cannot be clamped inside it:
                # lo exceeds hi and max(lo, min(hi, x)) then collapses
                # EVERY such part onto lo, which is how identical
                # positions come out of a pass whose whole job is to
                # separate things.
                pos[r][axis] = new if lo > hi else max(lo, min(hi, new))

        residual = overlaps_this_round
        if overlaps_this_round == 0:
            break

    snapped: dict[str, list[int]] = {}
    for r in pos:
        hx, hy = bbox_half[r]
        x = int(round(pos[r][0] / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        y = int(round(pos[r][1] / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        if min_x + hx <= max_x - hx:
            x = max(min_x + hx, min(max_x - hx, x))
        if min_y + hy <= max_y - hy:
            y = max(min_y + hy, min(max_y - hy, y))
        snapped[r] = [x, y]

    for _ in range(_SHOVE_MAX_ROUNDS):
        any_overlap = False
        idelta: dict[str, list[int]] = {r: [0, 0] for r in snapped}
        for i in range(len(refdes_list)):
            a = refdes_list[i]
            for j in range(i + 1, len(refdes_list)):
                b = refdes_list[j]
                if sheet_of[a] != sheet_of[b]:
                    continue
                ax, ay = snapped[a]
                bx, by = snapped[b]
                if bodies_overlap(ax, ay, bx, by,
                                  bbox_half[a], bbox_half[b]):
                    any_overlap = True
                    # One separation distance for the pair, taken from the
                    # axis they are already furthest apart on: that is the
                    # axis this loop will push along, and mixing the two
                    # halves into ox and oy separately made the cheaper-axis
                    # choice below compare distances measured against
                    # different bars.
                    # WITH the clearance, because the test just above is
                    # `bodies_overlap`, which includes it. A push sized
                    # from the bodies alone is SHORTER than the distance
                    # that test demands, so every round computed a
                    # negative push, moved nothing, and the pass reported
                    # a residual overlap it could never clear.
                    needed = _BODY_CLEARANCE_MILS + (
                        bbox_half[a][0] + bbox_half[b][0]
                        if abs(ax - bx) >= abs(ay - by)
                        else bbox_half[a][1] + bbox_half[b][1])
                    ox = needed - abs(ax - bx)
                    oy = needed - abs(ay - by)
                    frac_a, frac_b = _shove_split(
                        plan, part_by_refdes, mass, pin_count, a, b
                    )
                    i_fits = _axis_fits(bbox_half[a], bbox_half[b],
                                        min_x, min_y, max_x, max_y)
                    cheaper = _cheapest_feasible_push(ox, oy, i_fits)
                    only_axis = _same_face_axis(a, b, face_of)
                    for axis, push in ((0, ox), (1, oy)):
                        if push <= 0 or not i_fits[axis]:
                            continue
                        if only_axis is not None and axis != only_axis:
                            continue
                        if axis == 0:
                            sign_v = 1 if (bx - ax) >= 0 else -1
                            if (bx - ax) == 0:
                                sign_v = 1 if a < b else -1
                        else:
                            sign_v = 1 if (by - ay) >= 0 else -1
                            if (by - ay) == 0:
                                sign_v = 1 if a < b else -1
                        push_grid = int(math.ceil(push / SNAP_GRID_MILS) * SNAP_GRID_MILS)
                        if push != cheaper:
                            push_grid = max(0, push_grid // 2)
                            push_grid = int(math.ceil(push_grid / SNAP_GRID_MILS) * SNAP_GRID_MILS)
                        if push_grid <= 0:
                            continue
                        lo_a_i = (min_x if axis == 0 else min_y) + bbox_half[a][axis]
                        hi_a_i = (max_x if axis == 0 else max_y) - bbox_half[a][axis]
                        lo_b_i = (min_x if axis == 0 else min_y) + bbox_half[b][axis]
                        hi_b_i = (max_x if axis == 0 else max_y) - bbox_half[b][axis]
                        a_blk = snapped[a][axis] <= lo_a_i if sign_v > 0 else snapped[a][axis] >= hi_a_i
                        b_blk = snapped[b][axis] >= hi_b_i if sign_v > 0 else snapped[b][axis] <= lo_b_i
                        fa, fb = frac_a, frac_b
                        if a_blk and not b_blk:
                            fa, fb = 0.0, 1.0
                        elif b_blk and not a_blk:
                            fa, fb = 1.0, 0.0
                        elif a_blk and b_blk:
                            continue
                        move_b = int(math.ceil(push_grid * fb / SNAP_GRID_MILS) * SNAP_GRID_MILS)
                        move_a = max(0, push_grid - move_b)
                        move_a = int(math.ceil(move_a / SNAP_GRID_MILS) * SNAP_GRID_MILS)
                        idelta[a][axis] += -sign_v * move_a
                        idelta[b][axis] += sign_v * move_b
        for r in snapped:
            for axis in (0, 1):
                lo = (min_x if axis == 0 else min_y) + bbox_half[r][axis]
                hi = (max_x if axis == 0 else max_y) - bbox_half[r][axis]
                new = snapped[r][axis] + idelta[r][axis]
                if lo <= hi:
                    new = max(lo, min(hi, new))
                snapped[r][axis] = int(round(new / SNAP_GRID_MILS) * SNAP_GRID_MILS)
        if not any_overlap:
            residual = 0
            break

    out: list[PlacedPart] = []
    for p in placed:
        r = p.refdes
        x, y = snapped[r]
        out.append(
            PlacedPart(
                refdes=r,
                sheet=sheet_of[r],
                x_mils=x,
                y_mils=y,
                rotation=rot_of[r],
            )
        )
    residual = sum(
        1
        for i in range(len(out))
        for j in range(i + 1, len(out))
        if out[i].sheet == out[j].sheet
        and bodies_overlap(
            out[i].x_mils, out[i].y_mils, out[j].x_mils, out[j].y_mils,
            bbox_half[out[i].refdes], bbox_half[out[j].refdes])
    )
    return out, residual
