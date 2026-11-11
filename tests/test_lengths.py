# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Lengths in mils or millimetres, by one rule on every tool (lengths.py).

The calls go through the server's own call_tool, so what is tested is the
boundary a client meets: the tool sees its own unit, the client its
session's.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import typing

import pytest

from eda_agent import lengths


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    return tmp_path


def _server():
    from eda_agent.server import _CompactFastMCP
    from eda_agent.tools.folds import FoldingServer
    server = _CompactFastMCP("lengths-test")
    seen: dict = {}
    mcp = FoldingServer(server)

    @mcp.tool()
    async def easyeda_put_track(x1: float, y1: float, width: float = 10,
                                clearance_mils: float = 5,
                                layers: typing.Optional[list] = None) -> dict:
        seen["track"] = {"x1": x1, "y1": y1, "width": width,
                         "clearance_mils": clearance_mils, "layers": layers}
        return {"x1": x1, "y1": y1, "width": width, "count": 3,
                "total_mils": 1000, "bounds": [0, 0, 100, 200],
                "outline": [{"x": 100, "y": 0, "angle": 90}],
                "area_sq_mils": 7}

    @mcp.tool()
    async def easyeda_take_picture(width: int = 800) -> dict:
        seen["picture"] = width
        return {"width": width}

    @mcp.tool()
    async def kicad_put_via(x: float, diameter_mm: float = 0.6) -> dict:
        seen["via"] = {"x": x, "diameter_mm": diameter_mm}
        return {"x": x, "diameter_mm": diameter_mm}

    from eda_agent.tools.length_units import register_length_units_tools
    register_length_units_tools(mcp)
    mcp.finish()
    return server, seen


def _call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    blocks, structured = result if isinstance(result, tuple) else (result, None)
    text = json.loads(blocks[0].text)
    if structured is not None:
        assert structured == text
    return text


def test_a_mil_session_passes_lengths_through_and_says_so(workspace):
    server, seen = _server()
    out = _call(server, "easyeda_put_track", {"x1": 100, "y1": 200})
    assert seen["track"]["x1"] == 100 and seen["track"]["width"] == 10
    assert out["x1"] == 100 and out["total_mils"] == 1000
    assert out["length_units"] == "mil"


def test_a_mm_session_converts_both_ways(workspace):
    server, seen = _server()
    assert _call(server, "length_units", {"to": "mm"})["length_units"] == "mm"
    out = _call(server, "easyeda_put_track",
                {"x1": 25.4, "y1": 2.54, "width": 0.254,
                 "layers": [{"name": "Top", "thickness_mm": 0.035}]})
    track = seen["track"]
    assert (track["x1"], track["y1"], track["width"]) == (1000, 100, 10)
    # Nested lengths with a unit reach the tool in its own unit.
    assert track["layers"] == [{"name": "Top", "thickness_mils": pytest.approx(1.377953)}]
    assert (out["x1"], out["y1"], out["width"]) == (25.4, 2.54, 0.254)
    # Not a length: left alone.
    assert out["count"] == 3
    # A name with a unit is renamed to the session's and converted.
    assert "total_mils" not in out and out["total_mm"] == 25.4
    # Points and boxes are scaled; an angle inside them is not.
    assert out["bounds"] == [0, 0, 2.54, 5.08]
    assert out["outline"] == [{"x": 2.54, "y": 0, "angle": 90}]
    # An area does not scale linearly: kept as its name says.
    assert out["area_sq_mils"] == 7
    assert out["length_units"] == "mm"


def test_a_name_with_a_unit_always_means_it(workspace):
    server, seen = _server()
    _call(server, "length_units", {"to": "mm"})
    _call(server, "easyeda_put_track", {"x1": 1, "y1": 1, "clearance_mils": 8})
    assert seen["track"]["clearance_mils"] == 8
    _call(server, "easyeda_put_track", {"x1": 1, "y1": 1, "clearance_mm": 0.254})
    assert seen["track"]["clearance_mils"] == 10


def test_a_name_with_a_unit_the_tool_does_not_take_is_refused(workspace):
    server, _ = _server()
    with pytest.raises(Exception, match="takes no gap_mm"):
        asyncio.run(server.call_tool("easyeda_put_track",
                                     {"x1": 1, "y1": 1, "gap_mm": 1}))


