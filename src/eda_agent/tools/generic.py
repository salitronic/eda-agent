# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Generic primitive tools for Altium Designer MCP Server.

These primitives provide a thin, generic interface to Altium objects.
All intelligence lives in the Python/MCP side, the DelphiScript is just
a pass-through layer for object iteration, property access, and process execution.
"""

from typing import Any, Optional
from ..bridge.payload import payload_safe
from ..bridge import get_bridge
from ..scope import to_wire as scope_to_wire
from .bulk_hints import BulkHintTracker


def register_generic_tools(mcp):
    """Register generic primitive tools with the MCP server."""

    @mcp.tool()
    async def obj_query(
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
                "Designator,Name,ConnectionX,ConnectionY" for pins.
                ConnectionX/ConnectionY are the pin's ELECTRICAL end --
                the point a wire or net label must sit on to attach.
                Location is the BODY-side root and is NOT the connection
                point; deriving the end by hand from Orientation and
                PinLength is a common source of wrong conclusions about
                what is wired.
                "Designator.Text,Comment.Text,LibReference" for components
            scope: Document scope:
                "active_doc", current sheet only (default)
                "project", all SCH sheets in focused project
                "doc:C:\\path\\to\\Sheet.SchDoc", specific sheet by path (no focus change)
                "project:C:\\path\\to\\Project.PrjPcb", specific project by path
                "lib_component:NAME", a named symbol in the active SchLib
                "lib_component:NAME@3", part 3 of a MULTI-PART symbol.
                    A SchLib iterator yields one part at a time, so
                    without the suffix only part 1 is visible and a pin
                    on part 3 reports matched:0. Query each part in
                    turn to cover the whole component. The suffix is
                    read from the RIGHT, so a LibRef containing '@'
                    still works.
                    Ask for the `OwnerPartId` property to see which
                    part a returned primitive belongs to (0 means
                    shared across every part).
            filter: Pipe-separated property=value conditions (AND logic), e.g.:
                "Text=VCC", match net labels with Text equal to VCC
                "Designator.Text=R1", match component with designator R1
                "" (empty), match all objects of the type
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
    async def obj_explain_pin(designator: str, pin: str) -> dict[str, Any]:
        """Explain WHY a schematic pin is on the net it is on.

        ``proj_get_nets`` gives the verdict but never the evidence, so a
        surprising net assignment can only be debugged by reconstructing
        Altium's connectivity by hand from raw geometry. This returns the
        evidence directly.

        Reports the pin's ROOT (``Pin.Location``, body side) and its
        CONNECTION point (``Location + PinLength`` along ``Orientation``),
        then lists every schematic object sitting on each point.

        Reading the result:

        - ``at_connection`` is what actually drives the pin's net. Two net
          labels with different ``text`` there is a short between two named
          nets, and the usual symptom is one of them reporting zero pins.
        - ``at_root`` is electrically INERT. A net label there is the
          classic "sheet looks wired but the pin floats on an auto-net" bug.
        - An empty ``at_connection`` with a populated ``at_root`` means the
          label must move by ``pin_length_mils`` along the pin's
          orientation; the target is given as ``connect_x_mils`` /
          ``connect_y_mils``.

        Hits are filtered by the object's Location (or a pin's electrical
        end, or a wire vertex), not by bounding box. Altium's
        ``AddFilter_Area`` matches text boxes, which extend hundreds of
        mils from a net label; without the Location check this tool
        reports labels that are merely nearby.

        Args:
            designator: Component designator, e.g. "U10".
            pin: Pin NUMBER (not pin name), e.g. "5".

        Returns:
            Dict with ``{designator, pin, pin_name, sheet, orientation,
            pin_length_mils, root_x_mils, root_y_mils, connect_x_mils,
            connect_y_mils, compiled_net, at_connection[], at_root[]}``.
            Each object entry carries ``{kind, text, x_mils, y_mils}``
            where kind is one of net_label, power_object, port, wire,
            junction, pin, other. ``compiled_net`` is the pin's net from
            the compiled netlist, so geometry and compile can be compared
            in one response.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.explain_pin",
            {"designator": designator, "pin": pin},
        )
        if isinstance(result, dict) and "error" not in result:
            try:
                conn = await bridge.send_command_async(
                    "project.get_connectivity",
                    {"designator": designator},
                )
                for p in conn.get("pins", []) if isinstance(conn, dict) else []:
                    if str(p.get("pin_number")) == str(pin):
                        result["compiled_net"] = p.get("net")
                        break
            except Exception:
                pass
        return result

    @mcp.tool()
    async def obj_modify(
        object_type: str,
        set: str,
        scope: str = "active_doc",
        filter: str = "",
    ) -> dict[str, Any]:
        """Apply ONE set of property values to every object matching ONE filter.

        IMPORTANT, if every target object needs a DIFFERENT value (move 10
        pins to 10 different positions, rename 5 nets to 5 different names,
        set distinct designators per component), use `obj_batch_modify` instead.
        Each call to this tool is a full LLM round-trip; doing that in a
        loop is the single biggest wall-time cost in the server. One
        `obj_batch_modify` with a list of operations does the same work in one
        turn.

        Use this tool when the SAME set string applies to every match
        (e.g., "set every 10k resistor's Tolerance to 1%").

        Args:
            object_type: Altium object type constant (see `obj_query`)
            set: Pipe-separated property=value assignments to apply, e.g.:
                "Text=NEW_NAME", set Text property
                "Location.X=100|Location.Y=200", set multiple properties
            scope: Document scope:
                "active_doc", current sheet only (default)
                "project", all SCH sheets in focused project
                "doc:C:\\path\\to\\Sheet.SchDoc", specific sheet by path (no focus change)
                "lib_component:NAME", a named symbol in the active SchLib
                    -- selects that component first, so there is no need
                    for a separate lib_set_current_component call
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with "matched" count and "sheets_processed"

        Example - rename a net across all sheets (one value fits all matches):
            obj_modify(
                object_type="eNetLabel",
                scope="project",
                filter="Text=OLD_NET",
                set="Text=NEW_NET"
            )
        Example - modify a specific sheet without switching:
            obj_modify(
                object_type="eParameter",
                scope="doc:C:\\path\\USB_LANBridge.SchDoc",
                filter="Name=Title",
                set="Text=USB-Ethernet Bridge"
            )
        Example - edit a pin inside one SchLib symbol (no lib_set_current_component):
            obj_modify(
                object_type="ePin",
                scope="lib_component:R_0402",
                filter="Designator=1",
                set="Name=A"
            )
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.modify_objects",
            {
                "scope": scope_to_wire(scope),
                "object_type": object_type,
                "filter": filter,
                "set": set,
            },
        )
        hint = BulkHintTracker.record_and_hint("obj_modify")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def obj_create(
        object_type: str,
        properties: str,
        container: str = "document",
    ) -> dict[str, Any]:
        """Create and place a schematic object.

        If you are creating more than one object, use `obj_batch_create`
        instead, one IPC round-trip for the whole set vs one LLM turn
        per object.

        Args:
            object_type: Altium object type constant (see `obj_query`)
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
        hint = BulkHintTracker.record_and_hint("obj_create")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def obj_delete(
        object_type: str,
        scope: str = "active_doc",
        filter: str = "",
        confirm_delete_all: bool = False,
    ) -> dict[str, Any]:
        """Find and delete schematic objects.

        For several scope/type/filter sets at once, use `obj_batch_delete`
       , one IPC round-trip vs one LLM turn per delete.

        Args:
            object_type: Altium object type constant (see `obj_query`)
            scope: "active_doc", "project", "doc:PATH", or
                "lib_component:NAME" (a named symbol in the active SchLib)
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
                "scope": scope_to_wire(scope),
                "object_type": object_type,
                "filter": filter,
            },
        )
        hint = BulkHintTracker.record_and_hint("obj_delete")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def obj_get_font_spec(
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
    async def obj_get_font_id(
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
        to set an object's FontId property via `obj_modify`.

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
    async def obj_select(
        object_type: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Select objects matching a filter on the active document.

        Sets the selection state on matching schematic or PCB objects for
        visual highlighting. Use `obj_deselect_all` to clear.

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
    async def obj_deselect_all() -> dict[str, Any]:
        """Clear all object selection on the active document.

        Returns:
            Dictionary confirming deselection
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.deselect_all", {})
        return result

    @mcp.tool()
    async def obj_zoom(action: str = "fit") -> dict[str, Any]:
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
    async def obj_batch_modify(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Apply many filter+set operations in ONE IPC round-trip.

        PREFER THIS over looping `obj_modify` whenever you have more
        than one change to make, especially when each change targets a
        different object (different designator, different pin name,
        different sheet). A single `obj_batch_modify` call touches N objects
        in the same Altium transaction; N separate `obj_modify` calls
        cost N LLM round-trips plus N IPC round-trips. The wall-time
        difference is typically 10-100x on a multi-item edit.

        Use it for: moving multiple pins to specific positions, re-laying
        component placement, per-sheet title changes, bulk designator
        rewrites, any workflow where each object gets its own value.

        Args:
            operations: List of operation dicts, each with:
                - scope: "active_doc" (default), "project",
                  "doc:C:\\path\\to\\file.SchDoc", or
                  "lib_component:NAME" -- a named symbol in the active
                  SchLib. Mixing lib_component scopes across ops edits
                  many library symbols in ONE call, no per-symbol
                  lib_set_current_component round-trip.
                - object_type: Altium object type (e.g., "ePin", "eParameter",
                  "eSchComponent", "eNetLabel")
                - filter: Pipe-separated filter conditions
                  (e.g., "Designator.Text=U1", "Name=VDD")
                - set: Pipe-separated property=value assignments
                  (e.g., "Location.X=300|Location.Y=-100|Orientation=2")

        Returns:
            Dictionary with operations_processed, operations_skipped,
            total_matched, and a per-operation `results` list. Each result
            row carries its own `matched` count plus a `note` -- an op that
            matched nothing reports note="no_objects_matched" instead of
            being silently counted as successful.

        Example, edit pins across THREE different library symbols in ONE call:
            obj_batch_modify(operations=[
                {"scope": "lib_component:R_0402", "object_type": "ePin",
                 "filter": "Designator=1", "set": "Name=A"},
                {"scope": "lib_component:C_0402", "object_type": "ePin",
                 "filter": "Designator=1", "set": "Name=+"},
                {"scope": "lib_component:LED_0603", "object_type": "ePin",
                 "filter": "Designator=1", "set": "Name=K"},
            ])

        Example, reposition 10 pins on a library symbol in ONE call
        (vs. 10 separate `obj_modify` calls, each a full LLM turn):
            obj_batch_modify(operations=[
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

        Example, update 4 parameters across every project sheet in ONE call:
            obj_batch_modify(operations=[
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=Engineer",     "set": "Text=John Smith"},
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=Revision",     "set": "Text=2.0"},
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=Organization", "set": "Text=Acme Corp"},
                {"scope": "project", "object_type": "eParameter",
                 "filter": "Name=CompanyName",  "set": "Text=Acme Corp"},
            ])

        Example, different titles on specific sheets in ONE call:
            obj_batch_modify(operations=[
                {"scope": "doc:C:\\path\\TopLevel.SchDoc",
                 "object_type": "eParameter",
                 "filter": "Name=Title", "set": "Text=Top Level"},
                {"scope": "doc:C:\\path\\PSU.SchDoc",
                 "object_type": "eParameter",
                 "filter": "Name=Title", "set": "Text=Power Supply"},
            ])
        """
        # Operations use the '~~' batch framing shared with obj_batch_delete.
        # The old framing joined operations with '|' -- the SAME character
        # that separates conditions inside `filter` and assignments inside
        # `set`. Any op with a two-condition filter or a two-property set was
        # torn into fragments on the Altium side and the fragments after the
        # first were dropped without a trace (observed: 4 eSchComponent ops
        # followed by 8 eNetLabel ops reported operations_processed=4).
        # '~~' cannot occur in an Altium name, filter, or property string.
        op_strings = []
        for op in operations:
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            filt = op.get("filter", "")
            set_str = op.get("set", "")
            if not obj_type or not set_str:
                continue
            op_strings.append(
                f"scope={scope};object_type={obj_type};"
                f"filter={filt};set={set_str}"
            )

        if not op_strings:
            return {"error": "No valid operations provided", "operations_processed": 0}

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.batch_modify",
            {"operations": "~~".join(op_strings)},
        )
        return result

    @mcp.tool()
    async def obj_run_process(
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
    async def proj_run_erc() -> dict[str, Any]:
        """Run Electrical Rules Check on the focused project.

        Compiles the project first (required), then runs ERC.
        Call ``proj_get_erc_violations()`` afterwards to retrieve the
        structured list of violations.

        Returns:
            Dictionary confirming ERC execution
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.run_erc", {})
        return result

    @mcp.tool()
    async def sch_set_components_parameters(
        stamps: list[dict[str, str]],
        sheet_path: str = "",
    ) -> dict[str, Any]:
        """Bulk-update parameters on PLACED schematic components.

        Single IPC call wraps N component updates inside one
        PreProcess/PostProcess undo group. Avoids the 10-100x slowdown
        of looping the singular variant. The canonical follow-up tool
        after audits that flag parameter-level bugs --
        ``audit_find_visible_supplier_pn``,
        ``audit_find_placeholder_values``,
        ``audit_find_mpn_inconsistencies`` -- recommend it.

        Each stamp targets one component by designator and sets one
        or more parameters. Special keys allowed:
          - ``Value``, ``Comment``, ``Manufacturer``, ``MPN``,
            ``Footprint``, ``Description``, ``HelpURL``, etc.
          - Custom parameters: any key name. Unknown parameters are
            CREATED if they don't already exist on the component.

        Args:
            stamps: List of dicts. Each dict must include ``designator``
                and one or more parameter key/value pairs. Example:
                ``[{"designator": "R1", "Value": "10k",
                "Manufacturer": "Yageo", "MPN": "RC0603FR-0710KL"},
                {"designator": "C5", "Value": "100nF"}]``
            sheet_path: Optional explicit SchDoc path. Empty uses the
                currently-focused schematic.

        Returns:
            Dict with ``{updated, failed, total}``. ``updated`` is the
            count of components whose parameters were touched;
            ``failed`` is the count of stamps that couldn't be applied
            (designator not found, parameter not settable, etc).
        """
        # Encode the list-of-dicts into the Pascal handler's expected
        # wire format: stamps separated by `~~`, fields within a stamp
        # separated by `;`, key=value pairs.
        if not stamps:
            return {"ok": False, "reason": "stamps list is empty"}
        encoded_stamps = []
        for stamp in stamps:
            if "designator" not in stamp:
                return {"ok": False,
                        "reason": f"stamp missing 'designator': {stamp}"}
            # The designator was interpolated RAW here while every other
            # value was escaped, so the one field guaranteed to come
            # from the caller was the one that could reshape the
            # payload. Both go through the shared sanitiser now; the
            # local copy also collapsed "~~" differently from
            # library.py, so identical input produced different output
            # depending on which tool sent it.
            fields = [f"designator={payload_safe(stamp['designator'])}"]
            for key, val in stamp.items():
                if key == "designator":
                    continue
                fields.append(f"{key}={payload_safe(val)}")
            encoded_stamps.append(";".join(fields))
        params: dict[str, str] = {"stamps": "~~".join(encoded_stamps)}
        if sheet_path:
            params["sheet_path"] = sheet_path
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_sch_components_parameters", params, timeout=60.0)

    @mcp.tool()
    async def proj_get_erc_violations() -> dict[str, Any]:
        """Return the ERC violation list produced by the last
        ``proj_run_erc()`` invocation.

        Two-step pattern by design: ``proj_run_erc()`` performs the
        compile-and-check (slow, can be 30+ s on a large project);
        this tool reads back the violations cheaply so the agent can
        re-query without re-running ERC.

        Each violation names the OBJECTS it is about, not just a
        category. ``related_objects`` carries one entry per offending
        object with its ``kind`` (Pin, Net, Port), its ``document``, and
        a ``cross_probe`` string, which is what Altium itself uses to
        jump to that exact object and is therefore what identifies the
        specific pin or net.

        That detail is the difference between a report and something
        actionable. A category plus a sheet name cannot be acted on: the
        only safe response to "floating input pin, somewhere on this
        sheet" is to do nothing, because a NoERC marker placed by
        guesswork silently suppresses a real disconnection and is worse
        than the warning it clears.

        ``related_object_count`` is reported separately, so a violation
        that genuinely exposes no objects is distinguishable from one
        whose objects could not be read.

        Returns:
            ``{"violation_count", "violations": [{"index",
            "description", "detail", "related_object_count",
            "related_objects": [{"kind", "document", "cross_probe"}]}]}``
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.get_erc_violations", {})

    @mcp.tool()
    async def obj_highlight_net(
        net_name: str,
        clear_existing: bool = True,
        context: str = "",
    ) -> dict[str, Any]:
        """Highlight a net by name in the focused schematic or PCB document.

        PCB path sets IPCB_Net.IsHighlighted on the matched net (the
        documented property). Schematic path walks net labels / power
        ports / ports / sheet entries on the active sheet and marks
        those whose name matches as Selection=True, the closest thing
        Altium exposes to a "highlight" on schematic without interactive
        commands.

        If a schematic is the focused document, this paints the
        schematic even when a PcbDoc is also open. Pass
        ``context="pcb"`` or ``context="schematic"`` to force one side.
        The previous behaviour always preferred the PCB whenever any
        board was loaded, which made a schematic Find-Net look like it
        did nothing.

        Args:
            net_name: Exact net name to highlight (e.g., "VCC", "GND",
                "NET_D0"). Returns NOT_FOUND if the net doesn't exist
                on the PCB, or `highlighted=0` on schematic if no
                primitive carries that net name on the active sheet
                (check other sheets).
            clear_existing: Clear existing highlights first (default True).
            context: Empty (default, follow the focused document),
                "schematic", or "pcb".

        Returns:
            Dict with success, net, context ("pcb" or "schematic"), and
            `highlighted` (count of matches, 1 for PCB, N for sch).
        """
        bridge = get_bridge()
        params = {
            "net_name": net_name,
            "clear_existing": "true" if clear_existing else "false",
        }
        if context:
            params["context"] = context
        result = await bridge.send_command_async(
            "generic.highlight_net",
            params,
        )
        return result

    @mcp.tool()
    async def obj_crossref_net(net_name: str) -> dict[str, Any]:
        """Compare the schematic vs PCB membership of a named net.

        Returns the full pin list the SCHEMATIC assigns to this net
        alongside the pad list the PCB assigns to the same net, plus
        the diff in each direction.

        USE THIS when:
          - `proj_get_nets` / `proj_get_connectivity` returns an answer that
            surprises you or the user (e.g. a net appears to be missing
            a pin you expect on it, or appears to be disconnected from
            a component you know is wired).
          - Investigating "board works but schematic says otherwise":
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
              - sch_pins[], pcb_pins[], each entry is
                "Designator.PinNumber"
              - sch_only[], pcb_only[], the diff lists. If
                `sch_only` is non-empty the PCB is missing those
                connections. If `pcb_only` is non-empty the PCB has
                stale connections the current schematic doesn't have.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.crossref_net", {"net_name": net_name}
        )

    @mcp.tool()
    async def obj_clear_highlights() -> dict[str, Any]:
        """Clear all net highlights in the active schematic or PCB document.

        Returns:
            Dictionary confirming highlights were cleared
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.clear_highlights", {})
        return result

    @mcp.tool()
    async def proj_add_sheet(
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
    async def proj_delete_sheet(
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
    async def obj_switch_view(
        mode: str = "3d",
    ) -> dict[str, Any]:
        """Toggle between 2D and 3D view for PCB documents.

        Args:
            mode: Target view mode, "3d" or "2d" (default "3d")

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
    async def obj_refresh_document() -> dict[str, Any]:
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
    async def proj_get_unconnected_pins() -> dict[str, Any]:
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
    async def sch_stub_pins(
        stub_length_mils: int = 100,
        only_designators: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Draw a short wire stub + net label on every unconnected schematic pin.

        Finds floating pins (via the same connectivity check as
        `proj_get_unconnected_pins`), then for each one places a wire extending from
        the pin's electrical end outward along its orientation, terminated by a
        net label named "<designator>_<pin_number>". This makes dangling pins
        explicit so ERC reports them as named single-pin nets rather than silent
        floats, and gives a labelled anchor to wire up later.

        Args:
            stub_length_mils: Length of each stub wire in mils (default 100)
            only_designators: If given, only stub pins on these components

        Returns:
            Dictionary with "stubbed" and "failed" counts, plus the pin list
            that was acted on
        """
        bridge = get_bridge()
        floating = await bridge.send_command_async(
            "generic.get_unconnected_pins", {}, timeout=60.0
        )
        pins = floating.get("unconnected_pins", []) if isinstance(floating, dict) else []
        if only_designators:
            wanted = {d.upper() for d in only_designators}
            pins = [p for p in pins if str(p.get("designator", "")).upper() in wanted]
        if not pins:
            return {"stubbed": 0, "failed": 0, "pins": [], "note": "no unconnected pins found"}

        records = []
        for p in pins:
            desig = str(p.get("designator", "")).strip()
            pin_num = str(p.get("pin_number", "")).strip()
            if not desig or not pin_num:
                continue
            label = f"{desig}_{pin_num}"
            records.append(f"{desig},{pin_num},{label}")

        result = await bridge.send_command_async(
            "generic.stub_pins",
            {"pins": "|".join(records), "stub_length_mils": str(stub_length_mils)},
            timeout=120.0,
        )
        if isinstance(result, dict):
            result["pins"] = records
        return result

    @mcp.tool()
    async def sch_place_rectangle(
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
    async def sch_place_line(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        line_width: int = 1,
    ) -> dict[str, Any]:
        """Place a decorative line on the active schematic.

        This is a graphic line, not a signal wire. Use `sch_place_wires`
        for electrical connections. Use this for hand-drawn borders,
        arrows, diagram overlays.

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
    async def sch_place_note(
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
    async def sch_place_sheet_symbol(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        sheet_file_name: str,
        sheet_name: str = "",
    ) -> dict[str, Any]:
        """Place a sheet symbol linking to a child schematic document.

        Used for hierarchical designs, the parent sheet has one sheet
        symbol per child .SchDoc in the project. The sheet_file_name
        must exactly match a .SchDoc that's a project member. Without
        that match the sheet symbol is dangling.

        After placing, use `sch_place_sheet_entry` to add sheet entries
        corresponding to the child sheet's ports.

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
    async def sch_place_sheet_entry(
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
    async def sch_place_bus_entry(
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
    async def sch_place_bus(
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
    async def sch_place_net_label(
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
    async def sch_place_port(
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
            style: Arrow style, "none", "left", "right", "left_right"
            io_type: I/O direction, "unspecified", "output", "input", "bidirectional"

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
    async def sch_place_power_port(
        text: str,
        x: int,
        y: int,
        style: str = "circle",
        orientation: int = -1,
    ) -> dict[str, Any]:
        """Place a power port symbol (VCC, GND, etc.) on the active schematic.

        Args:
            text: Net name for the power port (e.g., "VCC", "GND", "+3V3")
            x: X coordinate in mils
            y: Y coordinate in mils
            style: Symbol style:
                "circle", circle symbol (default, typical for VCC)
                "arrow", arrow symbol
                "bar", bar/line symbol
                "wave", wave symbol
                "gnd_power", power ground symbol
                "gnd_signal", signal ground symbol
                "gnd_earth", earth ground symbol
            orientation: which way the glyph points from the connection
                point: 0=right, 1=up, 2=left, 3=down. Leave at -1 to let
                the bridge decide by style, which sends the ground
                glyphs down and everything else up.

                PASS IT EXPLICITLY FOR A RAIL DRAWN WITH style="bar".
                The style-based default groups "bar" and "wave" with the
                grounds, so a VCC bar comes out pointing DOWN and reads
                as a ground symbol. orientation=1 is what you want there.

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
                "orientation": str(int(orientation)),
            },
        )
        return result

    @mcp.tool()
    async def sch_get_sheet_parameters(
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
    async def obj_copy(
        object_type: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Copy matching schematic objects to the clipboard.

        Selects objects matching the filter, copies them to the system
        clipboard, then clears selection.

        Args:
            object_type: Altium schematic object type (see `obj_query`)
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
    async def obj_count(
        object_type: str,
        scope: str = "active_doc",
        filter: str = "",
    ) -> dict[str, Any]:
        """Quick count of objects by type, faster than `obj_query` when you only need the count.

        Args:
            object_type: Altium object type constant (see `obj_query` for options)
            scope: "active_doc" (default), "project", or
                "doc:C:\\path\\Sheet.SchDoc" for a specific loaded sheet.
            filter: Pipe-separated property=value conditions (AND logic)

        Returns:
            Dictionary with "count" (and "sheets_processed" for project scope)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_object_count",
            {"object_type": object_type, "scope": scope_to_wire(scope), "filter": filter},
        )
        # The PCB active-doc path runs the query handler, which ships a list of
        # (empty, since no properties were requested) objects alongside the
        # count. This tool only promises the count -- drop the wasteful array.
        if isinstance(result, dict):
            result.pop("objects", None)
        return result

    @mcp.tool()
    async def sch_place_no_erc(
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
    async def sch_place_junction(
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
    async def obj_get_document_info() -> dict[str, Any]:
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
    async def obj_set_grid(
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
        current unit is readable via `obj_get_document_info` (unit_system field).

        Args:
            unit: one of
                "mil"           , imperial, mils (0.001 in)
                "inch"          , imperial, inches
                "dxp"           , DXP default
                "auto_imperial" , auto-scaled imperial display
                "mm"            , metric, millimetres
                "cm"            , metric, centimetres
                "m"             , metric, metres
                "auto_metric"   , auto-scaled metric display

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
         , marks the net as a member of the USB differential pair.
        - ``param_name="NetClass", param_value="HighSpeed"``
         , assigns the net to the HighSpeed class.
        - ``param_name="Signal_Stimulus", param_value="..."``
         , any custom per-net rule parameter.

        Args:
            x, y: Location in mils, place ON the target wire/net.
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
            Dictionary with ``directives`` array, each entry has
            ``name``, ``x``, ``y``, and a ``parameters`` list of
            ``{name, value}`` objects, plus ``count``.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("generic.get_directives", {})
        return result

    @mcp.tool()
    async def sch_place_image(
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
    async def proj_replace_component(
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
    async def sch_clear_source_library(
        sheet_path: str = "",
        designators: Optional[list[str]] = None,
        clear_source_library: bool = True,
        sync_design_item_id: bool = True,
    ) -> dict[str, Any]:
        """Unpin placed schematic components from a stale source library.

        Schematic mirror of ``pcb_clear_source_footprint_library``. When
        a library is renamed, moved, or consolidated, placed components
        keep pointing at it through two component PROPERTIES:

        - ``SourceLibraryName``: which library file the part came from.
        - ``DesignItemId``: which library ITEM it re-matches against
          ("Design Item ID" in the Properties panel). A re-link that
          updates only LibReference leaves this naming the OLD item,
          which is exactly the ``<Not Found>`` state in the panel.

        Per matching component this clears SourceLibraryName (when set)
        and syncs DesignItemId to LibReference (when they differ), so
        Altium re-matches from whatever is currently in Available
        Libraries. Both actions are independently switchable.

        DesignItemId is a component property, NOT a parameter: writing
        it through the parameter-stamping API only creates a stray user
        parameter of that name. Use this tool (bulk) or ``obj_modify``
        with property ``DesignItemId`` (single component).

        Args:
            sheet_path: absolute .SchDoc path; empty = the focused
                sheet. For a whole project, call once per sheet (see
                proj_list_documents).
            designators: restrict to these designators (whole-string,
                case-sensitive); None/empty = every component on the
                sheet.
            clear_source_library: clear ``SourceLibraryName``.
            sync_design_item_id: set ``DesignItemId`` = LibReference
                where they differ.

        Returns:
            {"total": inspected, "cleared_source_library": n,
             "synced_design_item_id": n}.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "sheet_path": sheet_path,
            "clear_source_library": "true" if clear_source_library else "false",
            "sync_design_item_id": "true" if sync_design_item_id else "false",
        }
        if designators:
            # The list rides a comma-separated field; a designator
            # containing a comma would arrive as two designators, and a
            # fragment like 'R1' can match a real component. Refuse
            # rather than strip: stripping can produce a different
            # valid designator.
            bad = [d for d in designators if "," in str(d)]
            if bad:
                return {"ok": False, "reason":
                        f"designators {bad} contain a comma, which is "
                        "the wire-format separator; fragments could "
                        "match other components, so the call is refused"}
            params["designators"] = ",".join(designators)
        return await bridge.send_command_async(
            "generic.clear_sch_source_library", params
        )

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
        label, they connect a signal to the same net name on another
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
    async def sch_place_text_frame(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        text: str,
        align: str = "left",
    ) -> dict[str, Any]:
        """Place a multi-line text frame (note block) on the active sheet.

        A text frame is a bordered rectangle that word-wraps a block of
        text. Use ``\\n`` in ``text`` for explicit line breaks.

        Args:
            x1, y1, x2, y2: The two rectangle corners in mils.
            text: Frame contents (use \\n for line breaks).
            align: Horizontal alignment "left", "center", or "right".

        Returns:
            Dict confirming placement and the frame corners.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.place_text_frame",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "text": text,
                "align": align,
            },
        )

    @mcp.tool()
    async def sch_generate_toc(
        x1: int = 200,
        y1: int = 200,
        x2: int = 2400,
        y2: int = 1800,
        title: str = "Table of Contents",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Place a table-of-contents note listing every schematic sheet.

        Reads the project's document list, keeps the .SchDoc sheets in
        project order, and drops a numbered text frame on the active sheet.
        Run it on a dedicated cover/index sheet. Re-running does not remove a
        prior frame, delete the old one first if you regenerate.

        Args:
            x1, y1, x2, y2: Frame corners in mils (default a tall left box).
            title: Heading line above the list.
            project_path: Optional project; uses active if omitted.

        Returns:
            Dict with sheet_count and the placement result, or an error if
            no schematic sheets were found.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        docs = await bridge.send_command_async("project.get_documents", params)
        if isinstance(docs, dict):
            docs = docs.get("documents", docs.get("items", []))
        if not isinstance(docs, list):
            docs = []

        sheets = [
            d for d in docs
            if str(d.get("file_name", "")).lower().endswith(".schdoc")
        ]
        if not sheets:
            return {"success": False, "error": "no .SchDoc sheets found in project"}

        lines = [title, ""]
        for i, d in enumerate(sheets, start=1):
            name = str(d.get("file_name", "")).strip()
            if name.lower().endswith(".schdoc"):
                name = name[: -len(".SchDoc")]
            lines.append(f"{i}. {name}")
        text = "\n".join(lines)

        result = await bridge.send_command_async(
            "generic.place_text_frame",
            {
                "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
                "text": text, "align": "left",
            },
        )
        if isinstance(result, dict):
            result["sheet_count"] = len(sheets)
        return result

    @mcp.tool()
    async def sch_set_net_tie(
        designator: str,
        mode: str = "nobom",
    ) -> dict[str, Any]:
        """Mark a placed schematic component as a net tie.

        A net tie shorts the nets that land on its pins for routing while
        keeping them as separate logical nets (so ERC/DRC treat the join as
        intentional). Sets the component's ComponentKind. Use on a dedicated
        net-tie footprint/symbol you have already placed and wired.

        Args:
            designator: Component to convert (e.g. "NT1").
            mode: "nobom" (default, hidden from BOM, kept through sync) or
                "bom" (appears in the BOM).

        Returns:
            {"designator", "component_kind"} or NOT_FOUND.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_net_tie",
            {"designator": designator, "mode": mode},
        )

    @mcp.tool()
    async def sch_increment_designators(
        delta: int,
        prefix: str = "",
    ) -> dict[str, Any]:
        """Offset the trailing number of schematic designators by a delta.

        Adds ``delta`` to the numeric suffix of every component designator
        on the active sheet (e.g. delta=100 turns R5 into R105),
        optionally restricted to a designator prefix. Handy after copying
        a block to avoid designator collisions before re-annotating.

        Args:
            delta: Non-zero integer added to each designator's number.
            prefix: If set (e.g. "R"), only designators with this letter
                prefix are changed.

        Returns:
            Dict with success, modified (count), delta.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"delta": str(delta)}
        if prefix:
            params["prefix"] = prefix
        return await bridge.send_command_async(
            "generic.increment_designators", params
        )

    @mcp.tool()
    async def sch_toggle_pin_visibility(
        designator: str = "",
        show_name: Optional[bool] = None,
        show_designator: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Show or hide pin name / designator labels on schematic symbols.

        Targets a single component (by ``designator``) or every component
        on the active sheet when ``designator`` is empty. Provide
        ``show_name`` and/or ``show_designator`` to set those flags; omit a
        flag to leave it unchanged.

        Args:
            designator: Component to target, or "" for all components.
            show_name: True/False to set pin-name visibility (optional).
            show_designator: True/False to set pin-designator visibility.

        Returns:
            Dict with success and pins_modified (count).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if designator:
            params["designator"] = designator
        if show_name is not None:
            params["show_name"] = "true" if show_name else "false"
        if show_designator is not None:
            params["show_designator"] = "true" if show_designator else "false"
        return await bridge.send_command_async(
            "generic.toggle_pin_visibility", params
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
    async def sch_set_component_unique_id(
        designator: str,
        unique_id: str,
    ) -> dict[str, Any]:
        """Set UniqueId on a placed schematic component.

        ECO treats sub-parts of a multi-gate symbol (quad comparator,
        dual op-amp) as one physical footprint only when they share
        UniqueId. Four copies of part 1 with four UniqueIds become four
        packages.

        Args:
            designator: Component reference as drawn (e.g. "U_COMP2B").
            unique_id: Target UniqueId, usually copied from the A unit.

        Returns:
            Dict with success, designator, unique_id, unique_id_after.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.set_component_unique_id",
            {"designator": designator, "unique_id": unique_id},
        )

    @mcp.tool()
    async def sch_replicate_component(
        designator: str,
        part_id: int = 0,
        x: Optional[int] = None,
        y: Optional[int] = None,
        new_designator: str = "",
    ) -> dict[str, Any]:
        """Copy a placed schematic part and share the master's UniqueId.

        ECO groups multi-part packages (quad comparators, dual op-amps)
        only when every unit has the same UniqueId. Writing UniqueId
        after the copy is already on the sheet remints a new id if that
        value is already present. This path stamps UniqueId on the
        replica *before* AddSchObject, which is the share that sticks.

        Finds the first instance whose designator matches, Replicates
        it, optionally switches CurrentPartID, optionally moves it, and
        keeps the master's UniqueId.

        Args:
            designator: Source instance to copy (e.g. "U8").
            part_id: 1-based CurrentPartID for the copy. 0 = keep source.
            x, y: Placement in mils. Omit both to leave the replica
                where Replicate dropped it.
            new_designator: Designator for the copy. Empty keeps the
                source designator (required for multi-part packing).

        Returns:
            Dict with success, source/copy UniqueIds, part_id, and
            ``shared`` (true when the copy kept the master's UniqueId).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if part_id >= 1:
            params["part_id"] = str(part_id)
        if x is not None:
            params["x"] = str(x)
        if y is not None:
            params["y"] = str(y)
        if new_designator:
            params["new_designator"] = new_designator
        return await bridge.send_command_async(
            "generic.replicate_sch_component",
            params,
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
    async def obj_batch_create(
        operations: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create many schematic objects in ONE IPC round-trip.

        PREFER THIS over looping `obj_create`. Each create costs one
        LLM turn when done one at a time; batched it's a single
        PreProcess/PostProcess + one save for the whole set.

        Args:
            operations: List of create dicts, each with:
                - scope: "active_doc" (default), only active_doc is
                  currently honored for creates.
                - object_type: Altium type name (e.g. "eNetLabel",
                  "eJunction", "eNoERC").
                - properties: pipe-separated ``Name=Value`` list, same
                  format as ``obj_create`` accepts.
                - container: "document" (default) or "component" (for
                  library-symbol contents when a lib is active).

        Example, drop 3 net labels in one call:
            obj_batch_create(operations=[
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
            # `properties` is PIPE-separated ("Text=X|Location.X=100"),
            # so ";" is not a legitimate separator inside it and "=" is.
            # payload_safe leaves "=" alone for exactly that reason.
            fields = [
                f"scope={payload_safe(scope)}",
                f"object_type={payload_safe(obj_type)}",
                f"properties={payload_safe(props)}",
            ]
            if container:
                fields.append(f"container={payload_safe(container)}")
            op_strs.append(";".join(fields))

        if not op_strs:
            return {"error": "No valid operations", "created": 0}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.batch_create",
            {"operations": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def obj_batch_delete(
        operations: list[dict[str, str]],
        confirm_delete_all: bool = False,
    ) -> dict[str, Any]:
        """Delete matching objects across many scope/type/filter operations.

        PREFER THIS over looping `obj_delete`. Each op is evaluated
        in one go, cleaning a mixed set of stale junctions, no-ERCs,
        and net labels costs one IPC round-trip instead of N.

        Args:
            operations: List of delete dicts, each with:
                - scope: "active_doc" (default), "project", or
                  "doc:<absolute_path>".
                - object_type: Altium type name (e.g. "eJunction",
                  "eNoERC", "eWire").
                - filter: pipe-separated ``PropName=Value`` filter
                  conditions (AND logic), same format as
                  ``obj_delete``. An EMPTY filter deletes every object
                  of that type in that scope.
            confirm_delete_all: required when any operation has an empty
                filter. Same guard ``obj_delete`` applies to the single
                call, so routing a sweep through the bulk tool does not
                get around it.

        Example, purge all no-ERCs on a specific sheet and every
        junction on the project. Both filters are empty, so the
        confirmation is required:
            obj_batch_delete(confirm_delete_all=True, operations=[
                {"scope": "doc:C:\\proj\\Power.SchDoc",
                 "object_type": "eNoERC", "filter": ""},
                {"scope": "project",
                 "object_type": "eJunction", "filter": ""},
            ])

        Returns:
            Dict with operations_processed and total, or an ``error``
            with ``operations_processed`` 0 when a sweep is unconfirmed,
            in which case nothing is sent.
        """
        op_strs: list[str] = []
        sweeping: list[str] = []
        for i, op in enumerate(operations):
            scope = op.get("scope", "active_doc")
            obj_type = op.get("object_type", "")
            filt = op.get("filter", "")
            # ';' separates fields within an op and '~~' separates ops,
            # so either inside a value reshapes the batch. The dangerous
            # shape: a '~~' inside filter fabricates a NEW op the
            # confirm_delete_all guard below never saw, and an
            # unfiltered delete rides in unconfirmed. Refuse rather than
            # strip: stripping can produce a different valid value.
            for field, value in (("scope", scope),
                                 ("object_type", obj_type),
                                 ("filter", filt)):
                if ";" in str(value) or "~~" in str(value):
                    return {"ok": False, "reason":
                            f"operations[{i}].{field} contains ';' or "
                            "'~~', which are the batch delimiters; the "
                            "wire format cannot carry them, so this "
                            "batch is refused rather than reshaped",
                            "operations_processed": 0}
            if not obj_type:
                continue
            if not str(filt).strip():
                sweeping.append(f"{obj_type} in {scope}")
            op_strs.append(
                f"scope={scope};object_type={obj_type};filter={filt}"
            )

        if not op_strs:
            return {"error": "No valid operations", "operations_processed": 0}

        # obj_delete refuses an unfiltered delete and then tells the
        # caller to come here instead, so without this the guard is just
        # a detour. One op with a blank filter is enough: the others
        # would still run, and a partly-applied batch is harder to
        # reason about afterwards than one that did nothing.
        if sweeping and not confirm_delete_all:
            return {
                "error": (
                    "Safety guard: these operations have an empty filter "
                    f"and would delete ALL matching objects: {sweeping}. "
                    "Add a filter to select specific objects, or set "
                    "confirm_delete_all=True to sweep them."
                ),
                "operations_processed": 0,
                "unfiltered_operations": sweeping,
            }

        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.batch_delete",
            {"operations": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def sch_place_wires(
        wires: list[dict[str, int]],
    ) -> dict[str, Any]:
        """Place MANY wire segments on the active schematic in ONE call.

        PREFER THIS over placing wires one segment at a time (there is no
        singular variant). Wiring up a netlist is inherently N pairs of
        endpoints; the bulk version is 10-100x faster in wall time because
        the whole batch shares one PreProcess/PostProcess and one redraw.

        Args:
            wires: List of wire dicts, each with x1, y1, x2, y2 in mils.

        Example, a 3-segment L-shaped bus routing:
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
    async def sch_place_components(
        placements: list[dict[str, Any]],
        document_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Place MANY schematic components from libraries in ONE call.

        This is the only path for library placement (there is no singular
        variant). Laying out a 50-part BOM is inherently a bulk operation;
        done one-by-one it would cost 50 LLM turns.

        TARGET DOCUMENT: placement lands on the ACTIVE schematic. Right
        after `app_create_document` the new sheet is NOT auto-focused, so
        without `document_path` parts can silently land on a *different*
        open sheet. Pass `document_path` (absolute .SchDoc path) and this
        tool focuses that sheet first (`app_set_active_document`) before
        placing, always set it when you have just created the target
        sheet.

        Args:
            document_path: Absolute path of the .SchDoc to place onto.
                When given, it is focused before placement so parts can't
                land on the wrong sheet. When omitted, the current active
                document is used.
            placements: List of placement dicts, each with:
                - library_path (str, optional, empty uses an
                  already-open library)
                - lib_reference (str, required), component name
                - x, y (int, mils), placement location
                - designator (str, optional), override designator
                - rotation (int, optional), 0 / 90 / 180 / 270
                - footprint (str, optional), override current footprint

        Example, place a 5-part BOM row:
            sch_place_components(placements=[
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
                f"library_path={payload_safe(p.get('library_path', ''))}",
                f"lib_reference={payload_safe(lib_ref)}",
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
        # Focus the target sheet first so placement can't land on a
        # different open document (placement targets the active doc, and
        # a freshly created sheet is not auto-focused).
        if document_path:
            focus = await bridge.send_command_async(
                "application.set_active_document",
                {"file_path": document_path},
            )
            if isinstance(focus, dict) and not focus.get("success", True):
                return {
                    "error": "FOCUS_FAILED",
                    "reason": f"could not focus {document_path} before "
                    "placement; aborting to avoid placing on the wrong "
                    "sheet",
                    "focus_result": focus,
                    "placed": 0,
                }
        return await bridge.send_command_async(
            "generic.place_sch_components_from_library",
            {"placements": "~~".join(op_strs)},
        )

    @mcp.tool()
    async def sim_attach_primitives(
        attachments: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Attach SPICE primitives to MANY components in ONE call.

        PREFER THIS after running `sim_get_readiness`, the
        readiness response typically lists 20-50 passives that all
        need the SpicePrefix + Value parameter pair. Attaching them one
        at a time would cost one LLM turn per component (there is no
        singular variant); this tool does the whole set in one round-trip.

        Args:
            attachments: List of attach dicts, each with:
                - designator (str, required)
                - primitive  (str, required), R/L/C/V/I/D/Q/M/X
                - value      (str, optional), "10k", "100n",
                  "DC 5", "SIN(0 1 1k)", etc.
                - spice_model (str, optional), model name for
                  semi/sub-circuit parts
                - sim_kind   (str, optional), "General" /
                  "Subcircuit" / "Model"

        Example, attach the 4 passives from a readiness audit:
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

    # Schematic power ports, net labels, and free text are placed via the
    # dedicated handlers place_power_port / place_net_label / place_note.
