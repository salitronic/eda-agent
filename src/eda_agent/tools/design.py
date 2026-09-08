# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design-agent MCP tools, surfaces the design discipline + primitives.

Claude Code is the planner. It calls ``design_get_discipline`` to read
the rules, ``design_snapshot_inventory`` to learn what parts exist in
the user's libraries, then constructs a DesignPlan JSON, validates it
with ``design_validate_plan``, and hands it to ``design_execute_plan``
to instantiate the schematic.

This module deliberately makes no Anthropic API calls, the AI is the
client (Claude Code), this is just the tool layer it drives.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import ValidationError

from ..design.audit import audit_schematic as run_audit_schematic
from ..design.component_values import (
    buck_inductor,
    capacitor_energy,
    crystal_load_caps,
    discharge_resistor,
    divider_tolerance,
    feedback_divider,
    holdup_capacitance,
    i2c_pullup,
    junction_temperature,
    led_series_resistor,
    max_power_dissipation,
    nearest_preferred,
    opamp_gain_resistors,
    rc_lowpass,
    required_theta_ja,
    resistor_divider,
)
from ..design.discipline import get_discipline
from ..design.executor import execute_plan_from_json
from ..design.fab_profile import load_fab_profile
from ..design.hierarchy import apply_hierarchy, plan_hierarchy
from ..design.requirement import (
    DesignRequirement,
    summarize_requirement,
    validate_requirement,
)
from ..design.rule_synthesis import synthesize_rules
from ..design.inventory import LibraryInventory, snapshot_live
from ..design.learner import learn_from_layout
from ..design.orchestrator import (
    execute_plan_via_canvas_from_json,
    hints_from_sheet,
    layout_plan_from_json,
    plan_from_live_sheet,
    preview_plan_from_json,
)
from ..design.motif_descriptions import describe_motifs
from ..design.net_classes import classify_nets
from ..design.placement_constraints import infer_placement_constraints
from .bulk_hints import BulkHintTracker
from ..design.bom import bom_summary, generate_bom
from ..design.plan import DesignPlan
from ..design.plan_blocks import (
    add_block,
    add_component,
    compose_netlist,
    connect_bus,
    describe_blocks,
)
from ..design.plan_edit import edit_plan
from ..design.plan_erc import check_plan_erc
from ..design.plan_stats import summarize_plan
from ..design.validator import validate as run_validate


def _schematic_summary(
    score: dict,
    net_representation: dict,
    n_placements: int,
) -> str:
    """One-line plain-language verdict on a schematic layout, distilling the
    aesthetic score + net representation so the planner can judge at a glance.
    """
    cx = int(score.get("wire_crossings", 0))
    bends = int(score.get("total_bends", 0))
    reps: dict[str, int] = {}
    for kind in net_representation.values():
        reps[kind] = reps.get(kind, 0) + 1
    parts = [
        "no wire crossings (clean)" if cx == 0
        else f"{cx} wire crossing(s) (review)",
        f"{bends} bend(s)",
        f"{n_placements} part(s)",
    ]
    if reps:
        parts.append("nets as " + ", ".join(
            f"{reps[k]} {k}" for k in sorted(reps)))
    return "; ".join(parts) + "."


def _divider_payload(r, series: str, mode: str) -> dict[str, Any]:
    """Shape a DividerResult into the tool's JSON response."""
    return {
        "ok": True,
        "mode": mode,
        "r_top_ohms": r.r_top,
        "r_bottom_ohms": r.r_bottom,
        "v_out": r.v_out,
        "v_out_ideal": r.v_out_ideal,
        "error_pct": r.error_pct,
        "series": series.upper(),
        "summary": (f"Rtop={r.r_top:g} ohm, Rbot={r.r_bottom:g} ohm -> "
                    f"{r.v_out:.4f} V ({r.error_pct:+.3f}%)"),
    }


