# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design-agent MCP tools, surfaces the design discipline + primitives.

Claude Code is the planner. It calls ``design_get_discipline`` to read
the rules, ``design_snapshot_inventory`` to learn what parts exist in
the user's libraries, then constructs a DesignPlan JSON, validates it
with ``design_validate_plan``, and draws it from a hand template with
``sch_draw_template``.

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
from ..design import fab_library
from ..design.fab_profile import load_fab_profile
from ..design.hierarchy import apply_hierarchy, plan_hierarchy
from ..design.requirement import (
    DesignRequirement,
    summarize_requirement,
    validate_requirement,
)
from ..design.rule_synthesis import synthesize_rules
from ..design.inventory import LibraryInventory, snapshot_live
from ..design.sheet_plan import plan_from_live_sheet
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


#: Entries a list in a job result keeps inline; the rest go to a file.
_JOB_LIST_INLINE = 25


def _trim_job_result(rec: dict, job_id: str) -> dict:
    """``rec`` with each long list in its result cut to its first entries
    and counted, the whole of it written to the workspace."""
    import json

    from ..config import get_config

    result = dict(rec["result"])
    trimmed = {}
    for key, value in list(result.items()):
        if isinstance(value, list) and len(value) > _JOB_LIST_INLINE:
            trimmed[key] = len(value)
            result[key] = value[:_JOB_LIST_INLINE]
    out = dict(rec, result=result)
    if trimmed:
        folder = get_config().workspace_dir / "jobs"
        folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in job_id)
        path = folder / f"{safe}.json"
        path.write_text(json.dumps(rec, default=str), encoding="utf-8")
        out["trimmed"] = {k: f"{n} entries, first {_JOB_LIST_INLINE} shown"
                          for k, n in trimmed.items()}
        out["full_path"] = str(path)
    return out


