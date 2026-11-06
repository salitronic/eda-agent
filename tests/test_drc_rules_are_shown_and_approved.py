# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A DRC says what rules it ran under, and whether they are the approved ones.

A DRC with no violations proves the board meets its rules, not that the
rules are the ones the designer set: a clearance loosened, a rule switched
out of DRC, a scope narrowed or a rule deleted all let the same board
pass. Every DRC reply carries the rule sheet, its difference from the
approved set kept beside the board, and a verdict that never reads a plain
pass under changed or unapproved rules. Rule edits made through any tool
are logged with what they changed.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from eda_agent.export import drc_rules

CLEAR = {"name": "Clearance", "rule_kind": 0, "enabled": True, "drc_enabled": True,
         "priority": 1, "scope_1": "All", "scope_2": "All", "comment": "",
         "descriptor": "Clearance Constraint (Gap=6mil) (All),(All)", "gap_mm": 0.1524,
         "from_schematic": False}
WIDTH = {"name": "Width", "rule_kind": 1, "enabled": True, "drc_enabled": True,
         "priority": 1, "scope_1": "All", "scope_2": "", "comment": "",
         "descriptor": "Width Constraint (Min=6mil) (Max=20mil) (Preferred=10mil) (All)",
         "from_schematic": False}


def _with(rule, **kw):
    r = dict(rule)
    r.update(kw)
    return r


class _Bridge:
    def __init__(self, board, rules, drc=None, pours=None):
        self.board, self.rules = str(board), [dict(r) for r in rules]
        self.drc = drc or {}
        self.pours = pours or []
        self.edits = []

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "pcb.get_design_rules":
            return {"rules": [dict(r) for r in self.rules], "board_path": self.board}
        if command == "generic.query_objects":
            return {"objects": list(self.pours)}
        if command == "pcb.run_drc":
            return dict(self.drc)
        if command == "pcb.set_rules_enabled":
            for r in self.rules:
                if r["name"] == params["names"]:
                    r["drc_enabled"] = params["enabled"] == "true"
            return {"updated": 1}
        raise AssertionError(command)


def _drc(**kw):
    base = {"drc_confirmed": True, "violation_count": 0, "violations": [],
            "mode": "batch", "report_path": "", "board_path": ""}
    base.update(kw)
    return base


def _enrich(bridge, result):
    asyncio.run(drc_rules.enrich_drc(bridge, result))
    return result


def test_a_pass_under_rules_nobody_approved_says_so(tmp_path):
    board = tmp_path / "B.PcbDoc"
    out = _enrich(_Bridge(board, [CLEAR, WIDTH]), _drc(board_path=str(board)))
    assert out["verdict"] == "pass_unapproved_rules"
    assert [r["name"] for r in out["rules"]["sheet"]] == ["Clearance", "Width"]
    assert out["rules"]["sheet"][0]["descriptor"].startswith("Clearance Constraint (Gap=6mil)")


def test_a_loosened_clearance_turns_the_pass_into_a_changed_rules_pass(tmp_path):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR, WIDTH], note="approved by George")
    loose = _with(CLEAR, gap_mm=0.0508,
                  descriptor="Clearance Constraint (Gap=2mil) (All),(All)")
    out = _enrich(_Bridge(board, [loose, WIDTH]), _drc(board_path=str(board)))
    assert out["verdict"] == "pass_under_changed_rules"
    changed = {(c["rule"], c["change"]): c for c in out["rules"]["changes_since_approval"]}
    assert changed[("Clearance", "gap_mm")]["loosened"] is True
    assert changed[("Clearance", "descriptor")]["values"]["gap"] == "loosened"


@pytest.mark.parametrize("edit, why", [
    (lambda rules: [_with(CLEAR, drc_enabled=False), WIDTH], "switched out of DRC"),
    (lambda rules: [_with(CLEAR, enabled=False), WIDTH], "disabled"),
    (lambda rules: [WIDTH], None),
    (lambda rules: [_with(CLEAR, scope_1="InNet('GND')"), WIDTH], None),
])
def test_every_way_round_a_rule_is_named(tmp_path, edit, why):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR, WIDTH])
    out = _enrich(_Bridge(board, edit(None)), _drc(board_path=str(board)))
    assert out["verdict"] == "pass_under_changed_rules"
    assert out["rules"]["changes_since_approval"]
    if why:
        assert {"rule": "Clearance", "why": why} in out["rules"]["not_checked"]


def test_the_approved_rules_unchanged_give_a_plain_pass(tmp_path):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR, WIDTH])
    out = _enrich(_Bridge(board, [CLEAR, WIDTH]), _drc(board_path=str(board)))
    assert out["verdict"] == "pass"
    assert out["rules"]["fingerprint"] == out["rules"]["approved_fingerprint"]


def test_a_tighter_rule_is_not_called_loosened(tmp_path):
    tight = _with(CLEAR, gap_mm=0.2032, descriptor="Clearance Constraint (Gap=8mil) (All),(All)")
    changes = drc_rules.diff([CLEAR], [tight])
    assert {c["change"]: c["loosened"] for c in changes} == {"descriptor": False, "gap_mm": False}


