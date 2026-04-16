# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Project management tools for Altium Designer MCP Server."""

from typing import Any, Optional
from ..bridge import get_bridge


def register_project_tools(mcp):
    """Register project tools with the MCP server."""

    @mcp.tool()
    async def create_project(
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
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.create",
            {"project_path": project_path, "project_type": project_type},
        )
        return result

    @mcp.tool()
    async def open_project(project_path: str) -> dict[str, Any]:
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
    async def save_project(project_path: Optional[str] = None) -> dict[str, Any]:
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
    async def close_project(
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
    async def get_project_documents(
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
    async def add_document_to_project(
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
    async def remove_document_from_project(
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
    async def get_project_parameters(
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
    async def set_project_parameter(
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
        return result

    @mcp.tool()
    async def get_nets(
        component: str = "",
        net_name: str = "",
        project_path: Optional[str] = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Get net-to-pin connectivity from the compiled project netlist.

        Compiles the project and returns pin-level net assignments. Use this to
        trace which pins connect to which nets, which components share a net, or
        verify that a specific pin is wired to the correct net.

        Args:
            component: Filter by component designator (e.g., "U1", "R8"). Empty = all components.
            net_name: Filter by net name (e.g., "VCC", "CLSA"). Empty = all nets.
            project_path: Optional project path. If None, uses active project.
            limit: Maximum pin records to return (default 500).

        Returns:
            Dictionary with "pins" array (each: component, pin_number, pin_name, net) and "count"

        Examples:
            get_nets(component="U1") — all pin-net connections for U1
            get_nets(net_name="CLSA") — all pins connected to the CLSA net
            get_nets(component="U1", net_name="VCC") — just U1's VCC pins
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if component:
            params["component"] = component
        if net_name:
            params["net_name"] = net_name
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_nets", params)
        return result

    @mcp.tool()
    async def compile_project(project_path: Optional[str] = None) -> dict[str, Any]:
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
    async def get_bom(
        project_path: Optional[str] = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Export a full BOM from the compiled project.

        Returns every component with designator, comment/value, footprint,
        library reference, and all pin-net connections.

        Args:
            project_path: Optional project path. If None, uses active project.
            limit: Max components to return (default 1000).

        Returns:
            Dictionary with "components" array and "count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_bom", params)
        return result

    @mcp.tool()
    async def get_component_info(
        designator: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get full information about a single component.

        Compiles the project and returns the component's designator, comment,
        footprint, library reference, all parameters, and every pin with its
        net assignment — all in one call.

        Args:
            designator: Component designator (e.g., "U1", "R8", "C3")
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with designator, comment, footprint, lib_ref, sheet,
            parameters dict, and pins array
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_component_info", params)
        return result

    @mcp.tool()
    async def export_pdf(output_path: str) -> dict[str, Any]:
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
    async def cross_probe(
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
    async def get_design_stats(
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
    async def get_board_info() -> dict[str, Any]:
        """Get PCB board information — outline vertices, layer stack, origin.

        Requires an active PCB document.

        Returns:
            Dictionary with origin_x, origin_y, outline (vertex array),
            and layers (active copper layer names)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_board_info", {})
        return result

    @mcp.tool()
    async def annotate(
        order: str = "down_then_across",
    ) -> dict[str, Any]:
        """Annotate schematic designators programmatically — no dialog, no user interaction.

        Compiles the project, iterates every schematic sheet, collects all
        unlocked components, sorts them by the chosen order, and assigns
        sequential designators per alpha prefix (R1, R2, ... C1, C2, ... U1,
        U2, ...). Designator prefixes are preserved from the current value
        (e.g., "R?" or "R13" both keep the "R" prefix). Locked designators
        (Designator.IsLocked = True) are skipped. All changes are wrapped in
        SchServer.ProcessControl.PreProcess/PostProcess for undo support.

        Args:
            order: Annotation traversal order —
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
    async def generate_output(
        output_type: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Generate manufacturing output files from the active PCB.

        Note: These may open Altium's export dialogs for configuration.

        Args:
            output_type: Type of output — "gerber", "drill", "pick_place", "ipc_netlist"
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
    async def get_focused_project() -> dict[str, Any]:
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
    async def export_step(output_path: str = "") -> dict[str, Any]:
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
    async def export_dxf(output_path: str = "") -> dict[str, Any]:
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
    async def export_image(
        output_path: str,
        format: str = "png",
        width: int = 1920,
        height: int = 1080,
    ) -> dict[str, Any]:
        """Export the current document view as an image file.

        Args:
            output_path: Full path for the output image file
            format: Image format — "png", "jpg", or "bmp" (default "png")
            width: Image width in pixels (default 1920)
            height: Image height in pixels (default 1080)

        Returns:
            Dictionary confirming the export with dimensions
        """
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
        return result

    @mcp.tool()
    async def get_outjob_containers(outjob_path: str = "") -> dict[str, Any]:
        """List all output containers defined in an OutJob file.

        OutJob files define output configurations (Gerber, PDF, BOM, etc.)
        organized into named containers. Use this to discover what outputs
        are available before running them with run_outjob().

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
    async def run_outjob(
        container_name: str,
        outjob_path: str = "",
    ) -> dict[str, Any]:
        """Execute a specific output container from an OutJob file.

        First use get_outjob_containers() to list available containers,
        then run the desired one by name. Supports both GeneratedFiles
        (Gerber, drill, BOM, etc.) and Publish (PDF) container types.

        Args:
            container_name: Name of the output container to execute
            outjob_path: Path to the .OutJob file. If omitted, uses the
                         first OutJob found in the focused project.

        Returns:
            Dictionary with success status, container name and type
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"container_name": container_name}
        if outjob_path:
            params["outjob_path"] = outjob_path
        result = await bridge.send_command_async(
            "project.run_outjob", params, timeout=120.0
        )
        return result

    # ------------------------------------------------------------------
    # Variant management tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_variants(
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
    async def get_active_variant(
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
    async def set_active_variant(
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
    async def create_variant(
        name: str,
        description: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a new project variant.

        After creating, use set_active_variant() to switch to it, and
        generic.modify_objects() to configure component variations.

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

    # ------------------------------------------------------------------
    # Additional project operations
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_open_projects() -> dict[str, Any]:
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

    @mcp.tool()
    async def save_all() -> dict[str, Any]:
        """Save all open documents in the workspace.

        Equivalent to File > Save All in Altium Designer.

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.save_all", {})
        return result

    # ------------------------------------------------------------------
    # Messages, search, connectivity, import, path, document parameters
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_messages(
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
    async def find_component(
        search_text: str,
        search_by: str = "designator",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search for components across all project sheets.

        Performs a case-insensitive partial match against the chosen property.

        Args:
            search_text: Text to search for (e.g., "U1", "100nF", "LM317")
            search_by: Property to search — "designator", "value", or "comment" (default "designator")
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "results" array (each: designator, comment, footprint,
            lib_ref, sheet, location_x, location_y) and "count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "search_text": search_text,
            "search_by": search_by,
        }
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.find_component", params)
        return result

    @mcp.tool()
    async def get_connectivity(
        designator: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get pin-to-net connectivity for a specific component.

        Compiles the project and returns every pin with its number, name, net
        assignment, and electrical type for the given designator.

        Args:
            designator: Component designator (e.g., "U1", "R8")
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with designator, comment, sheet, pin_count,
            and "pins" array (each: pin_number, pin_name, net, electrical_type)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_connectivity", params)
        return result

    @mcp.tool()
    async def import_document(
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
    async def get_project_path() -> dict[str, Any]:
        """Get the full path of the currently focused project file.

        Returns:
            Dictionary with project_path, project_dir, and project_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_project_path")
        return result

    @mcp.tool()
    async def set_document_parameter(
        file_path: str,
        name: str,
        value: str,
    ) -> dict[str, Any]:
        """Set a document-level parameter on a specific schematic sheet.

        Useful for per-sheet title block data (e.g., "SheetTitle", "Revision").
        If the parameter already exists it is updated; otherwise a new hidden
        parameter object is created on the sheet.

        Args:
            file_path: Full path to the schematic document (.SchDoc)
            name: Parameter name
            value: Parameter value

        Returns:
            Dictionary confirming the operation with file_path, name, and value
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
    async def compare_sch_pcb(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compare schematic and PCB: compile and report net/component count differences.

        Compiles the project and compares net counts and component counts between
        the schematic sheets and the primary PCB document.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with sch_nets, sch_components, pcb_nets, pcb_components,
            nets_match (bool), components_match (bool), and pcb_path
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
    async def update_pcb() -> dict[str, Any]:
        """Push schematic changes to PCB (ECO). Attempts silent execution.

        Equivalent to Design > Update PCB Document in Altium Designer.
        The server:
          1. Compiles the project and records before-state mappings
             (matched, extra-in-schematic, extra-in-pcb).
          2. Invokes PCB:UpdatePCBFromProject with silent-mode parameter
             flags (DisableDialog, Silent, NoConfirm, AutoApply). Modern
             Altium builds (AD20+) honor DisableDialog=True and apply
             changes without a dialog. Older builds may still display the
             ECO dialog.
          3. Recompiles and reports the after-state — the delta tells you
             exactly how many components were added/removed programmatically.
          4. If counts did not change but differences existed, the response
             includes dialog_may_have_opened:true to flag that manual
             confirmation may still be needed.

        Returns:
            Dictionary with success, pcb_path, before/after mapping counts,
            components_added_to_pcb, components_removed_from_pcb, in_sync,
            and dialog_may_have_opened flag.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.update_pcb", {})
        return result

    @mcp.tool()
    async def update_schematic() -> dict[str, Any]:
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
    async def get_design_differences(
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
    async def lock_designator(
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
    async def get_project_options(
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
