# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A filter the bridge cannot parse refuses the call; it never matches all.

A filter is Name=Value pairs joined by ``|``. Both script matchers used to
skip a condition without ``=``, so an Altium query expression or a bare
name left the filter empty and matched every object of the type:
``obj_delete(object_type="track", filter="OnLayer('TopLayer') Or
OnLayer('BottomLayer')")`` removed every track on a board, footprint
silkscreen included, and reported success. ``confirm_delete_all`` was no
protection, because the tool believed it had a filter.

Three layers now, each tested here: the Python tools refuse before
sending, every script entry point refuses with BAD_FILTER, and the
matchers themselves treat a malformed condition as matching nothing.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import generic as generic_module
from eda_agent.tools.generic import filter_problem

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "altium"
QUERY = "OnLayer('TopLayer') Or OnLayer('BottomLayer')"


@pytest.mark.parametrize("filt,bad", [
    ("", ""),
    ("Layer=TopLayer", ""),
    ("Layer=TopLayer|Net=GND", ""),
    ("Text=a=b", ""),
    ("Designator.Text=PWR IN", ""),
    ("Layer=TopLayer|", ""),
    (QUERY, QUERY),
    ("IsDesignator", "IsDesignator"),
    ("Layer = TopLayer", "Layer = TopLayer"),
    ("Text!=x", ""),
    ("Y>4250|Y<=5000", ""),
    ("Width>=8", ""),
    ("Net!GND", "Net!GND"),
    ("=x", "=x"),
    ("!=x", "!=x"),
    ("Layer=TopLayer|IsDesignator", "IsDesignator"),
])
def test_only_name_value_pairs_pass(filt, bad):
    assert filter_problem(filt) == bad


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"matched": 1}


