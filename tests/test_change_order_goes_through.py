# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_sync_pcb executes and closes the change order when set to Automatic."""
from __future__ import annotations

import threading

import pytest

from eda_agent.bridge import altium_bridge
from eda_agent.ui import change_order
from eda_agent.ui import windows


class _Button:
    def __init__(self, text, enabled=True):
        self.text, self.enabled = text, enabled


class _Altium:
    """An ECO that runs for two looks after Execute, then greys it out."""

    def __init__(self, appears_after=2):
        self.looks, self.appears_after = 0, appears_after
        self.pressed: list[str] = []
        self.running, self.open = 0, True

    def dialogs(self, pid):
        self.looks += 1
        if not self.open or self.looks <= self.appears_after:
            return []
        if self.running:
            self.running -= 1
        done = "Execute Changes" in self.pressed and not self.running
        eco = self

        class _Dialog:
            hwnd, title = 42, "Engineering Change Order"

            def find_button(self, caption):
                if caption == "Execute Changes":
                    return _Button(caption, enabled=not done)
                return _Button(caption) if caption == "Close" else None
        _Dialog.owner = eco
        return [_Dialog()]

    def click(self, button):
        self.pressed.append(button.text)
        if button.text == "Execute Changes":
            self.running = 2
        if button.text == "Close":
            self.open = False

    def wait_for_close(self, hwnd, timeout):
        return not self.open


@pytest.fixture
def altium(monkeypatch):
    fake = _Altium()
    monkeypatch.setattr(windows, "dialogs", fake.dialogs)
    monkeypatch.setattr(windows, "click", fake.click)
    return fake


#: Rows OCR made of a change order with Only Show Errors ticked (measured
#: on AD 26.11, 2026-11-04): the headings, two failed lines, the buttons.
_ROWS = ["Engineering Change Order x",
         "Modifications Status",
         "Enable Action Affected Object Affected Document Check Done Message",
         "Add Components(5)",
         "Add T DIO To Blinker555_v4.PcbDoc Footprint Not Found D041_AXlAL",
         "IMI Add T D12 To Blinker555_v4.PcbDoc Footprint Not Found",
         "Validate Changes Execute Changes Report Changes... Only Show Errors Close"]


@pytest.fixture
def grid(monkeypatch):
    from eda_agent.ui import dialog_answer, ocr
    shown = []
    monkeypatch.setattr(dialog_answer, "_set",
                        lambda dialog, step: shown.append(step.label) or {"ok": True})
    monkeypatch.setattr(ocr, "read_window_rows", lambda hwnd: list(_ROWS))
    return shown


def test_execute_then_close_once_the_run_has_finished(altium, grid):
    out = change_order.drive(1, threading.Event(), poll=0.001)
    assert altium.pressed == ["Execute Changes", "Close"]
    assert grid == ["Only Show Errors"]
    assert out == {"dialog": "Engineering Change Order", "appeared": True,
                   "done": ["Execute Changes", "read", "Close"], "closed": True,
                   "read_filtered": True, "read": _ROWS}


def test_the_failed_lines_are_the_rows_left_by_the_filter(altium, grid):
    out = change_order.drive(1, threading.Event(), poll=0.001)
    lines = change_order.failed_lines(out)
    assert lines == [_ROWS[4], _ROWS[5]]
    # The schematic's exact name finds its line past OCR's 0/O, l/I slips.
    assert change_order.failure_for("DO-41_AXIAL", lines) == _ROWS[4]
    assert change_order.failure_for("SOT23_N", lines) == ""
    assert change_order.says_not_found("Add D10 Footprint N0t F0und")
    assert not change_order.says_not_found("Add R50 Unknown Pin")


def test_a_line_with_its_name_cut_off_is_found_by_designator():
    """Measured: OCR left "Footprint Not Found" with no name on R50's row."""
    line = "Modify -Y [R50) In Blinker555_v4.PcbDoc Footprint Not Found"
    assert change_order.failure_for("NO_SUCH_FP", [line], "R50") == line
    assert change_order.failure_for("NO_SUCH_FP", [line], "R5") == ""
    assert change_order.failure_for("NO_SUCH_FP", ["Add R50 Unknown Pin"], "R50") == ""


def test_icon_noise_and_the_split_title_are_not_lines():
    out = {"read_filtered": True,
           "read": ["Order x", "Add -e CI-1 to NetC1_1 Unknown Pin. \u0007 Pin CI-1"]}
    assert change_order.failed_lines(out) == [
        "Add -e CI-1 to NetC1_1 Unknown Pin. Pin CI-1"]


def test_no_failed_lines_are_claimed_when_the_filter_did_not_take():
    assert change_order.failed_lines({"read_filtered": False, "read": _ROWS}) == []


def test_the_comparator_question_on_the_way_is_answered(monkeypatch):
    """"Continue and create ECO?" blocked the sync until a person pressed
    Yes; it is pressed, and recorded, on the way to the change order."""
    pressed = []

    class _Question:
        hwnd, title = 7, "Comparator Results (24 Differences)"

        def find_button(self, caption):
            return _Button(caption) if caption in ("Yes", "No", "Cancel") else None

    monkeypatch.setattr(windows, "dialogs", lambda pid: [] if pressed else [_Question()])
    monkeypatch.setattr(windows, "click", lambda b: pressed.append(b.text))
    out = change_order.drive(1, threading.Event(), appear_within=0.05, poll=0.001)
    assert pressed == ["Yes"]
    assert out["passed"] == [{"dialog": "Comparator Results (24 Differences)",
                              "pressed": "Yes"}]


def test_nothing_is_pressed_when_no_change_order_appears(monkeypatch):
    monkeypatch.setattr(windows, "dialogs", lambda pid: [])
    pressed = []
    monkeypatch.setattr(windows, "click", pressed.append)
    out = change_order.drive(1, threading.Event(), appear_within=0.05, poll=0.001)
    assert pressed == [] and out["appeared"] is False


def test_a_stop_ends_the_wait():
    stop = threading.Event()
    stop.set()
    assert change_order.drive(1, stop, appear_within=30)["appeared"] is False


def test_the_bridge_does_not_stop_a_call_for_the_dialog_it_expects():
    report = {"blocked": True, "dialogs": [{"hwnd": 42, "title": "Engineering Change Order"}]}
    assert altium_bridge._blocking_signature(report) != ""
    assert altium_bridge._blocking_signature(
        report, frozenset({change_order.TITLE})) == ""
    other = {"blocked": True, "dialogs": [{"hwnd": 9, "title": "Confirm"}]}
    assert altium_bridge._blocking_signature(
        other, frozenset({change_order.TITLE})) != ""


def test_expecting_is_undone_after_the_call():
    bridge = altium_bridge.AltiumBridge()
    with bridge.expecting("Engineering Change Order"):
        assert "Engineering Change Order" in bridge.expected_dialogs
    assert bridge.expected_dialogs == frozenset()
