# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A decimal in a filter reaches the script intact and matches as a number.

Reported on a comma-decimal machine: obj_query(eTrackObject,
filter="X2=3172.6356") raised a script modal and stopped the bridge. The
filter is never written into script text: the Python side sends it as a
JSON string and MatchesFilterPCB compares it with what the getter reads.
What was wrong there is that the compare was a plain string compare, so a
length written 3172.63560 or 3172,6356 matched nothing. Lengths now
compare as numbers, to half a coordinate unit, under either separator.

The PCB matcher and the real helpers it calls are lifted out of the scripts
and run under Free Pascal with the decimal separator set to '.' and to
','; only GetPCBProperty is a stand-in.
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

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


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


@pytest.mark.parametrize("filt", ["X2=3172.6356", "X1=-120.5|Layer=TopLayer",
                                  "Width=0,2032"])
@pytest.mark.parametrize("tool,kwargs", [
    ("obj_query", {"object_type": "eTrackObject", "properties": "X2"}),
    ("obj_select", {"object_type": "eTrackObject"}),
    ("obj_modify", {"object_type": "eTrackObject", "set": "Width=10"}),
    ("obj_delete", {"object_type": "eTrackObject"}),
])
def test_the_value_is_sent_exactly_as_given(monkeypatch, filt, tool, kwargs):
    bridge = _Bridge()
    asyncio.run(_tools(monkeypatch, bridge)[tool](filter=filt, **kwargs))
    assert bridge.calls, f"{tool} refused {filt!r}"
    assert bridge.calls[0][1]["filter"] == filt


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


#: (filter, should match) against a track whose getter reads the values
#: in the stub below.
CASES = [
    ("X2=3172.6356", True),
    ("X2=3172.63560", True),
    ("X2=3172,6356", True),
    ("X2= 3172.6356", True),
    ("X2=3172.636", False),
    ("X2=3172.6357", False),
    ("X1=-120.5", True),
    ("X1=-120.50", True),
    ("X1=120.5", False),
    ("Width=10", True),
    ("Width=10.0", True),
    ("Layer=TopLayer", True),
    ("Layer=Toplayer", False),
    ("Text=1.5", False),          # not a length: still exact
    ("Text=1,5", True),
    ("X2=abc", False),            # not a number: no match, and no raise
    ("X2=3172.6356|Layer=TopLayer", True),
    ("X2=3172.6356|Layer=BottomLayer", False),
]


@pytest.mark.parametrize("separator", [".", ","])
def test_lengths_match_as_numbers_under_either_separator(tmp_path, separator):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks = []
    for i, (filt, want) in enumerate(CASES, 1):
        test = "not " if want else ""
        checks.append(f"  if {test}MatchesFilterPCB(P, '{filt}') then Halt({i});")
    program = "\n".join([
        "program decimals;", "{$mode delphi}", "uses SysUtils;",
        "type IPCB_Primitive = class end;",
        "function GetPCBProperty(Obj: IPCB_Primitive; PropName: String): String;",
        "begin",
        "  if PropName = 'X2' then Result := '3172.6356'",
        "  else if PropName = 'X1' then Result := '-120.5'",
        "  else if PropName = 'Width' then Result := '10'",
        "  else if PropName = 'Layer' then Result := 'TopLayer'",
        "  else if PropName = 'Text' then Result := '1,5'",
        "  else Result := '';",
        "end;",
        _routine("Utils.pas", "IsFloatStr"),
        _routine("Utils.pas", "StrToFloatDef"),
        # The filter grammar (=, <>, <, >, <=, >=) MatchesFilterPCB uses.
        _routine("Utils.pas", "FilterOpPos"),
        _routine("Utils.pas", "FilterOpText"),
        _routine("Utils.pas", "FilterWildMatch"),
        _routine("Utils.pas", "FilterOpHolds"),
        _routine("PCBGeneric.pas", "IsLengthProperty"),
        _routine("PCBGeneric.pas", "FilterValueMatchesPCB"),
        _routine("PCBGeneric.pas", "MatchesFilterPCB"),
        "var P: IPCB_Primitive;",
        "begin",
        f"  DecimalSeparator := '{separator}';",
        "  P := IPCB_Primitive.Create;",
        *checks,
        "end.",
    ])
    path = tmp_path / "decimals.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("decimals.exe" if os.name == "nt" else "decimals")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        filt, want = CASES[ran.returncode - 1] if ran.returncode <= len(CASES) else ("?", "?")
        pytest.fail(f"separator {separator!r}: {filt!r} should "
                    f"{'match' if want else 'not match'} (exit {ran.returncode}) "
                    f"{ran.stdout}{ran.stderr}")
