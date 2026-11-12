# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A script error box whose crashed call has gone is answered by the next call.

MEASURED 2026-11-11: proj_set_parameter on an existing parameter crashed the
script (a blank "Error" box). The caller that sent it was no longer waiting,
so nothing pressed OK, and every other session's call failed on the box
until a person closed it. Only the crashed call's own client recovered a
crash; now a call that finds the box still up and unclaimed after
ORPHAN_GRACE_S recovers it the same way and then runs.

Uses the rig of test_script_crash_recovers_itself: the real poll loop on an
empty workspace, the Win32 side faked.
"""

from __future__ import annotations

import json
import time

import eda_agent.bridge.altium_bridge as ab
from eda_agent.bridge import script_crash
from tests.test_script_crash_recovers_itself import REQ, _box, _probe, rig  # noqa: F401

OLD = "0123456789ab"


def _queued_behind_a_halt(ws):
    (ws / f"request_{REQ}.json").write_text("{}", encoding="utf-8")
    (ws / f"progress_{OLD}.json").write_text("{}", encoding="utf-8")


def _new_loop_answers(ws, state):
    def restart():
        state["restarts"] += 1
        state["marker_during_restart"] = (ws / ab.MAINTENANCE_FILE).exists()
        (ws / f"request_{REQ}.json").unlink()
        (ws / f"response_{REQ}.json").write_text(
            json.dumps({"id": REQ, "success": True, "data": {}}), encoding="utf-8")
        return {"restarted": True, "output": "The bridge answers again"}
    return restart


def test_an_orphaned_box_is_cleared_and_this_call_then_runs(rig):
    bridge, ws, state, mp = rig
    mp.setattr(script_crash, "ORPHAN_GRACE_S", 0.1)
    _queued_behind_a_halt(ws)
    mp.setattr(bridge, "_dialog_probe", lambda: _probe(_box()))
    state["on_ok"] = lambda: mp.setattr(bridge, "_dialog_probe", lambda: None)
    mp.setattr(script_crash, "restart_loop", _new_loop_answers(ws, state))

    response = bridge._poll_response(REQ, timeout=5.0)

    assert response.success
    assert state["pressed"] == 1 and state["restarts"] == 1
    assert state["marker_during_restart"], "other calls must wait for the new loop"
    for _ in range(100):
        if not (ws / ab.MAINTENANCE_FILE).exists():
            break
        time.sleep(0.02)
    assert not (ws / ab.MAINTENANCE_FILE).exists()
    told = ab._CALL.answered_box
    assert "earlier call had crashed" in told["note"] and "restarted" in told["note"]
    record = json.loads(open(told["record"], encoding="utf-8").read())
    assert record["request_id"] == OLD, "the record names the halted request"


def test_the_crashed_call_keeps_its_box_while_it_can_answer(rig):
    bridge, ws, state, mp = rig
    mp.setattr(script_crash, "ORPHAN_GRACE_S", 60.0)
    _queued_behind_a_halt(ws)
    mp.setattr(bridge, "_dialog_probe", lambda: _probe(_box()))
    started = time.monotonic()

    def later():
        # The crashed call claims its own recovery, then the loop answers.
        if time.monotonic() - started > 0.3 and not (ws / f"response_{REQ}.json").exists():
            (ws / ab.MAINTENANCE_FILE).write_text("{}", encoding="utf-8")
            (ws / f"request_{REQ}.json").unlink()
            (ws / f"response_{REQ}.json").write_text(
                json.dumps({"id": REQ, "success": True, "data": {}}), encoding="utf-8")
        return _probe(_box())
    mp.setattr(bridge, "_dialog_probe", later)

    assert bridge._poll_response(REQ, timeout=5.0).success
    assert state["pressed"] == 0 and state["restarts"] == 0


def test_a_box_someone_is_recovering_is_left_to_them(rig):
    bridge, ws, state, mp = rig
    mp.setattr(script_crash, "ORPHAN_GRACE_S", 0.0)
    _queued_behind_a_halt(ws)
    (ws / ab.MAINTENANCE_FILE).write_text("{}", encoding="utf-8")
    assert bridge._clear_orphan_crash(REQ, _probe(_box())) is None
    assert state["pressed"] == 0


def test_a_question_is_never_answered(rig):
    bridge, ws, state, mp = rig
    mp.setattr(script_crash, "ORPHAN_GRACE_S", 0.0)
    _queued_behind_a_halt(ws)
    assert bridge._clear_orphan_crash(REQ, _probe(_box(("OK", "Cancel")))) is None
    assert bridge._clear_orphan_crash(REQ, _probe(_box(("Yes", "No")))) is None
    assert state["pressed"] == 0


def test_a_taken_request_is_left_to_its_own_recovery(rig):
    bridge, ws, state, mp = rig
    mp.setattr(script_crash, "ORPHAN_GRACE_S", 0.0)
    assert bridge._clear_orphan_crash(REQ, _probe(_box())) is None
    assert state["pressed"] == 0


def test_only_one_session_claims_the_box(tmp_path):
    marker = tmp_path / ab.MAINTENANCE_FILE
    assert script_crash.claim_marker(marker, "first")
    assert not script_crash.claim_marker(marker, "second")
    assert json.loads(marker.read_text(encoding="utf-8"))["reason"] == "first"


def test_the_halted_request_is_the_marker_left_alone(tmp_path):
    (tmp_path / "progress_aaaa.json").write_text("{}", encoding="utf-8")
    (tmp_path / "request_aaaa.json").write_text("{}", encoding="utf-8")   # queued
    (tmp_path / "progress_bbbb.json").write_text("{}", encoding="utf-8")
    (tmp_path / "response_bbbb.json").write_text("{}", encoding="utf-8")  # answered
    assert script_crash.halted_request(tmp_path) == ""
    (tmp_path / "progress_cccc.json").write_text("{}", encoding="utf-8")
    assert script_crash.halted_request(tmp_path) == "cccc"
