# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""pcb_set_rule_properties writes gap_mils for the mask-sliver and silk rules.

MinimumSolderMaskSliver (53), SilkToSolderMaskClearance (54) and
SilkToSilkClearance (55) carry no Gap; each has its own SetState_/GetState_
pair. Before this they were SKIPPED, so a board kept Altium's 10 mil
defaults and a 0.5 mm-pitch footprint's mask dams failed DRC.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

_PAIRS = {
    53: "MinSolderMaskSliver",
    54: "SilkToMaskGap",
    55: "SilkToSilkClearance",
}


def _body():
    return load(_ALTIUM / "PCB.pas")["PCB_SetRuleProperties"]


def test_the_three_kinds_count_as_supported():
    body = _body()
    start = body.index("GapKindSupported := (Kind")
    clause = body[start:body.index(";", start)]
    for kind in _PAIRS:
        assert f"(Kind = {kind})" in clause


def test_each_kind_is_written_and_read_back_through_its_own_pair():
    body = _body()
    for kind, name in _PAIRS.items():
        match = body.index(f"RuleKind = {kind})")
        branch = body[match:match + 600]
        before = branch.index(f"GetState_{name};")
        write = branch.index(f"SetState_{name}(GapWanted)")
        after = branch.index(f"GetState_{name};", write)
        assert before < write < after


def test_only_a_read_back_match_counts():
    body = _body()
    start = body.index("((Kind = 53) Or (Kind = 54) Or (Kind = 55)) Then")
    block = body[start:body.index("MarkDocDirtyByPath", start)]
    assert "GapVerified := Found And (GapAfter = GapWanted);" in block
    assert "If GapVerified Then Inc(UpdatedCount);" in block


def test_the_clearance_iterator_is_not_aimed_at_the_new_kinds():
    """Gap on IPCB_ClearanceConstraint is not what these rules hold."""
    body = _body()
    start = body.index("RuleClearIter := Iter.FirstPCBObject;")
    guard = body[body.rindex("If ((GapStr", 0, start):start]
    for kind in _PAIRS:
        assert f"(Kind = {kind})" not in guard


def test_no_unknown_erule_names():
    """An identifier DelphiScript does not know halts the polling loop."""
    body = _body()
    for name in ("eRule_MinimumSolderMaskSliver", "eRule_SilkToSolderMaskClearance",
                 "eRule_SilkToSilkClearance"):
        assert name not in body
