# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Verify MCP tool docstrings don't recommend tools that don't exist.

Lying-docstring bugs (caught in cron iteration #76) take this shape::

    @mcp.tool()
    async def proj_run_erc():
        '''Compile and run ERC.

        Use ``get_erc_violations()`` afterwards to read the results.
        '''

If ``get_erc_violations()`` isn't actually a registered MCP tool, the
agent reads that suggestion, dutifully tries to call it, and hits a
"no such tool" error. The bug stays silent because the tool's own
test suite never exercises the recommendation.

This test walks every MCP tool docstring across ``src/eda_agent/tools/``
and verifies that every backticked function-with-parens reference is
either a registered MCP tool, a Python stdlib / well-known helper name,
or in the explicit allowlist below.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "src" / "eda_agent" / "tools"

# Backticked references that aren't MCP tools but appear in docstrings
# for legitimate reasons. Either Python stdlib, third-party libs, or
# generic verbs the docstring uses descriptively.
ALLOWED_NON_MCP_REFS = {
    # Generic / structural
    "main", "run", "init", "setup", "teardown",
    # Python builtins
    "len", "str", "int", "float", "list", "dict", "set", "tuple",
    "print", "open", "close", "range", "enumerate", "zip", "map", "filter",
    "isinstance", "hasattr", "getattr", "setattr",
    # asyncio / contextlib
    "await", "asyncio", "asynccontextmanager",
    # bridge / web stdlib
    "fetch", "WebFetch", "WebSearch", "send_command_async",
    "send_command", "bridge",
    # Altium / Pascal-side names users might reference
    "PreProcess", "PostProcess", "Begin", "End", "SchServer", "PCBServer",
    "GetCurrentPCBBoard", "GetCurrentSchDocument", "BoardIterator_Create",
    "SchIterator_Create", "GroupIterator_Create",
    # Common internal helpers that aren't @mcp.tool decorated
    "get_bridge", "tag_response", "_bundled_script_version",
    "_check_disconnected", "BulkHintTracker",
}


def _registered_mcp_tools() -> set[str]:
    """Names of every @mcp.tool()-decorated function across tools/."""
    names = set()
    for py_file in TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py" or ".tmp." in py_file.name:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                # Look for @mcp.tool() in the decorator list.
                for dec in node.decorator_list:
                    is_tool = False
                    if isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Attribute) and \
                                dec.func.attr == "tool":
                            is_tool = True
                    elif isinstance(dec, ast.Attribute):
                        if dec.attr == "tool":
                            is_tool = True
                    if is_tool:
                        names.add(node.name)
                        break
    return names


def _docstring_refs_in_file(py_file: Path) -> list[tuple[str, str, int]]:
    """Find `name()` backticked references inside @mcp.tool docstrings.

    Returns a list of (tool_name, referenced_name, line_number).
    """
    refs: list[tuple[str, str, int]] = []
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        # Must be @mcp.tool-decorated
        decorated = any(
            (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
             and dec.func.attr == "tool")
            or (isinstance(dec, ast.Attribute) and dec.attr == "tool")
            for dec in node.decorator_list
        )
        if not decorated:
            continue
        ds = ast.get_docstring(node)
        if not ds:
            continue
        # Match ``name()`` or ``name`` inside double-backticks
        # specifically when followed by () so we only catch
        # "call this function" references, not arbitrary mentions.
        for m in re.finditer(r"``([a-z_][a-z0-9_]*)\(\)``", ds):
            refs.append((node.name, m.group(1), node.lineno))
        for m in re.finditer(r"`([a-z_][a-z0-9_]*)\(\)`", ds):
            refs.append((node.name, m.group(1), node.lineno))
    return refs


