# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Score a SchematicCanvas's layout quality.

The pipeline used to produce one canvas and emit it. With this module
it can produce N candidate canvases, score each, and pick the lowest-
score one. That closes the iteration loop the SVG renderer was always
meant to serve.

A LayoutScore aggregates eleven metrics into one badness number. Lower is
better. Each weight's own constant below carries the measurement that
justifies it; this is the summary:

| metric               | weight | basis                                     |
|----------------------|--------|-------------------------------------------|
| body_overlaps        |  1000  | illegal geometry; the optimiser must flee |
| wires_through_bodies |   400  | illegible, and usually a short             |
| wire_crossings       |   100  | MEASURED: human median 2/sheet, 36% clean  |
| total_wire_length    |  0.01  | per mil; MEASURED: this engine draws 2.4x  |
| aspect_ratio_penalty |    50  | fit the SHEET, not a square (see below)    |
| alignment            |    40  | MEASURED: humans align 91% of parts        |
| flow_reversals       |    40  | MEASURED: left to right holds ~75%         |
| port_count           |    25  | MEASURED: 6.5 human vs 8.0 here, 2x if dense |
| decap_orphans        |     0  | falsified: bypass caps sit FURTHER away    |
| rail_flips           |     0  | real at 93%, but its trade-off is unmeasured |
| label_spread         |     0  | falsified: 62% of human sheets violate it  |

MEASURED means against the human-drawn KiCad demo sheets, through
design.human_benchmark, which rebuilds a professional's sheet on this
canvas and scores it beside this engine's layout of the same netlist.
Three of the weights above previously carried claims that measurement
overturned, and two more are held at zero because it falsified them.

A weight of zero is deliberate and not dead code: the metric is still
computed and reported in the breakdown, so a layout can be inspected
against a convention the score does not yet steer by.

The breakdown is preserved in the score so failures explain themselves.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from eda_agent.design.canvas import SchematicCanvas, WireSegment
from eda_agent.design.plan import DesignPlan

logger = logging.getLogger("eda_agent.design.quality")


@dataclass
class LayoutScore:
    """One layout's quality breakdown. ``total`` is the badness number."""

    total: float = 0.0
    wire_crossings: int = 0
    wires_through_bodies: int = 0
    body_overlaps: int = 0
    aspect_ratio_penalty: float = 0.0
    total_wire_length: int = 0
    port_count: int = 0
    alignment_penalty: float = 0.0
    # Structural / convention features. See the module docstring: the
    # scalars above measure whether a sheet is TIDY, these measure
    # whether it is CONVENTIONAL, and reviewers complain about the
    # second far more often than the first.
    decap_orphans: int = 0
    rail_direction_flips: int = 0
    flow_reversals: int = 0
    label_column_spread: int = 0
    # The three laws on which human sheets and engine output differ most,
    # measured over 176 public sheets (see ruler.py): 2-pin rail passives
    # lying on their side (humans 2%, engine 100% before the rotation fix),
    # wire runs over 1000 mils (0% vs 10% of segments), and how many
    # horizontal bands the parts spread over (humans 2.5 parts per band,
    # the engine 1.57). They carry NO weight in the hand-tuned score; they
    # exist so a model fitted on human-versus-engine pairs can learn what
    # the hand weights cannot express.
    shunt_on_side: int = 0
    long_wires: int = 0
    row_bands_per_part: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)

    def __lt__(self, other: "LayoutScore") -> bool:
        return self.total < other.total


