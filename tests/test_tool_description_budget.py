# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The tool listing a client loads stays within a budget that only shrinks.

Every description and input schema is sent to the model whenever the
full surface is listed, so its size is a cost paid on every session. The
budgets below are the sizes measured when this guard was added. Lower
them when a change trims the text; a change that grows the listing past
them has to trim something else first.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from eda_agent.tools import register_backend

# Characters of description plus input schema over the full listing.
BUDGET = {"altium": 890_463, "easyeda": 370_386, "kicad": 189_782}

# One tool's description. Those already longer are capped at their size.
TOOL_LIMIT = 3_000
OVER_LIMIT = {
    "obj_modify": 7_021, "pcb_apply_geometry_map": 6_566, "obj_query": 5_208,
    "pcb_set_rule_properties": 4_975, "audit": 4_945, "pcb_calc": 4_556,
    "pcb_plan_placement": 4_171, "lib_set_pad_properties": 3_714,
    "lib_create_footprint_from_package": 3_229, "sch_editor_options": 3_223,
    "sch_preview_template": 3_095,
}


def _listing(backend: str):
    mcp = FastMCP(backend)
    register_backend(mcp, backend, "full")
    return asyncio.run(mcp.list_tools())


@pytest.mark.parametrize("backend", sorted(BUDGET))
def test_the_listing_fits_its_budget(backend):
    tools = _listing(backend)
    assert len(tools) > 50, "the listing came back almost empty"
    size = sum(len(t.description or "") + len(json.dumps(t.inputSchema)) for t in tools)
    assert size <= BUDGET[backend], (
        f"the {backend} listing is {size} characters, over its budget of "
        f"{BUDGET[backend]}: trim a description before adding to it")


@pytest.mark.parametrize("backend", sorted(BUDGET))
def test_no_description_grows_past_its_limit(backend):
    long = {t.name: len(t.description or "") for t in _listing(backend)
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
