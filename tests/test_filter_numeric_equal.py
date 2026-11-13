# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A number in a filter matches the same number however it is written.

``=`` and ``!=`` compared TEXT, so ``Location.Y=9000.0`` matched nothing on
a schematic object whose getter reads ``9000``, and ``Rotation=90.0``
nothing on a board object at ``90``: no error, just zero matches, and a
modify or delete that silently did nothing. ``<`` and ``>`` already
compared numbers; ``=`` did only for PCB lengths.

Now a property the matcher KNOWS is read from a number (a length, a
colour, an orientation, a font id, an enum ordinal) compares as a number,
to half a unit in the fourth decimal, comma or point. A text, a name or a
designator stays exact, so ``Designator=1`` still does not match pin
``01``. Wildcards (``~`` / ``!~``) are unchanged.

The matchers and the real helpers they call are lifted out of the scripts
and run under Free Pascal with the decimal separator set to '.' and to
','; only the property getters are stand-ins.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(unit: str) -> str:
    return (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")


def _routine(unit: str, name: str) -> str:
    code = _code(unit)
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


def test_the_schematic_matcher_compares_numbers_as_numbers():
    body = _routine("Generic.pas", "MatchesFilter")
    assert "FilterValueMatchesSch(PropName, Actual, Expected)" in body
    assert "Actual = Expected)" not in body.replace("FilterValueMatchesSch", "")


def test_only_number_properties_compare_as_numbers():
    numbers = _routine("Generic.pas", "IsSchNumberProperty")
    for prop in ("LOCATION.Y", "CORNER.X", "COLOR", "ORIENTATION", "FONTID"):
        assert f"'{prop}'" in numbers, prop
    for prop in ("TEXT", "NAME", "DESIGNATOR", "COMMENT", "DESCRIPTION", "VERTICES"):
        assert f"(U = '{prop}')" not in numbers, prop


#: (schematic filter, should match). The stub object reads:
#: Location.X -250.5, Location.Y 9000, Color 1, Text 1.50, Designator 01,
#: Name 9000, Vertex2.X 100, Orientation 0.
SCH_CASES = [
    ("Location.Y=9000", True),
    ("Location.Y=9000.0", True),           # the reported case
    ("Location.Y=9000,0", True),
    ("Location.Y= 9000.00", True),
    ("Location.Y=9000.0001", False),
    ("Location.Y=8999.9999", False),
    ("Location.Y=abc", False),
    ("Location.Y!=9000.0", False),
    ("Location.Y!=9000.5", True),
    ("Color=1|Location.Y=9000.0", True),   # the call that found it
    ("Color=1.0", True),
    ("Color=2", False),
    ("Location.X=-250.50", True),
    ("Location.X=250.5", False),
    ("Vertex2.X=100.0", True),
    ("Text=1.5", False),                   # a text stays exact
    ("Text=1.50", True),
    ("Designator=1", False),               # pin 01 is not pin 1
    ("Name=9000.0", False),
    ("Location.Y~9000.0", False),          # a pattern is text, as before
    ("Location.Y~9000", True),
    ("Orientation=0.0", True),
    ("Orientation=abc", False),            # no number, not read as 0
]

#: (PCB filter, should match). Rotation 90, UnionIndex 3, X 100.5, Name 7.
PCB_CASES = [
    ("Rotation=90.0", True),
    ("Rotation=90", True),
    ("Rotation=90.001", False),
    ("Rotation!=90.0", False),
    ("UnionIndex=3.0", True),
    ("X=100.50", True),
    ("Name=7.0", False),
]


def _fpc() -> str:
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


@pytest.mark.parametrize("separator", [".", ","])
def test_numbers_match_as_numbers_under_either_separator(tmp_path, separator):
    fpc = _fpc()
    cases = [("MatchesFilter(S", f, w) for f, w in SCH_CASES] + \
            [("MatchesFilterPCB(P", f, w) for f, w in PCB_CASES]
    checks = [f"  if {'not ' if want else ''}{call}, '{filt}') then Halt({i});"
              for i, (call, filt, want) in enumerate(cases, 1)]
    program = "\n".join([
        "program numeric;", "{$mode delphi}", "uses SysUtils;",
        "type IPCB_Primitive = class end; ISch_GraphicalObject = class end;",
        "function GetSchProperty(Obj: ISch_GraphicalObject; PropName: String): String;",
        "begin",
        "  if PropName = 'Location.X' then Result := '-250.5'",
        "  else if PropName = 'Location.Y' then Result := '9000'",
        "  else if PropName = 'Color' then Result := '1'",
        "  else if PropName = 'Text' then Result := '1.50'",
        "  else if PropName = 'Designator' then Result := '01'",
        "  else if PropName = 'Name' then Result := '9000'",
        "  else if PropName = 'Vertex2.X' then Result := '100'",
        "  else if PropName = 'Orientation' then Result := '0'",
        "  else Result := '';",
        "end;",
        "function GetPCBProperty(Obj: IPCB_Primitive; PropName: String): String;",
        "begin",
        "  if PropName = 'Rotation' then Result := '90'",
        "  else if PropName = 'UnionIndex' then Result := '3'",
        "  else if PropName = 'X' then Result := '100.5'",
        "  else if PropName = 'Name' then Result := '7'",
        "  else Result := '';",
        "end;",
        "function UnsupportedSchProperty(Obj: ISch_GraphicalObject; SetStr: String): String;",
        "begin Result := ''; end;",
        "procedure NotePropertyDiag(Kind: String; PropName: String);",
        "begin end;",
        _routine("Utils.pas", "IsFloatStr"),
        _routine("Utils.pas", "StrToFloatDef"),
        _routine("Utils.pas", "FilterOpPos"),
        _routine("Utils.pas", "FilterOpText"),
        _routine("Utils.pas", "FilterWildMatch"),
        _routine("Utils.pas", "FilterNumbersEqual"),
        _routine("Utils.pas", "FilterOpHolds"),
        _routine("Generic.pas", "IsSchNumberProperty"),
        _routine("Generic.pas", "FilterValueMatchesSch"),
        _routine("Generic.pas", "MatchesFilter"),
        _routine("PCBGeneric.pas", "IsLengthProperty"),
        _routine("PCBGeneric.pas", "FilterValueMatchesPCB"),
        _routine("PCBGeneric.pas", "MatchesFilterPCB"),
        "var P: IPCB_Primitive; S: ISch_GraphicalObject;",
        "begin",
        f"  DecimalSeparator := '{separator}';",
        "  P := IPCB_Primitive.Create;",
        "  S := ISch_GraphicalObject.Create;",
        *checks,
        "end.",
    ])
    path = tmp_path / "numeric.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("numeric.exe" if os.name == "nt" else "numeric")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        call, filt, want = cases[ran.returncode - 1] if ran.returncode <= len(cases) \
            else ("?", "?", "?")
        pytest.fail(f"separator {separator!r}: {call}, {filt!r}) should "
                    f"{'match' if want else 'not match'} (exit {ran.returncode}) "
                    f"{ran.stdout}{ran.stderr}")
