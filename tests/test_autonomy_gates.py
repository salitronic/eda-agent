# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The layout stages end on numbers, and the harness reads them.

Placement, routing, pours_tuning and verification used to close on prose
("no overlaps", "100% routed; DRC clean") that the client judged about its
own work. Each now names checks, numbers and the values that pass, and a
stage logged ok whose own logged numbers fail is sent back.

What must hold:

* the sentence a client reads and the numbers the harness checks are the
  same criteria, and every number named is one the audits really report;
* logged numbers that fail send the stage back, and repeated failure
  escalates to the user like any other;
* a journal with no numbers in it, which is every journal written before
  this, walks the pipeline exactly as it did.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from eda_agent.design.autonomy import (
    AUDIT_CHECKS,
    MEASURED_GATES,
    apply_measured_gates,
    autonomy_guide,
    evaluate_gate,
    gate_text,
    measured_next_action,
)
from eda_agent.design.session import STAGES, SessionStore
from eda_agent.design.state_machine import MAX_STAGE_ATTEMPTS, next_action


def _journal(tmp_path, sid="g1"):
    return SessionStore(tmp_path / "sessions").start("a board", session_id=sid)


def _ok_through(j, last):
    for stage in STAGES:
        if stage == last:
            return
        j.stage_result(stage, "ok")


def _action(j):
    return measured_next_action(j.state(), j.events())


def _audit_board(defect=None):
    from tests import test_layout_audit as boards

    b = boards._clean()
    if defect == "overlap":
        boards._add(b, "R3", 200, 325, "D", "E")
    return b


# ---------------------------------------------------------------------------
# The criteria
# ---------------------------------------------------------------------------

def test_the_gated_stages_are_the_four_layout_stages():
    assert set(MEASURED_GATES) == {"placement", "routing", "pours_tuning", "verification"}


def test_the_audit_check_names_are_the_ones_the_audits_run():
    from eda_agent.layout.audit import CHECKS

    assert AUDIT_CHECKS == tuple(CHECKS)


def test_every_number_a_gate_names_is_one_the_audits_report():
    """A gate naming a count the audit does not report would read
    "not logged" forever and never send a stage back."""
    from eda_agent.layout.audit import run_audits

    summary = run_audits(_audit_board())["summary"]
    named = 0
    for stage, criteria in MEASURED_GATES.items():
        for c in criteria:
            if c.check not in AUDIT_CHECKS:
                continue
            named += 1
            assert c.metric in summary[c.check], (stage, c)
            if isinstance(c.want, str):
                assert c.want in summary[c.check], (stage, c)
    assert named >= 10


def test_the_gate_sentence_names_every_criterion():
    for stage, criteria in MEASURED_GATES.items():
        text = gate_text(stage)
        for c in criteria:
            assert c.metric in text and c.check in text, (stage, c.check, c.metric)
            if c.op != "listed":
                assert f"{c.op} {c.want}" in text, (stage, c)
        assert chr(0x2014) not in text


def test_the_named_gates_are_the_ones_the_stages_were_asked_for():
    want = {
        "placement": {("placement_audit", "overlaps"),
                      ("placement_audit", "pad_gaps_below_rule"),
                      ("placement_audit", "on_keepouts"),
                      ("placement_audit", "off_board")},
        "routing": {("connectivity_summary", "routed"), ("drc", "violations"),
                    ("corner_audit", "sharper_than_45")},
        "pours_tuning": {("plane_region_audit", "islands"),
                         ("pcb_calc_length_match", "worst_skew_ps")},
        "verification": {("return_via_audit", "exceptions"),
                         ("proj_run_erc", "violation_count")},
    }
    for stage, pairs in want.items():
        have = {(c.check, c.metric) for c in MEASURED_GATES[stage]}
        assert pairs <= have, (stage, pairs - have)
    # Verification re-measures everything the layout stages did.
    every = {(c.check, c.metric) for s in ("placement", "routing", "pours_tuning")
             for c in MEASURED_GATES[s]}
    assert every <= {(c.check, c.metric) for c in MEASURED_GATES["verification"]}


