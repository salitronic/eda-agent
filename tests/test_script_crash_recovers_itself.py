# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A handler that crashes the script is cleared and the loop restarted,
and the call that crashed says so.

MEASURED 2026-10-30: PCB_PlaceArc failed on its reply line, Altium put up
"Error" with only OK, and after OK held the script at the line with the
title naming Altium_API.PrjScr. Every call then failed until a person
pressed OK, pressed Ctrl+F3 and started the loop again.

These drive the real poll loop on an empty workspace, with the Win32 side
(the box, the title, the restart) replaced, so what is tested is the
decision: when to act, when to leave a box alone, and what the caller is
told.
"""

from __future__ import annotations

import json

import pytest

import eda_agent.bridge.altium_bridge as ab
from eda_agent.bridge import script_crash
from eda_agent.bridge.altium_bridge import AltiumBridge
from eda_agent.bridge.exceptions import AltiumScriptCrashError, AltiumTimeoutError

REQ = "deadbeefcafe"
HALTED = "Altium_API.PrjScr - Altium Designer Develop"
WORKING = "RoundLED.PcbDoc - Altium Designer Develop"


class _Process:
    pid = 4242
    exe_path = "X2.EXE"


class _Frame:
    hwnd = 7

    def __init__(self, title):
        self.title = title


def _box(buttons=("OK",), title="Error"):
    return {"title": title, "kind": "error", "message": [],
            "ocr_text": ["x"], "hwnd": 99,
            "buttons": [{"caption": b, "enabled": True} for b in buttons]}


def _probe(box):
    return {"blocked": True, "dialog_count": 1, "dialogs": [box],
            "summary": f"Altium is BLOCKED by a modal. error: {box['title']!r}"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A bridge on an empty workspace with the screen and restart faked."""
    import eda_agent.ui.windows as windows

    b = AltiumBridge()
    b.config.workspace_dir = tmp_path
    monkeypatch.setattr(b, "_note_fault", lambda *a, **k: None)
    monkeypatch.setenv("EDA_AGENT_POINTER_FILE", str(tmp_path / "no-pointer.txt"))
    monkeypatch.setattr(ab, "_DIALOG_PROBE_AFTER", 0.0)
    monkeypatch.setattr(ab, "_DIALOG_PROBE_EVERY", 0.05)
    monkeypatch.setattr(ab, "_MAX_HEARTBEAT_EXTENSIONS", 2)
    monkeypatch.setattr(script_crash, "REPLY_AFTER_OK_S", 0.2)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(b.process_manager, "get_altium_info", lambda: _Process())

    state = {"title": HALTED, "restarts": 0, "pressed": 0, "on_ok": None,
             "marker_during_restart": None}

    def capture(hwnd, path):
        path.write_bytes(b"png")
        return str(path)

    def press_ok(hwnd):
        state["pressed"] += 1
        if state["on_ok"]:
            state["on_ok"]()
        return ""

    def restart():
        state["restarts"] += 1
        state["marker_during_restart"] = (tmp_path / ab.MAINTENANCE_FILE).exists()
        return {"restarted": True, "output": "The bridge answers again"}

    monkeypatch.setattr(script_crash, "_capture", capture)
    monkeypatch.setattr(script_crash, "_press_ok", press_ok)
    monkeypatch.setattr(script_crash, "_main_frame", lambda pid: _Frame(state["title"]))
    monkeypatch.setattr(script_crash, "restart_loop", restart)
    from eda_agent.ui import dialog_report
    monkeypatch.setattr(dialog_report, "report", lambda pid: {"dialog_count": 0})
    ab._CALL.command = "pcb.place_arc"
    return b, tmp_path, state, monkeypatch


def test_a_script_error_box_is_cleared_and_the_loop_restarted(rig):
    """The regression: the box, a halt, and the caller told it crashed."""
    import threading
    import time

    bridge, ws, state, mp = rig
    mp.setattr(bridge, "_dialog_probe", lambda: _probe(_box()))
    go = threading.Event()
    done = threading.Event()

    def slow_restart():
        state["restarts"] += 1
        state["marker_during_restart"] = (ws / ab.MAINTENANCE_FILE).exists()
        go.wait(5)
        done.set()
        return {"restarted": True, "output": "The bridge answers again"}

    mp.setattr(script_crash, "restart_loop", slow_restart)

    with pytest.raises(AltiumScriptCrashError) as caught:
        bridge._poll_response(REQ, timeout=5.0)

    # Told while the restart is still running: it measured 145 s, past the
    # client's 120 s, and the crash reached nobody (2026-10-30).
    assert not done.is_set()
    assert (ws / ab.MAINTENANCE_FILE).exists(), (
        "other sessions must see the maintenance marker while it restarts")
    text = str(caught.value)
    assert "CRASHED" in text and "pcb.place_arc" in text
    assert "read the design back" in text and "being restarted now" in text
    assert state["pressed"] == 1
    shots = caught.value.details["screenshots"]
    assert [s.rsplit("_", 1)[-1] for s in shots] == ["dialog.png", "altium.png"]
    record = json.loads(open(caught.value.details["record"], encoding="utf-8").read())
    assert record["command"] == "pcb.place_arc"

    go.set()
    assert done.wait(5)
    for _ in range(50):
        if not (ws / ab.MAINTENANCE_FILE).exists():
            break
        time.sleep(0.02)
    assert state["restarts"] == 1 and state["marker_during_restart"]
    assert not (ws / ab.MAINTENANCE_FILE).exists(), "the marker outlived the restart"
    record = json.loads(open(caught.value.details["record"], encoding="utf-8").read())
    assert record["restart"]["restarted"] is True


