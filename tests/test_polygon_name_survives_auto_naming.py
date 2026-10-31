# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A polygon Name write turns Auto Naming off and reports what it holds.

A pour with Auto Naming on (AutoGenerateName) builds its name from layer,
net and index again, so a Name written through generic.modify_objects was
lost while the reply said matched 1, failed 0 (issue #150). The Polygon
Pour dialog unticks Auto Naming when a name is typed; the setter now does
the same before it writes, and answers with the name read back.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"


def _name_branch() -> str:
    body = load(_GENERIC, minimum=20)["PCBSetPoly"]
    start = body.index("If PropName = 'Name' Then")
    return body[start:body.index("Exit;", body.index("Poly.Name := Value;", start)) + 5]


def test_auto_naming_is_turned_off_before_the_name_is_written():
    branch = _name_branch()
    off = branch.index("Poly.AutoGenerateName := False;")
    assert off < branch.index("Poly.Name := Value;")


def test_the_write_answers_with_the_name_read_back():
    branch = _name_branch()
    assert "Result := Poly.Name = Value;" in branch
    assert "Result := True;" not in branch


def test_an_empty_name_is_still_refused_before_anything_is_written():
    branch = _name_branch()
    refuse = branch.index("If Trim(Value) = '' Then Exit;")
    assert refuse < branch.index("Poly.AutoGenerateName := False;")
