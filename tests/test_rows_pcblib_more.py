# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""rows kinds pcblib/arc and pcblib/text (RowsPcbLibMore.pas).

The two kinds share one read and one write that are told the kind by name;
the field tables, value checks, the arc sweep and the special-string rule
for texts are pure and run under Free Pascal with the core routines they
use. The walks need Altium; their shape is checked in the source: the read
narrows by object kind inside Altium, the writer finds and guards every row
before it writes, writes inside one edit, reads back what it wrote, and
never adds or removes an object.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
UNIT = "RowsPcbLibMore.pas"


def _code(unit: str) -> str:
    return (ALTIUM / unit).read_text(encoding="utf-8", errors="replace")


def _routine(unit: str, name: str) -> str:
    code = _code(unit)
    start = re.search(rf"(?m)^Function {name}\b", code)
    assert start, f"missing {name} in {unit}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:''|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body, flags=re.S)


# --- the unit's shape ---------------------------------------------------------

def test_the_unit_is_crlf_and_stamped():
    raw = (ALTIUM / UNIT).read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert "Function UnitStamp_RowsPcbLibMore(Dummy : Integer) : String;" in _code(UNIT)


def test_every_routine_takes_a_parameter():
    for m in re.finditer(r"(?m)^Function (\w+)\s*(\(|:)", _code(UNIT)):
        assert m[2] == "(", f"{m[1]} takes no parameter"


def test_the_unit_adds_or_removes_nothing():
    code = _code(UNIT)
    for call in ("AddPCBObject", "RemovePCBObject", "DestroyPCBObject",
                 "PCBObjectFactory", "RemoveSchObject", "AddSchObject"):
        assert call not in code


def test_no_getter_case_reads_as_a_bridge_command():
    # tests/test_bridge_handlers_reachable.py takes any 'x': Result := F( as one
    assert not re.search(r"'\w+':\s*Result\s*:=", _code(UNIT))


def test_the_entry_points_route_to_the_shared_walks():
    for kind, cap in (("arc", "Arc"), ("text", "Text")):
        read = _routine(UNIT, f"RowsPcbLib{cap}Read")
        write = _routine(UNIT, f"RowsPcbLib{cap}Write")
        assert f"RowsPcbLibMoreRead(Params, RequestId, '{kind}');" in read
        assert f"RowsPcbLibMoreWrite(Params, RequestId, '{kind}');" in write


def test_the_read_narrows_inside_altium():
    body = _routine(UNIT, "RowsPcbLibMoreRead")
    assert "GrpIter.AddFilter_ObjectSet(MkSet(eArcObject))" in body
    assert "GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));" in body
    assert "FilterWildMatch(FpName, FpPattern)" in body
    assert "If LayerStr <> '' Then Keep := Prim.Layer = LayerId;" in body
    assert body.index("RowsCompileWhere") < body.index("LibraryIterator_Create")
    assert "Types := RowsFieldTypes(Spec, Fields);" in body
    assert "RowsPart(Conds" not in body
    assert "CF := RowsColumn(Conds, 0);" in body
    assert "RowsReadReply('pcblib', Kind," in body
    assert "More := True;" in body


def test_the_writer_guards_every_row_before_it_writes():
    body = _routine(UNIT, "RowsPcbLibMoreWrite")
    assert "RowsTakeRows(Spec, Params, 'footprint'," in body
    assert "GrpIter.AddFilter_ObjectSet(MkSet(eArcObject))" in body
    assert "GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));" in body
    assert "ValueProblem" not in body                  # rows.py checks the values
    assert body.index("ROWS_REFUSED") < body.index("PcbBeginEdit(0)")
    # the special-string refusal sits in pass 1, before the edit opens
    pass1 = body.index("If Pass = 1 Then")
    assert pass1 < body.index("RowsTextWriteProblem(Current, Value)") < body.index(
        "Else If Done.IndexOf(Addr) >= 0 Then")
    assert "If Pass = 2 Then PcbEndEdit(0);" in body
    repeat = body.index("Else If Done.IndexOf(Addr) >= 0 Then")
    assert body.index("Inc(Repeats)", repeat) - repeat < 120
    # robots bracket round each object, refresh after, read back every field
    begin = body.index("PCBM_BeginModify")
    assert begin < body.index("RowsPcbLibMoreSet(Prim") < body.index("PCBM_EndModify")
    assert body.index("PCBM_EndModify") < body.index("RowsPcbLibMoreRefresh(Prim, Kind);")
    assert "' reads ' + Have + ' after writing '" in body


def test_the_setters_write_only_writable_fields():
    for kind, spec_fn, set_fn in (("arc", "RowsPcbLibArcSpec", "RowsPcbLibArcSet"),
                                  ("text", "RowsPcbLibTextSpec", "RowsPcbLibTextSet")):
        spec = re.findall(r"'([^']*)'", _routine(UNIT, spec_fn))
        writable = {e.split(":")[0] for e in "".join(spec).split("|") if e.endswith(":w")}
        cases = set(re.findall(r"'(\w+)':", _routine(UNIT, set_fn)))
        assert cases == writable, kind


def test_the_getters_answer_every_field():
    for spec_fn, get_fn in (("RowsPcbLibArcSpec", "RowsPcbLibArcGet"),
                            ("RowsPcbLibTextSpec", "RowsPcbLibTextGet")):
        spec = re.findall(r"'([^']*)'", _routine(UNIT, spec_fn))
        names = {e.split(":")[0] for e in "".join(spec).split("|")}
        cases = set(re.findall(r"'(\w+)':", _routine(UNIT, get_fn)))
        assert cases == names, get_fn