# Tuned weights -- see module docstring.
#: VALIDATED against the corpus, unlike aspect and the structural four.
#: Measured on 104 human-drawn KiCad demo sheets, counting only
#: crossings between DIFFERENT nets: the median sheet has 2, 35.6% have
#: none at all, and the rate is 0.018 crossings per wire. Professionals
#: do avoid these, so penalising them is not a proxy trap.
#:
#: STRONGER ON REAL HARDWARE than on the demos. Across 1124 sheets from
#: 172 public repositories the median is 1 and 49% are crossing-free,
#: so the convention this penalises is held to more tightly in
#: production work than KiCad's own examples suggested.
#:
#: The mean is 13.2 because a few sheets cannot avoid them: Ethernet
#: fans four differential pairs out of a connector into a PHY in fixed
#: pin order and takes 39, which is topology rather than untidiness. A
#: sheet losing badly on this term is worth looking at before it is
#: worth blaming.
_W_CROSSINGS = 100.0
_W_THROUGH_BODY = 400.0
_W_OVERLAP = 1000.0
_W_ASPECT = 50.0
#: MEASURED on the 36 human-drawn sheets the benchmark can compare:
#: the median human sheet carries 8050 mils of wire and this engine
#: draws 16550 for the same netlist, 2.06 times longer. It is worse on
#: 31 of the 36 and remains one of the two largest gaps.
#:
#: AND 2.88x AGAINST REAL HARDWARE, which is the number that counts.
#: Measured on 313 comparable sheets drawn from 172 public hardware
#: repositories: human median 6700 mils, engine 19300, and the engine
#: is worse on 280 of the 313. Production boards are drawn more tightly
#: than KiCad's teaching examples, so the demo corpus was flattering
#: this gap rather than exaggerating it.
#:
#: An earlier 2.42x (7075 against 17150) is superseded, not withdrawn:
#: both halves moved. The engine's median fell when collinear same-net
#: overlaps stopped being drawn twice, which removed 37420 mils across
#: these sheets, and the human's rose because the comparable SET
#: changed when the benchmark started reading each sheet's real paper
#: size and flagging its rails.
#:
#: MOSTLY SPREAD, PARTLY ROUTING, NOT LABELS. Measured on six sheets,
#: the engine's placement covers 2.0 to 6.4 times the AREA of the
#: human's for the same netlist (royer1: 4260 x 1231 mils drawn by hand
#: against 6220 x 5380 here), and wire length follows from area.
#:
#: Routing adds to it. Measured over 28 two-pin signal nets, where the
#: ideal route is exactly the pins' Manhattan separation, the engine
#: draws a median 1.27 times that and 5 of the 28 exceed 1.5. The worst
#: pay their pin stub TWICE: on rectifier, signal_in leaves V1's
#: left-facing pin 300 mils leftward, then doubles back east for the
#: whole span, 2500 mils where 1900 was available, because a vertical
#: drop at the pin's own x is clear of V1's body.
#:
#: An earlier version of this note blamed spread alone. Spread is the
#: larger half; it is not all of it.
#:
#: The wire-vs-label gate was the first suspect and was ruled out by
#: measurement. It is pipeline._LABEL_SPAN_MILS paired with a
#: two-crossing test, not schematic_layout._DEFAULT_LABEL_SPAN_MILS
#: (that module is not run in the canvas-building path). Driving the
#: real threshold from 2400 down to 800 demoted no additional net on
#: amplifier-ac and moved the total by nothing: the binding condition
#: is the crossing test, not the span.
#:
#: Where to look instead: force_directed._BBOX_HALF_* keeps parts
#: apart by a half-extent guessed from PIN COUNT, from 450 mils for a
#: two-pin passive up to 1200, and a two-pin passive's real drawn body
#: is nearer 60 x 20.
_W_LENGTH = 0.01
#: MEASURED on 36 comparable sheets: the humans draw 295 power glyphs
#: in total and this engine draws 453, so 1.54 times as many. Holds on
#: real hardware: 2439 against 3788 over 313 sheets from 172 public
#: repositories, 1.55 times as many. On the
#: worst sheets it is worse than that (sfp+ 8 against 25, nPM1300 16
#: against 30). A human runs a return WIRE and hangs one glyph on it;
#: this engine places a glyph per pin cluster and routes spokes out to
#: the pins, so its count follows how spread the pins are.
#:
#: AN EARLIER FIGURE OF 1.18x IS WITHDRAWN. It was measured while the
#: benchmark's plan left power and ground nets unflagged, so nets a
#: human drew as rails were being wired instead of ported. Flagging
#: them, which is what a real DesignPlan carries, added 104 glyphs
#: across these sheets; banding added 2 more. The engine is worse at
#: this than the old number said, against input it actually gets.
#:
#: Not an artifact of the comparison: the human canvas only receives
#: glyphs whose net survived into the plan, and that filter was checked
#: to drop 0 to 2 glyphs on these sheets.
#:
#: Row count is upstream of this. See _cluster_radius_for_net in
#: pipeline for why a rail trunk only pays on a compact layout.
_W_PORTS = 25.0
# Node alignment is a core drawing aesthetic; mild so it nudges toward tidy
# rows/columns without overriding crossings / overlaps.
#: VALIDATED against the corpus. Measured on 104 human-drawn KiCad demo
#: sheets: the median penalty is 0.094, so about 91% of parts share a row
#: or a column with another part, and 17.3% of sheets are perfect.
#: Replicated on 1046 layouts from 172 public hardware repositories:
#: median 0.103, so 90% of parts aligned. Same convention, same
#: strength, a corpus ten times the size. That
#: costs the median human sheet 3.8 points, which is the right order for
#: something real but secondary.
#:
#: It is also where this engine is weakest against humans, after wire
#: length: on the 36 sheets the benchmark can compare, the engine scores
#: worse on 30 of them, with a median penalty of 0.555 against the
#: humans' 0.164. On 313 real-hardware sheets the same shape: 0.40
#: against 0.13, worse on 204 of them. That is a genuine gap, not a miscalibrated measure,
#: and it is what the pipeline's shared-axis variant exists to close.
_W_ALIGNMENT = 40.0


