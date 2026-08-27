# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design-review orchestration tools.

One call to ``design_review_snapshot`` bundles 8-12 separate reads
(project info, components, nets, rules, DRC messages, unrouted nets,
sch/PCB diff, BOM, ...) into a single tool response. The response
also carries a ``_review_guidance`` block that holds the agent to
two disciplines, so every review it produces follows them instead of
relying on whatever the model happens to remember:

  - datasheet discipline: every device-function claim is grounded in
    the manufacturer datasheet, never the symbol or library metadata;
  - review-quality discipline: a finding must add analysis the raw
    tools do not -- specific, evidenced, actionable -- and is never a
    relayed ERC/DRC message nor a 'verify X' prompt handed back to
    the user.
"""

from __future__ import annotations

from typing import Any, Optional

from ..bridge import get_bridge
from .datasheet_hints import (
    DATASHEET_RULES,
    build_guidance_block,
    extract_unique_parts,
)


REVIEW_SECTIONS: dict[str, tuple[str, dict[str, Any], float]] = {
    # section_name: (command, params, timeout_seconds)
    "project_info":  ("project.get_focused",         {},  10.0),
    "project_options": ("project.get_project_options", {}, 10.0),
    "design_stats":  ("project.get_design_stats",    {},  20.0),
    "components":    ("pcb.get_components",          {},  20.0),
    "nets":          ("pcb.get_nets",                {},  10.0),
    "design_rules":  ("pcb.get_design_rules",        {},  10.0),
    "unrouted":      ("pcb.get_unrouted_nets",       {},  30.0),
    "diff":          ("project.get_design_differences", {}, 30.0),
    "messages":      ("project.get_messages",        {},  10.0),
    "board_stats":   ("pcb.get_board_statistics",    {},  10.0),
    "fab_stats":     ("pcb.get_fab_stats",           {},  20.0),
    # Slow / optional, only run on explicit request.
    "bom":           ("project.get_bom",             {},  60.0),
    "drc":           ("pcb.run_drc",                 {},  90.0),
    "erc":           ("generic.run_erc",             {},  90.0),
    "unconnected_pins": ("generic.get_unconnected_pins", {}, 60.0),
}

DEFAULT_SECTIONS = [
    "project_info",
    "design_stats",
    "components",
    "nets",
    "design_rules",
    "unrouted",
    "diff",
    "messages",
    "board_stats",
    "fab_stats",
]


# Per-audit severity classification used by design_lint_report and by the
# dashboard's /api/lint endpoint. Calibrated by real impact, not by how many
# items the check tends to fire on. Three buckets:
#
#   critical -- broken state escaping to fab / will-not-work bug.
#               The agent should treat ANY violation as a top
#               finding in its review.
#   warning  -- high risk of malfunction or fab feedback. Worth
#               investigating each instance.
#   info     -- style / curation / library hygiene. Surface if
#               there's a pattern, but don't gate release on
#               isolated instances.
#
# Module-scope so dashboard.py can import without circular-import or
# scope-mismatch surprises.
LINT_SEVERITY: dict[str, str] = {
    # ---- critical: phantom nets, escaped placeholders, things that
    # break the board or block fab ----
    "find_designator_collisions":        "critical",
    "find_orphan_net_labels":            "critical",
    "find_net_label_conflicts":          "critical",
    "find_orphan_power_objects":         "critical",
    "find_placeholder_values":           "critical",
    "find_invalid_regions":              "critical",
    "find_mirrored_pcb_text":            "critical",
    "find_components_outside_board_outline": "critical",
    "find_non_embedded_images":          "critical",
    # ---- warning: real risk, but recoverable ----
    "find_floating_ports":               "warning",
    "find_unmatched_ports":              "warning",
    "find_signal_vias_without_return":   "warning",
    "find_via_antennas":                 "warning",
    "find_bad_connections":              "warning",
    "find_acute_angles":                 "warning",
    "find_inconsistent_track_widths":    "warning",
    "find_pads_near_board_edge":         "warning",
    "find_removed_pad_shapes":           "warning",
    "find_pads_center_not_connected":    "warning",
    "variant_not_fitted":                "warning",
    "find_unlocked_component_primitives":"warning",
    "power_port_orientation":            "warning",
    "tented_via_ratio":                  "warning",
    "find_single_pin_nets":              "warning",
    "find_missing_decoupling":           "warning",
    "find_pin_net_name_mismatches":      "warning",
    "find_unconnected_ic_pins":          "warning",
    # ---- info: style / curation ----
    "component_param_visibility":        "info",
    "find_off_grid_components":          "info",
    "find_missing_datasheets":           "info",
    "find_mpn_inconsistencies":          "info",
    "find_visible_supplier_pn":          "info",
    "find_mixed_designator_rotation":    "info",
}


# Ordered list of Pascal-side audits the design-lint sweep runs. Both
# the MCP design_lint_report tool AND the dashboard /api/lint endpoint
# iterate this same list -- so adding a new audit means appending here
# once, instead of fixing two parallel call lists. BOM-side audits (which
# share a single BOM fetch) are NOT in this list; they run as a separate
# block in each consumer because the BOM-fetch + dispatch shape differs.
#
# Each entry: (section_name, mcp_command). For audits that take params
# (currently only find_bad_connections), the caller threads them in.
LINT_AUDIT_LIST: list[tuple[str, str]] = [
    # --- schematic ---
    ("component_param_visibility",      "audit.validate_component_params"),
    ("power_port_orientation",          "audit.power_port_orientation"),
    ("find_floating_ports",             "audit.find_floating_ports"),
    ("find_unmatched_ports",            "audit.find_unmatched_ports"),
    ("find_designator_collisions",      "audit.find_designator_collisions"),
    ("find_missing_datasheets",         "audit.find_missing_datasheets"),
    ("find_mpn_inconsistencies",        "audit.find_mpn_inconsistencies"),
    ("find_single_pin_nets",            "audit.find_single_pin_nets"),
    ("find_non_embedded_images",        "audit.find_non_embedded_images"),
    ("find_visible_supplier_pn",        "audit.find_visible_supplier_pn"),
    ("find_orphan_net_labels",          "audit.find_orphan_net_labels"),
    ("find_net_label_conflicts",        "audit.find_net_label_conflicts"),
    ("find_orphan_power_objects",       "audit.find_orphan_power_objects"),
    ("find_placeholder_values",         "audit.find_placeholder_values"),
    # --- pcb ---
    ("find_off_grid_components",        "audit.find_off_grid_components"),
    ("find_components_outside_board_outline",
                                        "audit.find_components_outside_board_outline"),
    ("find_pads_near_board_edge",       "audit.find_pads_near_board_edge"),
    ("variant_not_fitted",              "audit.variant_not_fitted"),
    ("tented_via_ratio",                "audit.tented_via_ratio"),
    ("find_bad_connections",            "audit.find_bad_connections"),
    ("find_signal_vias_without_return", "audit.find_signal_vias_without_return"),
    ("find_via_antennas",               "audit.find_via_antennas"),
    ("find_removed_pad_shapes",         "audit.find_removed_pad_shapes"),
    ("find_pads_center_not_connected",  "audit.find_pads_center_not_connected"),
    ("find_invalid_regions",            "audit.find_invalid_regions"),
    ("find_mixed_designator_rotation",  "audit.find_mixed_designator_rotation"),
    ("find_unlocked_component_primitives",
                                        "audit.find_unlocked_component_primitives"),
    ("find_mirrored_pcb_text",          "audit.find_mirrored_pcb_text"),
    ("find_acute_angles",               "audit.find_acute_angles"),
    ("find_inconsistent_track_widths",  "audit.find_inconsistent_track_widths"),
]


# ---------------------------------------------------------------------------
# Review-quality discipline.
#
# Datasheet discipline (datasheet_hints.DATASHEET_RULES) keeps device
# *facts* honest. This second discipline keeps the *findings* honest --
# it governs what may be surfaced as a review finding at all. It ships
# inside the _review_guidance block of every design_review_snapshot so
# the standard is enforced by the agent, not left to the model's recall.
#
# Each rule below is here because skipping it produced a useless review:
# relayed ERC categories the user already has in Altium's Messages panel;
# "verify X" prompts that hand the analysis back to the user; device
# functions guessed from symbol pin names; and normal design practice
# surfaced as noise.
# ---------------------------------------------------------------------------

REVIEW_DISCIPLINE: str = (
    "A finding must add analysis the raw tools do not: name the exact "
    "parts, pins and nets, back it with a netlist fact or a cited "
    "datasheet section, and state a concrete consequence and fix. Run "
    "ERC and DRC, but investigate every violation and surface only "
    "the verified-real ones as specific findings -- never relay the "
    "raw list. Never emit 'verify X' as a finding -- do the analysis. "
    "Normal design practice is not a finding."
)

REVIEW_QUALITY_RULES: list[str] = [
    "RUN ERC AND DRC, THEN TRIAGE -- never relay them, never drop "
    "them. ERC and DRC must still be run: a genuine violation can be "
    "one of the most important findings in the whole review, so a "
    "real one is never discarded. But the raw output is not the "
    "review, and much of it is false positives -- ERC routinely "
    "flags power and passive nets, unset pin electrical types, and "
    "intentional no-connects. After the datasheet-grounded part "
    "review, investigate EACH ERC/DRC violation one by one: trace it "
    "to the exact net and pins and use the datasheet understanding "
    "of the parts on that net to decide whether it is a real fault "
    "or a false positive. Surface every violation verified as real "
    "-- each as a specific finding (named net and pins, the actual "
    "electrical fault, the fix), severity-rated on its real impact. "
    "Drop the false positives. Never relay a raw category like 'N "
    "nets with floating inputs' -- the user already has Altium's "
    "Messages panel for that, and a relayed copy is worthless. The "
    "triage exists to cut false-positive noise so the genuine ERC "
    "and DRC issues stand out, not to dismiss ERC/DRC.",

    "NO HOMEWORK. Never emit 'verify X', 'confirm X' or 'check X "
    "against the datasheet' as a finding -- that hands the analysis "
    "back to the user, which is the opposite of a review. Fetch the "
    "datasheet, trace the net, and state the conclusion yourself. "
    "'Verify the decoupling' is not a finding; 'U3 pin 12 VDDA is on "
    "net +3V3 with no capacitor on that net' is.",

    "SPECIFIC, EVIDENCED, ACTIONABLE. Every finding names the exact "
    "components, pins and nets involved; is backed by either a "
    "netlist fact (stated as a netlist fact) or a datasheet section "
    "or page citation; and states the concrete electrical "
    "consequence and the concrete fix. A finding with no named parts "
    "and no citation is not a finding.",

    "TOPOLOGY vs FUNCTION. A netlist fact -- a single-pin net, a pin "
    "count, two pins shorted onto one net -- is directly observable "
    "and may be stated plainly. A device function -- what a pin "
    "does, a rating, a threshold, a default state -- is a datasheet "
    "fact and may NEVER be inferred from a symbol pin name, the "
    "Comment field, or the part number. A pin the symbol labels "
    "'OUT' is not known to be an output until the datasheet says so.",

    "NOT EVERYTHING IS A FINDING. Normal, expected design practice "
    "is noise: unused MCU GPIOs left unrouted, an unused logic "
    "output left open, declared no-connect pins. A review that is "
    "mostly noise is useless -- signal-to-noise is the metric. If "
    "you would not raise it with a senior engineer in a real design "
    "review, do not surface it.",

    "CALIBRATE SEVERITY. 'critical' and 'warning' are for real, "
    "evidenced problems that can cause a malfunction or a respin. "
    "Something you could not confirm is not 'critical'. Verified-OK "
    "and positive observations are 'info'. Do not inflate counts.",
]


def _extract_unique_parts(
    components: Any, bom: Any
) -> list[dict[str, str]]:
    """Thin wrapper around the shared extractor, kept for test compat."""
    return extract_unique_parts(components=components, bom=bom)


def _guidance_block(unique_parts: list[dict[str, str]]) -> dict[str, Any]:
    """Build the ``_review_guidance`` block.

    It carries both disciplines: the shared datasheet discipline (so
    device facts are grounded in the datasheet) and the review-quality
    discipline (so what gets surfaced as a finding is specific,
    evidenced, and adds analysis the raw sections do not).
    """
    block = build_guidance_block(unique_parts, context="design_review")
    block["__REVIEW_DISCIPLINE__"] = REVIEW_DISCIPLINE
    block["review_quality_rules"] = REVIEW_QUALITY_RULES
    return block


def register_review_tools(mcp):
    """Register design-review orchestration tools."""

    @mcp.tool()
    async def design_review_snapshot(
        sections: list[str] | None = None,
        include_bom: bool = True,
        run_drc: bool = False,
        run_erc: bool = False,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Fetch a comprehensive design-review snapshot in ONE tool call.

        THIS -- the compiled netlist + part data it returns -- is the
        BASIS of a design review, not a render. Judge connectivity, part
        values, ratings, and pin functions from these sections (and the
        cited datasheets), never from `sch_render_svg` / `pcb_render_svg`
        / `design_visual_review`, which are geometry-only and exist for
        when an image is genuinely needed (layout/placement). A picture
        can show a wire that shares no net, or hide a net that is
        electrically correct -- so the review reads the data here.

        PREFER THIS over running 8-12 individual review queries.
        A normal review (components, nets, rules, diff, messages, stats,
        unrouted, BOM) is one round-trip instead of one LLM turn per
        section. That's the single biggest time cost on a full review.

        CRITICAL, datasheet discipline (enforced via _review_guidance):
        Before drawing ANY conclusion about a component's pin
        function, voltage rating, or electrical spec, fetch the
        actual manufacturer datasheet and verify. The schematic
        symbol, footprint, and parameter fields are NOT ground
        truth - they are often wrong or outdated. Every proposed
        fix must cite the datasheet section or page you relied on.

        The response's ``_unique_parts`` field is the checklist of
        components whose datasheets you must have read before
        reviewing. Do not skip this step.

        CRITICAL, review-quality discipline (enforced via _review_guidance):
        A finding must ADD analysis the raw sections do not. Run ERC
        and DRC, but do NOT relay their raw output as findings -
        investigate each violation after the datasheet review, drop
        the false positives, and surface the real ones as specific,
        traced findings (a genuine ERC/DRC violation can be a top
        finding - never dropped). Do NOT emit "verify X" / "confirm
        X" as a finding - that hands the analysis back to the user.
        Every finding names the exact parts/pins/nets, is backed by a
        netlist fact or a cited datasheet, states a concrete
        consequence and fix, and is something you would raise in a
        real design review. The full rules are in
        _review_guidance["review_quality_rules"].

        Args:
            sections: Which snapshot sections to include. Defaults to
                the standard review set (project_info, design_stats,
                components, nets, design_rules, unrouted, diff,
                messages, board_stats, fab_stats). Available extras: "bom",
                "drc", "erc", "unconnected_pins", "project_options".
            include_bom: Convenience - adds "bom" to sections if True
                (default). BOM is the best source of manufacturer
                part numbers for datasheet lookup.
            run_drc: If True, runs DRC (slow, 30-90 s) and includes
                results. Off by default - assume the user already
                ran it, or run it separately when asked.
            run_erc: If True, runs ERC and includes results. Off by
                default.
            force_recompile: SaveAll + invalidate SmartCompile cache
                + recompile before gathering any sections. Use this
                when the user has been editing schematics in the
                Altium UI and you need a guaranteed-fresh netlist.
                Costs one extra ~5-10 s compile up-front.

        Returns:
            Dict with one key per requested section, plus:
              - _review_guidance: datasheet + review-quality rules
                Claude must follow when turning sections into findings
              - _unique_parts: list of {manufacturer, part_number,
                designators} to fetch datasheets for
              - _sections_failed: sections that errored (partial results
                are still usable)
        """
        bridge = get_bridge()

        # Force a fresh compile up-front if requested. Subsequent
        # SmartCompile calls inside each section will hit the newly
        # refreshed cache.
        recompile_error = ""
        if force_recompile:
            try:
                await bridge.send_command_async(
                    "project.force_recompile", {}, timeout=120.0
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                # Non-fatal: the sections still run against whatever
                # compile state is current. But the caller ASKED for
                # fresh connectivity, and a review drawn from stale
                # netlist data while believing it is fresh is the same
                # defect this release fixed in the force_recompile flag
                # itself. Report it instead of swallowing it.
                recompile_error = str(exc)

        requested = list(sections) if sections else list(DEFAULT_SECTIONS)
        if include_bom and "bom" not in requested:
            requested.append("bom")
        if run_drc and "drc" not in requested:
            requested.append("drc")
        if run_erc and "erc" not in requested:
            requested.append("erc")

        # Deduplicate while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for s in requested:
            if s in seen:
                continue
            seen.add(s)
            ordered.append(s)

        result: dict[str, Any] = {}
        failed: list[dict[str, str]] = []

        for section in ordered:
            spec = REVIEW_SECTIONS.get(section)
            if spec is None:
                failed.append({
                    "section": section,
                    "error": f"Unknown section '{section}'",
                })
                continue
            command, params, timeout = spec
            try:
                result[section] = await bridge.send_command_async(
                    command, params, timeout=timeout
                )
            except Exception as exc:
                failed.append({
                    "section": section,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        unique_parts = _extract_unique_parts(
            result.get("components"), result.get("bom")
        )

        result["_unique_parts"] = unique_parts
        result["_review_guidance"] = _guidance_block(unique_parts)
        if failed:
            result["_sections_failed"] = failed
        if recompile_error:
            result["_recompile_failed"] = recompile_error
            result["_recompile_note"] = (
                "force_recompile was requested but did not run, so these "
                "sections reflect whatever compile state was already "
                "current. Connectivity may be stale."
            )
        result["_sections_fetched"] = [
            s for s in ordered if s in result and not s.startswith("_")
        ]

        # A review that fetched NOTHING must say so. _sections_fetched
        # being empty is already honest, but it is an absence, and an
        # absence is the easiest thing for a reader to skim past: the
        # equivalent aggregator on the EasyEDA side reported a board as
        # having no violations when not one audit had been able to read
        # it. Stated here rather than left to inference.
        #
        # Deliberately NOT adding an `ok` key. Whether this tool should
        # carry the ok/reason envelope is the open question in the
        # failure-shape task, and answering it here by hand would settle
        # a published contract as a side effect of a different fix.
        if not result["_sections_fetched"]:
            result["_nothing_fetched"] = (
                "not one section could be fetched, so this is not a "
                "clean or empty design: nothing was read. See "
                "_sections_failed for why."
            )

        return result

    @mcp.tool()
    async def design_lint_report(
        run_drc: bool = False,
        bad_connection_tolerance_mils: float = 1.0,
        checks: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Run all design-lint checks and return a consolidated violation
        report.

        Sequences the individual ``audit.*`` checks the way a pre-release
        review would: compile-gate first, then schematic-side rules, then
        PCB-side rules, then optional ERC/DRC. Each section's raw result
        is preserved so an agent can drill in; a top-level ``summary``
        gives counts per check.

        Returns structured JSON instead of dumping to the Messages panel
        (Altium's panel is fine for human review but the agent needs a
        machine-readable shape).

        Checks executed:
          - ``component_param_visibility`` -- per-class parameter
            visibility (cap voltage, IC MPN, etc).
          - ``power_port_orientation`` -- ground down / rails up.
          - ``find_floating_ports`` -- SCH ports not connected to a pin
            or sheet entry.
          - ``tented_via_ratio`` -- PCB tented vs untented via surfaces.
          - ``find_bad_connections`` -- PCB tracks / arcs with dangling
            endpoints within ``bad_connection_tolerance_mils``.
          - ``find_signal_vias_without_return`` -- PCB signal vias with
            no nearby ground/power via for return-current.
          - ``drc`` (only if ``run_drc=True``) -- Altium's Design Rule
            Check, full project.

        Args:
            run_drc: If True, also runs Altium's DRC and includes its
                violations. Off by default because DRC mutates the .DRC
                report file and can take ~5-10s on a busy board.
            bad_connection_tolerance_mils: Coord tolerance for
                ``find_bad_connections``. Default 1.0; raise to surface
                only gross misalignments.

        Returns:
            Dict with:
              - ``summary``: per-check `{checked, violations}` (best
                effort -- missing/failed checks listed under ``_failed``)
              - ``sections``: per-check raw result objects under their
                section name
              - ``totals``: overall ``violations`` count across all
                sections (does NOT include DRC raw count -- DRC items
                are listed separately because severity / fix-cost
                differs)
        """
        bridge = get_bridge()
        sections: dict[str, Any] = {}
        summary: dict[str, dict[str, int]] = {}
        failed: list[str] = []

        # ``checks`` folds the 31 standalone audit_* tools into this one:
        # pass a subset of section names to run only those. None = run all.
        wanted = set(checks) if checks else None

        def _want(name: str) -> bool:
            return wanted is None or name in wanted

        async def _run(name: str, command: str, params: dict[str, Any] | None = None):
            try:
                data = await bridge.send_command_async(command, params or {})
            except Exception as e:
                failed.append(f"{name}: {e}")
                sections[name] = {"ok": False, "error": str(e)}
                return
            sections[name] = data
            if isinstance(data, dict):
                ch = data.get("checked")
                vi = data.get("violations")
                if isinstance(ch, int) or isinstance(vi, int):
                    summary[name] = {
                        "checked": int(ch) if isinstance(ch, int) else 0,
                        "violations": int(vi) if isinstance(vi, int) else 0,
                        "severity": LINT_SEVERITY.get(name, "info"),
                    }

        # Iterate the shared LINT_AUDIT_LIST -- one source of truth for
        # the audit ordering, shared with dashboard.py /api/lint.
        # find_bad_connections is the only audit that needs a parameter
        # (tolerance_mils); special-case it here.
        for name, command in LINT_AUDIT_LIST:
            if not _want(name):
                continue
            if name == "find_bad_connections":
                await _run(name, command,
                           {"tolerance_mils": str(bad_connection_tolerance_mils)})
            else:
                await _run(name, command)

        # Python-side BOM checks (no Pascal handler: run off the
        # project.get_bom snapshot the bridge already cached). Fetch
        # the BOM ONCE and feed all three helpers from it. Kept separate
        # from LINT_AUDIT_LIST because the call shape differs. Skip the
        # BOM fetch entirely if none of these are requested.
        _BOM_CHECKS = ("find_unconnected_ic_pins", "find_pin_net_name_mismatches",
                       "find_missing_decoupling")
        try:
            from .audit import (
                find_unconnected_ic_pins_from_bom,
                find_pin_net_name_mismatches_from_bom,
                find_missing_decoupling_from_bom,
            )
            # Only pay for the BOM fetch if a BOM-side check is wanted.
            bom = (await bridge.send_command_async(
                "project.get_bom", {"limit": "5000"})
                if any(_want(n) for n in _BOM_CHECKS) else {})
            for name, fn in [
                ("find_unconnected_ic_pins", find_unconnected_ic_pins_from_bom),
                ("find_pin_net_name_mismatches",
                 find_pin_net_name_mismatches_from_bom),
                ("find_missing_decoupling", find_missing_decoupling_from_bom),
            ]:
                if not _want(name):
                    continue
                data = fn(bom or {})
                sections[name] = data
                summary[name] = {
                    "checked": data.get("checked", 0),
                    "violations": data.get("violations", 0),
                    "severity": LINT_SEVERITY.get(name, "info"),
                }
        except Exception as e:
            failed.append(f"bom-side audits: {e}")

        if run_drc:
            await _run("drc", "pcb.run_drc")

        # Severity-bucketed counts -- the agent should prioritize
        # critical → warning → info in its review writeup.
        sev_buckets: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        for s in summary.values():
            sev = s.get("severity", "info")
            sev_buckets[sev] = sev_buckets.get(sev, 0) + s.get("violations", 0)
        totals = {
            "violations": sum(s.get("violations", 0) for s in summary.values()),
            "violations_by_severity": sev_buckets,
            "checks_run": len(summary),
            "checks_failed": len(failed),
        }
        result = {
            "summary": summary,
            "sections": sections,
            "totals": totals,
            "_failed": failed,
            "_hint": (
                "Each section is the same shape the individual audit_* "
                "tool returns. Drill into sections[<name>].items for "
                "specific violations. Re-run with run_drc=True for an "
                "ERC/DRC-inclusive sweep. Pass checks=[...] to run only "
                "named audits (folds the standalone audit_* tools)."
            ),
        }
        if wanted is not None:
            known = {n for n, _ in LINT_AUDIT_LIST} | set(_BOM_CHECKS) | {"drc"}
            unknown = sorted(wanted - known)
            if unknown:
                result["_unknown_checks"] = unknown
        return result

    @mcp.tool()
    async def design_datasheet_checklist() -> dict[str, Any]:
        """Return the datasheet-first discipline checklist for design review.

        Use this when you need the rules without pulling the full
        snapshot. The rules also ship inside the _review_guidance
        block of design_review_snapshot - no need to call this
        separately if you already ran a snapshot.

        Returns:
            Dict with datasheet_rules (list of rules) and a short
            action_required summary.
        """
        return {
            "datasheet_rules": DATASHEET_RULES,
            "action_required": (
                "For every unique manufacturer part number in the design, "
                "fetch the datasheet and verify pin function, voltage "
                "limits, and timing before proposing any fix. Library "
                "metadata is not authoritative."
            ),
        }
