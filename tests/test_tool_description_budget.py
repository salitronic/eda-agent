# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each tool's description stays within a per-tool limit.

Every description is sent to the model whenever the surface is listed,
so a long one is a cost paid on every session. The listing as a whole
grows with the tools; no single description may.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from eda_agent.tools import register_backend

BACKENDS = ("altium", "easyeda", "kicad")

# One tool's description. Those already longer are capped at their size.
TOOL_LIMIT = 3_000
OVER_LIMIT = {
    "obj_modify": 7_021, "pcb_apply_geometry_map": 6_566, "obj_query": 5_355,
    "pcb_set_rule_properties": 4_975, "audit": 5_091, "pcb_calc": 4_556,
    "pcb_plan_placement": 4_171, "lib_set_pad_properties": 3_714,
    "lib_create_footprint_from_package": 3_229, "sch_editor_options": 3_223,
    "sch_preview_template": 3_095,
}


def _listing(backend: str):
    mcp = FastMCP(backend)
    register_backend(mcp, backend, "full")
    return asyncio.run(mcp.list_tools())


@pytest.mark.parametrize("backend", BACKENDS)
def test_no_description_grows_past_its_limit(backend):
    tools = _listing(backend)
    assert len(tools) > 50, "the listing came back almost empty"
    long = {t.name: len(t.description or "") for t in tools
            if len(t.description or "") > OVER_LIMIT.get(t.name, TOOL_LIMIT)}
    assert not long, (
        f"{long}: keep a description to what the tool does now, its "
        f"arguments, its reply and its hazards")


def test_schemas_carry_no_generated_titles():
    for tool in _listing("altium"):
        assert '"title": "' not in json.dumps(
            {k: v for k, v in tool.inputSchema.items() if k != "properties"})
        for prop in tool.inputSchema.get("properties", {}).values():
            assert "title" not in prop, tool.name
