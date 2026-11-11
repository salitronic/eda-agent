# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tool results reach the client as compact JSON text.

FastMCP writes a returned dict with indent=2. A client that reads the text
copy of a result sends that padding to the model on every call, 40-65% more
characters than the same data compact. The server rewrites the text copy
and leaves structuredContent alone.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from eda_agent import server

PAYLOAD = {
    "count": 2,
    "objects": [{"Net": "GND", "X": 8599.9967, "Name": "R29-2"},
                {"Net": "+3V3", "X": -1, "Name": "µC°"}],
    "nested": {"empty": [], "none": None, "flag": True},
}


def _server():
    m = server._CompactFastMCP("t")

    @m.tool()
    async def as_dict() -> dict[str, Any]:
        return PAYLOAD

    @m.tool()
    async def as_text() -> str:
        return "plain text, not JSON"

    return m


def _call(name: str):
    return asyncio.run(_server().call_tool(name, {}))


def test_the_live_server_is_the_compact_one():
    assert isinstance(server.mcp, server._CompactFastMCP)


def test_a_dict_result_is_compact_and_the_same_data():
    unstructured, structured = _call("as_dict")
    text = unstructured[0].text
    assert "\n" not in text and ": " not in text
    # Every reply says the unit its lengths are in (lengths.py).
    said = {**PAYLOAD, "length_units": "mil"}
    assert json.loads(text) == said
    assert "µC°" in text            # not escaped to µ
    assert structured == said               # the same data in both copies


def test_the_compact_text_is_shorter_than_what_fastmcp_wrote():
    import pydantic_core
    indented = pydantic_core.to_json(PAYLOAD, indent=2).decode()
    unstructured, _ = _call("as_dict")
    assert len(unstructured[0].text) < len(indented)


def test_text_that_is_not_json_passes_through():
    result = _call("as_text")
    blocks = result[0] if isinstance(result, tuple) else result
    assert blocks[0].text == "plain text, not JSON"


def test_tool_invoke_still_decodes_a_compact_result():
    # tool_invoke json.loads the first text block of the inner call
    unstructured, _ = _call("as_dict")
    assert json.loads(unstructured[0].text)["objects"][1]["Net"] == "+3V3"
