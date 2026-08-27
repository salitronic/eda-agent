# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the tool_catalog discovery meta-tool (roadmap 2.2)."""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture(scope="module")
def mcp():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend

    # register_backend, not register_all_tools: the latter is the
    # ALTIUM-specific suite, and the backend-agnostic registrars
    # (register_eda_tools, register_meta_tools) are layered on top of it
    # by register_backend. Calling the inner one built a surface that no
    # real server ever serves -- one without tool_catalog itself.
    m = FastMCP("test")
    register_backend(m, "altium")
    return m


def _call(mcp, **kwargs):
    return asyncio.run(mcp.call_tool("tool_catalog", kwargs))


def _payload(result):
    # FastMCP.call_tool returns a list of content items; the tool's dict is
    # JSON-serialized into the first TextContent.
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


def test_catalog_lists_everything_by_default(mcp):
    res = _payload(_call(mcp))
    assert res["count"] >= 350
    # Every category present.
    assert set(res["categories"]) >= {
        "application", "project", "library", "pcb", "design", "audit",
    }


def test_filter_by_category(mcp):
    res = _payload(_call(mcp, category="audit"))
    assert res["count"] == 32
    assert all(t["category"] == "audit" for t in res["tools"])


def test_filter_by_interaction_modal(mcp):
    res = _payload(_call(mcp, interaction="modal"))
    names = {t["name"] for t in res["tools"]}
    assert {"proj_sync_pcb", "proj_sync_schematic", "pcb_add_teardrops"} <= names


def test_query_substring(mcp):
    res = _payload(_call(mcp, query="checkpoint"))
    names = {t["name"] for t in res["tools"]}
    assert "app_checkpoint" in names
    assert "app_restore_checkpoint" in names


def test_with_description_included(mcp):
    res = _payload(_call(mcp, category="routing", with_description=True))
    assert res["count"] >= 1
    assert all("description" in t and t["description"] for t in res["tools"])


def test_combined_filters_narrow(mcp):
    res = _payload(_call(mcp, category="pcb", interaction="readonly"))
    assert all(t["category"] == "pcb" and t["interaction"] == "readonly"
               for t in res["tools"])
    # readonly pcb tools are a strict subset of all pcb tools.
    all_pcb = _payload(_call(mcp, category="pcb"))["count"]
    assert 0 < res["count"] < all_pcb


def _invoke(mcp, name, arguments=None):
    result = asyncio.run(mcp.call_tool(
        "tool_invoke", {"name": name, "arguments": arguments or {}}))
    return _payload(result)


def test_tool_invoke_runs_an_offline_tool(mcp):
    # Invoke an offline calculator via the meta-tool and get its real result.
    res = _invoke(mcp, "pcb_calc_impedance", {
        "geometry": "microstrip", "width_mils": 6.0,
        "dielectric_height_mils": 4.0, "dielectric_constant": 4.3,
    })
    assert res["tool"] == "pcb_calc_impedance"
    assert isinstance(res["result"], dict)


def test_tool_invoke_bad_args_returns_error(mcp):
    # An argument mistake surfaces as an error dict, not a raised exception.
    res = _invoke(mcp, "pcb_calc_impedance", {"geometry": "microstrip"})
    assert "error" in res


def test_tool_invoke_catalog_through_invoke(mcp):
    res = _invoke(mcp, "tool_catalog", {"category": "routing"})
    assert res["result"]["count"] == 2


def test_tool_invoke_unknown_tool(mcp):
    res = _invoke(mcp, "no_such_tool")
    assert "error" in res


def test_tool_invoke_cannot_recurse(mcp):
    res = _invoke(mcp, "tool_invoke", {"name": "tool_catalog"})
    assert "error" in res
