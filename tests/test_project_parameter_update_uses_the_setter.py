# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Changing an existing project parameter goes through DM_SetValue, and
setting one writes the project file.

MEASURED on AD 25.8.1: proj_set_parameter on a parameter the project
already had stopped the script at ``Param.DM_Value := ParamValue``. DM_Value
is a getter on the DM parameter interface; assigning it raised a blank
"Error" box that the request loop's Try/Except never saw, so the loop
stopped and the box blocked every session until a person pressed OK.
DM_SetValue is the setter (an independent Prj-Parameters.pas script uses
it). The DM_ interfaces expose getters and Set methods, never writable
properties, so no DM_ member is assigned anywhere in the bridge.

Also measured: a NEW parameter read back fine but was not in the .PrjPcb.
Adding a parameter does not mark the project modified, and the handler
saved only modified documents, so the project file was skipped while every
other modified document of the project was written. SaveProjectFile saves
that file alone.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load, strip_comments

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _set_parameter() -> str:
    return load(_ALTIUM / "Project.pas")["Proj_SetParameter"]


def test_an_existing_parameter_is_changed_through_the_setter():
    body = _set_parameter()
    assert "Param.DM_SetValue(ParamValue);" in body


def test_dm_value_is_never_assigned():
    assert not re.search(r"DM_Value\s*:=", _set_parameter())


def test_no_dm_member_is_assigned_anywhere():
    found = []
    for path in sorted(_ALTIUM.glob("*.pas")):
        text = strip_comments(path.read_text(encoding="utf-8-sig"))
        for line in text.splitlines():
            if re.search(r"\.DM_\w+\s*:=", line):
                found.append(f"{path.name}: {line.strip()}")
    assert not found, ("a DM_ member is assigned; the DM_ interfaces have no "
                       "writable properties, use its DM_Set... method: "
                       + "; ".join(found))


def test_the_project_file_itself_is_saved():
    body = _set_parameter()
    assert "FileSaved := SaveProjectFile(Project);" in body
    assert "SaveProjectMembers" not in body, (
        "setting a project parameter must not save the project's other documents")


def test_the_reply_says_whether_the_file_was_written():
    body = _set_parameter()
    assert "',\"project_file_saved\":' + BoolToJsonStr(FileSaved)" in body