#: Structural weights. Deliberately in the same band as through_body
#: rather than the 1000 reserved for illegal overlaps: a misplaced decap
#: is wrong, not invalid, and the optimiser should trade it against
#: crossings rather than flee it absolutely.
#: REPORTED, NOT OPTIMISED. This is a BOARD rule, and this is a SHEET
#: scorer. On a PCB a bypass cap must sit next to the pin it serves, and
#: pcb_placement already enforces that. On a schematic the same caps are
#: conventionally drawn together in a row near the power rail.
#:
#: MEASURED on 801 capacitors across 57 human-drawn KiCad demo sheets:
#: caps with decoupling VALUES (100n, 0.1u, 1u) sit a median 2550 mils
#: from the nearest IC, while every other cap sits at 1768. The bypass
#: caps are FURTHER away, so proximity does not distinguish them and
#: there is nothing here to threshold. A 600 mil rule flagged 96.9% of
#: them.
#:
#: Not retuned, because retuning a measure whose premise is inverted is
#: just fitting noise. Kept as a diagnostic.
_W_DECAP_ORPHAN = 0.0
#: THE CONVENTION IS VALIDATED. ITS WEIGHT IS NOT.
#:
#: MEASURED on 2572 power-glyph placements across 109 human-drawn KiCad
#: demo sheets: an earlier pass reported 3.9-4.1%, and that number is
#: withdrawn. It was taken while the sheet reader left power nets
#: ANONYMOUS, so a glyph reading "GND" almost never matched the net it
#: sits on: only 13 glyphs in the whole corpus were comparable, and the
#: rest were skipped as having no net rather than counted as clean.
#:
#: RE-MEASURED once a power symbol names its net globally, the way
#: KiCad means it: 1685 comparable glyphs, of which 684 (40.6%) read as
#: violations. That is not a convention professionals break two times
#: in five. It is this comparison being wrong: a rail is now ONE net
#: spanning the sheet, and the code asks whether the glyph sits above
#: the MEDIAN pin of all of it. The convention is local -- a ground
#: glyph sits below the pin it attaches to -- and the same mistake this
#: package has made repeatedly, an aggregate standing in for the
#: electrical relationship.
#:
#: FIXED to compare each glyph against the nearest pin ON ITS NET,
#: which is the question the convention actually asks: 7.1% violations
#: rather than 40.6%, so professionals hold to it about 93% of the
#: time.
#:
#: Weighted zero throughout, so nothing shipped moved either way, and
#: it stays zero for the reason it always should have: the corpus says
#: how OFTEN humans obey this and nothing about what they would TRADE
#: to obey it. At 150 it outweighed pin-side placement in a 555 blinker
#: and pulled C2 to the wrong side of its IC, overturning a separately
#: validated behaviour. What would settle the weight is a measurement
#: of the two conventions in conflict, which this corpus cannot give.
_W_RAIL_FLIP = 0.0
#: A REAL BUT SOFT CONVENTION, and the figure was corrected downward
#: once the netlist extraction was fixed.
#:
#: MEASURED across the human-drawn KiCad demo sheets, paired BY NET and
#: read through this scorer's own path: 51 driver-to-load nets, 13 of
#: them right to left, 25.5%. Feedback paths, buses and sheet-edge
#: connections legitimately go backwards, so left to right holds about
#: three quarters of the time rather than the 95% an early pass claimed.
#:
#: THE DENOMINATOR IS 51, NOT THE 452 STATED HERE BEFORE. Most demo
#: libraries mark their pins passive, and a pin with no declared
#: direction cannot be judged, so the evidence for this feature is
#: thin. It is enough to say the convention is real and not enough to
#: tune a weight on, which is why 40 has been left alone.
#:
#: That earlier 5.1% was an artefact of THIS repository's own bug: net
#: extraction unioned wire ENDPOINTS only, so a label placed part way
#: along a wire failed to merge and every affected net split in two.
#: Fewer joined nets meant fewer driver-load pairs to judge, which
#: flattered the number. Both halves looked complete, which is why it
#: went unnoticed until a fixture exposed it.
#:
#: Weighted, unlike the other three, because it is the only one that
#: measurably discriminates without overturning a validated placement
#: behaviour. Halved from 80 to reflect 80% adherence rather than 95%.
_W_FLOW_REVERSAL = 40.0
#: REPORTED, NOT OPTIMISED. Measured against 114 human-drawn sheets from
#: the KiCad demo projects: only 43 of them score zero, the median is 2
#: and the worst is 155. A net legitimately appears in several unrelated
#: places on a sheet, so requiring its labels to share a row or a column
#: penalises what professionals actually do.
#:
#: Kept as a diagnostic because a genuinely stacked group SHOULD line up,
#: but it cannot steer the optimiser until it can tell a stacked group
#: from a net that simply appears twice.
_W_LABEL_SPREAD = 0.0

#: A bypass cap is "beside" its IC within this. Two 100-mil pin pitches
#: plus a body: close enough to read as a pair, far enough not to punish
#: a cap that clears the silkscreen.
_DECAP_NEAR_MILS = 600

#: Roles that mean "this part decouples something".
_DECOUP_ROLE_TAGS = ("decoup", "bypass", "vcc_decoup", "cin_hf")


def _is_ground_port(port) -> bool:
    """Ground glyphs by style, falling back to the text."""
    style = str(getattr(port, "style", "") or "").lower()
    if style.startswith("gnd"):
        return True
    text = str(getattr(port, "text", "") or "").upper()
    return text in ("GND", "AGND", "DGND", "PGND", "VSS", "EARTH")


def _count_decap_orphans(instances, plan, roles) -> int:
    """Bypass caps that are not beside the part they bypass.

    Distance is measured body-centre to body-centre, which is stable
    under rotation in a way a pin-to-pin measure is not.
    """
    if not plan:
        return 0
    by_refdes = {inst.refdes: inst for inst in instances}
    ic_centres = []
    for inst in instances:
        if roles.get(inst.refdes, "") == "ic":
            box = inst.world_bbox()
            ic_centres.append(((box.x_min + box.x_max) / 2.0, (box.y_min + box.y_max) / 2.0))
    if not ic_centres:
        # Nothing to be near. Not a violation: a sheet with no IC cannot
        # have an orphaned bypass cap.
        return 0

    orphans = 0
    for refdes, role in roles.items():
        if not any(tag in role for tag in _DECOUP_ROLE_TAGS):
            continue
        inst = by_refdes.get(refdes)
        if inst is None:
            continue
        box = inst.world_bbox()
        cx, cy = (box.x_min + box.x_max) / 2.0, (box.y_min + box.y_max) / 2.0
        nearest = min(
            ((cx - ix) ** 2 + (cy - iy) ** 2) ** 0.5 for ix, iy in ic_centres
        )
        if nearest > _DECAP_NEAR_MILS:
            orphans += 1
    return orphans


