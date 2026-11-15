# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Lengths the session's unit used to get wrong, on the real tools.

Each was found by sending one call in the other unit through the
server's own call_tool, the boundary a client meets:

- the sweep of an ``[x, y, sweep]`` region vertex was scaled as a length
  (90 degrees went out as 3543 in a mm session);
- lib_select_footprint_primitives takes its box in mm and was converted
  as if it took mils;
- the ``x_mils`` guard of a lib_get_pad_geometry read in a mil session
  reached lib_set_pad_geometry under a name it refuses;
- a symbol line's ``width`` size code was scaled as a length;
- a whole-mil argument converted from mm was refused as a fraction;
- coordinates written as ``"x,y,..."`` text were not converted at all;
- an arc's centre and radius and a text's size in the footprint reader
  stayed mm while the rest of its reply turned to mils.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import typing

import pytest

from eda_agent import lengths
from eda_agent.tools import library as lib_tools
from eda_agent.tools import pcb as pcb_tools


@pytest.fixture(scope="module")
def server():
    from eda_agent.server import _CompactFastMCP
    from eda_agent.tools import folds, register_meta_tools
    from eda_agent.tools.folds import FoldingServer
    from eda_agent.tools.length_units import register_length_units_tools
    saved = dict(folds._BUILT)
    srv = _CompactFastMCP("session-unit-library-fields")
    mcp = FoldingServer(srv)
    lib_tools.register_library_tools(mcp)
    pcb_tools.register_pcb_tools(mcp)
    register_meta_tools(mcp)
    register_length_units_tools(mcp)
    mcp.finish()
    yield srv
    folds._BUILT.clear()
    folds._BUILT.update(saved)


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """What reached the bridge; its replies are set per command."""
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    calls: list = []
    replies: dict = {}

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            calls.append((command, params))
            return replies.get(command, {"success": True})

    monkeypatch.setattr(lib_tools, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: _Bridge())
    return calls, replies


def _call(srv, name, args):
    result = asyncio.run(srv.call_tool(name, args))
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _unit(srv, unit):
    assert _call(srv, "length_units", {"to": unit})["length_units"] == unit


# ------------------------------------------------------------ region sweeps

def test_a_region_sweep_stays_degrees_on_the_way_in_and_out(server, sent):
    calls, replies = sent
    replies["pcb.place_region"] = {"success": True,
                                   "points": [[0, 0], [100, 0], [100, 100]],
                                   "shape": [[0, 0, 0], [100, 0, 90], [100, 100, 0]]}
    _unit(server, "mm")
    out = _call(server, "pcb_place_region",
                {"points": [[0, 0, 0], [2.54, 0, 90], [2.54, 2.54, 0]]})
    params = calls[-1][1]
    assert params["points"] == "0.0,0.0/100.0,0.0/100.0,100.0"
    # A quarter circle on the 100 mil edge, not a 3543 degree sweep.
    assert params["arcs"] == "2,50,50,70.710678,315,45"
    assert out["shape"] == [[0, 0, 0], [2.54, 0, 90], [2.54, 2.54, 0]]
    assert out["points"] == [[0, 0], [2.54, 0], [2.54, 2.54]]

    # A vertex as a dict names its sweep: the same region.
    _call(server, "pcb_place_region",
          {"points": [{"x": 0, "y": 0}, {"x": 2.54, "y": 0, "sweep": 90},
                      {"x": 2.54, "y": 2.54}]})
    assert calls[-1][1]["arcs"] == params["arcs"]


def test_a_footprint_region_shape_keeps_its_sweep_in_either_unit():
    reply = {"regions": [{"layer": "TopLayer", "x1": 0, "y1": 0, "x2": 2.54,
                          "y2": 2.54,
                          "shape_mm": [[0, 0, 0], [2.54, 0, 90], [2.54, 2.54, -45]]}]}
    mil = lengths.reply_in("lib_get_footprint_primitives", reply, "mil")
    assert mil["regions"][0]["shape_mils"] == [[0, 0, 0], [100, 0, 90], [100, 100, -45]]
    assert mil["regions"][0]["x2"] == 100
    mm = lengths.reply_in("lib_get_footprint_primitives", reply, "mm")
    assert mm["regions"][0]["shape_mm"] == reply["regions"][0]["shape_mm"]


def test_a_footprint_arc_and_text_size_follow_the_rest_of_the_reply():
    reply = {"arcs": [{"layer": "TopOverlay", "xc": 1.27, "yc": -2.54, "r": 0.254,
                       "start": 0, "end": 180, "width": 0.127}],
             "texts": [{"text": "1", "x": 2.54, "y": 0, "size": 1.27, "rotation": 90}]}
    mil = lengths.reply_in("lib_get_footprint_primitives", reply, "mil")
    assert mil["arcs"][0] == {"layer": "TopOverlay", "xc": 50, "yc": -100, "r": 10,
                              "start": 0, "end": 180, "width": 5}
    assert mil["texts"][0] == {"text": "1", "x": 100, "y": 0, "size": 50, "rotation": 90}


# ------------------------------------------------------------- select box

@pytest.mark.parametrize("unit, box", [("mm", [-1, -1, 1, 1]),
                                       ("mil", [-39.370079, -39.370079,
                                                39.370079, 39.370079])])
