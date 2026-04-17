# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Application-level tools for Altium Designer MCP Server."""

import re
from functools import lru_cache
from typing import Any, Optional
from .. import __version__ as _mcp_server_version
from ..bridge import get_bridge, AltiumNotRunningError
from ..cli import get_bundled_scripts_path


_VERSION_RE = re.compile(r"SCRIPT_VERSION\s*=\s*'([^']+)'")


@lru_cache(maxsize=1)
def _bundled_script_version() -> Optional[str]:
    """Read SCRIPT_VERSION from the bundled Main.pas.

    Returns None if the file can't be found or parsed — in which case we
    skip the stale-cache comparison and just report whatever Altium
    reported.
    """
    try:
        main_pas = get_bundled_scripts_path() / "Main.pas"
        text = main_pas.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


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
        """Test if the Altium script is responding and report script version.

        Verifies that:
        1. Altium Designer is running
        2. The Altium_API.PrjScr script is running (StartMCPServer)
        3. File-based communication is working

        Also reads SCRIPT_VERSION from the .pas that Altium has compiled
        and compares it to the version in the bundled on-disk Main.pas.
        A mismatch means Altium is running a stale cached script — close
        and reopen Altium_API.PrjScr (or restart Altium) to recompile.

        Returns:
            Dictionary with:
            - success: True if Altium responded
            - mcp_server_version: version of this eda-agent Python package
              (from `eda_agent.__version__`) — identifies the MCP server
              process currently handling tool calls
            - altium_script_version: version the running script reports
              (empty string if the script is too old to report it)
            - bundled_script_version: version of the on-disk Main.pas
            - version_match: True if Altium matches bundled script version
            - message: human-readable status (flags stale cache if detected)
        """
        bridge = get_bridge()
        if not bridge.is_altium_running():
            return {
                "success": False,
                "mcp_server_version": _mcp_server_version,
                "altium_script_version": None,
                "bundled_script_version": _bundled_script_version(),
                "version_match": False,
                "message": "Altium Designer is not running",
            }

        result = bridge.ping_with_version()
        bundled = _bundled_script_version()
        if result is None:
            return {
                "success": False,
                "mcp_server_version": _mcp_server_version,
                "altium_script_version": None,
                "bundled_script_version": bundled,
                "version_match": False,
                "message": "Altium script is not responding. Run StartMCPServer in Altium_API.PrjScr.",
            }

        altium_ver = result.get("script_version") or ""
        if bundled is None:
            match = False
            msg = "Altium script is responding (bundled version unknown)."
        elif altium_ver == "":
            match = False
            msg = (
                "Altium script is responding but predates version reporting. "
                "Close and reopen Altium_API.PrjScr to pick up the new code."
            )
        elif altium_ver == bundled:
            match = True
            msg = f"Altium script is responding (version {altium_ver})."
        else:
            match = False
            msg = (
                f"STALE SCRIPT CACHE: Altium is running version {altium_ver}, "
                f"but the on-disk bundle is {bundled}. Close and reopen "
                f"Altium_API.PrjScr (or restart Altium) to recompile."
            )

        return {
            "success": True,
            "mcp_server_version": _mcp_server_version,
            "altium_script_version": altium_ver,
            "bundled_script_version": bundled,
            "version_match": match,
            "message": msg,
        }

    @mcp.tool()
    async def get_open_documents() -> list[dict[str, Any]]:
        """List all documents known to the current Altium workspace.

        Returns both project members and any free documents. Each entry
        carries a `loaded` flag that distinguishes "listed as project
        member on disk" from "actually resident in the editor".
        Project-scope queries (query_objects, batch_modify, ...) only
        iterate loaded sheets — if `loaded` is false for sheets you need
        to hit, call load_project_sheets first.

        Returns:
            List of document information dictionaries containing:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document (SCH, PCB, etc.)
            - loaded: True if the doc is resident in the editor server.
              False means it's a project member on disk whose editor
              state hasn't been opened yet.
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
