# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Matched Lengths rules: made, changed and read back.

pcb_create_design_rule could not make a Matched Lengths rule (Altium's
High Speed > Matched Lengths), and pcb_set_rule_properties /
pcb_get_rule_properties did not know its fields, so the rule that says
"these nets must match each other" could only be set in the Rules and
Constraints Editor. RuleExtrasMatched writes the tolerance and the mode
through IPCB_MatchedNetLengthsConstraint and reports what the rule holds
afterwards; create, set and get all go through it.

MEASURED 2026-11-15 on AD 25.8 (a scratch board): the four
check flags are one two-way choice. CheckNetsInDiffPair on turns the
other three off; off turns all three on; a write to one of the other
three changes nothing. Hence one mode, written through that flag alone.
Issue #381.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import pcb as pcb_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

#: check flag on IPCB_MatchedNetLengthsConstraint -> name in the reply
_CHECKS = {
    "CheckNetsInDiffPair": "within_pair",
    "CheckDiffPairVsDiffPair": "pair_to_pair",
    "CheckBetweenXSignals": "xsignals",
    "CheckOtherElectricalObjects": "other_objects",
}


@pytest.fixture(scope="module")
def fns() -> dict[str, str]:
    return load(_PCB_PAS, minimum=100)


class _Recorder:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        if command in ("pcb.get_design_rules", "generic.query_objects"):
            return {"rules": [], "objects": []}
        self.sent.append((command, params or {}))
        return {"ok": True}


@pytest.fixture()
def tools(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: recorder)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    found = {t.name: t.fn for t in asyncio.run(registry.list_tools())}

    def run(tool, **kwargs):
        before = len(recorder.sent)
        out = asyncio.run(found[tool](**kwargs))
        return out, (recorder.sent[-1] if len(recorder.sent) > before else None)

    return run


def test_the_helper_narrows_to_the_matched_lengths_interface(fns):
    body = fns["RuleExtrasMatched"]
    decl = next(ln for ln in body.split("Begin", 1)[0].splitlines()
                if ln.strip().startswith("R :"))
    assert "IPCB_MatchedNetLengthsConstraint" in decl
    assert "R := Iter.FirstPCBObject;" in body
    assert "AddFilter_ObjectSet(MkSet(eRuleObject))" in body
    assert "(R.RuleKind = 4) And (R.Name = RuleName)" in body


def test_tolerance_and_mode_are_written_as_asked_and_read_back(fns):
    body = fns["RuleExtrasMatched"]
    assert "R.Tolerance := MilsToCoordF(StrToFloatDef(TolS, 0));" in body
    assert "If Not RuleMilsTook(R.Tolerance, TolS) Then Missed := Missed + '\"matched_tolerance_mils\",';" in body
    assert "R.CheckNetsInDiffPair := (ModeS = 'within_pair');" in body
    assert ("If R.CheckNetsInDiffPair <> (ModeS = 'within_pair') Then "
            "Missed := Missed + '\"matched_mode\",';") in body
    assert "ExtractJsonValue(Params, 'matched_tolerance_mils')" in body
    assert "ExtractJsonValue(Params, 'matched_mode')" in body


def test_only_the_deciding_flag_is_written(fns):
    """The other three follow it in Altium; writing them changes nothing."""
    body = fns["RuleExtrasMatched"]
    for prop in ("CheckDiffPairVsDiffPair", "CheckBetweenXSignals",
                 "CheckOtherElectricalObjects"):
        assert f"R.{prop} :=" not in body


def test_the_mode_and_every_check_are_reported(fns):
    body = fns["RuleExtrasMatched"]
    assert ("If R.CheckNetsInDiffPair Then ModeNow := 'within_pair' "
            "Else ModeNow := 'group';") in body
    assert '"matched_mode":"\' + ModeNow' in body
    for prop, key in _CHECKS.items():
        assert f'"{key}":\' + BoolToJsonStr(R.{prop})' in body
    assert '"matched_use_delay_units":' in body


def test_create_makes_kind_four_and_reports_the_settings(fns):
    create = fns["PCB_CreateDesignRule"]
    assert "PCBRuleFactory(4)" in create
    branch = create.split("RuleTypeStr = 'matched_lengths' Then", 1)[1]
    assert "Rule.Scope1Expression := ScopeStr" in branch.split("End", 1)[0]
    after = create.split("Board.AddPCBObject(Rule)", 1)[1]
    assert "RuleExtrasMatched(Board, RuleName, Params, MatchedMissed)" in after
    assert '"settings_not_applied":[' in after
    message = create.split("'Unknown rule_type: '", 1)[1].split(";", 1)[0]
    assert "matched_lengths" in message


def test_set_refuses_another_kind_and_writes_this_one(fns):
    setter = fns["PCB_SetRuleProperties"]
    assert "If MatchedAsked(Params) And (Kind <> 4) Then" in setter
    assert "'matched_* is for a Matched Lengths rule'" in setter
    assert "If (Kind = 4) And MatchedAsked(Params) Then" in setter
    assert "XReport := RuleExtrasMatched(Board, RuleName, Params, XMissed);" in setter
    asked = fns["MatchedAsked"]
    assert "'matched_tolerance_mils'" in asked and "'matched_mode'" in asked


def test_get_reads_the_settings_without_writing(fns):
    getter = fns["PCB_GetRuleProperties"]
    assert "If Rule.RuleKind = 4 Then" in getter
    assert "RuleExtrasMatched(Board, RuleName, '', MatchedMissed)" in getter


def test_create_sends_the_tolerance_and_the_mode_given(tools):
    _, (command, params) = tools("pcb_create_design_rule", name="ML",
                                 rule_type="matched_lengths", value=25,
                                 scope="InNetClass('DDR')", match_mode="within_pair")
    assert command == "pcb.create_design_rule"
    assert params["rule_type"] == "matched_lengths"
    assert params["matched_tolerance_mils"] == "25"
    assert params["matched_mode"] == "within_pair"
    _, (_, params) = tools("pcb_create_design_rule", name="ML2",
                           rule_type="matched_lengths", value=10)
    assert "matched_mode" not in params


@pytest.mark.parametrize("kwargs", [
    {"rule_type": "matched_lengths", "value": 0},
    {"rule_type": "matched_lengths", "value": 10, "match_mode": "pairs"},
    {"rule_type": "width", "value": 10, "match_mode": "group"},
])
def test_create_refuses_before_altium(tools, kwargs):
    out, sent = tools("pcb_create_design_rule", name="ML", **kwargs)
    assert out["ok"] is False and sent is None


def test_set_refuses_before_altium(tools):
    out, sent = tools("pcb_set_rule_properties", name="ML", matched_tolerance_mils=0)
    assert out["ok"] is False and sent is None
    out, sent = tools("pcb_set_rule_properties", name="ML", matched_mode="all")
    assert out["ok"] is False and "group" in out["reason"] and sent is None


def test_set_rule_properties_passes_the_settings(tools):
    _, (_, params) = tools("pcb_set_rule_properties", name="ML",
                           matched_tolerance_mils=7.874, matched_mode="group")
    assert params["matched_tolerance_mils"] == "7.874"
    assert params["matched_mode"] == "group"
