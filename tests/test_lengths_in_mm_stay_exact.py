# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Footprint and pad lengths can be given in millimetres and stay exact.

Every authoring path took whole mils: the tools declared ``int``, the pad
batch payload ran each value through ``round()``, and the script parsed
it with StrToIntDef. A pad asked for at 1.625 mm arrived as 64 mil and
read back as 1.6256 mm, off a metric grid by up to 12.7 um on every
coordinate. ``units="mm"`` now carries the value to Altium as given.

Checked here: the Python payloads, the script's conversion run under FPC,
and the handlers' use of it. A caller that never passes units sends and
places exactly what it did before.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import library as library_module
from eda_agent.tools import pcb as pcb_module
from eda_agent.units import format_length, length_in, normalise_units

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
MM = [0.1, 0.5, 0.65, 1.0, 1.27, 1.625, 2.54]


@pytest.mark.parametrize("value,text", [
    (60, "60"), (60.0, "60"), (-25, "-25"), (1.625, "1.625"), (0.1 + 0.2, "0.3"),
    (1e-7, "0"), (1e12, "1000000000000"), (-0.0, "0"), (47.244094, "47.244094"),
])
def test_lengths_go_out_fixed_point(value, text):
    assert format_length(value) == text
    assert re.fullmatch(r"-?\d+(\.\d+)?", text)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None])
def test_a_length_that_is_not_a_number_is_refused(bad):
    with pytest.raises((ValueError, TypeError)):
        format_length(bad)


def test_units_and_their_defaults():
    assert normalise_units("MM") == "mm" and normalise_units("mils") == "mil"
    assert normalise_units("") == "mil" and normalise_units("inch") is None
    assert length_in("mil", 60) == 60
    assert length_in("mm", 60) == pytest.approx(1.524), "a 60 mil default is not 60 mm"


def test_a_whole_mil_pad_payload_is_unchanged():
    pads = [{"designator": "1", "x": -50, "y": 0, "x_size": 30, "y_size": 40},
            {"designator": "2", "x": 50, "y": 0}]
    payload, skipped = library_module._pads_payload(pads)
    assert skipped == 0
    assert payload == (
        "designator=1;x=-50;y=0;x_size=30;y_size=40;hole_size=0;shape=rectangular;"
        "corner_radius=25;rotation=0;layer=TopLayer~~"
        "designator=2;x=50;y=0;x_size=60;y_size=60;hole_size=0;shape=rectangular;"
        "corner_radius=25;rotation=0;layer=TopLayer")


def test_millimetres_reach_the_payload_unrounded():
    pads = [{"designator": str(i), "x": v, "y": -v, "x_size": v, "y_size": v}
            for i, v in enumerate(MM, 1)]
    payload, _ = library_module._pads_payload(pads, "mm")
    for op, v in zip(payload.split("~~"), MM):
        fields = dict(f.split("=", 1) for f in op.split(";"))
        assert float(fields["x"]) == v and float(fields["y"]) == -v
        assert float(fields["x_size"]) == v
    # An omitted size in millimetres is the 60 mil default, not 60 mm.
    payload, _ = library_module._pads_payload([{"designator": "1"}], "mm")
    assert "x_size=1.524;" in payload


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"success": True}


def _tools(monkeypatch, module, register):
    bridge = _Bridge()
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    getattr(module, register)(DummyMcp())
    return captured, bridge


CALLS = [
    ("lib_add_footprint_pad", {"designator": "1", "x": 1.625, "y": 0, "x_size": 0.3, "y_size": 1.15}),
    ("lib_add_footprint_pads", {"pads": [{"designator": "1", "x": 1.625, "y": 0}]}),
    ("lib_add_footprint_track", {"x1": 0, "y1": 0, "x2": 1.625, "y2": 0, "width": 0.12}),
    ("lib_add_footprint_tracks", {"tracks": [{"x1": 0, "y1": 0, "x2": 1.625, "y2": 0}]}),
    ("lib_add_footprint_arc", {"x_center": 0, "y_center": 0, "radius": 1.625}),
    ("lib_add_footprint_text", {"text": "1", "x": 1.625, "y": 0}),
    ("lib_add_footprint_fills", {"fills": [{"layer": "TopPaste", "x1": 0, "y1": 0, "x2": 1.625, "y2": 1}]}),
]


