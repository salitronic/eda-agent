# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The canned requests: MCP prompts a user picks instead of typing the ask."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from eda_agent.tools import _register_full, register_backend
from eda_agent.tools.registry import ToolRegistry
from eda_agent.tools.requests import (
    BUILT_IN, REPORT_ONLY, load_requests, register_request_prompts,
)

BACKENDS = ("altium", "kicad", "easyeda", "both")


@pytest.fixture(scope="module")
def surfaces():
    out = {}
    for backend in BACKENDS:
        reg = ToolRegistry()
        _register_full(reg, backend)
        out[backend] = reg
    return out


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_named_tool_exists_on_its_backend(surfaces, backend):
    """A request must never send the AI after a tool this server lacks."""
    for req in BUILT_IN:
        tools = req.tools_for(backend)
        if tools is None:
            continue
        # A member of a family tool counts: the prompt names it by the
        # family tool that runs it.
        reachable = set(surfaces[backend].names) | set(surfaces[backend].hidden)
        missing = [t for t in tools if t not in reachable]
        assert not missing, f"{req.name} on {backend} names {missing}"


def test_every_backend_named_in_a_request_is_real():
    for req in BUILT_IN:
        assert set(req.tools) <= {"*", "altium", "kicad", "easyeda"}, req.name


def _prompts(mcp):
    return {p.name: p for p in asyncio.run(mcp.list_prompts())}


def _text(mcp, name, args=None):
    result = asyncio.run(mcp.get_prompt(name, args or {}))
    return result.messages[0].content.text


def test_registered_on_the_real_server_with_arguments(tmp_path):
    mcp = FastMCP("t")
    names = register_request_prompts(mcp, "altium", workspace=tmp_path)
    assert set(names) == {r.name for r in BUILT_IN}
    prompts = _prompts(mcp)
    args = {a.name: a.required for a in prompts["check_part"].arguments}
    assert args == {"designator": True}
    assert prompts["check_part"].title == "Check a part against its datasheet"

    text = _text(mcp, "check_part", {"designator": "U3"})
    assert text.startswith("Check U3 against its datasheet")
    # Named by the bulk tool that runs it.
    assert "`proj_get_component_info_many`" in text
    assert text.count(REPORT_ONLY) == 1
    with pytest.raises(Exception):
        _text(mcp, "check_part")


def test_optional_argument_drops_out_when_empty(tmp_path):
    mcp = FastMCP("t")
    register_request_prompts(mcp, "altium", workspace=tmp_path)
    assert "Concentrate" not in _text(mcp, "review_board")
    assert "Concentrate on the power stage." in _text(
        mcp, "review_board", {"focus": "the power stage"})
    assert " \n" not in _text(mcp, "review_board")


def test_the_tool_line_follows_the_backend(tmp_path):
    kicad, easy = FastMCP("k"), FastMCP("e")
    register_request_prompts(kicad, "kicad", workspace=tmp_path)
    register_request_prompts(easy, "easyeda", workspace=tmp_path)
    assert "`kicad_compare_sch_pcb`" in _text(kicad, "check_sync")
    assert "`easyeda_compare_schematic_pcb`" in _text(easy, "check_sync")
    # No KiCad tool lists unrouted nets, so the request is not offered there.
    assert "routing_status" not in _prompts(kicad)
    assert "routing_status" in _prompts(easy)


def test_the_minimal_toolset_still_offers_the_requests():
    mcp = FastMCP("m")
    register_backend(mcp, "altium", "minimal")
    assert {r.name for r in BUILT_IN} <= set(_prompts(mcp))


def test_workspace_file_adds_and_replaces(tmp_path):
    (tmp_path / "requests.json").write_text(json.dumps([
        {"name": "explain_drc", "title": "Mine", "text": "Just the count."},
        {"name": "pin_swap", "title": "Pin swap",
         "description": "Suggest pin swaps",
         "text": "Suggest pin swaps on {part} to untangle the routing.",
         "arguments": [{"name": "part", "description": "The FPGA"}]},
        {"name": "Bad Name", "text": "x"},
        {"name": "no_text"},
        "not an object",
    ]), encoding="utf-8")
    by_name = {r.name: r for r in load_requests(tmp_path)}
    assert "Bad Name" not in by_name and "no_text" not in by_name

    mcp = FastMCP("t")
    register_request_prompts(mcp, "kicad", workspace=tmp_path)
    assert _text(mcp, "explain_drc") == "Just the count."
    assert _text(mcp, "pin_swap", {"part": "U1"}) == (
        "Suggest pin swaps on U1 to untangle the routing.")
    assert _prompts(mcp)["pin_swap"].arguments[0].required is True


def test_unreadable_workspace_file_keeps_the_built_ins(tmp_path):
    (tmp_path / "requests.json").write_text("{not json", encoding="utf-8")
    assert [r.name for r in load_requests(tmp_path)] == [r.name for r in BUILT_IN]
