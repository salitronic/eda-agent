# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Autonomy state machine: ``design_next_action`` core (roadmap 1.4).

The journal (``session.py``) records what happened; this module decides what
to do next. Given a replayed ``SessionState`` it returns a single
``NextAction``: the next pipeline stage to work on, its goal, the exact tools
to reach for, the exit gate that marks it done, and, when a stage has failed
too many times or the run is waiting on a human, a ``blocked`` verdict with
the question to ask.

This is what lets ANY MCP client drive the full spec-to-board pipeline
without memorizing the workflow: the client loops "call design_next_action →
do what it says → log the result" until the run is complete or blocked. A
weaker client produces a plainer board, never a broken pipeline, because the
sequencing and gates live here, server-side.

Pure Python, no Altium, fully unit-testable. The per-stage playbooks are
data (``STAGE_PLAYBOOKS``) so they stay easy to audit and extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .session import SessionState

# A stage that fails this many times stops the loop and asks the human,
# rather than burning turns on an unrecoverable step.
MAX_STAGE_ATTEMPTS = 3

# Verdicts a client acts on.
PROCEED = "proceed"      # do this stage now
RETRY = "retry"          # this stage failed; try again with the fix guidance
BLOCKED = "blocked"      # stop and ask the human
COMPLETE = "complete"    # all stages done


@dataclass
class NextAction:
    session_id: str
    status: str                       # proceed | retry | blocked | complete
    stage: Optional[str]
    goal: str
    suggested_tools: list = field(default_factory=list)
    exit_gate: str = ""
    guidance: str = ""
    attempt: int = 0
    open_question: Optional[str] = None


# Per-stage playbook: what the stage is for, which tools do it, and what
# "done" means. Kept deliberately tool-name-explicit so the client doesn't
# have to guess the surface.
STAGE_PLAYBOOKS = {
    "requirement": {
        "goal": "Capture the design requirement as structured, checkable facts.",
        "tools": ["design_validate_requirement"],
        "exit_gate": "No unresolved open_questions; outputs, power source, and "
                     "IO all specified.",
    },
    "architecture": {
        "goal": "Choose a topology with calculator-backed numbers; record the "
                "decision and rationale in the plan.",
        "tools": ["design_compute_component_value", "pcb_calc_impedance",
                  "pcb_calc_trace_width_for_current"],
        "exit_gate": "A topology is chosen and its key values are computed, not "
                     "guessed.",
    },
    "plan": {
        "goal": "Author the DesignPlan: parts wired to nets via blocks/buses.",
        "tools": ["design_get_discipline", "design_snapshot_inventory",
                  "design_add_part", "design_add_circuit_block",
                  "design_connect_bus", "design_compose_netlist"],
        "exit_gate": "A complete DesignPlan exists with every part and net.",
    },
    "plan_verification": {
        "goal": "Vet the plan offline, before anything reaches the editor.",
        "tools": ["design_validate_plan", "design_review_plan",
                  "design_describe_circuits", "design_generate_bom"],
        "exit_gate": "design_validate_plan ok:true; ERC-lite clean; circuit "
                     "values match intent.",
    },
    "library_readiness": {
        "goal": "Ensure every part has a verified symbol + footprint (+3D).",
        "tools": ["design_snapshot_inventory", "lib_search",
                  "lib_inspect_cse_zip", "lib_create_symbol",
                  "lib_create_footprint"],
        "exit_gate": "No part is needs_creation; footprints checked against the "
                     "datasheet land pattern.",
    },
    "schematic_emit": {
        "goal": "Instantiate the plan as a neat, ERC-clean schematic. One "
                "engine draws every schematic: design_layout_schematic for "
                "the geometry as data, design_preview_plan for the same "
                "layout as SVG, design_execute_plan to emit it. Do not look "
                "for a second layout engine to compare against; there is "
                "not one. CHANGING a sheet goes the same way: re-run "
                "design_execute_plan and it moves only what the plan "
                "changed. For a sheet this engine did not draw, "
                "design_plan_from_sheet reads a plan back off it first.",
        "tools": ["design_layout_schematic", "design_preview_plan",
                  "design_execute_plan", "design_plan_from_sheet",
                  "design_hints_from_sheet",
                  "design_audit_schematic", "design_validate"],
        "exit_gate": "ERC clean; visual-review rubric passes; no floating pins.",
    },
    "sch_to_pcb": {
        "goal": "Transfer the netlist to a PCB without a dialog someone has "
                "to click.",
        "tools": ["pcb_place_components", "pcb_build_from_project",
                  "proj_compare_sch_pcb", "obj_crossref_net"],
        "exit_gate": "proj_compare_sch_pcb reports in_sync:true.",
    },
    "rules_stackup": {
        "goal": "Set the layer stack and design rules from a fab profile.",
        "tools": ["design_load_fab_profile", "design_synthesize_rules",
                  "pcb_create_design_rule", "pcb_modify_layer"],
        "exit_gate": "Rules + stackup applied; every value traces to the profile "
                     "or a calculator.",
    },
    "placement": {
        "goal": "Place components: minimize HPWL, keep groups together.",
        "tools": ["pcb_plan_placement", "pcb_move_components",
                  "design_visual_review"],
        "exit_gate": "No overlaps; all parts on-board; visual review passes.",
    },
    "routing": {
        "goal": "Route all nets DRC-clean; critical nets by template first.",
        "tools": ["route_plan", "pcb_place_tracks", "pcb_place_via",
                  "pcb_run_drc", "route_plan_repairs"],
        "exit_gate": "100% routed; DRC clean.",
    },
    "pours_tuning": {
        "goal": "Add planes/pours, stitching, teardrops, length tuning.",
        "tools": ["pcb_start_polygon_placement", "pcb_place_stitching_vias",
                  "pcb_calc_length_match", "pcb_tune_length"],
        "exit_gate": "Planes poured; skew within budget; DRC still clean.",
    },
    "verification": {
        "goal": "Final closed-loop checks: DRC + ERC + audits + visual.",
        "tools": ["pcb_run_drc", "proj_run_erc", "design_lint_report",
                  "design_visual_review"],
        "exit_gate": "DRC + ERC clean; lint sweep clean; audits pass.",
    },
    "outputs": {
        "goal": "Generate the fab package, BOM, and assembly outputs.",
        "tools": ["proj_generate_fab_package", "design_generate_bom",
                  "proj_export_step"],
        "exit_gate": "Fab package + BOM produced; manifest complete.",
    },
}