def test_one_call_can_use_the_other_unit(workspace):
    server, seen = _server()
    out = _call(server, "easyeda_put_track",
                {"x1": 2.54, "y1": 0, "length_units": "mm"})
    assert seen["track"]["x1"] == 100
    assert out["x1"] == 2.54 and out["length_units"] == "mm"
    # The session's own unit is untouched.
    assert _call(server, "length_units", {})["length_units"] == "mil"
    with pytest.raises(Exception, match="'mil' or 'mm'"):
        asyncio.run(server.call_tool("easyeda_put_track",
                                     {"x1": 1, "y1": 1, "length_units": "inch"}))


def test_a_millimetre_tool_converts_from_its_own_unit(workspace):
    server, seen = _server()
    out = _call(server, "kicad_put_via", {"x": 100, "diameter_mils": 40})
    assert seen["via"]["x"] == pytest.approx(2.54)
    assert seen["via"]["diameter_mm"] == pytest.approx(1.016)
    assert out["x"] == pytest.approx(100) and out["diameter_mils"] == pytest.approx(40)


def test_pixels_are_not_lengths(workspace):
    server, seen = _server()
    lengths.NO_LENGTHS = lengths.NO_LENGTHS | {"easyeda_take_picture"}
    try:
        _call(server, "length_units", {"to": "mm"})
        out = _call(server, "easyeda_take_picture", {"width": 800})
    finally:
        lengths.NO_LENGTHS = lengths.NO_LENGTHS - {"easyeda_take_picture"}
    assert seen["picture"] == 800 and out == {"width": 800}


def test_items_and_tool_invoke_convert_each_call_once(workspace):
    server, seen = _server()
    _call(server, "length_units", {"to": "mm"})
    out = _call(server, "easyeda_put_track",
                {"items": [{"x1": 2.54, "y1": 0}, {"x1": 5.08, "y1": 0}]})
    assert seen["track"]["x1"] == 200
    assert [i["x1"] for i in out["items"]] == [2.54, 5.08]


def test_the_session_unit_is_the_sessions_and_starts_at_the_config_tab(workspace):
    (workspace / "preferences.json").write_text('{"length_units": "mm"}')
    assert lengths.session_unit() == "mm"
    lengths.set_session_unit("mil")
    assert lengths.session_unit() == "mil"
    assert lengths.default_unit() == "mm"
    from eda_agent.bridge.altium_bridge import _SESSION_TAG
    assert (workspace / lengths.SESSIONS_DIR / f"{_SESSION_TAG}.json").is_file()
    with pytest.raises(ValueError):
        lengths.set_session_unit("inch")


def test_converted_numbers_have_no_float_noise():
    assert lengths._tidy(25.4 * (1 / 0.0254)) == 1000
    assert lengths._tidy(0.1 / 0.0254) == pytest.approx(3.937008)


# ------------------------------------------------- every length is declared

#: Plain argument names that are not lengths on any tool.
NOT_LENGTH_NAMES = re.compile(
    r"count|index|indices|^n_|_n$|number|^rows?$|^cols?$|columns|angle|rotat|degree|"
    r"_deg$|ratio|factor|scale|pct|percent|fraction|weight|priority|limit|max_|"
    r"min_|timeout|seconds|_s$|_ms$|interval|retries|iterations|rounds|steps?$|"
    r"turns|_hz$|_khz$|_ghz$|freq|_ohms?$|impedance|^z0|_v$|^v_|volt|_a$|^i_|amps|"
    r"current|_w$|power|_c$|^t_|temp|ambient|tj_|theta|_pf$|_nf$|_f$|_h$|"
    r"farads|capacitance|inductance|_ns$|_ps$|_um$|oz|awg|^er|^k_|dk|df|loss|"
    r"constant|tangent|dpi|zoom|opacity|transparency|azimuth|elevation|"
    r"depth_of_field|offset$|page|seed|level|quality|precision|decimals|"
    r"budget|cost|penalty|^delta$|gain|^dc$|^ac$|^c_|^r_|tol|^font_id|"
    r"part_id|owner|unit$|^set$|^vcc$|^supply|^value$|_value$|span|share|"
    r"threshold|below$|zones|correction|repeat|start$|end$|first|last|"
    r"^x$|^y$|^width$|^height$|^size$|^radius$|^margin$|^corner_radius$|"
    r"^line_width$|^tolerance$|^gap$|^font_size$|^grid$|^length$|font|"
    r"^track$|^via$|^visible|^component_[xy]$|electrical_grid|shove|auto_pan|"
    r"pour_redraw|ignore|minimum_pads|^fixed$|^restarts$|^wait|^ripple|"
    r"colou?r|mode|style|orientation|type$|kind$|layers$|revision|^sides$|"
    r"increment|spokes|^symbol$|^entries$|^units$|pins|^f_|supplies|"
    r"^region$|^names_region$|^wires$|^pins$|direction|display|naming|"
    r"multiple|_pair$|_move$")


