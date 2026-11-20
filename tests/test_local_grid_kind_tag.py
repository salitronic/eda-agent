# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A local grid's kind comes from its name, never from asking the grid.

A script cannot ask a placement grid its kind: no member says it, and
reading a member the grid lacks (GridStepX of a polar grid, AngularStep of
a cartesian one) raises an Undeclared identifier error that Try does not
catch, so the script stops (measured on AD 25.8.1).

So a grid made through pcb.local_grid is stored with its kind at the end
of its name (_cart, _pol), list reads the members of one kind only for a
tagged grid or the default board grid (always cartesian), and any other
grid is listed with the members both kinds have and kind unknown. The
three name helpers are run under Free Pascal; the handlers are checked for
where they take the kind from.
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


KIND = [
    ("BGA_cart", False, "cartesian"),
    ("Ring_pol", False, "polar"),
    ("BGA", False, ""),                   # made in Altium: no tag
    ("BGA_CART", False, ""),              # the tag is matched exactly
    ("_cart", False, ""),                 # a tag alone is no name
    ("_pol", False, ""),
    ("pol", False, ""),
    ("Global Board Snap Grid", True, "cartesian"),
    ("Ring_pol", True, "cartesian"),      # the default grid is cartesian
]
SHOWN = [("BGA_cart", "BGA"), ("Ring_pol", "Ring"), ("BGA", "BGA"),
         ("_cart", "_cart"), ("A_pol_cart", "A_pol")]
TAGGED = [("BGA", "cartesian", "BGA_cart"), ("BGA_cart", "cartesian", "BGA_cart"),
          ("Ring", "polar", "Ring_pol"), ("Ring_pol", "polar", "Ring_pol"),
          ("Grid", "polar", "Grid_pol")]


def test_the_name_helpers_read_and_write_the_tag(tmp_path):
    fpc = _fpc()
    lines = ["program gridtag;", "{$mode delphi}", "uses SysUtils;",
             _helper("GridKindOfName"), _helper("GridShownName"),
             _helper("GridTaggedName"), "begin"]
    for name, default, _ in KIND:
        lines.append(f"  WriteLn('[' + GridKindOfName('{name}', {str(default)}) + ']');")
    for name, _ in SHOWN:
        lines.append(f"  WriteLn('[' + GridShownName('{name}') + ']');")
    for name, kind, _ in TAGGED:
        lines.append(f"  WriteLn('[' + GridTaggedName('{name}', '{kind}') + ']');")
    lines.append("end.")
    path = tmp_path / "gridtag.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("gridtag.exe" if os.name == "nt" else "gridtag")
    got = subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
    want = ([f"[{k}]" for _, _, k in KIND] + [f"[{s}]" for _, s in SHOWN]
            + [f"[{t}]" for _, _, t in TAGGED])
    assert got == want


def test_list_takes_the_kind_from_the_name_and_reads_nothing_else():
    bodies = _bodies()
    any_grid = bodies["SdkAnyGridJson"]
    # No member of one kind is read to find the kind.
    for member in ("GridStepX", "GridStepY", "QuadrantSize", "Rotation",
                   "AngularStep", "RadialStep", "Radius", "Angle"):
        assert member not in any_grid, member
    assert "GridKindOf(G)" in any_grid
    cart = any_grid.index("SdkCartesianJson(")
    polar = any_grid.index("SdkPolarJson(")
    assert any_grid.rindex("Kind = 'cartesian'", 0, cart) < cart
    assert any_grid.rindex("Kind = 'polar'", 0, polar) < polar
    # A grid of no known kind gets the shared members only.
    assert "SdkGridCommonJson(G, Kind)" in any_grid
    kind_of = bodies["GridKindOf"]
    assert "GridKindOfName(Name, IsDefault)" in kind_of
    assert "'unknown'" in kind_of
    for member in ("GridStepX", "AngularStep"):
        assert member not in kind_of


def test_the_shared_members_name_the_kind_and_the_name_as_given():
    common = _bodies()["SdkGridCommonJson"]
    assert "JsonStr('name', GridShownName(Name))" in common
    assert "JsonStr('stored_name', Name)" in common
    assert "JsonStr('kind', Kind)" in common
    bodies = _bodies()
    assert "SdkGridCommonJson(G, 'cartesian')" in bodies["SdkCartesianJson"]
    assert "SdkGridCommonJson(G, 'polar')" in bodies["SdkPolarJson"]


def test_a_new_grid_is_tagged_and_a_name_of_the_other_kind_refused_first():
    body = _bodies()["PCB_LocalGrid"]
    refusal = re.search(
        r"If \(\(Action = 'add_polar'\) And \(GridKindOfName\(Name, False\) = 'cartesian'\)\)"
        r"\s+Or \(\(Action = 'add_cartesian'\) And \(GridKindOfName\(Name, False\) = 'polar'\)\)"
        r" Then\s+Begin\s+Result := BuildErrorResponse\(RequestId, 'BAD_PARAM',\s+"
        r"'the name ends in the tag of the other kind", body)
    assert refusal, "a name of the other kind is not refused"
    refused = refusal.start()
    assert refused < body.index("Mgr.AddPolarGrid")
    assert refused < body.index("Mgr.AddCartesianGrid")
    assert "PG.SetProperty_NameW(GridTaggedName(GridName, 'polar'))" in body
    assert "CG.SetProperty_NameW(GridTaggedName(GridName, 'cartesian'))" in body
    # The name given goes in as it is; with none, Altium's own name is tagged.
    assert body.count("If GridName = '' Then GridName := 'Grid';") == 2


def test_delete_and_at_know_the_name_without_its_tag():
    body = _bodies()["PCB_LocalGrid"]
    assert body.count("SameText(GridShownName(GridName), Name)") == 2
    assert "SdkGridCommonJson(G, GridKindOf(G))" in body


def test_the_tool_sends_the_name_as_given(monkeypatch):
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
    asyncio.run(captured["pcb_local_grid"]("add_polar", name="Ring"))
    assert sent == [("pcb.local_grid", {"action": "add_polar", "x": "0", "y": "0",
                                        "name": "Ring"})]
