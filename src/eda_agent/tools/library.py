# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Library management tools for Altium Designer MCP Server."""

from typing import Any, Optional
from ..bridge import get_bridge
from ..bridge.exceptions import InvalidParameterError
from ..config import get_config


def register_library_tools(mcp):
    """Register library tools with the MCP server."""

    # =========================================================================
    # Symbol Creation
    # =========================================================================

    @mcp.tool()
    async def lib_create_symbol(
        name: str,
        designator_prefix: str = "U",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new schematic symbol in the active library.

        Args:
            name: Component name
            designator_prefix: Default designator prefix (e.g., "U", "R", "C")
            description: Component description

        Returns:
            Dictionary with created symbol information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.create_symbol",
            {
                "name": name,
                "designator_prefix": designator_prefix,
                "description": description,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_pin(
        designator: str,
        name: str,
        x: int,
        y: int,
        length: int = 200,
        rotation: int = 0,
        electrical_type: str = "passive",
        hidden: bool = False,
    ) -> dict[str, Any]:
        """Add a pin to the current symbol.

        Args:
            designator: Pin designator (e.g., "1", "2", "VCC")
            name: Pin name
            x: X coordinate in mils
            y: Y coordinate in mils
            length: Pin length in mils
            rotation: Pin rotation in degrees (0, 90, 180, 270)
            electrical_type: Electrical type:
                - "input", "output", "bidirectional", "passive"
                - "open_collector", "open_emitter", "power", "hiz"
            hidden: Whether to hide the pin

        Returns:
            Dictionary confirming pin addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_pin",
            {
                "designator": designator,
                "name": name,
                "x": x,
                "y": y,
                "length": length,
                "rotation": rotation,
                "electrical_type": electrical_type,
                "hidden": hidden,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_rectangle(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        fill_color: int = -1,
        border_color: int = 0,
    ) -> dict[str, Any]:
        """Add a rectangle to the current symbol body.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            fill_color: Fill color index (-1 = no fill)
            border_color: Border color index

        Returns:
            Dictionary confirming rectangle addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_rectangle",
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "fill_color": fill_color,
                "border_color": border_color,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_line(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        width: int = 1,
    ) -> dict[str, Any]:
        """Add a line to the current symbol.

        Args:
            x1: Start X in mils
            y1: Start Y in mils
            x2: End X in mils
            y2: End Y in mils
            width: Line width

        Returns:
            Dictionary confirming line addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_line",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": width},
        )
        return result

    # =========================================================================
    # Footprint Creation
    # =========================================================================

    @mcp.tool()
    async def lib_create_footprint(
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new PCB footprint in the active library.

        Args:
            name: Footprint name
            description: Footprint description

        Returns:
            Dictionary with created footprint information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.create_footprint",
            {"name": name, "description": description},
        )
        return result

    @mcp.tool()
    async def lib_add_footprint_pad(
        designator: str,
        x: int,
        y: int,
        x_size: int = 60,
        y_size: int = 60,
        hole_size: int = 0,
        shape: str = "rectangular",
        layer: str = "TopLayer",
        rotation: int = 0,
    ) -> dict[str, Any]:
        """Add a pad to the current footprint.

        Args:
            designator: Pad designator (e.g., "1", "2")
            x: X coordinate in mils
            y: Y coordinate in mils
            x_size: Pad X size in mils
            y_size: Pad Y size in mils
            hole_size: Drill hole size in mils (0 for SMD)
            shape: Pad shape ("round", "rectangular", "octagonal")
            layer: Layer ("TopLayer", "BottomLayer", "MultiLayer")
            rotation: Pad rotation in degrees

        Returns:
            Dictionary confirming pad addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_pad",
            {
                "designator": designator,
                "x": x,
                "y": y,
                "x_size": x_size,
                "y_size": y_size,
                "hole_size": hole_size,
                "shape": shape,
                "layer": layer,
                "rotation": rotation,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_footprint_track(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        width: int = 10,
        layer: str = "TopOverlay",
    ) -> dict[str, Any]:
        """Add a track to the current footprint (for silkscreen/courtyard).

        Args:
            x1: Start X in mils
            y1: Start Y in mils
            x2: End X in mils
            y2: End Y in mils
            width: Track width in mils
            layer: Layer (typically TopOverlay for silkscreen)

        Returns:
            Dictionary confirming track addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_track",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": width, "layer": layer},
        )
        return result

    @mcp.tool()
    async def lib_add_footprint_arc(
        x_center: int,
        y_center: int,
        radius: int,
        start_angle: float = 0,
        end_angle: float = 360,
        width: int = 10,
        layer: str = "TopOverlay",
    ) -> dict[str, Any]:
        """Add an arc to the current footprint.

        Args:
            x_center: Center X in mils
            y_center: Center Y in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            width: Line width in mils
            layer: Layer for the arc

        Returns:
            Dictionary confirming arc addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_arc",
            {
                "x_center": x_center,
                "y_center": y_center,
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "width": width,
                "layer": layer,
            },
        )
        return result

    # =========================================================================
    # Component Linking
    # =========================================================================

    @mcp.tool()
    async def lib_link_footprint(
        component_name: str,
        footprint_name: str,
        footprint_library: str = "",
    ) -> dict[str, Any]:
        """Link a footprint to a schematic component.

        NOTE: Uses the current active library component, not the specified
        component_name. Open/focus the target component in the SchLib editor
        before calling this.

        Args:
            component_name: Name of the schematic component (currently ignored —
                see note above)
            footprint_name: Name of the footprint to link
            footprint_library: Library containing the footprint (optional if same library)

        Returns:
            Dictionary confirming link
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.link_footprint",
            {
                "component_name": component_name,
                "footprint_name": footprint_name,
                "library_name": footprint_library,
            },
        )
        return result

    @mcp.tool()
    async def lib_link_3d_model(
        component_name: str,
        model_path: str,
        offset_x: float = 0,
        offset_y: float = 0,
        offset_z: float = 0,
        rotation_x: float = 0,
        rotation_y: float = 0,
        rotation_z: float = 0,
    ) -> dict[str, Any]:
        """Link a 3D model to a footprint.

        NOTE: offset and rotation parameters are currently ignored by Altium —
        set them manually in the library after linking.

        Args:
            component_name: Name of the footprint
            model_path: Path to the 3D model file (.step, .stp)
            offset_x: X offset in mils (ignored — see note)
            offset_y: Y offset in mils (ignored — see note)
            offset_z: Z offset in mils (ignored — see note)
            rotation_x: X rotation in degrees (ignored — see note)
            rotation_y: Y rotation in degrees (ignored — see note)
            rotation_z: Z rotation in degrees (ignored — see note)

        Returns:
            Dictionary confirming link
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.link_3d_model",
            {
                "component_name": component_name,
                "model_path": model_path,
                "offset_x": offset_x,
                "offset_y": offset_y,
                "offset_z": offset_z,
                "rotation_x": rotation_x,
                "rotation_y": rotation_y,
                "rotation_z": rotation_z,
            },
        )
        return result

    # =========================================================================
    # Library Search and Information
    # =========================================================================

    @mcp.tool()
    async def lib_get_components(library_path: Optional[str] = None) -> dict[str, Any]:
        """Get all components in a library.

        Args:
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with "count" and "components" list
        """
        bridge = get_bridge()
        params = {}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async("library.get_components", params)
        return result or {}

    @mcp.tool()
    async def lib_search(
        query: str,
        search_type: str = "all",
    ) -> list[dict[str, Any]]:
        """Search installed libraries for components.

        Args:
            query: Search query string
            search_type: What to search ("all", "name", "description", "parameters")

        Returns:
            List of matching component dictionaries
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.search", {"query": query, "search_type": search_type}
        )
        return result

    @mcp.tool()
    async def lib_get_component_details(
        component_name: str,
        library_path: str,
    ) -> dict[str, Any]:
        """Get detailed information about a library component.

        NOTE: Uses the focused library document, not library_path. Open the
        target library in Altium before calling.

        Args:
            component_name: Name of the component
            library_path: Path to the library (currently ignored — see note)

        Returns:
            Dictionary with full component details including pins and parameters
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.get_component_details",
            {"component_name": component_name, "library_path": library_path},
        )
        return result

    @mcp.tool()
    async def lib_batch_set_params(
        assignments: list[dict[str, str]],
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch set parameters on library components.

        Each assignment sets one parameter on one component.
        If the parameter exists it is updated; if not it is created.

        Args:
            assignments: List of dicts with keys:
                - component_name: Name of the component in the library
                - param_name: Parameter name (e.g., "Partnumber", "Manufacturer")
                - param_value: Value to set
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with counts of updated, created, and failed assignments
        """
        config = get_config()
        config.ensure_workspace()
        batch_path = config.workspace_dir / "batch_params.txt"

        # Validate keys and values before writing
        required_keys = {"component_name", "param_name", "param_value"}
        for i, a in enumerate(assignments):
            missing = required_keys - set(a.keys())
            if missing:
                raise InvalidParameterError(
                    f"Assignment {i} is missing required keys: {', '.join(sorted(missing))}"
                )
            for key in required_keys:
                if "|" in str(a[key]):
                    raise InvalidParameterError(
                        f"Assignment {i}: '{key}' value contains pipe character '|' which would corrupt the batch file"
                    )

        with open(batch_path, "w", encoding="latin-1") as f:
            for a in assignments:
                f.write(f"{a['component_name']}|{a['param_name']}|{a['param_value']}\n")

        bridge = get_bridge()
        params = {"batch_file": str(batch_path)}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.batch_set_params", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def lib_batch_rename(
        assignments: list[dict[str, str]],
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch rename components in a schematic library.

        Each assignment renames one component from old_name to new_name.

        Args:
            assignments: List of dicts with keys:
                - old_name: Current name of the component in the library
                - new_name: New name for the component
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with counts of renamed and failed assignments
        """
        config = get_config()
        config.ensure_workspace()
        batch_path = config.workspace_dir / "batch_rename.txt"

        # Validate keys and values before writing
        required_keys = {"old_name", "new_name"}
        for i, a in enumerate(assignments):
            missing = required_keys - set(a.keys())
            if missing:
                raise InvalidParameterError(
                    f"Assignment {i} is missing required keys: {', '.join(sorted(missing))}"
                )
            for key in required_keys:
                if "|" in str(a[key]):
                    raise InvalidParameterError(
                        f"Assignment {i}: '{key}' value contains pipe character '|' which would corrupt the batch file"
                    )

        with open(batch_path, "w", encoding="latin-1") as f:
            for a in assignments:
                f.write(f"{a['old_name']}|{a['new_name']}\n")

        bridge = get_bridge()
        params = {"batch_file": str(batch_path)}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.batch_rename", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def lib_diff_libraries(
        library_a: str,
        library_b: str,
    ) -> dict[str, Any]:
        """Compare two schematic libraries and report differences.

        Returns which components are only in library A, only in B, or shared.

        Args:
            library_a: Full path to the first SchLib file
            library_b: Full path to the second SchLib file

        Returns:
            Dictionary with only_in_a, only_in_b, common arrays,
            and count_a, count_b, only_a, only_b, shared counts
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.diff_libraries",
            {"library_a": library_a, "library_b": library_b},
            timeout=60.0,
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_arc(
        x_center: int,
        y_center: int,
        radius: int,
        start_angle: float = 0,
        end_angle: float = 360,
        width: int = 1,
    ) -> dict[str, Any]:
        """Add an arc to the current library symbol.

        Args:
            x_center: Center X coordinate in mils
            y_center: Center Y coordinate in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees (0 = right, 90 = up)
            end_angle: End angle in degrees
            width: Line width (0=zero, 1=small, 2=medium, 3=large)

        Returns:
            Dictionary confirming arc addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_arc",
            {
                "x_center": x_center,
                "y_center": y_center,
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "width": width,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_polygon(
        vertices: str,
    ) -> dict[str, Any]:
        """Add a polygon (filled shape) to the current library symbol.

        Args:
            vertices: Comma-separated x,y coordinate pairs in mils.
                Example: "0,0,100,0,100,100,0,100" creates a square with
                vertices at (0,0), (100,0), (100,100), (0,100).
                Minimum 3 vertices (6 values) required.

        Returns:
            Dictionary confirming polygon addition with vertex count
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_polygon",
            {"vertices": vertices},
        )
        return result

    @mcp.tool()
    async def lib_set_component_description(
        component_name: str,
        description: str,
    ) -> dict[str, Any]:
        """Set the description field on a library component.

        Args:
            component_name: Name of the component in the active library
            description: New description text

        Returns:
            Dictionary confirming the description was set
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.set_component_description",
            {"component_name": component_name, "description": description},
        )
        return result

    @mcp.tool()
    async def lib_get_pin_list() -> dict[str, Any]:
        """Get all pins of the current library component.

        Returns:
            Dictionary with "count", "component" name, and "pins" array.
            Each pin has: designator, name, electrical_type, x, y,
            orientation, hidden
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.get_pin_list", {}
        )
        return result

    @mcp.tool()
    async def lib_copy_component(
        source_name: str,
        new_name: str,
    ) -> dict[str, Any]:
        """Duplicate a component within the same schematic library.

        Creates a deep copy of the source component (including all pins,
        graphics, and parameters) and adds it to the library with the
        new name. The new component becomes the active component.

        Args:
            source_name: Name of the existing component to copy
            new_name: Name for the new component (must not already exist)

        Returns:
            Dictionary confirming the copy with source and new_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.copy_component",
            {"source_name": source_name, "new_name": new_name},
        )
        return result
