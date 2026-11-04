# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_sync_pcb executes and closes the change order when set to Automatic."""
from __future__ import annotations

import threading

import pytest

from eda_agent.bridge import altium_bridge
from eda_agent.ui import change_order


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
    monkeypatch.setattr(change_order.windows, "dialogs", fake.dialogs)
    monkeypatch.setattr(change_order.windows, "click", fake.click)
    monkeypatch.setattr(change_order.windows, "wait_for_close", fake.wait_for_close)
    return fake


def test_execute_then_close_once_the_run_has_finished(altium):
    out = change_order.drive(1, threading.Event(), poll=0.001)
    assert altium.pressed == ["Execute Changes", "Close"]
    assert out == {"appeared": True, "executed": True, "finished": True, "closed": True}


def test_nothing_is_pressed_when_no_change_order_appears(monkeypatch):
    monkeypatch.setattr(change_order.windows, "dialogs", lambda pid: [])
    pressed = []
    monkeypatch.setattr(change_order.windows, "click", pressed.append)
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