def _tools(monkeypatch, bridge):
    monkeypatch.setattr(generic_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    generic_module.register_generic_tools(DummyMcp())
    return captured


@pytest.mark.parametrize("name,kwargs", [
    ("obj_delete", {"object_type": "track", "filter": QUERY, "confirm_delete_all": True}),
    ("obj_query", {"object_type": "eTextObject", "properties": "Text", "filter": "IsDesignator"}),
    ("obj_select", {"object_type": "eTextObject", "filter": "IsDesignator"}),
    ("obj_modify", {"object_type": "eTrackObject", "set": "Width=10", "filter": QUERY}),
    ("obj_count", {"object_type": "eTrackObject", "filter": QUERY}),
    ("obj_batch_delete", {"operations": [{"object_type": "eTrackObject", "filter": QUERY}],
                          "confirm_delete_all": True}),
    ("obj_batch_modify", {"operations": [{"object_type": "eTrackObject", "filter": QUERY,
                                          "set": "Width=10"}]}),
])
def test_the_tools_refuse_before_sending(monkeypatch, name, kwargs):
    bridge = _Bridge()
    out = asyncio.run(_tools(monkeypatch, bridge)[name](**kwargs))
    assert "error" in out and "Name=Value" in out["error"]
    assert bridge.calls == [], "nothing may reach Altium"


def test_a_well_formed_filter_still_goes_through(monkeypatch):
    bridge = _Bridge()
    asyncio.run(_tools(monkeypatch, bridge)["obj_delete"](
        object_type="eTrackObject", filter="Layer=TopLayer|InComponent=false"))
    assert bridge.calls and bridge.calls[0][1]["filter"] == "Layer=TopLayer|InComponent=false"


def _code(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


@pytest.mark.parametrize("unit,entry", [
    ("Generic.pas", "ProcessActiveDoc"),
    ("Generic.pas", "ProcessDocByPath"),
    ("Generic.pas", "IterateProjectDocs"),
    ("PCBGeneric.pas", "ProcessActivePCBDoc"),
])
def test_every_script_entry_point_refuses_first(unit, entry):
    body = _routine(_code(unit), entry)
    check = body.find("FilterProblem(FilterStr)")
    assert check > 0, f"{entry} never checks its filter"
    assert "'BAD_FILTER'" in body
    for later in ("SchServer", "GetWorkspace", "GetPCBBoard", "Iterator"):
        at = body.find(later)
        assert at < 0 or check < at, f"{entry} touches {later} before checking the filter"


@pytest.mark.parametrize("unit,matcher", [
    ("Generic.pas", "MatchesFilter"),
    ("PCBGeneric.pas", "MatchesFilterPCB"),
])
def test_no_matcher_skips_a_condition_it_cannot_parse(unit, matcher):
    body = _routine(_code(unit), matcher)
    assert not re.search(r"EqPos\s*=\s*0\s*Then\s*Continue", body)
    assert re.search(r"\(?EqPos\s*<\s*2\)?\s*(Or\s*\(Op\s*=\s*''\)\s*)?Then", body)


def _fpc():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def test_the_production_check_and_matchers_run_fail_closed(tmp_path):
    """FilterProblem and both matchers, extracted and executed under FPC."""
    fpc = _fpc()
    problem = "\n".join([_routine(_code("Utils.pas"), "FilterOpPos"),
                         _routine(_code("Utils.pas"), "FilterOpText"),
                         _routine(_code("Utils.pas"), "FilterProblem")])
    sch = _routine(_code("Generic.pas"), "MatchesFilter")
    pcb = _routine(_code("PCBGeneric.pas"), "MatchesFilterPCB")
    # What the PCB matcher compares a value with: a length as a number.
    value = "\n".join([_routine(_code("Utils.pas"), "IsFloatStr"),
                       _routine(_code("Utils.pas"), "StrToFloatDef"),
                       _routine(_code("Utils.pas"), "FilterWildMatch"),
                       _routine(_code("Utils.pas"), "FilterOpHolds"),
                       _routine(_code("PCBGeneric.pas"), "IsLengthProperty"),
                       _routine(_code("PCBGeneric.pas"), "FilterValueMatchesPCB")])
    checks = [(f, b) for f, b in [
        ("", ""), ("Layer=TopLayer", ""), ("Text=a=b", ""), ("Layer=TopLayer|", ""),
        ("Designator.Text=PWR IN", ""), (QUERY, QUERY), ("IsDesignator", "IsDesignator"),
        ("Layer = TopLayer", "Layer = TopLayer"), ("=x", "=x"),
        ("Net!=GND", ""), ("Y>4250|Y<=5000", ""), ("Net!GND", "Net!GND"),
    ]]
    lines = [f"  if FilterProblem('{f.replace(chr(39), chr(39) * 2)}') <> "
             f"'{b.replace(chr(39), chr(39) * 2)}' then Halt({i});"
             for i, (f, b) in enumerate(checks, 1)]
    # Every object's Layer is TopLayer in the stubs: a query that names
    # it must still match nothing, and a real condition must still match.
    n = len(checks)
    q = QUERY.replace("'", "''")
    lines += [
        f"  if MatchesFilterPCB(P, '{q}') then Halt({n + 1});",
        f"  if MatchesFilterPCB(P, 'IsDesignator') then Halt({n + 2});",
        f"  if not MatchesFilterPCB(P, 'Layer=TopLayer') then Halt({n + 3});",
        f"  if MatchesFilterPCB(P, 'Layer=BottomLayer') then Halt({n + 4});",
        f"  if MatchesFilter(S, '{q}') then Halt({n + 5});",
        f"  if not MatchesFilter(S, 'Layer=TopLayer|') then Halt({n + 6});",
        # Y is 4300 in the stubs, a length; Layer is no number.
        f"  if not MatchesFilterPCB(P, 'Layer!=BottomLayer') then Halt({n + 7});",
        f"  if MatchesFilterPCB(P, 'Layer!=TopLayer') then Halt({n + 8});",
        f"  if not MatchesFilterPCB(P, 'Y>4250|Y<=4300') then Halt({n + 9});",
        f"  if MatchesFilterPCB(P, 'Y<4300') then Halt({n + 10});",
        f"  if not MatchesFilterPCB(P, 'Y>=4300,0') then Halt({n + 11});",
        f"  if MatchesFilterPCB(P, 'Layer>3') then Halt({n + 12});",
        f"  if MatchesFilterPCB(P, 'Y!=4300.00001') then Halt({n + 13});",
        f"  if not MatchesFilter(S, 'Y>4000') then Halt({n + 14});",
        f"  if MatchesFilter(S, 'Layer!=TopLayer') then Halt({n + 15});",
        f"  if MatchesFilter(S, 'Net!GND') then Halt({n + 16});",
    ]
    program = "\n".join([
        "program filters;", "{$mode delphi}", "uses SysUtils;",
        "type IPCB_Primitive = class end; ISch_GraphicalObject = class end;",
        "function GetPCBProperty(Obj: IPCB_Primitive; PropName: String): String;",
        "begin if PropName = 'Layer' then Result := 'TopLayer'",
        " else if PropName = 'Y' then Result := '4300' else Result := ''; end;",
        "function GetSchProperty(Obj: ISch_GraphicalObject; PropName: String): String;",
        "begin if PropName = 'Layer' then Result := 'TopLayer'",
        " else if PropName = 'Y' then Result := '4300' else Result := ''; end;",
        # The TextColor guard, which test_sch_textcolor runs for real: every
        # property here is one the stub object declares.
        "function UnsupportedSchProperty(Obj: ISch_GraphicalObject; SetStr: String): String;",
        "begin Result := ''; end;",
        "procedure NotePropertyDiag(Kind: String; PropName: String);",
        "begin end;",
        problem, value, sch, pcb,
        "var P: IPCB_Primitive; S: ISch_GraphicalObject;",
        "begin", "  P := IPCB_Primitive.Create; S := ISch_GraphicalObject.Create;",
        *lines, "end.",
    ])
    path = tmp_path / "filters.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("filters.exe" if os.name == "nt" else "filters")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, f"check {ran.returncode} failed"