def _surface():
    from eda_agent.tools import register_backend

    class Capture:
        def __init__(self):
            self.t = {}
            self.hidden = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.t[k.get("name") or fn.__name__] = fn
                return fn
            return deco

        def resource(self, *a, **k):
            return lambda f: f

        def prompt(self, *a, **k):
            return lambda f: f

    tools = {}
    for backend in ("altium", "easyeda", "kicad"):
        cap = Capture()
        register_backend(cap, backend)
        tools.update(cap.t)
        for name, spec in (cap.hidden or {}).items():
            tools.setdefault(name, spec.fn)
    return tools


def test_every_numeric_argument_is_a_declared_length_or_plainly_not_one():
    """A new length argument with a plain name must be declared: in
    LENGTH_KEYS, or for its tool in ADDED. A test that guessed from names
    could not tell a track's width from a picture's."""
    undeclared = []
    for name, fn in sorted(_surface().items()):
        fn = getattr(fn, "single", fn)
        if not lengths.converts(name) or name in lengths.SELF_CONVERTING:
            continue
        try:
            hints = typing.get_type_hints(fn)
        except Exception:                      # noqa: BLE001
            hints = {}
        keys = lengths.plain_lengths(name, {"rule_type": "width"})
        for p in inspect.signature(fn).parameters.values():
            kind = str(hints.get(p.name, p.annotation))
            if "bool" in kind or not re.search(r"\b(int|float)\b", kind):
                continue
            if p.name in keys or p.name in lengths.POINT_KEYS                     or p.name in lengths.LENGTH_DOCUMENTS                     or re.search(r"_(mils?|mm)$", p.name):
                continue
            if NOT_LENGTH_NAMES.search(p.name):
                continue
            undeclared.append(f"{name}.{p.name}")
    assert not undeclared, undeclared


def test_the_plain_names_that_mean_lengths_elsewhere_are_declared_tool_by_tool():
    """x, width, size and the like are lengths on some tools and not on
    others; each tool that takes one is accounted for."""
    ambiguous = {"size", "margin", "corner_radius", "line_width", "tolerance",
                 "font_size", "track", "via", "visible", "visible_big"}
    unaccounted = []
    for name, fn in sorted(_surface().items()):
        fn = getattr(fn, "single", fn)
        if not lengths.converts(name) or name in lengths.SELF_CONVERTING:
            continue
        try:
            hints = typing.get_type_hints(fn)
        except Exception:                      # noqa: BLE001
            hints = {}
        for p in inspect.signature(fn).parameters:
            kind = str(hints.get(p, ""))
            if "bool" in kind or not re.search(r"\b(int|float)\b", kind):
                continue
            if p in ambiguous and p not in lengths.ADDED.get(name, ()) \
                    and p not in lengths.NOT_LENGTHS.get(name, ()) \
                    and name not in _KNOWN_NOT_LENGTH.get(p, ()):
                unaccounted.append(f"{name}.{p}")
    assert not unaccounted, unaccounted


#: Ambiguous names checked against each tool's description and found not
#: to be lengths there.
_KNOWN_NOT_LENGTH = {
    "margin": {"pcb_calc_trace_width_for_current"},      # a fraction
    "corner_radius": {"lib_create_standard_footprint"},  # percent
    "line_width": {"lib_add_symbol_polygon", "lib_add_symbol_rectangle",
                   "sch_place_line", "sch_place_rectangle"},  # size code
    "font_size": {"easyeda_add_schematic_text"},
}


