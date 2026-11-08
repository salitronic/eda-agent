# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""footprint_check_package and footprint_render_package take the same
package as the footprint tool, drawing_dims included: the check refused it
as an unknown field, so a package that builds could not be checked."""
from __future__ import annotations

import asyncio

from eda_agent.tools import footprint

_PKG = {"family": "custom", "name": "T", "body_x": 3, "body_y": 3,
        "custom_pads": [{"number": "1", "x": 0, "y": -0.5, "width": 0.25, "height": 0.25},
                        {"number": "2", "x": 0, "y": 0.5, "width": 0.25, "height": 0.25}]}


def _tools():
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    footprint.register_footprint_tools(_Mcp())
    return tools


def test_the_check_measures_the_drawing_dims():
    check = _tools()["footprint_check_package"]
    good = asyncio.run(check({**_PKG, "drawing_dims": [
        {"from": "1", "to": "2", "mm": 1.0, "axis": "y"}]}))
    assert good["ok"] and good["drawing_dims"][0]["ok"]
    bad = asyncio.run(check({**_PKG, "drawing_dims": [
        {"from": "1", "to": "2", "mm": 0.75, "axis": "y"}]}))
    assert not bad["ok"] and "0.75" in bad["drawing_dims"][0]["why"]


def test_the_render_accepts_them(tmp_path):
    render = _tools()["footprint_render_package"]
    out = asyncio.run(render({**_PKG, "drawing_dims": [
        {"from": "1", "to": "2", "mm": 1.0}]}, path=str(tmp_path / "t.svg")))
    assert out.get("ok", True) is not False, out
