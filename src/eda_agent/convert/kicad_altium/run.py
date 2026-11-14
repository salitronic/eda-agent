# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run an import plan: each step is one of this server's own tools."""
from __future__ import annotations

import json
from typing import Any

#: Tools whose own argument is a list: steps in a row join their lists
#: when every other argument is the same.
LIST_ARGS = {"lib_add_symbol_lines": "lines", "lib_add_footprint_tracks": "tracks",
             "lib_add_footprint_regions": "regions"}

#: Tools that act on the current symbol, footprint or sheet, one object
#: per call: steps in a row become one call with ``items``, which Altium
#: runs in order as one edit.
ITEM_TOOLS = frozenset({
    "lib_add_symbol_rectangle", "lib_add_symbol_arc", "lib_add_symbol_polygon",
    "lib_add_symbol_text", "lib_add_footprint_arc", "lib_add_footprint_text",
    "sch_place_sheet_entry", "sch_place_no_erc", "sch_place_port",
    "lib_link_footprint"})

#: The most items one call carries (Altium runs up to 200 as one edit).
MAX_ITEMS = 200


def gather_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same steps in the same order, with each run of one tool in a
    row made into one call: a list tool's lists joined, any other tool in
    ITEM_TOOLS given its steps as ``items``."""
    out: list[dict[str, Any]] = []
    for step in steps:
        tool, args = step["tool"], step["args"]
        last = out[-1] if out else None
        if last is not None and last["tool"] == tool:
            key = LIST_ARGS.get(tool)
            if key and isinstance(args.get(key), list) and \
                    {k: v for k, v in last["args"].items() if k != key} == \
                    {k: v for k, v in args.items() if k != key}:
                last["args"][key] = last["args"][key] + args[key]
                continue
            if tool in ITEM_TOOLS and "items" not in args:
                items = last["args"].get("items") if set(last["args"]) == {"items"} else None
                if items is None:
                    last["args"] = {"items": [last["args"]]}
                    items = last["args"]["items"]
                if len(items) < MAX_ITEMS:
                    items.append(dict(args))
                    continue
        out.append({**step, "args": dict(args)})
    return out


def _unwrap(result: Any) -> Any:
    """A tool's own reply out of what FastMCP's call_tool hands back."""
    content = result[0] if isinstance(result, tuple) else result
    if isinstance(content, list) and content and hasattr(content[0], "text"):
        try:
            return json.loads(content[0].text)
        except (ValueError, TypeError):
            return content[0].text
    return content


def failed(reply: Any) -> bool:
    """True for a reply that says the step did not do its work."""
    if not isinstance(reply, dict):
        return False
    if reply.get("success") is False or reply.get("ok") is False:
        return True
    if reply.get("error") and reply.get("success") is not True:
        return True
    n = reply.get("failed")
    return isinstance(n, int) and not isinstance(n, bool) and n > 0


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        if isinstance(v, (list, dict)):
            out[k] = f"<{type(v).__name__} of {len(v)}>"
        else:
            out[k] = v
    return out


async def run_steps(mcp, steps: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    """Run ``steps`` in order, a run of one tool as one call
    (``gather_steps``), and stop at the first that fails."""
    steps = gather_steps(steps)
    done = 0
    for i, step in enumerate(steps):
        try:
            reply = _unwrap(await mcp.call_tool(step["tool"], step["args"]))
        except Exception as exc:  # noqa: BLE001 - a tool error is a report
            reply = {"success": False, "error": str(exc)}
        if failed(reply):
            return {"ok": False, "stage": stage, "done": done, "of": len(steps),
                    "failed_step": {"index": i, "tool": step["tool"],
                                    "args": _brief(step["args"]), "reply": reply}}
        done += 1
    return {"ok": True, "stage": stage, "done": done, "of": len(steps)}


async def call(mcp, tool: str, args: dict[str, Any]) -> Any:
    try:
        return _unwrap(await mcp.call_tool(tool, args))
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