def _count_rail_direction_flips(instances, ports, canvas=None,
                                plan=None) -> int:
    """A supply glyph below the pins it feeds, or a ground glyph above.

    MATCHED BY NET NAME, not by proximity. A power port carries its net
    in its text, and the plan says which pins are on that net, so there
    is nothing to guess.

    The first version compared each glyph to its NEAREST pin, which is a
    different part more often than not. At a non-zero weight that guess
    pulled a decoupling cap to the wrong side of its IC and
    test_pin_aware_fd_places_parts_on_their_ic_pin_side caught it.

    AND COMPARED AGAINST THE PIN IT ATTACHES TO. The net says which
    pins are candidates; which one the glyph serves is then a question
    of where it was drawn, because a glyph is placed beside the pin it
    feeds. Comparing it against the whole rail instead asks something
    the convention never claimed: a ground net spans the sheet, so its
    median y is an average over unrelated parts, and a glyph correctly
    tucked under its own pin reads as a violation whenever that pin
    sits high on the sheet.

    MEASURED on 1685 comparable glyph placements across the human-drawn
    KiCad demo sheets, once power symbols name their nets globally the
    way KiCad means them: 40.6% violate the rail-median form and 7.1%
    violate this one. Supplies up and grounds down is a real convention
    professionals hold to about 93% of the time; the 40.6% was the
    measure being wrong, not the sheets.

    Canvas Y is UP, so a glyph ABOVE the parts has the LARGER y.
    """
    if not ports or canvas is None or plan is None:
        return 0

    pins_by_net: dict[str, list[tuple[int, int]]] = {}
    for net in getattr(plan, "nets", None) or []:
        points: list[tuple[int, int]] = []
        for ref in getattr(net, "pins", None) or []:
            endpoint = canvas.pin_world(
                str(getattr(ref, "refdes", "") or ""),
                str(getattr(ref, "pin", "") or ""),
            )
            if endpoint is not None:
                points.append((endpoint.x, endpoint.y))
        if points:
            pins_by_net[str(getattr(net, "name", "") or "")] = points

    flips = 0
    for port in ports:
        points = pins_by_net.get(str(getattr(port, "text", "") or ""))
        if not points:
            # No net of that name, so nothing to be above or below.
            continue
        # Nearest pin ON THE NET. The net is the electrical constraint
        # and distance only picks which of its pins this glyph serves,
        # so this is not the proximity guess that has been wrong here
        # before: it never reaches across to an unrelated net.
        _, pin_y = min(points,
                       key=lambda pt: abs(pt[0] - port.x) + abs(pt[1] - port.y))
        if _is_ground_port(port):
            if port.y > pin_y:        # ground drawn above what it returns
                flips += 1
        else:
            if port.y < pin_y:        # supply drawn below what it feeds
                flips += 1
    return flips

def _count_flow_reversals(instances, canvas=None, plan=None) -> int:
    """An output driving an input that sits to its left.

    PAIRED BY NET WHEN THE PLAN SUPPLIES ONE. The first version paired
    each output with its NEAREST input, which is a guess: without nets
    the closest input is frequently on an unrelated one. MEASURED with
    that guess against 2986 output pins on 68 human-drawn KiCad demo
    sheets, it flagged 36% of them, which is far too high for a real
    convention and is mostly the pairing being wrong.

    With nets, an output is compared only against inputs it actually
    drives, and there is nothing left to guess.

    Only pins whose electrical type is known are considered, so a
    library that leaves everything passive contributes nothing rather
    than contributing noise.
    """
    kinds: dict[tuple[str, str], str] = {}
    for inst in instances:
        try:
            endpoints = inst.all_pin_endpoints()
        except Exception:             # noqa: BLE001 - symbol model varies
            continue
        for pin in endpoints:
            kind = str(getattr(pin, "electrical_type", "") or "").lower()
            kinds[(inst.refdes, str(pin.pin_id))] = kind

    if plan is None or canvas is None:
        # No nets to pair on. Reporting zero is right: the previous
        # nearest-pin fallback measured its own guess.
        return 0

    reversals = 0
    for net in getattr(plan, "nets", None) or []:
        outs, ins = [], []
        for ref in getattr(net, "pins", None) or []:
            refdes = str(getattr(ref, "refdes", "") or "")
            pin_id = str(getattr(ref, "pin", "") or "")
            kind = kinds.get((refdes, pin_id), "")
            endpoint = canvas.pin_world(refdes, pin_id)
            if endpoint is None:
                continue
            if kind.startswith("output"):
                outs.append(endpoint.x)
            elif kind.startswith("input"):
                ins.append(endpoint.x)
        for ox in outs:
            # One reversal per net, not per pair: a fanout of eight to
            # the left is one routing mistake, not eight.
            if any(ix < ox for ix in ins):
                reversals += 1
                break
    return reversals


