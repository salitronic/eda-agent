# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_autoplace_silkscreen keeps the board's silk clearances.

It moved every designator, checked only pads and texts on any layer with
no clearance, and left a designator no anchor cleared on the last anchor
tried. A board with no Silk To Silk violations came back with two: a
designator pushed onto a neighbour's outline at 0 mm.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_module

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def test_a_clear_designator_is_left_alone_and_a_blocked_one_restored():
    body = _routine(_code("PCB.pas"), "PCB_AutoplaceSilkscreen")
    first_check = body.index("SilkBlocked(")
    first_move = body.index("ChangeNameAutoposition(SilkAnchorForIndex")
    assert first_check < first_move, "a designator is moved before it is checked"
    assert re.search(r"If Not SilkBlocked\([^)]*\) Then\s*Begin\s*Inc\(AlreadyClear\);\s*Continue;",
                     body), "an already clear designator must be skipped untouched"
    # No anchor cleared: back to the original autoposition and location.
    assert re.search(r"OrigAuto\s*:=\s*Comp\.NameAutoPosition", body)
    assert "ChangeNameAutoposition(OrigAuto)" in body
    assert "Slk.MoveToXY(OrigX, OrigY)" in body
    # Collected first: nothing moves while the board iterator is alive.
    walk_end = body.index("BoardIterator_Destroy(", body.index("eComponentObject"))
    assert walk_end < first_move


def test_the_check_covers_outlines_mask_openings_and_the_board_edge():
    body = _routine(_code("PCB.pas"), "SilkBlocked")
    for kind in ("eTrackObject", "eArcObject", "eFillObject", "eRegionObject",
                 "eTextObject", "ePadObject"):
        assert kind in body, f"{kind} is not an obstacle"
    # Silk only against its own overlay layer; pads only on its side.
    assert re.search(r"Else If Obj\.Layer = SilkLayer Then", body)
    assert re.search(r"\(Obj\.Layer = eMultiLayer\) Or \(Obj\.Layer = CopperSide\)", body)
    # The clearance is the overlap margin, not just the search area.
    assert re.search(r"RectsOverlap\([^;]*OBB\.Top,\s*Gap\)", body)
    assert re.search(r"SBB\.Left < BL", body)


def test_the_clearances_come_from_the_rules_or_the_call_and_never_from_nowhere():
    body = _routine(_code("PCB.pas"), "PCB_AutoplaceSilkscreen")
    # 54/55 are SilkToSolderMask / SilkToSilk in the published TRuleKind
    # order, as literals: an unknown eRule_ name halts the polling loop.
    assert "(Kind = 55)" in body and "(Kind = 54)" in body
    assert "eRule_Silk" not in body
    assert "GapMilsFromDescriptor(Rule.Descriptor)" in body
    refuse = body.index("'NO_CLEARANCE'")
    assert body.index("PcbBeginEdit(0)") > refuse, "refused after something moved"


@pytest.mark.asyncio
async def test_the_tool_forwards_clearances_and_refuses_nonsense(monkeypatch):
    calls = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            calls.append((command, params or {}))
            return {"success": True}

    monkeypatch.setattr(pcb_module, "get_bridge", lambda: _Bridge())
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(DummyMcp())
    tool = captured["pcb_autoplace_silkscreen"]

    await tool()
    assert calls[-1] == ("pcb.autoplace_silkscreen", {"mask_expansion_mils": "4.0"})
    await tool(silk_clearance_mils=5, mask_clearance_mils=2.5, designators=["Q5", "Q6"])
    assert calls[-1][1] == {"mask_expansion_mils": "4.0", "silk_clearance_mils": "5.0",
                            "mask_clearance_mils": "2.5", "designators": "Q5|Q6"}
    n = len(calls)
    assert (await tool(silk_clearance_mils=-1))["ok"] is False
    assert (await tool(designators=["Q5|Q6"]))["ok"] is False
    assert len(calls) == n


def _fpc():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def test_the_production_descriptor_parser_reads_every_unit(tmp_path):
    fpc = _fpc()
    utils = _code("Utils.pas")
    cases = [
        ("Silk To Silk (Clearance=10mil) (All),(All)", 10.0),
        ("Silk To Solder Mask (Clearance=0.254mm) (IsPad),(All)", 10.0),
        ("Clearance Constraint (Gap=6mil) (All),(All)", 6.0),
        ("Silk To Silk (Clearance=0.005in) (All),(All)", 5.0),
        ("Rule (Mode=Fast) (Gap=4.5mil) (All),(All)", 4.5),
        ("Silk To Silk (All),(All)", -1.0),
        ("", -1.0),
    ]
    lines = [f"  if Abs(GapMilsFromDescriptor('{d}') - ({v})) > 0.001 then Halt({i});"
             for i, (d, v) in enumerate(cases, 1)]
    program = "\n".join([
        "program descriptors;", "{$mode delphi}", "uses SysUtils;",
        _routine(utils, "IsFloatStr"),
        "function StrToFloatDef(S: String; D: Double): Double;",
        "var FS: TFormatSettings;",
        "begin FS := DefaultFormatSettings; FS.DecimalSeparator := '.';",
        "Result := SysUtils.StrToFloatDef(S, D, FS); end;",
        _routine(utils, "GapMilsFromDescriptor"),
        "begin", *lines, "end.",
    ])
    path = tmp_path / "descriptors.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("descriptors.exe" if os.name == "nt" else "descriptors")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, f"case {ran.returncode} failed: {cases[ran.returncode - 1]}"
