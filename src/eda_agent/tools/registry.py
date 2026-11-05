# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A capturing stand-in for the MCP server, used by the minimal toolset.

Some MCP clients cap how many tools a server may expose, or serialize
every schema into the model context at startup and slow to a crawl. This
server registers several hundred, so those clients cannot use it at all.

The fix is NOT to merge tools into generic dispatchers: the specialised
names, descriptions and schemas are what let a model find the right
operation and follow the per-tool discipline, and collapsing them
measurably degrades that. Instead, ``minimal`` keeps every tool intact
but stops ADVERTISING them, exposing only ``tool_catalog`` (discover)
and ``tool_invoke`` (run by name).

:class:`ToolRegistry` is what makes that possible. It quacks like the
FastMCP object the registrars expect:

* ``tool()`` captures the decorated function instead of publishing it
* ``list_tools()`` and ``call_tool()`` answer from what it captured

Because ``tool_catalog`` and ``tool_invoke`` close over the object they
were registered against, handing them a registry makes them discover and
dispatch across the FULL set while the client only ever sees two tools.
Neither function needs to know which mode it is running in.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Optional

__all__ = ["MINIMAL_TOOLS", "ToolRegistry", "ToolSpec"]

#: The only tools a minimal server advertises. ``tool_catalog`` finds an
#: operation, ``tool_invoke`` runs it, and between them they reach
#: everything else.
MINIMAL_TOOLS = ("tool_catalog", "tool_invoke")


def _json_type(annotation: Any) -> str:
    """Best-effort JSON type name for a Python annotation."""
    text = str(annotation)
    if annotation is inspect.Parameter.empty:
        return "any"
    # The container first: "list[str]" is an array, not a string.
    for needle, name in (("list", "array"), ("dict", "object"),
                         ("bool", "boolean"), ("int", "integer"),
                         ("float", "number"), ("str", "string")):
        if needle in text:
            return name
    return "any"


@dataclass
class ToolSpec:
    """The subset of a FastMCP tool that the meta-tools actually read."""

    name: str
    description: str
    fn: Callable

    @property
    def inputSchema(self) -> dict[str, Any]:
        """A JSON-Schema-ish view, matching what FastMCP exposes.

        Same attribute name and shape as ``mcp.types.Tool.inputSchema``
        so ``tool_catalog`` can read parameters identically whether it is
        looking at a real MCP tool or a captured one.
        """
        props: dict[str, Any] = {}
        required: list[str] = []
        try:
            sig = inspect.signature(self.fn)
        except (TypeError, ValueError):
            return {"type": "object", "properties": {}, "required": []}
        for name, param in sig.parameters.items():
            if name == "self" or param.kind in (
                    param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            entry: dict[str, Any] = {"type": _json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(name)
            elif isinstance(param.default, (str, int, float, bool)):
                entry["default"] = param.default
            props[name] = entry
        return {"type": "object", "properties": props, "required": required}


class ToolRegistry:
    """Collects tool registrations and can dispatch to them by name."""

    def __init__(self, delegate: Optional[Any] = None) -> None:
        """
        Args:
            delegate: when given, every captured tool is ALSO registered
                on this object. That keeps a full-surface server working
                while still building the index.
        """
        self._delegate = delegate
        self._tools: dict[str, ToolSpec] = {}
        self._meta: dict[str, Any] = {}

    # -- registration -------------------------------------------------
    def tool(self, *args: Any, **kwargs: Any):
        """Stand in for ``@mcp.tool()``."""
        def deco(fn: Callable) -> Callable:
            # The tool as an advertised one runs: with ``items`` (bulk.py).
            from .bulk import with_items
            self._tools[fn.__name__] = ToolSpec(
                name=fn.__name__,
                description=inspect.getdoc(fn) or "",
                fn=with_items(fn, fn.__name__),
            )
            if self._delegate is not None:
                self._delegate.tool(*args, **kwargs)(fn)
            return fn
        return deco

    def prompt(self, *args: Any, **kwargs: Any):
        """Pass prompts through; they are not part of the tool surface."""
        if self._delegate is not None and hasattr(self._delegate, "prompt"):
            return self._delegate.prompt(*args, **kwargs)
        return lambda fn: fn

    def resource(self, *args: Any, **kwargs: Any):
        if self._delegate is not None and hasattr(self._delegate, "resource"):
            return self._delegate.resource(*args, **kwargs)
        return lambda fn: fn

    # -- the read side the meta-tools use -----------------------------
    async def list_tools(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    async def call_tool(self, name: str, arguments: Optional[dict] = None):
        """Invoke a captured tool by name.

        Returns the tool's own return value. ``tool_invoke`` already
        handles both this shape and FastMCP's content-list shape, so no
        wrapping is needed here.
        """
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"unknown tool: {name}")
        # The arguments checked against the tool's types, as the advertised
        # tool's are: a bare "A" for a list[str] is refused, not walked as
        # the list "A".
        meta = self._meta.get(name)
        if meta is None:
            from mcp.server.fastmcp.utilities.func_metadata import func_metadata
            meta = self._meta[name] = func_metadata(spec.fn)
        return await meta.call_fn_with_arg_validation(
            spec.fn, inspect.iscoroutinefunction(spec.fn), arguments or {}, None)

    # -- plain accessors ----------------------------------------------
    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
