# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""UseTTFonts is written on board text by the script.

obj_query read it and obj_modify refused it as unknown; TextKind=1 did
the job. A rewrite to TextKind in the tool layer missed every spelling of
the type but eTextObject, and turned UseTTFonts=false into TextKind=0,
which also made a barcode a stroke text. The script reads each text's
own kind: true makes it TrueType, false makes a TrueType text stroke and
leaves a barcode alone. Any other value is refused, any other type is
not matched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"


def _generic(name):
    return load(_GENERIC, minimum=20)[name]


def _fpc():
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    return fpc


def _switch(tmp_path, cases):
    """Each (kind, use_tt, value) through the real routine: the result,
    and the kind and flag the text is left with."""
    lines = [
        "program usettf;", "{$mode delphi}", "uses SysUtils;",
        "type TText = class public TextKind: Integer; UseTTFonts: Boolean; end;",
        "IPCB_Text = TText;",
        _generic("SetTextUseTTFonts"),
        "procedure Check(Kind: Integer; TT: Boolean; Value: String);",
        "var T: TText; R: Boolean;",
        "begin T := TText.Create; T.TextKind := Kind; T.UseTTFonts := TT;",
        "  R := SetTextUseTTFonts(T, Value);",
        "  WriteLn(Ord(R), ' ', T.TextKind, ' ', Ord(T.UseTTFonts)); T.Free; end;",
        "begin",
    ]
    lines += [f"  Check({k}, {'True' if tt else 'False'}, '{v}');" for k, tt, v in cases]
    lines.append("end.")
    path = tmp_path / "usettf.pas"
    path.write_text("\n".join(lines), encoding="utf-8")
    built = subprocess.run([_fpc(), str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("usettf.exe" if os.name == "nt" else "usettf")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0
    return [tuple(int(x) for x in line.split()) for line in ran.stdout.strip().splitlines()]


def test_true_makes_any_text_truetype(tmp_path):
    # Stroke, barcode and TrueType alike; the case of the word is free.
    assert _switch(tmp_path, [(0, False, "true"), (2, False, "TRUE"), (1, True, "true")]) \
        == [(1, 1, 1), (1, 1, 1), (1, 1, 1)]


def test_false_makes_truetype_stroke_and_leaves_a_barcode(tmp_path):
    assert _switch(tmp_path, [(1, True, "false"), (2, False, "false"), (0, False, "False")]) \
        == [(1, 0, 0), (1, 2, 0), (1, 0, 0)]


def test_a_value_that_is_not_a_boolean_is_refused_and_nothing_written(tmp_path):
    assert _switch(tmp_path, [(0, False, "1"), (1, True, "yes"), (2, False, "")]) \
        == [(0, 0, 0), (0, 1, 1), (0, 2, 0)]


def test_the_setter_takes_it_on_text_only_and_refuses_a_bad_value():
    body = _generic("SetPCBProperty")
    branch = body[body.index("Else If PropName = 'UseTTFonts' Then"):]
    branch = branch[:branch.index("Else If", 10)]
    assert "If Oid <> eTextObject Then" in branch
    assert "Matched := False" in branch
    assert "If Not SetTextUseTTFonts(Txt, Value) Then Refused := True;" in branch