def register_design_tools(mcp) -> None:
    """Register the design-agent tools with the MCP server."""

    @mcp.tool()
    async def design_get_discipline() -> dict[str, Any]:
        """Read the design discipline + DesignPlan JSON schema.

        ALWAYS call this first when starting a design task. The result
        contains the hard rules the planner must follow (net-label-driven
        wiring, datasheet-first part choice, NDA isolation, etc.) plus
        the JSON schema that ``design_execute_plan`` enforces on input.

        Returns:
            A dict with ``discipline`` (markdown text) and
            ``schema`` (DesignPlan JSON schema as a dict).
        """
        return {
            "discipline": get_discipline(),
            "schema": DesignPlan.model_json_schema(),
        }

    # --- session journal (autonomy-harness durable state) ----------------

    def _adapt_action(action: dict) -> dict:
        """Translate one next_action reply for the active backend.

        The state machine is deliberately pure: it knows the pipeline,
        not which editor is attached, and adapting inside it would put
        a backend import in the one module that is fully unit-testable
        without one. So the translation happens here, at the boundary
        where the reply is handed to a client.

        This is the tool an autonomous run calls on every iteration, so
        an Altium-only name in its reply is not a cosmetic wart: the
        client does what the reply says. Before this, `goal`,
        `guidance`, `exit_gate` and `suggested_tools` all reached an
        EasyEDA client naming tools it does not register.
        """
        from ..core.backends import active_backend_name
        from ..design.autonomy import (_EQUIVALENTS, _adapt_lines,
                                       _registered_tools)

        backend = active_backend_name()
        if backend == "altium":
            return action

        available = _registered_tools(backend)
        keys = [k for k in ("goal", "guidance", "exit_gate", "open_question")
                if action.get(k)]
        adapted = _adapt_lines([action[k] for k in keys], backend, available)
        for key, line in zip(keys, adapted):
            action[key] = line

        # The tool list is names, not prose, so the "(not available on
        # this backend)" annotation _adapt_lines adds to a sentence
        # would corrupt it into an uncallable name.
        action["suggested_tools"] = [
            _EQUIVALENTS[t] if _EQUIVALENTS.get(t) in available else t
            for t in action.get("suggested_tools") or []
        ]
        return action

    def _session_store():
        from ..config import get_config
        from ..design.session import SessionStore
        return SessionStore(get_config().workspace_dir / "design_sessions")

    def _resolve_journal(session_id: str):
        """Return a journal for session_id, or the active one if blank."""
        store = _session_store()
        if session_id:
            return store.get(session_id)
        return store.active()

    @mcp.tool()
    async def design_session_start(requirement: str, session_id: str = "") -> dict[str, Any]:
        """Open a durable design-session journal for an autonomous run.

        The journal is append-only state that survives context compaction,
        client restarts, and model switches, so any later client can resume
        the run from recorded fact instead of chat history. Call this once at
        the start of a spec-to-board task, then log stage results as you go
        and read `design_session_resume` to know what's next.

        Args:
            requirement: the design requirement text this run implements.
            session_id: optional explicit id; defaults to a timestamp.

        Returns:
            ``{"session_id": ..., "state": {...}}``.
        """
        store = _session_store()
        journal = store.start(requirement, session_id=session_id or None)
        return {"session_id": journal.session_id, "state": asdict(journal.state())}

    @mcp.tool()
    async def design_session_log(
        event: str,
        session_id: str = "",
        stage: str = "",
        status: str = "",
        text: str = "",
        path: str = "",
        kind: str = "",
        question: str = "",
        revision: int = 0,
    ) -> dict[str, Any]:
        """Append one event to a design-session journal.

        ``event`` is one of: ``stage_enter`` (needs ``stage``),
        ``stage_result`` (``stage`` + ``status`` = ok|blocked|failed),
        ``plan_revision`` (``revision``), ``artifact`` (``path``, ``kind``),
        ``blocked`` (``question``), ``resolved`` (``text`` = answer),
        ``note`` (``text``). Returns the updated derived state.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no active design session; call design_session_start first"}
        try:
            if event == "stage_enter":
                journal.enter_stage(stage)
            elif event == "stage_result":
                journal.stage_result(stage, status, verdict=text)
            elif event == "plan_revision":
                journal.plan_revision(revision, summary=text)
            elif event == "artifact":
                journal.artifact(path, kind=kind)
            elif event == "blocked":
                journal.blocked(question, stage=stage)
            elif event == "resolved":
                journal.resolved(text)
            elif event == "note":
                journal.note(text)
            else:
                return {"error": f"unknown event kind: {event!r}"}
        except ValueError as e:
            return {"error": str(e)}
        return {"session_id": journal.session_id, "state": asdict(journal.state())}

    @mcp.tool()
    async def design_session_status(session_id: str = "") -> dict[str, Any]:
        """Read a design session's derived state (stage map, artifacts, ...).

        With no ``session_id`` the most recently active session is used.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no design sessions found"}
        return {"session_id": journal.session_id, "state": asdict(journal.state())}

    @mcp.tool()
    async def design_session_resume(session_id: str = "") -> dict[str, Any]:
        """Resume a design run: state plus a plain-language next-action hint.

        Surfaces any open blocking question first (answer it before
        proceeding), otherwise names the next pipeline stage to work on. Use
        this at the start of a fresh client session to pick up where the last
        one stopped.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no design sessions found; start one with design_session_start"}
        state = journal.state()
        if state.open_question:
            guidance = f"BLOCKED: ask the user: {state.open_question}"
        elif state.complete:
            guidance = "All 13 pipeline stages complete."
        else:
            guidance = f"Next stage: {state.next_stage}"
        return {
            "session_id": journal.session_id,
            "guidance": guidance,
            "state": asdict(state),
        }

    @mcp.tool()
    async def design_next_action(session_id: str = "") -> dict[str, Any]:
        """Decide the single next action for an autonomous design run.

        Reads the session journal and returns the next 13-stage pipeline step
        server-side, so a client need not memorize the workflow: loop
        "call design_next_action -> do what it says -> design_session_log the
        result" until ``status`` is ``complete`` or ``blocked``.

        Returns a dict with ``status`` (proceed | retry | blocked | complete),
        the ``stage``, its ``goal``, ``suggested_tools`` (exact tool names),
        the ``exit_gate`` that marks it done, and, on ``blocked``, the
        ``open_question`` to put to the user. Bounded retries: a stage that
        fails repeatedly escalates to ``blocked`` instead of looping forever.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no design sessions found; start one with design_session_start"}
        from ..design.state_machine import next_action as _next_action
        action = _next_action(journal.state())
        return _adapt_action(asdict(action))

    @mcp.tool()
    async def design_autonomy_guide() -> dict[str, Any]:
        """The autonomous spec-to-board loop protocol, in one call.

        Returns how to drive a full design run with the harness: the loop
        (start session → design_next_action → execute → log → repeat), the
        13 pipeline stages with their tools and exit gates, the hard
        constraints, and how to resume after context loss. Read this once
        before an autonomous run; `design_next_action` then guides each step.
        """
        from ..design.autonomy import autonomy_guide
        return autonomy_guide()

    # Register the MCP prompt only on a real FastMCP; test harnesses that
    # register tools with a minimal fake mcp (``.tool()`` only, no
    # ``.prompt()``) must not break at registration time.
    if hasattr(mcp, "prompt"):
        @mcp.prompt(
            name="autonomous_design",
            description="Drive a full spec-to-board design run with the harness.",
        )
        def autonomous_design_prompt(requirement: str = "") -> str:
            """MCP prompt: the autonomous-design loop, optionally seeded with a
            requirement. Prompt-capable clients invoke this to start a run."""
            from ..design.autonomy import autonomy_prompt_text
            return autonomy_prompt_text(requirement)

    # --- async jobs (engine runs that outlive a tool timeout) ------------

    @mcp.tool()
    async def design_job_start(kind: str, params: dict) -> dict[str, Any]:
        """Start a long engine run as a background job; returns its id.

        For work that can exceed the MCP tool timeout (routing a dense
        board). Poll `design_job_status`, then `design_job_result` when done.

        Args:
            kind: registered job kind. Currently: ``route`` (offline A*
                router; ``params`` needs a ``geometry`` dict, optional
                ``rules`` / ``net_classes`` / ``grid_pitch_mils``).
            params: keyword arguments for the engine.

        Returns:
            ``{"job_id": ..., "kind": ...}`` or ``{"error": ...}`` for an
            unknown kind.
        """
        from ..design.jobs import JOB_KINDS, get_job_store
        if kind not in JOB_KINDS:
            return {"error": f"unknown job kind {kind!r}; known: {sorted(JOB_KINDS)}"}
        store = get_job_store()
        job_id = store.submit(kind, JOB_KINDS[kind], params or {})
        return {"job_id": job_id, "kind": kind}

    @mcp.tool()
    async def design_job_status(job_id: str = "") -> dict[str, Any]:
        """Status of a background job (or all jobs if ``job_id`` is blank)."""
        from ..design.jobs import get_job_store
        store = get_job_store()
        if not job_id:
            return {"jobs": store.list()}
        rec = store.status(job_id)
        return rec if rec else {"error": f"no such job: {job_id}"}

    @mcp.tool()
    async def design_job_result(job_id: str) -> dict[str, Any]:
        """Fetch a finished job's result payload (None until it's done)."""
        from ..design.jobs import get_job_store
        rec = get_job_store().result(job_id)
        return rec if rec else {"error": f"no such job: {job_id}"}

    @mcp.tool()
    async def design_snapshot_inventory(
        library_paths: list[str],
        name_filter: str = "",
        limit_per_library: int = 60,
        include_descriptions: bool = True,
    ) -> dict[str, Any]:
        """Open a list of SchLib files and return what components live in them.

        NDA scope: only pass paths to the user's own neutral standard
        libraries. Do NOT pass project-local library paths from another
        client engagement, design knowledge cannot cross NDA boundaries.

        Large libraries (passives/connectors can hold hundreds of parts)
        easily exceed the tool-output token budget, so the RETURNED set is
        filtered and capped. The FULL inventory is always cached to
        ``<workspace>/inventory.json`` for the dashboard regardless of
        these limits.

        Args:
            library_paths: Absolute paths to .SchLib files to scan.
            name_filter: Case-insensitive substring; keep only components
                whose lib_ref or description contains it (e.g. ``"10k"``,
                ``"NE555"``). Empty = no filter.
            limit_per_library: Cap components returned per library
                (default 60; 0 = unlimited). Each library reports
                ``total`` and ``returned`` so you know if it was capped.
            include_descriptions: Drop the (often long) ``description``
                field from the returned rows to save tokens. Default True.

        Returns:
            Inventory dict: ``{"libraries": [{"path", "total",
            "returned", "components": [...]}]}``. Each component carries
            lib_ref, designator_prefix, pin_count, description (unless
            suppressed), and footprint when available.
        """
        paths = [Path(p) for p in library_paths]
        inventory = snapshot_live(paths)
        full_payload = inventory.model_dump()
        # Cache the FULL snapshot (unfiltered) so the web dashboard's
        # Libraries tab can render it without re-running the slow scan.
        try:
            from ..config import get_config
            cache_path = get_config().workspace_dir / "inventory.json"
            cache_path.write_text(
                json.dumps(full_payload, indent=2), encoding="utf-8",
            )
        except OSError:
            pass

        # Build a trimmed payload for the response.
        needle = name_filter.strip().lower()
        cap = max(0, int(limit_per_library))
        out_libs: list[dict[str, Any]] = []
        for lib in full_payload.get("libraries", []):
            comps = lib.get("components") or []
            if needle:
                comps = [
                    c for c in comps
                    if needle in str(c.get("lib_ref") or "").lower()
                    or needle in str(c.get("description") or "").lower()
                ]
            total = len(comps)
            if cap:
                comps = comps[:cap]
            if not include_descriptions:
                comps = [
                    {k: v for k, v in c.items() if k != "description"}
                    for c in comps
                ]
            out_libs.append({
                "path": lib.get("path"),
                "total": total,
                "returned": len(comps),
                "components": comps,
            })
        return {"libraries": out_libs}

    @mcp.tool()
    async def design_validate_plan(plan_json: Union[str, dict]) -> dict[str, Any]:
        """Validate a candidate DesignPlan JSON against the schema + cross-check.

        Run this before ``design_execute_plan`` to catch schema problems
        and cross-references that the executor will reject. Cheap; no
        Altium round-trip.

        Args:
            plan_json: Either a JSON string of the DesignPlan, or the
                DesignPlan as a JSON object/dict. The MCP framework
                auto-deserializes JSON-object literals to dicts before
                the tool sees them, so both shapes are accepted.

        Returns:
            ``{"ok": True, "summary": "..."}`` on success, or
            ``{"ok": False, "errors": [...]}`` listing the specific
            problems. The planner can read these and revise.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}

        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        cross = plan.cross_check()
        if cross:
            return {"ok": False, "errors": cross}

        # Offline ERC-lite: a floating net (all pins on one part) is a hard
        # connectivity error; unconnected parts and undecoupled ICs are
        # advisory. Surfaced here so the planner catches them before the
        # Altium round-trip. Schema-valid plans can still carry ERC errors,
        # so a floating net flips ``ok`` to False.
        erc = check_plan_erc(plan)
        erc_payload = {
            "passed": erc.passed,
            "errors": [
                {"code": i.code, "message": i.message, "refs": list(i.refs)}
                for i in erc.errors
            ],
            "warnings": [
                {"code": i.code, "message": i.message, "refs": list(i.refs)}
                for i in erc.warnings
            ],
        }
        if not erc.passed:
            return {
                "ok": False,
                "errors": [i.message for i in erc.errors],
                "erc": erc_payload,
            }

        return {
            "ok": True,
            "summary": (
                f"Plan valid. {len(plan.parts)} parts, {len(plan.nets)} nets, "
                f"{len(plan.sheets)} sheet(s). "
                f"ERC: {len(erc.warnings)} warning(s)."
            ),
            "erc": erc_payload,
        }

    @mcp.tool()
    async def design_list_circuit_blocks() -> dict[str, Any]:
        """List the canonical circuit blocks and their parameter contracts.

        Discoverability for `design_add_circuit_block`: returns every block
        with its summary, required params, optional params (already folding
        in the universal `sheet` / `lib_path` / `pins`), and which nets it
        creates. Call this to author with the exact parameter names instead
        of guessing -- a mistyped optional key is silently ignored by the
        block, so getting the names right up front avoids an incomplete
        part the ERC can't catch. Pure Python, no Altium.

        Returns:
            ``{ok, blocks: [{name, summary, required, optional,
            creates_nets}]}``.
        """
        return {"ok": True, "blocks": describe_blocks()}

    @mcp.tool()
    async def design_add_circuit_block(
        plan_json: Union[str, dict],
        block: str,
        params: dict,
    ) -> dict[str, Any]:
        """Fold a canonical circuit block into a DesignPlan in one call.

        Authoring boilerplate sub-circuits pin-by-pin in plan JSON is slow
        and error-prone (a decoupling network is N caps, 2N pins, and two
        net merges that must line up). This emits the canonical *topology*
        of a common block and wires it into ``plan_json`` -- allocating
        unique refdes, tying every pin to the right net, and tagging
        power/ground and roles -- then returns the augmented plan plus an
        inline re-validation so the next authoring step starts from a known
        state.

        It is **naming-agnostic**: it owns the wiring pattern, never the
        part choice. YOU supply every part identity in ``params``
        (``lib_ref``/``value``/``footprint``/``mpn``/``datasheet_url``),
        picked semantically from the live inventory snapshot. Pass computed
        values from ``design_compute_component_value`` where relevant.

        Blocks and their required ``params`` (all also accept ``pins`` to
        override default pin names, ``refdes_prefix``, ``sheet``):

          * ``decoupling``      power_net, ground_net, lib_ref [, count,
                                value, footprint] -- N bypass caps.
          * ``pullup``          signal_net, rail_net, lib_ref [, value].
          * ``pulldown``        signal_net, ground_net, lib_ref [, value].
          * ``series_resistor`` net_a, net_b, lib_ref [, value].
          * ``voltage_divider`` rail_net, ground_net, lib_ref_top,
                                lib_ref_bottom [, value_top, value_bottom,
                                output_net] -- fresh midpoint net.
          * ``rc_lowpass``      input_net, ground_net, lib_ref_r, lib_ref_c
                                [, value_r, value_c, output_net].
          * ``rc_highpass``     input_net, ground_net, lib_ref_r, lib_ref_c
                                [, value_r, value_c, output_net] -- series
                                C + shunt R (AC coupling).
          * ``led_indicator``   anode_net, cathode_net, lib_ref_r,
                                lib_ref_led [, value_r, mid_net].
          * ``crystal``         xin_net, xout_net, ground_net, lib_ref,
                                cap_lib_ref [, value, cap_value] -- crystal
                                + a matched pair of load caps (one shared
                                cap_value keeps the legs equal).
          * ``pi_filter``       input_net, ground_net, lib_ref_l, lib_ref_c
                                [, value_l, value_c, output_net] -- shunt C,
                                series L/ferrite, shunt C.
          * ``mosfet_low_side`` control_net, load_net, ground_net,
                                lib_ref_r, lib_ref_fet [, value_r_gate,
                                value_r_pulldown, pulldown, fet_pins] --
                                gate series R + gate pull-down + FET
                                switching a load low-side.
          * ``mosfet_high_side`` control_net, load_net, rail_net,
                                lib_ref_r, lib_ref_fet [, value_r_gate,
                                value_r_pullup, pullup, fet_pins] -- gate
                                series R + gate pull-up + PMOS switching a
                                load high-side (source at the rail).

        Call ``design_list_circuit_blocks`` for the full per-block
        parameter contract.

        Net endpoints the block does NOT own (a pullup's signal, an RC
        input, a divider rail) should already exist in the plan; the block
        only creates the nets it fully populates. If you point a block at a
        net that ends up with a single pin, the inline ERC reports it as a
        floating net for you to complete.

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            block: block name (see the list above).
            params: block parameters (see per-block requirements above).

        Returns:
            ``{ok, plan, added: {refdes, nets, extended_nets}, notes,
            validation}`` where ``plan`` is the augmented DesignPlan dict
            ready for the next ``design_add_circuit_block`` or
            ``design_execute_plan``, and ``validation`` is the same payload
            ``design_validate_plan`` returns. ``ok`` is False on a bad
            block name / missing param / schema error, with ``errors``.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        try:
            res = add_block(plan, block, params or {})
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "errors": [str(exc)]}

        # Re-validate the augmented plan so the caller gets the same gate
        # design_validate_plan would give -- schema, cross-check, ERC-lite.
        aug_dict = res.plan.model_dump()
        validation: dict[str, Any]
        try:
            reparsed = DesignPlan.model_validate(aug_dict)
        except ValidationError as exc:
            validation = {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }
        else:
            cross = reparsed.cross_check()
            erc = check_plan_erc(reparsed)
            validation = {
                "ok": not cross and erc.passed,
                "errors": cross or [i.message for i in erc.errors],
                "warnings": [i.message for i in erc.warnings],
            }

        result = {
            "ok": True,
            "plan": aug_dict,
            "added": {
                "refdes": res.added_refdes,
                "nets": res.added_nets,
                "extended_nets": res.extended_nets,
            },
            "notes": res.notes,
            "validation": validation,
        }
        hint = BulkHintTracker.record_and_hint("design_add_circuit_block")
        if hint:
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def design_add_part(
        plan_json: Union[str, dict],
        refdes: str,
        lib_ref: str,
        connections: dict,
        value: Optional[str] = None,
        footprint: Optional[str] = None,
        lib_path: Optional[str] = None,
        sheet: str = "main",
        manufacturer: Optional[str] = None,
        mpn: Optional[str] = None,
        datasheet_url: Optional[str] = None,
        role: Optional[str] = None,
        power_nets: Optional[list] = None,
        ground_nets: Optional[list] = None,
        net_roles: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Add one part and wire its pins to named nets, in one call.

        The atomic authoring primitive under the block/bus tools: place a
        single arbitrary part -- an MCU, a connector, a regulator -- and
        fold each pin into a net. ``connections`` is a pin->net map, the
        shape a datasheet pinout reads in; pins that map to the same net
        (an IC's five VCC pins) merge onto one net automatically, so you
        never hand-maintain a net's pin list. Chain with
        ``design_add_circuit_block`` (peripherals) and
        ``design_connect_bus`` (wide interfaces) to author a whole netlist
        without writing raw net JSON.

        Naming-agnostic: you supply the part identity and net names chosen
        from the live inventory; this owns only the pin->net folding.

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            refdes: the new part's designator (must be free in the plan).
            lib_ref: its library symbol name.
            connections: ``{pin: net_name}`` -- a net is created if absent
                or merged into if present.
            value / footprint / lib_path / sheet / manufacturer / mpn /
                datasheet_url / role: standard Part fields.
            power_nets / ground_nets: net names to flag power / ground when
                this call creates them (an existing net keeps its flags).
            net_roles: ``{net_name: role}`` for nets created here.

        Returns:
            ``{ok, plan, added: {refdes, nets, extended_nets}, notes,
            validation}`` -- same shape as ``design_add_circuit_block``.
            ``ok`` is False on a duplicate refdes / empty connections /
            schema error.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        try:
            res = add_component(
                plan, refdes=refdes, lib_ref=lib_ref, connections=connections,
                value=value, footprint=footprint, lib_path=lib_path,
                sheet=sheet, manufacturer=manufacturer, mpn=mpn,
                datasheet_url=datasheet_url, role=role,
                power_nets=tuple(power_nets or ()),
                ground_nets=tuple(ground_nets or ()), net_roles=net_roles)
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "errors": [str(exc)]}

        aug_dict = res.plan.model_dump()
        validation: dict[str, Any]
        try:
            reparsed = DesignPlan.model_validate(aug_dict)
        except ValidationError as exc:
            validation = {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }
        else:
            cross = reparsed.cross_check()
            erc = check_plan_erc(reparsed)
            validation = {
                "ok": not cross and erc.passed,
                "errors": cross or [i.message for i in erc.errors],
                "warnings": [i.message for i in erc.warnings],
            }

        result = {
            "ok": True,
            "plan": aug_dict,
            "added": {
                "refdes": res.added_refdes,
                "nets": res.added_nets,
                "extended_nets": res.extended_nets,
            },
            "notes": res.notes,
            "validation": validation,
        }
        hint = BulkHintTracker.record_and_hint("design_add_part")
        if hint:
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def design_connect_bus(
        plan_json: Union[str, dict],
        endpoints: list,
        net_names: Optional[list] = None,
        net_prefix: str = "D",
        start_index: int = 0,
    ) -> dict[str, Any]:
        """Wire a parallel bus across two+ parts in one call.

        A wide interface -- an 8-bit data bus between an MCU and a display,
        an address bus to a memory -- is otherwise N near-identical net
        objects where bit i of one side is easily misaligned with bit i+1
        of the other. This joins the i-th pin of every endpoint into one
        net per bit, so alignment is structural and the whole bus is one
        call. It creates NO parts: the endpoints reference parts already in
        the plan. The nets share one part-set -- the signature the pipeline
        uses to DRAW a >=4-bit bus as a bus glyph automatically.

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            endpoints: one dict per part on the bus, ``{"refdes": str,
                "pins": [pin, ...]}``. Every endpoint must list the same
                number of pins (the bus width); bit i joins ``pins[i]`` of
                each endpoint.
            net_names: explicit name per bit (length must equal the width);
                omitted -> ``f"{net_prefix}{start_index+i}"`` (D0, D1, ...).
            net_prefix: prefix for auto-named bits.
            start_index: first bit number for auto-naming.

        Returns:
            ``{ok, plan, added: {nets, extended_nets}, notes, validation}``
            -- same shape as ``design_add_circuit_block``. ``ok`` is False
            on a bad endpoint / width mismatch / schema error.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        try:
            res = connect_bus(
                plan, endpoints, net_names=net_names,
                net_prefix=net_prefix, start_index=start_index)
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "errors": [str(exc)]}

        aug_dict = res.plan.model_dump()
        validation: dict[str, Any]
        try:
            reparsed = DesignPlan.model_validate(aug_dict)
        except ValidationError as exc:
            validation = {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }
        else:
            cross = reparsed.cross_check()
            erc = check_plan_erc(reparsed)
            validation = {
                "ok": not cross and erc.passed,
                "errors": cross or [i.message for i in erc.errors],
                "warnings": [i.message for i in erc.warnings],
            }

        result = {
            "ok": True,
            "plan": aug_dict,
            "added": {
                "nets": res.added_nets,
                "extended_nets": res.extended_nets,
            },
            "notes": res.notes,
            "validation": validation,
        }
        hint = BulkHintTracker.record_and_hint("design_connect_bus")
        if hint:
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def design_compose_netlist(
        plan_json: Union[str, dict],
        operations: list,
    ) -> dict[str, Any]:
        """Apply many authoring operations to a plan in ONE call.

        The bulk form of the authoring primitives -- the same reason you
        prefer a batch Altium tool over looping the singular one. Instead
        of one round-trip per part/block/bus, pass an ordered list and this
        threads the plan through all of them, then validates once. Each
        operation sees the previous one's result, so a part added early is
        available to a bus wired later. Build a whole board in a single
        call.

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            operations: ordered list of op dicts, each with an ``"op"`` key:

                * ``{"op": "add_part", "refdes", "lib_ref", "connections",
                  ...}`` -- plus any optional add-part field (``value``,
                  ``footprint``, ``lib_path``, ``power_nets``,
                  ``ground_nets``, ``net_roles``, ...).
                * ``{"op": "add_block", "block", "params"}`` -- see
                  ``design_list_circuit_blocks`` for each block's params.
                * ``{"op": "connect_bus", "endpoints", "net_names"?,
                  "net_prefix"?, "start_index"?}``.

        Returns:
            ``{ok, plan, added: {refdes, nets, extended_nets}, notes,
            validation}`` for the FINAL plan -- same shape as the single-op
            tools. On a bad operation, ``{ok: False, errors: [...]}`` where
            the message names the failing operation's index.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        try:
            res = compose_netlist(plan, list(operations or []))
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "errors": [str(exc)]}

        aug_dict = res.plan.model_dump()
        validation: dict[str, Any]
        try:
            reparsed = DesignPlan.model_validate(aug_dict)
        except ValidationError as exc:
            validation = {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }
        else:
            cross = reparsed.cross_check()
            erc = check_plan_erc(reparsed)
            validation = {
                "ok": not cross and erc.passed,
                "errors": cross or [i.message for i in erc.errors],
                "warnings": [i.message for i in erc.warnings],
            }

        return {
            "ok": True,
            "plan": aug_dict,
            "added": {
                "refdes": res.added_refdes,
                "nets": res.added_nets,
                "extended_nets": res.extended_nets,
            },
            "notes": res.notes,
            "validation": validation,
        }

    @mcp.tool()
    async def design_generate_bom(
        plan_json: Union[str, dict],
        set_on_plan: bool = True,
    ) -> dict[str, Any]:
        """Derive the bill of materials from a plan's parts in one call.

        Every design needs a BOM, and it is mechanical to build from parts
        you already authored -- this rolls them up so you do not hand-group
        refdes (redundant with the parts, and easy to miscount). Parts with
        an ``mpn`` consolidate by ``(manufacturer, mpn)`` -- one orderable
        line each; parts without an mpn consolidate by ``(lib_ref, value,
        footprint)`` -- so every 100 nF 0402 cap is one line. Deterministic
        (natural-sorted, R2 before R10). Pure Python, no Altium.

        ``lines_without_mpn`` flags lines that still need a manufacturer
        part number before the BOM is orderable -- the gaps to fill.

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            set_on_plan: when True (default), the returned ``plan`` carries
                the generated BOM in its ``bom`` field, ready for
                ``design_execute_plan``; the parts are unchanged.

        Returns:
            ``{ok, bom: [{refdes_list, manufacturer, mpn, description,
            qty}], summary: {line_count, total_parts, lines_without_mpn},
            plan?}``.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        lines = generate_bom(plan)
        result: dict[str, Any] = {
            "ok": True,
            "bom": [bl.model_dump() for bl in lines],
            "summary": bom_summary(lines),
        }
        if set_on_plan:
            result["plan"] = plan.model_copy(update={"bom": lines}).model_dump()
        return result

    @mcp.tool()
    async def design_edit_plan(
        plan_json: Union[str, dict],
        operations: list,
    ) -> dict[str, Any]:
        """Edit an existing plan -- the MODIFY complement to the add tools.

        Authoring is iterative: a review turns up a wrong value, a part to
        drop, two nets that should be one. Fix it with edits instead of
        re-emitting the whole plan, and let the tool own the error-prone
        bookkeeping (deleting a part scrubs it from every net; merging nets
        moves and de-dupes pins). Ordered ops, each sees the previous
        result; validates once at the end so a now-floating net surfaces.

        Operations (each a dict keyed by ``"op"``):

          * ``{"op": "set_part", "refdes", <field>: <value>, ...}`` --
            change ``value`` / ``footprint`` / ``mpn`` / ``manufacturer`` /
            ``lib_ref`` / ``status`` / ``sheet`` / ``role`` / ... on a part.
          * ``{"op": "delete_part", "refdes"}`` -- remove a part; nets it
            emptied are dropped, nets left with one pin are flagged in
            ``notes`` as floating.
          * ``{"op": "set_net", "net", <flag>: <value>, ...}`` -- change a
            net's ``is_power`` / ``is_ground`` / ``role`` / ``force_label``
            / ``force_wires`` (fix a forgotten power/ground flag).
          * ``{"op": "connect_pin", "net", "refdes", "pin"}`` -- tie a
            part's pin to a net (fix an unconnected pin); makes the net if
            new.
          * ``{"op": "disconnect_pin", "net", "refdes", "pin"}`` -- remove a
            pin from a net.
          * ``{"op": "rename_net", "old", "new"}`` -- rename a net.
          * ``{"op": "merge_nets", "into", "from"}`` -- fold ``from`` into
            ``into`` (keeping ``into``'s flags), then drop ``from``.

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            operations: the ordered edit ops.

        Returns:
            ``{ok, plan, notes, validation}`` for the edited plan -- same
            validation shape as the add tools. On a bad op,
            ``{ok: False, errors: [...]}`` naming the operation's index.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }

        try:
            res = edit_plan(plan, list(operations or []))
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "errors": [str(exc)]}

        aug_dict = res.plan.model_dump()
        validation: dict[str, Any]
        try:
            reparsed = DesignPlan.model_validate(aug_dict)
        except ValidationError as exc:
            validation = {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }
        else:
            cross = reparsed.cross_check()
            erc = check_plan_erc(reparsed)
            validation = {
                "ok": not cross and erc.passed,
                "errors": cross or [i.message for i in erc.errors],
                "warnings": [i.message for i in erc.warnings],
            }

        return {
            "ok": True,
            "plan": aug_dict,
            "notes": res.notes,
            "validation": validation,
        }

    @mcp.tool()
    async def design_compute_component_value(
        kind: str,
        series: str = "E96",
        v_in: Optional[float] = None,
        v_out: Optional[float] = None,
        v_ref: Optional[float] = None,
        v_supply: Optional[float] = None,
        v_forward: Optional[float] = None,
        i_led_ma: Optional[float] = None,
        f_cutoff_hz: Optional[float] = None,
        r_ohms: Optional[float] = None,
        c_farads: Optional[float] = None,
        r_bottom_ohms: Optional[float] = None,
        value: Optional[float] = None,
        c_load_pf: Optional[float] = None,
        c_stray_pf: float = 5.0,
        v_bus: Optional[float] = None,
        c_bus_pf: Optional[float] = None,
        t_rise_ns: Optional[float] = None,
        r_top_ohms: Optional[float] = None,
        tol_pct: float = 1.0,
        gain: Optional[float] = None,
        config: str = "inverting",
        i_out_a: Optional[float] = None,
        f_sw_khz: Optional[float] = None,
        ripple_pct: float = 30.0,
        voltage: Optional[float] = None,
        i_load_a: Optional[float] = None,
        t_s: Optional[float] = None,
        v_drop: Optional[float] = None,
        v_initial: Optional[float] = None,
        v_final: Optional[float] = None,
        power_w: Optional[float] = None,
        theta_ja: Optional[float] = None,
        t_ambient: float = 25.0,
        tj_max: Optional[float] = None,
    ) -> dict[str, Any]:
        """Compute a manufacturable component value, snapped to an E-series.

        Use this instead of doing the arithmetic by hand: it applies the
        standard sizing equation, snaps to the nearest IEC 60063 preferred
        value, and reports the ACHIEVED result plus the error so you can see
        if the snap is good enough or a tighter ``series`` is needed.

        Args:
            kind: Which calculation:
              - ``"nearest"``: snap ``value`` to the nearest preferred value.
              - ``"feedback_divider"``: regulator FB divider
                ``v_out = v_ref*(1+Rtop/Rbot)``; needs ``v_out``, ``v_ref``;
                optional ``r_bottom_ohms`` to fix the low side.
              - ``"resistor_divider"``: unloaded divider
                ``v_out = v_in*Rb/(Rt+Rb)``; needs ``v_in``, ``v_out``.
              - ``"led_resistor"``: series resistor; needs ``v_supply``,
                ``v_forward``, ``i_led_ma``.
              - ``"rc_lowpass"``: first-order RC; needs ``f_cutoff_hz`` and
                exactly one of ``r_ohms`` / ``c_farads``.
              - ``"crystal_load_caps"``: symmetric crystal load caps; needs
                ``c_load_pf`` (datasheet CL), optional ``c_stray_pf`` (default
                5 pF per leg).
              - ``"i2c_pullup"``: bus pull-up window (NXP UM10204); needs
                ``v_bus``, ``c_bus_pf``, ``t_rise_ns`` (1000 standard / 300
                fast / 120 fast-plus).
              - ``"divider_tolerance"``: worst-case output window of an
                unloaded divider; needs ``v_in``, ``r_top_ohms``,
                ``r_bottom_ohms``, optional ``tol_pct`` (default 1).
              - ``"opamp_gain"``: Rf/Rin (or Rg) for a gain stage; needs
                ``gain`` and ``config`` (``inverting`` / ``non_inverting``).
              - ``"buck_inductor"``: buck inductor; needs ``v_in``, ``v_out``,
                ``i_out_a``, ``f_sw_khz``, optional ``ripple_pct`` (default 30).
              - ``"capacitor_energy"``: stored energy; needs ``c_farads``,
                ``voltage``.
              - ``"holdup_cap"``: bulk hold-up capacitance; needs ``i_load_a``,
                ``t_s`` (hold-up time), ``v_drop`` (allowed sag).
              - ``"discharge_resistor"``: bleeder; needs ``c_farads``,
                ``v_initial``, ``v_final``, ``t_s`` (discharge time).
              - ``"junction_temp"``: Tj = Ta + P*theta_JA; needs ``power_w``,
                ``theta_ja``, optional ``t_ambient`` (default 25).
              - ``"max_power"``: thermal derating; needs ``tj_max``,
                ``theta_ja``, optional ``t_ambient``.
              - ``"required_theta_ja"``: package/heatsink sizing; needs
                ``power_w``, ``tj_max``, optional ``t_ambient``.
            series: E-series name (E6/E12/E24/E48/E96). Default E96 for
                dividers/precision, pass E24 for jellybean R/C.

        Returns:
            ``{"ok": True, ...fields..., "summary": "..."}`` or
            ``{"ok": False, "error": "..."}``.
        """
        try:
            k = kind.strip().lower()
            if k == "nearest":
                if value is None:
                    return {"ok": False, "error": "nearest needs 'value'"}
                snapped = nearest_preferred(value, series)
                return {
                    "ok": True, "value": value, "snapped": snapped,
                    "series": series.upper(),
                    "error_pct": 100.0 * (snapped - value) / value,
                    "summary": f"{value:g} -> {snapped:g} ({series.upper()})",
                }
            if k == "feedback_divider":
                if v_out is None or v_ref is None:
                    return {"ok": False,
                            "error": "feedback_divider needs v_out and v_ref"}
                r = feedback_divider(v_out, v_ref, series=series,
                                     r_bottom=r_bottom_ohms)
                return _divider_payload(r, series, "feedback")
            if k == "resistor_divider":
                if v_in is None or v_out is None:
                    return {"ok": False,
                            "error": "resistor_divider needs v_in and v_out"}
                r = resistor_divider(v_in, v_out, series=series)
                return _divider_payload(r, series, "unloaded")
            if k == "led_resistor":
                if v_supply is None or v_forward is None or i_led_ma is None:
                    return {"ok": False, "error": "led_resistor needs "
                            "v_supply, v_forward, i_led_ma"}
                r = led_series_resistor(v_supply, v_forward, i_led_ma / 1000.0,
                                        series=series)
                return {
                    "ok": True, "resistor_ohms": r.resistor,
                    "current_ma": r.current_a * 1000.0,
                    "power_w": r.power_w, "error_pct": r.error_pct,
                    "series": series.upper(),
                    "summary": (f"R={r.resistor:g} ohm, I={r.current_a*1000:.2f} "
                                f"mA, P={r.power_w*1000:.1f} mW "
                                f"({r.error_pct:+.2f}%)"),
                }
            if k == "rc_lowpass":
                if f_cutoff_hz is None:
                    return {"ok": False, "error": "rc_lowpass needs f_cutoff_hz"}
                r = rc_lowpass(f_cutoff_hz, r=r_ohms, c=c_farads, series=series)
                return {
                    "ok": True, "r_ohms": r.r, "c_farads": r.c,
                    "f_cutoff_hz": r.f_cutoff, "error_pct": r.error_pct,
                    "series": series.upper(),
                    "summary": (f"R={r.r:g} ohm, C={r.c*1e9:g} nF, "
                                f"fc={r.f_cutoff:.2f} Hz ({r.error_pct:+.2f}%)"),
                }
            if k == "crystal_load_caps":
                if c_load_pf is None:
                    return {"ok": False,
                            "error": "crystal_load_caps needs c_load_pf"}
                r = crystal_load_caps(c_load_pf * 1e-12, c_stray_pf * 1e-12,
                                      series=series)
                return {
                    "ok": True, "cap_pf": r.cap * 1e12,
                    "c_load_achieved_pf": r.c_load_achieved * 1e12,
                    "c_load_target_pf": r.c_load_target * 1e12,
                    "error_pct": r.error_pct, "series": series.upper(),
                    "summary": (f"C1 = C2 = {r.cap*1e12:g} pF -> CL "
                                f"{r.c_load_achieved*1e12:.1f} pF "
                                f"({r.error_pct:+.2f}%)"),
                }
            if k == "i2c_pullup":
                if v_bus is None or c_bus_pf is None or t_rise_ns is None:
                    return {"ok": False, "error": "i2c_pullup needs v_bus, "
                            "c_bus_pf, t_rise_ns"}
                r = i2c_pullup(v_bus, c_bus_pf * 1e-12, t_rise_ns * 1e-9,
                               series=series)
                return {
                    "ok": True, "r_min_ohms": r.r_min, "r_max_ohms": r.r_max,
                    "recommended_ohms": r.recommended, "feasible": r.feasible,
                    "series": series.upper(),
                    "summary": (
                        f"R in [{r.r_min:.0f}, {r.r_max:.0f}] ohm -> "
                        f"{r.recommended:g} ohm" if r.feasible else
                        f"infeasible: no {series.upper()} value in "
                        f"[{r.r_min:.0f}, {r.r_max:.0f}] ohm (bus C too high "
                        f"for the mode)"),
                }
            if k == "divider_tolerance":
                if v_in is None or r_top_ohms is None or r_bottom_ohms is None:
                    return {"ok": False, "error": "divider_tolerance needs "
                            "v_in, r_top_ohms, r_bottom_ohms"}
                r = divider_tolerance(v_in, r_top_ohms, r_bottom_ohms,
                                      tol_pct=tol_pct)
                return {
                    "ok": True, "v_nominal": r.v_nominal, "v_min": r.v_min,
                    "v_max": r.v_max, "spread_pct": r.spread_pct,
                    "summary": (f"{r.v_nominal:.4f} V nominal, "
                                f"[{r.v_min:.4f}, {r.v_max:.4f}] V at "
                                f"{tol_pct:g}% (spread {r.spread_pct:.2f}%)"),
                }
            if k == "opamp_gain":
                if gain is None:
                    return {"ok": False, "error": "opamp_gain needs gain"}
                r = opamp_gain_resistors(gain, config=config, series=series)
                sign = "-" if r.config == "inverting" else "+"
                return {
                    "ok": True, "config": r.config,
                    "r_feedback_ohms": r.r_feedback, "r_input_ohms": r.r_input,
                    "gain": r.gain, "error_pct": r.error_pct,
                    "series": series.upper(),
                    "summary": (f"{r.config}: Rf={r.r_feedback:g} ohm, "
                                f"R{'in' if r.config=='inverting' else 'g'}="
                                f"{r.r_input:g} ohm -> gain {sign}{r.gain:.3f} "
                                f"({r.error_pct:+.2f}%)"),
                }
            if k == "buck_inductor":
                if (v_in is None or v_out is None or i_out_a is None
                        or f_sw_khz is None):
                    return {"ok": False, "error": "buck_inductor needs v_in, "
                            "v_out, i_out_a, f_sw_khz"}
                r = buck_inductor(v_in, v_out, i_out_a, f_sw_khz * 1e3,
                                  ripple_fraction=ripple_pct / 100.0,
                                  series=series)
                return {
                    "ok": True, "inductance_uh": r.inductance * 1e6,
                    "ripple_current_a": r.ripple_current_a,
                    "peak_current_a": r.peak_current_a,
                    "error_pct": r.error_pct, "series": series.upper(),
                    "summary": (f"L={r.inductance*1e6:g} uH -> ripple "
                                f"{r.ripple_current_a:.3f} A, peak "
                                f"{r.peak_current_a:.3f} A ({r.error_pct:+.2f}%)"),
                }
            if k == "capacitor_energy":
                if c_farads is None or voltage is None:
                    return {"ok": False,
                            "error": "capacitor_energy needs c_farads, voltage"}
                e = capacitor_energy(c_farads, voltage)
                return {
                    "ok": True, "energy_j": e, "charge_c": c_farads * voltage,
                    "summary": (f"{c_farads*1e6:g} uF @ {voltage:g} V stores "
                                f"{e:.4g} J ({c_farads*voltage:.4g} C)"),
                }
            if k == "holdup_cap":
                if i_load_a is None or t_s is None or v_drop is None:
                    return {"ok": False, "error": "holdup_cap needs i_load_a, "
                            "t_s, v_drop"}
                c = holdup_capacitance(i_load_a, t_s, v_drop)
                return {
                    "ok": True, "capacitance_f": c, "capacitance_uf": c * 1e6,
                    "summary": (f"hold {i_load_a:g} A for {t_s*1e3:g} ms within "
                                f"{v_drop:g} V sag -> {c*1e6:.4g} uF"),
                }
            if k == "discharge_resistor":
                if (c_farads is None or v_initial is None or v_final is None
                        or t_s is None):
                    return {"ok": False, "error": "discharge_resistor needs "
                            "c_farads, v_initial, v_final, t_s"}
                r = discharge_resistor(c_farads, v_initial, v_final, t_s)
                return {
                    "ok": True, "resistor_ohms": r,
                    "power_w": v_initial * v_initial / r,
                    "summary": (f"discharge {c_farads*1e6:g} uF "
                                f"{v_initial:g}->{v_final:g} V in {t_s:g} s -> "
                                f"{r:.4g} ohm"),
                }
            if k == "junction_temp":
                if power_w is None or theta_ja is None:
                    return {"ok": False,
                            "error": "junction_temp needs power_w, theta_ja"}
                tj = junction_temperature(power_w, theta_ja, t_ambient)
                return {
                    "ok": True, "tj_c": tj, "rise_c": tj - t_ambient,
                    "summary": (f"{power_w:g} W * {theta_ja:g} C/W + "
                                f"{t_ambient:g} C -> Tj = {tj:.1f} C"),
                }
            if k == "max_power":
                if tj_max is None or theta_ja is None:
                    return {"ok": False,
                            "error": "max_power needs tj_max, theta_ja"}
                p = max_power_dissipation(tj_max, theta_ja, t_ambient)
                return {
                    "ok": True, "power_w": p,
                    "summary": (f"({tj_max:g}-{t_ambient:g}) / {theta_ja:g} C/W "
                                f"-> max {p:.3f} W"),
                }
            if k == "required_theta_ja":
                if power_w is None or tj_max is None:
                    return {"ok": False, "error": "required_theta_ja needs "
                            "power_w, tj_max"}
                th = required_theta_ja(power_w, tj_max, t_ambient)
                return {
                    "ok": True, "theta_ja_c_per_w": th,
                    "summary": (f"({tj_max:g}-{t_ambient:g}) / {power_w:g} W -> "
                                f"need theta_JA <= {th:.1f} C/W"),
                }
            return {"ok": False, "error": f"unknown kind {kind!r}"}
        except (ValueError, ZeroDivisionError) as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    async def design_describe_circuits(
        plan_json: Union[str, dict],
    ) -> dict[str, Any]:
        """Report the electrical behaviour of each recognised sub-circuit.

        Recognises the parametric blocks in a ``DesignPlan`` (voltage / feedback
        dividers, RC low/high-pass filters, crystal load networks) and computes
        each one's characteristic from the chosen component values: a divider's
        ratio, a filter's cut-off, a crystal's load capacitance. Use it to
        verify a block does what you intended -- it catches the
        wrong-but-consistent value error (a divider of two valid resistors that
        produces the wrong ratio) that connectivity and equality checks miss.

        Most parameters are ratios / time-constants that need only the values,
        not a supply voltage, so this works on the plan alone. No Altium.

        Returns ``{"ok": True, "circuits": [{motif, parts, summary, params}]}``;
        blocks whose values are missing or unparseable are skipped.
        """
        payload = plan_json if isinstance(plan_json, dict) else None
        if payload is None:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid JSON: {exc}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": str(exc)}
        circuits = [
            {"motif": d.motif_name, "parts": list(d.parts),
             "summary": d.summary, "params": d.params}
            for d in describe_motifs(plan)
        ]
        return {"ok": True, "circuits": circuits}

    @mcp.tool()
    async def design_suggest_diff_pair_traces(
        plan_json: Union[str, dict],
        target_ohms: float = 90.0,
        geometry: str = "microstrip_diff",
        dielectric_height_mils: float = 7.0,
        dielectric_constant: float = 4.2,
        copper_oz: float = 1.0,
        spacing_mils: float = 6.0,
    ) -> dict[str, Any]:
        """Recommend a controlled-impedance trace width for every differential
        pair in a plan.

        Detects the differential pairs (nets with role ``differential``) and
        sizes each to ``target_ohms`` (90 USB / 100 HDMI/LVDS) for the supplied
        stackup and edge-to-edge ``spacing_mils`` via the IPC-2141 impedance
        inverse -- the trace geometry for every pair in one call instead of
        identifying pairs and running ``pcb_calc_trace_width_for_impedance`` by
        hand. The stackup and target are board-level decisions you supply; pure
        Python, no Altium.

        Returns ``{"ok": True, "pairs": [{nets, endpoints, width_mils,
        spacing_mils, target_ohms, feasible}]}`` or ``{"ok": False, ...}``.
        """
        from ..design.hsd_rules import suggest_diff_pair_traces
        payload = plan_json if isinstance(plan_json, dict) else None
        if payload is None:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid JSON: {exc}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            traces = suggest_diff_pair_traces(
                plan, target_ohms=target_ohms, geometry=geometry,
                dielectric_height_mils=dielectric_height_mils,
                dielectric_constant=dielectric_constant, copper_oz=copper_oz,
                spacing_mils=spacing_mils)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "pairs": [
                {"nets": list(t.nets), "endpoints": list(t.endpoints),
                 "width_mils": t.width_mils, "spacing_mils": t.spacing_mils,
                 "target_ohms": t.target_ohms, "feasible": t.feasible}
                for t in traces
            ],
        }

    @mcp.tool()
    async def design_review_file(path: str) -> dict[str, Any]:
        """Offline FALLBACK review of a .SchDoc/.PrjPcb: OFF by default.

        This is NOT the preferred way to review a design. It parses the
        Altium file directly (OLE reader) with no running Altium and no
        license, so it only covers the netlist-free, component-level subset
        (missing MPN / datasheet / manufacturer, placeholder values,
        designator collisions, missing / unannotated designators, incomplete
        title block). It cannot compile a netlist or run ERC, and an offline
        parser can misread a file.

        Prefer the live-Altium tools whenever a session is available:
        ``design_lint_report``, ``proj_run_erc``, ``design_review_snapshot``,
        and the ``audit_*`` family run Altium's own engines and see
        connectivity this reader cannot. Reach for this tool only when Altium
        genuinely can't be opened (a file on disk, a CI runner).

        Because of that, this surface is **disabled by default**. To enable
        it, set the environment variable ``EDA_AGENT_HEADLESS_REVIEW=1``.
        Without it the tool returns an ``error`` explaining the opt-in.

        Args:
            path: a ``.SchDoc`` sheet or a ``.PrjPcb`` project (reviews all
                its sheets).

        Returns the review report ``{file, component_count, findings,
        summary, ...}``: or ``{"error": ...}`` if disabled or unreadable.
        """
        from ..fileio.review import (
            HEADLESS_DISABLED_MESSAGE,
            headless_review_enabled,
            review_project_file,
        )
        if not headless_review_enabled():
            return {"error": HEADLESS_DISABLED_MESSAGE, "disabled": True}
        try:
            return review_project_file(path)
        except (ValueError, OSError) as e:
            return {"error": f"cannot read {path}: {e}"}

    @mcp.tool()
    async def design_solve_netlist_file(path: str) -> dict[str, Any]:
        """Reconstruct a .SchDoc's netlist geometrically: OFF by default.

        Offline FALLBACK, same opt-in as ``design_review_file``. Parses the
        file (no Altium, no license) and rebuilds the compiled netlist from
        geometry (pins, wires, power ports, junctions, and by-name net
        labels), then runs connectivity ERC (``single_pin_net`` floating
        pins, ``net_short`` rail shorts).

        Prefer the live-Altium path (``proj_get_nets`` / ``proj_run_erc``)
        whenever a session is available: Altium's own compiler is ground
        truth. Use this only when Altium can't be opened.

        Validated envelope: wire + power port + junction + net-label
        connectivity (live Altium 24/24; pipeline buck 7/7). It faithfully
        reports a net the schematic left floating; cross-sheet connectors are
        out of scope. A single sheet only (a ``.SchDoc``), not a project.

        Enable with ``EDA_AGENT_HEADLESS_REVIEW=1``.

        Returns ``{file, net_count, nets, pin_count, findings}``: or
        ``{"error": ...}`` if disabled or unreadable.
        """
        from ..fileio.review import (
            HEADLESS_DISABLED_MESSAGE,
            headless_review_enabled,
            review_connectivity,
        )
        if not headless_review_enabled():
            return {"error": HEADLESS_DISABLED_MESSAGE, "disabled": True}
        try:
            from ..fileio.netlist_solver import solve_schematic_nets
            solved = solve_schematic_nets(path)
        except (ValueError, OSError, KeyError) as e:
            return {"error": f"cannot solve {path}: {e}"}
        return {
            "file": str(path),
            "net_count": len(solved["nets"]),
            "pin_count": len(solved["pin_nets"]),
            "nets": {name: [f"{m['component']}.{m['pin']}" for m in members]
                     for name, members in solved["nets"].items()},
            "findings": review_connectivity(solved),
        }

    @mcp.tool()
    async def design_bom_file(path: str) -> dict[str, Any]:
        """Consolidated BOM from a .SchDoc/.PrjPcb on disk: OFF by default.

        Offline FALLBACK, same opt-in as ``design_review_file``. Parses the
        Altium file directly (no Altium, no license) and groups components
        into purchasable BOM lines, one per distinct ``(mpn, value,
        lib_reference)``, designators grouped and naturally sorted, quantity
        summed. A ``.PrjPcb`` aggregates all its sheets.

        Prefer the live-Altium ``proj_get_bom`` / ``proj_export_bom_html``
        when a session is available. Use this for a file on disk or a CI
        artifact.

        Enable with ``EDA_AGENT_HEADLESS_REVIEW=1``.

        Returns ``{file, line_count, part_count, lines}`` where each line is
        ``{quantity, designators, mpn, manufacturer, value, lib_reference,
        datasheet}``: or ``{"error": ...}`` if disabled or unreadable.
        """
        from ..fileio.review import (
            HEADLESS_DISABLED_MESSAGE,
            headless_review_enabled,
        )
        if not headless_review_enabled():
            return {"error": HEADLESS_DISABLED_MESSAGE, "disabled": True}
        try:
            from ..fileio.bom import bom_from_file
            lines = bom_from_file(path)
        except (ValueError, OSError) as e:
            return {"error": f"cannot read {path}: {e}"}
        return {
            "file": str(path),
            "line_count": len(lines),
            "part_count": sum(ln["quantity"] for ln in lines),
            "lines": lines,
        }

    @mcp.tool()
    async def design_review_plan(
        plan_json: Union[str, dict],
    ) -> dict[str, Any]:
        """One-call offline pre-flight: bundle every plan-level analysis.

        Runs, on the plan alone (no Altium), the checks and reports the agent
        would otherwise call one by one, so the planner can vet a design in a
        single step before emit:

          - ``stats``: part counts by kind, IC / passive split, power & ground
            rails, average net degree, the widest signal net (a routing
            hotspot).
          - ``erc``: the ERC-lite report (floating nets, unconnected parts,
            missing decoupling, malformed values, matched-value mismatches);
            ``passed`` is False only on a hard error.
          - ``circuits``: the computed behaviour of each recognised block
            (divider ratios, filter cut-offs, crystal load).
          - ``placement_constraints``: the match / keepout groups that would be
            auto-derived for ``pcb_plan_placement(plan_json=...)``.
          - ``net_classes``: each net's class (power / ground / differential /
            clock / analog / ... / signal) grouped for PCB net-class setup.

        Returns ``{"ok": True, "passed": <erc.passed>, ...sections...}`` or
        ``{"ok": False, "errors"/"error": ...}`` on a schema problem.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid JSON: {exc}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}

        stats = summarize_plan(plan)
        erc = check_plan_erc(plan)
        constraints = infer_placement_constraints(plan)
        return {
            "ok": True,
            "passed": erc.passed,
            "stats": {
                "part_count": stats.part_count,
                "net_count": stats.net_count,
                "parts_by_kind": stats.parts_by_kind,
                "ic_count": stats.ic_count,
                "passive_count": stats.passive_count,
                "power_rails": list(stats.power_rails),
                "ground_nets": list(stats.ground_nets),
                "avg_net_degree": round(stats.avg_net_degree, 2),
                "highest_fanout_signal": list(stats.highest_fanout_signal)
                if stats.highest_fanout_signal else None,
            },
            "erc": {
                "passed": erc.passed,
                "errors": [{"code": i.code, "message": i.message,
                            "refs": list(i.refs)} for i in erc.errors],
                "warnings": [{"code": i.code, "message": i.message,
                              "refs": list(i.refs)} for i in erc.warnings],
            },
            "circuits": [
                {"motif": d.motif_name, "parts": list(d.parts),
                 "summary": d.summary, "params": d.params}
                for d in describe_motifs(plan)
            ],
            "placement_constraints": {
                "match_groups": constraints.match_groups,
                "keepout_groups": constraints.keepout_groups,
            },
            "net_classes": {
                cls: list(nets)
                for cls, nets in classify_nets(plan).groups.items()
            },
        }

    @mcp.tool()
    async def design_layout_schematic(
        plan_json: Union[str, dict],
        sheet: str = "main",
        grid_mils: int = 100,
        fr_iterations: int = 80,
        placement_hints: Optional[dict[str, dict[str, int]]] = None,
        render_png: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compute the schematic layout for a DesignPlan, as pure data.

        THE ONE SCHEMATIC ENGINE. Every schematic tool now runs the same
        canvas pipeline, so whenever you are asked to draw or lay out a
        schematic, reach for this family and nothing else: this tool for
        the geometry as data, ``design_preview_plan`` for the same layout
        rendered to SVG, ``design_execute_plan`` to place it in Altium.
        There is no longer a second engine to choose between.

        It used to run a second, standalone engine whose placements and
        score described a layout nobody would ever see. That engine is no
        longer reachable: measured over ten held-out corpus sheets it lost
        the shared objective on seven and produced four pairs of
        OVERLAPPING BODIES where this pipeline produced none, so the
        shorter wire it appeared to draw was partly bought by stacking
        parts on top of one another.

        Runs OFFLINE and never reaches Altium, so it works on every
        backend. That costs one thing and the result says which: symbol
        geometry is synthesised from the plan's pin lists rather than
        read from a library, so ``symbols`` comes back ``"synthetic"``
        and ``execution_accurate`` is False. The placement and routing
        RULES are the ones that execute. When you need the exact
        geometry, use ``design_preview_plan`` (same pipeline, real
        symbols, renders SVG) or ``design_execute_plan`` to emit.

        Args:
            plan_json: A DesignPlan as a JSON string or a JSON object/dict.
            sheet: Sheet name to report (default ``"main"``).
            grid_mils: Accepted for compatibility; the canvas pipeline
                snaps to its own 100-mil grid.
            fr_iterations: Accepted for compatibility; the pipeline sweeps
                its own force-directed budget and scores the results.
            placement_hints: Optional ``{refdes: {"x", "y", "rotation"}}``
                pinned positions, same as ``design_execute_plan``.
            render_png: Optional path for a preview image of the layout.

        Returns:
            Dict with ``ok`` / ``sheet`` / ``engine`` ("canvas") /
            ``execution_accurate`` (True) / ``summary`` / ``placements`` /
            ``net_representation`` / ``wires`` / ``net_labels`` /
            ``power_ports`` / ``junctions`` / ``score`` / ``notes`` /
            ``failures``. On a bad plan: ``{"ok": False, "errors": [...]}``.
        """
        result = layout_plan_from_json(
            plan_json, sheet=sheet, placement_hints=placement_hints,
            render_png=render_png,
        )
        if result.get("ok") and "placements" in result:
            result["summary"] = _schematic_summary(
                result.get("score", {}),
                result.get("net_representation", {}),
                len(result.get("placements", [])),
            )
        return result

    @mcp.tool()
    async def design_suggest_partition(
        plan_json: Union[str, dict],
        n_groups: int = 2,
        max_fanout: int = 8,
    ) -> dict[str, Any]:
        """Suggest how to split a design into balanced functional groups.

        Computes a min-cut partition (Kernighan-Lin style) of the plan's
        components into ``n_groups`` balanced groups that MINIMISE the number
        of nets crossing between groups -- so each group is internally
        well-connected and few signals span the boundary. Use it to group a
        PCB into functional ROOMS, or to decide how to break a design with
        SEPARABLE blocks across schematic sheets.

        Note: a HUB-centric design (one IC fanning out to many peripherals)
        does not separate cleanly -- the hub is connected to everything, so
        the cut stays high and per-sheet density barely drops. Partitioning
        helps when the netlist has genuinely distinct functional blocks; read
        ``cut_nets`` (low = clean) and ``group_sizes`` to judge before acting.

        Power/ground rails (nets touching more than ``max_fanout`` parts) are
        excluded from the connectivity graph -- they route as planes and
        connect nearly everything, so the split follows the SIGNAL structure.

        Pure data; touches nothing. Assign the returned groups to part
        ``sheet`` (or ``zone``) fields yourself if you act on the suggestion.

        Args:
            plan_json: A DesignPlan (dict or JSON string).
            n_groups: Number of groups to split into (default 2).
            max_fanout: Nets above this pin count are treated as rails and
                ignored for partitioning (default 8).

        Returns:
            ``{ok, n_groups, groups: {idx: [refdes...]}, cut_nets,
            boundary_nets: [names...], group_sizes, summary}``. ``cut_nets``
            is how many nets cross the boundary (lower is cleaner);
            ``boundary_nets`` names them -- those become cross-sheet labels or
            off-sheet connectors if you split there.
        """
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors()]}

        from ..design.partition import partition_netlist
        refdes = [p.refdes for p in plan.parts]
        nets = [[pr.refdes for pr in n.pins] for n in plan.nets]
        res = partition_netlist(refdes, nets, n_groups=int(n_groups),
                                max_fanout=int(max_fanout))
        groups: dict[str, list[str]] = {}
        for r, g in res["group_of"].items():
            groups.setdefault(str(g), []).append(r)
        for g in groups:
            groups[g].sort()
        # Named nets whose pins span more than one group -- what a split there
        # would have to carry across the boundary (labels / off-sheet conns).
        group_of = res["group_of"]
        boundary_nets = sorted(
            n.name for n in plan.nets
            if len({group_of.get(pr.refdes) for pr in n.pins
                    if pr.refdes in group_of} - {None}) > 1
        )
        summary = (
            f"{res['n_groups']}-way split, group sizes {res['group_sizes']}, "
            f"{res['cut_nets']} net(s) cross the boundary (lower is cleaner)."
        )
        return {
            "ok": True,
            "n_groups": res["n_groups"],
            "groups": groups,
            "cut_nets": res["cut_nets"],
            "boundary_nets": boundary_nets,
            "group_sizes": res["group_sizes"],
            "summary": summary,
        }

    @mcp.tool()
    async def design_execute_plan(
        plan_json: Union[str, dict],
        project_path: str,
        use_canvas: bool = True,
        placement_hints: Optional[dict[str, dict[str, int]]] = None,
        mode: str = "auto",
    ) -> dict[str, Any]:
        """Instantiate a DesignPlan in Altium.

        Two execution paths:

        - ``use_canvas=True`` (default): the new canvas-based pipeline.
          Plan -> SymbolExtractor -> SchematicCanvas (all in Python) ->
          one-shot batched emit to Altium. Layout decisions are made
          before any IPC; an SVG preview is written next to the project
          file so you can sanity-check the schematic without opening it.

        - ``use_canvas=False``: the legacy executor that interleaves
          layout with Altium IPC. Kept as a fallback while the new path
          stabilises.

        Both halt early if the plan contains any needs_creation parts.
        Resolve those first by either picking an existing-lib equivalent
        or branching into a library-authoring sub-task.

        Args:
            plan_json: Either a DesignPlan JSON string, or the DesignPlan
                as a JSON object/dict. The MCP framework auto-deserializes
                JSON-object literals to dicts before the tool sees them,
                so both shapes are accepted.
            project_path: Absolute path to the target .PrjPcb. Created
                if it does not exist.
            use_canvas: Pick the execution path. Default ``True`` runs
                the new canvas pipeline; pass ``False`` to fall back to
                the legacy executor.
            placement_hints: Optional ``{refdes: {"x": int, "y": int,
                "rotation": int}}`` partial anchors. Hinted refdes pin
                to the supplied position; others run through the
                algorithmic placement (Sugiyama + multi-try scoring).
                Used by the agent-in-loop refinement workflow:
                  1. Run ``design_preview_plan`` -> see SVG + score.
                  2. If layout is bad, identify specific refdes that
                     should sit elsewhere; build hints dict.
                  3. Call ``design_preview_plan`` again with hints ->
                     iterate until score is acceptable.
                  4. Call ``design_execute_plan`` with the same hints
                     to emit the refined layout.
            mode: How to reach the sheet. ``"auto"`` (default) EDITS a
                sheet this tool has drawn before, moving only the parts
                whose position changed, placing the new ones, deleting
                the ones the plan dropped, and leaving every other
                component exactly as it is. It draws from scratch when
                there is no prior sheet. This is what makes re-running a
                changed plan safe on a schematic somebody has worked on.
                ``"full"`` re-places every component, discarding any
                hand edit; use it when you want the sheet redrawn.
                ``"delta"`` refuses rather than redraw.

                The edit path only knows what is in the plan. To keep a
                part where a person dragged it, read the sheet back with
                ``design_hints_from_sheet`` and pass the result as
                ``placement_hints``.

        Returns:
            Result dict with ok / project_path / sheets_touched / placed
            (list of placements) / failures / needs_creation / notes.
            Canvas-path additions: ``canvas`` (the SchematicCanvas dict
            snapshot) and ``preview_svg_path`` (where the SVG was written).
            On an edit, ``delta`` lists the refdes added, moved, replaced,
            removed and untouched; ``placed`` then covers only the parts
            actually placed, which on an edit is the new ones.
        """
        if use_canvas:
            return execute_plan_via_canvas_from_json(
                plan_json, project_path,
                placement_hints=placement_hints,
                mode=mode,
            )
        if mode != "auto":
            return {"ok": False, "reason":
                    "mode is a canvas-path option; the legacy executor "
                    "(use_canvas=False) always redraws the whole sheet"}
        if isinstance(plan_json, dict):
            plan_json = json.dumps(plan_json)
        result = execute_plan_from_json(plan_json, project_path)
        return result.to_dict()

    @mcp.tool()
    async def design_learn_from_layout(
        project_path: str,
    ) -> dict[str, Any]:
        """Capture your placement edits as training data.

        Workflow:
        1. Run ``design_execute_plan`` (canvas path): it writes a
           ``<project>.canvas.json`` snapshot alongside the .PrjPcb.
        2. Open the schematic in Altium, drag components to taste, save.
        3. Call this tool. It reads the snapshot + current Altium
           positions, diffs them, and appends one row per moved component
           to ``%USERPROFILE%\\.eda-agent\\placement_edits.jsonl``.

        Each row carries: design_id, refdes, part_role, part_lib_ref,
        anchor_refdes, anchor_role, anchor_lib_ref, dx_mils, dy_mils,
        rot_delta_deg, design_size, ts.

        Anchor = highest-pin-count netlist neighbor on a non-power /
        non-ground net (or spatial-nearest fallback). Captures the
        relational placement preference, not just "I moved this 200 mils
        right".

        The accumulating log feeds ``placement_priors.json`` (the
        relative-anchor priors used by the pipeline's placement pass).

        Args:
            project_path: Same project path you passed to
                ``design_execute_plan``.

        Returns:
            Dict with ok, rows_appended, refdes_moved, refdes_unchanged,
            log_path, notes.
        """
        if not project_path:
            return {"ok": False, "reason":
                    "project_path is required: the same .PrjPcb path "
                    "passed to design_execute_plan"}
        return learn_from_layout(project_path)

    @mcp.tool()
    async def design_plan_from_sheet(
        project_path: str,
        sheet_document: str = "",
    ) -> dict[str, Any]:
        """Read a DesignPlan back off a schematic, including one you did
        not draw.

        Use this when you are asked to change a schematic that has no
        ``<project>.canvas.json`` beside it: a sheet drawn by hand, or
        one from an older project. It gives you the plan the rest of the
        design tools take, so an edit becomes "change the plan, lay it
        out again" instead of dragging symbols around one at a time.

        The plan is reconstructed from what is actually drawn: the
        components give the parts, the compiled netlist gives the nets,
        and the power-port glyphs say which nets are rails. It is
        validated before it is returned.

        WHAT A SCHEMATIC DOES NOT RECORD, so you must supply it:

        - ``role`` on each part. This is the tag that lets the placer
          recognise a decoupling bank, a crystal cluster, an op-amp
          motif. A plan without roles lays out noticeably worse. Read
          the returned part list and netlist and assert the roles
          yourself before laying it out.
        - ``zone`` on each part, if you want functional blocks.
        - Single-pin nets, which the plan schema cannot express. They
          come back in ``dropped_pins``.

        Then: edit the plan, call ``design_layout_schematic`` to see it,
        and ``design_execute_plan`` to apply it. Pass
        ``design_hints_from_sheet`` output as ``placement_hints`` if you
        want the existing positions kept.

        Args:
            project_path: The .PrjPcb the sheet belongs to. Its compiled
                netlist is what supplies the nets.
            sheet_document: Full path to the .SchDoc to read. Defaults to
                the active document.

        Returns:
            ``ok`` / ``plan`` (a DesignPlan dict, or None on a refusal) /
            ``parts_read`` / ``nets_read`` / ``dropped_pins`` / ``notes``.
        """
        return plan_from_live_sheet(project_path, sheet_document)

    @mcp.tool()
    async def design_hints_from_sheet(
        project_path: str,
    ) -> dict[str, Any]:
        """Current sheet positions, as placement_hints for a re-run.

        THIS IS HOW YOU EDIT A SCHEMATIC WITH THE ENGINE. Editing by hand
        means choosing coordinates, which is what sch_place_components
        refuses. Do this instead:

        1. ``design_edit_plan`` to change the plan (add the part, change
           a value, add a net).
        2. ``design_hints_from_sheet`` to capture where everything
           currently sits.
        3. ``design_execute_plan`` with those hints. Every existing part
           stays exactly where it is, and only the new or changed ones are
           placed, by the engine rather than by you.

        Positions come back as BODY CENTRES, the frame placement_hints
        expects, converted from the symbol origins Altium reports. Pass
        the returned ``hints`` through unchanged; do not round or adjust
        them, and drop a refdes from the dict only when you deliberately
        want the placer free to move that part.

        Needs the ``<project>.canvas.json`` snapshot that
        ``design_execute_plan`` writes, which is what records the library
        symbol behind each refdes.

        Args:
            project_path: Absolute path to the .PrjPcb whose sheet to read.

        Returns:
            ``ok`` / ``hints`` ({refdes: {x, y, rotation}}) / ``unmatched``
            (refdes present in the snapshot but not readable now) /
            ``notes``.
        """
        return hints_from_sheet(project_path)

    @mcp.tool()
    async def design_preview_plan(
        plan_json: Union[str, dict],
        output_svg_path: Optional[str] = None,
        placement_hints: Optional[dict[str, dict[str, int]]] = None,
    ) -> dict[str, Any]:
        """Render a DesignPlan to SVG without emitting to Altium.

        Same pipeline as ``design_execute_plan(use_canvas=True)`` minus
        the place + wire + save IPC pass. Symbol extraction still
        consults Altium on cache miss (it has to read the SchLib), but
        no project is created and nothing is placed. Use this to
        sanity-check a layout cheaply before committing to a full emit.

        Args:
            plan_json: DesignPlan as JSON string or dict.
            output_svg_path: Where to write the SVG. Default:
                ``<repo>/.symbol_cache/preview.svg``.
            placement_hints: Optional ``{refdes: {"x", "y", "rotation"}}``
                partial anchors, same as ``design_execute_plan``: hinted
                refdes pin to the supplied position, the rest run through
                algorithmic placement. Iterate preview→hints→preview to
                refine a layout before a full emit.

        Returns:
            Dict with ok / preview_svg_path / canvas (snapshot) /
            counts {placements, wires, labels, power_ports, junctions} /
            notes / failures.
        """
        result = preview_plan_from_json(
            plan_json, output_svg_path, placement_hints=placement_hints,
        )
        # Cache the most recent plan input for the dashboard's Plan tab.
        try:
            from ..config import get_config
            cache_path = get_config().workspace_dir / "plan.json"
            payload = plan_json if isinstance(plan_json, dict) else json.loads(plan_json)
            cache_path.write_text(
                json.dumps({"plan": payload, "preview": result}, indent=2),
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return result

    @mcp.tool()
    async def design_audit_schematic(
        project_path: Optional[str] = None,
        cluster_radius_mils: int = 600,
    ) -> dict[str, Any]:
        """Structured visual/layout audit of the active schematic.

        Call AFTER ``design_execute_plan`` and BEFORE ``design_validate``
        so layout problems are fixed first; ERC violations downstream are
        less noisy. Detects three classes of issue, each with enough
        geometry for the planner to compute a corrective move:

          * overlaps        - pairs of components whose bboxes intersect
          * wire_crossings  - wire segments that cross a component body
                              (excluding pin-to-pin connections)
          * stacked_ports   - 3+ power/ground glyphs of the same net
                              huddled inside ``cluster_radius_mils``

        Args:
            project_path: Optional .PrjPcb path. None uses the focused
                project.
            cluster_radius_mils: Radius for stacked-port clustering.
                Default 600 mils.

        Returns:
            SchematicAuditReport dict: ``{ok, project_path, overlaps[],
            wire_crossings[], stacked_ports[], notes[]}``.
            ``ok=True`` iff every list is empty.
        """
        report = run_audit_schematic(
            project_path,
            cluster_radius_mils=cluster_radius_mils,
        )
        return report.to_dict()

    @mcp.tool()
    async def design_validate(project_path: Optional[str] = None) -> dict[str, Any]:
        """ERC + connectivity sanity report on the focused project.

        Runs run_erc, project.get_messages, and get_unconnected_pins,
        then bundles the output into a structured ValidationReport that
        the planner can read and respond to. Schematic-only in this
        slice, PCB validation is a separate later slice.

        Args:
            project_path: Optional absolute path to a .PrjPcb. If omitted,
                uses the focused project.

        Returns:
            ValidationReport dict: ``{passed, project_path, errors,
            warnings, notes}`` where each error/warning is an Issue with
            ``{category, severity, text, refdes, pin, net, sheet}``.
        """
        report = run_validate(project_path)
        return report.to_dict()

    @mcp.tool()
    async def design_validate_requirement(
        requirement: dict,
    ) -> dict[str, Any]:
        """Validate a structured DesignRequirement before planning starts.

        Stage 1 of the autonomous flow (requirement capture & architecture,
        reference/autonomy-roadmap.md): capture what the board must do as a
        ``DesignRequirement`` dict, run this gate, and only move on to part
        selection / DesignPlan construction when it returns ok. Every fact
        the requirement does NOT state goes into ``open_questions`` as a
        question for the user -- it is never guessed -- and validation fails
        while any question remains unresolved.

        Checks beyond the schema: unresolved open questions, no outputs, no
        power source, inverted temperature / IO-voltage ranges, comms IO
        without a protocol, and supply rails or power outputs whose
        magnitude exceeds every stated power input (a boost / inverting
        stage the user must confirm). Pure Python, no Altium.

        Args:
            requirement: DesignRequirement as a JSON object/dict. Units are
                SI with the unit in the field name (``voltage_v``,
                ``current_a``, ``temp_min_c``); mechanical dimensions are
                MILLIMETRES (``board_size_mm``, ``height_max_mm``).

        Returns:
            ``{"ok": True, "issues": [], "summary": "..."}`` when the
            requirement is planning-ready (``summary`` is the labelled-line
            block to embed in ``DesignPlan.summary``), or
            ``{"ok": False, "issues": [...]}`` / ``{"ok": False,
            "errors": [...]}`` (schema problems) listing what to resolve.
        """
        try:
            req = DesignRequirement.model_validate(requirement)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ],
            }
        result = validate_requirement(req)
        result["summary"] = summarize_requirement(req)
        return result

    @mcp.tool()
    async def design_load_fab_profile(profile: dict) -> dict[str, Any]:
        """Validate a fab capability profile and echo the normalized form.

        Stage 8 of the autonomous flow (rules & stackup,
        reference/autonomy-roadmap.md): before synthesizing design rules,
        the fab's published limits are captured as a ``FabProfile`` dict --
        transcribed from the fab's capability page (cite it in ``source``),
        NEVER recalled from memory. This tool is the schema gate: it
        validates the dict (all dimensions MILS, copper weight oz/ft^2,
        stackups with copper outer layers and no adjacent copper plies) and
        returns the normalized profile for ``design_synthesize_rules``.

        Args:
            profile: FabProfile as a JSON object/dict: ``name``, optional
                ``source`` citation, ``copper_layer_counts``, the seven
                ``min_*_mils`` floors, and optional ``stackups``.

        Returns:
            ``{"ok": True, "profile": {...}, "stackups": [names],
            "summary": "..."}`` or ``{"ok": False, "reason": "..."}``.
        """
        loaded = load_fab_profile(profile)
        if not loaded["ok"]:
            return loaded
        prof = loaded["profile"]
        return {
            "ok": True,
            "profile": prof.model_dump(),
            "stackups": [s.name for s in prof.stackups],
            "summary": (
                f"{prof.name}: {len(prof.stackups)} stackup(s), copper "
                f"layer counts {prof.copper_layer_counts}, min track "
                f"{prof.min_track_mils:g} / gap {prof.min_gap_mils:g} / "
                f"drill {prof.min_drill_mils:g} mils."
            ),
        }

    @mcp.tool()
    async def design_synthesize_rules(
        profile: dict,
        plan_json: Union[str, dict] = "",
        net_class_map: Optional[dict] = None,
        options: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Synthesize PCB design rules + stackup ops from a fab profile.

        Stage 8 of the autonomous flow (rules & stackup,
        reference/autonomy-roadmap.md): turn the validated fab profile, the
        plan's net classes, and a few board-level targets into the concrete
        ``pcb_create_design_rule`` and ``pcb_modify_layer`` parameter dicts,
        dispatched verbatim after ``pcb_create_net_class`` creates classes
        named after the synthesized groups. Every number traces to a
        profile field or a verified calculator (IPC-2221 width inverse,
        IPC-2141 impedance inverse); a rule whose inputs are missing is
        skipped with a note, never guessed. Pure Python, no Altium.

        Args:
            profile: FabProfile as a dict (see ``design_load_fab_profile``).
            plan_json: Optional DesignPlan (JSON string or dict). When given
                and ``net_class_map`` is not, net classes are derived from
                the plan (flags + net roles, naming-agnostic).
            net_class_map: Optional explicit ``{net_name: class}`` map;
                takes precedence over ``plan_json`` derivation.
            options: Optional synthesis targets: ``stackup`` (name, default
                first), ``class_current_a`` ({class: amps} -> per-class
                width rules), ``delta_t_c``, ``track_margin``, ``layer``,
                ``copper_oz``, ``geometry``, ``diff_pair_target_ohms``
                (required for differential rules),
                ``diff_pair_spacing_mils``.

        Returns:
            ``{"ok": True, "rules": [...], "stackup_ops": [...],
            "notes": [...], "stackup": name|None, "net_classes": {...}}``
            -- rule values are INTEGER MILS -- or
            ``{"ok": False, "reason": "..."}``.
        """
        ncm: dict = {}
        if net_class_map is not None:
            ncm = net_class_map
        elif plan_json:
            payload = plan_json if isinstance(plan_json, dict) else None
            if payload is None:
                try:
                    payload = json.loads(plan_json)
                except json.JSONDecodeError as exc:
                    return {"ok": False, "reason": f"invalid plan JSON: {exc}"}
            try:
                plan = DesignPlan.model_validate(payload)
            except ValidationError as exc:
                return {"ok": False, "reason": f"invalid plan: {exc}"}
            ncm = classify_nets(plan).by_net

        result = synthesize_rules(profile, ncm, options)
        if not result["ok"]:
            return result
        result["net_classes"] = ncm
        return result

    @mcp.tool()
    async def design_plan_hierarchy(
        plan_json: Union[str, dict],
        max_parts_per_sheet: int = 20,
    ) -> dict[str, Any]:
        """Propose a multi-sheet hierarchy for a dense DesignPlan.

        Stage 6 of the autonomous flow (schematic emit,
        reference/autonomy-roadmap.md): a plan past ~20 parts stops fitting
        one readable sheet and dense single sheets are the known short-risk
        regime. This tool partitions the parts by signal connectivity
        (min-cut, zones kept atomic), names each child sheet from its
        dominant zone role, derives the inter-sheet ports from the signal
        nets the cut severs (power/ground rails are continuous through
        power ports and never become sheet entries), and emits the
        top-sheet op list in the exact parameter shapes of
        ``sch_place_sheet_symbol`` / ``sch_place_sheet_entry`` /
        ``sch_generate_toc``. Apply the result to the plan with
        ``design_apply_hierarchy`` before ``design_execute_plan``.
        Deterministic; pure Python, no Altium.

        Args:
            plan_json: DesignPlan as a JSON string or dict.
            max_parts_per_sheet: Split threshold (default 20). At or below
                it the plan stays single-sheet (``split=False``).

        Returns:
            ``{"ok": True, "split": bool, "top_sheet": str, "sheets":
            [{name, refdes, zones, part_count}], "ports": [{net,
            from_sheet, to_sheet, io_type}], "top_sheet_ops": [{tool,
            params}], "cut_nets": int}`` (op coordinates are MILS) or
            ``{"ok": False, "reason": "..."}``.
        """
        payload = plan_json if isinstance(plan_json, dict) else None
        if payload is None:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "reason": f"invalid plan JSON: {exc}"}
        return plan_hierarchy(payload, max_parts_per_sheet=max_parts_per_sheet)

    @mcp.tool()
    async def design_apply_hierarchy(
        plan_json: Union[str, dict],
        hierarchy: dict,
    ) -> dict[str, Any]:
        """Rewrite a DesignPlan onto the sheets a hierarchy proposes.

        Stage 6 of the autonomous flow (schematic emit,
        reference/autonomy-roadmap.md), the second half of the hierarchy
        step: take the result of ``design_plan_hierarchy`` and produce a
        NEW plan with ``sheets`` = [top sheet, *child sheets], every
        part's ``sheet`` re-homed per the hierarchy, and every zone moved
        with its parts (part-less zones park on the top sheet). The input
        plan is never mutated; a non-split hierarchy returns the plan
        unchanged. Feed the returned plan to ``design_validate_plan`` and
        then ``design_execute_plan``; emit the hierarchy's
        ``top_sheet_ops`` separately to draw the top sheet. Pure Python.

        Args:
            plan_json: DesignPlan as a JSON string or dict.
            hierarchy: The dict returned by ``design_plan_hierarchy``.

        Returns:
            ``{"ok": True, "plan": {...rewritten DesignPlan...},
            "sheets": [names], "summary": "..."}`` or
            ``{"ok": False, "reason": "..."}`` on a malformed plan or
            hierarchy.
        """
        payload = plan_json if isinstance(plan_json, dict) else None
        if payload is None:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "reason": f"invalid plan JSON: {exc}"}
        try:
            new_plan = apply_hierarchy(payload, hierarchy)
        except ValidationError as exc:
            return {"ok": False, "reason": f"invalid plan: {exc}"}
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        sheet_names = [s.name for s in new_plan.sheets]
        per_sheet = {
            name: sum(1 for p in new_plan.parts if p.sheet == name)
            for name in sheet_names
        }
        return {
            "ok": True,
            "plan": new_plan.model_dump(mode="json"),
            "sheets": sheet_names,
            "summary": (
                f"{len(new_plan.parts)} part(s) across "
                f"{len(sheet_names)} sheet(s): "
                + ", ".join(f"{n}={per_sheet[n]}" for n in sheet_names)
                + "."
            ),
        }
