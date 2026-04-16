# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""EDA Agent MCP Server - Main entry point + CLI subcommands."""

import argparse
import logging
import sys
from mcp.server.fastmcp import FastMCP

from .tools import register_all_tools
from .config import get_config

logger = logging.getLogger("eda_agent")


def setup_logging() -> None:
    """Configure logging for the MCP server."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root = logging.getLogger("eda_agent")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# Create global FastMCP instance
mcp = FastMCP("eda-agent")

# Register all tools
register_all_tools(mcp)


def serve_mcp() -> int:
    """Start the MCP server on stdio. This is the default mode -- it's
    what an MCP-compatible client calls when it invokes `eda-agent` with no args."""
    setup_logging()
    logger.info("Starting EDA Agent MCP Server")

    config = get_config()
    config.ensure_workspace()
    logger.info("Workspace directory: %s", config.workspace_dir)

    mcp.run(transport="stdio")
    return 0


def main() -> int:
    """CLI entry point.

    Subcommands:
      serve             -- run the MCP server (default when no args given)
      scripts-path      -- print the path to the bundled DelphiScript files
      install-scripts   -- copy bundled scripts to a chosen directory

    IMPORTANT: when invoked with no arguments, this MUST start the MCP
    server on stdio -- MCP-compatible clients rely on that behaviour.
    """
    parser = argparse.ArgumentParser(
        prog="eda-agent",
        description=(
            "MCP server bridge for Altium Designer. "
            "Run with no arguments to start the MCP server on stdio."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # serve -- default when no args given
    subparsers.add_parser(
        "serve",
        help="Run the MCP server on stdio (default when no args given)",
    )

    # scripts-path
    subparsers.add_parser(
        "scripts-path",
        help="Print the path to the bundled DelphiScript files",
    )

    # install-scripts
    install_p = subparsers.add_parser(
        "install-scripts",
        help="Copy bundled scripts to a directory of your choice",
    )
    install_p.add_argument(
        "--dest",
        help=r"Destination directory (default: %%USERPROFILE%%\EDA Agent\scripts)",
    )
    install_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing scripts without prompting",
    )

    args = parser.parse_args()

    if args.command is None or args.command == "serve":
        return serve_mcp()

    # Lazy import -- keeps the hot stdio path free of CLI-only deps.
    from . import cli

    if args.command == "scripts-path":
        return cli.cmd_scripts_path()
    if args.command == "install-scripts":
        return cli.cmd_install_scripts(dest=args.dest, force=args.force)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
