# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""MCP Tools for Altium Designer."""

from .application import register_application_tools
from .project import register_project_tools
from .library import register_library_tools
from .generic import register_generic_tools
from .pcb import register_pcb_tools


def register_all_tools(mcp):
    """Register all Altium tools with the MCP server."""
    register_application_tools(mcp)
    register_project_tools(mcp)
    register_library_tools(mcp)
    register_generic_tools(mcp)
    register_pcb_tools(mcp)


__all__ = [
    "register_all_tools",
    "register_application_tools",
    "register_project_tools",
    "register_library_tools",
    "register_generic_tools",
    "register_pcb_tools",
]
