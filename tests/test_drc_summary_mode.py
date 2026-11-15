# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A DRC run again and again can leave the rule sheet out.

The rule sheet and Altium's row for every rule checked are most of a DRC
reply, and they rarely change between two runs while a board is being
fixed. ``pcb_run_drc(detail="summary")`` leaves the sheet out and keeps
only the rules that have violations, while everything that says which
rules the run was under stays: the fingerprint, the difference from the
approved set and the verdict. The default reply is unchanged.
"""
from __future__ import annotations

import asyncio
import copy

from eda_agent.export import drc_rules

CLEAR = {"name": "Clearance", "enabled": True, "drc_enabled": True,
         "priority": 1, "scope_1": "All", "scope_2": "All",
         "descriptor": "Clearance Constraint (Gap=6mil) (All),(All)", "gap_mm": 0.1524}
WIDTH = {"name": "Width", "enabled": True, "drc_enabled": True,
         "priority": 1, "scope_1": "All", "scope_2": "",
         "descriptor": "Width Constraint (Min=6mil) (Max=20mil) (Preferred=10mil) (All)"}
FANOUT = {"name": "Fanout", "enabled": True, "drc_enabled": True,
          "descriptor": "Fanout Control Rule ( (All) )"}

REPORT_ROWS = [
    {"rule": "Clearance Constraint (Gap=6mil) (All),(All)", "violations": 3},
    {"rule": "Width Constraint (Min=6mil) (Max=20mil) (Preferred=10mil) (All)",
     "violations": 0},
]


class _Bridge:
    def __init__(self, board, rules, drc):
        self.board, self.rules, self.drc = str(board), rules, drc

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "pcb.get_design_rules":
            return {"rules": copy.deepcopy(self.rules), "board_path": self.board}
        if command == "generic.query_objects":
            return {"objects": []}
        if command == "pcb.run_drc":
            return copy.deepcopy(self.drc)
        raise AssertionError(command)


def _drc(board, **kw):
    base = {"drc_confirmed": True, "violation_count": 0, "violations": [],
            "mode": "batch", "report_path": "", "board_path": str(board)}
    base.update(kw)
    return base


def _run(bridge, detail=None, monkeypatch=None):
    from eda_agent.tools import pcb as pcb_tools

    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    # The report file is read from report_path; hand its rows in directly.
    monkeypatch.setattr("eda_agent.export.drc_report.read_drc_report",
                        lambda path: {"counts": {"rule_violations": 3},
                                      "rules": copy.deepcopy(REPORT_ROWS),
                                      "rules_add_up": True,
                                      "stopped_at_limit": False})
    pcb_tools.register_pcb_tools(_Mcp())
    fn = captured["pcb_run_drc"]
    kwargs = {} if detail is None else {"detail": detail}
    return asyncio.run(fn(**kwargs))


def test_summary_leaves_the_sheet_out_and_keeps_what_names_the_rules(tmp_path, monkeypatch):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR, WIDTH])
    loose = dict(CLEAR, gap_mm=0.0508,
                 descriptor="Clearance Constraint (Gap=2mil) (All),(All)")
    bridge = _Bridge(board, [loose, WIDTH],
                     _drc(board, report_path="x.html", violation_count=3))
    full = _run(bridge, monkeypatch=monkeypatch)
    out = _run(bridge, "summary", monkeypatch=monkeypatch)
    assert "sheet" in full["rules"]
    assert "sheet" not in out["rules"] and out["rules"]["sheet_omitted"] is True
    assert "pcb_drc_rules" in out["rules"]["note"]
    assert out["rules"]["fingerprint"] == full["rules"]["fingerprint"]
    assert out["rules"]["changes_since_approval"] == full["rules"]["changes_since_approval"]
    assert out["verdict"] == full["verdict"] == "fail"
    assert out["verdict_reason"] == full["verdict_reason"]


def test_summary_keeps_only_the_rules_with_violations(tmp_path, monkeypatch):
    board = tmp_path / "B.PcbDoc"
    bridge = _Bridge(board, [CLEAR, WIDTH, FANOUT],
                     _drc(board, report_path="x.html", violation_count=3))
    full = _run(bridge, monkeypatch=monkeypatch)
    out = _run(bridge, "summary", monkeypatch=monkeypatch)
    assert len(full["report_rules"]) == 2
    assert out["report_rules"] == [REPORT_ROWS[0]]
    assert out["report_rules_checked"] == 2
    # Worked out from every row, so Width (0 violations) is not called unchecked.
    assert out["rules"]["not_checked"] == full["rules"]["not_checked"] == [
        {"rule": "Fanout", "why": "not in Altium's report"}]


def test_a_pass_under_changed_rules_still_says_so(tmp_path, monkeypatch):
    board = tmp_path / "B.PcbDoc"
    drc_rules.write_baseline(str(board), [CLEAR, WIDTH])
    bridge = _Bridge(board, [dict(CLEAR, drc_enabled=False), WIDTH], _drc(board))
    out = _run(bridge, "summary", monkeypatch=monkeypatch)
    assert out["verdict"] == "pass_under_changed_rules"
    assert out["rules"]["changes_since_approval"]
    assert out["rules"]["logged_changes_since_approval_count"] == 0
    assert "logged_changes_since_approval" not in out["rules"]


def test_an_unapproved_pass_points_at_the_rules_tool(tmp_path, monkeypatch):
    board = tmp_path / "B.PcbDoc"
    bridge = _Bridge(board, [CLEAR, WIDTH], _drc(board))
    out = _run(bridge, "summary", monkeypatch=monkeypatch)
    assert out["verdict"] == "pass_unapproved_rules"
    assert "rules.sheet" not in out["verdict_reason"]
    assert "pcb_drc_rules(action='show')" in out["verdict_reason"]


def test_the_default_reply_is_the_full_one(tmp_path, monkeypatch):
    board = tmp_path / "B.PcbDoc"
    bridge = _Bridge(board, [CLEAR, WIDTH],
                     _drc(board, report_path="x.html", violation_count=3))
    default = _run(bridge, monkeypatch=monkeypatch)
    full = _run(bridge, "full", monkeypatch=monkeypatch)
    assert default == full
    assert [r["name"] for r in default["rules"]["sheet"]] == ["Clearance", "Width"]
    assert len(default["report_rules"]) == 2 and "report_rules_checked" not in default
    assert "sheet_omitted" not in default["rules"]


def test_an_unknown_detail_is_refused_before_any_run(tmp_path, monkeypatch):
    board = tmp_path / "B.PcbDoc"

    class _NoCall(_Bridge):
        async def send_command_async(self, command, params=None, timeout=None):
            raise AssertionError(f"no call expected, got {command}")

    out = _run(_NoCall(board, [], {}), "everything", monkeypatch=monkeypatch)
    assert out["ok"] is False and "summary" in out["reason"]


def test_summary_of_unread_rules_changes_nothing_it_cannot_see():
    result = {"rules": {"read": False, "reason": "x"}, "verdict": "rules_unknown"}
    drc_rules.summarize(result)
    assert result == {"rules": {"read": False, "reason": "x"}, "verdict": "rules_unknown"}