def register_design_tools(mcp) -> None:
    """Register the design-agent tools with the MCP server."""

    @mcp.tool()
    async def design_get_discipline(include_schema: bool = False) -> dict[str, Any]:
        """Read the design discipline, and on request the DesignPlan schema.

        ALWAYS call this first when starting a design task. The result
        contains the hard rules the planner must follow (datasheet-first
        part choice, NDA isolation, the template method for schematics,
        placement and routing discipline).

        Args:
            include_schema: Also return the DesignPlan JSON schema, which
                the plan tools (design_validate_plan, design_review_plan,
                design_compose_netlist) take. A schematic drawn from
                templates does not need it.

        Returns:
            A dict with ``discipline`` (markdown text) and, when asked,
            ``schema`` (DesignPlan JSON schema as a dict).
        """
        # Under one tool result: with the schema always in, the reply was
        # 52.5 KB and Claude Code set it aside in a file under the user's
        # profile, whose path it then showed on screen.
        out: dict[str, Any] = {"discipline": get_discipline(include_schema=False)}
        if include_schema:
            out["schema"] = DesignPlan.model_json_schema()
        return out

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

    def _mirror_to_live_view(event, stage, status, text, path, kind, question,
                             revision, topic) -> None:
        """The live view's decision log shows the run's journal entries
        beside the board, so a run that logs to the journal is visible
        without also calling design_live_note. Never fails the log."""
        line = {
            "stage_enter": f"Starting {stage}.",
            "stage_result": f"{stage}: {status}" + (f". {text}" if text else "."),
            "plan_revision": f"Plan revision {revision}" + (f": {text}" if text else "."),
            "artifact": f"Wrote {kind or 'a file'}: {path}",
            "blocked": f"Blocked: {question}",
            "resolved": f"Resolved: {text}",
            "note": text,
        }.get(event, "")
        if not line.strip():
            return
        section = (topic or "note") if event == "note" else (stage or event)
        try:
            from ..design import live
            live.note(section, line)
        except Exception:  # noqa: BLE001 - the view never fails the journal
            pass

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
        topic: str = "",
        data: Optional[Union[dict, str]] = None,
    ) -> dict[str, Any]:
        """Append one event to a design-session journal.

        ``event`` is one of: ``stage_enter`` (needs ``stage``),
        ``stage_result`` (``stage`` + ``status`` = ok|blocked|failed),
        ``plan_revision`` (``revision``), ``artifact`` (``path``, ``kind``),
        ``blocked`` (``question``), ``resolved`` (``text`` = answer),
        ``note`` (``text``). Returns the updated derived state.

        ``data`` (a dict, or JSON text) goes with a ``stage_result`` or a
        ``note``. On a stage_result it is the evidence for the exit gate:
        for placement, routing, pours_tuning and verification, pass the
        ``pcb_layout_audit`` result (and the ``pcb_calc_length_match`` or
        ``proj_run_erc`` reply under its own tool name) and the reply
        carries ``gate``, the verdict on those numbers. A stage logged ok
        whose numbers fail its gate is sent back by
        ``design_next_action``. ``topic`` files a note under a section of
        ``design_session_report``: placement, stackup, critical_routes,
        power, planes (fan-out and via-in-pad too), silkscreen,
        verification, simulation, issue, or decision.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no active design session; call design_session_start first"}
        if isinstance(data, str):
            try:
                data = json.loads(data) if data.strip() else None
            except json.JSONDecodeError as exc:
                return {"error": f"data is not valid JSON: {exc}"}
        if data is not None and not isinstance(data, dict):
            return {"error": "data must be a JSON object"}
        try:
            if event == "stage_enter":
                journal.enter_stage(stage)
            elif event == "stage_result":
                journal.stage_result(stage, status, verdict=text, data=data)
            elif event == "plan_revision":
                journal.plan_revision(revision, summary=text)
            elif event == "artifact":
                journal.artifact(path, kind=kind)
            elif event == "blocked":
                journal.blocked(question, stage=stage)
            elif event == "resolved":
                journal.resolved(text)
            elif event == "note":
                journal.note(text, topic=topic, data=data)
            else:
                return {"error": f"unknown event kind: {event!r}"}
        except ValueError as e:
            return {"error": str(e)}
        _mirror_to_live_view(event, stage, status, text, path, kind, question,
                             revision, topic)
        out: dict[str, Any] = {"session_id": journal.session_id,
                               "state": asdict(journal.state())}
        if event == "stage_result" and data:
            from ..design.autonomy import MEASURED_GATES, evaluate_gate
            if stage in MEASURED_GATES:
                out["gate"] = evaluate_gate(stage, data)
        return out

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
        from ..design.autonomy import apply_measured_gates
        state, gates = apply_measured_gates(journal.state(), journal.events())
        if state.open_question:
            guidance = f"BLOCKED: ask the user: {state.open_question}"
        elif state.complete:
            guidance = "All 13 pipeline stages complete."
        else:
            guidance = f"Next stage: {state.next_stage}"
            sent_back = gates.get(state.next_stage)
            if sent_back and sent_back["verdict"] == "fail":
                guidance += (" (its logged numbers fail the exit gate: "
                             + "; ".join(sent_back["failed"]) + ")")
        return {
            "session_id": journal.session_id,
            "guidance": guidance,
            "state": asdict(state),
            "gates": gates,
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

        The layout stages (placement, routing, pours_tuning, verification)
        have measured gates: the reply also carries ``gate``, the numbers
        and the values that pass, and ``last_gate``, the verdict on numbers
        already logged for the stage. A stage logged ok whose logged numbers
        fail its gate comes back as ``retry``, with the failing numbers in
        ``guidance``. A stage logged with no numbers is taken as logged.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no design sessions found; start one with design_session_start"}
        from ..design.autonomy import measured_next_action
        return _adapt_action(measured_next_action(journal.state(), journal.events()))

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

    @mcp.tool()
    async def design_session_report(session_id: str = "",
                                    output_path: str = "") -> dict[str, Any]:
        """Write up a design run from its session journal, as markdown.

        Assembled only from what the run recorded: the requirement, each
        stage's outcome with the gate numbers logged for it, decisions,
        audit results, simulations and open issues. The body sections come
        in a fixed order: placement strategy, stack-up and impedance,
        critical routes (lengths, skews, layer changes, return vias,
        exceptions), power paths, planes and fan-out (where via-in-pad was
        used and why), silkscreen policy, verification results, simulations,
        open issues. A section nothing was logged for says so in one line.
        Notes reach a section through their ``topic`` (see
        ``design_session_log``).

        Args:
            session_id: the session to report; blank uses the most recently
                active one.
            output_path: also write the markdown to this file. Its folder
                must exist, and a path inside the installed eda_agent
                package is refused. Blank writes nothing.

        Returns:
            ``{"session_id", "markdown", "path"}`` (``path`` is "" when
            nothing was written), or ``{"error": ...}``.
        """
        journal = _resolve_journal(session_id)
        if journal is None:
            return {"error": "no design sessions found; start one with design_session_start"}
        if session_id and not journal.path.exists():
            return {"error": f"no design session {session_id!r}"}
        from ..design.report import build_report
        markdown = build_report(journal)
        out: dict[str, Any] = {"session_id": journal.session_id,
                               "markdown": markdown, "path": ""}
        if not output_path:
            return out
        import eda_agent
        target = Path(output_path).expanduser().resolve()
        package = Path(eda_agent.__file__).resolve().parent
        if target == package or package in target.parents:
            return {"error": f"refusing to write inside the installed package ({package}); "
                             "choose a path outside it"}
        if target.is_dir():
            return {"error": f"{target} is a folder; give a file path"}
        if not target.parent.is_dir():
            return {"error": f"the folder {target.parent} does not exist"}
        target.write_text(markdown, encoding="utf-8")
        out["path"] = str(target)
        return out

    @mcp.tool()
    async def pcb_layout_audit(
        checks: Optional[list[str]] = None,
        expect_file: str = "",
        board_json_path: str = "",
        nets: Optional[list[str]] = None,
        max_distance_mils: float = 40.0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Measure a board for the layout stages' exit gates. Changes nothing.

        Reads the focused board (or a saved LayoutBoard JSON) and runs
        these checks, each with counts and findings in mils:

        - ``placement_audit``: body overlaps, pads under clearance, parts
          on keepouts or off the board.
        - ``connectivity_summary``: routed / partial / unstarted nets.
        - ``drc``: the exact clearance check.
        - ``corner_audit``: bends sharper than 45 degrees.
        - ``return_via_audit``: signal vias with no ground via within
          ``max_distance_mils``.
        - ``plane_region_audit``: pour copper broken into islands.
        - ``loop_audit``: hot, gate-drive, decoupling, crystal and other
          loops, worst first. Advisory.
        - ``pad_entry_audit``: off-axis, off-centre, too-wide or uneven
          pad entries. Fails only on high severity.
        - ``rotation_audit``: parts whose connections are shorter turned
          90, 180 or 270 degrees, a turn into a neighbour's courtyard
          excluded. Advisory.

        Log the result as ``data`` on the stage_result
        (``design_session_log``).

        Args:
            checks: names from the list above; None runs them all.
            expect_file: full path of the focused board, for a live read;
                refused if another board is focused.
            board_json_path: a saved LayoutBoard (.json or .json.gz) to
                audit offline instead.
            nets: nets for ``return_via_audit``; None checks diff-pair
                and high-speed nets, or every signal via.
            max_distance_mils: return via distance, centre to centre.
            limit: findings listed per kind; counts are never cut.

        Returns:
            ``{"board", "pass", "summary": {check: {pass, counts...}},
            "checks": {check: full result}, "source": "live"|"file"}``, or
            ``{"error": ...}``.
        """
        import asyncio

        from ..layout.audit import CHECKS, run_audits

        names = list(checks) if checks else None
        unknown = [n for n in names or [] if n not in CHECKS]
        if unknown:
            return {"error": f"unknown check(s) {unknown}; known: {list(CHECKS)}"}
        if board_json_path:
            from ..layout.model import LayoutBoard
            source_path = Path(board_json_path)
            if not source_path.is_file():
                return {"error": f"no such file: {board_json_path}"}
            try:
                board = await asyncio.to_thread(LayoutBoard.load, source_path)
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                return {"error": f"could not load {board_json_path}: {exc}"}
            source = "file"
        elif expect_file:
            from ..layout.read_altium import WrongBoard, read_live_board
            try:
                board = await asyncio.to_thread(read_live_board, expect_file)
            except WrongBoard as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                return {"error": f"board read failed: {exc}"}
            source = "live"
        else:
            return {"error": "pass expect_file (the focused board's full path) to read "
                             "it live, or board_json_path to audit a saved LayoutBoard"}
        result = await asyncio.to_thread(
            run_audits, board, names, nets=nets,
            max_distance_mils=float(max_distance_mils), limit=max(1, int(limit)))
        result["source"] = source
        result["next_step"] = ("log this result as data on the stage_result "
                               "(design_session_log event='stage_result', data=...)")
        return result

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

    #: Longest design_job_status holds a call: under the client's 120 s.
    _JOB_WAIT_CAP_S = 100.0

    @mcp.tool()
    async def design_job_start(kind: str, params: dict) -> dict[str, Any]:
        """Start a long engine run as a background job; returns its id.

        For work that can exceed the MCP tool timeout (routing a dense
        board). Call `design_job_status` with ``wait_seconds`` until it is
        done (nothing notifies you), then `design_job_result`.

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
    async def design_job_status(job_id: str = "", wait_seconds: float = 0) -> dict[str, Any]:
        """Status of a background job (or all jobs if ``job_id`` is blank).

        Nothing tells you when a job finishes, so keep calling this until
        it is done rather than ending your turn: a session that stopped to
        "check when it finishes" was never woken again.

        Args:
            job_id: the job; blank lists every job.
            wait_seconds: hold the call until the job finishes or this
                many seconds pass (capped at 100, inside the client's
                two-minute limit). 0 answers at once.
        """
        import asyncio

        from ..design.jobs import get_job_store
        store = get_job_store()
        if not job_id:
            return {"jobs": store.list()}
        wait = max(0.0, min(float(wait_seconds or 0), _JOB_WAIT_CAP_S))
        if wait and store.get(job_id) is not None:
            await asyncio.to_thread(store.wait, job_id, wait)
        rec = store.status(job_id)
        return rec if rec else {"error": f"no such job: {job_id}"}

    @mcp.tool()
    async def design_job_result(job_id: str, full: bool = False) -> dict[str, Any]:
        """Fetch a finished job's result payload (None until it's done).

        A router result lists every track and via, which ran to 250,000
        characters on a 61-LED board; the client set it aside in a file
        instead of reading it. So any list longer than a few dozen
        entries comes back as its count and first entries, and the whole
        result is written to ``full_path`` (pcb_autoroute_apply takes the
        tracks from the job itself, not from this reply).

        Args:
            job_id: the job.
            full: return every entry inline, however long.
        """
        from ..design.jobs import get_job_store
        rec = get_job_store().result(job_id)
        if not rec:
            return {"error": f"no such job: {job_id}"}
        if full or not isinstance(rec.get("result"), dict):
            return rec
        return _trim_job_result(rec, job_id)

    @mcp.tool()
    async def design_snapshot_inventory(
        library_paths: list[str],
        name_filter: str = "",
        limit_per_library: int = 60,
        include_descriptions: bool = True,
    ) -> dict[str, Any]:
        """Open a list of SchLib files and return what components live in them.

        NDA scope: pass only the user's own neutral standard libraries,
        never project-local libraries from another client engagement.

        The returned set is filtered and capped; the full inventory is
        always written to ``<workspace>/inventory.json``.

        Args:
            library_paths: Absolute paths to .SchLib files to scan.
            name_filter: Case-insensitive substring; keep only components
                whose lib_ref or description contains it (e.g. ``"10k"``,
                ``"NE555"``). Empty = no filter.
            limit_per_library: Cap components returned per library
                (default 60; 0 = unlimited). Each library reports
                ``total`` and ``returned`` so you know if it was capped.
            include_descriptions: False drops the (often long)
                ``description`` from each row. Default True.

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

        Run this before drawing to catch schema problems and
        cross-references that ``sch_draw_template`` would reject. Cheap;
        no Altium round-trip.

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
        """Add a standard sub-circuit to a DesignPlan, wired, in one call.

        It sets the topology (unique refdes, every pin on its net, power
        and ground tagged) and re-validates the plan. You choose every
        part: lib_ref, value, footprint, mpn, datasheet_url in ``params``.
        Nets the block only touches (a pull-up's signal, a divider's rail)
        should already exist in the plan.

        Blocks and required params (all accept pins, refdes_prefix, sheet;
        design_list_circuit_blocks gives the full contract):
          ``decoupling``: power_net, ground_net, lib_ref [count, value]
          ``pullup``: signal_net, rail_net, lib_ref
          ``pulldown``: signal_net, ground_net, lib_ref
          ``series_resistor``: net_a, net_b, lib_ref
          ``voltage_divider``: rail_net, ground_net, lib_ref_top,
            lib_ref_bottom [output_net]
          ``rc_lowpass`` / ``rc_highpass``: input_net, ground_net,
            lib_ref_r, lib_ref_c [output_net]
          ``led_indicator``: anode_net, cathode_net, lib_ref_r,
            lib_ref_led
          ``crystal``: xin_net, xout_net, ground_net, lib_ref, cap_lib_ref
          ``pi_filter``: input_net, ground_net, lib_ref_l, lib_ref_c
          ``mosfet_low_side``: control_net, load_net, ground_net,
            lib_ref_r, lib_ref_fet
          ``mosfet_high_side``: control_net, load_net, rail_net,
            lib_ref_r, lib_ref_fet

        Args:
            plan_json: the DesignPlan as a JSON string or dict.
            block: block name from the list.
            params: block parameters.

        Returns:
            {ok, plan, added: {refdes, nets, extended_nets}, notes,
            validation}; ``plan`` is ready for the next block or for
            drawing. ``ok`` False comes with ``errors``.
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

        ``connections`` maps pin to net, as a datasheet pinout reads;
        pins on the same net merge onto it. With
        ``design_add_circuit_block`` and ``design_connect_bus`` this
        builds a whole netlist without raw net JSON. You choose the part
        and the net names.

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

        Joins the i-th pin of every endpoint into one net per bit, so
        bits cannot be misaligned. Creates no parts. A bus of 4 or more
        bits is drawn as a bus.

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

        Runs the operations in order, each seeing the last one's result,
        and validates once at the end: a whole netlist in one call.

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
                ``sch_draw_template``; the parts are unchanged.

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
        """Edit an existing plan instead of re-emitting it. The tool does
        the bookkeeping (a deleted part leaves every net; merged nets
        de-dupe pins). Ops run in order and the result is validated once
        at the end, so a newly floating net surfaces.

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
        """Compute a component value snapped to an E-series, with the value
        actually achieved and its error. Use it instead of hand arithmetic.

        Args:
            kind and what it needs:
              nearest: value
              feedback_divider: v_out, v_ref [r_bottom_ohms]
              resistor_divider: v_in, v_out
              led_resistor: v_supply, v_forward, i_led_ma
              rc_lowpass: f_cutoff_hz and one of r_ohms / c_farads
              crystal_load_caps: c_load_pf [c_stray_pf, default 5]
              i2c_pullup: v_bus, c_bus_pf, t_rise_ns (1000/300/120)
              divider_tolerance: v_in, r_top_ohms, r_bottom_ohms [tol_pct]
              opamp_gain: gain, config (inverting / non_inverting)
              buck_inductor: v_in, v_out, i_out_a, f_sw_khz [ripple_pct]
              capacitor_energy: c_farads, voltage
              holdup_cap: i_load_a, t_s, v_drop
              discharge_resistor: c_farads, v_initial, v_final, t_s
              junction_temp: power_w, theta_ja [t_ambient, default 25]
              max_power: tj_max, theta_ja [t_ambient]
              required_theta_ja: power_w, tj_max [t_ambient]
            series: E6/E12/E24/E48/E96 (default E96; E24 for jellybean).

        Returns:
            {"ok": True, ...values..., "summary"} or {"ok": False, "error"}.
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

        Reads the file without Altium, so it checks only component-level
        items (MPN, datasheet, placeholder values, designators, title
        block): no netlist, no ERC. With Altium running prefer
        ``design_lint_report``, ``proj_run_erc``, ``design_review_snapshot``
        and the ``audit_*`` tools. Disabled unless
        ``EDA_AGENT_HEADLESS_REVIEW=1``.

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
        datasheet}``: or ``{"error": ...}`` if disabled or unreadable
        (missing file, a suffix other than .SchDoc/.PrjPcb, or a project
        whose sheets cannot be resolved). A design read with zero parts
        adds a ``warning``: an empty BOM is not proof of an empty design.
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
        result: dict[str, Any] = {
            "file": str(path),
            "line_count": len(lines),
            "part_count": sum(ln["quantity"] for ln in lines),
            "lines": lines,
        }
        if not lines:
            from ..fileio.bom import EMPTY_BOM_WARNING
            result["warning"] = EMPTY_BOM_WARNING
        return result

    @mcp.tool()
    async def design_find_loops(
        plan_json: Union[str, dict, None] = None,
        board_json_path: str = "",
        expect_file: str = "",
        pin_names: Optional[dict[str, dict[str, str]]] = None,
        kinds: Optional[list[str]] = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Find the circuit loops whose physical size matters, and measure
        them on a placed board. Changes nothing.

        Loops: a switching converter's HOT loop (input cap, switch, low side,
        ground back to the cap; boost: switch, diode, output cap), its
        output loop and bootstrap cap, a MOSFET gate drive, a decoupling cap
        with the IC pins it serves, a crystal with its load caps, a
        regulator FB divider or op-amp feedback network, and a current-sense
        Kelvin connection. Each loop lists its parts and pins IN PATH ORDER
        and says what found it: ``role`` (plan tags), ``pin_name`` (SW,
        BOOT, FB, G/S/D, IN-/OUT, SENSE+) or ``structure`` (netlist shape).

        Give ONE source:
        - ``plan_json``: a DesignPlan; loops only, no geometry.
        - ``board_json_path``: a saved LayoutBoard; loops measured on the
          placement (area and an inductance estimate, or path length),
          worst first.
        - ``expect_file``: the focused board, read live (full path).

        A board carries pad numbers, not pin functions, so pin-function loops
        need ``pin_names``: {refdes: {pad_number: pin_name}}, e.g. from
        lib_get_pin_list. Without them a board gives decoupling, crystal,
        switching and shunt loops from structure alone.

        Args:
            kinds: restrict to these loop kinds.
            limit: loops listed; counts are never cut.
        """
        import asyncio

        from ..design.loops import find_loops, graph_from_plan

        sources = [bool(plan_json), bool(board_json_path), bool(expect_file)]
        if sum(sources) != 1:
            return {"ok": False, "reason": "give exactly one of plan_json, "
                    "board_json_path or expect_file"}
        if plan_json:
            payload = plan_json
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    return {"ok": False, "error": f"invalid JSON: {exc}"}
            try:
                plan = DesignPlan.model_validate(payload)
            except ValidationError as exc:
                return {"ok": False, "errors": [
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                    for e in exc.errors()]}
            loops = [l.as_dict() for l in
                     find_loops(graph_from_plan(plan, pin_names), kinds)]
            source = "plan"
        else:
            from ..layout.loop_audit import analyze_loops
            if board_json_path:
                from ..layout.model import LayoutBoard
                if not Path(board_json_path).is_file():
                    return {"ok": False, "error": f"no such file: {board_json_path}"}
                board = await asyncio.to_thread(LayoutBoard.load,
                                                Path(board_json_path))
                source = "file"
            else:
                from ..layout.read_altium import WrongBoard, read_live_board
                try:
                    board = await asyncio.to_thread(read_live_board, expect_file)
                except WrongBoard as exc:
                    return {"ok": False, "error": str(exc)}
                except Exception as exc:  # noqa: BLE001 - reported to the caller
                    return {"ok": False, "error": f"board read failed: {exc}"}
                source = "live"
            loops = await asyncio.to_thread(analyze_loops, board, pin_names,
                                            kinds)
        by_kind: dict[str, int] = {}
        for l in loops:
            by_kind[l["kind"]] = by_kind.get(l["kind"], 0) + 1
        return {"ok": True, "source": source, "count": len(loops),
                "by_kind": by_kind, "loops": loops[:limit],
                "truncated": len(loops) > limit}

    @mcp.tool()
    async def design_simulators() -> dict[str, Any]:
        """Which circuit and field simulators are installed: ngspice
        (batch build; KiCad's copy is a DLL only) and openEMS with its
        Python bindings, with versions. Changes nothing, installs nothing.

        A missing one comes back with an install offer (source, and the
        scoop command where scoop is present). Put the offer to the user
        and install only after they approve; a missing simulator never
        fails a design, it only skips the simulation.
        """
        import asyncio

        from ..sim.ngspice import simulator_backends

        return await asyncio.to_thread(simulator_backends)

    @mcp.tool()
    async def design_simulate_deck(
        deck: str,
        engine: str = "auto",
        waves: Optional[list[str]] = None,
        points: int = 0,
        timeout_s: float = 120,
    ) -> dict[str, Any]:
        """Simulate a SPICE deck in ngspice or LTspice on this machine and
        return its .meas values, errors and waveforms.

        ``engine`` "auto" uses ngspice when it is installed and LTspice
        when only LTspice is; name "ltspice" or "ngspice" to choose. Both
        return the same shape. Altium's own simulator runs a deck through
        sim_run_deck instead.

        The deck is screened first: .ferret is refused, and .include/.lib
        may only name a bare file or one in the workspace or
        EDA_AGENT_SPICE_MODEL_DIRS. An IC needs its manufacturer's model
        (SPICE_MODEL_RULES); this simulates whatever it is given. LTspice
        refuses a .control block, which is ngspice's.

        Args:
            deck: The SPICE text, with an analysis (.tran, .ac, .dc, .op)
                and any .meas lines.
            engine: "auto", "ngspice" or "ltspice".
            waves: Traces to report (e.g. ["V(out)"]); empty reports all.
            points: Also return this many samples of each trace in
                ``waves`` (AC traces in dB and degrees).
            timeout_s: Stop the simulator after this many seconds.

        Returns:
            ``engine`` and ``why``, ``measures`` ({name: value}),
            ``errors``, ``warnings``, ``plots`` (per analysis, each trace's
            min, max, first and last), ``run_folder`` and ``log_tail``;
            ``install_offers`` when no simulator is installed.
        """
        import asyncio

        from ..config import get_config
        from ..sim.deck_runner import simulate

        return await asyncio.to_thread(simulate, deck, get_config().workspace_dir, engine,
                                       timeout_s, waves, max(0, int(points)))

    @mcp.tool()
    async def design_simulate_loops(
        plan_json: Union[str, dict],
        models: Optional[dict[str, dict]] = None,
        supplies: Optional[dict[str, float]] = None,
        pin_names: Optional[dict[str, dict[str, str]]] = None,
        f_start: float = 1.0,
        f_stop: float = 100e6,
    ) -> dict[str, Any]:
        """Loop gain, crossover, phase and gain margin of every feedback
        loop in a plan, simulated in ngspice. Changes nothing.

        The loops come from design_find_loops. Each is broken at the
        driving pin (an op-amp's output) and measured by Middlebrook
        double injection, so the real load on the output counts. A
        switching regulator's FB loop runs only when its model is marked
        ``"averaged": true``: a vendor's switching (transient) model has
        no small-signal loop gain.

        Models are the manufacturer's: ``models`` maps refdes to
        ``{"file": path, "subckt": name, "pins": [plan pins in the
        subckt's node order]}``. Without ``pins`` the .SUBCKT node names
        are matched to the plan's pin names (and ``pin_names``). R, C and
        L are SPICE primitives from their values. A part with no model is
        listed in ``missing_models`` and nothing runs; nothing is ever
        approximated. Rails take ``supplies`` ({net: volts}) or the
        voltage in the net name (+12V, 3V3).

        No ngspice: the reply carries the install offer and runs nothing.

        A phase margin under 45 degrees is flagged ``low``: a common
        design convention, not a datasheet limit; loops are ranked by
        margin either way.
        """
        import asyncio

        from ..design.loops import find_loops, graph_from_plan
        from ..sim.deck import deck_from_plan
        from ..sim.ngspice import find_ngspice, loop_gain, simulator_backends

        payload = plan_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid JSON: {exc}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}
        models = models or {}
        loops = find_loops(graph_from_plan(plan, pin_names), ["feedback"])
        if not loops:
            return {"ok": True, "loops": [], "note": "no feedback loop found"}
        if not find_ngspice():
            return {"ok": True, "ran": False, "loops": [l.as_dict() for l in loops],
                    "simulators": await asyncio.to_thread(simulator_backends)}

        results = []
        for loop in loops:
            entry = loop.as_dict()
            ic = loop.members[0][0]
            if "FB with divider" in loop.note:
                if not models.get(ic, {}).get("averaged"):
                    entry.update(status="not_run", reason=(
                        "a switching regulator's loop gain needs the vendor's "
                        "averaged (AC) model; mark it \"averaged\": true"))
                    results.append(entry)
                    continue
                # Break between the output rail and the divider's top pin.
                top = next((k for k in loop.members[1:]), None)
                if not top:
                    entry.update(status="not_run", reason="no divider found")
                    results.append(entry)
                    continue
                deck = deck_from_plan(plan, models, supplies, pin_names,
                                      break_at=top)
                drive, load = deck.get("load_node"), deck.get("driver_node")
            else:
                deck = deck_from_plan(plan, models, supplies, pin_names,
                                      break_at=loop.members[0])
                drive, load = deck.get("driver_node"), deck.get("load_node")
            if not deck.get("ok"):
                entry.update(status="not_run", **{
                    k: deck[k] for k in ("missing_models", "needs_pin_map",
                                         "bad_values", "unknown_supplies",
                                         "problems", "model_policy", "reason")
                    if deck.get(k)})
                results.append(entry)
                continue
            run = await asyncio.to_thread(loop_gain, deck["netlist"], drive,
                                          load, f_start, f_stop)
            if not run.get("ok"):
                entry.update(status="failed", errors=run.get("errors"),
                             log_tail=run.get("log_tail"))
                results.append(entry)
                continue
            pm = run.get("phase_margin_deg")
            entry.update(status="ran", method=run["method"],
                         dc_gain_db=run.get("dc_gain_db"),
                         crossover_hz=run.get("crossover_hz"),
                         phase_margin_deg=pm,
                         gain_margin_db=run.get("gain_margin_db"),
                         low=pm is not None and pm < 45)
            if run.get("note"):
                entry["note_sim"] = run["note"]
            results.append(entry)
        ran = [r for r in results if r.get("status") == "ran"]
        ran.sort(key=lambda r: (r.get("phase_margin_deg") is None,
                                r.get("phase_margin_deg") or 0))
        return {"ok": True, "ran": bool(ran), "worst_first": ran,
                "loops": results}

    @mcp.tool()
    async def design_export_spice_deck(
        plan_json: Union[str, dict],
        dialect: str = "ngspice",
        analyses: Optional[dict] = None,
        models: Optional[dict[str, dict]] = None,
        supplies: Optional[dict[str, float]] = None,
        pin_names: Optional[dict[str, dict[str, str]]] = None,
        path: str = "",
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        """Write a plan's circuit as a SPICE deck for ngspice or LTspice.

        The same netlist design_simulate_loops runs: R, C and L from their
        values, everything else from the manufacturer's model (``models``,
        as there), rails as DC sources. ``dialect`` "ltspice" writes a
        .net LTspice opens and runs as it is; "ngspice" writes a .cir. A
        plan read off a live sheet comes from design_plan_from_sheet.

        A part without its model is never approximated: the deck is not
        written and ``gaps`` names what is missing. ``allow_partial``
        writes it anyway with every gap listed in comments at the top, so
        the file cannot pass for the whole circuit.

        Args:
            plan_json: a DesignPlan (object or JSON).
            dialect: "ngspice" or "ltspice".
            analyses: {"op": true}, {"tran": {"step", "stop"}},
                {"ac": {"points", "start", "stop"}} (decade) or {"dc":
                {"source", "start", "stop", "step"}}; default .op.
            models / supplies / pin_names: as design_simulate_loops.
            path: file to write; default <workspace>/sim/<title>.cir|.net.
            allow_partial: write a deck that is missing parts.
        """
        import re as _re

        from ..config import get_config
        from ..sim.deck import deck_from_plan
        from ..sim.dialects import deck_gaps, write_deck

        payload = plan_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid JSON: {exc}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}
        deck = deck_from_plan(plan, models or {}, supplies, pin_names,
                              title=plan.summary or "")
        gaps = deck_gaps(deck)
        out: dict[str, Any] = {
            "ok": False, "dialect": dialect, "complete": not gaps,
            "gaps": gaps, "rails": deck.get("rails"),
            "missing_models": deck.get("missing_models")}
        if gaps and not allow_partial:
            out["reason"] = ("the deck is missing parts, so it was not "
                             "written; give their models, or allow_partial "
                             "to write it with the gaps listed")
            return out
        try:
            text = write_deck(deck["netlist"], dialect, analyses,
                              gaps if gaps else None)
        except ValueError as exc:
            out["reason"] = str(exc)
            return out
        ext = ".net" if dialect.lower() == "ltspice" else ".cir"
        if path:
            target = Path(path)
        else:
            stem = _re.sub(r"[^A-Za-z0-9._-]+", "_",
                           plan.summary or "design").strip("._")[:60] or "design"
            target = get_config().workspace_dir / "sim" / f"{stem}{ext}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            out["reason"] = f"could not write {target}: {exc}"
            return out
        out.update(ok=True, path=str(target), deck=text)
        if dialect.lower() == "ltspice":
            out["note"] = ("open it in LTspice (File > Open, then Run), or run it "
                           "here with design_simulate_deck(engine='ltspice')")
        return out

    @mcp.tool()
    async def pcb_smart_drc(
        board_json_path: str = "",
        expect_file: str = "",
        spacing_mils: float = 40.0,
        probe_mils: float = 100.0,
        currents: Optional[dict[str, float]] = None,
        pin_names: Optional[dict[str, dict[str, str]]] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Design checks from TEMPORARY rules, evaluated offline. Writes no
        rule and changes nothing on the board.

        Asks what the board's own rules usually leave out, using the circuit
        loops (design_find_loops) to know which nets matter:
        - ``spacing``: sensitive nets (regulator/op-amp feedback, crystal)
          closer than ``spacing_mils`` to noisy ones (switch node, bootstrap,
          gate drive). Take ``spacing_mils`` from the datasheet layout
          section where one is given; 40 is a starting value.
        - ``tightest_pairs``: the actual gap of every sensitive/noisy pair
          within ``probe_mils``, smallest first: how close the board runs to
          trouble, not only where it fails.
        - ``hot_loop_width``: hot-loop tracks narrower than the IPC-2221
          width for the current given per net in ``currents`` (amps).

        Gaps use the DRC's own geometry. Give ``board_json_path`` (saved
        LayoutBoard) or ``expect_file`` (focused board, read live). A board
        carries pad numbers only, so pass ``pin_names`` ({refdes: {pad:
        pin_name}}) to find feedback, bootstrap and gate-drive nets.
        """
        import asyncio

        from ..layout.smart_drc import smart_drc

        if bool(board_json_path) == bool(expect_file):
            return {"ok": False, "reason": "give exactly one of "
                    "board_json_path or expect_file"}
        if board_json_path:
            from ..layout.model import LayoutBoard
            if not Path(board_json_path).is_file():
                return {"ok": False, "error": f"no such file: {board_json_path}"}
            board = await asyncio.to_thread(LayoutBoard.load, Path(board_json_path))
            source = "file"
        else:
            from ..layout.read_altium import WrongBoard, read_live_board
            try:
                board = await asyncio.to_thread(read_live_board, expect_file)
            except WrongBoard as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                return {"ok": False, "error": f"board read failed: {exc}"}
            source = "live"
        result = await asyncio.to_thread(
            smart_drc, board, spacing_mils=spacing_mils, probe_mils=probe_mils,
            currents=currents, pin_names=pin_names, limit=limit)
        return {"ok": True, "source": source, **result}

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
    async def design_suggest_partition(
        plan_json: Union[str, dict],
        n_groups: int = 2,
        max_fanout: int = 8,
    ) -> dict[str, Any]:
        """Split a plan's parts into ``n_groups`` balanced groups with the
        fewest nets crossing between them (min-cut, Kernighan-Lin style),
        for PCB rooms or schematic sheets. Pure data; touches nothing.
        Assign the groups to part ``sheet`` or ``zone`` yourself.

        A hub-centric design (one IC wired to everything) does not split
        cleanly; check ``cut_nets`` and ``group_sizes`` before acting.

        Args:
            plan_json: A DesignPlan (dict or JSON string).
            n_groups: Number of groups.
            max_fanout: Nets touching more than this many parts are
                treated as rails and ignored.

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
    async def design_plan_from_sheet(
        project_path: str,
        sheet_document: str = "",
    ) -> dict[str, Any]:
        """Read a DesignPlan back off a schematic so an edit becomes a
        plan edit. Parts come from the components, nets from the compiled
        netlist, rails from the power ports; the plan is validated.

        A schematic does not record, so you supply:

        - ``role`` on each part (decoupling, crystal, op-amp motif...).
        - ``zone`` on each part, for functional blocks.
        - Single-pin nets, which a plan cannot express; they come back
          in ``dropped_pins``.

        Edit the plan, then redraw the block on a fresh sheet with
        ``sch_preview_template`` and ``sch_draw_template`` (plan_json).

        Args:
            project_path: The .PrjPcb the sheet belongs to. Its compiled
                netlist is what supplies the nets.
            sheet_document: Full path to the .SchDoc to read. Defaults to
                the active document.

        Returns:
            ``ok`` / ``plan`` (a DesignPlan dict, or None on a refusal) /
            ``parts_read`` / ``nets_read`` / ``dropped_pins`` / ``notes``,
            and ``complete``: false when anything on the sheet is not in
            the plan. ``interfaces_not_in_plan`` lists each lost
            connection as ``{refdes, pin, net, why, is_rail}`` and
            ``skipped_components`` the parts left out. A redraw from an
            incomplete plan drops them: restore each one and compare the
            netlists before keeping it.
        """
        return plan_from_live_sheet(project_path, sheet_document)

    @mcp.tool()
    async def design_audit_schematic(
        project_path: Optional[str] = None,
        cluster_radius_mils: int = 600,
    ) -> dict[str, Any]:
        """Structured visual/layout audit of the active schematic.

        Call AFTER ``sch_draw_template`` and BEFORE ``design_validate``
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
        """Validate a DesignRequirement before part selection and planning
        (stage 1 of the autonomous flow). Pure Python, no Altium.

        A fact the requirement does not state goes into ``open_questions``
        for the user, never guessed; validation fails while any remain.
        Also fails on: no outputs, no power source, inverted temperature or
        IO-voltage ranges, comms IO without a protocol, and a rail or power
        output larger than every power input (a boost or inverting stage
        the user must confirm).

        ``board_intent`` (stackup, impedance, peak currents, edges,
        enclosure, assembly, finish, test) is optional; when one matters
        and is missing, ``notes`` suggests a question.

        Args:
            requirement: DesignRequirement as a JSON object/dict. Units are
                SI with the unit in the field name (``voltage_v``,
                ``current_a``, ``temp_min_c``); mechanical dimensions are
                MILLIMETRES (``board_size_mm``, ``height_max_mm``).

        Returns:
            ``{"ok": True, "issues": [], "notes": [...], "summary": "..."}``
            when the requirement is planning-ready (``summary`` is the
            labelled-line block to embed in ``DesignPlan.summary``), or
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
    async def design_list_fab_vendors() -> dict[str, Any]:
        """Fab houses whose published capabilities ship with this server.

        Each has its capability page, the day it was read, whether that
        is over a year ago (``stale``), its tiers and how many stack-ups
        it has. Nothing here is recalled: every figure was read off the
        fab's page and carries the fab's own words.

        Returns:
            ``{"ok": True, "vendors": [...], "laminates": [...]}``; each
            laminate with its design Dk.
        """
        return {"ok": True, "vendors": fab_library.list_vendors(),
                "laminates": fab_library.list_laminates()}

    @mcp.tool()
    async def design_fab_vendor_profile(
        vendor: str,
        copper_layers: int = 0,
        outer_oz: float = 1.0,
        tier: str = "standard",
        fill: Optional[dict] = None,
    ) -> dict[str, Any]:
        """A fab profile for one build at a fab, ready for
        ``design_synthesize_rules``, with every value's source.

        Minimums depend on the build: JLCPCB's track is 4 mil on two
        layers, 3.5 mil on four, and grows with copper weight. The
        profile is built for the layer count and outer copper given.
        A figure the fab does not publish is never filled in here: the
        profile is refused, naming each one, until ``fill`` supplies it
        (recorded as yours, not the fab's). PCBWay's standard table, for
        one, gives no hole-to-hole or mask bridge; its ``advanced_normal``
        tier does.

        With ``copper_layers`` 0, returns the vendor's raw figures with
        their quotes instead.

        Args:
            vendor: a fab from design_list_fab_vendors ("JLCPCB",
                "Eurocircuits", "Sierra Circuits", ...)
            copper_layers: copper layer count of the board
            outer_oz: outer copper weight, oz
            tier: the vendor's tier, "standard" by default
            fill: {FabProfile field: mils} for figures the fab does not
                publish, e.g. {"min_hole_to_hole_mils": 10}

        Returns:
            ``{"ok": True, "profile": {...}, "provenance": {field:
            {quote, ...}}, "age": {...}}`` or ``{"ok": False, "missing":
            [{field, reason}], ...}``.
        """
        if not copper_layers:
            return fab_library.vendor(vendor)
        got = fab_library.fab_profile(vendor, copper_layers=copper_layers,
                                      outer_oz=outer_oz, tier=tier, fill=fill)
        if got.get("ok"):
            got["profile"] = got["profile"].model_dump()
        return got

    @mcp.tool()
    async def design_list_stackups(
        vendor: str = "", copper_layers: int = 0,
    ) -> dict[str, Any]:
        """The fabs' own stack-ups that ship with this server.

        JLCPCB's are parsed ply by ply from its impedance page (36 at
        1.6 mm, 4 and 6 layers), so every height and Dk is JLCPCB's.

        Args:
            vendor: limit to one fab ("" for all)
            copper_layers: limit to one copper count (0 for all)

        Returns:
            ``{"ok": True, "stackups": [{name, vendor, copper_layers,
            total_mm, dielectrics}]}``.
        """
        return {"ok": True, "stackups": fab_library.stackup_templates(
            vendor or None, copper_layers or None)}

    @mcp.tool()
    async def design_get_stackup(
        name: str,
        impedance_targets: Optional[list] = None,
        board_stackup: Optional[dict] = None,
    ) -> dict[str, Any]:
        """One fab stack-up ply by ply, with optional trace widths for
        target impedances and an optional comparison against a board.

        Widths: outer layers as microstrip over the dielectric to the next
        copper; an inner layer only when it sits near the middle of its
        two planes (the closed form is symmetric; otherwise it is reported
        and not sized). Closed forms are +/-10 %, so these are starting
        widths for the fab's solver to confirm.

        Args:
            name: stack-up name, e.g. "JLC04161H-7628"
            impedance_targets: e.g. [{"ohms": 50}, {"ohms": 90,
                "spacing_mils": 6}] (spacing makes it differential)
            board_stackup: a ``pcb_get_layer_stackup`` result to compare
                with; the template's plies are first collapsed to one
                dielectric per copper gap, as Altium keeps them

        Returns:
            ``{"ok": True, "stackup": {...}, "plies": [...], "source":
            {...}, "impedance": {...}?, "comparison": {...}?}``.
        """
        got = fab_library.stackup_template(name)
        if not got["ok"]:
            return got
        got["stackup"] = got["stackup"].model_dump()
        if impedance_targets:
            got["impedance"] = fab_library.impedance_widths(name, impedance_targets)
        if board_stackup:
            got["comparison"] = fab_library.compare_stackup(board_stackup, name)
        return got

    @mcp.tool()
    async def design_get_laminate(
        name: str, frequency_ghz: float = 0.0,
    ) -> dict[str, Any]:
        """A laminate's datasheet figures and the Dk to design with.

        Makers publish different kinds of Dk: Rogers a process value (a QC
        test) AND a design value 5 % higher, Isola and Panasonic typical
        values by frequency. Each figure here says which it is, with its
        frequency, method and datasheet. The returned design Dk is the
        design value when there is one, else the typical value nearest
        ``frequency_ghz``; never a process value.

        Args:
            name: e.g. "RO4350B", "370HR", "MEGTRON 6" (prefix matches)
            frequency_ghz: the frequency to pick a typical Dk at (0: the
                highest listed)

        Returns:
            The laminate record plus the design Dk it chose and why.
        """
        got = fab_library.laminate(name)
        if got["ok"]:
            got["design_dk"] = fab_library.design_dk(name, frequency_ghz or None)
        return got

    @mcp.tool()
    async def design_synthesize_rules(
        profile: dict,
        plan_json: Union[str, dict] = "",
        net_class_map: Optional[dict] = None,
        options: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Synthesize PCB design rules + stackup ops from a fab profile.

        Returns ``pcb_create_design_rule`` and ``pcb_modify_layer``
        parameter dicts to send as they are, after
        ``pcb_create_net_class`` creates the classes they name. Every
        number comes from a profile field or a calculator (IPC-2221
        width, IPC-2141 impedance); a rule missing an input is skipped
        with a note. Pure Python, no Altium.

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

        Splits the parts by signal connectivity (min-cut, zones kept
        whole), names each sheet from its main zone, makes ports from the
        signal nets the cut severs (rails stay power ports), and returns
        top-sheet ops shaped for ``sch_place_sheet_symbol``,
        ``sch_place_sheet_entry`` and ``sch_generate_toc``. Apply with
        ``design_apply_hierarchy`` before drawing the sheets. Pure
        Python, deterministic.

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
        draw each sheet with ``sch_draw_template``; emit the hierarchy's
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

    # --- stages to production: feasibility, prototype, EVT, DVT, PVT -------

    def _ledger(product: str):
        from ..config import get_config
        from ..design.phase_gates import Ledger, ledger_name
        return Ledger(get_config().workspace_dir / "phase_gates" / ledger_name(product))

    def _line_json(line, with_reason: bool) -> dict[str, Any]:
        check = line.check
        out: dict[str, Any] = {"key": check.key, "asks": check.asks,
                               "outcome": line.outcome, "required": check.required,
                               "done_when": check.done_when,
                               "answered_by": "tools" if check.tools else "people"}
        if check.tools:
            from .folds import call_form
            out["tools"] = list(dict.fromkeys(call_form(n) for n in check.tools))
        if with_reason:
            out["why_now"] = check.reason
        if line.answer is not None:
            a = line.answer
            out["answer"] = {"outcome": a.outcome, "source": a.source, "detail": a.detail,
                             "revision": a.revision,
                             "when": a.when.isoformat(timespec="seconds")}
        if line.ignored_because:
            out["ignored_because"] = line.ignored_because
        return out

    @mcp.tool()
    async def design_phase_gate(stage: str, revision: str = "", product: str = "default",
                                with_reasons: bool = True) -> dict[str, Any]:
        """The checks that close one stage on the way to production, and how
        the product stands against them.

        The stages in order: feasibility, prototype, evt, dvt, pvt. Each check
        says what it asks, why at this stage, what counts as done, which tools
        here answer it, and its outcome from the answers recorded so far. It
        runs nothing: call ``tools_to_run``, then record each result with
        design_phase_answer. ``for_people`` are the holding checks no tool
        can answer.

        Args:
            stage: feasibility, prototype, evt, dvt or pvt (also "design
                validation" and similar).
            revision: The board revision in question; answers given for
                another revision stop counting, and the check says so.
            product: Whose ledger, when there are several products.
            with_reasons: Include each check's ``why_now``.

        Returns:
            ``cleared``, ``tally``, ``checks``, ``holding``, ``tools_to_run``
            and ``for_people``.
        """
        from ..design.phase_gates import TITLES, review, stage_from
        try:
            name = stage_from(stage)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        result = review(name, _ledger(product).answers(), revision)
        return {"stage": name, "title": TITLES[name], "product": product,
                "revision": revision, "cleared": result.cleared, "tally": result.tally(),
                "checks": [_line_json(line, with_reasons) for line in result.lines],
                "holding": [line.check.key for line in result.holding()],
                "tools_to_run": result.tools_to_run(),
                "for_people": result.for_people()}

    @mcp.tool()
    async def design_phase_answer(key: str, outcome: str, detail: str = "",
                                  source: str = "", revision: str = "",
                                  product: str = "default") -> dict[str, Any]:
        """Record the answer to one stage check in the product's ledger.

        The ledger is only appended to: a new answer replaces the old one in
        reviews and both stay on file. A waiver needs a ``detail``; months
        later an unexplained waiver cannot be told from a skipped check.

        Args:
            key: The check's key from design_phase_gate, e.g. "prototype:drc".
            outcome: met, unmet, waived or open.
            detail: The evidence: a count, a measurement, a document.
            source: The tool that answered, or the person who confirmed it.
            revision: The board revision it applies to.
            product: Whose ledger.

        Returns:
            The check's stage reviewed again: ``stage_cleared``, ``tally``
            and ``holding``.
        """
        from ..design.phase_gates import CHECKS, review
        ledger = _ledger(product)
        try:
            ledger.add(key, outcome, source=source, detail=detail, revision=revision)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        stage = CHECKS[key].stage
        result = review(stage, ledger.answers(), revision)
        return {"ok": True, "key": key, "outcome": outcome, "stage": stage,
                "stage_cleared": result.cleared, "tally": result.tally(),
                "holding": [line.check.key for line in result.holding()]}

    @mcp.tool()
    async def design_phase_standing(revision: str = "",
                                    product: str = "default") -> dict[str, Any]:
        """Which stage the product is in, and what that stage still needs.

        A product is in the FIRST stage that does not clear, however much
        work exists beyond it: a board whose feasibility checks never closed
        is in feasibility.

        Returns:
            ``stage``, ``title``, ``in_production`` (every stage cleared),
            that stage's ``holding`` and ``tools_to_run``, and ``stages``
            (each one's ``cleared``, ``tally`` and ``holding``).
        """
        from ..design.phase_gates import DONE, TITLES, standing
        stage, reviews = standing(_ledger(product).answers(), revision)
        current = next((r for r in reviews if r.stage == stage), None)
        return {"product": product, "revision": revision, "stage": stage,
                "title": TITLES[stage], "in_production": stage == DONE,
                "holding": [line.check.key for line in current.holding()] if current else [],
                "tools_to_run": current.tools_to_run() if current else [],
                "stages": [{"stage": r.stage, "cleared": r.cleared, "tally": r.tally(),
                            "holding": [line.check.key for line in r.holding()]}
                           for r in reviews]}
