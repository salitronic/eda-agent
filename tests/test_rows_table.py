# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""The table file: rows.py hands a write over as the flat table, already split.

RowsParseFlat split the rows text in the script interpreter (2.5 s of a
1000-pad write, stage profile 2026-11-11). rows.py now writes the table the
bridge would have built -- keys, values, row ranges -- and RowsTableTake
loads it with native list calls. The table it loads must be the table the
old parse built from the same rows, and a table that is not one rows.py
wrote must be refused, never half taken.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from eda_agent.tools import rows as rows_tools
from tests.test_rows_api import _routine

SPEC = "id:s:r|footprint:s:r|layer:s:w|x1:l:w|width:l:w|keepout:b:r|text:s:w"

_LIFTED = [
    ("RowsCore.pas", n) for n in (
        "RowsIntStr", "RowsPointListOk", "RowsTypeWord", "RowsFieldAt", "RowsFieldType", "RowsFieldWritable",
        "RowsSpecNames", "RowsSpecNameList", "RowsTypeProblem", "RowsSplit",
        "RowsFlatAdd", "RowsFlatValue", "RowsParseFlat",
        "RowsTableCut", "RowsDigitLines", "RowsTableTake")]

_ALTIUM = r"""
{ Altium's StringReplace takes its flags as an integer (-1 = all). }
function StringReplace(S, OldP, NewP: String; Flags: Integer): String; overload;
begin
  Result := SysUtils.StringReplace(S, OldP, NewP, [rfReplaceAll]);
end;
""".rstrip()

_HELPERS = r"""

function Dump(Ids, RS, RE, K, V, T: TStringList): String;
var I, F: Integer;
begin
  Result := '';
  for I := 0 to Ids.Count - 1 do
  begin
    Result := Result + Ids[I] + ':';
    for F := StrToInt(RS[I]) to StrToInt(RE[I]) - 1 do
      Result := Result + K[F] + '=' + V[F] + '/' + T[F] + ';';
    Result := Result + '|';
  end;
end;

{ '' when the table and the old parse give the same flat table, else both. }
function Same(Old, Tbl: String): String;
var A, B: array[0..5] of TStringList; Tx: TStringList; C1, C2, P1, P2, D1, D2: String;
    I: Integer;
begin
  for I := 0 to 5 do begin A[I] := TStringList.Create; B[I] := TStringList.Create; end;
  Tx := TStringList.Create;
  P1 := RowsParseFlat(SPEC, Old, 'footprint', A[0], Tx, A[1], A[2], A[3], A[4], A[5], C1);
  Tx.Clear;
  P2 := RowsTableTake(SPEC, Tbl, B[0], Tx, B[1], B[2], B[3], B[4], B[5], C2);
  D1 := Dump(A[0], A[1], A[2], A[3], A[4], A[5]);
  D2 := Dump(B[0], B[1], B[2], B[3], B[4], B[5]);
  if (P1 <> '') or (P2 <> '') then Result := 'PROBLEM ' + P1 + ' / ' + P2
  else if D1 <> D2 then Result := D1 + ' <> ' + D2
  else Result := '';
  for I := 0 to 5 do begin A[I].Free; B[I].Free; end;
  Tx.Free;
end;

function TakeCode(Tbl: String): String;
var L: array[0..6] of TStringList; P: String; I: Integer;
begin
  for I := 0 to 6 do L[I] := TStringList.Create;
  P := RowsTableTake(SPEC, Tbl, L[0], L[1], L[2], L[3], L[4], L[5], L[6], Result);
  if (P = '') <> (Result = '') then Result := 'INCONSISTENT';
  for I := 0 to 6 do L[I].Free;
end;

function ValOf(Tbl: String; F: Integer): String;
var L: array[0..6] of TStringList; C: String; I: Integer;
begin
  for I := 0 to 6 do L[I] := TStringList.Create;
  if RowsTableTake(SPEC, Tbl, L[0], L[1], L[2], L[3], L[4], L[5], L[6], C) = '' then
    Result := L[5][F]
  else
    Result := 'REFUSED ' + C;
  for I := 0 to 6 do L[I].Free;
end;
"""


def _pas(text: str) -> str:
    """A Python string as a Pascal expression: quoted runs and #n characters."""
    parts, run = [], ""
    for ch in text:
        if 32 <= ord(ch) < 127 and len(run) < 200:
            run += ch
            continue
        if run:
            parts.append("'" + run.replace("'", "''") + "'")
            run = ""
        if 32 <= ord(ch) < 127:
            run = ch
        else:
            parts.append(f"#{ord(ch)}")
    if run:
        parts.append("'" + run.replace("'", "''") + "'")
    return "+".join(parts) or "''"


def _table(rows):
    return rows_tools.table_text(rows)


ROWS = [
    {"id": "30", "?footprint": "A", "width": "1500"},
    {"id": "4", "?FOOTPRINT": "A", "?width": "1500", "Width": "2000", "layer": "TopLayer"},
    {"id": "100", "?footprint": "B", "text": "", "x1": "-7"},
    {"id": "12", "?footprint": "A", "text": "A;B|C=D", "?keepout": "true", "x1": "0"},
]


def _swap(text, old, new):
    assert text.count(old) >= 1, old
    return text.replace(old, new, 1)


