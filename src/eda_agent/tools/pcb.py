# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""PCB-specific tools for Altium Designer MCP Server.

Provides high-level PCB operations: net classes, design rules, DRC,
component placement, trace lengths, layer stackup, board outline, etc.
"""

from typing import Any
from ..bridge import get_bridge
from .bulk_hints import BulkHintTracker


def register_pcb_tools(mcp):
    """Register PCB tools with the MCP server."""

    @mcp.tool()
    async def pcb_get_nets() -> dict[str, Any]:
        """Get all unique net names from the active PCB board.

        Returns:
            Dictionary with "nets" array of net name strings and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_nets", {})
        return result

    @mcp.tool()
    async def pcb_get_net_classes() -> dict[str, Any]:
        """Get all net classes from the active PCB.

        Only returns class metadata — IPCB_ObjectClass.MemberCount and
        MemberName[] are not exposed in Altium's DelphiScript host, so
        per-member enumeration has to be done by iterating eNetObject and
        grouping by each net's parent class.

        Returns:
            Dictionary with "net_classes" array (each with name, super_class)
            and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_net_classes", {})
        return result

    @mcp.tool()
    async def pcb_create_net_class(
        name: str,
        nets: str,
    ) -> dict[str, Any]:
        """Create a net class (or add nets to an existing one) on the active PCB.

        Args:
            name: Name for the net class (e.g., "PowerNets", "HighSpeed")
            nets: Comma-separated list of net names to add
                  (e.g., "VCC,GND,3V3")

        Returns:
            Dictionary with class_name, class_created (bool), nets_added count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.create_net_class",
            {"name": name, "nets": nets},
        )
        return result

    @mcp.tool()
    async def pcb_get_design_rules() -> dict[str, Any]:
        """Get all design rules from the active PCB.

        Returns:
            Dictionary with "rules" array (each with name, rule_kind, enabled,
            priority, scope_1, scope_2, comment, descriptor) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_design_rules", {})
        return result

    @mcp.tool()
    async def pcb_run_drc() -> dict[str, Any]:
        """Run Design Rule Check (DRC) on the active PCB.

        Executes the DRC and returns the violation count and details.
        Up to 100 violations are returned with descriptions.

        Returns:
            Dictionary with "violation_count" and "violations" array
            (each with description and name)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.run_drc", {})
        return result

    @mcp.tool()
    async def pcb_get_components() -> dict[str, Any]:
        """Get all components from the active PCB with position and properties.

        Returns:
            Dictionary with "components" array (each with designator, x, y,
            rotation, layer, footprint) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_components", {})
        return result

    @mcp.tool()
    async def pcb_move_component(
        designator: str,
        x: int | None = None,
        y: int | None = None,
        rotation: float | None = None,
    ) -> dict[str, Any]:
        """Move and/or rotate ONE PCB component by its designator.

        IMPORTANT — if you need to reposition more than one component,
        use `pcb_move_components` (batch) instead. Looping this tool is
        the single biggest wall-time cost: each call is a full LLM
        round-trip, but the batch version does N moves in one turn.

        Sets the absolute position/rotation. Only provided parameters are
        changed; omitted parameters keep their current values.

        Args:
            designator: Component reference designator (e.g., "U1", "R5")
            x: New X position in mils (optional)
            y: New Y position in mils (optional)
            rotation: New rotation angle in degrees (optional, 0-360)

        Returns:
            Dictionary with final designator, x, y, rotation values
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if x is not None:
            params["x"] = str(x)
        if y is not None:
            params["y"] = str(y)
        if rotation is not None:
            params["rotation"] = str(rotation)
        result = await bridge.send_command_async("pcb.move_component", params)
        hint = BulkHintTracker.record_and_hint("pcb_move_component")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def pcb_move_components(
        moves: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Move and/or rotate MANY PCB components in ONE IPC round-trip.

        PREFER THIS over looping `pcb_move_component`. Each call to the
        singular tool is a full LLM turn (5-15 s); one call to this tool
        repositions every component in the list in a single Altium
        transaction.

        Typical uses: applying a full layout pass, running an
        auto-placement result, undoing and redoing a placement set,
        adjusting a row of components relative to each other.

        Args:
            moves: List of move dicts. Each dict supports:
                designator (required)  — target component
                x        (optional)    — new X in mils
                y        (optional)    — new Y in mils
                rotation (optional)    — new rotation in degrees

            Example:
                [
                  {"designator": "U1", "x": 5000, "y": 5000, "rotation": 0},
                  {"designator": "R1", "x": 5200, "y": 4800},
                  {"designator": "C1", "rotation": 90},
                ]

        Returns:
            Dictionary with per-designator results and a count.
        """
        # Pack each move as comma-separated fields: designator,x,y,rotation
        # (empty field = leave that property unchanged). Moves joined by '|'.
        # This format is unambiguous and matches PCB_PlaceTracks.
        ops: list[str] = []
        for m in moves:
            desig = str(m.get("designator", "")).strip()
            if not desig:
                continue
            x_str = (
                str(int(m["x"])) if "x" in m and m["x"] is not None else ""
            )
            y_str = (
                str(int(m["y"])) if "y" in m and m["y"] is not None else ""
            )
            rot_str = (
                str(m["rotation"])
                if "rotation" in m and m["rotation"] is not None
                else ""
            )
            if x_str == "" and y_str == "" and rot_str == "":
                continue
            ops.append(f"{desig},{x_str},{y_str},{rot_str}")

        if not ops:
            return {"error": "No valid moves provided", "moves_applied": 0}

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.batch_move_components",
            {"moves": "|".join(ops)},
        )
        return result

    @mcp.tool()
    async def pcb_get_trace_lengths(
        net: str = "",
    ) -> dict[str, Any]:
        """Get total routed track length per net on the active PCB.

        Sums all track segment lengths for each net. Useful for length
        matching analysis and checking differential pair balance.

        Args:
            net: Optional net name filter. If provided, only returns the
                 length for that specific net. Empty = all nets.

        Returns:
            Dictionary with "trace_lengths" array (each with net name and
            length_mils) and "net_count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if net:
            params["net"] = net
        result = await bridge.send_command_async("pcb.get_trace_lengths", params)
        return result

    @mcp.tool()
    async def pcb_get_layer_stackup() -> dict[str, Any]:
        """Get the full PCB layer stackup information.

        Returns copper layers with thickness, dielectric type/height/constant,
        and board name.

        Returns:
            Dictionary with "layers" array (each with name, order,
            copper_thickness_mils, dielectric_type, dielectric_height_mils,
            dielectric_constant), "layer_count", and "board_name"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_layer_stackup", {})
        return result

    @mcp.tool()
    async def pcb_add_layer(layer: str) -> dict[str, Any]:
        """Insert a copper layer into the PCB layer stack.

        Calls IPCB_LayerStack.InsertLayer with the requested TLayer enum.
        Valid names include MidLayer1..MidLayer30 (signal layers) and
        InternalPlane1..InternalPlane16 (power / ground planes). Top / Bottom
        are always present — they cannot be added.

        Args:
            layer: Layer name, e.g. "MidLayer1", "InternalPlane1"

        Returns:
            Dictionary confirming the layer was inserted.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.add_layer", {"layer": layer}
        )
        return result

    @mcp.tool()
    async def pcb_remove_layer(layer: str) -> dict[str, Any]:
        """Remove a copper layer from the PCB layer stack.

        Calls IPCB_LayerStack.RemoveFromStack on the requested layer.
        Does nothing if the layer is not currently in the stack.

        Args:
            layer: Layer name, e.g. "MidLayer1", "InternalPlane2"

        Returns:
            Dictionary confirming the layer was removed.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.remove_layer", {"layer": layer}
        )
        return result

    @mcp.tool()
    async def pcb_modify_layer(
        layer: str,
        name: str = "",
        copper_thickness_mils: int = 0,
        dielectric_type: str = "",
        dielectric_height_mils: int = 0,
        dielectric_constant: float = 0.0,
        dielectric_material: str = "",
    ) -> dict[str, Any]:
        """Tune properties on an existing copper layer.

        Every optional parameter is applied only if provided (non-empty
        string / non-zero number). Maps to:
          name                   IPCB_LayerObject.Name
          copper_thickness_mils  IPCB_LayerObject.CopperThickness
          dielectric_type        Dielectric.DielectricType
                                 (one of "none", "core", "prepreg", "surface")
          dielectric_height_mils Dielectric.DielectricHeight
          dielectric_constant    Dielectric.DielectricConstant
          dielectric_material    Dielectric.DielectricMaterial

        Args:
            layer: Target layer name, e.g. "MidLayer1"
            name: New layer name (optional)
            copper_thickness_mils: New copper thickness in mils (optional)
            dielectric_type: "none" | "core" | "prepreg" | "surface" (optional)
            dielectric_height_mils: New dielectric height in mils (optional)
            dielectric_constant: New Dk value (optional)
            dielectric_material: New dielectric material string (optional)

        Returns:
            Dictionary confirming the changes.
        """
        bridge = get_bridge()
        params: dict[str, str] = {"layer": layer}
        if name:
            params["name"] = name
        if copper_thickness_mils:
            params["copper_thickness_mils"] = str(copper_thickness_mils)
        if dielectric_type:
            params["dielectric_type"] = dielectric_type
        if dielectric_height_mils:
            params["dielectric_height_mils"] = str(dielectric_height_mils)
        if dielectric_constant:
            params["dielectric_constant"] = str(dielectric_constant)
        if dielectric_material:
            params["dielectric_material"] = dielectric_material
        result = await bridge.send_command_async("pcb.modify_layer", params)
        return result

    @mcp.tool()
    async def pcb_get_board_outline() -> dict[str, Any]:
        """Get the board outline vertices and bounding rectangle.

        Returns outline geometry as a list of vertices (line segments and
        arcs) plus the bounding rectangle dimensions.

        Returns:
            Dictionary with "point_count", "vertices" array (each with
            index, kind, x, y, and optionally cx, cy, angle1, angle2 for arcs),
            and "bounding_rect" (left, bottom, right, top in mils)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_board_outline", {})
        return result

    @mcp.tool()
    async def pcb_get_selected_objects(
        properties: str = "ObjectId,X,Y,Layer,Net",
    ) -> dict[str, Any]:
        """Get properties of currently selected objects on the active PCB.

        Args:
            properties: Comma-separated property names to return.
                Available: ObjectId, X, Y, X1, Y1, X2, Y2, Layer, Net,
                Width, Name, Rotation, HoleSize, TopXSize, TopYSize,
                Size, Pattern, SourceDesignator, Text, Descriptor, Selected

        Returns:
            Dictionary with "objects" array and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.get_selected_objects",
            {"properties": properties},
        )
        return result

    @mcp.tool()
    async def pcb_set_layer_visibility(
        layer: str,
        visible: bool = True,
    ) -> dict[str, Any]:
        """Show or hide a specific PCB layer.

        Args:
            layer: Layer name string, e.g.:
                Copper: "TopLayer", "BottomLayer", "MidLayer1"-"MidLayer30"
                Overlay: "TopOverlay", "BottomOverlay"
                Mask: "TopPaste", "BottomPaste", "TopSolder", "BottomSolder"
                Plane: "InternalPlane1"-"InternalPlane16"
                Other: "DrillGuide", "DrillDrawing", "MultiLayer",
                       "KeepOutLayer", "Mechanical1"-"Mechanical16"
            visible: True to show, False to hide

        Returns:
            Dictionary with layer name and visibility state
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_layer_visibility",
            {"layer": layer, "visible": "true" if visible else "false"},
        )
        return result

    @mcp.tool()
    async def pcb_repour_polygons() -> dict[str, Any]:
        """Repour all polygon pours on the active PCB.

        Triggers a full repour of all polygon copper pours, which
        recalculates thermal reliefs and clearances.

        Returns:
            Dictionary confirming repour completed
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.repour_polygons", {})
        return result

    @mcp.tool()
    async def pcb_set_board_shape(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any]:
        """Define the physical PCB board outline as a rectangle.

        Overwrites the current board shape. Use this right after creating a
        new PCB document to establish the board size before placing parts.
        Coordinates are in mils; (x1,y1) and (x2,y2) are opposite corners in
        any order.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils

        Returns:
            Dictionary confirming the new outline rectangle
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_board_shape",
            {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)},
        )
        return result

    @mcp.tool()
    async def pcb_place_polygon_rect(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        net: str = "",
        layer: str = "TopLayer",
        pour_over: bool = True,
    ) -> dict[str, Any]:
        """Drop a copper polygon pour on a rectangular area.

        Useful for placing a ground plane or power plane: pass the board's
        corners, the layer, and the net name (typically "GND"). Set
        pour_over=False to force the pour around same-net tracks/pads
        instead of covering them.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            net: Net name to assign (empty = no-net fill, unusual)
            layer: Copper layer (default "TopLayer")
            pour_over: Pour over same-net objects (default True)

        Returns:
            Dictionary confirming the polygon placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_polygon_rect",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "net": net,
                "layer": layer,
                "pour_over": "true" if pour_over else "false",
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_via_array(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        pitch: int = 50,
        net: str = "",
        size: int = 30,
        hole_size: int = 12,
        low_layer: str = "TopLayer",
        high_layer: str = "BottomLayer",
    ) -> dict[str, Any]:
        """Stitch vias in a regular grid across a rectangle.

        Typical use: GND stitching between top and bottom layer copper
        pours. Places vias at every (pitch × pitch) grid intersection
        inside the rectangle.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            pitch: Grid spacing in mils (default 50)
            net: Net to assign (typically "GND"; empty = no net)
            size: Via pad diameter in mils (default 30)
            hole_size: Drill hole diameter in mils (default 12)
            low_layer: Start layer (default "TopLayer")
            high_layer: End layer (default "BottomLayer")

        Returns:
            Dictionary with count of vias placed and rectangle/pitch echo
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_via_array",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "pitch": str(pitch),
                "net": net,
                "size": str(size),
                "hole_size": str(hole_size),
                "low_layer": low_layer,
                "high_layer": high_layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_via(
        x: int,
        y: int,
        net: str = "",
        size: int = 50,
        hole_size: int = 28,
        low_layer: str = "TopLayer",
        high_layer: str = "BottomLayer",
    ) -> dict[str, Any]:
        """Place a via at specific coordinates on the active PCB.

        Args:
            x: Via X position in mils
            y: Via Y position in mils
            net: Net name to assign (optional, empty = no net)
            size: Via pad diameter in mils (default 50)
            hole_size: Drill hole diameter in mils (default 28)
            low_layer: Start layer (default "TopLayer")
            high_layer: End layer (default "BottomLayer")

        Returns:
            Dictionary with placed via position and size
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x": str(x),
            "y": str(y),
            "size": str(size),
            "hole_size": str(hole_size),
            "low_layer": low_layer,
            "high_layer": high_layer,
        }
        if net:
            params["net"] = net
        result = await bridge.send_command_async("pcb.place_via", params)
        return result

    @mcp.tool()
    async def pcb_place_track(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        width: int = 10,
        layer: str = "TopLayer",
        net_name: str = "",
    ) -> dict[str, Any]:
        """Place ONE track segment on the active PCB.

        IMPORTANT: If you are about to place more than one segment
        (multi-segment manhattan routes, a whole net, a batch of
        traces), use `pcb_place_tracks` instead — it takes a list of
        segments and runs them in a single IPC round-trip, which is
        dramatically faster than calling this tool repeatedly.

        Args:
            x1: Start X position in mils
            y1: Start Y position in mils
            x2: End X position in mils
            y2: End Y position in mils
            width: Track width in mils (default 10)
            layer: PCB layer name (default "TopLayer"). Options:
                "TopLayer", "BottomLayer", "MidLayer1"-"MidLayer30"
            net_name: Net name to assign (optional, empty = no net)

        Returns:
            Dictionary with placed track coordinates, width, and layer
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x1": str(x1),
            "y1": str(y1),
            "x2": str(x2),
            "y2": str(y2),
            "width": str(width),
            "layer": layer,
        }
        if net_name:
            params["net_name"] = net_name
        result = await bridge.send_command_async("pcb.place_track", params)
        hint = BulkHintTracker.record_and_hint("pcb_place_track")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def pcb_place_tracks(
        tracks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Place many track segments on the active PCB in ONE IPC round-trip.

        PREFER THIS over looping `pcb_place_track` whenever you have
        more than one segment to place. The whole batch is wrapped in
        a single PreProcess/PostProcess and a single save, so 50
        tracks take roughly the same wall time as 1. Typical uses:
        routing a full net, laying down a whole stitch pattern,
        replicating a motif, drawing a keepout rectangle.

        Args:
            tracks: List of track dicts. Each dict supports:
                x1, y1, x2, y2 (required, mils)
                width (default 10), layer (default "TopLayer"),
                net_name (optional, empty = no net)

            Example:
                [
                  {"x1": 5010, "y1": 4785, "x2": 5070, "y2": 4785,
                   "width": 10, "net_name": "NetC8_2"},
                  {"x1": 5070, "y1": 4785, "x2": 5070, "y2": 4862,
                   "width": 10, "net_name": "NetC8_2"},
                ]

        Returns:
            Dictionary with "placed" and "failed" counts
        """
        parts = []
        for t in tracks:
            x1 = int(t["x1"])
            y1 = int(t["y1"])
            x2 = int(t["x2"])
            y2 = int(t["y2"])
            width = int(t.get("width", 10))
            layer = str(t.get("layer", "TopLayer"))
            net = str(t.get("net_name", ""))
            parts.append(f"{x1},{y1},{x2},{y2},{width},{layer},{net}")
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_tracks", {"tracks": "|".join(parts)}
        )
        return result

    @mcp.tool()
    async def pcb_place_arc(
        x_center: int,
        y_center: int,
        radius: int,
        start_angle: float = 0,
        end_angle: float = 360,
        width: int = 10,
        layer: str = "TopLayer",
    ) -> dict[str, Any]:
        """Place an arc on the active PCB.

        Creates a circular arc segment defined by center, radius, and
        angular range.

        Args:
            x_center: Arc center X position in mils
            y_center: Arc center Y position in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees (default 0)
            end_angle: End angle in degrees (default 360 = full circle)
            width: Arc line width in mils (default 10)
            layer: PCB layer name (default "TopLayer")

        Returns:
            Dictionary with placed arc geometry and layer
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x_center": str(x_center),
            "y_center": str(y_center),
            "radius": str(radius),
            "start_angle": str(start_angle),
            "end_angle": str(end_angle),
            "width": str(width),
            "layer": layer,
        }
        result = await bridge.send_command_async("pcb.place_arc", params)
        return result

    @mcp.tool()
    async def pcb_place_text(
        text: str,
        x: int,
        y: int,
        layer: str = "TopOverlay",
        height: int = 60,
        rotation: float = 0,
    ) -> dict[str, Any]:
        """Place a text string on the active PCB.

        Args:
            text: Text content to place
            x: Text X position in mils
            y: Text Y position in mils
            layer: PCB layer name (default "TopOverlay"). Common choices:
                "TopOverlay", "BottomOverlay", "TopLayer", "BottomLayer",
                "Mechanical1"-"Mechanical16"
            height: Text height in mils (default 60)
            rotation: Rotation angle in degrees (default 0)

        Returns:
            Dictionary with placed text properties
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "text": text,
            "x": str(x),
            "y": str(y),
            "layer": layer,
            "height": str(height),
            "rotation": str(rotation),
        }
        result = await bridge.send_command_async("pcb.place_text", params)
        return result

    @mcp.tool()
    async def pcb_place_fill(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        layer: str = "TopLayer",
        net_name: str = "",
    ) -> dict[str, Any]:
        """Place a rectangular copper fill on the active PCB.

        Creates a solid copper rectangle. Useful for thermal pads,
        ground planes, and copper pours in specific areas.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Second corner X in mils
            y2: Second corner Y in mils
            layer: PCB layer name (default "TopLayer")
            net_name: Net name to assign (optional, empty = no net)

        Returns:
            Dictionary with placed fill coordinates and layer
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x1": str(x1),
            "y1": str(y1),
            "x2": str(x2),
            "y2": str(y2),
            "layer": layer,
        }
        if net_name:
            params["net_name"] = net_name
        result = await bridge.send_command_async("pcb.place_fill", params)
        return result

    @mcp.tool()
    async def pcb_start_polygon_placement(
        layer: str = "TopLayer",
        net_name: str = "",
    ) -> dict[str, Any]:
        """Start INTERACTIVE polygon pour placement on the active PCB.

        This is an interactive command — it launches Altium's polygon
        placement mode. The user must then draw the polygon boundary
        interactively in Altium Designer (clicks define vertices, right-click
        or Escape completes). It does NOT create a polygon programmatically
        from coordinates.

        Args:
            layer: Target copper layer (default "TopLayer")
            net_name: Net to assign to the polygon pour (optional)

        Returns:
            Dictionary confirming polygon placement mode was initiated
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"layer": layer}
        if net_name:
            params["net_name"] = net_name
        result = await bridge.send_command_async("pcb.start_polygon_placement", params)
        return result

    @mcp.tool()
    async def pcb_create_design_rule(
        name: str,
        rule_type: str = "clearance",
        value: int = 10,
        scope: str = "",
        net_scope: str = "different_nets",
    ) -> dict[str, Any]:
        """Create a new design rule on the active PCB.

        Args:
            name: Rule name (e.g., "Min Clearance 6mil")
            rule_type: Type of rule to create. Options:
                "clearance" - Electrical clearance (value = gap in mils)
                "width" - Track width constraint (value = min width in mils,
                    max auto-set to 5x min)
                "via_size" - Hole size constraint (value = min hole in mils,
                    max auto-set to 5x min)
            value: Rule value in mils (default 10)
            scope: Optional query expression for Scope1
                (e.g., "InNet('GND')", "All")
            net_scope: Which nets the rule applies between. Options:
                "different_nets" (default) - only between pads/tracks of
                    different nets. This is what you want for Clearance.
                "any_net" - include same-net objects (flags a track
                    touching a pad of its own net — almost always a bug).
                "same_net" - only between same-net objects.

        Returns:
            Dictionary with created rule details
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "name": name,
            "rule_type": rule_type,
            "value": str(value),
            "net_scope": net_scope,
        }
        if scope:
            params["scope"] = scope
        result = await bridge.send_command_async("pcb.create_design_rule", params)
        return result

    @mcp.tool()
    async def pcb_delete_design_rule(
        name: str,
    ) -> dict[str, Any]:
        """Delete a design rule by name from the active PCB.

        Args:
            name: Exact name of the design rule to delete

        Returns:
            Dictionary confirming deletion
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.delete_design_rule",
            {"name": name},
        )
        return result

    @mcp.tool()
    async def pcb_get_component_pads(
        designator: str,
    ) -> dict[str, Any]:
        """Get all pads of a specific PCB component.

        Returns detailed pad information including pin name, position,
        net assignment, size, and hole information.

        Args:
            designator: Component reference designator (e.g., "U1", "J3")

        Returns:
            Dictionary with "designator", "pads" array (each with name,
            x, y, net, layer, hole_size, top_x_size, top_y_size,
            rotation), and "pad_count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.get_component_pads",
            {"designator": designator},
        )
        return result

    @mcp.tool()
    async def pcb_flip_component(
        designator: str,
    ) -> dict[str, Any]:
        """Flip a component to the other side of the board (top to bottom
        or bottom to top).

        Args:
            designator: Component reference designator (e.g., "U1", "R5")

        Returns:
            Dictionary with designator, old_layer, and new_layer
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.flip_component",
            {"designator": designator},
        )
        return result

    @mcp.tool()
    async def pcb_align_components(
        designators: str,
        alignment: str = "left",
    ) -> dict[str, Any]:
        """Align multiple PCB components along a common edge or center.

        Args:
            designators: Comma-separated component reference designators
                (e.g., "R1,R2,R3,R4")
            alignment: Alignment mode. Options:
                "left" - Align to leftmost X
                "right" - Align to rightmost X
                "top" - Align to topmost Y
                "bottom" - Align to bottommost Y
                "center_x" - Center horizontally
                "center_y" - Center vertically

        Returns:
            Dictionary with alignment result and component count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.align_components",
            {"designators": designators, "alignment": alignment},
        )
        return result

    @mcp.tool()
    async def pcb_snap_to_grid(
        designator: str,
        grid_size: int = 50,
    ) -> dict[str, Any]:
        """Snap a component to the nearest grid point.

        Rounds the component's X and Y position to the nearest multiple
        of the specified grid size.

        Args:
            designator: Component reference designator (e.g., "U1", "R5")
            grid_size: Grid spacing in mils (default 50)

        Returns:
            Dictionary with designator, old and new positions, and grid_size
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.snap_to_grid",
            {"designator": designator, "grid_size": str(grid_size)},
        )
        return result

    @mcp.tool()
    async def pcb_get_diff_pair_rules() -> dict[str, Any]:
        """Get all differential pair routing rules from PCB design rules.

        Returns design rules of kind eRule_DifferentialPairsRouting — these
        are routing rules, NOT IPCB_DifferentialPair pair objects on the board.

        Returns:
            Dictionary with "diff_pair_rules" array (each with name, enabled,
            scope_1, scope_2, comment, descriptor) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_diff_pair_rules", {})
        return result

    @mcp.tool()
    async def pcb_get_vias() -> dict[str, Any]:
        """Get all vias on the active PCB board.

        Returns via position, pad size, hole size, net assignment, and
        start/end layer for every via on the board.

        Returns:
            Dictionary with "vias" array (each with x, y, size, hole_size,
            net, low_layer, high_layer) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_vias", {})
        return result

    @mcp.tool()
    async def pcb_delete_object(
        x: int,
        y: int,
        layer: str = "TopLayer",
        object_type: str = "track",
    ) -> dict[str, Any]:
        """Delete a PCB object closest to specific coordinates on a layer.

        Finds the nearest matching object within 100 mils of the given
        coordinates and removes it from the board.

        Args:
            x: Target X position in mils
            y: Target Y position in mils
            layer: PCB layer name (default "TopLayer")
            object_type: Type of object to delete. Options:
                "track" - Track segment
                "via" - Via
                "fill" - Copper fill
                "text" - Text string

        Returns:
            Dictionary with deleted status, object_type, and distance_mils
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.delete_object",
            {"x": str(x), "y": str(y), "layer": layer, "object_type": object_type},
        )
        return result

    @mcp.tool()
    async def pcb_get_pad_properties(
        net: str = "",
        designator: str = "",
    ) -> dict[str, Any]:
        """Get detailed pad information filtered by net or component.

        Returns pad shape, size, hole, thermal relief, and solder/paste
        mask expansion details. Provide at least one filter (net or
        designator) to avoid returning all pads on the board.

        Args:
            net: Filter by net name (e.g., "GND", "VCC"). Optional.
            designator: Filter by component designator (e.g., "U1"). Optional.

        Returns:
            Dictionary with "pads" array (each with name, component, x, y,
            net, layer, shape, top_x_size, top_y_size, hole_size, rotation,
            is_smd, solder_mask_expansion, paste_mask_expansion) and "count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if net:
            params["net"] = net
        if designator:
            params["designator"] = designator
        result = await bridge.send_command_async("pcb.get_pad_properties", params)
        return result

    @mcp.tool()
    async def pcb_set_track_width(
        net_name: str,
        width_mils: int,
    ) -> dict[str, Any]:
        """Modify track width for all tracks on a specific net.

        Changes the width of every routed track segment assigned to
        the given net. Useful for adjusting power or signal trace widths.

        Args:
            net_name: Name of the net whose tracks to modify (e.g., "VCC")
            width_mils: New track width in mils (e.g., 10, 20, 50)

        Returns:
            Dictionary with net_name, width_mils, and tracks_modified count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.set_track_width",
            {"net_name": net_name, "width_mils": str(width_mils)},
        )
        return result

    @mcp.tool()
    async def pcb_get_unrouted_nets() -> dict[str, Any]:
        """Get list of nets with unrouted connections (ratsnest lines).

        Identifies nets that still have ratsnest lines, meaning they are
        not fully routed. Useful for checking routing completion status.

        Returns:
            Dictionary with "unrouted_nets" array (each with net name and
            unrouted_connections count), "net_count", and "total_unrouted"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_unrouted_nets", {})
        return result

    @mcp.tool()
    async def pcb_get_polygons() -> dict[str, Any]:
        """Get all polygon pours on the active PCB.

        Returns polygon pour details including layer, net assignment,
        hatching style, and pour settings.

        Returns:
            Dictionary with "polygons" array (each with index, name, net,
            layer, hatch_style, pour_over, remove_dead_copper) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_polygons", {})
        return result

    @mcp.tool()
    async def pcb_modify_polygon(
        index: int,
        net: str = "",
        layer: str = "",
        hatch_style: str = "",
    ) -> dict[str, Any]:
        """Modify a polygon pour's properties.

        Changes net, layer, or hatching style of an existing polygon pour.
        Use pcb_get_polygons first to find the polygon index.

        Args:
            index: Polygon index (from pcb_get_polygons output)
            net: New net name to assign (optional, empty = no change)
            layer: New layer name (optional, empty = no change)
            hatch_style: New hatch style (optional). Options:
                "Solid" - Solid copper fill
                "45Degree" - 45-degree crosshatch
                "90Degree" - 90-degree crosshatch
                "Horizontal" - Horizontal lines
                "Vertical" - Vertical lines

        Returns:
            Dictionary with modified status, index, and polygon name
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"index": str(index)}
        if net:
            params["net"] = net
        if layer:
            params["layer"] = layer
        if hatch_style:
            params["hatch_style"] = hatch_style
        result = await bridge.send_command_async("pcb.modify_polygon", params)
        return result

    @mcp.tool()
    async def pcb_get_room_rules() -> dict[str, Any]:
        """Get all room-like rules (confinement constraint design rules).

        Returns design rules of kind eRule_ConfinementConstraint — these are
        NOT physical IPCB_Room objects on the board. The rule bounding rect
        is reported as x1/y1/x2/y2 in mils.

        Returns:
            Dictionary with "room_rules" array (each with name, enabled, kind,
            scope_1, comment, x1, y1, x2, y2) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_room_rules", {})
        return result

    @mcp.tool()
    async def pcb_create_room(
        name: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        components: str = "",
    ) -> dict[str, Any]:
        """Create a room for component grouping on the active PCB.

        Creates a confinement constraint rule that defines a rectangular
        region. Components can be assigned via scope expression.

        Args:
            name: Room name (e.g., "Power Section", "USB Block")
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Second corner X in mils
            y2: Second corner Y in mils
            components: Comma-separated component designators to confine
                (e.g., "U1,U2,R1,R2"). Optional; empty = applies to all.

        Returns:
            Dictionary with created status, name, coordinates, and scope
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "name": name,
            "x1": str(x1),
            "y1": str(y1),
            "x2": str(x2),
            "y2": str(y2),
        }
        if components:
            params["components"] = components
        result = await bridge.send_command_async("pcb.create_room", params)
        return result

    @mcp.tool()
    async def pcb_get_board_statistics() -> dict[str, Any]:
        """Get comprehensive statistics for the active PCB board.

        Returns counts of all object types, total trace length, board
        dimensions, and layer count. Useful for design reviews and
        progress tracking.

        Returns:
            Dictionary with track_count, via_count, pad_count,
            component_count, fill_count, text_count, polygon_count,
            unrouted_connections, total_trace_length_mils,
            board_width_mils, board_height_mils, board_area_sq_mils,
            layer_count, and board_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.get_board_statistics", {})
        return result

    @mcp.tool()
    async def pcb_export_coordinates() -> dict[str, Any]:
        """Export component placement coordinates — same as pcb_get_components but formatted for pick-and-place.

        Returns designator, footprint, comment, position (x, y),
        rotation, layer, and side (Top/Bottom) for every component.
        Useful for manufacturing pick-and-place machine programming.

        Returns:
            Dictionary with "placements" array (each with designator,
            footprint, comment, x, y, rotation, layer, side) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("pcb.export_coordinates", {})
        return result

    @mcp.tool()
    async def pcb_create_diff_pair(
        positive_net: str,
        negative_net: str,
        name: str = "",
    ) -> dict[str, Any]:
        """Create a differential pair object from two existing nets.

        The two nets must already exist on the board (typically present
        after update_pcb / ECO). The diff-pair object lets Altium apply
        differential routing constraints and the interactive router to
        honour impedance / matched-length rules between the pair.

        Args:
            positive_net: Positive-side net name (e.g. "USB_DP")
            negative_net: Negative-side net name (e.g. "USB_DM")
            name: Optional diff-pair name (defaults to "<pos>_<neg>")

        Returns:
            Dictionary confirming creation with name, positive_net, negative_net
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.create_diff_pair",
            {
                "positive_net": positive_net,
                "negative_net": negative_net,
                "name": name,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_region(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        layer: str = "TopLayer",
        net: str = "",
    ) -> dict[str, Any]:
        """Place a solid copper region on a rectangular area.

        Regions are solid primitives; unlike polygons, they don't participate
        in the connectivity engine unless you assign a net. Use for
        mechanical copper zones, thermal pads, or solder-mask openings.
        For a true ground plane with ratsnest tracking, prefer
        pcb_place_polygon_rect.

        Args:
            x1, y1, x2, y2: Opposite corners in mils (any order)
            layer: Copper or mech layer (default "TopLayer")
            net: Optional net assignment

        Returns:
            Dictionary confirming the region placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_region",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "layer": layer,
                "net": net,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_dimension(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        layer: str = "TopOverlay",
        orientation: str = "",
    ) -> dict[str, Any]:
        """Place a linear dimension between two points.

        Horizontal dimension measures delta-X, vertical measures delta-Y.
        Orientation auto-detects from the larger axis delta if not given.

        Args:
            x1, y1: First reference point in mils
            x2, y2: Second reference point in mils
            layer: Layer to draw the dimension on (default "TopOverlay")
            orientation: "horizontal" or "vertical" ("" = auto)

        Returns:
            Dictionary confirming the dimension placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_dimension",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "layer": layer,
                "orientation": orientation,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_pad(
        x: int,
        y: int,
        name: str = "",
        net: str = "",
        shape: str = "round",
        x_size: int = 60,
        y_size: int = 60,
        hole_size: int = 0,
        layer: str = "TopLayer",
    ) -> dict[str, Any]:
        """Place a standalone pad on the active PCB.

        Not part of any component. Use for fiducials, test points,
        mounting holes. Set hole_size=0 for surface-mount pads,
        nonzero for through-hole.

        Args:
            x, y: Position in mils
            name: Pad designator / label (optional)
            net: Net to connect to (optional)
            shape: "round" (default) / "rect" / "oct"
            x_size, y_size: Pad dimensions in mils
            hole_size: Drill diameter in mils (0 = SMD)
            layer: Copper layer (default "TopLayer")

        Returns:
            Dictionary confirming pad placement
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_pad",
            {
                "x": str(x),
                "y": str(y),
                "name": name,
                "net": net,
                "shape": shape,
                "x_size": str(x_size),
                "y_size": str(y_size),
                "hole_size": str(hole_size),
                "layer": layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_angular_dimension(
        center_x: int,
        center_y: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int = 100,
        layer: str = "TopOverlay",
    ) -> dict[str, Any]:
        """Place an angular dimension (angle between two reference directions).

        Args:
            center_x, center_y: Vertex of the angle in mils
            x1, y1: First reference direction endpoint in mils
            x2, y2: Second reference direction endpoint in mils
            radius: Arc radius at which to draw the dimension in mils
            layer: Layer (default "TopOverlay")

        Returns:
            Dictionary confirming the angular dimension
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_angular_dimension",
            {
                "center_x": str(center_x),
                "center_y": str(center_y),
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "radius": str(radius),
                "layer": layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_place_radial_dimension(
        center_x: int,
        center_y: int,
        radius: int,
        layer: str = "TopOverlay",
    ) -> dict[str, Any]:
        """Place a radial dimension around a center point with a given radius.

        Args:
            center_x, center_y: Center point in mils
            radius: Radius to dimension in mils
            layer: Layer (default "TopOverlay")

        Returns:
            Dictionary confirming the radial dimension
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.place_radial_dimension",
            {
                "center_x": str(center_x),
                "center_y": str(center_y),
                "radius": str(radius),
                "layer": layer,
            },
        )
        return result

    @mcp.tool()
    async def pcb_distribute_components(
        designators: str,
        axis: str = "x",
        start: int = 0,
        end: int = 1000,
    ) -> dict[str, Any]:
        """Evenly space components along an axis.

        Moves each named component so its X (or Y) coordinate lands at
        equally-spaced stops from `start` to `end`. Order follows the
        designators list — designators="R1,R2,R3" with start=0 end=200
        places R1 at 0, R2 at 100, R3 at 200 on the chosen axis. Y (or X)
        is untouched.

        Args:
            designators: Comma-separated list of component designators
            axis: "x" or "y" (default "x")
            start: First position in mils
            end: Last position in mils

        Returns:
            Dictionary with distribution result
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "pcb.distribute_components",
            {
                "designators": designators,
                "axis": axis,
                "start": str(start),
                "end": str(end),
            },
        )
        return result
