# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guard STEP body positioning against unsupported AD21 members.

Native mutation checks belong on disposable documents only. These guards
also mutate the recorded defects back into the source in memory, so an
assertion which cannot detect its target defect cannot pass unnoticed.
"""
from pathlib import Path
import re

import pytest

from tests.pascal_source import load

ROOT = Path(__file__).resolve().parents[1]


def _pcb():
    return load(ROOT / "scripts/altium/PCB.pas")


def _safe_placement(body):
    assert not re.search(r"\bBody\.[xy]\b", body, re.I)
    assert "BeforeBox := Body.BoundingRectangle" in body
    assert "AfterBox := Body.BoundingRectangle" in body
    assert "JsonBool('position_verified', PositionOk)" in body
    assert "Abs(ReadBackX - BodyX)" in body
    assert "Abs(ReadBackY - BodyY)" in body
    assert "AfterBox.Right - BeforeBox.Right" in body
    assert "AfterBox.Top - BeforeBox.Top" in body


def test_body_placement_never_probes_the_missing_xy_members():
    body = _pcb()["PCB_Place3DBody"]
    _safe_placement(body)
    with pytest.raises(AssertionError):
        _safe_placement(body.replace("BeforeBox := Body.BoundingRectangle;",
                                     "CurX := Body.x;"))
    generic = load(ROOT / "scripts/altium/PCBGeneric.pas")["PCBPrimitivePos"]
    for m in re.finditer(r"\bBody\.[xy]\b", generic, re.I):
        before = generic[:m.start()]
        assert before.rfind("If AltiumVersionCode(0) >= 2500 Then") > \
            before.rfind("Else If Oid ="), "a body x/y read outside the version gate"


def test_step_standoff_is_set_on_model_before_binding():
    body = _pcb()["PCB_Place3DBody"]
    assert "Body.StandoffHeight :=" not in body
    assert body.index("Model.SetState(0, 0, 0, MilsToCoordF(Standoff))") < body.index("Body.Model := Model")
    move = body.index("Body.MoveByXY(MilsToCoordF(BodyX)")
    assert body.index("Board.AddPCBObject(Body)") < body.index("Body.BeginModify") < move
    assert move < body.index("Body.EndModify") < body.index("PcbEndEdit(0)")
