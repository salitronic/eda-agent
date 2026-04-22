# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Design-review orchestration tools.

One call to ``design_review_snapshot`` bundles 8-12 separate reads
(project info, components, nets, rules, DRC messages, unrouted nets,
sch/PCB diff, BOM, ...) into a single tool response. The response
also carries a ``_review_guidance`` block that enforces
datasheet-first discipline: every review conclusion must be grounded
in the actual component datasheet, not the symbol or library
metadata.
"""

from __future__ import annotations

from typing import Any

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
    # Slow / optional — only run on explicit request.
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
]


def _extract_unique_parts(
    components: Any, bom: Any
) -> list[dict[str, str]]:
    """Thin wrapper around the shared extractor — kept for test compat."""
    return extract_unique_parts(components=components, bom=bom)


def _guidance_block(unique_parts: list[dict[str, str]]) -> dict[str, Any]:
    """Thin wrapper around the shared guidance builder — kept for test compat."""
    return build_guidance_block(unique_parts, context="design_review")


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

        PREFER THIS over running 8-12 individual review queries.
        A normal review (components, nets, rules, diff, messages, stats,
        unrouted, BOM) is one round-trip instead of one LLM turn per
        section. That's the single biggest time cost on a full review.

        CRITICAL — datasheet discipline (enforced via _review_guidance):
        Before drawing ANY conclusion about a component's pin
        function, voltage rating, or electrical spec, fetch the
        actual manufacturer datasheet and verify. The schematic
        symbol, footprint, and parameter fields are NOT ground
        truth - they are often wrong or outdated. Every proposed
        fix must cite the datasheet section or page you relied on.

        The response's ``_unique_parts`` field is the checklist of
        components whose datasheets you must have read before
        reviewing. Do not skip this step.

        Args:
            sections: Which snapshot sections to include. Defaults to
                the standard review set (project_info, design_stats,
                components, nets, design_rules, unrouted, diff,
                messages, board_stats). Available extras: "bom",
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
              - _review_guidance: datasheet-first rules Claude must follow
              - _unique_parts: list of {manufacturer, part_number,
                designators} to fetch datasheets for
              - _sections_failed: sections that errored (partial results
                are still usable)
        """
        bridge = get_bridge()

        # Force a fresh compile up-front if requested. Subsequent
        # SmartCompile calls inside each section will hit the newly
        # refreshed cache.
        if force_recompile:
            try:
                await bridge.send_command_async(
                    "project.force_recompile", {}, timeout=120.0
                )
            except Exception:
                # Non-fatal — individual sections still run; they'll
                # just use whatever compile state is current.
                pass

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
        result["_sections_fetched"] = [
            s for s in ordered if s in result and not s.startswith("_")
        ]

        return result

    @mcp.tool()
    async def datasheet_checklist() -> dict[str, Any]:
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