def _count_label_column_spread(labels) -> int:
    """Same-net labels that do not share a column or a row.

    A net drawn in several places should have its labels lined up. Only
    nets with two or more labels can be misaligned, and a net whose
    labels all share EITHER an x or a y is aligned.
    """
    by_net: dict[str, list[tuple[int, int]]] = {}
    for lab in labels:
        text = str(getattr(lab, "text", "") or "").strip()
        if not text:
            continue
        by_net.setdefault(text, []).append((lab.x, lab.y))

    spread = 0
    for positions in by_net.values():
        if len(positions) < 2:
            continue
        xs = {x for x, _ in positions}
        ys = {y for _, y in positions}
        if len(xs) > 1 and len(ys) > 1:
            spread += 1
    return spread


def score_canvas(
    canvas: SchematicCanvas,
    plan: Optional[DesignPlan] = None,
    *,
    sheet: Optional[str] = None,
) -> LayoutScore:
    """Return a badness score for one sheet of the canvas.

    Lower is better. The score is interpretable -- a value of 0 is a
    "perfect" layout (no crossings, no body intersections, square
    bbox, minimal wires). Real designs land in the hundreds-to-low-
    thousands.

    ``plan`` is optional; when supplied the wire-net membership is
    available for richer diagnostics (currently unused but plumbed in
    for future expansion).
    """
    sheet_name = sheet or (canvas.sheets[0].name if canvas.sheets else "main")
    instances = canvas.instances_on(sheet_name)
    wires = canvas.wires_on(sheet_name)
    ports = canvas.power_ports_on(sheet_name)
    if not instances:
        return LayoutScore()

    body_rects = [inst.world_bbox() for inst in instances]
    wire_segs = [(w.x1, w.y1, w.x2, w.y2) for w in wires]
    wire_nets = [w.net for w in wires]

    crossings = _count_wire_crossings(wire_segs, wire_nets)
    through_body = _count_wires_through_bodies(
        wire_segs, body_rects, instances
    )
    overlaps = _count_body_overlaps(body_rects)
    sheet_obj = next((s for s in canvas.sheets if s.name == sheet_name), None)
    target_ratio = 1.0
    if sheet_obj is not None and sheet_obj.height_mils:
        target_ratio = sheet_obj.width_mils / sheet_obj.height_mils
    aspect_pen = _aspect_ratio_penalty(body_rects, target_ratio)
    length = _total_wire_length(wire_segs)
    port_count = len(ports)
    align_pen = _alignment_penalty(body_rects)
    shunt_on_side = _count_shunt_on_side(instances, plan)
    long_wires = sum(1 for (x1, y1, x2, y2) in wire_segs
                     if abs(x2 - x1) + abs(y2 - y1) > _LONG_WIRE_MILS)
    row_bands_per_part = _row_bands_per_part(instances)

    # Structural features. Roles come from the plan when it is supplied,
    # widened by the same structural inference the priors consumer uses,
    # so an untagged decoupling cap still counts as one.
    roles: dict[str, str] = {}
    if plan is not None:
        try:
            from eda_agent.design.priors import (
                _infer_crystal_roles,
                _infer_decoup_roles,
            )
            inferred = _infer_decoup_roles(plan)
            inferred.update(_infer_crystal_roles(plan))
        except Exception:             # noqa: BLE001 - inference is best effort
            inferred = {}
        roles = {
            p.refdes: (p.role or inferred.get(p.refdes, "")) for p in plan.parts
        }

    decap_orphans = _count_decap_orphans(instances, plan, roles)
    rail_flips = _count_rail_direction_flips(instances, ports, canvas, plan)
    flow_rev = _count_flow_reversals(instances, canvas, plan)
    label_spread = _count_label_column_spread(canvas.labels_on(sheet_name))

    # Try the learned model first; fall back to the heuristic if no
    # quality_model.json is bundled (fresh install, no votes yet).
    learned = _load_quality_model()
    if learned is not None:
        breakdown, total = _apply_learned_model(learned, {
            "wire_crossings": crossings,
            "wires_through_bodies": through_body,
            "body_overlaps": overlaps,
            "aspect_ratio_penalty": aspect_pen,
            "total_wire_length": length,
            "port_count": port_count,
            "alignment_penalty": align_pen,
            "shunt_on_side": shunt_on_side,
            "long_wires": long_wires,
            "row_bands_per_part": row_bands_per_part,
        })
        # A model trained before these existed carries no weight for
        # them. Added at their heuristic value rather than dropped, so
        # turning a model on cannot make the score BLIND to convention.
        for name, value, weight in (
            ("decap_orphans", decap_orphans, _W_DECAP_ORPHAN),
            ("rail_flips", rail_flips, _W_RAIL_FLIP),
            ("flow_reversals", flow_rev, _W_FLOW_REVERSAL),
            ("label_spread", label_spread, _W_LABEL_SPREAD),
        ):
            if name not in breakdown:
                breakdown[name] = value * weight
                total += breakdown[name]
    else:
        breakdown = {
            "crossings": crossings * _W_CROSSINGS,
            "through_body": through_body * _W_THROUGH_BODY,
            "overlaps": overlaps * _W_OVERLAP,
            "aspect": aspect_pen * _W_ASPECT,
            "length": length * _W_LENGTH,
            "ports": port_count * _W_PORTS,
            "alignment": align_pen * _W_ALIGNMENT,
            "decap_orphans": decap_orphans * _W_DECAP_ORPHAN,
            "rail_flips": rail_flips * _W_RAIL_FLIP,
            "flow_reversals": flow_rev * _W_FLOW_REVERSAL,
            "label_spread": label_spread * _W_LABEL_SPREAD,
        }
        total = sum(breakdown.values())

    return LayoutScore(
        total=total,
        wire_crossings=crossings,
        wires_through_bodies=through_body,
        body_overlaps=overlaps,
        aspect_ratio_penalty=aspect_pen,
        total_wire_length=length,
        port_count=port_count,
        alignment_penalty=align_pen,
        decap_orphans=decap_orphans,
        rail_direction_flips=rail_flips,
        flow_reversals=flow_rev,
        label_column_spread=label_spread,
        shunt_on_side=shunt_on_side,
        long_wires=long_wires,
        row_bands_per_part=row_bands_per_part,
        breakdown=breakdown,
    )


