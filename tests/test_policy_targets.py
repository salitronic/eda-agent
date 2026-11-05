# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Board rules and ERC levels from the policy folder: what is read, what a
board or project would need, and what applying sends."""

from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.design import policy_targets as pt
from eda_agent.tools import conventions


def _write(folder, name, data):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


RULES = [
    {"name": "HV_Clearance", "rule_type": "clearance", "scope": "InNetClass('HV')",
     "value": 20},
    {"name": "Power_Width", "rule_type": "width", "scope": "InNetClass('PWR')",
     "value": 12, "max_value": 100, "favored_value": 20, "comment": "house"},
    {"name": "Tent", "rule_type": "solder_mask_expansion", "scope": "IsVia",
     "value": -2},
]
BOARD = [
    {"name": "HV_Clearance", "scope_1": "InNetClass('HV')", "enabled": True,
     "comment": "", "gap_mm": 0.254},
    {"name": "power_width", "scope_1": "InNetClass( 'PWR' )", "enabled": True,
     "comment": "old"},
    {"name": "Tent", "scope_1": "IsVia", "enabled": True, "comment": ""},
    {"name": "Clearance", "scope_1": "All", "enabled": True, "comment": ""},
]


def test_a_bad_rules_file_names_each_problem():
    trouble = pt.rule_problems([
        {"name": "A", "rule_type": "clearence"}, {"name": "a"},
        {"rule_type": "width", "valu": 3}, {"name": "B", "value": "10"},
        {"name": "C", "set": {"testpoint": {"grid_mils": 1}}}])
    text = " | ".join(trouble)
    for bit in ("A: rule_type", "a is listed twice", "has no name", "unknown keys",
                "B: value is a number", "C: set is"):
        assert bit in text, (bit, text)
    assert pt.rule_problems(RULES) == []


def test_the_plan_creates_updates_and_never_deletes():
    plan = pt.plan_board_rules(RULES + [{"name": "New", "value": 8}], BOARD)
    assert [c["name"] for c in plan["create"]] == ["New"]
    assert plan["create"][0]["rule_type"] == "clearance"
    rows = {r["name"]: r for r in plan["update"]}
    # Names match case aside, scopes whitespace aside.
    assert rows["power_width"]["differs"] == [
        {"field": "comment", "board": "old", "policy": "house"}]
    assert rows["power_width"]["write"] == {
        "min_width_mils": 12, "max_width_mils": 100, "favored_width_mils": 20,
        "scope1": "InNetClass('PWR')", "comment": "house"}
    # 0.254 mm is 10 mil, the policy says 20.
    assert rows["HV_Clearance"]["differs"] == [
        {"field": "value", "board": 10.0, "policy": 20}]
    assert "cannot be changed in place" in rows["Tent"]["note"]
    assert plan["extra"] == ["Clearance"]


def test_erc_sets_extend_and_null_drops(tmp_path):
    _write(tmp_path, "erc_levels/base.json", {"levels": {
        "Nets with no driving source": "warning", "Floating net labels": "error"}})
    _write(tmp_path, "erc_levels/strict.json", {"extends": "base", "levels": {
        "Nets with no driving source": "error", "Floating net labels": None}})
    got = pt.load_erc_levels(tmp_path, "strict")
    assert got["levels"] == {"Nets with no driving source": "error"}
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in got["chain"]] == \
        ["base.json", "strict.json"]
    _write(tmp_path, "erc_levels/loop.json", {"extends": "loop", "levels": {}})
    with pytest.raises(ValueError, match="loop"):
        pt.load_erc_levels(tmp_path, "loop")
    _write(tmp_path, "erc_levels/bad.json", {"levels": {"X": "loud"}})
    with pytest.raises(ValueError, match="no_report"):
        pt.load_erc_levels(tmp_path, "bad")
    with pytest.raises(ValueError):
        pt.load_erc_levels(tmp_path, "../x")


def test_the_erc_plan_matches_by_description_or_kind():
    current = [{"kind": 3, "description": "Nets with no driving source", "level": "warning"},
               {"kind": 7, "description": "Floating net labels", "level": "error"}]
    plan = pt.plan_erc_levels({"nets with no driving source": "error", "7": "error",
                               "Nonsense": "fatal"}, current)
    assert plan["change"] == [{"violation": "Nets with no driving source",
                               "project": "warning", "policy": "error"}]
    assert plan["same"] == ["7"] and plan["unknown"] == ["Nonsense"]


class _Bridge:
    def __init__(self, replies):
        self.replies, self.sent = replies, []

    async def send_command_async(self, command, params, **kwargs):
        self.sent.append((command, params))
        reply = self.replies.get(command, {"success": True})
        return reply(params) if callable(reply) else reply


def _run(monkeypatch, tmp_path, coro_fn, replies, *args):
    monkeypatch.setenv("EDA_AGENT_POLICY_DIR", str(tmp_path))
    bridge = _Bridge(replies)
    monkeypatch.setattr(conventions, "get_bridge", lambda: bridge)
    return asyncio.run(coro_fn(*args)), bridge.sent


def test_a_dry_run_writes_nothing(monkeypatch, tmp_path):
    _write(tmp_path, "board_rules.json", {"rules": RULES})
    out, sent = _run(monkeypatch, tmp_path, conventions._apply_board_rules,
                     {"pcb.get_design_rules": {"rules": BOARD}}, False)
    assert out["ok"] and not out["applied"]
    assert [c for c, _ in sent] == ["pcb.get_design_rules"]


def test_applying_creates_then_sets_and_updates(monkeypatch, tmp_path):
    rules = RULES[:2] + [{"name": "Vias", "rule_type": "via_size", "value": 8,
                          "enabled": False}]
    _write(tmp_path, "board_rules.json", {"rules": rules})
    out, sent = _run(monkeypatch, tmp_path, conventions._apply_board_rules,
                     {"pcb.get_design_rules": {"rules": BOARD[:2]}}, True)
    assert out["ok"] and out["applied"] and out["failed"] == []
    assert ("pcb.create_design_rule", {"name": "Vias", "rule_type": "via_size",
                                       "value": "8", "net_scope": "different_nets"}) in sent
    assert ("pcb.set_rule_properties", {"name": "Vias", "enabled": "false"}) in sent
    assert ("pcb.set_rule_properties", {
        "name": "power_width", "min_width_mils": "12", "max_width_mils": "100",
        "favored_width_mils": "20", "scope1": "InNetClass('PWR')",
        "comment": "house"}) in sent
    assert not any(c == "pcb.delete_design_rule" for c, _ in sent)


def test_a_refused_create_is_failed(monkeypatch, tmp_path):
    _write(tmp_path, "board_rules.json", {"rules": [{"name": "X", "value": 5}]})
    out, _ = _run(monkeypatch, tmp_path, conventions._apply_board_rules,
                  {"pcb.get_design_rules": {"rules": []},
                   "pcb.create_design_rule": {"success": False, "error": "RULE_EXISTS"}},
                  True)
    assert out["failed"] == ["X"] and out["ok"] is False


def test_erc_apply_sends_only_the_differences(monkeypatch, tmp_path):
    _write(tmp_path, "erc_levels.json", {"levels": {
        "Nets with no driving source": "no_report", "Floating net labels": "error"}})
    current = {"levels": [
        {"kind": 3, "description": "Nets with no driving source", "level": "warning"},
        {"kind": 7, "description": "Floating net labels", "level": "error"}]}
    out, sent = _run(monkeypatch, tmp_path, conventions._apply_erc,
                     {"project.get_erc_levels": current,
                      "project.set_erc_levels": {"set": 1, "failed": 0, "project": "P.PrjPcb"},
                      "application.save_document": {"saved": True}},
                     "", True, "")
    assert ("project.set_erc_levels",
            {"levels": "Nets with no driving source=no_report"}) in sent
    assert out["saved"] and out["ok"]


def test_no_policy_folder_enforces_nothing(monkeypatch):
    monkeypatch.delenv("EDA_AGENT_POLICY_DIR", raising=False)
    out = asyncio.run(conventions._apply_board_rules(True))
    assert out["ok"] is False
