# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Application-level tools for Altium Designer MCP Server."""

from typing import Any
from ..bridge import get_bridge, AltiumNotRunningError


def register_application_tools(mcp):
    """Register application tools with the MCP server."""

    @mcp.tool()
    async def get_altium_status() -> dict[str, Any]:
        """Check if Altium Designer is running and get status information.

        Returns information about the Altium Designer process including:
        - Whether Altium is running
        - Process ID
        - Executable path
        - Whether the MCP bridge is attached

        Returns:
            Dictionary with status information
        """
        bridge = get_bridge()
        return bridge.get_altium_status()

    @mcp.tool()
    async def attach_to_altium() -> dict[str, Any]:
        """Connect to a running Altium Designer instance.

        This verifies Altium is running and the polling script is responding.
        The Altium_API.PrjScr script must be running (StartMCPServer) in Altium.

        Returns:
            Dictionary with attachment status
        """
        bridge = get_bridge()
        try:
            bridge.attach()
            script_loaded = bridge.ping()
            return {
                "attached": True,
                "script_loaded": script_loaded,
                "message": "Connected to Altium Designer — script is responding"
                if script_loaded
                else "Altium is running but script is not responding. Run StartMCPServer in Altium_API.PrjScr.",
            }
        except AltiumNotRunningError as e:
            return {
                "attached": False,
                "script_loaded": False,
                "message": str(e),
            }

    @mcp.tool()
    async def detach_from_altium() -> dict[str, Any]:
        """Disconnect from Altium Designer and stop the MCP server script.

        Sends a stop command to the Altium polling script so it exits cleanly,
        then detaches the Python bridge.

        Returns:
            Dictionary confirming detachment
        """
        bridge = get_bridge()
        try:
            await bridge.send_command_async("application.stop_server", timeout=3.0)
        except Exception:
            pass  # Server may already be stopped
        bridge.detach()
        return {
            "attached": False,
            "message": "Detached from Altium Designer and stopped MCP server",
        }

    @mcp.tool()
    async def ping_altium() -> dict[str, Any]:
        """Test if the Altium script is responding.

        This verifies that:
        1. Altium Designer is running
        2. The Altium_API.PrjScr script is running (StartMCPServer)
        3. File-based communication is working

        Returns:
            Dictionary with ping result
        """
        bridge = get_bridge()
        if not bridge.is_altium_running():
            return {
                "success": False,
                "message": "Altium Designer is not running",
            }

        success = bridge.ping()
        return {
            "success": success,
            "message": "Altium script is responding"
            if success
            else "Altium script is not responding. Run StartMCPServer in Altium_API.PrjScr.",
        }

    @mcp.tool()
    async def get_open_documents() -> list[dict[str, Any]]:
        """List all currently open documents in Altium Designer.

        Returns:
            List of document information dictionaries containing:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document (SchDoc, PcbDoc, etc.)
            - modified: Whether the document has unsaved changes
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_open_documents")
        return result

    @mcp.tool()
    async def get_active_document() -> dict[str, Any]:
        """Get information about the currently active (focused) document.

        Returns:
            Dictionary with active document information:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document (SchDoc, PcbDoc, etc.)
            - modified: Whether the document has unsaved changes
            Returns empty dict if no document is active.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_active_document")
        return result

    @mcp.tool()
    async def set_active_document(file_path: str) -> dict[str, Any]:
        """Set a specific document as the active (focused) document.

        Args:
            file_path: Full path to the document to activate

        Returns:
            Dictionary with result of the operation
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.set_active_document", {"file_path": file_path}
        )
        if isinstance(result, dict):
            return {"success": True, "file_path": file_path, **result}
        elif result:
            return {"success": True, "file_path": file_path, "data": result}
        else:
            return {"success": True, "file_path": file_path}

    @mcp.tool()
    async def get_altium_version() -> dict[str, Any]:
        """Get the version of Altium Designer.

        Uses Client.GetProductVersion internally. If that API is unavailable
        (older builds or restricted script context), the returned dictionary
        will omit "version" and include a "note" field instead.

        Returns:
            Dictionary with product_name and either:
            - version: Full version string (when Client.GetProductVersion works)
            - note: Explanation when the version API is unavailable
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_version")
        return result

    # ------------------------------------------------------------------
    # Preferences, menu execution, clipboard
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_preferences() -> dict[str, Any]:
        """Get key Altium Designer preferences.

        Returns PCB preferences (snap grid, display unit) from the active board
        and schematic preferences (visible/snap grid) from the active schematic.
        Values are null if no PCB or schematic is currently open.

        Returns:
            Dictionary with "pcb" and "schematic" sub-objects containing
            grid and unit settings
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_preferences")
        return result

    @mcp.tool()
    async def execute_menu(menu_path: str) -> dict[str, Any]:
        """Execute a menu command by its path.

        Supports common menu paths which are mapped to internal processes:
        - "File|Save All"
        - "Tools|Design Rule Check"
        - "Tools|Electrical Rules Check"
        - "Project|Compile"
        - "Edit|Select All" / "Edit|Deselect All"
        - "View|Zoom Fit"
        - "Tools|Preferences"
        - "Tools|Extensions and Updates"

        Unknown paths are attempted via Client.SendMessage.

        Args:
            menu_path: Menu path using pipe separators (e.g., "File|Save All")

        Returns:
            Dictionary with success status, menu_path, and process used
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.execute_menu", {"menu_path": menu_path}
        )
        if isinstance(result, dict):
            return {"success": True, **result}
        return result or {"success": True, "menu_path": menu_path}

    @mcp.tool()
    async def get_clipboard_text() -> dict[str, Any]:
        """Get text content from the Windows clipboard.

        Returns whatever text is currently on the clipboard, which can be
        useful for reading data copied from Altium dialogs or reports.

        Returns:
            Dictionary with "text" containing the clipboard content.
            Returns empty string if clipboard is empty or non-text.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_clipboard_text")
        return result
