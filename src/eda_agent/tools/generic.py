# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Generic primitive tools for Altium Designer MCP Server.

These primitives provide a thin, generic interface to Altium objects.
All intelligence lives in the Python/MCP side — the DelphiScript is just
a pass-through layer for object iteration, property access, and process execution.
"""

from typing import Any
from ..bridge import get_bridge
from .bulk_hints import BulkHintTracker


def register_generic_tools(mcp):
    """Register generic primitive tools with the MCP server."""

    @mcp.tool()
    async def query_objects(
        object_type: str,
        properties: str,
        scope: str = "active_doc",
        filter: str = "",
        limit: int = 0,
    ) -> dict[str, Any]:
        """Query schematic objects and read their properties.

        Iterates objects of the given type, optionally filtering by property values,
        and returns the requested properties for each matching object.

        Args:
            object_type: Altium object type constant.
                Schematic: "eNetLabel", "ePort", "ePowerObject", "eSchComponent",
                "eWire", "eBus", "eBusEntry", "eParameter", "ePin",
                "eLabel", "eLine", "eRectangle", "eSheetSymbol", "eSheetEntry", "eNoERC", "eJunction"
                PCB: "eTrackObject", "ePadObject", "eViaObject", "eComponentObject",
                "eArcObject", "eFillObject", "eTextObject", "eRuleObject", "eDimensionObject"
            properties: Comma-separated property names to return, e.g.:
                "Text,Location.X,Location.Y" for net labels
                "Designator.Text,Comment.Text,LibReference" for components
            scope: Document scope:
                "active_doc" — current sheet only (default)
                "project" — all SCH sheets in focused project
                "doc:C:\\path\\to\\Sheet.SchDoc" — specific sheet by path (no focus change)
                "project:C:\\path\\to\\Project.PrjPcb" — specific project by path
            filter: Pipe-separated property=value conditions (AND logic), e.g.:
                "Text=VCC" — match net labels with Text equal to VCC
                "Designator.Text=R1" — match component with designator R1
                "" (empty) — match all objects of the type
            limit: Maximum number of objects to return (0 = unlimited)

        Returns:
            Dictionary with "objects" array and "count"
        """
        bridge = get_bridge()
        params = {
            "scope": scope,
            "object_type": object_type,
            "filter": filter,
            "properties": properties,
        }
        if limit > 0:
            params["limit"] = str(limit)
        result = await bridge.send_command_async(
            "generic.query_objects",
            params,
        )
        return result

    @mcp.tool()
    async def modify_objects(
        object_type: str,
        set: str,
        scope: str = "active_doc",
        filter: str = "",
    ) -> dict[str, Any]:
        """Apply ONE set of property values to every object matching ONE filter.

        IMPORTANT — if every target object needs a DIFFERENT value (move 10
        pins to 10 different positions, rename 5 nets to 5 different names,
        set distinct designators per component), use `batch_modify` instead.
        Each call to this tool is a full LLM round-trip; doing that in a
        loop is the single biggest wall-time cost in the server. One
        `batch_modify` with a list of operations does the same work in one
        turn.

        Use this tool when the SAME set string applies to every match
        (e.g., "set every 10k resistor's Tolerance to 1%").

        Args:
            object_type: Altium object type constant (see query_objects)
            set: Pipe-separated property=value assignments to apply, e.g.:
                "Text=NEW_NAME" — set Text property
                "Location.X=100|Location.Y=200" — set multiple properties
            scope: Document scope:
                "active_doc" — current sheet only (default)
                "project" — all SCH sheets in focused project
                "doc:C:\\path\\to\\Sheet.SchDoc" — specific sheet by path (no focus change)
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with "matched" count and "sheets_processed"

        Example - rename a net across all sheets (one value fits all matches):
            modify_objects(
                object_type="eNetLabel",
                scope="project",
                filter="Text=OLD_NET",
                set="Text=NEW_NET"
            )
        Example - modify a specific sheet without switching:
            modify_objects(
                object_type="eParameter",
                scope="doc:C:\\path\\USB_LANBridge.SchDoc",
                filter="Name=Title",
                set="Text=USB-Ethernet Bridge"
            )
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.modify_objects",
            {
                "scope": scope,
                "object_type": object_type,
                "filter": filter,
                "set": set,
            },
        )
        hint = BulkHintTracker.record_and_hint("modify_objects")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def create_object(
        object_type: str,
        properties: str,
        container: str = "document",
    ) -> dict[str, Any]:
        """Create and place a schematic object.

        Args:
            object_type: Altium object type constant (see query_objects)
            properties: Pipe-separated property=value assignments, e.g.:
                "Text=MY_NET|Location.X=100|Location.Y=200"
            container: "document" (place on active schematic) or
                      "component" (add to current library component)

        Returns:
            Dictionary confirming creation
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.create_object",
            {
                "object_type": object_type,
                "properties": properties,
                "container": container,
            },
        )
        hint = BulkHintTracker.record_and_hint("create_object")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def delete_objects(
        object_type: str,
        scope: str = "active_doc",
        filter: str = "",
        confirm_delete_all: bool = False,
    ) -> dict[str, Any]:
        """Find and delete schematic objects.

        Args:
            object_type: Altium object type constant (see query_objects)
            scope: "active_doc" or "project"
            filter: Pipe-separated property=value conditions (AND logic).
                    WARNING: empty filter deletes ALL objects of the type.
            confirm_delete_all: Must be True to delete all objects when filter is empty.

        Returns:
            Dictionary with "matched" count (number deleted)
        """
        if not filter and not confirm_delete_all:
            return {
                "error": "Safety guard: empty filter would delete ALL objects of type "
                f"'{object_type}'. Provide a filter to select specific objects, "
                "or set confirm_delete_all=True to delete all.",
                "matched": 0,
            }
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.delete_objects",
            {
                "scope": scope,
                "object_type": object_type,
                "filter": filter,
            },
        )
        hint = BulkHintTracker.record_and_hint("delete_objects")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def get_font_spec(
        font_id: int,
    ) -> dict[str, Any]:
        """Get font properties for a given font ID.

        Reads the Altium font table to retrieve the full font specification
        (size, name, bold, italic, etc.) for a font ID obtained from an object's
        FontId property.

        Args:
            font_id: Font ID from an object's FontId property

        Returns:
            Dictionary with font_id, size, rotation, bold, italic, underline,
            strikeout, font_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_font_spec",
            {"font_id": str(font_id)},
        )
        return result

    @mcp.tool()
    async def get_font_id(
        size: int,
        font_name: str = "Arial",
        bold: bool = False,
        italic: bool = False,
        rotation: int = 0,
        underline: bool = False,
        strikeout: bool = False,
    ) -> dict[str, Any]:
        """Get or create a font ID for the given font properties.

        Looks up (or creates) an entry in the Altium font table matching the
        specified properties and returns its font ID. Use the returned font_id
        to set an object's FontId property via modify_objects.

        Args:
            size: Font size in points (e.g., 8, 10, 12)
            font_name: Font family name (default "Arial")
            bold: Whether the font is bold
            italic: Whether the font is italic
            rotation: Text rotation in degrees (0, 90, 180, 270)
            underline: Whether the font is underlined
            strikeout: Whether the font has strikeout

        Returns:
            Dictionary with font_id
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_font_id",
            {
                "size": str(size),
                "font_name": font_name,
                "bold": "true" if bold else "false",
                "italic": "true" if italic else "false",
                "rotation": str(rotation),
                "underline": "true" if underline else "false",
                "strikeout": "true" if strikeout else "false",
            },
        )
        return result

    @mcp.tool()
    async def select_objects(
        object_type: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Select objects matching a filter on the active document.

        Sets the selection state on matching schematic or PCB objects for
        visual highlighting. Use deselect_all to clear.

        Args:
            object_type: Altium object type (schematic or PCB)
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with count of selected objects
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.select_objects",
            {"object_type": object_type, "filter": filter},
        )
        return result

    @mcp.tool()
    async def deselect_all() -> dict[str, Any]:
        """Clear all object selection on the active document.

        Returns:
            Dictionary confirming deselection
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.deselect_all", {})
        return result

    @mcp.tool()
    async def zoom(action: str = "fit") -> dict[str, Any]:
        """Control the viewport zoom level.

        Args:
            action: "fit" (zoom to show all), "selection" (zoom to selected objects)

        Returns:
            Dictionary confirming the zoom action
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.zoom", {"action": action}
        )
        return result

    @mcp.tool()
    async def batch_modify(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Apply many filter+set operations in ONE IPC round-trip.

        PREFER THIS over looping `modify_objects` whenever you have more
        than one change to make — especially when each change targets a
        different object (different designator, different pin name,
        different sheet). A single `batch_modify` call touches N objects
        in the same Altium transaction; N separate `modify_objects` calls
        cost N LLM round-trips plus N IPC round-trips. The wall-time
        difference is typically 10-100x on a multi-item edit.

        Use it for: moving multiple pins to specific positions, re-laying
        component placement, per-sheet title changes, bulk designator
        rewrites, any workflow where each object gets its own value.

        Args:
            operations: List of operation dicts, each with:
                - scope: "active_doc" (default), "project", or
                  "doc:C:\\path\\to\\file.SchDoc"
                - object_type: Altium object type (e.g., "ePin", "eParameter",
                  "eSchComponent", "eNetLabel")
                - filter: Pipe-separated filter conditions
                  (e.g., "Designator.Text=U1", "Name=VDD")
                - set: Pipe-separated property=value assignments
                  (e.g., "Location.X=300|Location.Y=-100|Orientation=2")

        Returns:
            Dictionary with operations_processed count

        Example — reposition 10 pins on a library symbol in ONE call
        (vs. 10 separate modify_objects calls, each a full LLM turn):
            batch_modify(operations=[
                {"scope": "active_doc", "object_type": "ePin",
                 "filter": "Name=S1",
                 "set":    "Location.X=200|Location.Y=-100|Orientation=2"},
                {"scope": "active_doc", "object_type": "ePin",
                 "filter": "Name=S2",
                 "set":    "Location.X=200|Location.Y=-200|Orientation=2"},
                {"scope": "active_doc", "object_type": "ePin",
                 "filter": "Name=VDD",
                 "set":    "Location.X=800|Location.Y=-100|Orientation=0"},
                ... # 7 more pins
            ])

        Example — update 4 parameters across every project sheet in ONE call:
            batch_modify(operations=[
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=Engineer",     "set": "Text=John Smith"},
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=Revision",     "set": "Text=2.0"},
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=Organization", "set": "Text=Acme Corp"},
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=CompanyName",  "set": "Text=Acme Corp"},
            ])

        Example — different titles on specific sheets in ONE call:
            batch_modify(operations=[
                {"scope": "doc:C:\\path\\TopLevel.SchDoc",
                 "object_type": "eParameter",
                 "filter": "Name=Title", "set": "Text=Top Level"},
                {"scope": "doc:C:\\path\\PSU.SchDoc",
                 "object_type": "eParameter",
                 "filter": "Name=Title", "set": "Text=Power Supply"},
            ])
        """
        # Build pipe-separated operations string: scope;type;filter;set|scope;type;filter;set|...
        op_strings = []
        for op in operations:
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            filt = op.get("filter", "")
            set_str = op.get("set", "")
            if not obj_type or not set_str:
                continue
            op_strings.append(f"{scope};{obj_type};{filt};{set_str}")

        if not op_strings:
            return {"error": "No valid operations provided", "operations_processed": 0}

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.batch_modify",
            {"operations": "|".join(op_strings)},
        )
        return result

    @mcp.tool()
    async def generic_run_process(
        process_name: str,
        parameters: str = "",
    ) -> dict[str, Any]:
        """Run an Altium process command via the generic primitive layer.

        Wraps any Altium RunProcess call with structured pipe-separated parameters.

        Args:
            process_name: The Altium process identifier (e.g., "Sch:Compile",
                "WorkspaceManager:OpenObject", "PCB:Zoom")
            parameters: Optional pipe-separated key=value parameter pairs, e.g.:
                "ObjectKind=Document|FileName=C:\\path\\to\\file.SchDoc"

        Returns:
            Dictionary with execution result
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.run_process",
            {
                "process": process_name,
                "params": parameters,
            },
        )
        if isinstance(result, dict):
            return {"success": True, **result}
        elif result:
            return {"success": True, "data": result}
        else:
            return {"success": True, "process": process_name}

    @mcp.tool()
    async def run_erc() -> dict[str, Any]:
        """Run Electrical Rules Check on the focused project.

        Compiles the project first (required), then runs ERC.
        Use get_erc_violations() afterward to retrieve any violations found.

        Returns:
            Dictionary confirming ERC execution
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.run_erc", {})
        return result

    @mcp.tool()
    async def highlight_net(
        net_name: str,
        clear_existing: bool = True,
    ) -> dict[str, Any]:
        """Highlight a net by name in the active schematic or PCB document.

        PCB path sets IPCB_Net.IsHighlighted on the matched net (the
        documented property). Schematic path walks wires / net labels /
        power ports / pins / sheet entries on the active sheet and
        marks those whose NetName matches as Selection=True — the
        closest thing Altium exposes to a "highlight" on schematic
        without interactive commands.

        Args:
            net_name: Exact net name to highlight (e.g., "VCC", "GND",
                "NET_D0"). Returns NOT_FOUND if the net doesn't exist
                on the PCB, or `highlighted=0` on schematic if no
                primitive carries that net name on the active sheet
                (check other sheets).
            clear_existing: Clear existing highlights first (default True).

        Returns:
            Dict with success, net, context ("pcb" or "schematic"), and
            `highlighted` (count of matches — 1 for PCB, N for sch).
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.highlight_net",
            {
                "net_name": net_name,
                "clear_existing": "true" if clear_existing else "false",
            },
        )
        return result

    @mcp.tool()
    async def crossref_net(net_name: str) -> dict[str, Any]:
        """Compare the schematic vs PCB membership of a named net.

        Returns the full pin list the SCHEMATIC assigns to this net
        alongside the pad list the PCB assigns to the same net, plus
        the diff in each direction.

        USE THIS when:
          - `get_nets` / `get_connectivity` returns an answer that
            surprises you or the user (e.g. a net appears to be missing
            a pin you expect on it, or appears to be disconnected from
            a component you know is wired).
          - Investigating "board works but schematic says otherwise" —
            a non-empty `pcb_only` list means the PCB was fabricated
            from an older schematic revision, or an edit broke the
            post-ECO merge. `Design > Update PCB from Schematic` would
            rip up those PCB connections.
          - Debugging ECO workflow issues.

        Args:
            net_name: Exact net name as it appears in the schematic
                (case-sensitive).

        Returns:
            Dict with:
              - net_name
              - sch_pin_count, pcb_pin_count
              - matched (count of pins present on both sides)
              - sch_only_count, pcb_only_count
              - in_sync (True only when both sides list the same pins
                AND the net exists on at least one side)
              - sch_pins[], pcb_pins[] — each entry is
                "Designator.PinNumber"
              - sch_only[], pcb_only[] — the diff lists. If
                `sch_only` is non-empty the PCB is missing those
                connections. If `pcb_only` is non-empty the PCB has
                stale connections the current schematic doesn't have.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.crossref_net", {"net_name": net_name}
        )

    @mcp.tool()
    async def clear_highlights() -> dict[str, Any]:
        """Clear all net highlights in the active schematic or PCB document.

        Returns:
            Dictionary confirming highlights were cleared
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.clear_highlights", {})
        return result

    @mcp.tool()
    async def add_sheet(
        name: str = "NewSheet",
    ) -> dict[str, Any]:
        """Create a new schematic sheet and add it to the focused project.

        The sheet is created in the same directory as the project file.

        Args:
            name: Name for the new sheet (without .SchDoc extension).
                  Default "NewSheet".

        Returns:
            Dictionary with the path of the newly created sheet
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.add_sheet",
            {"name": name},
        )
        return result

    @mcp.tool()
    async def delete_sheet(
        file_path: str,
    ) -> dict[str, Any]:
        """Remove a schematic sheet from the focused project.

        Safety check: refuses to remove the last remaining schematic sheet.
        The file is closed and removed from the project but not deleted from disk.

        Args:
            file_path: Full path to the .SchDoc file to remove

        Returns:
            Dictionary confirming removal
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.delete_sheet",
            {"file_path": file_path},
        )
        return result

    @mcp.tool()
    async def switch_view(
        mode: str = "3d",
    ) -> dict[str, Any]:
        """Toggle between 2D and 3D view for PCB documents.

        Args:
            mode: Target view mode — "3d" or "2d" (default "3d")

        Returns:
            Dictionary confirming the view switch
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.switch_view",
            {"mode": mode},
        )
        return result

    @mcp.tool()
    async def refresh_document() -> dict[str, Any]:
        """Force a redraw/refresh of the current document.

        For schematics, calls GraphicallyInvalidate. For PCB, sends a
        PCB:Zoom Redraw command.

        Returns:
            Dictionary confirming the refresh
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.refresh_document", {})
        return result

    @mcp.tool()
    async def get_unconnected_pins() -> dict[str, Any]:
        """Find unconnected/floating pins in the focused project.

        Compiles the project first (required for connectivity data), then
        iterates all components via the DM API to check pin connection status.

        Returns:
            Dictionary with "count" and "unconnected_pins" array, each entry
            having designator, pin_number, pin_name, and sheet path
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_unconnected_pins", {}, timeout=60.0
        )
        return result

    @mcp.tool()
    async def place_wire(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any]:
        """Place a wire segment between two XY coordinates on the active schematic.

        Args:
            x1: Start X coordinate in mils
            y1: Start Y coordinate in mils
            x2: End X coordinate in mils
            y2: End Y coordinate in mils

        Returns:
            Dictionary confirming wire placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_wire",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        hint = BulkHintTracker.record_and_hint("place_wire")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def place_rectangle(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        solid: bool = False,
        line_width: int = 1,
    ) -> dict[str, Any]:
        """Place a rectangle shape on the active schematic (decorative only).

        Args:
            x1, y1, x2, y2: Opposite corners in mils
            solid: Fill the rectangle (default False = outline only)
            line_width: 1 (small), 2 (medium), 3 (large)

        Returns:
            Dictionary confirming rectangle placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_rectangle",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "solid": "true" if solid else "false",
                "line_width": str(line_width),
            },
        )
        return result

    @mcp.tool()
    async def place_line(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        line_width: int = 1,
    ) -> dict[str, Any]:
        """Place a decorative line on the active schematic.

        This is a graphic line, not a signal wire. Use place_wire for
        electrical connections. Use this for hand-drawn borders, arrows,
        diagram overlays.

        Args:
            x1, y1, x2, y2: Endpoints in mils
            line_width: 1 (small), 2 (medium), 3 (large)

        Returns:
            Dictionary confirming line placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_line",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "line_width": str(line_width),
            },
        )
        return result

    @mcp.tool()
    async def place_note(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        text: str,
    ) -> dict[str, Any]:
        """Place a text-box note on the active schematic.

        Useful for design commentary, TODO markers, revision notes.
        The box sizes itself to (x1,y1)-(x2,y2) and contains the
        given text.

        Args:
            x1, y1, x2, y2: Note box corners in mils
            text: Note content

        Returns:
            Dictionary confirming note placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_note",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "text": text,
            },
        )
        return result

    @mcp.tool()
    async def place_sheet_symbol(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        sheet_file_name: str,
        sheet_name: str = "",
    ) -> dict[str, Any]:
        """Place a sheet symbol linking to a child schematic document.

        Used for hierarchical designs — the parent sheet has one sheet
        symbol per child .SchDoc in the project. The sheet_file_name
        must exactly match a .SchDoc that's a project member. Without
        that match the sheet symbol is dangling.

        After placing, use place_sheet_entry (future tool) or manually
        add sheet entries corresponding to the child sheet's ports.

        Args:
            x1, y1, x2, y2: Sheet symbol box corners in mils
            sheet_file_name: Child SchDoc filename (e.g. "PSU.SchDoc")
            sheet_name: Display label (default: file name without extension)

        Returns:
            Dictionary confirming sheet symbol placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_sheet_symbol",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "sheet_file_name": sheet_file_name,
                "sheet_name": sheet_name,
            },
        )
        return result

    @mcp.tool()
    async def place_sheet_entry(
        sheet_name: str,
        entry_name: str,
        io_type: str = "unspecified",
        side: str = "left",
        distance_from_top: int = 100,
    ) -> dict[str, Any]:
        """Place a sheet entry port on an existing sheet symbol.

        Sheet entries are the ports on a sheet symbol that map to
        hierarchical ports inside the child sheet. Name must match a
        port defined in the child .SchDoc for electrical continuity.

        Args:
            sheet_name: SheetName of the target sheet symbol
            entry_name: Port name (must match a port in the child sheet)
            io_type: "input" | "output" | "bidirectional" | "unspecified"
            side: "left" | "right" | "top" | "bottom"
            distance_from_top: Position along the chosen side in mils

        Returns:
            Dictionary confirming sheet entry placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_sheet_entry",
            {
                "sheet_name": sheet_name,
                "entry_name": entry_name,
                "io_type": io_type,
                "side": side,
                "distance_from_top": str(distance_from_top),
            },
        )
        return result

    @mcp.tool()
    async def place_bus_entry(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any]:
        """Place a bus entry (45 degree stub) connecting a wire to a bus.

        Bus entries are the angled stubs that tap a single-signal wire
        off a bus line. Start point should sit on the bus; end point
        should sit on the wire (both usually at 45 degrees offset).

        Args:
            x1, y1: Start coordinates in mils (typically on the bus)
            x2, y2: End coordinates in mils (typically on the wire)

        Returns:
            Dictionary confirming bus entry placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_bus_entry",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        return result

    @mcp.tool()
    async def sch_set_sheet_size(
        style: str,
        custom_width: int = 0,
        custom_height: int = 0,
    ) -> dict[str, Any]:
        """Set the sheet size / template style of the active schematic.

        Changes SheetStyle on the current SchDoc. Use a named style
        (A-E, A0-A4, Letter, Legal, Tabloid) or "Custom" with explicit
        width and height in mils.

        Args:
            style: Sheet size name, e.g. "A", "A3", "A4", "Letter", "Custom"
            custom_width: Custom sheet width in mils (only if style="Custom")
            custom_height: Custom sheet height in mils (only if style="Custom")

        Returns:
            Dictionary confirming the style change
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.set_sheet_size",
            {
                "style": style,
                "custom_width": str(custom_width),
                "custom_height": str(custom_height),
            },
        )
        return result

    @mcp.tool()
    async def place_sch_component_from_library(
        lib_reference: str,
        x: int,
        y: int,
        library_path: str = "",
        designator: str = "",
        rotation: int = 0,
        footprint: str = "",
    ) -> dict[str, Any]:
        """Place a schematic component instance from a library at (x, y).

        Calls ISch_Document.PlaceSchComponent with the given library path
        and component name. If library_path is empty, Altium searches
        already-open libraries and the integrated library chain.

        Args:
            lib_reference: Component name inside the library (e.g. "Res1")
            x, y: Placement coordinates in mils
            library_path: Full path to .SchLib (optional if library already open)
            designator: Override designator (e.g. "R1"). Empty = keep default.
            rotation: 0, 90, 180, or 270 degrees
            footprint: Override current footprint model name (optional)

        Returns:
            Dictionary confirming component placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_sch_component_from_library",
            {
                "library_path": library_path,
                "lib_reference": lib_reference,
                "x": str(x),
                "y": str(y),
                "designator": designator,
                "rotation": str(rotation),
                "footprint": footprint,
            },
        )
        hint = BulkHintTracker.record_and_hint("place_sch_component_from_library")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def place_bus(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any]:
        """Place a bus segment between two XY coordinates on the active schematic.

        Buses carry multi-signal groups like DATA[0..7]. Place a bus line
        and then attach net labels with the bus naming syntax to mark the
        signal group. Typical workflow: bus line, bus entries at each pin,
        then connect each bus entry to a wire.

        Args:
            x1: Start X coordinate in mils
            y1: Start Y coordinate in mils
            x2: End X coordinate in mils
            y2: End Y coordinate in mils

        Returns:
            Dictionary confirming bus placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_bus",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        return result

    @mcp.tool()
    async def place_net_label(
        text: str,
        x: int,
        y: int,
        orientation: int = 0,
    ) -> dict[str, Any]:
        """Place a net label at coordinates on the active schematic.

        Args:
            text: Net name for the label (e.g., "VCC", "GND", "SDA")
            x: X coordinate in mils
            y: Y coordinate in mils
            orientation: Label rotation (0=0deg, 1=90deg, 2=180deg, 3=270deg)

        Returns:
            Dictionary confirming placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_net_label",
            {
                "text": text,
                "x": str(x),
                "y": str(y),
                "orientation": str(orientation),
            },
        )
        return result

    @mcp.tool()
    async def place_port(
        name: str,
        x: int,
        y: int,
        style: str = "right",
        io_type: str = "bidirectional",
    ) -> dict[str, Any]:
        """Place a port on the active schematic for inter-sheet connectivity.

        Args:
            name: Port name (maps to net name)
            x: X coordinate in mils
            y: Y coordinate in mils
            style: Arrow style — "none", "left", "right", "left_right"
            io_type: I/O direction — "unspecified", "output", "input", "bidirectional"

        Returns:
            Dictionary confirming placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_port",
            {
                "name": name,
                "x": str(x),
                "y": str(y),
                "style": style,
                "io_type": io_type,
            },
        )
        return result

    @mcp.tool()
    async def place_power_port(
        text: str,
        x: int,
        y: int,
        style: str = "circle",
    ) -> dict[str, Any]:
        """Place a power port symbol (VCC, GND, etc.) on the active schematic.

        Args:
            text: Net name for the power port (e.g., "VCC", "GND", "+3V3")
            x: X coordinate in mils
            y: Y coordinate in mils
            style: Symbol style:
                "circle" — circle symbol (default, typical for VCC)
                "arrow" — arrow symbol
                "bar" — bar/line symbol
                "wave" — wave symbol
                "gnd_power" — power ground symbol
                "gnd_signal" — signal ground symbol
                "gnd_earth" — earth ground symbol

        Returns:
            Dictionary confirming placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_power_port",
            {
                "text": text,
                "x": str(x),
                "y": str(y),
                "style": style,
            },
        )
        return result

    @mcp.tool()
    async def get_sheet_parameters(
        file_path: str = "",
    ) -> dict[str, Any]:
        """Get title block parameters (title, revision, date, etc.) from a schematic sheet.

        Reads all parameters from the sheet without modifying anything.

        Args:
            file_path: Full path to a specific .SchDoc file. If empty, reads
                       from the active document.

        Returns:
            Dictionary with "count" and "parameters" array, each having
            "name" and "value"
        """
        bridge = get_bridge()
        params = {}
        if file_path:
            params["file_path"] = file_path
        result = await bridge.send_command_async(
            "generic.get_sheet_parameters", params
        )
        return result

    @mcp.tool()
    async def copy_objects(
        object_type: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Copy matching schematic objects to the clipboard.

        Selects objects matching the filter, copies them to the system
        clipboard, then clears selection.

        Args:
            object_type: Altium schematic object type (see query_objects)
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with count of copied objects
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.copy_objects",
            {"object_type": object_type, "filter": filter},
        )
        return result

    @mcp.tool()
    async def get_object_count(
        object_type: str,
        scope: str = "active_doc",
        filter: str = "",
    ) -> dict[str, Any]:
        """Quick count of objects by type — faster than query_objects when you only need the count.

        Args:
            object_type: Altium object type constant (see query_objects for options)
            scope: "active_doc" (default) or "project"
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with "count" (and "sheets_processed" for project scope)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_object_count",
            {"object_type": object_type, "scope": scope, "filter": filter},
        )
        return result

    @mcp.tool()
    async def place_no_erc(
        x: int,
        y: int,
    ) -> dict[str, Any]:
        """Place a No-ERC marker at coordinates to suppress specific ERC violations.

        Use this after running ERC to suppress known-good violations at specific
        pin or wire locations.

        Args:
            x: X coordinate in mils
            y: Y coordinate in mils

        Returns:
            Dictionary confirming placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_no_erc",
            {"x": str(x), "y": str(y)},
        )
        return result

    @mcp.tool()
    async def place_junction(
        x: int,
        y: int,
    ) -> dict[str, Any]:
        """Place a wire junction at coordinates on the active schematic.

        Junctions are needed where wires cross and should connect (T or + intersections).

        Args:
            x: X coordinate in mils
            y: Y coordinate in mils

        Returns:
            Dictionary confirming placement with coordinates
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_junction",
            {"x": str(x), "y": str(y)},
        )
        return result

    @mcp.tool()
    async def get_document_info() -> dict[str, Any]:
        """Get comprehensive info about the active document.

        For schematics: file path, kind, sheet size, title block visibility,
        snap grid, visible grid, unit system, and custom dimensions.
        For PCB: file path, kind, origin, snap grid.

        Returns:
            Dictionary with document properties
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.get_document_info", {})
        return result

    @mcp.tool()
    async def set_grid(
        snap_grid: int = 0,
        visible_grid: int = 0,
    ) -> dict[str, Any]:
        """Set the snap grid and/or visible grid size for the active schematic.

        Args:
            snap_grid: Snap grid size in mils (0 = don't change)
            visible_grid: Visible grid size in mils (0 = don't change)

        Returns:
            Dictionary with the resulting grid sizes
        """
        bridge = get_bridge()
        params: dict[str, str] = {}
        if snap_grid > 0:
            params["snap_grid"] = str(snap_grid)
        if visible_grid > 0:
            params["visible_grid"] = str(visible_grid)
        result = await bridge.send_command_async("generic.set_grid", params)
        return result

    @mcp.tool()
    async def sch_set_units(
        unit: str,
    ) -> dict[str, Any]:
        """Set the unit system for the active schematic.

        Calls ISch_Document.SetState_Unit with a TUnit enum value. The
        current unit is readable via get_document_info (unit_system field).

        Args:
            unit: one of
                "mil"            — imperial, mils (0.001 in)
                "inch"           — imperial, inches
                "dxp"            — DXP default
                "auto_imperial"  — auto-scaled imperial display
                "mm"             — metric, millimetres
                "cm"             — metric, centimetres
                "m"              — metric, metres
                "auto_metric"    — auto-scaled metric display

        Returns:
            Dictionary confirming the change, with the resulting
            unit_system ("imperial" or "metric").
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.set_sch_units", {"unit": unit}
        )
        return result

    @mcp.tool()
    async def sch_add_directive(
        x: int,
        y: int,
        param_name: str,
        param_value: str,
    ) -> dict[str, Any]:
        """Place a parameter-set directive at (x, y) on the active schematic.

        A parameter-set directive attaches a single ``name=value`` design
        parameter to a wire or net. Drop the directive on top of the target
        wire/net and the compile engine will propagate the parameter. Common
        uses:

        - ``param_name="DifferentialPair", param_value="USB"``
          — marks the net as a member of the USB differential pair.
        - ``param_name="NetClass", param_value="HighSpeed"``
          — assigns the net to the HighSpeed class.
        - ``param_name="Signal_Stimulus", param_value="..."``
          — any custom per-net rule parameter.

        Args:
            x, y: Location in mils — place ON the target wire/net.
            param_name: Parameter name (e.g. "NetClass", "DifferentialPair")
            param_value: Parameter value

        Returns:
            Dictionary confirming placement.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_directive",
            {
                "x": str(x),
                "y": str(y),
                "param_name": param_name,
                "param_value": param_value,
            },
        )
        return result

    @mcp.tool()
    async def sch_get_directives() -> dict[str, Any]:
        """Enumerate parameter-set directives on the active schematic sheet.

        Returns every ISch_ParameterSet on the sheet along with its location
        and the list of parameters it carries. Use this to audit which nets
        have explicit NetClass / DifferentialPair / custom-rule assignments
        before a compile, or to verify a bulk placement worked.

        Returns:
            Dictionary with ``directives`` array — each entry has
            ``name``, ``x``, ``y``, and a ``parameters`` list of
            ``{name, value}`` objects — plus ``count``.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.get_directives", {})
        return result

    @mcp.tool()
    async def place_image(
        image_path: str,
        x: int,
        y: int,
        width: int = 500,
        height: int = 500,
    ) -> dict[str, Any]:
        """Place an image/logo on the active schematic.

        Args:
            image_path: Full path to the image file (BMP, JPG, PNG, etc.)
            x: X coordinate in mils (bottom-left corner)
            y: Y coordinate in mils (bottom-left corner)
            width: Image width in mils (default 500)
            height: Image height in mils (default 500)

        Returns:
            Dictionary confirming placement with path and dimensions
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.place_image",
            {
                "image_path": image_path,
                "x": str(x),
                "y": str(y),
                "width": str(width),
                "height": str(height),
            },
        )
        return result

    @mcp.tool()
    async def replace_component(
        designator: str,
        new_lib_ref: str,
        new_library: str = "",
    ) -> dict[str, Any]:
        """Replace a component with a different library part.

        Keeps existing connections and designator, swaps the symbol and library
        reference. The component must exist on the active schematic sheet.

        Args:
            designator: Component designator to replace (e.g., "U1", "R3")
            new_lib_ref: New library reference / component name
            new_library: New source library name (optional, keeps current if empty)

        Returns:
            Dictionary confirming the replacement
        """
        bridge = get_bridge()
        params = {
            "designator": designator,
            "new_lib_ref": new_lib_ref,
        }
        if new_library:
            params["new_library"] = new_library
        result = await bridge.send_command_async(
            "generic.replace_component", params
        )
        return result

    @mcp.tool()
    async def sch_get_constraint_groups() -> dict[str, Any]:
        """Enumerate IDocument.DM_ConstraintGroups on the active schematic.

        Constraint groups are FPGA-style pin/timing constraints attached
        to a document. Each group has a target kind and id, plus a list
        of IConstraint entries with a kind/data payload. Useful for
        auditing FPGA pin assignments and timing constraints that don't
        show up in the regular PCB design-rule list.

        Returns:
            Dict with groups (list of {target_kind, target_id,
            constraint_count, constraints[{kind, data}, ...]}) and count.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.get_constraint_groups", {}
        )

    @mcp.tool()
    async def sch_place_harness_connector(
        x: int,
        y: int,
        width: int = 500,
        height: int = 800,
        harness_type: str = "",
    ) -> dict[str, Any]:
        """Place a harness connector on the active schematic sheet.

        Harness connectors group a set of wires/buses into a named
        signal bundle, letting cross-sheet connections be drawn as a
        single line instead of a bus.

        Args:
            x, y: Bottom-left corner in mils.
            width, height: Connector rectangle size in mils.
            harness_type: Named harness type (optional).

        Returns:
            Dict with success, x, y, width, height, harness_type.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x": str(x), "y": str(y),
            "width": str(width), "height": str(height),
        }
        if harness_type:
            params["harness_type"] = harness_type
        return await bridge.send_command_async(
            "generic.place_harness_connector", params
        )

    @mcp.tool()
    async def sch_place_cross_sheet_connector(
        x: int,
        y: int,
        net: str = "",
        side: str = "",
    ) -> dict[str, Any]:
        """Place a cross-sheet connector (off-sheet port) on the active sheet.

        Cross-sheet connectors are the hierarchical equivalent of a net
        label — they connect a signal to the same net name on another
        sheet.

        Args:
            x, y: Connector location in mils.
            net: Net name the connector binds to.
            side: "left" or "right" orientation (optional).

        Returns:
            Dict with success, x, y, net.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"x": str(x), "y": str(y)}
        if net:
            params["net"] = net
        if side:
            params["side"] = side
        return await bridge.send_command_async(
            "generic.place_cross_sheet_connector", params
        )

    @mcp.tool()
    async def sch_set_component_part_id(
        designator: str,
        part_id: int,
    ) -> dict[str, Any]:
        """Switch the active sub-part on a multi-part schematic component.

        Multi-gate parts like quad op-amps expose one sub-part per gate
        (U1A, U1B, U1C, U1D). CurrentPartID selects which one this
        symbol instance represents. IDs are 1-based.

        Args:
            designator: Component reference (e.g., "U1").
            part_id: Sub-part index, 1-based (1=A, 2=B, ...).

        Returns:
            Dict with success, designator, part_id.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_component_part_id",
            {"designator": designator, "part_id": str(part_id)},
        )

    @mcp.tool()
    async def sch_place_probe(
        x: int,
        y: int,
        net_name: str = "",
        probe_method: str = "probed_nets_only",
    ) -> dict[str, Any]:
        """Place a probe/measurement marker on the active schematic.

        Probes mark nodes for SPICE/simulation output. Attach one to a
        wire to declare "capture voltage / current at this point" in
        simulation runs.

        Args:
            x, y: Probe location in mils.
            net_name: Optional explicit net label text.
            probe_method: "probed_nets_only" (default) or "all_nets".

        Returns:
            Dict with success, x, y, net_name.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_probe",
            {
                "x": str(x), "y": str(y),
                "net_name": net_name,
                "probe_method": probe_method,
            },
        )

    @mcp.tool()
    async def sch_add_datafile_link(
        designator: str,
        file_path: str,
        kind: str = "",
    ) -> dict[str, Any]:
        """Attach a datafile link to a component's current implementation.

        Datafile links are how parametric data (IBIS model files, sim
        models, external CSVs) is bound to a schematic part.

        Args:
            designator: Component reference (e.g., "U1").
            file_path: Full path to the file being linked.
            kind: Optional implementation-specific type
                (e.g., "SimModel", "IBIS").

        Returns:
            Dict with success, designator, file_path.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "designator": designator,
            "file_path": file_path,
        }
        if kind:
            params["kind"] = kind
        return await bridge.send_command_async(
            "generic.add_datafile_link", params
        )

    # --------------------------------------------------------------
    # Batch tools using the '~~'-separator format.
    # --------------------------------------------------------------

    @mcp.tool()
    async def batch_create(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create many schematic objects in ONE IPC round-trip.

        PREFER THIS over looping `create_object`. Each create costs one
        LLM turn when done one at a time; batched it's a single
        PreProcess/PostProcess + one save for the whole set.

        Args:
            operations: List of create dicts, each with:
                - scope: "active_doc" (default) — only active_doc is
                  currently honored for creates.
                - object_type: Altium type name (e.g. "eNetLabel",
                  "eJunction", "eNoERC").
                - properties: pipe-separated ``Name=Value`` list, same
                  format as ``create_object`` accepts.
                - container: "document" (default) or "component" (for
                  library-symbol contents when a lib is active).

        Example — drop 3 net labels in one call:
            batch_create(operations=[
                {"object_type": "eNetLabel",
                 "properties": "Text=VCC|Location.X=100|Location.Y=200"},
                {"object_type": "eNetLabel",
                 "properties": "Text=GND|Location.X=100|Location.Y=400"},
                {"object_type": "eNetLabel",
                 "properties": "Text=SCK|Location.X=300|Location.Y=200"},
            ])

        Returns:
            Dict with created, failed, total counts.
        """
        op_strs: list[str] = []
        for op in operations:
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            props = op.get("properties", "")
            container = op.get("container", "")
            if not obj_type:
                continue
            fields = [
                f"scope={scope}",
                f"object_type={obj_type}",
                f"properties={props}",
            ]
            if container:
                fields.append(f"container={container}")
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid operations", "created": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.batch_create",
            {"operations": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def batch_delete(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Delete matching objects across many scope/type/filter operations.

        PREFER THIS over looping `delete_objects`. Each op is evaluated
        in one go — cleaning a mixed set of stale junctions, no-ERCs,
        and net labels costs one IPC round-trip instead of N.

        Args:
            operations: List of delete dicts, each with:
                - scope: "active_doc" (default), "project", or
                  "doc:<absolute_path>".
                - object_type: Altium type name (e.g. "eJunction",
                  "eNoERC", "eWire").
                - filter: pipe-separated ``PropName=Value`` filter
                  conditions (AND logic), same format as
                  ``delete_objects``.

        Example — purge all no-ERCs on a specific sheet and every
        junction on the project:
            batch_delete(operations=[
                {"scope": "doc:C:\\proj\\Power.SchDoc",
                 "object_type": "eNoERC", "filter": ""},
                {"scope": "project",
                 "object_type": "eJunction", "filter": ""},
            ])

        Returns:
            Dict with operations_processed and total.
        """
        op_strs: list[str] = []
        for op in operations:
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            filt = op.get("filter", "")
            if not obj_type:
                continue
            op_strs.append(
                f"scope={scope};object_type={obj_type};filter={filt}"
            )

        if not op_strs:
            return {"error": "No valid operations", "operations_processed": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.batch_delete",
            {"operations": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def place_wires(
        wires: list[dict[str, int]],
    ) -> dict[str, Any]:
        """Place MANY wire segments on the active schematic in ONE call.

        PREFER THIS over looping `place_wire`. Wiring up a netlist is
        inherently N pairs of endpoints; the bulk version is 10-100x
        faster in wall time because the whole batch shares one
        PreProcess/PostProcess and one redraw.

        Args:
            wires: List of wire dicts, each with x1, y1, x2, y2 in mils.

        Example — a 3-segment L-shaped bus routing:
            place_wires(wires=[
                {"x1": 100, "y1": 200, "x2": 300, "y2": 200},
                {"x1": 300, "y1": 200, "x2": 300, "y2": 400},
                {"x1": 300, "y1": 400, "x2": 600, "y2": 400},
            ])

        Returns:
            Dict with placed, failed, total counts.
        """
        op_strs: list[str] = []
        for w in wires:
            op_strs.append(
                f"x1={int(w.get('x1', 0))};y1={int(w.get('y1', 0))};"
                f"x2={int(w.get('x2', 0))};y2={int(w.get('y2', 0))}"
            )
        if not op_strs:
            return {"error": "No wires provided", "placed": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_wires",
            {"wires": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def place_sch_components_from_library(
        placements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place MANY schematic components from libraries in ONE call.

        PREFER THIS over looping `place_sch_component_from_library`.
        Laying out a 50-part BOM is inherently a bulk operation;
        done one-by-one it costs 50 LLM turns.

        Args:
            placements: List of placement dicts, each with:
                - library_path (str, optional — empty uses an
                  already-open library)
                - lib_reference (str, required) — component name
                - x, y (int, mils) — placement location
                - designator (str, optional) — override designator
                - rotation (int, optional) — 0 / 90 / 180 / 270
                - footprint (str, optional) — override current footprint

        Example — place a 5-part BOM row:
            place_sch_components_from_library(placements=[
                {"library_path": "C:\\Lib\\ST.SchLib",
                 "lib_reference": "STM32F411RE",
                 "x": 1000, "y": 2000, "designator": "U1"},
                {"library_path": "C:\\Lib\\Res.SchLib",
                 "lib_reference": "Res1", "x": 1500, "y": 2000,
                 "designator": "R1"},
                ... # 3 more
            ])

        Returns:
            Dict with placed, failed, total counts.
        """
        op_strs: list[str] = []
        for p in placements:
            lib_ref = str(p.get("lib_reference", "")).strip()
            if not lib_ref:
                continue
            fields = [
                f"library_path={p.get('library_path', '')}",
                f"lib_reference={lib_ref}",
                f"x={int(p.get('x', 0))}",
                f"y={int(p.get('y', 0))}",
                f"rotation={int(p.get('rotation', 0))}",
            ]
            if p.get("designator"):
                fields.append(f"designator={p['designator']}")
            if p.get("footprint"):
                fields.append(f"footprint={p['footprint']}")
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid placements", "placed": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_sch_components_from_library",
            {"placements": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def sch_attach_spice_primitives(
        attachments: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Attach SPICE primitives to MANY components in ONE call.

        PREFER THIS after running `sch_get_simulation_readiness` — the
        readiness response typically lists 20-50 passives that all
        need the SpicePrefix + Value parameter pair. Looping
        `sch_attach_spice_primitive` costs one LLM turn per component;
        this tool does the whole set in one round-trip.

        Args:
            attachments: List of attach dicts, each with:
                - designator (str, required)
                - primitive  (str, required) — R/L/C/V/I/D/Q/M/X
                - value      (str, optional) — "10k", "100n",
                  "DC 5", "SIN(0 1 1k)", etc.
                - spice_model (str, optional) — model name for
                  semi/sub-circuit parts
                - sim_kind   (str, optional) — "General" /
                  "Subcircuit" / "Model"

        Example — attach the 4 passives from a readiness audit:
            sch_attach_spice_primitives(attachments=[
                {"designator": "R1", "primitive": "R", "value": "10k"},
                {"designator": "R2", "primitive": "R", "value": "10k"},
                {"designator": "C1", "primitive": "C", "value": "100n"},
                {"designator": "C2", "primitive": "C", "value": "1u"},
            ])

        Returns:
            Dict with attached, failed, total counts.
        """
        op_strs: list[str] = []
        for a in attachments:
            desig = str(a.get("designator", "")).strip()
            prim = str(a.get("primitive", "")).strip().upper()
            if not desig or not prim:
                continue
            fields = [f"designator={desig}", f"primitive={prim}"]
            if a.get("value"):
                fields.append(f"value={a['value']}")
            if a.get("spice_model"):
                fields.append(f"spice_model={a['spice_model']}")
            if a.get("sim_kind"):
                fields.append(f"sim_kind={a['sim_kind']}")
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid attachments", "attached": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.attach_spice_primitives",
            {"attachments": "~~".join(op_strs)},
        )