@pytest.mark.parametrize("tool,kwargs", CALLS)
def test_library_tools_name_millimetres_and_only_millimetres(monkeypatch, tool, kwargs):
    tools, bridge = _tools(monkeypatch, library_module, "register_library_tools")
    asyncio.run(tools[tool](**kwargs))
    asyncio.run(tools[tool](units="mm", **kwargs))
    (_c, mil), (_c2, mm) = bridge.calls
    assert "units" not in mil, "a call without units keeps its wire shape"
    assert mm["units"] == "mm"
    assert "1.625" in repr(mm), "the value is sent as given"


@pytest.mark.parametrize("tool,kwargs", CALLS)
def test_an_unknown_unit_is_refused_before_sending(monkeypatch, tool, kwargs):
    tools, bridge = _tools(monkeypatch, library_module, "register_library_tools")
    out = asyncio.run(tools[tool](units="inch", **kwargs))
    assert "units must be" in out["error"]
    assert bridge.calls == []


def test_pcb_place_pad_takes_millimetres(monkeypatch):
    tools, bridge = _tools(monkeypatch, pcb_module, "register_pcb_tools")
    asyncio.run(tools["pcb_place_pad"](x=12.7, y=1.625, x_size=0.3, y_size=1.15, units="mm"))
    asyncio.run(tools["pcb_place_pad"](x=500, y=600))
    (_c, mm), (_c2, mil) = bridge.calls
    assert (mm["x"], mm["y"], mm["x_size"], mm["units"]) == ("12.7", "1.625", "0.3", "mm")
    assert (mil["x"], mil["x_size"], mil["y_size"]) == ("500", "60", "60")
    assert "units" not in mil
    out = asyncio.run(tools["pcb_place_pad"](x=0, y=0, units="cm"))
    assert "units must be" in out["error"] and len(bridge.calls) == 2


# --------------------------------------------------------------------
# The script side.
# --------------------------------------------------------------------

