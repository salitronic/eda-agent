# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Motif-driven placement composer.

Three layers, applied top-down:

1. **Motif composition**. Detect canonical sub-circuits in the plan's
   netlist (bypass cap, voltage divider, fb_divider, lc_output, ...)
   using ``design.motifs.recognize_motifs``. Each match comes with a
   frozen canonical sub-layout. The composer splats every match into
   absolute positions, taking the matching IC's force-directed
   position as the motif anchor (for IC-anchored motifs) or the
   centroid of the matched components' positions (for self-contained
   motifs).

2. **Sugiyama fallback**. Parts that no motif claimed get the
   ``compute_layout`` (Sugiyama / force-directed) position as a
   reasonable fallback. The composer overlays motif positions ON TOP
   of the Sugiyama base so unmatched parts still get a sensible
   placement.

3. **Canonical priors refinement** (applied by the pipeline AFTER the
   composer runs, in ``apply_placement_priors``). Single-part nudges
   like "bypass cap goes 400 mils above the IC it bypasses" handle
   cases where a motif didn't fire but a one-to-one role pair does.

Why three layers: motifs give large-block topology (cluster Rtop/Rbot
of a divider together, place inductor right of regulator), priors
give per-part nudges, Sugiyama handles the long tail. Combining all
three is what makes the output look professional even for a circuit
the system has never seen before, because every part has at least one
of (motif rule, prior rule, fallback) to lean on.

Critical: the composer's output is a ``list[PlacedPart]`` -- same
shape as ``compute_layout``. The pipeline calls it as a drop-in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from eda_agent.design.layout import PlacedPart, compute_layout
from eda_agent.design.motifs import (
    MOTIF_CATALOGUE,
    Match,
    Motif,
    build_circuit_graph,
    find_all_matches,
    resolve_matches,
    splat_motif,
)
from eda_agent.design.plan import DesignPlan

logger = logging.getLogger("eda_agent.design.composer")


# Role-incompatibility table: motif_name -> set of Part.role values that
# disqualify a match if any claimed component carries that role. Drops
# false positives where the structural pattern matches but the semantic
# intent doesn't. The match gets discarded before any positions are
# splatted; the canonical priors then handle the placement via the
# part's role-pair entry.
#
# Keep this table small and conservative. The bias is toward rejecting
# motif matches when in doubt; the priors layer below will still place
# the part sensibly via its role pair.
_MOTIF_INCOMPATIBLE_ROLES: dict[str, frozenset[str]] = {
    "rc_lowpass": frozenset({
        "vcc_decoup", "decoup_cap", "decoup_hf", "decoup_bulk",
        "bulk_cap",
    }),
    "rc_highpass": frozenset({
        "vcc_decoup", "decoup_cap", "decoup_hf", "decoup_bulk",
        "bulk_cap",
    }),
    "rc_snubber": frozenset({
        "vcc_decoup", "decoup_cap", "decoup_hf", "decoup_bulk",
    }),
    "bypass_cap": frozenset({
        "filter_c", "cap_charge",
    }),
    "pull_up_r": frozenset({
        "led_limit", "current_sense",
        "fb_top", "fb_bot", "filter_r", "termination",
    }),
    "pull_down_r": frozenset({
        "led_limit", "current_sense",
        "fb_top", "fb_bot", "filter_r", "termination",
    }),
    "voltage_divider": frozenset({
        "led_limit", "current_sense",
    }),
    "rc_compensation": frozenset({
        "vcc_decoup", "decoup_cap", "decoup_hf", "decoup_bulk",
        "filter_c", "filter_r", "led_limit",
    }),
    "fb_divider": frozenset({
        "led_limit", "filter_r",
    }),
    "boot_cap": frozenset({
        "vcc_decoup", "decoup_cap", "filter_c",
    }),
    "lc_output": frozenset({
        "vcc_decoup", "filter_c",
    }),
}