def test_no_lying_docstring_function_refs():
    """Every ``name()`` reference in an MCP tool docstring resolves to a
    real MCP tool, a builtin, or an explicit allowlisted name."""
    registered = _registered_mcp_tools()
    bad: list[str] = []
    for py_file in TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py" or ".tmp." in py_file.name:
            continue
        for tool_name, ref_name, lineno in _docstring_refs_in_file(py_file):
            if ref_name in registered:
                continue
            if ref_name in ALLOWED_NON_MCP_REFS:
                continue
            bad.append(
                f"  {py_file.name}:{lineno} {tool_name}() docstring references "
                f"`{ref_name}()` which is not a registered MCP tool "
                f"and not in ALLOWED_NON_MCP_REFS."
            )
    assert not bad, (
        "MCP tool docstrings reference functions that don't exist:\n"
        + "\n".join(bad)
        + "\n\nEither (a) ship the referenced tool, (b) rename the "
          "reference to a tool that does exist, or (c) add it to "
          "ALLOWED_NON_MCP_REFS if it's a stdlib / generic verb."
    )


# ------------------- references written without parens ---------------
#
# The check above only matches ``name()``, i.e. an explicit "call this"
# reference. Measured against the current source that is 6 references
# out of 246 tool-shaped names appearing in tool docstrings, so a name
# written the usual way -- ``lib_add_pins``, no parens -- was never
# checked at all. That is where a typo or a renamed tool actually hides:
# the docstring keeps telling the agent to call something that no longer
# exists, and nothing notices.

#: Tool-shaped names in docstrings that are NOT tools. Mostly return
#: keys and parameter names, which share the prefix convention. An entry
#: here is a claim that the name is not meant to be callable.
NON_TOOL_NAMES = {
    # Return-dict keys.
    "sch_lib_found", "pcb_lib_found", "sch_lib_path", "pcb_lib_path",
    "pcb_only", "sch_only",
    # Parameter names. These are fields inside a call, not tools, and
    # lib_reference only looks like one because it shares the lib_ prefix
    # that the scan uses to recognise library tools.
    "lib_path", "lib_ref", "lib_reference",
}

#: Prefixes this server uses for tool names. A backticked identifier
#: starting with one of these is a tool reference unless allowlisted.
_TOOL_PREFIXES = (
    "lib_", "pcb_", "sch_", "proj_", "app_", "obj_", "audit_",
    "design_", "route_", "sim_",
)


def _backticked_tool_names():
    """(module, tool, referenced_name) for every backticked tool-shaped
    identifier in an MCP tool docstring, parens or not."""
    import ast

    out = []
    for py_file in sorted(TOOLS_DIR.glob("*.py")):
        if py_file.name == "__init__.py" or ".tmp." in py_file.name:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8",
                                           errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(node) or ""
            for m in re.finditer(r"``?([a-z_][a-z0-9_]{3,})(?:\(\))?``?", doc):
                name = m.group(1)
                if name.startswith(_TOOL_PREFIXES):
                    out.append((py_file.name, node.name, name))
    return out


def test_the_widened_scan_actually_sees_something():
    """Floor. Without one, a regex that stops matching turns the check
    below into a green no-op, which is exactly how the parens-only
    version came to be covering 6 references and looking healthy."""
    refs = _backticked_tool_names()
    assert len(refs) > 150, (
        f"only {len(refs)} backticked tool references found; the scan is "
        f"not seeing the docstrings it thinks it is")


def test_no_docstring_points_at_a_tool_that_does_not_exist():
    registered = _registered_mcp_tools()
    bad = [
        f"  {module}: {tool}() docstring references `{name}`, which is "
        f"not a registered MCP tool"
        for module, tool, name in _backticked_tool_names()
        if name not in registered
        and name not in ALLOWED_NON_MCP_REFS
        and name not in NON_TOOL_NAMES
    ]
    assert not bad, (
        "these docstrings send the agent to tools that do not exist:\n"
        + "\n".join(sorted(set(bad))))


def test_the_non_tool_allowlist_does_not_go_stale():
    """A name that became a real tool should leave the list, or it masks
    a future rename of that same tool."""
    registered = _registered_mcp_tools()
    now_real = sorted(n for n in NON_TOOL_NAMES if n in registered)
    assert not now_real, (
        f"these are registered tools now and should leave "
        f"NON_TOOL_NAMES: {now_real}")
