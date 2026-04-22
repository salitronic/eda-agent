# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""SPICE simulation tools.

The simulation flow is three steps:

1. ``sch_get_simulation_readiness()`` audits every component on the
   active schematic and returns three buckets:

     - ``ready``           — has SpicePrefix; nothing to do.
     - ``needs_primitive`` — R/L/C/V/I where just setting the prefix +
                             value makes it simulatable (no model file).
     - ``needs_file``      — an IC or active part that requires a
                             vendor SPICE model (.mdl / .ckt / .lib).

   The response also carries ``_spice_guidance`` with the hard rule:
   **vendor datasheets + vendor-published SPICE models only**. LLM-
   generated SPICE models are not trustworthy and must not be used.

2. ``sch_attach_spice_primitive()`` — for ``needs_primitive`` entries.
   One call per component; no file needed.

3. For ``needs_file`` entries: fetch the vendor model, save it locally,
   call ``sch_attach_spice_model()`` pointing at that file.

Then ``sim_run()`` dispatches Altium's mixed-signal simulator.
"""

from __future__ import annotations

from typing import Any

from ..bridge import get_bridge
from .bulk_hints import BulkHintTracker


SPICE_MODEL_RULES = [
    "SPICE models for ICs and active parts must come from the "
    "manufacturer's own product page or a trusted aggregator "
    "(SnapEDA, Ultra Librarian, Digi-Key model archive). Do NOT "
    "generate a .mdl or .ckt file from datasheet reasoning — the "
    "parameters that matter for convergence and small-signal "
    "behavior are not in any datasheet summary.",
    "Prefer the manufacturer page first. Most vendors host the model "
    "under '<product page> -> Design Resources -> Models' or a linked "
    "ZIP archive. Typical search: "
    "'<manufacturer> <part_number> SPICE model filetype:lib OR "
    "filetype:mdl OR filetype:cir'.",
    "A model file usually contains a .SUBCKT <name> declaration. The "
    "<name> is what goes into sch_attach_spice_model's model_name "
    "parameter — not the file name.",
    "If a vendor SPICE model cannot be located after a real search, "
    "tell the user. Do not fabricate a substitute; running the sim "
    "with a made-up model gives confidently-wrong results.",
    "For passives (R, L, C) and ideal sources (V, I), no file is "
    "needed — sch_attach_spice_primitive sets SpicePrefix + Value "
    "directly.",
]


def _suggest_vendor_search(entry: dict[str, Any]) -> dict[str, Any]:
    """Build a suggested-search hint from a needs_file entry."""
    mfr = str(entry.get("manufacturer") or "").strip()
    part = str(entry.get("manufacturer_part") or "").strip()
    comment = str(entry.get("comment") or "").strip()
    lib_ref = str(entry.get("lib_ref") or "").strip()

    search_part = part or comment or lib_ref
    search_mfr = mfr

    hints: dict[str, Any] = {
        "designator": entry.get("designator"),
        "search_part": search_part,
        "manufacturer": search_mfr,
    }
    if search_part:
        query_terms = [search_mfr, search_part] if search_mfr else [search_part]
        query = " ".join(query_terms).strip()
        hints["web_search_query"] = (
            f"{query} SPICE model filetype:lib OR filetype:mdl "
            f"OR filetype:cir OR filetype:sub"
        )
        hints["datasheet_query"] = f"{query} datasheet filetype:pdf"
    return hints


def _guidance_block(
    needs_file: list[dict[str, Any]],
    needs_primitive: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "spice_model_rules": SPICE_MODEL_RULES,
        "action_required": (
            "For every entry in needs_primitive, call "
            "sch_attach_spice_primitive. For every entry in "
            "needs_file, fetch the vendor SPICE model (WebFetch / "
            "WebSearch using the suggested query below), save it to "
            "a local file, then call sch_attach_spice_model with the "
            ".SUBCKT name from inside the file. Do NOT fabricate a "
            "SPICE model from datasheet reasoning — if the vendor "
            "doesn't publish one, flag it to the user and stop."
        ),
        "search_hints": [
            _suggest_vendor_search(e) for e in needs_file
        ],
        "primitive_hints": [
            {
                "designator": e.get("designator"),
                "suggested_prefix": e.get("suggested_prefix"),
                "suggested_value": e.get("suggested_value"),
            }
            for e in needs_primitive
        ],
    }


def register_sim_tools(mcp):
    """Register SPICE / simulation tools with the MCP server."""

    @mcp.tool()
    async def sch_get_simulation_readiness() -> dict[str, Any]:
        """Audit every component on the active schematic for SPICE readiness.

        Use this as step 1 of any simulation workflow. The response
        classifies each component into one of three buckets:

          - ``ready``           — SpicePrefix already set; nothing to do.
          - ``needs_primitive`` — a passive (R/L/C) or source (V/I/D/Q)
                                  that just needs SpicePrefix + Value.
                                  Call sch_attach_spice_primitive for
                                  each entry.
          - ``needs_file``      — an IC or active part that requires a
                                  manufacturer-supplied .mdl / .ckt /
                                  .lib file. Call sch_attach_spice_model
                                  after fetching the file from the
                                  vendor.

        CRITICAL — vendor SPICE models only:
        Never fabricate a SPICE model from datasheet reasoning. The
        parameters that matter for convergence, bias point, and
        small-signal behavior are not in any datasheet summary; a
        plausible-looking hand-rolled model produces confidently-wrong
        simulation results. The response's ``_spice_guidance`` block
        makes this rule explicit and gives you per-part search queries.

        Returns:
            Dict with:
              - ready, ready_count
              - needs_primitive, needs_primitive_count
                (each entry has designator, comment, suggested_prefix,
                 suggested_value)
              - needs_file, needs_file_count
                (each entry has designator, comment, lib_ref,
                 manufacturer, manufacturer_part)
              - _spice_guidance (rules + structured search_hints +
                primitive_hints)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_simulation_readiness", {}
        )
        if isinstance(result, dict):
            result["_spice_guidance"] = _guidance_block(
                result.get("needs_file") or [],
                result.get("needs_primitive") or [],
            )
        return result

    @mcp.tool()
    async def sch_attach_spice_primitive(
        designator: str,
        primitive: str,
        value: str = "",
        spice_model: str = "",
        sim_kind: str = "",
    ) -> dict[str, Any]:
        """Attach a built-in SPICE primitive to a component.

        For R/L/C/V/I/D/Q parts, Altium's simulator maps these to
        built-in primitives — no model file is needed, just the prefix
        letter and a value string.

        Args:
            designator: Component reference (e.g. "R1", "C3", "Q1").
            primitive: Single letter identifying the primitive:
                R (resistor), L (inductor), C (capacitor),
                V (voltage source), I (current source),
                D (diode), Q (BJT), M (MOSFET), X (subcircuit).
            value: SPICE value string — e.g. "10k" for a resistor,
                "100n" for a cap, "DC 5" or "SIN(0 1 1k)" for a source,
                a model name for a diode/BJT.
            spice_model: Optional explicit model name. For semi parts
                (D, Q, M) this is the model name from a .mdl file.
            sim_kind: Optional SimulationKind tag — "General",
                "Subcircuit", "Model".

        Returns:
            Dict with success, designator, primitive, value.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "designator": designator,
            "primitive": primitive,
        }
        if value:
            params["value"] = value
        if spice_model:
            params["spice_model"] = spice_model
        if sim_kind:
            params["sim_kind"] = sim_kind
        result = await bridge.send_command_async(
            "generic.attach_spice_primitive", params
        )
        hint = BulkHintTracker.record_and_hint("sch_attach_spice_primitive")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def sch_attach_spice_model(
        designator: str,
        file_path: str,
        model_name: str,
        primitive: str = "X",
    ) -> dict[str, Any]:
        """Attach an external SPICE model file (.mdl / .ckt / .lib) to a component.

        Use this after fetching a vendor SPICE model file from the
        manufacturer and saving it locally. The model_name parameter
        must be the exact ``.SUBCKT <name>`` declaration from inside
        the file — not the file name.

        Args:
            designator: Component reference (e.g. "U1").
            file_path: Full path to the locally-saved .mdl / .ckt / .lib.
            model_name: Subcircuit / model name declared inside the
                file. Open the file and look for ``.SUBCKT <name>``
                or ``.MODEL <name>``.
            primitive: SPICE prefix letter. Default "X" (subcircuit).
                Use "D"/"Q"/"M" when the file declares a .MODEL of
                that kind instead of a subcircuit.

        Returns:
            Dict with success, designator, file_path, model_name.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.attach_spice_model",
            {
                "designator": designator,
                "file_path": file_path,
                "model_name": model_name,
                "primitive": primitive,
            },
        )

    @mcp.tool()
    async def sim_run(analysis: str = "") -> dict[str, Any]:
        """Dispatch Altium's mixed-signal simulator on the active project.

        Runs whatever analysis is configured in the project's active
        simulation profile (Simulation Dashboard in Altium). This tool
        just kicks the run — profile setup (analysis type, parameters,
        probe selection) must already be done in the UI, because
        Altium's profile editor isn't exposed via DelphiScript.

        Typical workflow:
            1. Use sch_get_simulation_readiness + sch_attach_* to
               make sure every component has a SPICE model.
            2. Place probes on measurement nodes with sch_place_probe.
            3. In Altium's Simulation Dashboard, pick the analysis
               type and set parameters.
            4. Call sim_run.

        Args:
            analysis: Free-form label echoed back in the response.
                Does not drive Altium — the active profile does.

        Returns:
            Dict with success, analysis, note.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if analysis:
            params["analysis"] = analysis
        return await bridge.send_command_async(
            "generic.run_simulation", params, timeout=120.0
        )
