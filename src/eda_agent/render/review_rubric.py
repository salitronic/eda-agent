# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Visual-review rubric + loop protocol for ``design_visual_review``.

The render tools turn a design into a picture; this module turns the
picture into a *critique*. It encodes, per document kind, the things a
human reviewer looks for at a glance, and -- crucially -- pairs each
visual cue with the existing quantitative audit that measures it. Vision
finds candidates; the audits confirm and locate them. That render ->
look -> critique -> cross-check -> fix -> re-render cadence is the
error-correction loop these multimodal design agents run.

Pure data + a tiny selector; no Altium, no I/O.
"""

from __future__ import annotations

from typing import Any


# Each rubric item: what to look for, and which audit tool(s) measure it
# exactly (so the agent can promote a visual suspicion to a hard finding).
_SCHEMATIC_RUBRIC: list[dict[str, Any]] = [
    {"check": "Component bodies overlapping or touching",
     "audits": ["design_audit_schematic"]},
    {"check": "Wires routed straight through a component body",
     "audits": ["design_audit_schematic"]},
    {"check": "Power/ground ports duplicated or clustered on one node",
     "audits": ["design_audit_schematic", "audit_find_orphan_power_objects",
                "audit_power_port_orientation"]},
    {"check": "Net labels overlapping, illegible, or floating off a wire",
     "audits": ["audit_find_orphan_net_labels", "audit_find_floating_ports"]},
    {"check": "Ports/sheet-entries that do not match across sheets",
     "audits": ["audit_find_unmatched_ports"]},
    {"check": "Designators colliding, duplicated, or missing",
     "audits": ["audit_find_designator_collisions"]},
    {"check": "Decoupling caps not drawn next to their IC's power pins",
     "audits": ["audit_find_missing_decoupling"]},
    {"check": "Signal flow not left-to-right / power-top / ground-bottom",
     "audits": []},
]

_PCB_RUBRIC: list[dict[str, Any]] = [
    {"check": "Components off the board or straddling the outline",
     "audits": ["audit_find_components_outside_board_outline"]},
    {"check": "Overlapping courtyards / silkscreen collisions",
     "audits": ["pcb_check_placement_collision"]},
    {"check": "Designators mirrored, off their part, or illegible",
     "audits": ["audit_find_mirrored_pcb_text",
                "audit_find_designator_collisions"]},
    {"check": "Decoupling caps not adjacent to their IC pins",
     "audits": ["audit_find_missing_decoupling"]},
    {"check": "Long airwires / obvious ratsnest crossings (placement quality)",
     "audits": ["pcb_get_unrouted_nets", "pcb_plan_placement"]},
    {"check": "Pads / copper too close to the board edge",
     "audits": ["audit_find_pads_near_board_edge"]},
    {"check": "Components off the routing/placement grid",
     "audits": ["audit_find_off_grid_components"]},
    {"check": "Acute track angles or antenna stubs",
     "audits": ["audit_find_acute_angles", "audit_find_via_antennas"]},
]

_LOOP_PROTOCOL: list[str] = [
    "1. Render: this tool wrote the image (png_path, or svg_path if "
    "rasterization was unavailable).",
    "2. Look: open png_path with the Read tool so you actually see the "
    "layout.",
    "3. Critique: walk every rubric item; for each, note what you SEE and "
    "roughly where (designator / region).",
    "4. Cross-check: for items with `audits`, run those tools -- vision "
    "surfaces candidates, the audits confirm and give exact coordinates.",
    "5. Fix: apply changes with the normal tools (move/rotate/relabel; for "
    "PCB placement use pcb_plan_placement).",
    "6. Re-render: call design_visual_review again and confirm the issue is "
    "gone and nothing else regressed.",
    "7. Stop when the rubric is clean or a couple of iterations stop "
    "improving things.",
]


def visual_review_guidance(target: str) -> dict[str, Any]:
    """Return the rubric + loop protocol for a document kind.

    ``target`` is ``"schematic"`` or ``"pcb"``. Unknown kinds get an
    empty rubric but still the loop protocol.
    """
    t = (target or "").strip().lower()
    if t in ("schematic", "sch", "schdoc"):
        rubric = _SCHEMATIC_RUBRIC
    elif t in ("pcb", "pcbdoc", "board"):
        rubric = _PCB_RUBRIC
    else:
        rubric = []
    from ..tools.folds import call_form

    items = []
    for item in rubric:
        entry = dict(item)
        if "audits" in entry:
            # Named by the advertised tool that runs each audit.
            entry["audits"] = list(dict.fromkeys(call_form(a) for a in entry["audits"]))
        items.append(entry)
    return {
        "rubric": items,
        "loop_protocol": list(_LOOP_PROTOCOL),
    }