def test_the_select_box_is_given_in_the_sessions_unit(server, sent, unit, box):
    calls, _ = sent
    _unit(server, unit)
    _call(server, "lib_select_footprint_primitives",
          {"footprint_name": "F", "objects": [{"object_type": "region",
                                               "box": box}]})
    assert calls[-1][1]["objects"] == "object_type=region;box=-1,-1,1,1"


# -------------------------------------------------------- pad guard names

@pytest.mark.parametrize("unit", ["mil", "mm"])
def test_a_pad_read_guards_the_write_in_either_unit(server, sent, unit):
    """The id writer is reached as lib_set_pad_properties items and by
    tool_invoke; either way the guard a read gave goes back as x_mm."""
    calls, replies = sent
    replies["library.get_pad_geometry"] = {
        "pads": [{"name": "1", "id": "A", "x_mm": 1.27, "y_mm": -0.635}]}
    _unit(server, unit)
    pad = _call(server, "lib_get_pad_geometry", {"footprint_name": "F"})["pads"][0]
    guard = {k: v for k, v in pad.items() if k.startswith(("x_", "y_"))}
    assert set(guard) == ({"x_mils", "y_mils"} if unit == "mil" else {"x_mm", "y_mm"})
    row = {"id": "A", **guard, "rotation": 90}
    for name, args in (
            ("lib_set_pad_properties", {"footprint_name": "F", "items": [row]}),
            ("tool_invoke", {"name": "lib_set_pad_geometry",
                             "arguments": {"footprint_name": "F", "rows": [row]}})):
        calls.clear()
        out = _call(server, name, args)
        assert "error" not in json.dumps(out), (name, out)
        command, params = calls[-1]
        assert command == "library.set_pad_geometry", (name, calls)
        rows = params["rows"]
        assert "x_mm=1.27" in rows and "y_mm=-0.635" in rows             and "rotation=90" in rows, (name, rows)


# ------------------------------------------------- symbol width and grid

def test_a_symbol_line_width_is_a_size_code_not_a_length(server, sent):
    calls, _ = sent
    _unit(server, "mm")
    _call(server, "lib_add_symbol_lines",
          {"lines": [{"x1": 0, "y1": 0, "x2": 2.54, "y2": 0, "width": 2}],
           "grid": 0})
    assert calls[-1][1]["lines"] == "x1=0;y1=0;x2=100;y2=0;width=2"


def test_a_whole_mil_argument_is_rounded_not_refused(server, sent):
    calls, _ = sent
    _unit(server, "mm")
    out = _call(server, "lib_add_symbol_arc",
                {"x_center": 1, "y_center": -1, "radius": 2.54, "grid": 0})
    assert "error" not in out, out
    params = calls[-1][1]
    assert (params["x_center"], params["y_center"], params["radius"]) == (39, -39, 100)
    # A grid of 0 keeps the corners as given, in any unit.
    _call(server, "lib_add_symbol_rectangle",
          {"x1": 0, "y1": 0, "x2": 1, "y2": 2.54, "grid": 0})
    assert (calls[-1][1]["x2"], calls[-1][1]["y2"]) == (39, 100)


def test_a_fraction_the_call_gives_itself_is_still_refused(server, sent):
    calls, _ = sent
    _unit(server, "mil")
    with pytest.raises(Exception):
        _call(server, "lib_add_symbol_arc",
              {"x_center": 39.5, "y_center": 0, "radius": 100})
    assert not calls


# ----------------------------------------------- coordinates written as text

def test_coordinates_written_as_text_are_converted(server, sent):
    calls, _ = sent
    _unit(server, "mm")
    _call(server, "lib_add_symbol_polygon",
          {"vertices": "0,0, 2.54,0, 2.54,1.27", "grid": 0})
    assert calls[-1][1]["vertices"] == "0,0,100,0,100,50"
    # Snapped to the default glyph grid after conversion.
    _call(server, "lib_add_symbol_polygon", {"vertices": "0,0,1,0,1,1"})
    assert calls[-1][1]["vertices"] == "0,0,50,0,50,50"
    got = lengths.args_in("pcb_apply_geometry_map",
                          {"map_path": "m", "board_path": "b",
                           "bounds": "0,-12.7,25.4,12.7"}, "mm",
                          ["map_path", "board_path", "bounds"])
    assert got["bounds"] == "0,-500,1000,500"
    # Text that is not numbers is left for the tool to refuse.
    assert lengths.args_in("pcb_apply_geometry_map", {"bounds": "a,b"}, "mm",
                           ["bounds"])["bounds"] == "a,b"


# ----------------------------------------------------------- the tables

def _surface():
    tools = {}

    class _Capture:
        def tool(self, *a, **k):
            def reg(fn):
                tools[k.get("name") or fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    lib_tools.register_library_tools(_Capture())
    pcb_tools.register_pcb_tools(_Capture())
    return tools


def test_every_table_names_a_real_argument():
    tools = _surface()
    for tool, names in lengths.LENGTH_STRINGS.items():
        fn = getattr(tools[tool], "single", tools[tool])
        hints = typing.get_type_hints(fn)
        for name in names:
            assert hints[name] is str, (tool, name)
    for tool in (*lengths.ARC_VERTICES, *lengths.NESTED_NAMES):
        assert tool in tools, tool
    assert "points" in inspect.signature(tools["pcb_place_region"]).parameters
    assert "lib_select_footprint_primitives" in lengths.MM_NATIVE
    assert "width" in lengths.NOT_LENGTHS["lib_add_symbol_lines"]