# A wire segment longer than this is a RUN rather than a stub or a hop.
# Measured on 176 public sheets: humans draw none in the median sheet, the
# engine draws 10% of its segments that long.
_LONG_WIRE_MILS = 1000


def _count_shunt_on_side(instances, plan) -> int:
    """2-pin rail passives whose pins point left-right instead of up-down.

    The convention humans hold to most tightly: 98% of their decoupling caps
    stand upright. Judged in the WORLD frame off the placed pin coordinates,
    never off the rotation value, which is library-relative.
    """
    if plan is None:
        return 0
    from eda_agent.design.force_directed import _rotation_for_part
    from eda_agent.design.motifs import _kind_from_refdes

    parts = {p.refdes: p for p in plan.parts}
    n = 0
    for inst in instances:
        part = parts.get(inst.refdes)
        if part is None or _kind_from_refdes(inst.refdes) not in ("R", "C", "L"):
            continue
        eps = list(inst.all_pin_endpoints())
        if len(eps) != 2:
            continue
        if _rotation_for_part(part, plan.nets) != 270:
            continue
        a, b = eps
        if abs(a.x - b.x) >= abs(a.y - b.y):
            n += 1
    return n


def _row_bands_per_part(instances, tol: int = 200) -> float:
    """Horizontal bands the parts occupy, per part. Lower is denser.

    Humans put 2.5 parts in a band; the engine 1.57. Bands are runs of part
    centres within ``tol`` of the previous one when sorted by y.
    """
    ys = sorted(inst.y for inst in instances)
    if not ys:
        return 0.0
    bands = 1
    for prev, cur in zip(ys, ys[1:]):
        if cur - prev > tol:
            bands += 1
    return bands / len(ys)


def _alignment_penalty(body_rects, tol: int = 100) -> float:
    """Fraction of bodies NOT sharing a row or column with another body.

    Body centres are snapped to ``tol`` mils; a body is aligned if its
    snapped centre-x or centre-y is shared by at least one other body.
    Returns 0.0 when every body lines up (best) and approaches 1.0 when
    none do. Single-body sheets are trivially aligned (0.0). ``body_rects``
    are ``SymbolBBox`` objects (x_min/y_min/x_max/y_max).
    """
    if len(body_rects) < 2:
        return 0.0

    def snap(v: float) -> int:
        return int(round(v / tol) * tol)

    cxs = [snap((r.x_min + r.x_max) / 2.0) for r in body_rects]
    cys = [snap((r.y_min + r.y_max) / 2.0) for r in body_rects]
    xcount: dict[int, int] = {}
    ycount: dict[int, int] = {}
    for x in cxs:
        xcount[x] = xcount.get(x, 0) + 1
    for y in cys:
        ycount[y] = ycount.get(y, 0) + 1
    aligned = sum(
        1 for i in range(len(body_rects))
        if xcount[cxs[i]] >= 2 or ycount[cys[i]] >= 2
    )
    return 1.0 - aligned / len(body_rects)


def _count_wire_crossings(
    segs: list[tuple[int, int, int, int]],
    nets: Optional[list[str]] = None,
) -> int:
    """Pairs of axis-aligned wires that cross.

    Only counts true crossings (horizontal segment intersecting
    vertical segment at an interior point of both). Coincident
    parallel overlaps are NOT counted here -- those would inflate the
    score for parallel buses that are legitimately stacked.

    When ``nets`` (a per-segment net name, parallel to ``segs``) is
    supplied, a crossing between two segments of the SAME net is NOT
    counted: same-net wires meeting mid-span is an electrical junction
    (drawn with a dot), not a readability fault. Only crossings between
    DIFFERENT nets -- where two unrelated signals visually overlap -- are
    counted. Without ``nets`` every crossing counts (back-compatible).
    """
    horiz = [(min(x1, x2), max(x1, x2), y1, (nets[i] if nets else None))
             for i, (x1, y1, x2, y2) in enumerate(segs) if y1 == y2]
    vert = [(x1, min(y1, y2), max(y1, y2), (nets[i] if nets else None))
            for i, (x1, y1, x2, y2) in enumerate(segs) if x1 == x2]
    count = 0
    for hx_lo, hx_hi, hy, hnet in horiz:
        for vx, vy_lo, vy_hi, vnet in vert:
            if hx_lo < vx < hx_hi and vy_lo < hy < vy_hi:
                # Same non-empty net => junction, not a crossing fault.
                if hnet is not None and hnet == vnet and hnet != "":
                    continue
                count += 1
    return count


