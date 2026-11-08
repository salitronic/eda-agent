# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An empty row between pin groups is part of an IC symbol's layout, so the
tool's arguments must accept it: the layout generator always took None for
a gap, but the tool's schema refused it before the generator ran."""
from __future__ import annotations

import asyncio

from eda_agent.tools import library as lib_mod
from eda_agent.tools.registry import ToolRegistry


def _validate(args):
    """Validate as the MCP server does before the tool runs."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    registry = ToolRegistry()
    lib_mod.register_library_tools(registry)
    fn = {t.name: t.fn for t in asyncio.run(registry.list_tools())}["lib_create_ic_symbol"]
    return func_metadata(fn).arg_model.model_validate(args)


def test_both_sides_accept_a_gap_row():
    got = _validate({"name": "X",
                     "left_pins": [{"designator": "1", "name": "VIN"}, None,
                                   {"designator": "2", "name": "GND"}],
                     "right_pins": [None, {"designator": "3", "name": "OUT"}]})
    assert got.left_pins[1] is None and got.right_pins[0] is None
