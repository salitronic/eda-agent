# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_set_grids also sets how the visible grid is drawn (#442).

The fine and coarse colour and style belong to the grid manager's default
grid ("Global Board Snap Grid"), on the members every grid kind shares
(Color, ColorLarge, DrawMode, DrawModeLarge), so asking them is safe on any
grid. Measured on AD 26.12 in a PCB library: each writes, reads back and is
kept over save and reopen, but a style or multiplier written outside the
grid manager's BeginUpdate / EndUpdate is not drawn until something else
redraws the grid. So every value is checked first, every write goes inside
that bracket, and the bracket is always closed. With a PCB library in front
the library is acted on, never another open board.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import pcb
from tests.pascal_source import load

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_PCB = SCRIPTS / "PCB.pas"


def _bodies() -> dict[str, str]:
    return load(_PCB, minimum=100)


def _helper(name: str) -> str:
    code = _PCB.read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


STYLES = [("lines", 0), ("dots", 1), ("none", 2), ("  DOTS ", 1), ("Lines", 0),
          ("dot", -1), ("", -1), ("do not draw", -1)]
WORDS = [(0, "lines"), (1, "dots"), (2, "none"), (3, ""), (-1, "")]


def test_the_style_words_are_the_engines_values(tmp_path):
    fpc = _fpc()
    lines = ["program gridstyle;", "{$mode delphi}", "uses SysUtils;",
             _helper("GridStyleNo"), _helper("GridStyleWord"), "begin"]
    for word, _ in STYLES:
        lines.append(f"  WriteLn(GridStyleNo('{word}'));")
    for number, _ in WORDS:
        lines.append(f"  WriteLn('[' + GridStyleWord({number}) + ']');")
    lines.append("end.")
    path = tmp_path / "gridstyle.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("gridstyle.exe" if os.name == "nt" else "gridstyle")
    got = subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
    assert got == [str(n) for _, n in STYLES] + [f"[{w}]" for _, w in WORDS]


def test_a_library_in_front_is_acted_on_never_another_board():
    body = _bodies()["PCB_SetGrids"]
    lib = body.index("If LvPcbLibInFront(0) Then")
    assert lib < body.index("Board := PcbLib.Board")
    assert re.search(r"End\s+Else\s+Board := GetPCBBoardAnywhere\(0\);", body)
    assert body.index("Board := GetPCBBoardAnywhere(0)") > lib


def test_every_value_is_checked_before_the_first_write():
    body = _bodies()["PCB_SetGrids"]
    begin = body.index("Mgr.BeginUpdate")
    first_write = body.index("Board.SnapGridSizeX := ")
    assert begin < first_write
    refusals = [m.start() for m in re.finditer(r"BuildErrorResponse\(", body)]
    assert len(refusals) >= 8
    assert all(r < begin for r in refusals)
    for text in ("'fine_color and coarse_color are #RRGGBB'",
                 "'fine_style and coarse_style are lines, dots or none'",
                 "give fine_style or dot_grid, not both",
                 "no default grid, so its colours and styles cannot be set"):
        assert body.index(text) < begin, text


def test_every_write_is_inside_the_bracket_and_it_is_always_closed():
    body = _bodies()["PCB_SetGrids"]
    begin = body.index("Mgr.BeginUpdate")
    end = body.index("Mgr.EndUpdate")
    for write in ("Board.SnapGridSizeX := ", "Board.BigVisibleGridSize := ",
                  "Board.DrawDotGrid := (DotStr = 'true');",
                  "Board.BigVisibleGridMultFactor := V",
                  "Board.SnapGridUnit := UnitWanted",
                  "G.SetProperty_Color(FineColor)",
                  "G.SetProperty_ColorLarge(CoarseColor)",
                  "G.SetProperty_DrawMode(FineStyle)",
                  "G.SetProperty_DrawModeLarge(CoarseStyle)"):
        at = body.index(write)
        assert begin < at < end, write
        assert body.count(write) == 1, write
    # Nothing between the bracket's ends can leave it open, and a write
    # that raises still reaches EndUpdate.
    assert "Exit;" not in body[begin:end]
    assert body.index("Problem := 'a grid write raised an error';") < end


def test_the_look_is_read_back_after_the_bracket():
    body = _bodies()["PCB_SetGrids"]
    end = body.index("Mgr.EndUpdate")
    for member, key in (("GetProperty_Color;", "fine_color"),
                        ("GetProperty_ColorLarge;", "coarse_color"),
                        ("GetProperty_DrawMode;", "fine_style"),
                        ("GetProperty_DrawModeLarge;", "coarse_style")):
        read = body.index("Try N := G." + member)
        assert read > end, member
        assert body.index(f"""'"{key}"'""", read) > read
    assert "GridLookJson(G)" in body


def test_the_default_grid_is_found_by_its_flag_and_asked_shared_members_only():
    finder = _bodies()["DefaultPlacementGrid"]
    assert "G.GetProperty_IsDefault" in finder
    look = _bodies()["GridLookJson"]
    # A member only one grid kind has stops the script when asked of the
    # other kind (#419); colour and style are shared.
    for member in ("GridStepX", "GridStepY", "AngularStep", "RadialStep"):
        assert member not in finder, member
        assert member not in look, member


def _tool(monkeypatch):
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params, **kwargs):
            sent.append((command, params))
            return {"success": True}

    monkeypatch.setattr(pcb, "get_bridge", lambda: _Bridge())
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    pcb.register_pcb_tools(_Mcp())
    return captured["pcb_set_grids"], sent


def test_the_tool_sends_colours_and_styles(monkeypatch):
    tool, sent = _tool(monkeypatch)
    asyncio.run(tool(fine_color="ff0000", coarse_color="#00a0Ff",
                     fine_style=" Dots", coarse_style="NONE"))
    assert sent == [("pcb.set_grids", {"fine_color": "#FF0000",
                                       "coarse_color": "#00A0FF",
                                       "fine_style": "dots",
                                       "coarse_style": "none"})]


@pytest.mark.parametrize("kwargs", [
    {"fine_color": "#FF00"}, {"coarse_color": "#GG0000"},
    {"fine_style": "dot"}, {"coarse_style": "hidden"},
    {"dot_grid": True, "fine_style": "lines"},
])
def test_the_tool_refuses_before_sending(monkeypatch, kwargs):
    tool, sent = _tool(monkeypatch)
    result = asyncio.run(tool(**kwargs))
    assert sent == [] and result["ok"] is False, kwargs