def _count_wires_through_bodies(
    segs: list[tuple[int, int, int, int]],
    body_rects: list,
    instances: list,
) -> int:
    """Wires whose path crosses a component body interior.

    Skips wires that merely TOUCH the body's edge (those are legitimate
    pin connections). Counts only crossings of the body's INTERIOR --
    i.e., the wire passes from one side of the bbox to the other
    through the inside.

    AND SKIPS THE BODY OF A PART THE WIRE ATTACHES TO. Some symbols
    draw their graphics around the connection point rather than beside
    it: a test point is a small mark CENTRED on its own pin, so every
    wire leaving one crosses its body by construction. Measured on
    nRF54L15, three of four reported crossings were test points
    charging 400 points each for existing. ``instances`` was already a
    parameter of this function and was never read; this is what it was
    for.

    The fourth was real, and still counts: a ground wire crossing an
    IC it does not connect to.
    """
    attached: dict[tuple[int, int], set[str]] = {}
    owner: list[str] = []
    for inst in instances or []:
        ref = str(getattr(inst, "refdes", "") or "")
        owner.append(ref)
        try:
            endpoints = inst.all_pin_endpoints()
        except Exception:             # noqa: BLE001 - symbol model varies
            continue
        for pin in endpoints:
            attached.setdefault((pin.x, pin.y), set()).add(ref)

    count = 0
    for (x1, y1, x2, y2) in segs:
        connects = attached.get((x1, y1), set()) | attached.get((x2, y2), set())
        for i, bb in enumerate(body_rects):
            if connects and i < len(owner) and owner[i] in connects:
                continue
            if _segment_crosses_bbox_interior(x1, y1, x2, y2,
                                              bb.x_min, bb.y_min,
                                              bb.x_max, bb.y_max):
                count += 1
                break  # one crossing per wire is enough; avoid double-count
    return count


def _segment_crosses_bbox_interior(
    x1: int, y1: int, x2: int, y2: int,
    bx1: int, by1: int, bx2: int, by2: int,
) -> bool:
    """True iff an axis-aligned wire segment passes through the bbox interior.

    Edge-touching segments (the wire terminates on a pin at the body
    edge) do not count. Interior means the segment has a non-zero-length
    overlap strictly inside the bbox.
    """
    if x1 == x2:
        # Vertical segment. Crosses interior iff x is inside (bx1, bx2)
        # AND segment y-range intersects (by1, by2) on the interior.
        if not (bx1 < x1 < bx2):
            return False
        seg_lo, seg_hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return seg_lo < by2 and seg_hi > by1 and seg_lo < by2 and seg_hi > by1
    if y1 == y2:
        if not (by1 < y1 < by2):
            return False
        seg_lo, seg_hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return seg_lo < bx2 and seg_hi > bx1
    return False


def _count_body_overlaps(body_rects: list) -> int:
    """Pairs of component bboxes that intersect over a positive AREA.

    Legitimate adjacency (sharing an edge but not overlapping) is OK.

    The area matters, not just the relative positions. A symbol with no
    drawn body -- a net tie, or a part whose graphics this reader does
    not recognise -- has a bbox of zero height or width, and a flat box
    lying across a real one satisfies every edge comparison while
    covering nothing. That charged 1000 points, the band reserved for
    illegal geometry, for a capacitor sitting beside an IC.
    """
    count = 0
    for i in range(len(body_rects)):
        for j in range(i + 1, len(body_rects)):
            a, b = body_rects[i], body_rects[j]
            width = min(a.x_max, b.x_max) - max(a.x_min, b.x_min)
            height = min(a.y_max, b.y_max) - max(a.y_min, b.y_min)
            if width > 0 and height > 0:
                count += 1
    return count


def _aspect_ratio_penalty(body_rects: list, target: float = 1.0) -> float:
    """Penalty for a layout whose proportions fight the sheet.

    THE TARGET IS THE SHEET, NOT A SQUARE. Schematics are drawn on
    landscape paper, so filling one evenly produces a wide layout, and
    an earlier version of this asked for a square.

    MEASURED on 104 human-drawn KiCad demo sheets: the median layout is
    1.49 times wider than tall, 81.7% are wider than tall at all, and
    only 8.7% are anywhere near square.
    Replicated on 1046 layouts from 172 public hardware repositories:
    median 1.55, and 82% wider than tall. A4's drawing area is 1.51, so
    the paper and the practice agree on two independent corpora. Against a square target this
    scorer charged those professionals a median penalty of 0.34 for
    doing the normal thing.

    A4's drawing area is 11500 x 7600 mils, a ratio of 1.51, so the
    target is not fitted to that corpus: it comes from the paper, and
    the corpus agrees with it. Returns 0 when the layout matches the
    sheet's proportions and approaches 1 as it degenerates either way.
    """
    if not body_rects:
        return 0.0
    x_min = min(b.x_min for b in body_rects)
    y_min = min(b.y_min for b in body_rects)
    x_max = max(b.x_max for b in body_rects)
    y_max = max(b.y_max for b in body_rects)
    w = max(1, x_max - x_min)
    h = max(1, y_max - y_min)
    target = target if target > 0 else 1.0
    ratio = (w / h) / target
    # Symmetric in the two ways of being wrong: twice as wide as the
    # sheet and half as wide both score 0.5.
    ratio = max(ratio, 1.0 / ratio)
    return 1.0 - 1.0 / ratio