def test_violations_and_pours_that_ignore_them_are_said(tmp_path):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR])
    out = _enrich(_Bridge(board, [CLEAR], pours=[{"Name": "GND_TOP"}]),
                  _drc(board_path=str(board)))
    assert out["verdict"] == "pass" and "ignore violations" in out["verdict_reason"]
    out = _enrich(_Bridge(board, [CLEAR]), _drc(board_path=str(board), violation_count=3))
    assert out["verdict"] == "fail"


def test_a_rule_edit_through_a_tool_is_logged_and_listed_by_the_next_drc(tmp_path, monkeypatch):
    from eda_agent.tools import pcb as pcb_tools

    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR, WIDTH])
    bridge = _Bridge(board, [CLEAR, WIDTH])
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    reply = asyncio.run(pcb_tools._rule_edit(
        "pcb_set_rules_enabled", {"names": "Clearance"}, "pcb.set_rules_enabled",
        {"names": "Clearance", "enabled": "false", "match": "name"}))
    assert reply["rule_changes"] == [{"rule": "Clearance", "change": "drc_enabled",
                                      "before": True, "after": False, "loosened": True}]
    log = drc_rules.read_log(str(board))
    assert log[-1]["tool"] == "pcb_set_rules_enabled" and log[-1]["session"]
    out = _enrich(bridge, _drc(board_path=str(board)))
    assert out["rules"]["logged_changes_since_approval"][-1]["tool"] == "pcb_set_rules_enabled"


def test_generic_writes_to_rules_are_logged_too():
    assert drc_rules.is_rule_type("eRuleObject") and drc_rules.is_rule_type("rule")
    assert not drc_rules.is_rule_type("eTrackObject")


def test_the_backend_drc_carries_the_verdict(tmp_path, monkeypatch):
    from eda_agent.core import backends

    board = tmp_path / "B.PcbDoc"
    bridge = _Bridge(board, [CLEAR], drc=_drc(board_path=str(board)))
    be = backends.AltiumBackend() if hasattr(backends, "AltiumBackend") else None
    if be is None:
        pytest.skip("no Altium backend class")

    async def fake_bridge():
        return bridge, RuntimeError
    monkeypatch.setattr(be, "_bridge", fake_bridge)
    out = asyncio.run(be.run_drc())
    assert out["verdict"] == "pass_unapproved_rules" and "rules" in out


def test_the_baseline_is_readable_beside_the_board(tmp_path):
    board = tmp_path / "B.PcbDoc"
    done = drc_rules.write_baseline(str(board), [CLEAR], note="ok")
    data = json.loads((tmp_path / "B.DrcRules.json").read_text(encoding="utf-8"))
    assert data["note"] == "ok" and data["fingerprint"] == done["fingerprint"]
    assert "comment" not in data["rules"][0]


def test_the_dashboard_shows_the_rules_against_the_approved_set(tmp_path):
    from eda_agent.web.workbench import rules_report

    board = str(tmp_path / "B.PcbDoc")
    shown = rules_report(board, {"rules": [CLEAR, WIDTH]})
    assert shown["ok"] and shown["approved"] is False and shown["changes"] == []
    drc_rules.write_baseline(board, [CLEAR, WIDTH])
    off = rules_report(board, {"rules": [_with(CLEAR, drc_enabled=False), WIDTH]})
    assert off["approved"] and off["changes"][0]["loosened"] is True
    assert off["not_checked"] == [{"rule": "Clearance", "why": "switched out of DRC"}]
    assert rules_report(board, {"error": "x"})["ok"] is False


def test_a_dashboard_drc_keeps_its_verdict():
    from eda_agent.web.board_actions import _result_line

    line = _result_line({"tool": "pcb_run_drc", "reply": {
        "verdict": "pass_under_changed_rules", "verdict_reason": "differs",
        "violation_count": 0}})
    assert line["verdict"] == "pass_under_changed_rules" and line["verdict_reason"] == "differs"
    assert "verdict" not in _result_line({"tool": "pcb_unroute", "reply": {"ok": True}})


def test_altiums_report_is_matched_by_descriptor_with_every_rule_it_checked():
    report = [{"rule": "Clearance Constraint (Gap=6mil) (All),(All)", "violations": 3},
              {"rule": "Width  Constraint (Min=6mil) (Max=20mil) (Preferred=10mil) (All)",
               "violations": 0}]
    fanout = {"name": "Fanout", "enabled": True, "drc_enabled": True,
              "descriptor": "Fanout Control Rule ( (All) )"}
    missing = drc_rules.not_checked([CLEAR, WIDTH, fanout], report)
    assert missing == [{"rule": "Fanout", "why": "not in Altium's report"}]


def test_a_failing_drc_under_loosened_rules_says_the_count_is_not_comparable(tmp_path):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR])
    out = _enrich(_Bridge(board, [_with(CLEAR, drc_enabled=False)]),
                  _drc(board_path=str(board), violation_count=134))
    assert out["verdict"] == "fail" and "differ from the approved" in out["verdict_reason"]