# ------------------------------------------------ tools run through others

@pytest.fixture
def families():
    from eda_agent.tools import folds
    saved = dict(folds._BUILT)
    yield
    folds._BUILT.clear()
    folds._BUILT.update(saved)


def _server_with_family():
    from eda_agent.server import _CompactFastMCP
    from eda_agent.tools.folds import FoldingServer
    from eda_agent.tools import register_meta_tools
    server = _CompactFastMCP("lengths-family")
    seen: dict = {}
    mcp = FoldingServer(server)

    @mcp.tool()
    async def pcb_place_radial_dimension(center_x: float, center_y: float,
                                         radius: float) -> dict:
        seen["radial"] = (center_x, center_y, radius)
        return {"radius": radius, "angle": 45}

    @mcp.tool()
    async def easyeda_make_part(package: typing.Any = None, units: str = "mil",
                                x: float = 0) -> dict:
        seen["part"] = {"package": package, "units": units, "x": x}
        return {"x": x, "pitch": 50}

    register_meta_tools(mcp)
    from eda_agent.tools.length_units import register_length_units_tools
    register_length_units_tools(mcp)
    mcp.finish()
    return server, seen


def test_a_family_tool_converts_its_member_as_itself(workspace, families):
    server, seen = _server_with_family()
    _call(server, "length_units", {"to": "mm"})
    out = _call(server, "pcb_dimension",
                {"kind": "radial", "args": {"center_x": 2.54, "center_y": 0, "radius": 1.27}})
    assert seen["radial"] == (100, 0, 50)
    assert out["radius"] == 1.27 and out["angle"] == 45
    assert out["length_units"] == "mm"


def test_tool_invoke_converts_the_tool_it_runs_once(workspace, families):
    server, seen = _server_with_family()
    _call(server, "length_units", {"to": "mm"})
    out = _call(server, "tool_invoke", {"name": "pcb_place_radial_dimension",
                                        "arguments": {"center_x": 25.4, "center_y": 0,
                                                      "radius": 2.54}})
    assert seen["radial"] == (1000, 0, 100)
    assert out["result"]["radius"] == 2.54
    out = _call(server, "tool_invoke", {"name": "pcb_dimension", "arguments": {
        "kind": "radial", "args": {"center_x": 0, "center_y": 0, "radius": 0.254}}})
    assert seen["radial"][2] == 10
    assert out["result"]["radius"] == 0.254


def test_a_package_is_a_millimetre_document_on_every_tool(workspace, families, monkeypatch):
    server, seen = _server_with_family()
    package = {"family": "soic", "pins": 8, "pitch": {"min": 50, "nom": 50, "max": 50},
               "body_x": [190, 200]}
    _call(server, "easyeda_make_part", {"package": json.dumps(package), "x": 100})
    # Given in mils (the session's unit), it reaches the tool in mm, as
    # text when it came as text; the pin count is not a length.
    got = seen["part"]["package"]
    got = json.loads(got) if isinstance(got, str) else got
    assert got["pins"] == 8 and got["family"] == "soic"
    assert got["pitch"] == {"min": 1.27, "nom": 1.27, "max": 1.27}
    assert got["body_x"] == [4.826, 5.08]
    assert seen["part"]["x"] == 100


def test_a_tool_that_converts_itself_is_handed_the_sessions_unit(workspace, families, monkeypatch):
    monkeypatch.setattr(lengths, "SELF_CONVERTING",
                        lengths.SELF_CONVERTING | {"easyeda_make_part"})
    server, seen = _server_with_family()
    _call(server, "length_units", {"to": "mm"})
    out = _call(server, "easyeda_make_part", {"x": 2.54})
    # Its plain lengths are its own to convert, in the unit it is given.
    assert seen["part"]["units"] == "mm" and seen["part"]["x"] == 2.54
    assert out["x"] == 2.54
    _call(server, "easyeda_make_part", {"x": 100, "units": "mil"})
    assert seen["part"]["units"] == "mil"