def _total_wire_length(segs: list[tuple[int, int, int, int]]) -> int:
    return sum(abs(x1 - x2) + abs(y1 - y2) for (x1, y1, x2, y2) in segs)


# ---------------------- learned model (Bradley-Terry) ----------------------


_DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "quality_model.json"
_MODEL_CACHE: Optional[dict[str, Any]] = None
_MODEL_CACHE_PATH: Optional[Path] = None


def _quality_model_path() -> Path:
    """Resolve the model file location. Env override for tests / custom builds."""
    override = os.environ.get("EDA_AGENT_QUALITY_MODEL")
    if override:
        return Path(override)
    return _DEFAULT_MODEL_PATH


def _load_quality_model() -> Optional[dict[str, Any]]:
    """Read the BT-trained weights from disk; cache in-memory.

    Returns None when no model file is present (fresh install with no
    votes yet). The cache is invalidated when the file path changes
    (via env var override) so tests stay deterministic.
    """
    global _MODEL_CACHE, _MODEL_CACHE_PATH
    path = _quality_model_path()
    if _MODEL_CACHE is not None and _MODEL_CACHE_PATH == path:
        return _MODEL_CACHE
    if not path.exists():
        _MODEL_CACHE = None
        _MODEL_CACHE_PATH = path
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("quality_model at %s is unreadable: %s", path, exc)
        _MODEL_CACHE = None
        _MODEL_CACHE_PATH = path
        return None
    _MODEL_CACHE = data
    _MODEL_CACHE_PATH = path
    return data


def reset_model_cache() -> None:
    """Force the next score_canvas call to re-read the model from disk.

    Useful right after running the trainer -- otherwise the running
    Python process keeps using the stale weights it loaded at startup.
    """
    global _MODEL_CACHE, _MODEL_CACHE_PATH
    _MODEL_CACHE = None
    _MODEL_CACHE_PATH = None


# Feature name in the model file -> key in the score breakdown. The first
# six are the original Bradley-Terry features and keep their breakdown
# names; the rest are the human-law features a corpus-trained model adds.
_LEARNED_FEATURES: tuple[tuple[str, str], ...] = (
    ("wire_crossings", "crossings"),
    ("wires_through_bodies", "through_body"),
    ("body_overlaps", "overlaps"),
    ("aspect_ratio_penalty", "aspect"),
    ("total_wire_length", "length"),
    ("port_count", "ports"),
    ("alignment_penalty", "alignment"),
    ("shunt_on_side", "shunt_on_side"),
    ("long_wires", "long_wires"),
    ("row_bands_per_part", "row_bands"),
)
_ORIGINAL_SIX = frozenset(name for name, _ in _LEARNED_FEATURES[:6])


def raw_features(score: "LayoutScore") -> dict[str, float]:
    """The feature dict a model is fitted on and applied to.

    One function, used by the preference logger, the corpus pair miner and
    ``score_canvas`` itself, so a model can never be trained on one feature
    definition and applied to another.
    """
    return {
        "wire_crossings": float(score.wire_crossings),
        "wires_through_bodies": float(score.wires_through_bodies),
        "body_overlaps": float(score.body_overlaps),
        "aspect_ratio_penalty": float(score.aspect_ratio_penalty),
        "total_wire_length": float(score.total_wire_length),
        "port_count": float(score.port_count),
        "alignment_penalty": float(score.alignment_penalty),
        "shunt_on_side": float(score.shunt_on_side),
        "long_wires": float(score.long_wires),
        "row_bands_per_part": float(score.row_bands_per_part),
    }


def _apply_learned_model(
    model: dict[str, Any],
    features: dict[str, float],
) -> tuple[dict[str, float], float]:
    """Convert raw features to a score using the learned weights.

    Bradley-Terry trains so that HIGHER s(canvas) = BETTER layout. To keep
    the same "lower is better" contract the heuristic uses (and that
    ``build_best_canvas_from_plan`` consumes via `min`), the BT score is
    negated before returning. The breakdown is per-feature so callers see
    which feature drove the score.

    A model file names only the features it was fitted on. The original six
    always appear in the breakdown (weight 0 when absent, as before); a
    human-law feature appears only when the model carries a weight for it,
    so a model trained before those features existed scores exactly as it
    did.
    """
    raw = model.get("weights_raw", {})
    intercept = float(model.get("intercept_raw", 0.0))
    contributions: dict[str, float] = {}
    for fname, key in _LEARNED_FEATURES:
        if fname not in raw and fname not in _ORIGINAL_SIX:
            continue
        contributions[key] = (float(raw.get(fname, 0.0))
                              * float(features.get(fname, 0.0)))
    contributions["intercept"] = intercept
    bt_score = sum(contributions.values())
    # BT_score = goodness; we want badness, so negate.
    breakdown = {k: -v for k, v in contributions.items()}
    return breakdown, -bt_score