def test_a_box_the_handler_survives_is_answered_and_reported(rig):
    """OK, a reply comes: no restart, and the box rides on the reply."""
    bridge, ws, state, mp = rig
    mp.setattr(bridge, "_dialog_probe", lambda: _probe(_box()))
    state["title"] = WORKING

    def reply():
        (ws / f"response_{REQ}.json").write_text(
            json.dumps({"id": REQ, "success": True, "data": {}}), encoding="utf-8")
        mp.setattr(bridge, "_dialog_probe", lambda: None)
    state["on_ok"] = reply

    response = bridge._poll_response(REQ, timeout=5.0)

    assert response.success
    assert state["restarts"] == 0
    assert ab._CALL.answered_box["screenshots"], "the caller must see the box"


def test_a_request_not_yet_taken_is_never_recovered(rig):
    """Another session's crash is not this call's to clear."""
    bridge, ws, state, mp = rig
    mp.setattr(bridge, "_dialog_probe", lambda: _probe(_box()))
    (ws / f"request_{REQ}.json").write_text("{}", encoding="utf-8")

    with pytest.raises(AltiumTimeoutError):
        bridge._poll_response(REQ, timeout=5.0)

    assert state["pressed"] == 0 and state["restarts"] == 0


def test_a_box_that_asks_something_is_left_alone(rig):
    """OK and Cancel is a question, not a script error."""
    bridge, ws, state, mp = rig
    mp.setattr(bridge, "_dialog_probe", lambda: _probe(_box(("OK", "Cancel"))))

    with pytest.raises(AltiumTimeoutError):
        bridge._poll_response(REQ, timeout=5.0)

    assert state["pressed"] == 0 and state["restarts"] == 0


def test_a_halt_with_no_box_is_recovered_at_the_deadline(rig):
    """The box already answered: the progress file stays and the title
    names the script project."""
    bridge, ws, state, mp = rig
    mp.setattr(bridge, "_dialog_probe", lambda: None)
    (ws / f"progress_{REQ}.json").write_text("{}", encoding="utf-8")

    import time

    with pytest.raises(AltiumScriptCrashError):
        bridge._poll_response(REQ, timeout=0.2)

    # The restart runs in the background once the caller is told.
    for _ in range(100):
        if state["restarts"]:
            break
        time.sleep(0.02)
    assert state["pressed"] == 0 and state["restarts"] == 1
    assert not (ws / f"progress_{REQ}.json").exists()


def test_a_slow_handler_is_not_mistaken_for_a_halt(rig):
    """Title on a design document: the wait goes on as before."""
    bridge, ws, state, mp = rig
    mp.setattr(bridge, "_dialog_probe", lambda: None)
    state["title"] = WORKING
    (ws / f"progress_{REQ}.json").write_text("{}", encoding="utf-8")

    with pytest.raises(AltiumTimeoutError) as caught:
        bridge._poll_response(REQ, timeout=0.1)

    assert not isinstance(caught.value, AltiumScriptCrashError)
    assert state["restarts"] == 0


def test_a_failed_restart_is_said_plainly():
    message = script_crash.crash_message({
        "command": "pcb.place_arc", "screenshots": [],
        "restart": {"restarted": False, "output": "no answer"}})
    assert "did NOT come back" in message
    assert "relaunch --restart" in message


def test_a_box_already_gone_is_not_reported_as_pressed(monkeypatch):
    """It was: the record said "pressed" while a box stayed on screen until
    a person closed it (2026-10-30)."""
    import eda_agent.ui.windows as windows

    monkeypatch.setattr(windows, "capture", lambda hwnd: None)
    assert script_crash._press_ok(99) != ""


def test_every_error_box_is_pressed_and_anything_else_reported(monkeypatch, tmp_path):
    from eda_agent.ui import dialog_report

    question = _box(buttons=("Yes", "No"), title="Confirm")
    question["kind"] = "confirm"
    screens = [
        {"blocked": True, "dialogs": [_box(title="Error"), question], "summary": "two"},
        {"blocked": True, "dialogs": [question], "summary": "Confirm is open"},
    ]
    monkeypatch.setattr(dialog_report, "report", lambda pid: screens.pop(0))
    pressed = []
    monkeypatch.setattr(script_crash, "_press_ok", lambda hwnd: pressed.append(hwnd) or "")
    monkeypatch.setattr(script_crash, "_capture", lambda hwnd, path: str(path))

    out = script_crash.clear_error_boxes(4242, tmp_path, "crash")

    assert pressed == [99], "only the OK-only error box is pressed"
    assert out["cleared"][0]["title"] == "Error"
    assert out["cleared"][0]["pressed"] == "pressed"
    assert out["blocking"] == "Confirm is open"