def next_action(state: SessionState) -> NextAction:
    """Decide the single next action for a design session."""
    sid = state.session_id

    # 1. A human question outranks everything: stop and surface it.
    if state.open_question:
        return NextAction(
            session_id=sid,
            status=BLOCKED,
            stage=state.current_stage or state.next_stage,
            goal="Waiting on a human decision.",
            guidance=f"BLOCKED: ask the user: {state.open_question}",
            open_question=state.open_question,
        )

    # 2. All stages ok -> done.
    if state.complete or state.next_stage is None:
        return NextAction(
            session_id=sid,
            status=COMPLETE,
            stage=None,
            goal="Design pipeline complete.",
            guidance="All 13 pipeline stages are ok. Nothing left to do.",
        )

    stage = state.next_stage
    play = STAGE_PLAYBOOKS.get(stage, {})
    attempts = state.attempts.get(stage, 0)
    last_status = state.stage_status.get(stage)

    # 3. Too many failures -> escalate to the human.
    if attempts >= MAX_STAGE_ATTEMPTS and last_status != "ok":
        return NextAction(
            session_id=sid,
            status=BLOCKED,
            stage=stage,
            goal=play.get("goal", ""),
            guidance=(
                f"BLOCKED: stage '{stage}' failed {attempts} times "
                f"(limit {MAX_STAGE_ATTEMPTS}). Ask the user how to proceed or "
                f"relax the requirement."
            ),
            attempt=attempts,
            open_question=f"Stage '{stage}' keeps failing: how should I proceed?",
        )

    # 4. Normal path: proceed (or retry if this stage has a prior failure).
    status = RETRY if last_status in ("failed", "blocked") else PROCEED
    prefix = "" if status == PROCEED else f"Retry ({attempts + 1}/{MAX_STAGE_ATTEMPTS}): "
    return NextAction(
        session_id=sid,
        status=status,
        stage=stage,
        goal=play.get("goal", ""),
        suggested_tools=list(play.get("tools", [])),
        exit_gate=play.get("exit_gate", ""),
        guidance=(
            f"{prefix}{play.get('goal', stage)} "
            f"When done, log design_session_log(event='stage_result', "
            f"stage='{stage}', status='ok'): or status='blocked' with a "
            f"question if you need the user."
        ),
        attempt=attempts,
    )