def test_texts_read_the_stored_string_and_write_through_text():
    get = _routine(UNIT, "RowsPcbLibTextGet")
    assert "'text':          V := Txt.UnderlyingString;" in get
    assert "'shown':         V := Txt.Text;" in get
    assert "'text':         Txt.Text := Value;" in _routine(UNIT, "RowsPcbLibTextSet")


# --- the pure routines under Free Pascal ---------------------------------------

_LIFTED = [
    ("Utils.pas", "IsFloatStr"), ("Utils.pas", "StrToFloatDef"),
    ("Utils.pas", "BoolToJsonStr"), ("Utils.pas", "FloatToJsonStr"),
    ("RowsCore.pas", "RowsIntStr"), ("RowsCore.pas", "RowsIntOf"),
    ("RowsCore.pas", "RowsAngle"), ("RowsCore.pas", "RowsFieldAt"),
    ("RowsCore.pas", "RowsFieldType"), ("RowsCore.pas", "RowsFieldWritable"),
    (UNIT, "RowsPcbLibArcSpec"), (UNIT, "RowsPcbLibTextSpec"),
    (UNIT, "RowsPcbLibMoreSpec"), ("RowsCore.pas", "RowsArcSweep"), (UNIT, "RowsArcLength"),
    (UNIT, "RowsTextIsSpecial"), (UNIT, "RowsTextWriteProblem"),
]

CHECKS = [
    # tables: id first, context, the object's own; what is writable
    "Pos('id:s:r|footprint:s:r|description:s:r|layer:s:w|layer_name:s:r|', RowsPcbLibMoreSpec('arc')) = 1",
    "Pos('id:s:r|footprint:s:r|description:s:r|layer:s:w|layer_name:s:r|', RowsPcbLibMoreSpec('text')) = 1",
    "RowsPcbLibMoreSpec('pad') = ''",
    "RowsFieldWritable(RowsPcbLibArcSpec(0), 'start_angle')",
    "RowsFieldWritable(RowsPcbLibArcSpec(0), 'radius')",
    "not RowsFieldWritable(RowsPcbLibArcSpec(0), 'sweep')",
    "not RowsFieldWritable(RowsPcbLibArcSpec(0), 'length')",
    "RowsFieldType(RowsPcbLibArcSpec(0), 'width') = 'l'",
    "RowsFieldType(RowsPcbLibArcSpec(0), 'start_angle') = 'a'",
    "Pos(':n:', RowsPcbLibArcSpec(0) + RowsPcbLibTextSpec(0)) = 0",
    "RowsFieldWritable(RowsPcbLibTextSpec(0), 'height')",
    "RowsFieldWritable(RowsPcbLibTextSpec(0), 'mirrored')",
    "RowsFieldType(RowsPcbLibTextSpec(0), 'mirrored') = 'b'",
    "RowsFieldWritable(RowsPcbLibTextSpec(0), 'text')",
    "not RowsFieldWritable(RowsPcbLibTextSpec(0), 'shown')",
    "not RowsFieldWritable(RowsPcbLibTextSpec(0), 'is_designator')",
    "not RowsFieldWritable(RowsPcbLibTextSpec(0), 'text_kind')",
    "RowsFieldType(RowsPcbLibTextSpec(0), 'x') = 'l'",
    # sweep and length
    "Abs(RowsArcSweep(0, 90) - 90) < 1e-9",
    "Abs(RowsArcSweep(350, 10) - 20) < 1e-9",
    "Abs(RowsArcSweep(0, 360) - 360) < 1e-9",
    "Abs(RowsArcSweep(45, 45) - 360) < 1e-9",
    "Abs(RowsArcSweep(270, 90) - 180) < 1e-9",
    "Abs(RowsArcLength(10, 0, 180) - 31.4159265358979) < 1e-9",
    "RowsAngle(89.99999999) = '900000'",
    # value checks
    # special strings
    "RowsTextIsSpecial('.Designator')",
    "RowsTextIsSpecial(' .Comment')",
    "not RowsTextIsSpecial('U1')",
    "not RowsTextIsSpecial('')",
    "RowsTextWriteProblem('.Designator', 'U1') <> ''",
    "RowsTextWriteProblem('.Designator', '.Comment') = ''",
    "RowsTextWriteProblem('PIN1', 'A1') = ''",
]

_ESCAPE = r"""
function EscapeJsonString(S: String): String;
begin
  Result := S;
end;
""".rstrip()


def test_the_pure_routines_under_free_pascal(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    checks = [f"  if not ({c}) then Halt({i});" for i, c in enumerate(CHECKS, 1)]
    program = "\n".join([
        "program rowsmore;", "{$mode delphi}", "uses SysUtils, Classes;",
        _ESCAPE,
        *[_routine(u, n) for u, n in _LIFTED],
        "begin", *checks, "end.",
    ])
    path = tmp_path / "rowsmore.pas"
    path.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(path)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout[-4000:] + built.stderr
    exe = tmp_path / ("rowsmore.exe" if os.name == "nt" else "rowsmore")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    if ran.returncode:
        n = ran.returncode
        pytest.fail(f"check {n} failed: {CHECKS[n - 1] if n <= len(CHECKS) else '?'}")