def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def test_the_conversion_is_exact_under_fpc(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    utils = "Utils.pas"
    checks = []
    for i, mm in enumerate(MM, 1):
        coord = round(mm * 10_000_000 / 25.4)
        checks.append(f"  if CoordFromUnits({mm!r}, 'mm') <> {coord} then Halt({i});")
    n = len(MM)
    checks += [
        f"  if CoordFromUnits(64, '') <> 640000 then Halt({n + 1});",
        f"  if CoordFromUnits(64, 'mil') <> 640000 then Halt({n + 2});",
        f"  if CoordFromUnits(47.2441, 'MIL') <> 472441 then Halt({n + 3});",
        f"  if Abs(MilsInUnits(60, 'mm') - 1.524) > 1e-9 then Halt({n + 4});",
        f"  if MilsInUnits(60, '') <> 60 then Halt({n + 5});",
        f"  if UnitsProblem('mm') <> '' then Halt({n + 6});",
        f"  if UnitsProblem('mils') <> '' then Halt({n + 7});",
        f"  if UnitsProblem('inch') = '' then Halt({n + 8});",
    ]
    program = "\n".join([
        "program units;", "{$mode delphi}", "uses SysUtils;", "type TCoord = Integer;",
        _routine(utils, "MilsToCoordF"), _routine(utils, "MMToCoord"),
        _routine(utils, "UnitsAreMM"), _routine(utils, "UnitsProblem"),
        _routine(utils, "CoordFromUnits"), _routine(utils, "MilsInUnits"),
        "begin", *checks, "end.",
    ])
    path = tmp_path / "units.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("units.exe" if os.name == "nt" else "units")
    assert subprocess.run([str(exe)]).returncode == 0
    # 1.625 mm reads back as 1.625 mm to well inside a micrometre.
    assert abs(round(1.625 * 10_000_000 / 25.4) * 25.4 / 10_000_000 - 1.625) < 1e-5


def test_region_points_parse_exactly_under_fpc(tmp_path):
    """The regions handler reads its points with RegionPointCount and
    RegionPointCoord. Compiled here with the IsFloatStr they rely on and
    fed the payload the Python side builds, so the two agree on every
    coordinate, and a malformed pair refuses the region."""
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    region = {"layer": "TopPaste",
              "points": [[-0.775, -0.625], [0.775, -0.625], [0.775, 0.625],
                         [-0.775, 0.625]]}
    payload, problems = library_module._regions_payload([region])
    assert not problems
    points = payload.split(";points=", 1)[1]
    lines = [f"  WriteLn(RegionPointCount('{points}'));"]
    for i in range(1, 5):
        lines.append(f"  WriteLn(RegionPointCoord('{points}', {i}, True) + '|' "
                     f"+ RegionPointCoord('{points}', {i}, False));")
    lines += [
        "  WriteLn(RegionPointCount('1,2/3,4/5'));",
        "  WriteLn(RegionPointCount('1,2/3,x/5,6'));",
        "  WriteLn(RegionPointCount(''));",
        "  WriteLn('<' + RegionPointCoord('1,2/3,4', 3, True) + '>');",
    ]
    program = "\n".join([
        "program regions;", "{$mode delphi}", "uses SysUtils;",
        _routine("Utils.pas", "IsFloatStr"),
        _routine("Library.pas", "RegionPointCount"),
        _routine("Library.pas", "RegionPointCoord"),
        "begin", *lines, "end.",
    ])
    path = tmp_path / "regions.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True,
                           text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("regions.exe" if os.name == "nt" else "regions")
    out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
    assert out[0] == "4"
    for got, (x, y) in zip(out[1:5], region["points"]):
        gx, gy = got.split("|")
        assert (float(gx), float(gy)) == (x, y)
    assert out[5:] == ["-1", "-1", "0", "<>"]


@pytest.mark.parametrize("unit,name", [
    ("Library.pas", "Lib_AddFootprintPad"), ("Library.pas", "Lib_AddFootprintPads"),
    ("Library.pas", "Lib_AddFootprintTrack"), ("Library.pas", "Lib_AddFootprintTracks"),
    ("Library.pas", "Lib_AddFootprintArc"), ("Library.pas", "Lib_AddFootprintText"),
    ("Library.pas", "Lib_AddFootprintRegions"),
    ("PCB.pas", "PCB_PlacePad"),
])
def test_every_authoring_handler_reads_lengths_in_the_callers_unit(unit, name):
    body = _routine(unit, name)
    check = body.index("UnitsProblem(UnitsStr)")
    assert check < body.index("StrToFloatDef("), "the unit is checked before any length is read"
    assert "'BAD_UNITS'" in body
    assert "CoordFromUnits(" in body
    assert not re.search(r"MilsToCoord\(\s*(X|Y|XSize|YSize|HoleSize|X1|Y1|X2|Y2|Width|"
                         r"XCenter|YCenter|Radius|Size)\s*\)", body), "a length still goes through whole mils"
    for length in ("'x'", "'y'", "'x_size'", "'x1'", "'width'", "'radius'", "'size'"):
        assert not re.search(r"StrToIntDef\([^)]*" + length, body), f"{length} parsed as an integer"


def test_create_document_focuses_what_it_made_and_refuses_an_unsaved_one():
    body = _routine("Application.pas", "App_CreateDocument")
    not_saved = body.index("If Not Saved Then")
    assert "'NOT_SAVED'" in body[not_saved:]
    show = body.index("Client.ShowDocument(ServerDoc)")
    assert not_saved < show < body.index('"focused":')
    assert "CurrentFocusedDocPath(0)" in body
