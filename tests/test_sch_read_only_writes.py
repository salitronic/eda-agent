# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Schematic writes Altium does not keep.

MEASURED 2026-11-08 on AD 25.8.1, on a scratch copy of a sheet:
- A port's child parameter CrossRef (the sheets the port's net reaches,
  written by the compiler) reads NameIsReadOnly and ValueIsReadOnly true.
  obj_modify Text=... on it landed anyway; Name=... did nothing and was
  reported as written.
- A port's Style took 1, 2, 3, 5, 6 and 7 and read them back, but after a
  save and reopen they were 0 or 4 again: Style is only the port's axis.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _source() -> str:
    return (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def _branch(body: str, prop: str) -> str:
    """The setter's branch for one property name, up to the next name."""
    start = body.index(f"Else If PropName = '{prop}'")
    rest = body[start + 10:]
    nxt = re.search(r"\n\s*Else If PropName = '", rest)
    return body[start:start + 10 + (nxt.start() if nxt else len(rest))]


def test_altium_flags_decide_which_parameter_fields_are_kept():
    body = _routine(_source(), "SchParamKeptByAltium")
    assert "If Obj.ObjectId <> eParameter Then Exit;" in body
    assert "Par : ISch_Parameter;" in body
    assert body.index("Par := Obj;") < body.index("Par.ValueIsReadOnly")
    assert "If ForValue Then Result := Par.ValueIsReadOnly" in body
    assert "Else Result := Par.NameIsReadOnly;" in body


def test_text_and_name_writes_honour_the_flags_before_writing():
    setter = _routine(_source(), "SetSchProperty")
    for prop, flag in (("Text", "True"), ("Name", "False")):
        branch = _branch(setter, prop)
        # The type guard first (the bridge stops on a member a type lacks).
        assert branch.index(f"If SchObjectHas{prop}(Obj) Then") < branch.index("SchParamKeptByAltium")
        guard = branch.index(f"SchParamKeptByAltium(Obj, {flag})")
        refused = branch.index(f"WroteOK := NoteKeptByAltium({flag})", guard)
        assert guard < refused < branch.index(f"Obj.{prop} := Value")
    # The note says why, and answers False so the write is listed as failed.
    note = _routine(_source(), "NoteKeptByAltium")
    assert "Result := False;" in note and note.count("NoteNextStep(") == 2


def test_port_style_takes_only_its_axis():
    code = _source()
    kept = _routine(code, "PortStyleKept")
    assert "Result := (Value = '0') Or (Value = '4');" in kept
    branch = _branch(_routine(code, "SetSchProperty"), "Style")
    guard = branch.index("(Obj.ObjectId = ePort) And (Not PortStyleKept(Value))")
    assert guard < branch.index("WroteOK := False") < branch.index("Obj.Style :=")