def test_the_guide_serves_the_measured_gate(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_BACKEND", "altium")
    import eda_agent.design.autonomy as mod
    monkeypatch.setattr(mod, "_BACKEND_TOOLS", {})
    for entry in autonomy_guide()["stages"]:
        if entry["stage"] in MEASURED_GATES:
            assert entry["exit_gate"] == gate_text(entry["stage"])
            assert entry["gate"] == [c.as_dict() for c in MEASURED_GATES[entry["stage"]]]
        else:
            assert "gate" not in entry


# ---------------------------------------------------------------------------
# Reading the numbers
# ---------------------------------------------------------------------------

def test_the_raw_audit_result_is_read_as_logged():
    from eda_agent.layout.audit import run_audits

    v = evaluate_gate("placement", run_audits(_audit_board(), ["placement_audit"]))
    assert v["verdict"] == "pass" and not v["failed"] and not v["missing"]

    v = evaluate_gate("placement", run_audits(_audit_board("overlap"), ["placement_audit"]))
    assert v["verdict"] == "fail"
    assert v["failed"] == ["placement_audit.overlaps = 1, wants == 0"]


def test_the_count_is_read_not_the_length_of_a_cut_list():
    """The audit cuts its findings at ``limit`` and never its counts, so
    a gate must read the count."""
    from eda_agent.layout.audit import run_audits
    from tests import test_layout_audit as boards

    b = _audit_board("overlap")
    boards._add(b, "R4", 500, 325, "D", "E")      # a second overlap, on R2
    result = run_audits(b, ["placement_audit"], limit=1)
    assert len(result["checks"]["placement_audit"]["overlaps"]) == 1
    v = evaluate_gate("placement", result)
    assert v["failed"] == ["placement_audit.overlaps = 2, wants == 0"]


def test_flat_keys_and_other_reports_of_the_same_number_are_read():
    v = evaluate_gate("routing", {"connectivity_summary.routed": 4,
                                  "connectivity_summary.nets": 4,
                                  "corner_audit.sharper_than_45": 0,
                                  "pcb_run_drc": {"violation_count": 2}})
    assert v["verdict"] == "fail"
    assert v["failed"] == ["pcb_run_drc.violation_count = 2, wants == 0"]


def test_a_number_compared_with_another_names_both():
    v = evaluate_gate("routing", {"connectivity_summary": {"counts": {"routed": 3, "nets": 4}},
                                  "drc.violations": 0, "corner_audit.sharper_than_45": 0})
    assert v["failed"] == ["connectivity_summary.routed = 3, wants == nets (4)"]


def test_skew_is_judged_against_the_logged_budget():
    base = {"plane_region_audit.islands": 0, "drc.violations": 0}
    over = evaluate_gate("pours_tuning", {**base, "pcb_calc_length_match":
                                          {"worst_skew_ps": 12.5, "skew_budget_ps": 10.0}})
    assert over["verdict"] == "fail"
    within = evaluate_gate("pours_tuning", {**base, "pcb_calc_length_match":
                                            {"worst_skew_ps": 9.0, "skew_budget_ps": 10.0}})
    assert within["verdict"] == "pass"
    # No budget logged: the skew is not measured, and nothing else fails.
    open_ = evaluate_gate("pours_tuning", {**base, "pcb_calc_length_match":
                                           {"worst_skew_ps": 9.0, "skew_budget_ps": None}})
    assert open_["verdict"] == "incomplete" and len(open_["missing"]) == 1


def test_return_via_exceptions_only_have_to_be_listed():
    nums = {"placement_audit": {"counts": {"overlaps": 0, "pad_gaps_below_rule": 0,
                                           "on_keepouts": 0, "on_mounting_holes": 0,
                                           "off_board": 0}},
            "connectivity_summary.routed": 2, "connectivity_summary.nets": 2,
            "drc.violations": 0, "corner_audit.sharper_than_45": 0,
            "track_shape_audit.staircases": 0, "track_shape_audit.short_tracks": 0,
            "plane_region_audit.islands": 0,
            "pcb_calc_length_match": {"worst_skew_ps": 1, "skew_budget_ps": 5},
            "proj_run_erc": {"violation_count": 0}}
    without = evaluate_gate("verification", nums)
    assert without["verdict"] == "incomplete"
    assert without["missing"] == ["return_via_audit exceptions listed"]
    listed = evaluate_gate("verification", {**nums, "return_via_audit.exceptions": 3})
    assert listed["verdict"] == "pass"


def test_nothing_logged_is_unmeasured_and_a_stage_without_a_gate_is_not_gated():
    assert evaluate_gate("routing", {})["verdict"] == "unmeasured"
    assert evaluate_gate("routing", {"unrelated": 1})["verdict"] == "unmeasured"
    assert evaluate_gate("plan", {"x": 1})["verdict"] == "not_gated"


# ---------------------------------------------------------------------------
# The harness acting on them
# ---------------------------------------------------------------------------

def test_an_old_journal_with_no_numbers_walks_the_pipeline_as_before(tmp_path):
    j = _journal(tmp_path)
    seen = []
    for _ in range(len(STAGES) + 2):
        act = _action(j)
        old = next_action(j.state())
        # Same decision as the unmeasured state machine, stage by stage.
        assert (act["status"], act["stage"]) == (old.status, old.stage)
        if act["status"] == "complete":
            break
        seen.append(act["stage"])
        j.stage_result(act["stage"], "ok")
    assert seen == list(STAGES)
    state, verdicts = apply_measured_gates(j.state(), j.events())
    assert verdicts == {} and state.complete


def test_failing_numbers_send_the_stage_back(tmp_path):
    from eda_agent.layout.audit import run_audits

    j = _journal(tmp_path)
    _ok_through(j, "placement")
    j.stage_result("placement", "ok", data=run_audits(_audit_board("overlap")))
    act = _action(j)
    assert act["stage"] == "placement" and act["status"] == "retry"
    assert "placement_audit.overlaps = 1, wants == 0" in act["guidance"]
    assert act["last_gate"]["verdict"] == "fail"
    assert act["exit_gate"] == gate_text("placement")
    # The journal itself still says what the client logged.
    assert j.state().stage_status["placement"] == "ok"

    j.stage_result("placement", "ok", data=run_audits(_audit_board()))
    act = _action(j)
    assert act["stage"] == "routing" and act["status"] == "proceed"


def test_a_failing_earlier_stage_pulls_the_run_back_to_it(tmp_path):
    j = _journal(tmp_path)
    _ok_through(j, "placement")
    j.stage_result("placement", "ok", data={"placement_audit.off_board": 2})
    j.stage_result("routing", "ok")
    act = _action(j)
    assert act["stage"] == "placement" and act["status"] == "retry"


def test_numbers_that_keep_failing_escalate_to_the_user(tmp_path):
    j = _journal(tmp_path)
    _ok_through(j, "routing")
    for _ in range(MAX_STAGE_ATTEMPTS):
        j.stage_result("routing", "ok", data={"drc": {"counts": {"violations": 5}}})
    act = _action(j)
    assert act["status"] == "blocked" and act["stage"] == "routing"
    assert act["open_question"]


def test_partial_numbers_that_pass_keep_the_logged_status(tmp_path):
    j = _journal(tmp_path)
    _ok_through(j, "routing")
    j.stage_result("routing", "ok", data={"drc.violations": 0})
    act = _action(j)
    assert act["stage"] == "pours_tuning"
    state, verdicts = apply_measured_gates(j.state(), j.events())
    assert verdicts["routing"]["verdict"] == "incomplete"


def test_a_later_result_without_numbers_replaces_the_failing_one(tmp_path):
    """The newest word on a stage counts, as the journal's own replay does."""
    j = _journal(tmp_path)
    _ok_through(j, "placement")
    j.stage_result("placement", "ok", data={"placement_audit.overlaps": 1})
    j.stage_result("placement", "ok")
    assert _action(j)["stage"] == "routing"


# ---------------------------------------------------------------------------
# Through the tools
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp(tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP

    from eda_agent.tools import register_all_tools

    monkeypatch.setattr("eda_agent.config.get_config",
                        lambda: SimpleNamespace(workspace_dir=tmp_path))
    m = FastMCP("test")
    register_all_tools(m)
    return m


def _call(mcp, name, args):
    import json

    result = asyncio.run(mcp.call_tool(name, args))
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def _walk_to(mcp, sid, last):
    for stage in STAGES:
        if stage == last:
            return
        _call(mcp, "design_session_log", {"event": "stage_result", "session_id": sid,
                                          "stage": stage, "status": "ok"})


def test_the_tools_log_numbers_judge_them_and_send_the_stage_back(mcp, monkeypatch):
    monkeypatch.setenv("EDA_AGENT_BACKEND", "altium")
    sid = _call(mcp, "design_session_start", {"requirement": "x"})["session_id"]
    _walk_to(mcp, sid, "placement")
    act = _call(mcp, "design_next_action", {"session_id": sid})
    assert act["stage"] == "placement" and act["exit_gate"] == gate_text("placement")
    assert act["gate"] and "last_gate" not in act

    logged = _call(mcp, "design_session_log", {
        "event": "stage_result", "session_id": sid, "stage": "placement",
        "status": "ok", "data": {"placement_audit": {"counts": {"overlaps": 2}}}})
    assert logged["gate"]["verdict"] == "fail"

    act = _call(mcp, "design_next_action", {"session_id": sid})
    assert act["status"] == "retry" and act["stage"] == "placement"
    assert "placement_audit.overlaps = 2" in act["guidance"]
    resume = _call(mcp, "design_session_resume", {"session_id": sid})
    assert "placement" in resume["guidance"] and "fail" in resume["guidance"]
    assert resume["gates"]["placement"]["verdict"] == "fail"


def test_data_may_arrive_as_json_text_and_bad_json_is_refused(mcp):
    sid = _call(mcp, "design_session_start", {"requirement": "x"})["session_id"]
    _walk_to(mcp, sid, "placement")
    ok = _call(mcp, "design_session_log", {
        "event": "stage_result", "session_id": sid, "stage": "placement",
        "status": "ok", "data": '{"placement_audit.overlaps": 0}'})
    assert ok["gate"]["verdict"] == "incomplete"
    bad = _call(mcp, "design_session_log", {
        "event": "stage_result", "session_id": sid, "stage": "placement",
        "status": "ok", "data": "{not json"})
    assert "error" in bad


def test_the_measured_gate_is_adapted_for_another_backend(mcp, monkeypatch):
    """A tool name in a gate gets the same treatment as before: swapped
    where the backend has an equivalent, left in place where it has not."""
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    sid = _call(mcp, "design_session_start", {"requirement": "x"})["session_id"]
    _walk_to(mcp, sid, "placement")
    act = _call(mcp, "design_next_action", {"session_id": sid})
    assert "easyeda_render_image" in act["exit_gate"]
    assert "design_visual_review" not in act["exit_gate"]
    assert "pcb_layout_audit" in act["exit_gate"]
    assert "not available on this backend" not in act["exit_gate"]

    _walk_to(mcp, sid, "routing")
    _call(mcp, "design_session_log", {"event": "stage_result", "session_id": sid,
                                      "stage": "placement", "status": "ok"})
    act = _call(mcp, "design_next_action", {"session_id": sid})
    assert act["stage"] == "routing"
    assert "run_drc violation_count" in act["exit_gate"]
    assert "pcb_run_drc" not in act["exit_gate"]


def test_the_playbook_summaries_point_at_the_measured_check():
    # The state machine's exit gates are short summaries; the text a client
    # is served comes from gate_text. Both must name the same check, so a
    # summary cannot drift back to prose the harness does not measure.
    from eda_agent.design.autonomy import MEASURED_GATES, gate_text
    from eda_agent.design.state_machine import STAGE_PLAYBOOKS

    for stage in MEASURED_GATES:
        summary = STAGE_PLAYBOOKS[stage]["exit_gate"]
        assert summary.startswith("Measured"), (stage, summary)
        assert "pcb_layout_audit" in gate_text(stage)
        assert "pcb_layout_audit" in STAGE_PLAYBOOKS[stage]["tools"], stage
