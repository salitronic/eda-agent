# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Project management tools for Altium Designer MCP Server."""

from typing import Any, Optional
from ..bridge import get_bridge
from .option_hints import project_option_collision
from .datasheet_hints import tag_response
from .bulk_hints import BulkHintTracker


def register_project_tools(mcp):
    """Register project tools with the MCP server."""

    @mcp.tool()
    async def proj_create(
        project_path: str,
        project_type: str = "PCB",
    ) -> dict[str, Any]:
        """Create a new Altium project.

        Args:
            project_path: Full path for the new project file (.PrjPcb, .PrjLib, etc.)
            project_type: Type of project to create:
                - "PCB": PCB project (.PrjPcb)
                - "IntegratedLibrary": Integrated library project
                - "ScriptProject": Script project

        Returns:
            Dictionary with created project information
        """
        # Ensure the parent directory exists. The Altium side writes the
        # project stub with Rewrite(), which throws EInOutError ("Invalid
        # file name") into a blocking IDE modal when the folder is missing.
        # Creating it here (the server runs on the same host as Altium)
        # keeps that modal from ever firing.
        import os
        parent = os.path.dirname(project_path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.create",
            {"project_path": project_path, "project_type": project_type},
        )
        return result

    @mcp.tool()
    async def proj_open(project_path: str) -> dict[str, Any]:
        """Open an existing Altium project.

        Args:
            project_path: Full path to the project file

        Returns:
            Dictionary with opened project information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.open", {"project_path": project_path}
        )
        return result

    @mcp.tool()
    async def proj_save(project_path: Optional[str] = None) -> dict[str, Any]:
        """Save the current or specified project.

        Args:
            project_path: Optional path to specific project. If None, saves active project.

        Returns:
            Dictionary confirming save operation
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.save", params)
        return result

    @mcp.tool()
    async def proj_close(
        project_path: Optional[str] = None, save: bool = True
    ) -> dict[str, Any]:
        """Close a project.

        Args:
            project_path: Optional path to specific project. If None, closes active project.
            save: Whether to save before closing

        Returns:
            Dictionary confirming close operation
        """
        bridge = get_bridge()
        params = {"save": save}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.close", params)
        return result

    @mcp.tool()
    async def proj_list_documents(
        project_path: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List all documents in a project.

        Args:
            project_path: Optional path to specific project. If None, uses active project.

        Returns:
            List of document information dictionaries containing:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_documents", params)
        return result

    @mcp.tool()
    async def proj_add_document(
        document_path: str, project_path: Optional[str] = None
    ) -> dict[str, Any]:
        """Add an existing document to a project.

        Args:
            document_path: Full path to the document to add
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        params = {"document_path": document_path}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.add_document", params)
        return result

    @mcp.tool()
    async def proj_remove_document(
        document_path: str, project_path: Optional[str] = None
    ) -> dict[str, Any]:
        """Remove a document from a project.

        Note: This only removes the document from the project, it doesn't delete the file.

        Args:
            document_path: Full path to the document to remove
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        params = {"document_path": document_path}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.remove_document", params)
        return result

    @mcp.tool()
    async def proj_get_parameters(
        project_path: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get all parameters defined at the project level.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            List of parameter dictionaries with name and value
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_parameters", params)
        return result

    @mcp.tool()
    async def proj_set_parameter(
        name: str, value: str, project_path: Optional[str] = None
    ) -> dict[str, Any]:
        """Set a project-level parameter.

        Args:
            name: Parameter name
            value: Parameter value
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"name": name, "value": value}
        if project_path:
            params["project_path"] = project_path

        result = await bridge.send_command_async("project.set_parameter", params)
        # A PROJECT PARAMETER IS NOT A PROJECT OPTION, and the names
        # collide. Asking for hierarchy_mode here creates a user
        # parameter of that name and verifies it, correctly, while the
        # actual setting is untouched. Nothing said so, and the reply
        # looked like a success because it was one, of a different thing.
        hint = project_option_collision(name)
        if hint and isinstance(result, dict):
            result["_hint_not_a_project_option"] = hint
        return result

    @mcp.tool()
    async def proj_push_parameters() -> dict[str, Any]:
        """Copy all project-level parameters onto each loaded schematic sheet.

        Every project parameter is written as a document (sheet) parameter on
        each LOADED schematic in the focused project, so title blocks can
        reference project-wide values. Unloaded sheets are skipped -- call
        ``proj_load_sheets`` first to include every sheet.

        Returns:
            Dict with param_count, sheets_updated, sheets_skipped_not_loaded,
            and params_pushed.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "project.push_params_to_sheets", {}
        )

    @mcp.tool()
    async def proj_get_nets(
        component: str = "",
        net_name: str = "",
        project_path: Optional[str] = None,
        limit: int = 500,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Get net-to-pin connectivity from the compiled project netlist.

        CRITICAL for bulk queries, if you need connectivity for MORE
        THAN ONE component or net, do NOT loop this tool. Call it
        ONCE with no filters (`component=""`, `net_name=""`, raise
        `limit` if needed) to pull the entire pin-net table in a
        single round-trip, then slice the result locally. Each
        filtered call is ~700 ms and compiles the project.

        Compiles the project and returns pin-level net assignments.

        Args:
            component: Filter by component designator. Empty = all.
            net_name: Filter by net name. Empty = all.
            project_path: Optional project path. If None, uses active.
            limit: Max pin records (default 500). Raise for big boards.
            force_recompile: Save all dirty docs, invalidate the
                SmartCompile cache, recompile. Costs one extra
                compile (~5-10 s on real designs). Use when you need
                a guaranteed-fresh netlist (e.g., after the user
                edited schematics in the UI). Pair with
                `proj_get_compile_freshness` to confirm no docs are dirty.

        Returns:
            Dict with "pins" and "count".

        Examples:
            # PREFERRED, one unfiltered call, then filter locally:
            all_pins = proj_get_nets(limit=10000)["pins"]
            u1_pins = [p for p in all_pins if p["component"] == "U1"]

            # Guaranteed-fresh read after user edits:
            fresh = proj_get_nets(force_recompile=True, limit=10000)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if component:
            params["component"] = component
        if net_name:
            params["net_name"] = net_name
        if project_path:
            params["project_path"] = project_path
        if force_recompile:
            # Project.pas ForceRecompileIfRequested reads
            # "force_recompile". Sent under the tool's own name this
            # matched nothing, so the flag was dead and a caller who
            # asked for fresh data got the cached compile instead.
            params["force_recompile"] = "true"
        result = await bridge.send_command_async("project.get_nets", params)
        hint = BulkHintTracker.record_and_hint("proj_get_nets")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def proj_export_netlist(
        output_path: Optional[str] = None,
        net_format: str = "pads",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Write the compiled connectivity to a flat netlist file.

        Pulls the full pin-net table once (the same data as get_nets) and
        writes it as a standalone netlist that other tools can import. Two
        formats:

        - "pads": PADS-PCB ASCII, *NET* section with one *SIGNAL* block per
          net listing its component.pin nodes. Widely importable.
        - "tabular": one "component,pin,pin_name,net" CSV row per node, for
          quick diffing or spreadsheet review.

        Single-pin and unnamed nets are included so the file is a faithful
        dump, not a routed-only view.

        Args:
            output_path: Destination file. Defaults to
                workspace/netlist.<ext> if omitted.
            net_format: "pads" (default) or "tabular".
            project_path: Optional project path; uses active if omitted.

        Returns:
            Dictionary with output_path, net_format, net_count, node_count.
        """
        from pathlib import Path
        from ..config import get_config

        fmt = (net_format or "pads").strip().lower()
        if fmt not in ("pads", "tabular"):
            return {"success": False, "error": f"unknown net_format '{net_format}' (use 'pads' or 'tabular')"}

        bridge = get_bridge()
        params: dict[str, Any] = {"limit": "100000"}
        if project_path:
            params["project_path"] = project_path
        data = await bridge.send_command_async("project.get_nets", params)
        pins = data.get("pins", []) if isinstance(data, dict) else []

        nodes: list[tuple[str, str, str, str]] = []
        for p in pins:
            comp = str(p.get("component", "")).strip()
            pin = str(p.get("pin", "")).strip()
            pin_name = str(p.get("pin_name", "")).strip()
            net = str(p.get("net", "")).strip()
            if not comp or not pin or not net:
                continue
            nodes.append((comp, pin, pin_name, net))

        by_net: dict[str, list[tuple[str, str, str, str]]] = {}
        for n in nodes:
            by_net.setdefault(n[3], []).append(n)

        if fmt == "pads":
            lines = ["*PADS-PCB*", "*NET*"]
            for net in sorted(by_net):
                lines.append(f"*SIGNAL* {net}")
                row = "".join(f"{c}.{p} " for c, p, _pn, _net in sorted(by_net[net])).rstrip()
                lines.append(row)
            lines.append("*END*")
            text = "\r\n".join(lines) + "\r\n"
            default_name = "netlist.net"
        else:
            lines = ["component,pin,pin_name,net"]
            for comp, pin, pin_name, net in sorted(nodes):
                lines.append(f"{comp},{pin},{pin_name},{net}")
            text = "\n".join(lines) + "\n"
            default_name = "netlist.csv"

        if output_path:
            out = Path(output_path)
        else:
            out = get_config().workspace_dir / default_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

        return {
            "success": True,
            "output_path": str(out),
            "net_format": fmt,
            "net_count": len(by_net),
            "node_count": len(nodes),
        }

    @mcp.tool()
    async def proj_compile(project_path: Optional[str] = None) -> dict[str, Any]:
        """Compile a project to check for errors.

        This runs the project compilation which validates connectivity
        and checks for design errors.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with compilation results including any errors/warnings
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.compile", params)
        return result

    @mcp.tool()
    async def proj_load_sheets(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Load every schematic sheet of a project into the Altium editor.

        Project-scope operations (query_objects, batch_modify, etc. with
        scope="project") only iterate sheets already resident in SchServer.
        A sheet listed as a project member via get_open_documents may still
        show loaded=false, meaning Altium hasn't opened its editor state.
        Call this tool first to force every sheet to load as a proper
        project member (no free documents).

        This is a no-op for sheets already loaded. Safe to call repeatedly.

        Args:
            project_path: Optional project path. If None, uses focused project.

        Returns:
            Dictionary with:
            - total_sheets: Total SCH sheets in the project
            - loaded: Sheets newly loaded by this call
            - already_loaded: Sheets that were already resident
            - failed: Sheets that could not be opened
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.load_project_sheets", params
        )
        return result

    @mcp.tool()
    async def proj_get_bom(
        project_path: Optional[str] = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Export a full BOM from the compiled project.

        DATASHEET DISCIPLINE: The BOM is the canonical list of
        manufacturer part numbers in this design. The response carries
        `_datasheet_guidance` with per-part search queries. Before
        drawing any conclusion about a listed part, fetch and read
        its datasheet (WebSearch + WebFetch if not already at hand).
        Library metadata here is NOT authoritative.

        Returns every component with designator, comment/value, footprint,
        library reference, and all pin-net connections.

        Args:
            project_path: Optional project path. If None, uses active project.
            limit: Max components to return (default 1000).

        Returns:
            Dictionary with "components" array and "count", plus
            `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_bom", params)
        return tag_response(result, bom=result, context="proj_get_bom")

    @mcp.tool()
    async def proj_get_component_info(
        designator: str,
        project_path: Optional[str] = None,
        with_pin_nets: bool = True,
        with_parameters: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Get full information about a single component.

        IMPORTANT, if you need info for MORE THAN ONE component, use
        `proj_get_component_info_many` (batch). Looping this tool is a
        large wall-time sink, especially with `with_pin_nets=True`
        because each call compiles the project.

        Performance note: pin nets are the only field that needs a
        project compile, and a stale-cache compile on a multi-sheet
        hierarchical project can take 30-60s. When you only need the
        component's value, footprint, library reference, sheet, or
        pin numbers/names (no nets), pass ``with_pin_nets=False`` and
        the call drops to sub-second by skipping ``Project.DM_Compile``
        entirely. Use the default ``with_pin_nets=True`` only when you
        actually need to know what each pin is wired to.

        ``with_parameters=False`` further skips the parameter iterator
        (Manufacturer / MPN / Value / Footprint paths). Marginal win
        compared to skipping the compile, but useful for the cheapest
        possible "is this designator placed?" probe.

        ``timeout`` overrides the bridge's default poll timeout. The
        compile-bound full-fat call can legitimately need 60-90s on
        large projects, raise this when you keep `with_pin_nets=True`.

        DATASHEET DISCIPLINE: Before making any claim about this
        component's pin function, voltage rating, timing, or electrical
        behavior, fetch its datasheet (WebSearch + WebFetch if needed).
        The parameters / comment / library metadata returned here are
        NOT authoritative, only the manufacturer datasheet is.
        `_datasheet_guidance` in the response carries the rule and a
        suggested search query.

        Returns the component's designator, comment, footprint, library
        reference, parameters (when with_parameters=True), and every pin
        with name/number (and net assignment when with_pin_nets=True).

        Args:
            designator: Component designator (e.g., "U1", "R8", "C3")
            project_path: Optional project path. If None, uses active project.
            with_pin_nets: When True (default) compile the project and
                emit the flattened net for each pin. Set to False to
                skip the compile and get a sub-second metadata-only
                response.
            with_parameters: When True (default) include the parameter
                dict. Set to False for the cheapest possible probe.
            timeout: Optional bridge poll-timeout override (seconds).
                Use a higher value (e.g., 90) when keeping with_pin_nets
                True on large hierarchical projects.

        Returns:
            Dictionary with designator, comment, footprint, lib_ref,
            sheet, pins array (each with pin/name and, when requested,
            net), and (when requested) parameters dict, plus
            `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if project_path:
            params["project_path"] = project_path
        if not with_pin_nets:
            params["with_pin_nets"] = "false"
        if not with_parameters:
            params["with_parameters"] = "false"
        result = await bridge.send_command_async(
            "project.get_component_info", params, timeout=timeout,
        )
        hint = BulkHintTracker.record_and_hint("proj_get_component_info")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        if isinstance(result, dict):
            mfr = str(
                result.get("parameters", {}).get("Manufacturer")
                or result.get("parameters", {}).get("manufacturer")
                or ""
            )
            part = str(
                result.get("parameters", {}).get("Manufacturer Part Number")
                or result.get("parameters", {}).get("ManufacturerPartNumber")
                or result.get("parameters", {}).get("PartNumber")
                or result.get("comment")
                or ""
            ).strip()
            parts = [{
                "manufacturer": mfr,
                "part_number": part,
                "designators": str(result.get("designator", "")),
            }] if part else []
            return tag_response(
                result,
                explicit_parts=parts,
                context="proj_get_component_info",
            )
        return result

    @mcp.tool()
    async def proj_get_component_info_many(
        designators: list[str],
        project_path: Optional[str] = None,
        with_pin_nets: bool = True,
        with_parameters: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Full per-component info for MANY designators in ONE round-trip.

        PREFER THIS over looping `proj_get_component_info`. The compile
        (when `with_pin_nets=True`) happens once for the whole batch,
        not once per designator.

        Set ``with_pin_nets=False`` to skip the compile entirely for
        a sub-second metadata-only response (footprint, lib_ref,
        parameters, pin numbers/names, sheet). Set
        ``with_parameters=False`` for the cheapest possible probe.

        DATASHEET DISCIPLINE: parameters / comment / library metadata
        in the response are NOT authoritative. Before reasoning about
        any component's pin function, voltage rating, timing, or
        electrical behavior, fetch its manufacturer datasheet. The
        response carries `_datasheet_guidance` + `_datasheet_parts`.

        Args:
            designators: List of component designators.
            project_path: Optional project path. If None, uses active.
            with_pin_nets: When True (default) compile once and emit
                the flattened net for each pin on every component.
                Set False to skip the compile.
            with_parameters: When True (default) include each
                component's parameter dict.
            timeout: Optional bridge poll-timeout override (seconds).
                Raise this when keeping `with_pin_nets=True` on large
                hierarchical projects.

        Returns:
            Dict with components[], matched, requested, not_found[],
            plus `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        cleaned = [str(d).strip() for d in (designators or []) if str(d).strip()]
        if not cleaned:
            return {"error": "No designators provided", "matched": 0}
        params: dict[str, Any] = {"designators": "~~".join(cleaned)}
        if project_path:
            params["project_path"] = project_path
        if not with_pin_nets:
            params["with_pin_nets"] = "false"
        if not with_parameters:
            params["with_parameters"] = "false"
        result = await bridge.send_command_async(
            "project.get_component_info_batch", params, timeout=timeout,
        )
        if isinstance(result, dict):
            comps = result.get("components") or []
            explicit = []
            seen_keys: set[tuple[str, str]] = set()
            for c in comps:
                if not isinstance(c, dict):
                    continue
                desig = str(c.get("designator") or "").strip()
                params_dict = c.get("parameters") or {}
                mfr = ""
                pn = ""
                if isinstance(params_dict, dict):
                    mfr = str(
                        params_dict.get("Manufacturer")
                        or params_dict.get("manufacturer")
                        or ""
                    )
                    pn = str(
                        params_dict.get("Manufacturer Part Number")
                        or params_dict.get("ManufacturerPartNumber")
                        or params_dict.get("PartNumber")
                        or ""
                    ).strip()
                if not pn:
                    pn = str(c.get("comment") or "").strip()
                if not pn:
                    continue
                key = (pn.lower(), desig.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                explicit.append({
                    "manufacturer": mfr,
                    "part_number": pn,
                    "designators": desig,
                })
            return tag_response(
                result,
                explicit_parts=explicit,
                context="proj_get_component_info_many",
            )
        return result

    @mcp.tool()
    async def proj_export_pdf(output_path: str) -> dict[str, Any]:
        """Export the active document to PDF.

        Args:
            output_path: Full path for the output PDF file

        Returns:
            Dictionary confirming export
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.export_pdf", {"output_path": output_path}
        )
        return result

    @mcp.tool()
    async def proj_cross_probe(
        designator: str,
        target: str = "schematic",
    ) -> dict[str, Any]:
        """Jump to and highlight a component in the schematic or PCB.

        Args:
            designator: Component designator to find (e.g., "U1", "R8")
            target: "schematic" or "pcb"

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.cross_probe",
            {"designator": designator, "target": target},
        )
        return result

    @mcp.tool()
    async def proj_get_stats(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get design statistics from the compiled project.

        Returns counts of sheets, components, pins, and nets.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with sheets, components, pins, nets counts
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_design_stats", params)
        return result

    @mcp.tool()
    async def proj_get_board_info() -> dict[str, Any]:
        """Get PCB board information, outline vertices, layer stack, origin.

        IT DOES NOT REQUIRE A PCB TO BE ACTIVE, which is what this used
        to claim. The handler resolves ANY open board, so with a
        schematic focused it answers about some other document.
        MEASURED: with a schematic active and a two-document project
        focused, it returned an outline belonging to neither, and gave
        no way to tell.

        ``board_path`` in the reply names the document that actually
        answered. Check it before trusting the geometry, or activate
        the board you mean first.

        Returns:
            Dictionary with origin_x, origin_y, board_path, outline
            (vertex array), and layers (active copper layer names)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_board_info", {})
        return result

    @mcp.tool()
    async def proj_annotate(
        order: str = "down_then_across",
    ) -> dict[str, Any]:
        """Annotate schematic designators programmatically, no dialog, no user interaction.

        Compiles the project, iterates every schematic sheet, collects all
        unlocked components, sorts them by the chosen order, and assigns
        sequential designators per alpha prefix (R1, R2, ... C1, C2, ... U1,
        U2, ...). Designator prefixes are preserved from the current value
        (e.g., "R?" or "R13" both keep the "R" prefix). Locked designators
        (Designator.IsLocked = True) are skipped. All changes are wrapped in
        SchServer.ProcessControl.PreProcess/PostProcess for undo support.

        Args:
            order: Annotation traversal order:
                   "down_then_across" (default: row-major top-to-bottom, left-to-right)
                   "up_then_across"   (row-major bottom-to-top, left-to-right)
                   "across_then_down" (column-major left-to-right, top-to-bottom)
                   "across_then_up"   (column-major left-to-right, bottom-to-top)
                   "none"             (reset all designators to "<prefix>?")

        Returns:
            Dictionary with:
              - success: True
              - order: the order that was applied
              - renamed: count of components renumbered
              - reset: count reset to "?" (only for order="none")
              - skipped_locked: count of locked components left untouched
              - documents_processed: count of schematic sheets visited
              - programmatic: True (marks this as the non-interactive path)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.annotate", {"order": order}
        )
        return result

    @mcp.tool()
    async def proj_run_output(
        output_type: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Generate manufacturing output files from the active PCB.

        Note: These may open Altium's export dialogs for configuration.

        Args:
            output_type: Type of output, "gerber", "drill", "pick_place", "ipc_netlist"
            output_path: Optional output directory/file path

        Returns:
            Dictionary confirming generation
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"output_type": output_type}
        if output_path:
            params["output_path"] = output_path
        result = await bridge.send_command_async(
            "project.generate_output", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def proj_get_focused() -> dict[str, Any]:
        """Get information about the currently focused project.

        Returns:
            Dictionary with project information:
            - project_name: Name of the project
            - project_path: Full path to the project file
            - document_count: Number of documents in the project
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_focused")
        return result

    # ------------------------------------------------------------------
    # Output generation tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_export_step(output_path: str = "") -> dict[str, Any]:
        """Export the active PCB to a STEP 3D model file.

        Requires an active PCB document. If output_path is omitted,
        Altium may show a file-save dialog.

        Args:
            output_path: Full path for the output .step file (optional)

        Returns:
            Dictionary confirming the export
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if output_path:
            params["output_path"] = output_path
        result = await bridge.send_command_async(
            "project.export_step", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def proj_export_dxf(output_path: str = "") -> dict[str, Any]:
        """Export the active PCB to DXF (AutoCAD) format.

        Requires an active PCB document. If output_path is omitted,
        Altium may show a file-save dialog.

        Args:
            output_path: Full path for the output .dxf file (optional)

        Returns:
            Dictionary confirming the export
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if output_path:
            params["output_path"] = output_path
        result = await bridge.send_command_async(
            "project.export_dxf", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def proj_export_image(
        output_path: str,
        format: str = "pdf",
        width: int = 1920,
        height: int = 1080,
    ) -> dict[str, Any]:
        """Export the active schematic / PCB document, silent (no dialog).

        Only ``format="pdf"`` is currently supported as a silent path.
        Raster formats (png/jpg/bmp) require a hand-configured OutJob with
        a Schematic Print -> Multimedia (Image) container; the Pascal side
        rejects them with ``IMAGE_FORMAT_UNSUPPORTED`` and points the
        caller at ``proj_run_outjob`` instead.

        The PDF path uses Altium's
        ``WorkspaceManager:Print`` server process with ``FileName=`` - the
        same machinery as ``proj_export_pdf``. No OutJob is loaded. The print
        runs against ``Client.CurrentView`` (the focused document), so
        ensure the schematic or PCB you want exported is the active tab
        before calling.

        The Pascal side deletes any pre-existing file at ``output_path``
        before running so the post-run FileExists probe is a true
        existence check. Python verifies again here and downgrades a
        Pascal "success" to ``EXPORT_FILE_MISSING`` if no file landed.

        Args:
            output_path: Full path for the output file. The directory must
                already exist. The extension should be ``.pdf``.
            format: Output format. Only ``"pdf"`` is silent today
                (default). ``"png"``, ``"jpg"``, ``"bmp"`` return an
                explicit ``IMAGE_FORMAT_UNSUPPORTED`` error.
            width: Kept for API compatibility. Unused by the PDF path.
            height: Kept for API compatibility. Unused by the PDF path.

        Returns:
            Dictionary confirming the export with the resolved path,
            format, width, height, and the scoped schematic. Sets
            ``file_exists`` based on a Python-side existence check.
        """
        import os

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.export_image",
            {
                "output_path": output_path,
                "format": format,
                "width": str(width),
                "height": str(height),
            },
            timeout=120.0,
        )
        if isinstance(result, dict) and result.get("success"):
            exists = os.path.exists(output_path)
            result["file_exists"] = exists
            if exists:
                try:
                    result["file_size_bytes"] = os.path.getsize(output_path)
                except OSError:
                    pass
            else:
                # Pascal claimed success but file is missing - downgrade
                # to an explicit error so callers don't trust a phantom
                # success.
                result["success"] = False
                result["error"] = "EXPORT_FILE_MISSING"
                result["details"] = (
                    f"Pascal reported success but {output_path} does not "
                    f"exist on disk. The synthetic-OutJob silent path may "
                    f"not be supported on this Altium build."
                )
        return result

    @mcp.tool()
    async def proj_list_outjob_containers(outjob_path: str = "") -> dict[str, Any]:
        """List all output containers defined in an OutJob file.

        OutJob files define output configurations (Gerber, PDF, BOM, etc.)
        organized into named containers. Use this to discover what outputs
        are available before running them with `proj_run_outjob`.

        Args:
            outjob_path: Path to the .OutJob file. If omitted, uses the
                         first OutJob found in the focused project.

        Returns:
            Dictionary with "outjob_path" and "containers" array
            (each: name, type, group)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if outjob_path:
            params["outjob_path"] = outjob_path
        result = await bridge.send_command_async(
            "project.get_outjob_containers", params
        )
        return result

    @mcp.tool()
    async def proj_run_outjob(
        container_name: str,
        outjob_path: str = "",
        fresh_window_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Execute a specific output container from an OutJob file.

        First use `proj_list_outjob_containers` to list available
        containers, then run the desired one by name. Supports both GeneratedFiles
        (Gerber, drill, BOM, etc.) and Publish (PDF) container types.

        SUCCESS MEANS A FILE WAS WRITTEN. Running a container proves only
        that Altium was asked to. One bound to a managed release, or with
        its outputs switched off, runs cleanly and writes nothing, and
        this used to report ``success: true`` with an ``output_dir`` that
        had never been created. ``output_dir`` is now checked afterwards,
        and ``success`` is true only when a file under it was written or
        changed after the run started.

        That check covers ``output_dir`` and nothing else. A container
        that writes somewhere else reads as ``success: false``, and
        ``reason`` says what was examined, so a caller who knows where
        the output really goes can look there.

        Args:
            container_name: Name of the output container to execute
            outjob_path: Path to the .OutJob file. If omitted, uses the
                         first OutJob found in the focused project.
            fresh_window_seconds: files modified up to this many seconds
                before the run started still count as written, for file
                systems with coarse timestamps. Default 5s.

        Returns:
            ``success``, ``container_name``, ``container_type``,
            ``output_dir``, ``output_dir_exists``, ``files_written`` (a
            count), ``written`` (up to 50 paths), and ``reason`` when
            nothing was written.
        """
        import time as _time

        bridge = get_bridge()
        params: dict[str, Any] = {"container_name": container_name}
        if outjob_path:
            params["outjob_path"] = outjob_path
        started_at = _time.time()
        result = await bridge.send_command_async(
            "project.run_outjob", params, timeout=120.0
        )
        return _judge_outjob_run(result, started_at, fresh_window_seconds)

    # Shared by the three OutJob tools, so they cannot disagree about what
    # "produced" means. Defined after proj_run_outjob in this scope, which
    # is fine: the tools look these up when they are CALLED, by which time
    # registration has finished.
    def _scan_output_dir(output_dir, started_at, fresh_window_seconds):
        """(exists, files) for one container's output directory.

        ``files`` is every file under it, each flagged ``newly_produced``
        when its mtime is at or after the run start less the window.
        """
        from pathlib import Path

        if not output_dir:
            return False, []
        root = Path(output_dir)
        if not root.is_dir():
            return False, []
        cutoff = started_at - float(fresh_window_seconds)
        files: list[dict[str, Any]] = []
        for f in root.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
            except OSError:
                continue
            files.append({"path": str(f), "size": st.st_size,
                          "modified_at": st.st_mtime,
                          "newly_produced": st.st_mtime >= cutoff})
        return True, files

    def _judge_outjob_run(run, started_at, fresh_window_seconds):
        """A run reply with ``success`` decided by what was written.

        The handler reports only that the process was issued. Anything
        that is not a normal reply is passed through untouched.
        """
        if not isinstance(run, dict) or run.get("error"):
            return run
        out = dict(run)
        out_dir = run.get("output_dir") or ""
        exists, files = _scan_output_dir(out_dir, started_at,
                                         fresh_window_seconds)
        fresh = [f["path"] for f in files if f["newly_produced"]]
        out["output_dir_exists"] = exists
        out["files_written"] = len(fresh)
        out["written"] = fresh[:50]
        out["success"] = bool(fresh)
        if not fresh:
            if not out_dir:
                out["reason"] = (
                    "the container ran, but the OutJob gives it no output "
                    "path, so there was no folder to check and no file is "
                    "known to have been written")
            elif not exists:
                out["reason"] = (
                    f"the container ran, but {out_dir} does not exist, so "
                    f"nothing was written there. A container bound to a "
                    f"managed release, or with its outputs switched off, "
                    f"runs cleanly and writes no local file")
            else:
                out["reason"] = (
                    f"the container ran, but no file under {out_dir} was "
                    f"written or changed after it started. Outputs switched "
                    f"off in the OutJob, or bound to a managed release, "
                    f"produce this")
        return out

    @mcp.tool()
    async def proj_run_outjob_all(
        outjob_path: str = "",
        include_files: bool = True,
        fresh_window_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Run every container in an OutJob and report what each produced.

        Lists containers via ``proj_list_outjob_containers``, then runs each
        through ``proj_run_outjob`` in order. For each run, scans the
        OutJob's resolved ``output_dir`` afterwards and flags files
        whose mtime is later than the run started (``newly_produced``)
        so the caller can tell which files this particular container
        wrote.

        Args:
            outjob_path: Path to the .OutJob. Omit to use the first one
                in the focused project.
            include_files: When True (default) the response includes a
                per-container file listing with sizes / mtimes / a
                ``newly_produced`` flag.
            fresh_window_seconds: Files modified within this many
                seconds before the container started are still
                considered "newly produced" (some output processes
                touch existing files instead of rewriting). Default 5s.

        Returns:
            Dict with ``ok``, ``outjob_path``, ``containers_run``
            (list of container names), ``results`` (per-container
            dicts each with ``container``, ``container_type``,
            ``output_dir``, ``ok``, ``files``).
        """
        from pathlib import Path
        import time as _time

        bridge = get_bridge()
        list_params: dict[str, Any] = {}
        if outjob_path:
            list_params["outjob_path"] = outjob_path
        listing = await bridge.send_command_async(
            "project.get_outjob_containers", list_params, timeout=30.0,
        )
        if not isinstance(listing, dict):
            return {"ok": False, "reason": "container listing failed"}
        containers = listing.get("containers") or []
        if not containers:
            return {"ok": True, "containers_run": [], "results": [],
                    "reason": "no containers in OutJob"}

        per_container: list[dict[str, Any]] = []
        for c in containers:
            name = c.get("name") if isinstance(c, dict) else str(c)
            if not name:
                continue
            started_at = _time.time()
            params: dict[str, Any] = {"container_name": name}
            if outjob_path:
                params["outjob_path"] = outjob_path
            run = await bridge.send_command_async(
                "project.run_outjob", params, timeout=300.0,
            )
            judged = _judge_outjob_run(run, started_at, fresh_window_seconds)
            judged = judged if isinstance(judged, dict) else {}
            out_dir = judged.get("output_dir")
            exists, files = _scan_output_dir(out_dir, started_at,
                                             fresh_window_seconds)
            entry: dict[str, Any] = {
                "container": name,
                # From what was written, not from the handler, which
                # reports only that the process was issued.
                "ok": bool(judged.get("success")),
                "container_type": judged.get("container_type"),
                "output_dir": out_dir,
                "output_dir_exists": exists,
                "files_written": judged.get("files_written", 0),
                "files": files if include_files else [],
            }
            if not entry["ok"]:
                entry["reason"] = judged.get("reason", "")
            per_container.append(entry)

        produced = [r for r in per_container if r["ok"]]
        return {
            "ok": bool(produced),
            "containers_with_output": len(produced),
            "reason": "" if produced else (
                "no container wrote a file under its output_dir; each "
                "result says what was checked"),
            "outjob_path": (
                (per_container[0].get("output_dir") if per_container else "")
                if not outjob_path else outjob_path
            ),
            "containers_run": [r["container"] for r in per_container],
            "results": per_container,
        }

    @mcp.tool()
    async def proj_generate_fab_package(
        outjob_path: str = "",
        include_step: bool = False,
        include_dxf: bool = False,
        fresh_window_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Generate a fabrication package from an OutJob (Gerbers, NC drill,
        IPC-356, pick-and-place, assembly, BOM) and report every file produced.

        Altium has no per-format export process for Gerber / NC-drill /
        IPC-356 / P&P: those exist only as OutJob output containers. This runs
        every container in the project's OutJob, scans each container's output
        directory, and returns a consolidated manifest. Optionally also exports
        a STEP 3D model and a DXF (which are separate PCB export processes, not
        OutJob containers).

        Prerequisite: the project must have an OutJob whose fab containers are
        configured and enabled: the script cannot enable outputs that are off
        in the OutJob editor.

        Args:
            outjob_path: path to the .OutJob; omit to use the first one in the
                focused project.
            include_step: also export a STEP 3D model of the active PCB.
            include_dxf: also export a DXF of the active PCB.
            fresh_window_seconds: files modified within this window before a
                container ran still count as newly produced.

        Returns:
            {"ok", "outjob_path", "containers_run", "results" (per-container
             with output_dir + files), "extras" (step/dxf results),
             "all_files" (flat list of newly-produced file paths)}.
        """
        from pathlib import Path
        import time as _time

        bridge = get_bridge()
        list_params: dict[str, Any] = {}
        if outjob_path:
            list_params["outjob_path"] = outjob_path
        listing = await bridge.send_command_async(
            "project.get_outjob_containers", list_params, timeout=30.0,
        )
        if not isinstance(listing, dict):
            return {"ok": False, "reason": "container listing failed"}
        containers = listing.get("containers") or []
        if not containers:
            return {
                "ok": False,
                "reason": "no OutJob containers found; create and configure an "
                "OutJob (Gerber, NC drill, etc.) before generating a fab package",
            }

        per_container: list[dict[str, Any]] = []
        all_files: list[str] = []
        for c in containers:
            name = c.get("name") if isinstance(c, dict) else str(c)
            if not name:
                continue
            started_at = _time.time()
            params: dict[str, Any] = {"container_name": name}
            if outjob_path:
                params["outjob_path"] = outjob_path
            run = await bridge.send_command_async(
                "project.run_outjob", params, timeout=300.0,
            )
            judged = _judge_outjob_run(run, started_at, fresh_window_seconds)
            judged = judged if isinstance(judged, dict) else {}
            out_dir = judged.get("output_dir")
            exists, files = _scan_output_dir(out_dir, started_at,
                                             fresh_window_seconds)
            all_files.extend(f["path"] for f in files if f["newly_produced"])
            entry: dict[str, Any] = {
                "container": name,
                "container_type": judged.get("container_type"),
                # From what was written, not from the handler, which
                # reports only that the process was issued.
                "ok": bool(judged.get("success")),
                "output_dir": out_dir,
                "output_dir_exists": exists,
                "files_written": judged.get("files_written", 0),
                "files": files,
            }
            if not entry["ok"]:
                entry["reason"] = judged.get("reason", "")
            per_container.append(entry)

        extras: dict[str, Any] = {}
        if include_step:
            extras["step"] = await bridge.send_command_async(
                "project.export_step", {}, timeout=120.0
            )
        if include_dxf:
            extras["dxf"] = await bridge.send_command_async(
                "project.export_dxf", {}, timeout=120.0
            )

        return {
            "ok": bool(all_files),
            "reason": "" if all_files else (
                "no container wrote a file under its output_dir, so there "
                "is no fabrication package; each result says what was "
                "checked"),
            "outjob_path": outjob_path,
            "containers_run": [r["container"] for r in per_container],
            "results": per_container,
            "extras": extras,
            "all_files": sorted(set(all_files)),
        }

    # ------------------------------------------------------------------
    # Variant management tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_list_variants(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """List all project variants with their component overrides.

        Compiles the project and returns every variant definition including
        component variation details (Fitted / Not Fitted / Alternate) and
        any parameter overrides.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "variants" array and "count". Each variant has
            name, description, and variations array (designator, kind,
            alternate_part, parameters).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_variants", params)
        return result

    @mcp.tool()
    async def proj_get_active_variant(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get the currently active project variant.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with variant "name" and "description".
            Returns "[No Variations]" if no variant is active.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.get_active_variant", params
        )
        return result

    @mcp.tool()
    async def proj_set_active_variant(
        variant_name: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Switch the active project variant.

        Args:
            variant_name: Name of the variant to activate
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming the switch
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"variant_name": variant_name}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.set_active_variant", params
        )
        return result

    @mcp.tool()
    async def proj_export_variant_matrix_csv(
        output_path: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Write the variant fitted/not-fitted matrix to a CSV.

        Builds the conventional component-by-variant table: one row per
        flattened component, one column per variant, each cell
        ``Fitted`` / ``Not Fitted`` / ``Alternate``. Unlike
        ``proj_list_variants`` (which lists only per-variant deviations), every
        component appears, so the file merges cleanly with a BOM in a
        spreadsheet.

        Args:
            output_path: Destination .csv. Defaults to
                ``workspace/variant_matrix.csv``.
            project_path: Optional project path. If None, uses the focused
                project.

        Returns:
            {"output_path", "component_count", "variant_count"} or an error.
        """
        from pathlib import Path

        from ..config import get_config
        from ..export.variant_matrix_csv import format_variant_matrix_csv

        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        matrix = await bridge.send_command_async(
            "project.get_variant_matrix", params
        )
        if not isinstance(matrix, dict) or "rows" not in matrix:
            return {"success": False,
                    "error": "could not read variant matrix (open a PcbDoc project)"}

        csv_text = format_variant_matrix_csv(matrix)
        if output_path:
            out = Path(output_path)
        else:
            out = get_config().workspace_dir / "variant_matrix.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(csv_text, encoding="utf-8")
        return {
            "success": True,
            "output_path": str(out),
            "component_count": matrix.get("component_count",
                                          len(matrix.get("rows") or [])),
            "variant_count": len(matrix.get("variants") or []),
        }

    @mcp.tool()
    async def proj_print_all_variants(
        output_dir: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Export a PDF for every project variant.

        Loops the project's variants; for each it sets that variant active and
        runs the (silent) PDF export, writing one ``<variant>.pdf`` per
        variant. The originally-active variant is restored at the end. Composes
        the existing ``proj_set_active_variant`` + ``proj_export_pdf`` paths --
        no new export machinery.

        Args:
            output_dir: Folder for the per-variant PDFs. Defaults to the
                workspace directory.
            project_path: Optional project path. If None, uses the focused
                project.

        Returns:
            {"exported": [{variant, output_path, ok, error}], "count"} plus
            ``restored``: the variant put back, or null if putting it
            back FAILED. Null is not cosmetic. This loop leaves the
            project on the last variant it exported, so a null means the
            active variant is not the one you started with, and anything
            variant-sensitive run afterwards (an export, a BOM,
            ``pcb_apply_dnp_paste_exclusion``) will act on the wrong
            set. A failure also carries ``restore_error``,
            ``active_variant_unknown`` and a ``note``.
        """
        from pathlib import Path

        from ..config import get_config

        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path

        listing = await bridge.send_command_async("project.get_variants", params)
        variants = (listing or {}).get("variants", []) if isinstance(listing, dict) else []
        active = await bridge.send_command_async("project.get_active_variant", params)
        original = (active or {}).get("name") if isinstance(active, dict) else None

        base = Path(output_dir) if output_dir else get_config().workspace_dir
        base.mkdir(parents=True, exist_ok=True)

        def _safe(s: str) -> str:
            return "".join(c if c.isalnum() or c in "-_." else "_" for c in s) or "variant"

        exported: list[dict[str, Any]] = []
        for var in variants:
            vname = var.get("name") if isinstance(var, dict) else str(var)
            if not vname:
                continue
            out_pdf = base / f"{_safe(vname)}.pdf"
            entry: dict[str, Any] = {"variant": vname, "output_path": str(out_pdf),
                                     "ok": False, "error": ""}
            try:
                set_params = dict(params, variant_name=vname)
                await bridge.send_command_async("project.set_active_variant", set_params)
                res = await bridge.send_command_async(
                    "project.export_pdf", {"output_path": str(out_pdf)}
                )
                entry["ok"] = bool(isinstance(res, dict) and res.get("success", True))
                if isinstance(res, dict) and res.get("error"):
                    entry["error"] = str(res["error"])
            except Exception as exc:  # keep going across variants
                entry["error"] = str(exc)
            exported.append(entry)

        # Restore the variant that was active before we started.
        #
        # Report whether that WORKED, not merely that it was attempted.
        # This loop leaves the project on the last variant it touched,
        # so a swallowed failure here hands back a project whose active
        # variant is not the one the caller had, and every
        # variant-sensitive operation after it acts on the wrong set:
        # an export, a BOM, or pcb_apply_dnp_paste_exclusion stripping
        # paste off the components the OTHER variant does not fit.
        restored: Optional[str] = None
        restore_error = ""
        if original:
            try:
                await bridge.send_command_async(
                    "project.set_active_variant", dict(params, variant_name=original)
                )
                restored = original
            except Exception as exc:
                restore_error = str(exc)

        result: dict[str, Any] = {
            "exported": exported,
            "count": sum(1 for e in exported if e["ok"]),
            "restored": restored,
        }
        if restore_error:
            result["restore_error"] = restore_error
            result["active_variant_unknown"] = True
            result["note"] = (
                "the originally-active variant could not be restored, so "
                "the project is left on the last variant exported. Set it "
                "back with proj_set_active_variant before any "
                "variant-sensitive operation."
            )
        return result

    @mcp.tool()
    async def proj_create_variant(
        name: str,
        description: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a new project variant.

        After creating, use `proj_set_active_variant` to switch to it,
        and `obj_modify` to configure component variations.

        Args:
            name: Name for the new variant
            description: Optional description
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming creation with name and description
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"name": name}
        if description:
            params["description"] = description
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.create_variant", params
        )
        return result

    @mcp.tool()
    async def proj_delete_variant(
        variant_name: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Delete a project variant, with every component variation in it.

        THIS IS NOT REVERSIBLE FROM HERE. A variant's entries record which
        components are not fitted and which carry an alternate part, and
        ``DM_RemoveProjectVariant`` is the ONLY write in Altium's whole
        variant API. There is no scripted way to add a variation entry
        back, so deleting a variant with fifty entries costs fifty
        decisions that have to be re-made in the Variant Management
        dialog. The reply reports ``entries_removed`` so the size of that
        is on the record; read it with ``proj_list_variants`` first if
        the variant is not one you created.

        The variant is addressed by name rather than index, because
        removing one renumbers the rest: two deletions by index taken
        from a single listing would remove the wrong second variant.

        Args:
            variant_name: name of the variant to remove.
            project_path: optional project path; defaults to the focused
                project.

        Returns:
            ``{"success": true, "variant_name": ..., "entries_removed": N,
            "variant_count_before": N, "variant_count_after": N,
            "verified": true}``, or ``success: false`` with a ``reason``
            when the variant is still listed afterwards.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"variant_name": variant_name}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.delete_variant", params
        )
        return result

    # ------------------------------------------------------------------
    # Additional project operations
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_list_open() -> dict[str, Any]:
        """List all currently open projects in the Altium workspace.

        Returns:
            Dictionary with "projects" array (each: project_name,
            project_path, document_count) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.get_open_projects", {}
        )
        return result

    # ------------------------------------------------------------------
    # Messages, search, connectivity, import, path, document parameters
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_get_messages(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get all messages from the Messages panel (compile errors, ERC violations, etc.).

        Compiles the project first so violation data is current, then returns
        every violation with its text, severity, and source document.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "messages" array (each: message, severity, source) and "count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_messages", params)
        return result

    @mcp.tool()
    async def proj_find_component(
        search_text: str,
        search_by: str = "designator",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search for components across all project sheets.

        DATASHEET DISCIPLINE: Results carry `_datasheet_guidance`. If
        you're searching by a part number / comment to answer a
        technical question (pinout, rating, behavior), the matched
        parts' datasheets must be consulted before drawing any
        conclusion. Symbol metadata is not ground truth.

        Performs a case-insensitive partial match against the chosen property.

        Args:
            search_text: Text to search for (e.g., "U1", "100nF", "LM317")
            search_by: Property to search, "designator", "value", or "comment" (default "designator")
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "results" array (each: designator, comment, footprint,
            lib_ref, sheet, location_x, location_y) and "count", plus
            `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "search_text": search_text,
            "search_by": search_by,
        }
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.find_component", params)
        if isinstance(result, dict):
            # find_component response puts matches under "results"
            # with comment/value fields, reshape to the components form
            # extract_unique_parts understands.
            synthetic = {"components": result.get("results") or []}
            return tag_response(
                result,
                components=synthetic,
                context="proj_find_component",
            )
        return result

    @mcp.tool()
    async def proj_get_connectivity(
        designator: str,
        project_path: Optional[str] = None,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Get pin-to-net connectivity for a specific component.

        IMPORTANT, if you need connectivity for MORE THAN ONE
        component, use `proj_get_connectivity_many` (batch). Looping this
        tool for a set of designators is the biggest wall-time sink
        in design-review workflows.

        Compiles the project and returns every pin with number, name,
        net assignment, and electrical type.

        Args:
            designator: Component designator (e.g., "U1", "R8")
            project_path: Optional project path. If None, uses active.
            force_recompile: SaveAll + invalidate cache + recompile
                before reading. Use when you need a guaranteed-fresh
                netlist.

        DATASHEET DISCIPLINE: pin_name in the response comes from the
        symbol, which can be wrong. Before reasoning about the pin's
        function (input vs output, drive strength, voltage range,
        active level), fetch the manufacturer datasheet and verify
        against its pin-description table. The response carries
        `_datasheet_guidance` + `_datasheet_parts`.

        Returns:
            Dict with designator, comment, sheet, pin_count, pins[],
            plus `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if project_path:
            params["project_path"] = project_path
        if force_recompile:
            # Project.pas ForceRecompileIfRequested reads
            # "force_recompile". Sent under the tool's own name this
            # matched nothing, so the flag was dead and a caller who
            # asked for fresh data got the cached compile instead.
            params["force_recompile"] = "true"
        result = await bridge.send_command_async("project.get_connectivity", params)
        hint = BulkHintTracker.record_and_hint("proj_get_connectivity")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        if isinstance(result, dict):
            comment = str(result.get("comment") or "").strip()
            explicit = [{
                "manufacturer": "",
                "part_number": comment,
                "designators": designator,
            }]
            return tag_response(
                result, explicit_parts=explicit, context="proj_get_connectivity"
            )
        return result

    @mcp.tool()
    async def proj_get_connectivity_many(
        designators: list[str],
        project_path: Optional[str] = None,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Pin-net connectivity for MANY components in ONE round-trip.

        PREFER THIS over looping `proj_get_connectivity`.

        DATASHEET DISCIPLINE: pin_name on every component in the
        response comes from the symbol and can be wrong. Before
        reasoning about pin functions, fetch each part's manufacturer
        datasheet. The response carries `_datasheet_guidance` +
        `_datasheet_parts`.

        Args:
            designators: List of component designators.
            project_path: Optional project path.
            force_recompile: SaveAll + invalidate cache + recompile
                before reading. Use when you need a guaranteed-fresh
                netlist.

        Returns:
            Dict with components[], matched, requested, not_found[],
            plus `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        cleaned = [str(d).strip() for d in (designators or []) if str(d).strip()]
        if not cleaned:
            return {"error": "No designators provided", "matched": 0}
        params: dict[str, Any] = {"designators": "~~".join(cleaned)}
        if project_path:
            params["project_path"] = project_path
        if force_recompile:
            # Project.pas ForceRecompileIfRequested reads
            # "force_recompile". Sent under the tool's own name this
            # matched nothing, so the flag was dead and a caller who
            # asked for fresh data got the cached compile instead.
            params["force_recompile"] = "true"
        result = await bridge.send_command_async(
            "project.get_connectivity_batch", params
        )
        if isinstance(result, dict):
            comps = result.get("components") or []
            explicit = []
            seen_keys: set[tuple[str, str]] = set()
            for c in comps:
                if not isinstance(c, dict):
                    continue
                desig = str(c.get("designator") or "").strip()
                pn = str(c.get("comment") or "").strip()
                key = (pn.lower(), desig.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                explicit.append({
                    "manufacturer": "",
                    "part_number": pn,
                    "designators": desig,
                })
            return tag_response(
                result,
                explicit_parts=explicit,
                context="proj_get_connectivity_many",
            )
        return result

    @mcp.tool()
    async def proj_force_recompile() -> dict[str, Any]:
        """Flush all dirty docs, invalidate the compile cache, and recompile.

        Use this when you need a guaranteed-fresh netlist, e.g.
        immediately before re-running a connectivity check the user
        has disputed. Returns prev / new compile tick so you can
        verify the recompile actually happened.

        Returns:
            Dict with recompiled, prev_compile_tick, new_compile_tick,
            project path.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "project.force_recompile", {}, timeout=120.0
        )

    @mcp.tool()
    async def proj_get_compile_freshness() -> dict[str, Any]:
        """Report the age of the cached netlist and which docs are dirty.

        Use this when you're about to disagree with the user about
        connectivity, first check how stale the netlist you're
        reading actually is, and whether any open editor docs haven't
        been saved yet. A dirty doc means the netlist does NOT
        reflect what the user is looking at.

        Returns:
            Dict with compile_age_ms, compile_cached (bool), ttl_ms,
            open_doc_count, dirty_doc_count, dirty_docs[], project.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "project.get_compile_freshness", {}
        )

    @mcp.tool()
    async def proj_import_document(
        source_path: str,
    ) -> dict[str, Any]:
        """Import a document into the focused project from an external path.

        Copies the file into the project directory (if not already there),
        adds it to the project, and saves the project file.

        Args:
            source_path: Full path to the source document (SchDoc, PcbDoc, etc.)

        Returns:
            Dictionary with success status, source_path, and dest_path
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.import_document", {"source_path": source_path}
        )
        return result

    @mcp.tool()
    async def proj_get_path() -> dict[str, Any]:
        """Get the full path of the currently focused project file.

        Returns:
            Dictionary with project_path, project_dir, and project_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_project_path")
        return result

    @mcp.tool()
    async def proj_set_document_parameter(
        file_path: str,
        name: str,
        value: str,
    ) -> dict[str, Any]:
        """Set a document-level parameter on a specific schematic sheet.

        Useful for per-sheet title block data (e.g., "SheetTitle", "Revision").
        If the parameter already exists it is updated; otherwise a new hidden
        parameter object is created on the sheet.

        NOTE: the target sheet must already be loaded as a proper project
        member. Call `proj_load_sheets` once at the start of a batch;
        auto-opening from inside this tool risks detaching the sheet and
        rendering it as a "free document". If the sheet isn't loaded this
        tool returns NOT_LOADED.

        The write is persisted to disk immediately via the IServerDocument
        API, no subsequent `app_save_all` is required.

        Args:
            file_path: Full path to the schematic document (.SchDoc).
                Use Windows-style backslashes (e.g. C:\\path\\Sheet.SchDoc),
                not forward slashes.
            name: Parameter name
            value: Parameter value

        Returns:
            Dictionary with file_path, name, value, and dirty=true
            (the in-memory flag; the disk write already happened, so no
            `app_save_all` is needed for this parameter).
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.set_document_parameter",
            {"file_path": file_path, "name": name, "value": value},
        )
        return result

    # ------------------------------------------------------------------
    # Design verification and cross-probing tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_compare_sch_pcb(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compare schematic and PCB component counts.

        Compiles the project and compares physical component counts between the
        schematic and the primary PCB document, via the component mappings
        (matched + side-only) -- the same authoritative path as
        ``proj_get_differences``. For the per-component breakdown of what is
        extra on each side, use ``proj_get_differences``.

        DATASHEET DISCIPLINE: If the diff reveals mismatched or missing
        parts and you're proposing a fix, the datasheets of the parts
        involved are authoritative on their pinout and behavior, fetch
        them before suggesting a footprint change or pin reassignment.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with sch_components, pcb_components, components_match
            (bool), and pcb_path. (Net-count comparison is not provided here;
            use ``proj_get_nets`` / ``pcb_get_nets``.)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.compare_sch_pcb", params
        )
        return result

    @mcp.tool()
    async def proj_sync_pcb() -> dict[str, Any]:
        """Push schematic changes to PCB (ECO): Design ▸ Update PCB Document.

        IMPORTANT: this is NOT silent. Altium's ECO (change-review) dialog
        is non-suppressible by design, so this **fires the real ECO and then
        BLOCKS on a modal dialog until a human clicks "Execute Changes"**.
        Do not call it in an unattended/headless run: it will hang the
        Altium-side polling loop until someone interacts. (There is no
        documented silent flag; the prior implementation called a
        non-existent process id and silently did nothing.)

        The server:
          1. Compiles the project and records before-state mappings
             (matched, extra-in-schematic, extra-in-pcb).
          2. Invokes ``WorkspaceManager:Compare`` (ObjectKind=Project,
             Action=UpdateOther): the evidenced scriptable sch→PCB update.
             The modal ECO dialog opens here.
          3. After the user accepts, recompiles and reports the after-state
             delta (how many components were added/removed).

        Refuses with WRONG_FOCUS while a schematic is the active document.
        MEASURED 2026-08-17: Altium answers the compare with a modal reading
        "Cannot compare a source document against its owner project" and
        changes nothing, and this tool used to report success anyway, three
        times in a row, with the error still on screen. Focus the PCB with
        ``app_set_active_document`` first.

        What the result does NOT tell you: whether the dialog was accepted.
        ``dialog_outcome_verified`` is always false, because the handler
        returns as soon as the modal closes and cannot see which button was
        pressed. ``components_added_to_pcb`` and its siblings are recounts,
        so they catch a change in component PRESENCE and miss everything
        else an ECO carries: footprint swaps, designator and parameter
        edits, net changes. Unchanged counts therefore mean "nothing was
        added or removed", not "nothing happened". To learn what the dialog
        actually says, call ``app_list_open_dialogs``, which reads Altium
        over Win32 and so answers while the modal is blocking the bridge.

        For unattended board population without a schematic, use
        ``pcb_place_components`` instead (places geometry only: see its note
        about leaving the project unsynced).

        Returns:
            Dictionary with success, pcb_path, before/after mapping counts,
            components_added_to_pcb, components_removed_from_pcb,
            components_in_sync (component presence only, see above), and
            dialog_outcome_verified.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.update_pcb", {})
        return result

    @mcp.tool()
    async def proj_sync_schematic() -> dict[str, Any]:
        """Push PCB changes back to schematic (back-annotate ECO). Attempts silent execution.

        Equivalent to Design > Update Schematic in Altium Designer.
        Mirror of update_pcb: compiles, records before-state mappings,
        invokes PCB:UpdateSchematicFromPCB with silent-mode parameter flags
        (DisableDialog, Silent, NoConfirm, AutoApply), recompiles, and
        reports the after-state delta. Modern Altium versions execute
        without a dialog; older versions may still require manual
        confirmation (flagged via dialog_may_have_opened).

        Returns:
            Dictionary with success, pcb_path, before/after mapping counts,
            components_added_to_schematic, components_removed_from_schematic,
            in_sync, and dialog_may_have_opened flag.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.update_schematic", {}
        )
        return result

    @mcp.tool()
    async def proj_get_differences(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get detailed differences between schematic and PCB netlist.

        Compiles the project and uses component mappings to find:
        - Matched components (present in both schematic and PCB)
        - Extra components in schematic (not yet in PCB)
        - Extra components in PCB (not in schematic)

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with matched_components, extra_in_schematic (array),
            extra_in_pcb (array), and in_sync (bool)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.get_design_differences", params
        )
        return result

    @mcp.tool()
    async def proj_lock_designator(
        designator: str,
        lock: bool = True,
    ) -> dict[str, Any]:
        """Lock or unlock component designators to prevent re-annotation.

        When locked, designators are preserved during annotation operations.
        Use designator="all" to lock/unlock all components on the active sheet.

        Args:
            designator: Component designator (e.g., "U1", "R3") or "all" for all components
            lock: True to lock, False to unlock (default True)

        Returns:
            Dictionary with designator, locked status, and count of affected components
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.lock_designator",
            {
                "designator": designator,
                "lock": "true" if lock else "false",
            },
        )
        return result

    @mcp.tool()
    async def proj_get_options(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get project options: output path, hierarchy mode, compiler settings.

        Returns configuration details including output directory, hierarchy mode,
        document counts, variant count, channel settings, and net naming options.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with project_name, output_path, hierarchy_mode,
            logical_document_count, physical_document_count, variant_count,
            channel settings, and net naming options
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.get_project_options", params
        )
        return result
