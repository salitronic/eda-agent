# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Placed parts and sheet symbols get their labels where Altium puts them.

MEASURED 2026-10-30 on AD 26.11.1.5: six sheet symbols placed by script had
their names and file names at the sheet origin, the bottom-left corner,
twelve texts in a pile; a part placed rotated kept its labels where the
library left them, and a person fixed it by rotating the part and back.
On a scratch sheet, a modify bracket and a move and back left the labels
where they were; a rotation by 90 and back put them where Altium does and
left the symbol's size and its entries alone.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools import generic

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(name: str) -> str:
    code = (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_the_trigger_is_the_rotation_and_its_reverse_in_one_modify():
    body = _routine("SchAutopositionLabels")
    begin = body.index("SchBeginModify(Obj)")
    turn = body.index("Obj.RotateBy90(Center, eRotate90)")
    back = body.index("Obj.RotateBy90(Center, eRotate270)")
    end = body.index("SchEndModify(Obj)")
    assert begin < turn < back < end
    # A placement must not take a hand-placed label off its place.
    assert re.search(r"If Force Then\s*Begin", body)


@pytest.mark.parametrize("routine,after", [
    ("PutLoadedComponent", "SchRegisterObject(SchDoc, Comp);"),
    ("Gen_PlaceSchComponentFromLibrary", "SchRegisterObject(SchDoc, Comp);"),
    ("Gen_PlaceSheetSymbol", "NMObj.SetState_Text(NameStr);"),
])
def test_every_placement_positions_the_labels_once_they_exist(routine, after):
    body = _routine(routine)
    call = body.index("SchAutopositionLabels(")
    assert body.index(after) < call
    assert "'rotate', False)" in body[call:call + 60], "a placement must not force"


def test_the_action_forces_and_reports_before_and_after():
    body = _routine("Gen_AutopositionLabels")
    assert "SchAutopositionLabels(Obj, Method, True)" in body
    assert body.index('"before":') < body.index('"after":')
    # Collected before anything moves; the list is never freed.
    assert body.index("Iterator_Destroy") < body.index("SchAutopositionLabels(")
    assert "Found.Free" not in body


def test_the_label_property_names_parse(tmp_path):
    fpc = shutil.which("fpc")
    if not fpc:
        pytest.skip("Free Pascal Compiler (fpc) is not installed or not on PATH")
    cases = [
        ("Designator.Location.X", "Designator", "Location.X"),
        ("SheetName.Location.Y", "Designator", "Location.Y"),
        ("SheetFileName.Autoposition", "Filename", "Autoposition"),
        ("Filename.IsHidden", "Filename", "IsHidden"),
        ("Comment.Location.X", "Comment", "Location.X"),
        ("Designator.Text", "", None),
        ("Location.X", "", None),
        ("Orientation", "", None),
    ]
    checks = []
    for i, (name, which, field) in enumerate(cases, 1):
        checks.append(f"  if SchLabelPropWhich('{name}') <> '{which}' then Halt({i});")
        if field:
            checks.append(f"  if SchLabelPropField('{name}') <> '{field}' then Halt({i});")
    program = "\n".join(["program labels;", "{$mode delphi}", "uses SysUtils;",
                         _routine("SchLabelPropWhich"), _routine("SchLabelPropField"),
                         "begin", *checks, "end."])
    src = tmp_path / "labels.pas"
    src.write_text(program, encoding="utf-8")
    built = subprocess.run([fpc, str(src)], cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    exe = tmp_path / ("labels.exe" if os.name == "nt" else "labels")
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    assert ran.returncode == 0, f"case {ran.returncode} failed: {cases[ran.returncode - 1]}"


def test_the_tool_sends_the_names_and_refuses_what_the_wire_cannot_carry(monkeypatch):
    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, params))
            return {"count": 0}

    monkeypatch.setattr(generic, "get_bridge", lambda: _Bridge())
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    generic.register_generic_tools(DummyMcp())
    tool = captured["sch_autoposition_labels"]
    asyncio.run(tool(names=["RAILS", " MCU "]))
    assert sent == [("generic.autoposition_labels", {"names": "RAILS|MCU", "method": "rotate"})]
    assert asyncio.run(tool(names=["A|B"]))["ok"] is False
    assert asyncio.run(tool(method="spin"))["ok"] is False
    assert len(sent) == 1
