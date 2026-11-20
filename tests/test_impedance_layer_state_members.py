# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""An impedance profile layer's width and gap limits go through state methods.

On AD 25 the script engine has no ``TraceMaxWidth`` property on
IPCB_TraceImpedance, only ``GetState_TraceMaxWidth`` /
``SetState_TraceMaxWidth`` (AD 26 has the property as well), and
``DiffPairMinGap`` / ``DiffPairMaxGap`` are not properties on either. An
undeclared member halts the script past any Try, and the reply is built
after the edit is made, so a property read there would leave the edit made
and the call reported as a crash.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_PCB = _DIR / "PCB.pas"

# Members of IPCB_TraceImpedance that AD 25 knows only as GetState_ / SetState_.
STATE_ONLY = ("TraceMaxWidth", "DiffPairMinGap", "DiffPairMaxGap")


@pytest.fixture(scope="module")
def fns():
    return load(_PCB)


@pytest.mark.parametrize("member", STATE_ONLY)
def test_no_unit_uses_the_member_as_a_property(member):
    bare = re.compile(r"\." + member + r"\b")
    for pas in sorted(_DIR.glob("*.pas")):
        text = strip_comments(pas.read_text(encoding="utf-8", errors="replace"))
        hits = [line.strip() for line in text.splitlines() if bare.search(line)]
        assert not hits, f"{pas.name} uses .{member} as a property: {hits}"


def test_the_reply_reads_the_max_width_through_its_getter(fns):
    body = fns["ImpTraceJson"]
    assert ("'max_width_mils', FloatToJsonStr(CoordToMilsF("
            "T.GetState_TraceMaxWidth))") in body


def test_the_max_width_is_written_and_read_back_through_state_methods(fns):
    body = fns["PCB_ImpedanceProfileLayer"]
    at = body.index("ExtractJsonValue(Params, 'max_width_mils')")
    line = body[at:].splitlines()[1]
    assert "T.SetState_TraceMaxWidth(MilsToCoordF(StrToFloatDef(V, 0)))" in line
    assert "ImpMilsTook(T.GetState_TraceMaxWidth, V)" in line
