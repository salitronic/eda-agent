# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Board page's actions: what each sends for the selection in Altium,
what it refuses, and that one runs at a time."""
from __future__ import annotations

import threading

import pytest

from eda_agent.web import board_actions as ba

VIEW = {"components": [
    {"ref": "R1", "x": 300.0, "y": 50.0, "rot": 0.0},
    {"ref": "R2", "x": 100.0, "y": 80.0, "rot": 90.0},
    {"ref": "R3", "x": 200.0, "y": 20.0, "rot": 270.0},
]}
BOARD = "C:\\b\\A.PcbDoc"


def _sel(parts=(), nets=()):
    return {"parts": list(parts), "nets": list(nets)}


def test_align_and_distribute_use_the_selection():
    calls = ba.plan("align", {"mode": "top"}, _sel(["R1", "R2"]), VIEW, BOARD)
    assert calls == [("pcb_align_components", {"designators": "R1,R2", "alignment": "top"})]
    # Spread between the outer two, in board order, not selection order.
    [(tool, args)] = ba.plan("distribute", {"axis": "x"}, _sel(["R1", "R2", "R3"]), VIEW, BOARD)
    assert tool == "pcb_distribute_components"
    assert args["designators"] == "R2,R3,R1" and (args["start"], args["end"]) == (100.0, 300.0)


def test_rotate_turns_each_part_from_where_it_is():
    [(tool, args)] = ba.plan("rotate", {"degrees": 90}, _sel(["R2", "R3"]), VIEW, BOARD)
    assert tool == "pcb_move_components"
    assert args["moves"] == [{"designator": "R2", "rotation": 180.0},
                             {"designator": "R3", "rotation": 0.0}]


def test_net_actions_name_the_board():
    calls = ba.plan("route", {}, _sel(nets=["VBUS", "GND"]), VIEW, BOARD)
    assert calls == [("pcb_autoroute", {"expect_file": BOARD, "nets": ["VBUS", "GND"]})]
    assert ba.plan("tidy", {}, _sel(nets=["N1"]), VIEW, BOARD)[0][1]["board_path"] == BOARD


@pytest.mark.parametrize("action,selection", [
    ("align", _sel(["R1"])), ("distribute", _sel(["R1", "R2"])),
    ("route", _sel(["R1"])), ("nonsense", _sel(["R1", "R2", "R3"])),
])
def test_a_selection_that_does_not_fit_is_refused(action, selection):
    with pytest.raises(ValueError):
        ba.plan(action, {"mode": "left"}, selection, VIEW, BOARD)


def test_a_bad_align_mode_is_refused():
    with pytest.raises(ValueError):
        ba.plan("align", {"mode": "diagonal"}, _sel(["R1", "R2"]), VIEW, BOARD)


def test_one_action_at_a_time_and_its_result_is_kept():
    gate = threading.Event()
    sent = []

    def call(name, args):
        sent.append(name)
        gate.wait(2)
        return {"ok": False, "reason": "no board"} if name == "pcb_run_drc" else {"placed": 1}

    runner = ba.Runner(call=call)
    done = []
    assert runner.start("drc", [("pcb_run_drc", {})], done.append)["ok"]
    assert runner.start("repour", [("pcb_repour_polygons", {})])["ok"] is False
    gate.set()
    for _ in range(100):
        if not runner.busy():
            break
        threading.Event().wait(0.02)
    snap = runner.snapshot()
    assert snap["state"] == "failed" and snap["problems"] == ["pcb_run_drc: no board"]
    assert done == ["drc"] and sent == ["pcb_run_drc"]
    assert ba.after("repour") == "refresh" and ba.after("align") is None


def test_an_action_goes_to_the_board_shown_whatever_is_in_front():
    """The page acts on its own board: brought to the front, its selection
    read there, the action run, and the document that was in front given
    back. Nothing is refused because the person is elsewhere in Altium."""
    sent = []

    def call(name, args):
        sent.append((name, args))
        if name == "cmd:application.get_active_document":
            return {"file_path": "C:/b/Top.SchDoc"}
        if name == "cmd:pcb.cockpit_state":
            return {"file": BOARD, "selected": {"parts": ["R1", "R2"], "nets": []}}
        return {"ok": True}

    runner = ba.Runner(call=call)
    planned = []

    def planner(selected):
        planned.append(selected)
        return ba.plan("align", {"mode": "top"}, selected, VIEW, BOARD)

    assert runner.start("align", planner, board=BOARD)["ok"]
    for _ in range(100):
        if not runner.busy():
            break
        threading.Event().wait(0.02)
    assert [n for n, _ in sent] == [
        "cmd:application.get_active_document", "pcb_focus_board", "cmd:pcb.cockpit_state",
        "pcb_align_components", "cmd:application.set_active_document"]
    assert sent[1][1] == {"board_path": BOARD}
    assert sent[-1][1] == {"file_path": "C:/b/Top.SchDoc"}
    assert planned == [{"parts": ["R1", "R2"], "nets": []}]
    assert runner.snapshot()["state"] == "done"


def test_a_selection_that_does_not_fit_fails_the_action_and_still_gives_the_front_back():
    sent = []

    def call(name, args):
        sent.append(name)
        if name == "cmd:application.get_active_document":
            return {"file_path": "C:/b/Top.SchDoc"}
        if name == "cmd:pcb.cockpit_state":
            return {"selected": {"parts": ["R1"], "nets": []}}
        return {"ok": True}

    runner = ba.Runner(call=call)
    runner.start("align", lambda sel: ba.plan("align", {"mode": "top"}, sel, VIEW, BOARD),
                 board=BOARD)
    for _ in range(100):
        if not runner.busy():
            break
        threading.Event().wait(0.02)
    snap = runner.snapshot()
    assert snap["state"] == "failed" and "2 or more parts" in snap["problems"][0]
    assert "pcb_align_components" not in sent
    assert sent[-1] == "cmd:application.set_active_document"