def _checks():
    good = _table(ROWS)
    one = _table([ROWS[0]])
    checks = [
        # the same flat table as the old parse, row ranges, types and all
        f"Same({_pas(rows_tools.join_rows(ROWS))}, {_pas(good)}) = ''",
        f"Same({_pas(rows_tools.join_rows([ROWS[2]]))}, {_pas(_table([ROWS[2]]))}) = ''",
        f"TakeCode({_pas(good)}) = ''",
        # a value with line breaks travels swapped and comes back whole
        f"ValOf({_pas(_table([{'id': '1', '?footprint': 'A', 'text': 'L1' + chr(13) + chr(10) + 'L2'}]))}, 2)"
        f" = 'L1'+#13#10+'L2'",
        # an empty value at the very end survives
        f"ValOf({_pas(_table([{'id': '1', '?footprint': 'A', 'text': ''}]))}, 2) = ''",
        # refusals: never half taken
        f"TakeCode({_pas(_table([ROWS[0], ROWS[0]]))}) = 'BAD_ROWS'",           # id twice
        f"TakeCode({_pas(_table([{'id': '1', '?footprint': 'A', 'colour': '1'}]))}) = 'BAD_FIELD'",
        f"TakeCode({_pas(_table([{'id': '1', '?footprint': 'A', 'footprint': 'B'}]))}) "
        "= 'READ_ONLY_FIELD'",
        f"TakeCode({_pas(_swap(one, 'ROWSTABLE 1', 'ROWSTABLE 2'))}) = 'BAD_ROWS'",
        f"TakeCode({_pas(_swap(one, chr(13) + chr(10) + '1' + chr(13) + chr(10), chr(13) + chr(10) + '2' + chr(13) + chr(10)))}) = 'BAD_ROWS'",   # count
        f"TakeCode({_pas(_swap(one, chr(1) + chr(13) + chr(10) + '0' + chr(13), chr(1) + chr(13) + chr(10) + 'x' + chr(13)))}) = 'BAD_ROWS'",   # start
        f"TakeCode({_pas(_swap(one, chr(1) + chr(13) + chr(10) + '0' + chr(13), chr(1) + chr(13) + chr(10) + '1' + chr(13)))}) = 'BAD_ROWS'",   # chain
        f"TakeCode({_pas(_swap(one, chr(1) + chr(13) + chr(10) + '3' + chr(13), chr(1) + chr(13) + chr(10) + '99999999999' + chr(13)))}) = 'BAD_ROWS'",
        f"TakeCode({_pas(_swap(one, '?footprint' + chr(13) + chr(10) + 'width' + chr(13) + chr(10) + chr(1), '?footprint' + chr(13) + chr(10) + chr(1)))}) = 'BAD_ROWS'",  # a key unnamed
        f"TakeCode({_pas(_swap(one, chr(1) + chr(13) + chr(10) + '3' + chr(13), chr(1) + chr(13) + chr(10) + '2' + chr(13)))}) = 'BAD_ROWS'",   # end short of M
        f"TakeCode({_pas(_swap(one, 'ROWSTABLE 1' + chr(13) + chr(10) + '1', 'ROWSTABLE 1' + chr(13) + chr(10) + '99999999999'))}) = 'BAD_ROWS'",  # never to StrToInt
        "TakeCode('') = 'BAD_ROWS'",
    ]
    return checks


def test_the_table_file_is_the_old_parse_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks = _checks()
    lines = [f"  if not ({c}) then Halt({i});" for i, c in enumerate(checks, 1)]
    program = "\n".join([
        "program rowstable;", "{$mode delphi}{$H+}", "uses SysUtils, Classes;",
        _ALTIUM, f"const SPEC = '{SPEC}';",
        *[_routine(u, n) for u, n in _LIFTED],
        _HELPERS, "begin", *lines, "end."])
    path = tmp_path / "rowstable.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowstable.exe" if os.name == "nt" else "rowstable")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {checks[n - 1][:300] if n <= len(checks) else '?'}")


def test_the_table_text_rows_py_writes():
    text = _table([{"id": "7", "?Footprint": " X ", "width": "100"},
                   {"id": "8", "?footprint": "X", "text": "a\r\nb"}])
    secs = [s.split("\r\n") for s in text[:-2].split("\r\n\x01\r\n")]
    assert secs[0] == ["ROWSTABLE 1", "2", "6", "1"]
    assert secs[1] == ["id", "?footprint", "width", "text"]     # distinct, first seen
    assert secs[2:5] == [["7", "8"], ["0", "3"], ["3", "6"]]
    assert secs[5] == ["id", "?footprint", "width", "id", "?footprint", "text"]
    assert secs[6] == ["7", "X", "100", "8", "X", "a\x10\x11b"]
    # the id first in every row, whatever order the caller gave
    assert _table([{"width": "1", "id": "9"}]).split("\r\n\x01\r\n")[5] == "id\r\nwidth"


@pytest.mark.parametrize("rows", [
    [{"id": "1", "width": "1", "WIDTH": "2"}],          # a key twice
    [{"id": "1", "text": "a\x01b"}],                    # a reserved character
    [{"id": "1\n2", "width": "1"}],                     # a line break in an id
    [{"id": "", "width": "1"}],
])
def test_a_row_the_table_cannot_carry_is_refused(rows):
    with pytest.raises(ValueError):
        _table(rows)