@dataclass
class ComposerResult:
    """Diagnostic detail about a composer run.

    ``placements`` is the merged ``list[PlacedPart]`` ready for the
    pipeline. ``motif_matches`` is the list of motifs that fired (after
    arbitration); useful for the audit tool to explain WHY a part
    landed where it did.
    """

    placements: list[PlacedPart]
    motif_matches: list[Match]
    motif_parts: set[str]  # refdes claimed by motifs (got a motif position)
    fallback_parts: set[str]  # refdes that fell through to Sugiyama


def compose_layout(
    plan: DesignPlan,
    *,
    motifs: Optional[list[Motif]] = None,
    motif_half: Optional[dict] = None,
) -> ComposerResult:
    """Top-level entry point: motif compose + Sugiyama fallback.

    Pipeline calls this in place of bare ``compute_layout(plan)``. The
    returned ``placements`` list is one ``PlacedPart`` per plan part
    (same shape as ``compute_layout``'s output).

    Strategy:
      1. Run ``compute_layout(plan)`` to get a baseline position for
         every part.
      2. Run ``recognize_motifs(plan)`` to detect canonical
         sub-circuits.
      3. For each match, pick an anchor and splat the motif's canonical
         positions. IC-anchored motifs use their IC's baseline position
         as anchor; self-contained motifs use the bounding-box centre
         of the matched parts' baseline positions.
      4. Overlay splatted positions on top of the baseline.
      5. Snap to the 100-mil grid, return.

    Snap-to-grid happens at the end because both Sugiyama and the
    canonical motif offsets are designed for multiples of 100 mil --
    but the centroid math in step 3 may produce off-grid values.
    """
    catalogue = motifs if motifs is not None else list(MOTIF_CATALOGUE)

    # 1. Sugiyama baseline. Every part gets a starting position.
    # Straight through to the motif splat. The PLACER stays uniform:
    # correcting even one part's size on a 36-part sheet moved the
    # force-directed sweep onto a different minimum and cost crossings
    # (0.21 to 0.31 of the human's), so real extents go only to the
    # passes that cannot affect the search.
    baseline = compute_layout(plan, motif_half=motif_half)
    by_refdes: dict[str, PlacedPart] = {p.refdes: p for p in baseline}
    placeable_refdes = set(by_refdes.keys())

    # 2. Motif recognition. CRITICAL ORDERING: run the role-compat
    # filter BEFORE arbitration, not after. If a more-specific motif
    # (e.g. rc_compensation, specificity=6) and a less-specific one
    # (e.g. rc_lowpass, specificity=4) both match the same parts, the
    # MIS arbitration in resolve_matches() picks the higher specificity
    # FIRST. If we then filter out the winner because its claimed roles
    # are incompatible, we've thrown away the legitimate lower-specificity
    # alternative too. Filtering pre-arbitration lets the loser through
    # when the winner is semantically wrong.
    graph = build_circuit_graph(plan)
    all_matches = find_all_matches(plan, catalogue)
    motif_by_name = {m.name: m for m in catalogue}

    role_by_refdes = {p.refdes: (p.role or "") for p in plan.parts}
    role_compatible: list[Match] = []
    for match in all_matches:
        incompat = _MOTIF_INCOMPATIBLE_ROLES.get(match.motif_name, frozenset())
        if not incompat:
            role_compatible.append(match)
            continue
        claimed_roles = {
            role_by_refdes.get(refdes, "")
            for refdes in match.components
        }
        if claimed_roles & incompat:
            logger.debug(
                "composer: dropping %s match (claimed roles %s overlap "
                "incompatible roles %s for this motif)",
                match.motif_name,
                sorted(r for r in claimed_roles if r),
                sorted(incompat & claimed_roles),
            )
            continue
        role_compatible.append(match)

    # Now arbitrate among the role-compatible matches. resolve_matches
    # does the MIS-by-specificity pass and ensures no two kept matches
    # claim the same passive component.
    matches = resolve_matches(role_compatible, catalogue)

    # 3. Splat each match.
    motif_parts: set[str] = set()
    motif_positions: dict[str, tuple[int, int, int]] = {}  # refdes -> (x, y, rot)

    for match in matches:
        motif = motif_by_name.get(match.motif_name)
        if motif is None:
            continue
        anchor = _pick_anchor(match, motif, by_refdes)
        if anchor is None:
            continue
        placement = splat_motif(match, motif, anchor)
        for refdes, (px, py) in placement.parts.items():
            if refdes not in placeable_refdes:
                continue
            # Snap to 100-mil grid up front so the merged positions
            # are all grid-aligned for the wiring pass.
            px_snap = (px // 100) * 100
            py_snap = (py // 100) * 100
            existing_rot = by_refdes[refdes].rotation
            motif_positions[refdes] = (px_snap, py_snap, existing_rot)
            motif_parts.add(refdes)

    # 4. Merge: motif positions win over baseline. Build the merged list
    # in the same order as the baseline so downstream code that depends
    # on placement order (e.g., for deterministic refdes iteration) is
    # unaffected.
    merged: list[PlacedPart] = []
    for placement in baseline:
        if placement.refdes in motif_positions:
            mx, my, mrot = motif_positions[placement.refdes]
            merged.append(PlacedPart(
                refdes=placement.refdes,
                sheet=placement.sheet,
                x_mils=mx,
                y_mils=my,
                rotation=mrot,
            ))
        else:
            merged.append(placement)

    fallback_parts = placeable_refdes - motif_parts

    if matches:
        logger.info(
            "composer: %d motifs fired (%s); %d parts claimed; "
            "%d parts fell through to Sugiyama",
            len(matches),
            ", ".join(m.motif_name for m in matches),
            len(motif_parts),
            len(fallback_parts),
        )
    else:
        logger.info(
            "composer: no motifs matched; all %d parts kept Sugiyama positions",
            len(fallback_parts),
        )

    return ComposerResult(
        placements=merged,
        motif_matches=matches,
        motif_parts=motif_parts,
        fallback_parts=fallback_parts,
    )


def _pick_anchor(
    match: Match,
    motif: Motif,
    by_refdes: dict[str, PlacedPart],
) -> Optional[tuple[int, int]]:
    """Choose the absolute anchor point for a motif splat.

    IC-anchored motifs: use the IC's baseline (Sugiyama) position.
    That's deliberate -- the IC keeps wherever the force-directed
    placer put it (typically the centre of its block), and all the
    surrounding passives get pinned around it. This is the right
    behaviour for "regulator with bypass + fb_divider + boot_cap":
    the regulator is where the rest of the design wants it, the
    passives just need to cluster correctly.

    Self-contained motifs: use the bounding-box centre of the matched
    parts' baseline positions. That keeps the motif near where
    Sugiyama wanted to put its components, just rearranging them into
    the canonical relative shape.

    Returns None if no anchor can be picked (e.g., the IC in an
    IC-anchored motif isn't in the placement dict, which shouldn't
    happen but might if a plan part was skipped).
    """
    if motif.ic_anchor is not None:
        ic_refdes = match.host_refdes(motif.ic_anchor)
        if ic_refdes is None:
            return None
        ic_placement = by_refdes.get(ic_refdes)
        if ic_placement is None:
            return None
        return (ic_placement.x_mils, ic_placement.y_mils)

    # Self-contained motif: centroid of matched component baselines.
    xs: list[int] = []
    ys: list[int] = []
    for pat_refdes in motif.canonical.keys():
        actual = match.host_refdes(pat_refdes)
        if actual is None:
            continue
        placement = by_refdes.get(actual)
        if placement is None:
            continue
        xs.append(placement.x_mils)
        ys.append(placement.y_mils)
    if not xs:
        return None
    return (sum(xs) // len(xs), sum(ys) // len(ys))


__all__ = [
    "ComposerResult",
    "compose_layout",
]
