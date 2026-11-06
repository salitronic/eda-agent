# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A parameter's ShowName, Autoposition and Locked tick are reachable.

MEASURED on AD 25.8 in a SchLib: obj_query / obj_modify read and wrote a
parameter's Text, Name, IsHidden, Location, Orientation and Justification,
but ShowName answered "unreadable" / "unknown" (the generic layer knew it
only for pins), and Autoposition and the Locked tick were unknown names.
Only the component's Designator / Comment labels had Autoposition, through
the component, so a parameter shown on a symbol could not be told to stay
put, show its name, or be locked in place.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
GENERIC = load(SCRIPTS / "Generic.pas")


def test_only_parameters_and_designators_carry_the_settings():
    body = GENERIC["SchHasParamDisplay"]
    assert "(Obj.ObjectId = eParameter) Or (Obj.ObjectId = eDesignator)" in body
    names = GENERIC["IsSchParamDisplayProp"]
    assert "'Autoposition'" in names and "'GraphicallyLocked'" in names
    assert "'ShowName'" not in names, "ShowName stays a pin setting for pins"


def test_each_setting_is_read_and_written_on_the_typed_parameter():
    read = GENERIC["ReadSchParamDisplayProp"]
    write = GENERIC["WriteSchParamDisplayProp"]
    assert "P : ISch_Parameter" in read and "P : ISch_Parameter" in write
    for prop in ("ShowName", "Autoposition", "GraphicallyLocked"):
        assert f"BoolToJsonStr(P.{prop})" in read, prop
        assert f"P.{prop} := Flag" in write, prop
    # Written inside the modify bracket, as every schematic write is.
    assert write.index("SchBeginModify(P)") < write.index("P.ShowName := Flag")
    assert write.index("P.GraphicallyLocked := Flag") < write.index("SchEndModify(P)")


def _branch(fn: str) -> str:
    body = GENERIC[fn]  # comments are stripped: anchor on code
    start = body.index("Else If ((PropName = 'ShowName') Or IsSchParamDisplayProp")
    return body[start:body.index("Else If IsSchPinProp(PropName)", start)]


def test_the_get_side_checks_the_type_before_it_reads():
    part = _branch("GetSchProperty")
    assert re.search(r"\(\(PropName = 'ShowName'\) Or IsSchParamDisplayProp\(PropName\)\)"
                     r"\s+And SchHasParamDisplay\(Obj\) Then\s+"
                     r"Result := ReadSchParamDisplayProp\(Obj, PropName\)", part)
    assert "NotePropertyDiag('unreadable', PropName)" in part


def test_the_set_side_checks_the_type_and_refuses_other_objects():
    part = _branch("SetSchProperty")
    assert re.search(r"\(\(PropName = 'ShowName'\) Or IsSchParamDisplayProp\(PropName\)\)"
                     r"\s+And SchHasParamDisplay\(Obj\) Then\s+"
                     r"WroteOK := WriteSchParamDisplayProp\(Obj, PropName, Value\)", part)
    assert re.search(r"Else If IsSchParamDisplayProp\(PropName\) Then\s+Matched := False",
                     part)


def test_a_pin_keeps_its_own_show_name():
    body = GENERIC["GetSchProperty"]
    # The parameter branch comes first but cannot catch a pin: the type test.
    assert body.index("SchHasParamDisplay(Obj)") < body.index("ReadSchPinProp(Obj, PropName)")
    assert "ePin" not in GENERIC["SchHasParamDisplay"]
