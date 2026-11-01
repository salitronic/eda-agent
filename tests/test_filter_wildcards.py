# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A filter can match a wildcard pattern: Name~POUR_* and Name!~POUR_*.

Every filter condition was an exact compare (or a numeric one), so the
objects of one family, a set of polygons named POUR_L1-GND, POUR_L2_L3-VCC
..., could only be reached one call per object. Each generic call walks
every object of the type on the board and redraws it, so removing twenty
pours cost twenty walks. ``~`` matches a pattern: ``*`` is any run of
characters (none too), ``?`` exactly one, case ignored; ``!~`` is its
negation. ``=`` is unchanged, so a value holding a literal ``*`` still
matches only itself.

The operator lives in the filter grammar both matchers share (Utils.pas),
so it holds for query, modify, delete and select, schematic and PCB. The
matcher and the real helpers it calls are lifted out of the scripts and
run under Free Pascal; only GetPCBProperty is a stand-in.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools.generic import filter_problem

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


@pytest.mark.parametrize("filt,bad", [
    ("Name~POUR_*", ""),
    ("Name!~POUR_*", ""),
    ("Layer=TopLayer|Name~P?UR*", ""),
    ("Name~", ""),
    ("Text~A=B*", ""),              # the first operator is the ~, as the script reads it
    ("~POUR_*", "~POUR_*"),
    ("Na me~x", "Na me~x"),
])
def test_the_tools_let_a_wildcard_condition_through(filt, bad):
    assert filter_problem(filt) == bad


def test_the_grammar_knows_the_operator():
    pos = _routine("Utils.pas", "FilterOpPos")
    assert "'=!<>~'" in pos
    text = _routine("Utils.pas", "FilterOpText")
    assert "(Two = '!~')" in text
    holds = _routine("Utils.pas", "FilterOpHolds")
    assert "If Op = '~' Then Begin Result := FilterWildMatch(Actual, Expected); Exit; End;" in holds
    assert "If Op = '!~' Then Begin Result := Not FilterWildMatch(Actual, Expected); Exit; End;" in holds
    # before the numeric compare, which refuses every non-number
    assert holds.index("FilterWildMatch") < holds.index("IsFloatStr")


def test_the_schematic_guard_sees_the_name_whatever_the_operator():
    body = _routine("Generic.pas", "MatchesFilter")
    rewrite = body.index("Condition := PropName + '=' + Expected;")
    assert rewrite < body.index("UnsupportedSchProperty(Obj, Condition)")


#: (actual, op, pattern, holds)
CASES = [
    ("POUR_L1-GND", "~", "POUR_*", True),
    ("pour_l1-gnd", "~", "POUR_*", True),          # case ignored
    ("MYPOUR_L1", "~", "POUR_*", False),           # anchored at the start
    ("POUR_L1-GND", "~", "*-GND", True),
    ("POUR_L1-GNDX", "~", "*-GND", False),         # anchored at the end
    ("POUR_L1-GND", "~", "*L1*", True),
    ("POUR_L1-GND", "~", "POUR_L?-GND", True),
    ("POUR_L12-GND", "~", "POUR_L?-GND", False),   # ? is exactly one
    ("POUR_L12-GND", "~", "POUR_L*-GND", True),
    ("", "~", "*", True),
    ("", "~", "", True),
    ("X", "~", "", False),
    ("", "~", "?", False),
    ("ABC", "~", "***", True),
    ("ABC", "~", "A**C", True),
    ("AXBXC", "~", "A*B*C", True),
    ("AXBXD", "~", "A*B*C", False),
    ("AAAB", "~", "*AB", True),                    # backtracks past a false start
    ("ABAB", "~", "*AB*AB", True),                  # both stars empty
    ("ABA", "~", "*AB*AB", False),                  # not enough left after the first AB
    ("ABABAB", "~", "*AB*AB", True),
    ("A*B", "~", "A*B", True),
    ("POUR_L1-GND", "!~", "POUR_*", False),
    ("OTHER", "!~", "POUR_*", True),
    ("A*B", "=", "A*B", True),                     # = stays exact
    ("AXB", "=", "A*B", False),
    ("12.5", "~", "12*", True),                    # a number is text to ~
]


def test_wildcards_match_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks = []
    for i, (actual, op, pattern, want) in enumerate(CASES, 1):
        test = "not " if want else ""
        checks.append(f"  if {test}FilterOpHolds('{op}', '{actual}', '{pattern}', "
                      f"'{actual}' = '{pattern}') then Halt({i});")
    # The PCB matcher end to end: a polygon named POUR_L2_L3-VCC on Mech21.
    pcb = [("Name~POUR_*", True), ("Name~pour_*|Layer=Mechanical21", True),
           ("Name~POUR_*|Layer=Mechanical22", False), ("Name!~POUR_*", False),
           ("Name~*-VCC", True), ("Name~*-GND", False), ("Name=POUR_*", False)]
    base = len(CASES)
    for j, (filt, want) in enumerate(pcb, base + 1):
        test = "not " if want else ""
        checks.append(f"  if {test}MatchesFilterPCB(P, '{filt}') then Halt({j});")
    program = "\n".join([
        "program wildcards;", "{$mode delphi}", "uses SysUtils;",
        "type IPCB_Primitive = class end;",
        "function GetPCBProperty(Obj: IPCB_Primitive; PropName: String): String;",
        "begin",
        "  if PropName = 'Name' then Result := 'POUR_L2_L3-VCC'",
        "  else if PropName = 'Layer' then Result := 'Mechanical21'",
        "  else Result := '';",
        "end;",
        _routine("Utils.pas", "IsFloatStr"),
        _routine("Utils.pas", "StrToFloatDef"),
        _routine("Utils.pas", "FilterOpPos"),
        _routine("Utils.pas", "FilterOpText"),
        _routine("Utils.pas", "FilterWildMatch"),
        _routine("Utils.pas", "FilterOpHolds"),
        _routine("PCBGeneric.pas", "IsLengthProperty"),
        _routine("PCBGeneric.pas", "FilterValueMatchesPCB"),
        _routine("PCBGeneric.pas", "MatchesFilterPCB"),
        "var P: IPCB_Primitive;",
        "begin",
        "  P := IPCB_Primitive.Create;",
        *checks,
        "end.",
    ])
    path = tmp_path / "wildcards.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("wildcards.exe" if os.name == "nt" else "wildcards")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        what = CASES[n - 1] if n <= base else pcb[n - base - 1] if n - base <= len(pcb) else "?"
        pytest.fail(f"case {n} {what!r} came out wrong {ran.stdout}{ran.stderr}")
