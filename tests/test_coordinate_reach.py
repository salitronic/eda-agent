# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A place Altium cannot reach is refused before anything is sent.

Altium holds a coordinate as a signed 32-bit number, 10,000 to the mil: a
place past +/-214,748 mil wraps to the other side of the origin. A board's
editor shows only 0 to 99,999 mil on each axis: a place off it is kept but
can be neither seen nor picked. Both were measured on AD 25.8.1 (lengths.py
WRAP_MILS, BOARD_REACH_MILS). A sheet has no edge short of the wrap.

The calls go through the server's own call_tool, the boundary a client
meets, with stand-in tools named like the real ones.
"""
from __future__ import annotations

import asyncio
import json
import typing

import pytest

from eda_agent import lengths


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    return tmp_path


@pytest.fixture
def families():
    from eda_agent.tools import folds
    saved = dict(folds._BUILT)
    yield
    folds._BUILT.clear()
    folds._BUILT.update(saved)


def _server():
    from eda_agent.server import _CompactFastMCP
    from eda_agent.tools import register_meta_tools
    from eda_agent.tools.folds import FoldingServer
    from eda_agent.tools.length_units import register_length_units_tools
    server = _CompactFastMCP("reach-test")
    seen: dict = {}
    mcp = FoldingServer(server)

    @mcp.tool()
    async def sch_put_marker(x: float, y: float) -> dict:
        seen["marker"] = (x, y)
        return {"x": x, "y": y}

    @mcp.tool()
    async def pcb_put_tracks(tracks: typing.Optional[list] = None) -> dict:
        seen["tracks"] = tracks
        return {"placed": len(tracks or [])}

    @mcp.tool()
    async def pcb_put_outline(points: typing.Optional[list] = None,
                              bounds: typing.Optional[list] = None,
                              width: float = 10) -> dict:
        seen["outline"] = (points, bounds, width)
        return {"placed": 1}

    @mcp.tool()
    async def pcb_put_via(x_mils: float = 0, y_mils: float = 0) -> dict:
        seen["via"] = (x_mils, y_mils)
        return {"placed": 1}

    @mcp.tool()
    async def lib_add_footprint_dot(x: float, y: float, units: str = "mil") -> dict:
        seen["footprint"] = (x, y, units)
        return {"placed": 1}

    @mcp.tool()
    async def lib_add_symbol_dot(x: float, y: float) -> dict:
        seen["symbol"] = (x, y)
        return {"placed": 1}

    @mcp.tool()
    async def easyeda_put_dot(x: float, y: float) -> dict:
        seen["easyeda"] = (x, y)
        return {"placed": 1}

    register_meta_tools(mcp)
    register_length_units_tools(mcp)
    mcp.finish()
    return server, seen


def _call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    blocks, _ = result if isinstance(result, tuple) else (result, None)
    return json.loads(blocks[0].text)


def _refused(server, name, args, *words):
    with pytest.raises(Exception) as err:
        asyncio.run(server.call_tool(name, args))
    text = str(err.value)
    for w in words:
        assert w in text, text
    return text


def test_a_place_that_would_wrap_is_refused_and_nothing_is_sent(workspace, families):
    server, seen = _server()
    _call(server, "length_units", {"to": "mm"})
    # The issue's marker: 5,600 mm is 220,472 mil.
    text = _refused(server, "sch_put_marker", {"x": 5600, "y": 104.14},
                    "x = 5600 mm", "wraps", "Nothing was sent", "in mm")
    assert "y =" not in text
    assert "marker" not in seen


def test_a_sheet_reaches_past_its_own_edge_and_below_zero(workspace, families):
    server, seen = _server()
    # Zoomed to on AD 25.8.1: far off a 20,000 mil sheet, and below zero.
    _call(server, "sch_put_marker", {"x": 133858, "y": -500})
    assert seen["marker"] == (133858, -500)
    _call(server, "sch_put_marker", {"x": 214748, "y": 0})
    _refused(server, "sch_put_marker", {"x": 0, "y": -214749}, "y = -214749 mil")


def test_a_board_place_off_the_editors_area_is_refused(workspace, families):
    server, seen = _server()
    ok = [{"x1": 99999, "y1": 0, "x2": -99999, "y2": 50000, "width": 10}]
    _call(server, "pcb_put_tracks", {"tracks": ok})
    assert seen["tracks"][0]["x1"] == 99999
    seen.clear()
    far = [{"x1": 1000, "y1": 1000, "x2": 1100, "y2": 1000, "width": 10},
           {"x1": 100500, "y1": 1000, "x2": 100600, "y2": -100000, "width": 10}]
    text = _refused(server, "pcb_put_tracks", {"tracks": far},
                    "tracks[1].x1 = 100500 mil", "tracks[1].x2 = 100600 mil",
                    "tracks[1].y2 = -100000 mil", "neither seen nor picked")
    assert "tracks[0]" not in text
    assert "tracks" not in seen


def test_a_board_limit_is_met_in_the_calls_own_unit(workspace, families):
    server, seen = _server()
    _call(server, "length_units", {"to": "mm"})
    _call(server, "pcb_put_tracks", {"tracks": [
        {"x1": 2539, "y1": 0, "x2": 2539, "y2": 10, "width": 0.2}]})
    _refused(server, "pcb_put_tracks", {"tracks": [
        {"x1": 2540, "y1": 0, "x2": 2540, "y2": 10, "width": 0.2}]}, "x1 = 2540 mm")
    # A name with a unit means it, whatever the session's.
    _call(server, "pcb_put_via", {"x_mm": 2539, "y_mm": 0})
    _refused(server, "pcb_put_via", {"x_mm": 2541, "y_mm": 0}, "x_mils = 100039 mil")
    _refused(server, "pcb_put_via", {"x_mils": 100001, "y_mils": 0},
             "x_mils = 100001 mil")


def test_vertices_are_places_and_a_search_box_is_not(workspace, families):
    server, seen = _server()
    _refused(server, "pcb_put_outline",
             {"points": [[0, 0], [1000, 0, 90], [150000, 1000]]}, "points[2]")
    _refused(server, "pcb_put_outline",
             {"points": [{"x": 0, "y": 0}, {"x": 0, "y": 120000, "sweep": 90}]},
             "points[1].y")
    # A box larger than the board is a way to say all; a size is no place,
    # nor is a vertex's index.
    _call(server, "pcb_put_outline", {"points": [[0, 0], {"x": 1000, "y": 0,
                                                          "index": 250000}],
                                      "bounds": [-1e6, -1e6, 1e6, 1e6],
                                      "width": 150000})
    assert seen["outline"][1] == [-1e6, -1e6, 1e6, 1e6]


def test_a_footprint_is_a_board_and_a_symbol_is_a_sheet(workspace, families, monkeypatch):
    monkeypatch.setattr(lengths, "SELF_CONVERTING",
                        lengths.SELF_CONVERTING | {"lib_add_footprint_dot"})
    server, seen = _server()
    _call(server, "lib_add_symbol_dot", {"x": 150000, "y": 0})
    assert seen["symbol"] == (150000, 0)
    _refused(server, "lib_add_footprint_dot", {"x": 150000, "y": 0}, "x = 150000 mil")
    # A tool that converts itself is checked in the unit it is handed.
    _call(server, "lib_add_footprint_dot", {"x": 2500, "y": 0, "units": "mm"})
    assert seen["footprint"] == (2500, 0, "mm")
    _refused(server, "lib_add_footprint_dot", {"x": 2600, "y": 0, "units": "mm"},
             "x = 2600 mm")


def test_one_item_out_of_reach_refuses_the_whole_list(workspace, families):
    server, seen = _server()
    _refused(server, "sch_put_marker",
             {"items": [{"x": 100, "y": 100}, {"x": 300000, "y": 100}]},
             "items[1].x = 300000 mil")
    assert "marker" not in seen


def test_a_tool_run_through_tool_invoke_is_checked_as_itself(workspace, families):
    server, seen = _server()
    _refused(server, "tool_invoke",
             {"name": "pcb_put_tracks",
              "arguments": {"tracks": [{"x1": 0, "y1": 0, "x2": 0, "y2": -150000}]}},
             "tracks[0].y2")
    assert "tracks" not in seen


def test_the_issues_calls_on_the_real_tools(workspace):
    from eda_agent.server import _lengths_in, mcp
    mm = {"length_units": "mm"}
    # sch_place_no_erc at 5,600 mm landed at -209,024 mil.
    with pytest.raises(ValueError, match=r"x = 5600 mm .*wraps"):
        _lengths_in(mcp, "sch_place_no_erc", {"x": 5600, "y": 104.14, **mm})
    # A wire at 3,400 mm is far off the sheet but within reach: the unit
    # mix-up itself is not this check's to catch.
    args, _ = _lengths_in(mcp, "sch_place_wires", {"wires": [
        {"x1": 3400, "y1": 5100, "x2": 4100, "y2": 5100}], **mm})
    assert args["wires"][0]["x1"] == pytest.approx(133858.267717)
    # On a board 2,540 mm is 100,000 mil, past the editor's area.
    with pytest.raises(ValueError, match=r"tracks\[0\]\.x2 = 2540 mm"):
        _lengths_in(mcp, "pcb_place_tracks", {"tracks": [
            {"x1": 2500, "y1": 10, "x2": 2540, "y2": 10, "width": 0.2}], **mm})


def test_another_backends_tools_are_left_alone(workspace, families):
    server, seen = _server()
    _call(server, "easyeda_put_dot", {"x": 300000, "y": -300000})
    assert seen["easyeda"] == (300000, -300000)
